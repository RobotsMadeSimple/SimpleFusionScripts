"""Step cameras and PNG export.

A step's camera is stored in the manual as plain numbers so it travels with
the design. Exported images are named "Section 1 - Step 2 - Step name.png"
(step numbers count within their section) and go to the export folder,
by default Downloads/BuildBook - <manual title>.
"""

import os
import pathlib
import tempfile
import uuid

import adsk
import adsk.core

from . import crop, log, model, paths


# ---------------------------------------------------------------- camera

def camera_to_dict(camera):
    e, t, u = camera.eye, camera.target, camera.upVector
    return {
        "eye": [e.x, e.y, e.z],
        "target": [t.x, t.y, t.z],
        "up": [u.x, u.y, u.z],
        "type": int(camera.cameraType),
        "extents": camera.viewExtents,
        "angle": camera.perspectiveAngle,
    }


def current_camera(viewport, ratio=None):
    """The view as it is on screen now, for saving. Fusion is given a moment to finish first:
    with a SpaceMouse the camera Fusion reports could still be the one from before the move,
    so a saved view snapped back to where it was."""
    viewport.refresh()
    _pump()
    data = camera_to_dict(viewport.camera)
    ratio = ratio or crop.VIEWPORT
    width = frame_width(viewport, ratio)
    if width and data.get("type") == int(adsk.core.CameraTypes.OrthographicCameraType):
        # Work out the real zoom: put the camera back exactly as Fusion reports it, see how much
        # the frame shows then, and scale the extents until it matches what was on screen. The
        # saved extents are then right (and Fusion's camera is back in step with the screen).
        for _ in range(4):
            apply_camera(viewport, dict(data), smooth=False)
            got = frame_width(viewport, ratio)
            if not got or abs(got - width) <= width * 0.002:
                break
            log.info("view saved: zoom recalculated ({:.2f} cm shown with the reported extents, {:.2f} on screen)".format(
                got, width))
            data["extents"] = data["extents"] * width / got
    if width:
        # What the picture really shows (see apply_camera): the crop frame's width in the model.
        data["frameWidth"], data["frameRatio"] = width, ratio
    log.info("view saved: {} / crop frame {:.2f} cm wide".format(describe_camera(data), width or 0))
    return data


def frame_width(viewport, ratio):
    """How wide the crop frame (the area the picture shows) is in model units, measured from the
    screen itself. With a SpaceMouse the camera's extents don't follow its zoom in an orthographic
    view, but this does; and as it's the picture's own area, it means the same on any screen size."""
    try:
        vw, vh = viewport.width, viewport.height
        left, top, w, h = crop.centered_rect(crop.ratio_value(ratio, vw, vh), vw, vh)
        y = top + h / 2.0
        a = viewport.viewToModelSpace(adsk.core.Point2D.create(left, y))
        b = viewport.viewToModelSpace(adsk.core.Point2D.create(left + w, y))
        return a.distanceTo(b)
    except Exception:
        return None


def describe_camera(data):
    if not data:
        return "none"
    e, t = data["eye"], data["target"]
    dist = sum((a - b) ** 2 for a, b in zip(e, t)) ** 0.5
    return "eye ({:.1f}, {:.1f}, {:.1f}) target ({:.1f}, {:.1f}, {:.1f}) distance {:.1f} cm, {} extents {:.1f}".format(
        *e, *t, dist, "ortho" if data.get("type") == int(adsk.core.CameraTypes.OrthographicCameraType) else "persp", data.get("extents") or 0)


def apply_camera(viewport, data, smooth=True):
    """Move the viewport to a stored camera. Returns False if there's none."""
    if not data:
        return False
    cam = viewport.camera
    cam.cameraType = data.get("type", cam.cameraType)
    ortho = cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType
    if data.get("angle"):
        cam.perspectiveAngle = data["angle"]
    # Extents only for orthographic views (they're its zoom). In perspective the eye and target
    # already say how close the view is, and setting extents too makes Fusion move the eye to
    # match them: a view saved after navigating with a SpaceMouse (which moves the eye without
    # keeping Fusion's extents in step) came back zoomed out. Set first, so eye / target win.
    if ortho and data.get("extents"):
        cam.viewExtents = data["extents"]
    cam.eye = adsk.core.Point3D.create(*data["eye"])
    cam.target = adsk.core.Point3D.create(*data["target"])
    cam.upVector = adsk.core.Vector3D.create(*data["up"])
    cam.isFitView = False
    if ortho and data.get("frameWidth"):
        smooth = False                  # (the zoom is checked against the screen right after)
    cam.isSmoothTransition = smooth
    viewport.camera = cam
    # Orthographic: the zoom on screen can differ from the saved extents (a SpaceMouse zooms
    # without updating them). Scale the extents until the crop frame shows as much as when saved.
    want = data.get("frameWidth")
    if ortho and want and not smooth:
        for _ in range(3):
            got = frame_width(viewport, data.get("frameRatio") or crop.VIEWPORT)
            if not got or abs(got - want) <= want * 0.002:
                break
            cam = viewport.camera
            cam.viewExtents = cam.viewExtents * want / got
            cam.isSmoothTransition = False
            viewport.camera = cam
            log.info("view applied: corrected zoom, {:.2f} cm wide on screen, want {:.2f}".format(got, want))
    if not smooth:
        try:
            log.info("view applied: asked " + describe_camera(data) + " / got " + describe_camera(camera_to_dict(viewport.camera)))
        except Exception:
            pass
    return True


# ---------------------------------------------------------------- naming

image_name = model.image_name
safe_filename = model.safe_filename


def default_folder(manual):
    title = safe_filename(manual.get("title") or "") or "Build manual"
    return os.path.join(os.path.expanduser("~"), "Downloads", "BuildBook - " + title)


def export_folder(manual):
    return manual["settings"].get("exportFolder") or default_folder(manual)


# ---------------------------------------------------------------- crop

def at_saved_view(viewport, data, tol=0.01):
    """True if the viewport camera is (close to) a stored camera."""
    if not data:
        return False
    cam = viewport.camera
    eye, target = cam.eye, cam.target
    dist = max(eye.distanceTo(target), 1e-6)
    if (eye.distanceTo(adsk.core.Point3D.create(*data["eye"])) > tol * dist
            or target.distanceTo(adsk.core.Point3D.create(*data["target"])) > tol * dist):
        return False
    up = cam.upVector
    su = data["up"]
    if up.x * su[0] + up.y * su[1] + up.z * su[2] < 0.999:
        return False
    extents = data.get("extents")
    return not extents or abs(cam.viewExtents / extents - 1) < 2 * tol


def effective_crop(manual, vw, vh):
    """The area export captures, as viewport fractions, or None for the full view.

    It's the largest rectangle of the manual's crop ratio centred in the
    viewport ("Match viewport" = the whole view).
    """
    ratio_key = manual["settings"].get("image", {}).get("ratio", crop.VIEWPORT)
    if ratio_key in (crop.VIEWPORT, crop.FREE):
        return None
    ratio = crop.ratio_value(ratio_key, vw, vh)
    return crop.from_pixels(crop.centered_rect(ratio, vw, vh), vw, vh)


# ---------------------------------------------------------------- export

def _render(viewport, path, width, height, transparent):
    opts = adsk.core.SaveImageFileOptions.create(path)
    opts.width = int(width)
    opts.height = int(height)
    opts.isAntiAliased = True
    opts.isBackgroundTransparent = bool(transparent)
    return viewport.saveAsImageFileWithOptions(opts) and os.path.exists(path)


def render_raw(app, manual, path):
    """Fusion's part of saving a picture: render the view to `path` (a PNG). Returns the crop box
    to cut from it afterwards (finish_png), or None when the render is already the picture.
    Split from the cutting so a PDF export renders every view first and cuts them all at once."""
    image = manual["settings"].get("image", {})
    out_width = int(image.get("width", 1600))
    transparent = bool(image.get("transparent", False))
    viewport = app.activeViewport
    viewport.refresh()
    _pump()
    vw, vh = viewport.width, viewport.height
    frac = effective_crop(manual, vw, vh)
    if crop.is_full(frac):
        if not _render(viewport, path, out_width, round(out_width * vh / float(vw)), transparent):
            raise RuntimeError("Fusion could not save " + path)
        return None
    rw, rh, box = crop.render_plan(frac, out_width, vw, vh)
    if not _render(viewport, path, rw, rh, transparent):
        raise RuntimeError("Fusion could not render " + path)
    return box


def finish_png(path, box, keep_alpha=False, level=6):
    """PNG bytes of a render_raw file, cut to `box` (no Fusion calls: safe in a worker thread)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if box is None:
        return data
    _, _, channels, all_rows = crop.read_png(data)
    rows, out_channels = crop.crop_rows(all_rows, channels, box, keep_alpha=keep_alpha)
    return crop.write_png(rows, box[2], out_channels, level)


def save_png(app, manual, step, path):
    """Save the step's image: the whole view, or the crop-ratio area cut from a larger render.

    Fusion only renders whole views, so for a crop the view is rendered at the
    viewport's shape, scaled so the crop comes out at the image width, then
    cut out. The render goes to a PNG in the system temp folder (Fusion
    ignores a .bmp extension and writes "<name>.bmp.png" anyway) and is always
    deleted afterwards.
    """
    image = manual["settings"].get("image", {})
    out_width = int(image.get("width", 1600))
    transparent = bool(image.get("transparent", False))
    viewport = app.activeViewport
    viewport.refresh()
    _pump()
    vw, vh = viewport.width, viewport.height

    frac = effective_crop(manual, vw, vh)
    if crop.is_full(frac):
        if not _render(viewport, path, out_width, round(out_width * vh / float(vw)), transparent):
            raise RuntimeError("Fusion could not save " + path)
        return

    rw, rh, box = crop.render_plan(frac, out_width, vw, vh)
    tmp = os.path.join(tempfile.gettempdir(), "buildbook-render-{}.png".format(uuid.uuid4().hex[:8]))
    try:
        if not _render(viewport, tmp, rw, rh, transparent):
            raise RuntimeError("Fusion could not render " + path)
        with open(tmp, "rb") as handle:
            _, _, channels, all_rows = crop.read_png(handle.read())
        rows, out_channels = crop.crop_rows(all_rows, channels, box, keep_alpha=transparent)
        with open(path, "wb") as handle:
            handle.write(crop.write_png(rows, box[2], out_channels))
    finally:
        for leftover in (tmp, tmp + ".png"):
            try:
                os.remove(leftover)
            except OSError:
                pass


def remove_render_leftovers(folder):
    """Delete "*.render.bmp.png"-style temp files older exports left in the export folder."""
    removed = 0
    try:
        for name in os.listdir(folder):
            if ".png.render." in name and name.lower().endswith((".png", ".bmp")):
                try:
                    os.remove(os.path.join(folder, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    if removed:
        log.info("export: removed {} leftover render file(s) from {}".format(removed, folder))
    return removed


# ---------------------------------------------------------------- thumbnails

THUMB_WIDTH = 240
_THUMB_DIR = paths.data_dir("thumbs")       # per user, not in the add-in folder (lib/paths.py)


def _document_key(app):
    """Stable name for the open design, for thumbnail file names."""
    doc = app.activeDocument
    key = ""
    try:
        key = doc.dataFile.id if doc.dataFile else ""
    except Exception:
        key = ""
    return model.safe_filename(key or doc.name).replace(" ", "_") or "design"


def thumbnail_path(app, step_id):
    return os.path.join(_THUMB_DIR, "{}_{}.png".format(_document_key(app), step_id))


def delete_thumbnail(app, key):
    """Delete one thumbnail (a step id, "section-<id>" or "cover"), if there is one."""
    try:
        os.remove(thumbnail_path(app, key))
    except OSError:
        pass


def save_thumbnail(ctrl, step_id):
    """Small picture of the canvas as it is now, for the step list (kept locally)."""
    try:
        os.makedirs(_THUMB_DIR, exist_ok=True)
        viewport = ctrl.app.activeViewport
        height = max(1, round(THUMB_WIDTH * viewport.height / float(viewport.width)))
        ctrl.crop_overlay.clear()
        viewport.saveAsImageFile(thumbnail_path(ctrl.app, step_id), THUMB_WIDTH, height)
    except Exception:
        log.error("thumbnail")
    finally:
        ctrl.update_overlay()


def thumbnail_urls(app, manual):
    """step id (or "section-<id>", "cover") -> file URL of its thumbnail (with a version so the
    panel reloads changes)."""
    out = {}
    keys = [step["id"] for _, step in model.ordered_steps(manual)]
    keys += ["section-" + sec["id"] for sec in manual["sections"]] + ["cover"]
    for key in keys:
        path = thumbnail_path(app, key)
        if os.path.exists(path):
            out[key] = pathlib.Path(path).as_uri() + "?v={}".format(int(os.path.getmtime(path)))
    return out


def export_step(ctrl, manual, step, folder):
    """Show a step at its saved camera and write its PNG. Returns the file path."""
    os.makedirs(folder, exist_ok=True)
    remove_render_leftovers(folder)
    ctrl.show_step(step["id"], move_camera=True, smooth=False)
    ctrl.crop_overlay.clear()          # never capture the crop frame itself
    path = os.path.join(folder, image_name(manual, step))
    try:
        with log.timed("export " + os.path.basename(path)):
            save_png(ctrl.app, manual, step, path)
        save_thumbnail(ctrl, step["id"])
    finally:
        ctrl.update_overlay()
    log.info("export: " + path)
    return path


def pump():
    _pump()


def _pump():
    """Let Fusion redraw before capturing (camera moves and graphics are async)."""
    do_events = getattr(adsk, "doEvents", None)
    if do_events:
        for _ in range(3):
            do_events()
