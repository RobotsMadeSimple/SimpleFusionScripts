"""Drag-box picking for BuildBook's part dialogs (Pick parts, explode moves).

Those dialogs don't use Fusion's selection (its highlight drew over nearby parts), so Fusion's
own box select does nothing there. This draws the box while the mouse is dragged and works out
which parts it takes, the way Fusion does: dragging left to right takes the parts fully inside
the box, right to left also the ones it touches.
"""

import adsk.core
import adsk.fusion

from . import log, refs
from .overlay import _Projector

GROUP_ID = "BuildBookBoxSelect"
MIN_DRAG = 6                    # pixels before a press-and-drag counts as a box
COLOR_WINDOW = (0, 120, 215)    # left to right: fully inside (solid blue, like Fusion)
COLOR_CROSSING = (40, 160, 70)  # right to left: touching (green, like Fusion)


class Box:
    def __init__(self):
        self.start = None       # (x, y) where the left button went down
        self.end = None
        self._group = None

    def press(self, pos):
        self.start, self.end = (pos.x, pos.y), (pos.x, pos.y)

    def active(self):
        return self.start is not None and self.end is not None and \
            abs(self.end[0] - self.start[0]) + abs(self.end[1] - self.start[1]) > MIN_DRAG

    def crossing(self):
        return self.end[0] < self.start[0]

    def rect(self):
        (x0, y0), (x1, y1) = self.start, self.end
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def drag(self, design, viewport, pos):
        """The mouse moved with the button down: redraw the box."""
        if self.start is None:
            return
        self.end = (pos.x, pos.y)
        if not self.active():
            return
        try:
            left, top, right, bottom = self.rect()
            pts = [(left, top), (right, top), (right, bottom), (left, bottom), (left, top)]
            coords = _Projector(design, viewport).coords([pts])
            if self._group is None or not self._group.isValid:
                self._group = design.rootComponent.customGraphicsGroups.add()
                self._group.id = GROUP_ID
                self._lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords),
                                                   [], True, [len(pts)])
                self._lines.isSelectable = False
                self._lines.weight = 1.5
                self._lines.depthPriority = 2
            else:
                self._lines.coordinates.coordinates = coords
            color = COLOR_CROSSING if self.crossing() else COLOR_WINDOW
            self._lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(*color, 255))
            if self.crossing():
                self._lines.lineStylePattern = adsk.fusion.LineStylePatterns.dashedLineStylePattern
            else:
                self._lines.lineStylePattern = adsk.fusion.LineStylePatterns.continuousLineStylePattern
            viewport.refresh()
        except Exception:
            log.error("box select: draw")

    def release(self):
        """The button went up: (left, top, right, bottom, crossing) if it was a box, else None."""
        result = (self.rect() + (self.crossing(),)) if self.active() else None
        self.clear()
        return result

    def clear(self):
        self.start = self.end = None
        try:
            if self._group is not None and self._group.isValid:
                self._group.deleteMe()
        except Exception:
            pass
        self._group = None


def screen_box(viewport, box, offset=(0.0, 0.0, 0.0)):
    """(left, top, right, bottom) on screen of a BoundingBox3D (moved by `offset`)."""
    xs, ys = [], []
    for x in (box.minPoint.x, box.maxPoint.x):
        for y in (box.minPoint.y, box.maxPoint.y):
            for z in (box.minPoint.z, box.maxPoint.z):
                p = viewport.modelToViewSpace(adsk.core.Point3D.create(x + offset[0], y + offset[1], z + offset[2]))
                xs.append(p.x)
                ys.append(p.y)
    return min(xs), min(ys), max(xs), max(ys)


def takes(sel, shape):
    """Does box `sel` (left, top, right, bottom, crossing) take something at `shape` on screen?"""
    left, top, right, bottom, crossing = sel
    l, t, r, b = shape
    if crossing:
        return l <= right and r >= left and t <= bottom and b >= top
    return l >= left and r <= right and t >= top and b <= bottom


def leaf_parts(design, skip=None, showing=None):
    """Every part with bodies of its own on screen (a split part: one per body), as (path, part,
    the bounding box of those bodies). `skip(path)`: leave it out; `showing(body)`: whether a body
    counts as on screen (default: visible)."""
    showing = showing or (lambda b: b.isVisible)
    out = []
    for path, part in refs.path_index(design).items():
        if skip is not None and skip(path):
            continue
        try:
            if isinstance(part, refs.BodyPart):
                if showing(part.body):
                    out.append((path, part, part.body.boundingBox))
                continue
            if refs.is_split(part):
                continue                # (its bodies are listed themselves)
            bodies = [b for b in part.bRepBodies if showing(b)]
            if not bodies:
                continue
            box = bodies[0].boundingBox.copy()
            for b in bodies[1:]:
                box.combine(b.boundingBox)
            out.append((path, part, box))
        except Exception:
            continue
    return out
