"""Draw the faces / edges / points a relationship mates, as coloured custom graphics.

Fusion only hands out a relationship's geometry with the timeline rolled back,
so collect.py stores each side's entity token (and part) while it reads them;
here the tokens are resolved again at the current timeline position and drawn:
side one orange, side two blue (the same colours as the part names in the panel).
"""

import adsk.core
import adsk.fusion

from . import log
from .visibility import Hider

SIDE_COLORS = [(255, 136, 0), (0, 150, 255)]
GROUP_ID = "browserPlusMates"
STROKE_TOLERANCE = 0.005        # cm
CROSS = 0.3                     # cm, half size of the mark drawn for a point
GHOST_COLOR = (150, 165, 185)   # see-through parts in mate view
GHOST_EDGE = (70, 80, 95)
GHOST_OPACITY = 0.18


def _color(rgb, alpha=255):
    return adsk.core.Color.create(rgb[0], rgb[1], rgb[2], alpha)


def _flat(points):
    out = []
    for p in points:
        out.extend((p.x, p.y, p.z))
    return out


class MateHighlight:
    """The "mate view" of one relationship.

    - its faces / edges / points are drawn solid, in the side colours;
    - optionally (ghost) its parts are hidden and redrawn see-through, so the
      faces show even where the parts touch;
    - optionally (isolate) every other part is hidden.
    Only light bulbs that were on are switched off, and exactly those are put
    back by clear(). The timeline and the parts' positions are never touched.
    """

    def __init__(self, app):
        self.app = app
        self._group = None
        self._hider = Hider()       # light bulbs we switched off
        self.name = None            # relationship on show, for the panel

    def hidden_paths(self):
        """Parts switched off for the mate view (they're really visible)."""
        return self._hider.paths()

    @property
    def active(self):
        return self._group is not None or self._hider.active

    def clear(self):
        if not self.active:
            return
        try:
            if self._group is not None and self._group.isValid:
                self._group.deleteMe()
        except Exception:
            log.error("clear mate highlight")
        self._hider.restore()
        self._group, self.name = None, None
        self._refresh()

    def show(self, design, name, details, parts, occurrence, ghost=True, isolate=False):
        """Draw every side of every relationship in `details` (records from collect)."""
        self.clear()
        refs = [(side, ref) for d in details for side, ref in enumerate(d.get("geometry") or []) if ref]
        self._group = design.rootComponent.customGraphicsGroups.add()
        self._group.id = GROUP_ID
        self.name = name
        mated = [occ for occ in (occurrence(p) for p in dict.fromkeys(parts) if p) if occ is not None]
        if isolate:
            try:
                self._hider.hide_others(design, set(o.fullPathName for o in mated))
            except Exception:
                log.error("hide other parts")
        if ghost:
            for occ in mated:
                try:
                    self._ghost(occ)
                except Exception:
                    log.error("ghost " + occ.fullPathName)
        drawn = 0
        for side, ref in refs:
            try:
                drawn += self._draw(design, ref, SIDE_COLORS[side % 2], occurrence)
            except Exception:
                log.error("draw mated {}".format(ref.get("type")))
        self._refresh()
        return drawn

    def _ghost(self, occ):
        """Hide a mated part and redraw it see-through (with its edges)."""
        stack, leaves = [occ], []
        while stack:
            item = stack.pop()
            leaves.append(item)
            stack.extend(item.childOccurrences)
        effect = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            _color(GHOST_COLOR), _color(GHOST_COLOR), _color((255, 255, 255)), _color((0, 0, 0)), 10.0, GHOST_OPACITY)
        for item in leaves:
            if not item.isVisible:
                continue
            matrix = item.transform2
            for body in item.component.bRepBodies:
                if not body.isLightBulbOn:
                    continue
                tri = body.meshManager.displayMeshes.bestMesh
                if tri is None:
                    continue
                indices = list(tri.nodeIndices)
                mesh = self._group.addMesh(
                    adsk.fusion.CustomGraphicsCoordinates.create(list(tri.nodeCoordinatesAsDouble)),
                    indices, list(tri.normalVectorsAsDouble), indices)
                mesh.transform = matrix
                mesh.isSelectable = False
                mesh.color = effect
                self._strokes(list(body.edges), matrix, GHOST_EDGE, 1.0, priority=1)
        self._hider.switch_off(occ)

    def _refresh(self):
        try:
            self.app.activeViewport.refresh()
        except Exception:
            pass

    def _resolve(self, design, ref, occurrence):
        """(entity, native entity, placement matrix) for a stored side, or Nones."""
        found = design.findEntityByToken(ref.get("token") or "") if ref.get("token") else []
        if not found:
            log.info("mated {} not found by token (changed since it was read?)".format(ref.get("type")))
            return None, None, None
        entity = found[0]
        context = getattr(entity, "assemblyContext", None)
        native = getattr(entity, "nativeObject", None) or entity
        if context is None and ref.get("part"):
            context = occurrence(ref["part"])
        matrix = context.transform2 if context is not None else adsk.core.Matrix3D.create()
        return entity, native, matrix

    def _draw(self, design, ref, rgb, occurrence):
        entity, native, matrix = self._resolve(design, ref, occurrence)
        if native is None:
            return 0
        face = adsk.fusion.BRepFace.cast(native)
        if face is not None:
            self._face(face, matrix, rgb)
            return 1
        edge = adsk.fusion.BRepEdge.cast(native)
        if edge is not None:
            self._strokes([edge], matrix, rgb, 4.0)
            return 1
        point = None
        sketch_point = adsk.fusion.SketchPoint.cast(entity)
        if sketch_point is not None:
            point, matrix = sketch_point.worldGeometry, adsk.core.Matrix3D.create()   # already root space
        for cls in (adsk.fusion.BRepVertex, adsk.fusion.ConstructionPoint):
            item = cls.cast(native)
            if point is None and item is not None:
                point = item.geometry
        if point is not None:
            self._cross(point, matrix, rgb)
            return 1
        log.info("mated {}: no highlight for this kind of geometry".format(native.objectType))
        return 0

    def _face(self, face, matrix, rgb):
        tri = face.meshManager.displayMeshes.bestMesh
        if tri is not None:
            indices = list(tri.nodeIndices)
            mesh = self._group.addMesh(adsk.fusion.CustomGraphicsCoordinates.create(list(tri.nodeCoordinatesAsDouble)),
                                       indices, list(tri.normalVectorsAsDouble), indices)
            mesh.transform = matrix
            mesh.isSelectable = False
            mesh.depthPriority = 10         # wins over the real (coincident) face
            mesh.color = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
                _color(rgb), _color(rgb), _color((255, 255, 255)), _color((0, 0, 0)), 10.0, 1.0)
        self._strokes(list(face.edges), matrix, rgb, 3.0)

    def _strokes(self, edges, matrix, rgb, weight, priority=11):
        coords, lengths = [], []
        for edge in edges:
            try:
                ev = edge.evaluator
                ok, start, end = ev.getParameterExtents()
                ok2, points = ev.getStrokes(start, end, STROKE_TOLERANCE) if ok else (False, [])
                if ok2 and len(points) > 1:
                    coords.extend(_flat(points))
                    lengths.append(len(points))
            except Exception:
                pass
        if not lengths:
            return
        lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [], True, lengths)
        lines.transform = matrix
        lines.isSelectable = False
        lines.weight = weight
        lines.depthPriority = priority
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(rgb))

    def _cross(self, p, matrix, rgb):
        c = CROSS
        coords = [p.x - c, p.y, p.z, p.x + c, p.y, p.z,
                  p.x, p.y - c, p.z, p.x, p.y + c, p.z,
                  p.x, p.y, p.z - c, p.x, p.y, p.z + c]
        lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [0, 1, 2, 3, 4, 5], False)
        lines.transform = matrix
        lines.isSelectable = False
        lines.weight = 4.0
        lines.depthPriority = 11
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(rgb))
