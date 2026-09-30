"""Frame marking the area the crop ratio exports, drawn in the scene.

Each corner is given in viewport pixels, turned into a model point with
Viewport.viewToModelSpace, and slid along its view ray to a plane just in
front of the model, so it lands on the right pixel whatever the display
scaling and isn't hidden behind parts. Because it lives in the scene, it is
recomputed whenever the camera moves (coordinates edited in place, no
rebuild, which avoids flicker).

Lines only. It lives in its own tagged group so step renders don't erase it,
and it is removed before every image capture.
"""

import adsk.core
import adsk.fusion

from . import log

CROP_GROUP_ID = "BuildBookCrop"
FRAME_COLOR = (255, 120, 0)
OUTLINE_COLOR = (20, 20, 20)       # thin dark line around the frame, for contrast
OUTLINE_GAP = 2                    # px outside the frame


def _color(rgb):
    return adsk.core.Color.create(rgb[0], rgb[1], rgb[2], 255)


def _box(left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom), (left, top)]


def _shapes(rect):
    """Pixel polylines to draw: the frame and its dark outline."""
    left, top, w, h = rect
    right, bottom = left + w, top + h
    g = OUTLINE_GAP
    return {"frame": [_box(left, top, right, bottom)],
            "outline": [_box(left - g, top - g, right + g, bottom + g)]}


class _Projector:
    """Viewport pixel -> model point on a plane just in front of the model."""

    def __init__(self, design, viewport):
        self.viewport = viewport
        cam = viewport.camera
        self.eye = cam.eye
        target = cam.target
        self.perspective = cam.cameraType != adsk.core.CameraTypes.OrthographicCameraType
        d = self.eye.vectorTo(target)
        dist = max(d.length, 1e-6)
        d.normalize()
        self.dir = d
        # Depth of the nearest part of the model along the view direction.
        box = design.rootComponent.boundingBox
        depths = []
        for x in (box.minPoint.x, box.maxPoint.x):
            for y in (box.minPoint.y, box.maxPoint.y):
                for z in (box.minPoint.z, box.maxPoint.z):
                    depths.append(self.eye.vectorTo(adsk.core.Point3D.create(x, y, z)).dotProduct(d))
        near, span = min(depths), max(depths) - min(depths)
        # In front of the model (exploded copies reach further, hence the margin),
        # but not so close to the eye that the near clip plane cuts it.
        self.depth = max(near - 0.5 * span, 0.05 * dist)

    def point(self, x, y):
        p = self.viewport.viewToModelSpace(adsk.core.Point2D.create(x, y))
        ray = self.eye.vectorTo(p)
        if self.perspective:
            along = ray.dotProduct(self.dir)
            scale = self.depth / along if abs(along) > 1e-9 else 1.0
            return (self.eye.x + ray.x * scale, self.eye.y + ray.y * scale, self.eye.z + ray.z * scale)
        shift = self.depth - ray.dotProduct(self.dir)
        return (p.x + self.dir.x * shift, p.y + self.dir.y * shift, p.z + self.dir.z * shift)

    def coords(self, polylines):
        out = []
        for line in polylines:
            for x, y in line:
                out.extend(self.point(x, y))
        return out


class CropOverlay:
    def __init__(self):
        self._group = None
        self._entities = {}
        self.key = None     # (rect, vw, vh, camera) drawn now
        self.shape = None   # (vw, vh) the entities were built for

    def clear(self):
        try:
            if self._group is not None and self._group.isValid:
                self._group.deleteMe()
        except Exception:
            log.error("crop overlay clear")
        self._group = None
        self._entities = {}
        self.key = None
        self.shape = None

    def draw(self, design, viewport, rect):
        """Draw the frame at `rect` (left, top, width, height in viewport pixels)."""
        vw, vh = viewport.width, viewport.height
        cam = viewport.camera
        eye, target = cam.eye, cam.target
        key = (tuple(round(v) for v in rect), vw, vh,
               tuple(round(c, 4) for c in (eye.x, eye.y, eye.z, target.x, target.y, target.z)),
               round(cam.viewExtents, 4))
        alive = self._group is not None and self._group.isValid
        if alive and key == self.key:
            return
        shapes = _shapes(rect)
        projector = _Projector(design, viewport)
        if alive and self.shape == (vw, vh):
            try:
                for name, lines in shapes.items():
                    self._entities[name].coordinates.coordinates = projector.coords(lines)
                self.key = key
                return
            except Exception:
                log.error("crop overlay update; rebuilding")
        self._build(design, shapes, projector)
        self.shape = (vw, vh)
        self.key = key
        log.info("crop frame: {:.0f},{:.0f} {:.0f}x{:.0f} px in {}x{} viewport".format(
            rect[0], rect[1], rect[2], rect[3], vw, vh))

    def _build(self, design, shapes, projector):
        self.clear()
        group = design.rootComponent.customGraphicsGroups.add()
        group.id = CROP_GROUP_ID
        self._group = group
        styles = {"outline": (OUTLINE_COLOR, 1.0), "frame": (FRAME_COLOR, 3.0)}
        for name in ("outline", "frame"):
            lines = shapes[name]
            entity = group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(projector.coords(lines)),
                                    [], True, [len(line) for line in lines])
            color, weight = styles[name]
            entity.weight = weight
            entity.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(color))
            entity.isSelectable = False
            entity.depthPriority = 5
            self._entities[name] = entity
