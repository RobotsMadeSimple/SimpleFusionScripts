"""HoleThreadCallouts -- one image that shows which holes to tap.

Set up one or more thread views of the open part (each its own camera). In each,
the threaded holes it can see are filled in their size's colour (colour-blind-safe:
M3 yellow, M4 sky blue, M5 orange, M6 reddish purple...) in the wireframe-with-
visible-edges style, with a callout per size ("2x M3") whose arrow and text can be
dragged; each hole is called out once, in the first view that shows it. Dowel holes
can be marked (quartered-circle symbol). A shaded view ends the row. Export stitches
the views into one PNG (and copies it to the clipboard).

Data lives in the design (attribute ThreadCallouts/data, its first name), per component, so an
activated component in an assembly has its own views.
"""

import base64
import json
import os
import pathlib
import tempfile
import time
import traceback
import uuid

import adsk.core
import adsk.fusion

from .lib import callouts, clipboard, holes as holes_mod, log, mcp_server

PALETTE_ID = "holeThreadCalloutsPalette"
PALETTE_NAME = "Hole & Thread Callouts"
COMMAND_ID = "holeThreadCalloutsShow"
RELOAD_EVENT = "holeThreadCalloutsReload"   # custom event: reload the add-in's code (the panel's reload button)
EDITOR_ID = "holeThreadCalloutsEditor"
EDITOR_NAME = "Hole & thread callouts: edit view"
ATTR_GROUP, ATTR_NAME = "ThreadCallouts", "data"   # (kept from the first name: existing designs' views)
THUMB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thumbs")
THUMB_W = 240
STYLES = {"thread": "WireframeWithVisibleEdgesOnlyVisualStyle", "shaded": "ShadedWithVisibleEdgesOnlyVisualStyle"}

_handlers = []
_events = []          # (event, handler) pairs, for reload_addin
_ctrl = None
_mcp = None           # agent access (lib/mcp_server.py), when switched on in the panel


def default_data():
    return {"version": 1, "settings": {"guess": True, "colors": {}, "height": 900, "gap": 24, "folder": ""},
            "parts": {}}


def _safe(read, default=None):
    try:
        return read()
    except Exception:
        return default


# ---------------------------------------------------------------- cameras (as BuildBook)

def camera_to_dict(camera):
    e, t, u = camera.eye, camera.target, camera.upVector
    return {"eye": [e.x, e.y, e.z], "target": [t.x, t.y, t.z], "up": [u.x, u.y, u.z],
            "type": int(camera.cameraType), "extents": camera.viewExtents, "angle": camera.perspectiveAngle}


def apply_camera(viewport, data, smooth=False):
    if not data:
        return False
    cam = viewport.camera
    cam.cameraType = data.get("type", cam.cameraType)
    cam.eye = adsk.core.Point3D.create(*data["eye"])
    cam.target = adsk.core.Point3D.create(*data["target"])
    cam.upVector = adsk.core.Vector3D.create(*data["up"])
    if data.get("angle"):
        cam.perspectiveAngle = data["angle"]
    if data.get("extents"):
        cam.viewExtents = data["extents"]
    cam.isFitView = False
    cam.isSmoothTransition = smooth
    viewport.camera = cam
    return True


def set_style(viewport, kind):
    """Fusion's visual style for a kind of view: wireframe with visible edges (thread), shaded
    with visible edges (shaded, and Fusion's normal look when the panel closes)."""
    try:
        viewport.visualStyle = getattr(adsk.core.VisualStyles, STYLES["shaded" if kind != "thread" else "thread"])
    except Exception:
        log.error("visual style")


def _pump():
    do_events = getattr(adsk, "doEvents", None)
    if do_events:
        for _ in range(3):
            do_events()


class Controller:
    def __init__(self, app):
        self.app = app
        self.ui = app.userInterface
        self._notice = None
        self._editing = None            # view id open in the editor
        self._editor_payload = None
        self._export_path = None        # where the stitched image goes when it comes back
        self._holes_cache = None        # (what it was found for, holes)
        self._queue = []                # export every configuration: [(row id, path, name)] still to do
        self._written = None
        self._return_config = None
        self.target = None              # an occurrence an agent (lib/mcp_tools.py) works on; None = the activated one
        self.mcp_export = None          # an agent's export waiting for the panel's stitched image (mcp_server.Deferred)
        self._pending_stitch = None     # a stitch job waiting for the panel to load

    # ------------------------------------------------------------ data

    def design(self):
        return adsk.fusion.Design.cast(self.app.activeProduct)

    def load(self):
        design = self.design()
        data = default_data()
        if design is None:
            return data
        attr = design.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        if attr is not None:
            try:
                stored = json.loads(attr.value)
                data["settings"].update(stored.get("settings", {}))
                data["parts"] = stored.get("parts", {})
            except ValueError:
                log.error("read data")
        return data

    def save(self, data):
        design = self.design()
        if design is not None:
            design.attributes.add(ATTR_GROUP, ATTR_NAME, json.dumps(data))

    def part_key(self):
        """Views are per component, and per configuration in a configured design."""
        comp, _ = holes_mod.context(self.design(), self.target)
        key = _safe(lambda: comp.id, "") or comp.name
        row = self.active_config()
        return key + ("@" + row.id if row is not None else "")

    # ------------------------------------------------------------ configurations

    def config_table(self):
        design = self.design()
        if design is None or not (_safe(lambda: design.isConfiguredDesign, False)
                                  or _safe(lambda: design.isConfiguration, False)):
            return None
        return _safe(lambda: design.configurationTopTable)

    def active_config(self):
        table = self.config_table()
        return _safe(lambda: table.activeRow) if table is not None else None

    def configs(self):
        """[(row id, name)] of a configured design's configurations, else []."""
        table = self.config_table()
        if table is None:
            return []
        rows = _safe(lambda: table.rows)
        out = []
        for i in range(_safe(lambda: rows.count, 0) or 0):
            row = rows.item(i)
            if not _safe(lambda: row.isTestRow, False):
                out.append((row.id, row.name))
        return out

    def activate_config(self, row_id):
        table = self.config_table()
        if table is None:
            return False
        rows = table.rows
        for i in range(rows.count):
            row = rows.item(i)
            if row.id == row_id:
                if _safe(lambda: table.activeRow.id) == row_id:
                    return True
                with log.timed("activate configuration " + row.name):
                    ok = row.activate()
                adsk.doEvents()
                return ok
        return False

    def part(self, data):
        part = data["parts"].setdefault(self.part_key(), {})
        part.setdefault("views", [])
        part.setdefault("marks", {})
        part.setdefault("pinned", {})
        return part

    @staticmethod
    def ordered(part):
        """Thread views in order, then the shaded view (always last)."""
        views = part["views"]
        return [v for v in views if v["kind"] == "thread"] + [v for v in views if v["kind"] == "shaded"][:1]

    # ------------------------------------------------------------ rendering a view

    def _find_holes(self, data, part):
        """The part's holes; re-found only when the model (timeline), the marks or the guess setting change."""
        design = self.design()
        timeline = _safe(lambda: (design.timeline.count, design.timeline.markerPosition), None)
        bodies = holes_mod.bodies(design, self.target)
        _, occ = holes_mod.context(design, self.target)
        place = _safe(lambda: occ.fullPathName, "") if occ is not None else ""    # (each occurrence: its own spot)
        key = json.dumps([self.part_key(), place, timeline, [_safe(lambda: b.faces.count, 0) for b in bodies],
                          [_safe(lambda: b.revisionId, "") for b in bodies],
                          data["settings"].get("guess", True), part["marks"]], sort_keys=True)
        if self._holes_cache and self._holes_cache[0] == key:
            return self._holes_cache[1]
        with log.timed("find holes"):
            found = holes_mod.find(design, data["settings"].get("guess", True), part["marks"], self.target)
        self._holes_cache = (key, found)
        return found

    def _layout(self, data, part, found):
        """{view id: projection} for every thread view, and the hole -> view assignment.
        Moves the camera through the views (the caller puts it back)."""
        design, viewport = self.design(), self.app.activeViewport
        bodies = holes_mod.bodies(design, self.target)
        projections = {}
        for view in self.ordered(part):
            apply_camera(viewport, view.get("camera"))
            viewport.refresh()
            _pump()
            with log.timed("project " + view["id"]):
                projections[view["id"]] = holes_mod.project(design, viewport, found if view["kind"] == "thread" else [],
                                                             bodies, view.get("padding"))
        order = [v["id"] for v in self.ordered(part) if v["kind"] == "thread"]
        visible = {vid: {h["id"] for h in projections[vid]["holes"]} for vid in order}
        return projections, callouts.assign(order, visible, part.get("pinned"))

    def _capture(self, data, view, projection):
        """PNG bytes of the view (camera already set): the viewport re-rendered so the part's
        crop comes out `height` px tall, in the view's style."""
        viewport = self.app.activeViewport
        apply_camera(viewport, view.get("camera"))
        viewport.visualStyle = getattr(adsk.core.VisualStyles, STYLES[view["kind"]])
        viewport.refresh()
        _pump()
        crop_h = max(0.05, projection["crop"][3])
        scale = float(data["settings"].get("height", 900)) / (crop_h * viewport.height)
        width = int(min(8000, viewport.width * scale))
        height = int(min(8000, viewport.height * scale))
        tmp = os.path.join(tempfile.gettempdir(), "holethreadcallouts-{}.png".format(uuid.uuid4().hex[:8]))
        try:
            if not viewport.saveAsImageFile(tmp, width, height):
                raise RuntimeError("Fusion couldn't save the view")
            path = tmp if os.path.exists(tmp) else tmp + ".png"
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            for leftover in (tmp, tmp + ".png"):
                try:
                    os.remove(leftover)
                except OSError:
                    pass

    def render(self, view_ids=None):
        """[{view, image (data URL), crop, annotations}] for the views (all, in order, by default).
        The camera and visual style are put back afterwards."""
        data = self.load()
        part = self.part(data)
        viewport = self.app.activeViewport
        camera = camera_to_dict(viewport.camera)
        style = viewport.visualStyle
        out = []
        remapped = False
        try:
            found = self._find_holes(data, part)
            projections, assigned = self._layout(data, part, found)
            for view in self.ordered(part):
                if view_ids is not None and view["id"] not in view_ids:
                    continue
                projection = projections[view["id"]]
                if view.get("crop") != projection["crop"]:
                    # The framing changed (padding, camera, window size): keep the user's annotations
                    # and moved callouts on the same spot of the part.
                    view["annotations"] = callouts.remap(view.get("annotations", []), view.get("crop"),
                                                         projection["crop"])
                    view["crop"] = projection["crop"]
                    remapped = True
                annotations = callouts.build(view, projection["holes"], assigned, view.get("annotations"),
                                             data["settings"].get("colors"))
                png = self._capture(data, view, projection)
                out.append({"view": view, "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                            "crop": projection["crop"], "annotations": annotations})
        finally:
            viewport.visualStyle = style
            apply_camera(viewport, camera)
        if remapped:
            self.save(data)
        return out

    # ------------------------------------------------------------ panel state

    def state(self):
        design = self.design()
        if design is None:
            return {"error": "Open a Fusion design to use Hole & Thread Callouts."}
        data = self.load()
        part = self.part(data)
        comp, _ = holes_mod.context(design, self.target)
        try:
            found = self._find_holes(data, part)
        except Exception:
            log.error("find holes")
            found = []
        summary = {}
        for h in found:
            row = summary.setdefault((h["kind"], h["label"]), {"kind": h["kind"], "label": h["label"], "count": 0,
                                                              "sources": {}, "color": callouts.color_for(
                                                                  h["label"], data["settings"].get("colors"))})
            row["count"] += 1
            row["sources"][h["source"]] = row["sources"].get(h["source"], 0) + 1
        views = []
        n = 0
        for view in self.ordered(part):
            if view["kind"] == "thread":
                n += 1
            views.append({"id": view["id"], "kind": view["kind"],
                          "name": "View {}".format(n) if view["kind"] == "thread" else "Shaded",
                          "hasCamera": bool(view.get("camera")),
                          "annotations": len([a for a in view.get("annotations", []) if not a.get("auto")]),
                          "thumb": self._thumb_url(view["id"])})
        return {
            "error": "",
            "document": self.app.activeDocument.name if self.app.activeDocument else "",
            "component": comp.name,
            "configs": self._config_tabs(data),
            "views": views,
            "holes": sorted(summary.values(), key=lambda r: (r["kind"] == "dowel", callouts.size_key(r["label"]))),
            "settings": data["settings"],
            "palette": callouts.OKABE_ITO,
            "notice": self._notice,
            "mcp": _mcp_status(),
        }

    def _config_tabs(self, data):
        """[{id, name, active, views}] for the panel's tabs (empty unless a configured design)."""
        active = self.active_config()
        comp, _ = holes_mod.context(self.design(), self.target)
        base = _safe(lambda: comp.id, "") or comp.name
        out = []
        for row_id, name in self.configs():
            part = data["parts"].get(base + "@" + row_id, {})
            out.append({"id": row_id, "name": name, "active": active is not None and active.id == row_id,
                        "views": len([v for v in part.get("views", []) if v.get("kind") == "thread"])})
        return out

    def push_state(self):
        palette = self.ui.palettes.itemById(PALETTE_ID)
        if palette is None or not palette.isVisible:
            return
        try:
            with log.timed("panel state"):
                text = json.dumps(self.state())
            palette.sendInfoToHTML("state", text)
            self._notice = None
        except Exception:
            log.error("push state")

    # ------------------------------------------------------------ thumbnails

    def _thumb_path(self, view_id):
        doc = self.app.activeDocument
        key = _safe(lambda: doc.dataFile.id, "") or (doc.name if doc else "design")
        safe = "".join(c if c.isalnum() else "_" for c in key)
        return os.path.join(THUMB_DIR, "{}_{}.png".format(safe, view_id))

    def _thumb_url(self, view_id):
        path = self._thumb_path(view_id)
        if not os.path.exists(path):
            return None
        return pathlib.Path(path).as_uri() + "?v={}".format(int(os.path.getmtime(path)))

    def _save_thumb(self, view):
        viewport = self.app.activeViewport
        style = viewport.visualStyle
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
            viewport.visualStyle = getattr(adsk.core.VisualStyles, STYLES[view["kind"]])
            viewport.refresh()
            _pump()
            height = max(1, round(THUMB_W * viewport.height / float(viewport.width)))
            viewport.saveAsImageFile(self._thumb_path(view["id"]), THUMB_W, height)
        except Exception:
            log.error("thumbnail")
        finally:
            viewport.visualStyle = style

    # ------------------------------------------------------------ actions

    def handle(self, action, data_in):
        data = self.load()
        part = self.part(data)
        views = {v["id"]: v for v in part["views"]}
        view = views.get(data_in.get("id"))
        viewport = self.app.activeViewport
        changed = True

        if action in ("ready", "refresh"):
            changed = False
            if action == "ready" and self._pending_stitch is not None:
                job, self._pending_stitch = self._pending_stitch, None
                self.ui.palettes.itemById(PALETTE_ID).sendInfoToHTML("stitch", json.dumps(job))
        elif action == "style":
            set_style(viewport, data_in.get("kind"))
            changed = False
        elif action == "addView":
            kind = "shaded" if data_in.get("kind") == "shaded" else "thread"
            existing = next((v for v in part["views"] if v["kind"] == "shaded"), None) if kind == "shaded" else None
            if existing is not None:
                existing["camera"] = camera_to_dict(viewport.camera)
                view = existing
            else:
                view = {"id": uuid.uuid4().hex[:8], "kind": kind, "camera": camera_to_dict(viewport.camera),
                        "annotations": []}
                part["views"].append(view)
            self._save_thumb(view)
            set_style(viewport, kind)               # and stay in that view's look
        elif action == "setCamera" and view:
            view["camera"] = camera_to_dict(viewport.camera)
            self._save_thumb(view)
            set_style(viewport, view["kind"])
            self._notice = "View updated to the current camera."
        elif action == "goTo" and view:
            apply_camera(viewport, view.get("camera"), smooth=True)
            set_style(viewport, view["kind"])
            changed = False
        elif action == "deleteView" and view:
            part["views"].remove(view)
        elif action == "moveView" and view and view["kind"] == "thread":
            threads = [v for v in part["views"] if v["kind"] == "thread"]
            i = threads.index(view)
            j = max(0, min(len(threads) - 1, i + int(data_in.get("delta", 0))))
            threads.insert(j, threads.pop(i))
            part["views"] = threads + [v for v in part["views"] if v["kind"] == "shaded"]
        elif action == "activateConfig":
            if not self.activate_config(data_in.get("id")):
                self._notice = "Fusion couldn't switch to that configuration."
            changed = False
        elif action == "copyViews":
            # Start this configuration from another's views (cameras, padding, callouts).
            comp, _ = holes_mod.context(self.design(), self.target)
            source = data["parts"].get((_safe(lambda: comp.id, "") or comp.name) + "@" + str(data_in.get("from")), {})
            copied = json.loads(json.dumps(source.get("views", [])))
            for v in copied:
                v["id"] = uuid.uuid4().hex[:8]
            if copied:                      # replaces this configuration's views (the panel confirms)
                part["views"] = copied
            part["marks"] = dict(source.get("marks", {}), **part["marks"])
            self._notice = "Copied {} view{}; hole fills and counts follow this configuration's holes.".format(
                len(copied), "" if len(copied) == 1 else "s")
        elif action == "exportAll":
            self.save(data)
            self.export_all()
            return
        elif action == "orderViews":
            # Thread views in a new order (dragged in the panel); the shaded view stays last.
            ids = [i for i in (data_in.get("ids") or []) if i in views]
            threads = [v for v in part["views"] if v["kind"] == "thread"]
            threads.sort(key=lambda v: ids.index(v["id"]) if v["id"] in ids else len(ids))
            part["views"] = threads + [v for v in part["views"] if v["kind"] == "shaded"]
        elif action == "edit" and view:
            self.save(data)
            self.open_editor(view["id"])
            return
        elif action == "mark":
            keys = self._selected_hole_keys()
            if not keys:
                self._notice = "Select hole faces (or their circular edges) in Fusion first."
                changed = False
            else:
                mark = data_in.get("as")
                for key in keys:
                    if mark in (None, "", "auto"):
                        part["marks"].pop(key, None)
                    else:
                        part["marks"][key] = mark
                self._notice = "{} hole{} marked {}.".format(len(keys), "" if len(keys) == 1 else "s",
                                                            mark if mark not in (None, "", "auto") else "automatic")
        elif action == "settings":
            for key in ("guess", "height", "gap"):
                if key in data_in:
                    data["settings"][key] = data_in[key]
            if "colors" in data_in:
                data["settings"]["colors"] = dict(data["settings"].get("colors", {}), **data_in["colors"])
        elif action == "setMcp":
            config = mcp_server.settings()
            if "enabled" in data_in:
                config["enabled"] = bool(data_in["enabled"])
            if data_in.get("newToken"):
                import secrets
                config["token"] = secrets.token_urlsafe(18)
            mcp_server.save(config)
            _mcp_apply()
            log.info("mcp: " + ("on" if config["enabled"] else "off"))
            changed = False
        elif action == "export":
            self.save(data)
            self.export()
            return
        elif action == "stitched":
            self.write_export(data_in)
            return
        else:
            changed = False
        if changed:
            self.save(data)
        self.push_state()

    def _selected_hole_keys(self):
        keys = []
        sels = self.ui.activeSelections
        for i in range(sels.count):
            key = holes_mod.key_of(sels.item(i).entity)
            if key and key not in keys:
                keys.append(key)
        return keys

    # ------------------------------------------------------------ editor

    def editor(self, create=False):
        pal = self.ui.palettes.itemById(EDITOR_ID)
        if pal is None and create:
            url = (pathlib.Path(os.path.abspath(__file__)).parent / "palette" / "annotate.html").as_uri()
            pal = self.ui.palettes.add(EDITOR_ID, EDITOR_NAME, url, True, True, True, 1100, 820)
            pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
            _add(pal.incomingFromHTML, EditorHTMLHandler())
        return pal

    def open_editor(self, view_id):
        with log.timed("render view for editor"):
            rendered = self.render([view_id])
        if not rendered:
            return
        r = rendered[0]
        data = self.load()
        part = self.part(data)
        names = {v["id"]: v for v in self.state()["views"]}
        self._editing = view_id
        self._editor_payload = {
            "kind": "view", "id": view_id,
            "title": names.get(view_id, {}).get("name", "View"),
            "image": r["image"], "crop": r["crop"], "annotations": r["annotations"],
            "padding": next((v.get("padding") for v in part["views"] if v["id"] == view_id), None) or {},
            "defaults": {"color": "#222222", "weight": 12, "dashed": False, "size": 40, "bold": True, "box": True},
        }
        pal = self.editor(create=True)
        pal.isVisible = True
        pal.sendInfoToHTML("load", json.dumps(self._editor_payload))
        self.push_state()

    def handle_editor(self, action, data_in):
        if action == "ready" and self._editor_payload:
            self.editor().sendInfoToHTML("load", json.dumps(self._editor_payload))
        elif action == "save":
            data = self.load()
            part = self.part(data)
            view = next((v for v in part["views"] if v["id"] == data_in.get("id")), None)
            if view is None:
                return
            previous = self._editor_payload["annotations"] if self._editor_payload else []
            view["annotations"] = callouts.merge_saved([a for a in previous if a.get("auto")],
                                                       data_in.get("annotations") or [])
            if self._editor_payload and self._editor_payload["id"] == view["id"]:
                self._editor_payload["annotations"] = data_in.get("annotations") or []
            self.save(data)
            self.push_state()
        elif action == "refresh" and self._editing:
            self.open_editor(self._editing)
        elif action == "padding":
            # Room around the part on each side (percent of its size), then re-render the view.
            data = self.load()
            part = self.part(data)
            view = next((v for v in part["views"] if v["id"] == data_in.get("id")), None)
            if view is None:
                return
            pad = data_in.get("padding") or {}
            view["padding"] = {side: max(0.0, min(3.0, float(pad.get(side, 0) or 0) / 100.0))
                               for side in ("left", "top", "right", "bottom")}
            self.save(data)
            self.open_editor(view["id"])
        elif action == "close":
            pal = self.editor()
            if pal is not None:
                pal.isVisible = False

    # ------------------------------------------------------------ export

    def export(self):
        data = self.load()
        part = self.part(data)
        if not [v for v in part["views"] if v["kind"] == "thread"]:
            self._notice = "Add at least one thread view first."
            self.push_state()
            return
        doc = self.app.activeDocument
        dialog = self.ui.createFileDialog()
        dialog.title = "Export thread callouts"
        dialog.filter = "PNG image (*.png)"
        if data["settings"].get("folder"):
            dialog.initialDirectory = data["settings"]["folder"]
        name = (doc.name if doc else "part") + " threads.png"
        dialog.initialFilename = "".join(c for c in name if c not in '\\/:*?"<>|')
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        self._export_path = dialog.filename
        self._queue = []
        data["settings"]["folder"] = os.path.dirname(dialog.filename)
        self.save(data)
        self._render_and_stitch(data)

    def _render_and_stitch(self, data, label=""):
        """Render the current part's views and send them to the panel to stitch ("stitched" comes back)."""
        progress = self.ui.createProgressDialog()
        progress.show("Hole & Thread Callouts", "Rendering the views{}...".format(" of " + label if label else ""), 0, 1, 0)
        try:
            with log.timed("render all views " + label):
                rendered = self.render()
        except Exception:
            log.error("render views")
            self.ui.messageBox("Hole & Thread Callouts: couldn't render the views.\n\n" + traceback.format_exc())
            self._queue = []
            self._finish_all()
            return False
        finally:
            progress.hide()
        job = {"views": [{"kind": r["view"]["kind"], "image": r["image"], "crop": r["crop"],
                          "annotations": r["annotations"]} for r in rendered],
               "height": int(data["settings"].get("height", 900)), "gap": int(data["settings"].get("gap", 24))}
        palette = self.ui.palettes.itemById(PALETTE_ID)
        palette.sendInfoToHTML("stitch", json.dumps(job))     # the panel draws and stitches, then sends "stitched"
        return True

    def export_all(self):
        """Every configuration with thread views: one image each, "<design> - <configuration> threads.png"
        in a folder you pick. The configuration you were in is active again at the end."""
        data = self.load()
        comp, _ = holes_mod.context(self.design(), self.target)
        base = _safe(lambda: comp.id, "") or comp.name
        todo = [(rid, name) for rid, name in self.configs()
                if [v for v in data["parts"].get(base + "@" + rid, {}).get("views", []) if v.get("kind") == "thread"]]
        if not todo:
            self._notice = "No configuration has thread views yet."
            self.push_state()
            return
        dialog = self.ui.createFolderDialog()
        dialog.title = "Folder for the thread callout images"
        if data["settings"].get("folder"):
            dialog.initialDirectory = data["settings"]["folder"]
        if dialog.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        data["settings"]["folder"] = dialog.folder
        self.save(data)
        doc = self.app.activeDocument
        stem = doc.name if doc else "part"
        self._queue = [(rid, os.path.join(dialog.folder, "".join(
            c for c in "{} - {} threads.png".format(stem, name) if c not in '\\/:*?"<>|')), name) for rid, name in todo]
        self._return_config = _safe(lambda: self.active_config().id)
        self._written = []
        self._next_in_queue()

    def _next_in_queue(self):
        if not self._queue:
            self._finish_all()
            return
        row_id, path, name = self._queue.pop(0)
        if not self.activate_config(row_id):
            log.info("couldn't activate configuration " + name)
            self._next_in_queue()
            return
        self._export_path = path
        self._render_and_stitch(self.load(), name)

    def _finish_all(self):
        if getattr(self, "_return_config", None):
            self.activate_config(self._return_config)
            self._return_config = None
        written = getattr(self, "_written", None)
        if written is not None:
            self._notice = "Saved {} image{} to {}.".format(len(written), "" if len(written) == 1 else "s",
                                                           os.path.dirname(written[0]) if written else "the folder")
            self._written = None
        self.push_state()

    def show_palette(self):
        """The panel, shown (created if needed). Returns (palette, created): a new panel's page is
        still loading, and says "ready" when it can take messages."""
        palette = self.ui.palettes.itemById(PALETTE_ID)
        created = not palette
        if created:
            url = (pathlib.Path(os.path.abspath(__file__)).parent / "palette" / "index.html").as_uri()
            palette = self.ui.palettes.add(PALETTE_ID, PALETTE_NAME, url, True, True, True, 360, 700)
            _add(palette.incomingFromHTML, PaletteHTMLHandler())
            _add(palette.closed, PaletteClosedHandler())
        palette.isVisible = True
        if created:
            _place_palette(self.ui, palette)
        return palette, created

    def stitch_for_agent(self, job, path, deferred):
        """An agent's export (lib/mcp_tools.py): the panel stitches `job`, write_export saves it to
        `path` and answers the agent through `deferred`."""
        self._export_path = path
        self._written = None
        self._queue = []
        self.mcp_export = deferred
        palette, created = self.show_palette()
        if created:
            self._pending_stitch = job          # sent when the panel says "ready"
        else:
            palette.sendInfoToHTML("stitch", json.dumps(job))

    def _agent_export(self, data_in):
        deferred, path = self.mcp_export, self._export_path
        self.mcp_export = None
        self._export_path = None
        try:
            png = base64.b64decode((data_in.get("png") or "").split(",", 1)[-1])
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(png)
            log.info("agent export {} ({:.1f} MB)".format(path, len(png) / 1e6))
            deferred.resolve(png, path)
        except Exception as err:
            log.error("agent export")
            deferred.fail("Couldn't save the image: {}".format(err))

    def write_export(self, data_in):
        if self.mcp_export is not None:
            self._agent_export(data_in)
            return
        path = self._export_path
        if not path:
            return
        if getattr(self, "_written", None) is not None:      # exporting every configuration
            try:
                png = base64.b64decode((data_in.get("png") or "").split(",", 1)[-1])
                with open(path, "wb") as handle:
                    handle.write(png)
                self._written.append(path)
                log.info("exported " + path)
            except Exception:
                log.error("write export")
            self._export_path = None
            self._next_in_queue()
            return
        try:
            png = base64.b64decode((data_in.get("png") or "").split(",", 1)[-1])
            bmp = base64.b64decode((data_in.get("bmp") or "").split(",", 1)[-1]) if data_in.get("bmp") else None
            with open(path, "wb") as handle:
                handle.write(png)
            copied = False
            try:
                copied = clipboard.copy_image(png, bmp)
            except Exception:
                log.error("copy image")
            log.info("exported {} ({:.1f} MB){}".format(path, len(png) / 1e6, ", copied" if copied else ""))
            self._notice = "Saved {}{}.".format(os.path.basename(path), " and copied to the clipboard" if copied else "")
        except Exception:
            log.error("write export")
            self._notice = "Couldn't save the image (see the log)."
        self._export_path = None
        self.push_state()


# ---------------------------------------------------------------- event handlers

def _place_palette(ui, palette):
    """Start the panel under Fusion's browser: snapped below it when Fusion lists the browser
    among its palettes, else docked left (where a pinned browser sits, so it stacks under it).
    Only on the panel's first showing; after that it stays wherever the user moves it."""
    ours = ("browserplus", "buildbook", "holethreadcallouts", "stockscout")
    try:
        ids = [ui.palettes.item(i).id for i in range(ui.palettes.count)]
        log.info("palettes: " + ", ".join(ids))
        browser = next((ui.palettes.itemById(i) for i in ids
                        if "browser" in i.lower() and not i.lower().startswith(ours)), None)
        if browser is not None and browser.isVisible:
            palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
            if palette.snapTo(browser, adsk.core.PaletteSnapOptions.PaletteSnapOptionsBottom):
                log.info("panel snapped under " + browser.id)
                return
    except Exception:
        log.error("place panel under the browser")
    palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateLeft
    log.info("panel docked left")


def _add(event, handler):
    event.add(handler)
    _handlers.append(handler)
    _events.append((event, handler))      # detached again by reload_addin


def _detach_events():
    """Take this run's handlers off Fusion's events (a reload starts fresh ones)."""
    for event, handler in _events:
        try:
            event.remove(handler)
        except Exception:
            pass
    del _events[:]
    del _handlers[:]


class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            event = adsk.core.HTMLEventArgs.cast(args)
            if event.action == "response":
                return
            if event.action == "reloadAddin":
                # Not from here: this runs inside the panel's own event, and reloading deletes the panel.
                adsk.core.Application.get().fireCustomEvent(RELOAD_EVENT, "")
                return
            _ctrl.handle(event.action, json.loads(event.data) if event.data else {})
            event.returnData = "OK"
        except Exception:
            log.error("palette action " + str(getattr(args, "action", "?")))
            _ctrl.ui.messageBox("Hole & Thread Callouts failed:\n{}".format(traceback.format_exc()))


class EditorHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            event = adsk.core.HTMLEventArgs.cast(args)
            if event.action == "response":
                return
            _ctrl.handle_editor(event.action, json.loads(event.data) if event.data else {})
            event.returnData = "OK"
        except Exception:
            log.error("editor action " + str(getattr(args, "action", "?")))


class ShowPaletteHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _ctrl.show_palette()
            _ctrl.push_state()
        except Exception:
            log.error("show palette")


class PaletteClosedHandler(adsk.core.UserInterfaceGeneralEventHandler):
    """Leaving the tool: back to Fusion's normal look (shaded with visible edges)."""
    def notify(self, args):
        try:
            set_style(_ctrl.app.activeViewport, "shaded")
            pal = _ctrl.editor()
            if pal is not None:
                pal.isVisible = False
        except Exception:
            log.error("panel closed")


class DocSwitchHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.push_state()
        except Exception:
            log.error("document switch")


class CommandTerminatedHandler(adsk.core.ApplicationCommandEventHandler):
    """Activating a component (or changing the part) updates the panel."""
    QUIET = ("SelectCommand", "CommitCommand", "PanCommand", "OrbitCommand", "ZoomCommand",
             "FreeOrbitCommand", "ConstrainedOrbitCommand", "LookAtCommand", "ViewCubeCommand")

    def notify(self, args):
        try:
            cid = adsk.core.ApplicationCommandEventArgs.cast(args).commandId
            if cid not in self.QUIET and not cid.startswith("holeThreadCallouts"):
                _ctrl.push_state()
        except Exception:
            log.error("command terminated")


def _mcp_apply():
    """Start / stop agent access to match its setting (off unless switched on in the panel)."""
    global _mcp
    app = adsk.core.Application.get()
    config = mcp_server.settings()
    if not config["enabled"]:
        if _mcp is not None:
            _mcp.stop()
        return
    if _mcp is None:
        try:
            app.unregisterCustomEvent(mcp_server.EVENT)
        except Exception:
            pass
        _add(app.registerCustomEvent(mcp_server.EVENT), McpCallHandler())
        _mcp = mcp_server.Server(lambda: adsk.core.Application.get().fireCustomEvent(mcp_server.EVENT, ""))
    _mcp.start(config)


def _mcp_status():
    config = mcp_server.settings()
    return {"enabled": config["enabled"], "port": config["port"], "url": mcp_server.url(config),
            "running": bool(_mcp is not None and _mcp.running), "error": _mcp.error if _mcp is not None else ""}


class McpCallHandler(adsk.core.CustomEventHandler):
    """An agent's tool calls, on Fusion's main thread."""

    def notify(self, args):
        try:
            import sys
            from .lib import mcp_tools
            mcp_tools.MAIN[0] = sys.modules[__name__]

            def runner(name, arguments):
                if name == "__list__":
                    return mcp_tools.definitions()
                return mcp_tools.run(_ctrl, name, arguments)
            if _mcp is not None:
                _mcp.run_pending(runner)
        except Exception:
            log.error("mcp call")


def reload_addin():
    """Stop, reload every module of the add-in from disk, start again and reopen the panel: picks
    up new code (an update, an edit) without Fusion's Scripts and Add-Ins dialog."""
    import importlib
    import importlib.machinery
    import sys
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        log.info("reloading Hole & Thread Callouts")
        stop(None)
        _detach_events()
        package = log.__name__.rpartition(".lib.")[0]      # however Fusion named the add-in's package
        # Submodules first (reloaded in place, so "from . import x" references stay good), then
        # this module, whose "from .lib import x" lines pick up the new code.
        # lib/ first: commands read its constants when they load (alphabetical put commands first,
        # so a new constant in lib/model.py wasn't there yet for commands/explode_cmd.py).
        mine = [n for n in list(sys.modules) if n.startswith(package + ".") and n != __name__]
        for name in sorted(mine, key=lambda n: (".lib." not in n + ".", n)):
            module = sys.modules.get(name)
            if module is not None:
                importlib.reload(module)
        # Fusion loads this file under a made-up module name importlib.reload can't find, so run the
        # file's new code into the same module object instead (its globals become the new ones).
        main = sys.modules[__name__]
        importlib.machinery.SourceFileLoader(__name__, __file__).exec_module(main)
        main.run(None)
        cmd = ui.commandDefinitions.itemById(COMMAND_ID)
        if cmd is not None:
            cmd.execute()                       # reopen the panel
        main.log.info("Hole & Thread Callouts reloaded")
    except Exception:
        log.error("reload")
        ui.messageBox("Hole & Thread Callouts couldn't reload:\n{}\n\nUse Utilities > Scripts and Add-Ins to stop and run it.".format(
            traceback.format_exc()))


class ReloadHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        reload_addin()


# Fusion can load the same add-in twice (e.g. from the AddIns folder and from a path added in
# Scripts and Add-Ins). Two copies both answer the same buttons, so only the first one runs.
_SINGLE_KEY = "_rms_holethreadcallouts_module"


def _claim():
    """True if this copy may run (no other copy of the add-in is running)."""
    import sys
    other = getattr(sys, _SINGLE_KEY, None)
    if other and other != __name__ and other in sys.modules:
        log.info("another copy of this add-in is already running ({}); this one ({}) stays off".format(
            other, os.path.dirname(os.path.abspath(__file__))))
        return False
    setattr(sys, _SINGLE_KEY, __name__)
    return True


def _mine():
    import sys
    return getattr(sys, _SINGLE_KEY, None) == __name__


def _release():
    import sys
    if _mine():
        delattr(sys, _SINGLE_KEY)


def run(context):
    global _ctrl
    if not _claim():
        return
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _ctrl = Controller(app)
        log.info("Hole & Thread Callouts starting")
        cmd_def = ui.commandDefinitions.itemById(COMMAND_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                COMMAND_ID, "Hole & Thread Callouts", "One image showing which holes to tap (and dowels)")
        _add(cmd_def.commandCreated, ShowPaletteHandler())
        panel = ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")
        if panel and not panel.controls.itemById(COMMAND_ID):
            panel.controls.addCommand(cmd_def)
        _add(app.documentActivated, DocSwitchHandler())
        _add(ui.commandTerminated, CommandTerminatedHandler())
        try:
            app.unregisterCustomEvent(RELOAD_EVENT)
        except Exception:
            pass
        _add(app.registerCustomEvent(RELOAD_EVENT), ReloadHandler())
        _mcp_apply()
    except Exception:
        log.error("run")
        ui.messageBox("Hole & Thread Callouts failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    global _mcp
    if not _mine():
        return
    if _mcp is not None:
        _mcp.stop()
        _mcp = None
        try:
            adsk.core.Application.get().unregisterCustomEvent(mcp_server.EVENT)
        except Exception:
            pass
    _release()                  # (a reload claims it again in run)
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        try:
            set_style(app.activeViewport, "shaded")
        except Exception:
            pass
        for pid in (PALETTE_ID, EDITOR_ID):
            palette = ui.palettes.itemById(pid)
            if palette:
                palette.deleteMe()
        panel = ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")
        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if control:
                control.deleteMe()
        cmd_def = ui.commandDefinitions.itemById(COMMAND_ID)
        if cmd_def:
            cmd_def.deleteMe()
        log.info("Hole & Thread Callouts stopped")
    except Exception:
        log.error("stop")
