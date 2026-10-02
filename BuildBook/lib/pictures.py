"""Pictures: steps, sections and the cover, their views, renders and annotations.

A picture is addressed as (kind, id): ("step", step id), ("section", section id) or
("cover", None). Steps keep their camera and annotations on the step; sections and
the cover keep theirs in a picture dict (model.new_picture):
- a section's picture shows the assembly as it stands at the end of that section
  (its last step, with nothing exploded), from the section's own view, else its
  last step's;
- the cover shows the whole assembly (no step view), from the cover's view.
The annotation editor is a separate floating palette (palette/annotate.html).
"""

import base64
import json
import os
import pathlib
import tempfile
import uuid

import adsk.core

from . import capture, log, model

EDITOR_ID = "buildBookAnnotate"
EDITOR_NAME = "BuildBook annotations"
PREVIEW_WIDTH = 1400


def holder(manual, kind, pid):
    """(dict holding "camera" and "annotations", title) for a picture, or (None, "")."""
    if kind == "cover":
        return manual.setdefault("cover", model.new_picture(enabled=True)), "Cover"
    if kind == "section":
        for si, sec in enumerate(manual["sections"], 1):
            if sec["id"] == pid:
                return sec.setdefault("image", model.new_picture()), "Section {}: {}".format(si, sec["title"])
        return None, ""
    if kind == "step":
        si, ti = model.step_numbers(manual, pid)
        _, step = model.find_step(manual, pid)
        if step is not None:
            return step, "{}.{}  {}".format(si, ti, step["title"])
    return None, ""


def show(ctrl, manual, kind, pid, move_camera=True, frame=True):
    """Put the model in the picture's state (and at its view); `frame` shows the crop frame
    (if it's turned on) -- off while capturing."""
    design = ctrl.design()
    viewport = ctrl.app.activeViewport
    if kind == "step":
        ctrl.show_step(pid, move_camera=move_camera, smooth=False)
    elif kind == "section":
        sec = model.find_section(manual, pid)
        steps = sec["steps"] if sec else []
        if not steps:
            ctrl.close_view()
        else:
            last = steps[-1]
            assembled = json.loads(json.dumps(manual))
            _, copy = model.find_step(assembled, last["id"])
            copy["explodes"] = []               # the section's end state: nothing exploded
            ctrl.scene.show(design, assembled, last["id"])
        pic = sec.get("image", {}) if sec else {}
        if move_camera:
            capture.apply_camera(viewport, pic.get("camera") or (steps[-1].get("camera") if steps else None), False)
    else:                                       # cover: the whole assembly
        ctrl.close_view()
        if move_camera:
            capture.apply_camera(viewport, manual.get("cover", {}).get("camera"), False)
    if kind != "step":
        ctrl.picture_view = True        # (after close_view / show_step, which reset it)
    if frame:
        ctrl.crop_overlay.key = None        # draw it again for this camera, not "already drawn"
        ctrl.update_overlay()
    else:
        ctrl.crop_overlay.clear()
    viewport.refresh()                      # the frame is edited in place: show it now
    viewport.refresh()


def render(ctrl, manual, kind, pid, width=None):
    """PNG bytes of the picture (its view, the manual's crop, never transparent)."""
    show(ctrl, manual, kind, pid, frame=False)
    settings = json.loads(json.dumps(manual))
    image = settings["settings"].setdefault("image", {})
    image["transparent"] = False
    if width:
        image["width"] = int(width)
    tmp = os.path.join(tempfile.gettempdir(), "buildbook-pic-{}.png".format(uuid.uuid4().hex[:8]))
    try:
        capture.save_png(ctrl.app, settings, None, tmp)
        if kind != "step":
            save_thumb(ctrl, kind, pid)         # the panel's preview of it, while it's on screen
        with open(tmp, "rb") as handle:
            return handle.read()
    finally:
        for leftover in (tmp, tmp + ".png"):
            try:
                os.remove(leftover)
            except OSError:
                pass


def thumb_key(kind, pid):
    """Thumbnail name of a picture (steps: their id, as before)."""
    return pid if kind == "step" else ("cover" if kind == "cover" else "section-" + pid)


def save_thumb(ctrl, kind, pid):
    """Thumbnail of the picture as it's on screen now (call right after show())."""
    ctrl.crop_overlay.clear()
    capture.save_thumbnail(ctrl, thumb_key(kind, pid))


def data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def png_size(png):
    """(width, height) from a PNG's header."""
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


class Editor:
    """The floating annotation editor: opens on a picture, saves what's drawn into the manual."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.kind, self.pid = None, None
        self.payload = None

    def palette(self, create=False):
        ui = self.ctrl.ui
        pal = ui.palettes.itemById(EDITOR_ID)
        if pal is None and create:
            here = pathlib.Path(os.path.abspath(__file__)).parent.parent
            url = (here / "palette" / "annotate.html").as_uri()      # (see BuildBook._palette_url)
            pal = ui.palettes.add(EDITOR_ID, EDITOR_NAME, url, True, True, True, 1100, 820)
            pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
            handler = _EditorHandler(self)
            pal.incomingFromHTML.add(handler)
            self.ctrl.keep(handler)
        return pal

    def open(self, kind, pid):
        """Render the picture (then put the view back) and show it in the editor."""
        ctrl = self.ctrl
        manual = ctrl.load()
        pic, title = holder(manual, kind, pid)
        if pic is None:
            return
        return_to = ctrl.scene.step_id
        camera = capture.camera_to_dict(ctrl.app.activeViewport.camera)
        try:
            with log.timed("annotate preview"):
                png = render(ctrl, manual, kind, pid, PREVIEW_WIDTH)
        finally:
            if kind != "step":                  # a step's editor leaves you at the step
                if return_to:
                    ctrl.show_step(return_to)
                else:
                    ctrl.close_view()
                capture.apply_camera(ctrl.app.activeViewport, camera, smooth=False)
        width, height = png_size(png)
        self.kind, self.pid = kind, pid
        self.payload = {
            "kind": kind, "id": pid, "title": title, "image": data_url(png),
            "width": width, "height": height,
            "annotations": pic.get("annotations", []),
            "defaults": manual["settings"].get("annotation", {}),
        }
        pal = self.palette(create=True)
        pal.isVisible = True
        self.send()

    def send(self):
        pal = self.palette()
        if pal is not None and self.payload is not None:
            pal.sendInfoToHTML("load", json.dumps(self.payload))

    def handle(self, action, data):
        ctrl = self.ctrl
        if action == "ready":
            self.send()
        elif action == "save":
            manual = ctrl.load()
            pic, _ = holder(manual, data.get("kind"), data.get("id"))
            if pic is not None:
                pic["annotations"] = list(data.get("annotations") or [])
                ctrl.save(manual)
                if self.payload and self.payload["kind"] == data.get("kind") and self.payload["id"] == data.get("id"):
                    self.payload["annotations"] = pic["annotations"]
                ctrl.push_state()
        elif action == "defaults":
            manual = ctrl.load()
            manual["settings"].setdefault("annotation", {}).update(data.get("style") or {})
            ctrl.save(manual)
            if self.payload:
                self.payload["defaults"] = manual["settings"]["annotation"]
        elif action == "refresh":
            if self.kind:
                self.open(self.kind, self.pid)
        elif action == "close":
            pal = self.palette()
            if pal is not None:
                pal.isVisible = False

    def close(self):
        pal = self.palette()
        if pal is not None:
            pal.deleteMe()


class _EditorHandler(adsk.core.HTMLEventHandler):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor

    def notify(self, args):
        try:
            event = adsk.core.HTMLEventArgs.cast(args)
            if event.action == "response":
                return
            self.editor.handle(event.action, json.loads(event.data) if event.data else {})
            event.returnData = "OK"
        except Exception:
            log.error("annotation editor " + str(getattr(args, "action", "?")))
