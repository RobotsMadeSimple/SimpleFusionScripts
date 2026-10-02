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
AXIS_HALF = 2.0                 # cm, half length drawn of a mated construction axis
PLANE_HALF = 1.5                # cm, half size of the square drawn for a mated construction plane
GHOST_COLOR = (170, 195, 225)   # see-through parts in mate view: a light frosted-glass tint
GHOST_EDGE = (105, 120, 140)    # their edges, soft and depth-tested (back edges fade behind the glass)
GHOST_OPACITY = 0.32


def _color(rgb, alpha=255):
    return adsk.core.Color.create(rgb[0], rgb[1], rgb[2], alpha)


def _mesh(entity):
    """A body's / face's triangles: Fusion's display mesh, or (for a part that has stayed hidden,
    which has none yet) one calculated now."""
    try:
        return entity.meshManager.displayMeshes.bestMesh
    except Exception:
        pass
    try:
        calc = entity.meshManager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.LowQualityTriangleMesh)   # quick; fine see-through
        return calc.calculate()
    except Exception:
        log.error("mesh for a hidden part")
        return None


_body_cache = {}     # (component id, body name, revision) -> mesh + edge strokes, this session


def _stroke_data(edges):
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
    return coords, lengths


def _body_data(body):
    """(coords, indices, normals, edge coords, edge lengths) of a native body, kept for the session
    (a hidden part's triangles are calculated, which takes a while; edges are slow on big parts)."""
    try:
        key = (body.parentComponent.id, body.name, body.revisionId)
    except Exception:
        key = None
    if key is not None and key in _body_cache:
        return _body_cache[key]
    tri = _mesh(body)
    if tri is None:
        return None
    indices = list(tri.nodeIndices)
    edge_coords, edge_lengths = _stroke_data(list(body.edges))
    data = (list(tri.nodeCoordinatesAsDouble), indices, list(tri.normalVectorsAsDouble), edge_coords, edge_lengths)
    if key is not None:
        if len(_body_cache) > 400:
            _body_cache.clear()
        _body_cache[key] = data
    return data


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
    - optionally (isolate) every other part is hidden;
    - a mated part the user has hidden is shown for the view (see-through: just its
      see-through copy; otherwise its light bulb goes on) and hidden again by clear().
    Only light bulbs that were on are switched off (and off ones on), and exactly those
    are put back by clear(). The timeline and the parts' positions are never touched.
    """

    def __init__(self, app):
        self.app = app
        self._group = None
        self._hider = Hider()       # light bulbs we switched off
        self._shown = []            # light bulbs we switched on (hidden mated parts), in order
        self.name = None            # relationship on show, for the panel

    def hidden_paths(self):
        """Parts switched off for the mate view (they're really visible)."""
        return self._hider.paths()

    def shown_paths(self):
        """Hidden parts switched on for the mate view (they're really hidden)."""
        out = set()
        for item in self._shown:
            try:
                if item.isValid and item.objectType == "adsk::fusion::Occurrence":
                    out.add(item.fullPathName)
            except Exception:
                pass
        return out

    @property
    def active(self):
        return self._group is not None or self._hider.active or bool(self._shown)

    def clear(self):
        if not self.active:
            return
        try:
            if self._group is not None and self._group.isValid:
                self._group.deleteMe()
        except Exception:
            log.error("clear mate highlight")
        self._hider.restore()
        for item in reversed(self._shown):          # hidden again, as the user had them
            try:
                if item.isValid and item.isLightBulbOn:
                    item.isLightBulbOn = False
            except Exception:
                log.error("hide again")
        self._shown = []
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
        self._ghosted = 0
        if ghost:
            for occ in mated:
                try:
                    self._ghost(occ)
                except Exception:
                    log.error("ghost " + occ.fullPathName)
        else:
            for occ in mated:
                try:
                    self._reveal(occ)
                except Exception:
                    log.error("show hidden " + occ.fullPathName)
        still = [o.fullPathName for o in mated if ghost and o.isVisible]
        if self._shown:
            log.info("mate view: showing {} hidden for the view".format(", ".join(
                getattr(i, "fullPathName", "?") for i in self._shown)))
        try:
            style = self.app.activeViewport.visualStyle
        except Exception:
            style = "?"
        log.info("mate view {}: {} parts, ghost {} ({} bodies drawn see-through), {} switched off, still visible: {}, "
                 "visual style {}".format(name, len(mated), ghost, self._ghosted, len(self._hider.hidden),
                                          ", ".join(still) or "none", style))
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
            _color(GHOST_COLOR), _color(GHOST_COLOR), _color((255, 255, 255)), _color((0, 0, 0)), 60.0, GHOST_OPACITY)
        top = occ.fullPathName
        for item in leaves:
            # Drawn even if the part (or an assembly it's in) is hidden: the view shows it. Only
            # sub-parts hidden inside it stay out.
            if self._hidden_inside(item, top):
                continue
            matrix = item.transform2
            for body in item.component.bRepBodies:
                if not body.isLightBulbOn:
                    continue
                data = _body_data(body)
                if data is None:
                    continue
                coords, indices, normals, edge_coords, edge_lengths = data
                mesh = self._group.addMesh(adsk.fusion.CustomGraphicsCoordinates.create(coords),
                                           indices, normals, indices)
                mesh.transform = matrix
                mesh.isSelectable = False
                mesh.color = effect
                self._ghosted += 1
                self._add_lines(edge_coords, edge_lengths, matrix, GHOST_EDGE, 1.0, 0)
        self._hider.switch_off(occ)

    @staticmethod
    def _hidden_inside(item, top):
        """True if `item`, or an assembly between it and the mated part `top`, is switched off."""
        o = item
        while o is not None and o.fullPathName != top:
            if not o.isLightBulbOn:
                return True
            o = o.assemblyContext
        return False

    def _switch_on(self, item):
        if not item.isLightBulbOn:
            item.isLightBulbOn = True
            self._shown.append(item)

    def _reveal(self, occ):
        """Show a hidden mated part (not see-through): switch on its light bulb and those of
        hidden assemblies above it, keeping those assemblies' other contents hidden."""
        if occ.isVisible:
            return
        chain = []
        o = occ
        while o is not None:
            chain.append(o)
            o = o.assemblyContext
        on_path = set(c.fullPathName for c in chain)
        for o in reversed(chain):                   # outermost first
            if o.isLightBulbOn:
                continue
            self._switch_on(o)
            if o.fullPathName == occ.fullPathName:
                continue
            # An assembly switched on just to reach the part: what else is in it stays hidden.
            for child in o.childOccurrences:
                if child.fullPathName not in on_path:
                    self._hider.switch_off(child)
            for body in o.bRepBodies:
                self._hider.switch_off(body)

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
        if ref.get("point"):                    # a joint origin: root space, or its part's ("partSpace")
            matrix = adsk.core.Matrix3D.create()
            if ref.get("partSpace") and ref.get("part"):
                occ = occurrence(ref["part"])
                if occ is not None:
                    matrix = occ.transform2
            self._cross(adsk.core.Point3D.create(*ref["point"]), matrix, rgb)
            return 1
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
        # Construction geometry and sketch lines (joints often snap to these).
        axis = adsk.fusion.ConstructionAxis.cast(native)
        if axis is not None:
            line = axis.geometry                        # InfiniteLine3D in its component
            d = line.direction
            d.normalize()
            o = line.origin
            self._segment([o.x - d.x * AXIS_HALF, o.y - d.y * AXIS_HALF, o.z - d.z * AXIS_HALF,
                           o.x + d.x * AXIS_HALF, o.y + d.y * AXIS_HALF, o.z + d.z * AXIS_HALF], matrix, rgb)
            self._cross(o, matrix, rgb)
            return 1
        plane = adsk.fusion.ConstructionPlane.cast(native)
        if plane is not None:
            g = plane.geometry                          # Plane: origin + u / v directions
            o, u, v = g.origin, g.uDirection, g.vDirection
            u.normalize()
            v.normalize()
            h = PLANE_HALF
            corners = [(o.x + (su * u.x + sv * v.x) * h, o.y + (su * u.y + sv * v.y) * h, o.z + (su * u.z + sv * v.z) * h)
                       for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1))]
            coords = []
            for a, b in zip(corners, corners[1:]):
                coords.extend(a + b)
            self._segment(coords, matrix, rgb)
            return 1
        sketch_line = adsk.fusion.SketchLine.cast(entity)
        if sketch_line is not None:
            g = sketch_line.worldGeometry               # root space
            a, b = g.startPoint, g.endPoint
            self._segment([a.x, a.y, a.z, b.x, b.y, b.z], adsk.core.Matrix3D.create(), rgb)
            return 1
        log.info("mated {}: no highlight for this kind of geometry".format(native.objectType))
        return 0

    def _face(self, face, matrix, rgb):
        tri = _mesh(face)
        if tri is not None:
            indices = list(tri.nodeIndices)
            mesh = self._group.addMesh(adsk.fusion.CustomGraphicsCoordinates.create(list(tri.nodeCoordinatesAsDouble)),
                                       indices, list(tri.normalVectorsAsDouble), indices)
            mesh.transform = matrix
            mesh.isSelectable = False
            mesh.depthPriority = 10         # wins over the real (coincident) face
            # Flat, unlit colour: full strength in any visual style and lighting (a lit material
            # washed out under some styles), so the two sides always read clearly.
            mesh.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(rgb))
        self._strokes(list(face.edges), matrix, rgb, 3.0)

    def _strokes(self, edges, matrix, rgb, weight, priority=11):
        coords, lengths = _stroke_data(edges)
        self._add_lines(coords, lengths, matrix, rgb, weight, priority)

    def _add_lines(self, coords, lengths, matrix, rgb, weight, priority):
        if not lengths:
            return
        lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [], True, lengths)
        lines.transform = matrix
        lines.isSelectable = False
        lines.weight = weight
        lines.depthPriority = priority
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(rgb))

    def _segment(self, coords, matrix, rgb):
        """Thick coloured line pieces (pairs of points), drawn over everything like the faces."""
        n = len(coords) // 3
        lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), list(range(n)), False)
        lines.transform = matrix
        lines.isSelectable = False
        lines.weight = 5.0
        lines.depthPriority = 11
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
