"""BuildBook -- build-manual exploded views for Fusion.

A docked panel organises the manual into Sections -> Steps -> Parts. Each
step's parts can be exploded along any direction with trail lines. The real
model never moves: exploded views are drawn as custom graphics (lib/scene.py)
and the manual lives in a design attribute, so it travels with the .f3d.
"""

import json
import os
import pathlib
import time
import traceback

import adsk.core
import adsk.fusion

from .commands import anchor_cmd, explode_cmd, lines_cmd, pick_cmd
from .lib import capture, crop, explode, hardware, log, model, refs
from .lib.overlay import CropOverlay
from .lib import scene as scene_mod
from .lib.scene import Scene

PALETTE_ID = "buildBookPalette"
PALETTE_NAME = "BuildBook"
COMMAND_ID = "buildBookShow"
ATTR_GROUP = "BuildBook"
ATTR_NAME = "manual"
FILL_EVENT = "buildBookFillLabels"     # custom event: read hardware details a batch at a time

_handlers = []
_ctrl = None


class Controller:
    def __init__(self, app):
        self.app = app
        self.ui = app.userInterface
        self.scene = Scene()
        self.explode = explode_cmd.ExplodeCommand(self)
        self.picker = pick_cmd.PickCommand(self)
        self.lines = lines_cmd.LinesCommand(self)
        self.anchor = anchor_cmd.AnchorCommand(self)
        self.crop_overlay = CropOverlay()
        self.checked = []           # parts checked in the panel's list
        self.frame_ratio = None     # crop ratio key while the on-screen frame is showing, else None
        self._resume_step = None
        self._notice = None

    # ------------------------------------------------------------ design + storage

    def design(self):
        return adsk.fusion.Design.cast(self.app.activeProduct)

    def load(self):
        design = self.design()
        if design is None:
            return model.new_manual()
        attr = design.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        try:
            return model.from_json(attr.value if attr else "")
        except Exception:
            log.error("manual attribute unreadable; starting a new one")
            return model.new_manual()

    def save(self, manual):
        design = self.design()
        if design is not None:
            design.attributes.add(ATTR_GROUP, ATTR_NAME, model.to_json(manual))

    # ------------------------------------------------------------ viewing

    def show_step(self, step_id, move_camera=False, smooth=True, upto=None):
        """Render a step; with `move_camera`, also go to the step's saved view.

        `upto` shows the step as it stands just after that explode move.
        """
        design = self.design()
        if design is None or step_id is None:
            self.scene.clear()
        else:
            manual = self.load()
            if step_id != self.scene.step_id:
                self.checked = []           # a different step: its own checkboxes
            self.scene.show(design, manual, step_id, upto=upto)
            self.scene.set_highlight(self.checked)
            if move_camera:
                _, step = model.find_step(manual, step_id)
                if step is not None:
                    capture.apply_camera(self.app.activeViewport, step.get("camera"), smooth)
        self.update_overlay()
        self.app.activeViewport.refresh()
        self.push_state()

    def update_overlay(self):
        """Frame the area the crop ratio exports while a step is shown.

        Works out (and remembers) whether a frame is wanted, so the camera
        handler, which fires continuously while orbiting, can do nothing at
        all when it isn't.
        """
        try:
            design = self.design()
            manual = self.load() if design is not None else None
            _, step = model.find_step(manual, self.scene.step_id) if manual else (None, None)
            self.frame_ratio = None
            if step is None or self.scene.edit or not manual["settings"].get("showCropFrame"):
                self.crop_overlay.clear()
                return
            ratio = manual["settings"].get("image", {}).get("ratio", crop.VIEWPORT)
            if ratio in (crop.VIEWPORT, crop.FREE):
                self.crop_overlay.clear()
                return
            self.frame_ratio = ratio
            self.move_frame()
        except Exception:
            log.error("update crop overlay")

    def move_frame(self):
        """Redraw the remembered crop frame for the current camera (cheap: no manual load)."""
        if self.frame_ratio is None:
            return
        viewport = self.app.activeViewport
        vw, vh = viewport.width, viewport.height
        rect = crop.centered_rect(crop.ratio_value(self.frame_ratio, vw, vh), vw, vh)
        self.crop_overlay.draw(self.design(), viewport, rect)

    def notify(self, message):
        """Show a short message in the panel on the next state push."""
        self._notice = message

    def close_view(self):
        self.frame_ratio = None
        self.crop_overlay.clear()
        self.scene.clear()
        self.app.activeViewport.refresh()
        self.push_state()

    # ------------------------------------------------------------ palette state

    def state(self):
        design = self.design()
        if design is None:
            return {"error": "Open a Fusion design to use BuildBook."}
        manual = self.load()
        with log.timed("state: resolve parts"):
            index, missing, changed = refs.resolve_manual(design, manual)
        if changed:
            self.save(manual)
        units = design.unitsManager
        length_units = units.defaultLengthUnits

        def shown(cm):
            return round(units.convert(cm, "cm", length_units), 4)

        missing_paths = {m["path"] for m in missing}
        short = manual["settings"].get("shortHardwareNames", True)
        detail = None
        _, step = model.find_step(manual, self.scene.step_id)
        started = time.perf_counter()
        if step is not None:
            numbers = {}
            explodes = []
            for n, ex in enumerate(step["explodes"], 1):
                parts = []
                for part in ex["parts"]:
                    path = part["ref"].get("path", "")
                    numbers.setdefault(path, []).append(n)
                    own = part.get("distance") is not None
                    parts.append({
                        "path": path,
                        "name": refs.display_name(index.get(path), part["ref"].get("name", path), short),
                        "distance": shown(part["distance"]) if own else None,
                        "own": own,
                        "trail": part.get("trail", True),
                        "missing": path in missing_paths,
                    })
                trails = [p["trail"] for p in parts]
                kind = ex["direction"].get("kind", model.DIR_AXIS)
                explodes.append({
                    "id": ex["id"],
                    "number": n,
                    "name": ex.get("name", ""),
                    "label": model.explode_label(step, ex),
                    "direction": model.direction_label(ex["direction"]),
                    "axis": model.direction_label(ex["direction"]) if kind == model.DIR_AXIS else kind,
                    "distance": shown(model.explode_distance(manual, step, ex)),
                    "own": kind == model.DIR_XYZ or ex.get("distance") is not None,
                    "spacing": ex.get("spacing", model.UNIFORM),
                    "trail": "all" if trails and all(trails) else "some" if any(trails) else "none",
                    "parts": parts,
                })
            items = []
            for item in step["items"]:
                path = item["ref"].get("path", "")
                occ = index.get(path)
                items.append({
                    "path": path,
                    "name": refs.display_name(occ, item["ref"].get("name", path), short),
                    "component": occ.component.name if occ else "",
                    "moves": numbers.get(path, []),
                    "anchor": bool(item.get("anchor")),
                    "missing": path in missing_paths,
                })
            # Listed by name (numbers by value: M3x8 before M3x12); the manual keeps its own order.
            items.sort(key=lambda it: (hardware.natural_key(it["name"]), hardware.natural_key(it["path"])))
            detail = {
                "id": step["id"],
                "items": items,
                "explodes": explodes,
                "upto": self.scene.upto,
                "defaultDistance": shown(model.step_distance(manual, step)),
                "defaultOwn": step.get("distance") is not None,
                "hasCamera": bool(step.get("camera")),
                "image": self._image_info(manual),
                "imageName": capture.image_name(manual, step),
            }

        if step is not None:
            log.info("TIME  state: step details {:.0f} ms".format((time.perf_counter() - started) * 1000))
        return {
            "error": "",
            "document": self.app.activeDocument.name if self.app.activeDocument else "",
            "manual": manual,
            "currentStepId": self.scene.step_id,
            "edit": self.scene.edit,
            "step": detail,
            "unassigned": self._timed("state: unassigned list", refs.unassigned, manual, index),
            "missing": missing,
            "units": length_units,
            "manualDefault": shown(manual["settings"]["defaultDistance"]),
            "exportFolder": capture.export_folder(manual),
            "thumbs": capture.thumbnail_urls(self.app, manual),
            "ratios": [[key, label] for key, label, _ in crop.RATIOS],
            "notice": self._notice,
        }

    def _timed(self, label, fn, *args):
        with log.timed(label):
            return fn(*args)

    def push_state(self):
        palette = self.ui.palettes.itemById(PALETTE_ID)
        if palette is None or not palette.isVisible:
            return                  # hidden: nothing to update (showing the panel pushes)
        try:
            with log.timed("panel state"):
                data = self.state()
            refs.flush_label_cache()
            palette.sendInfoToHTML("state", json.dumps(data))
            self._notice = None
        except Exception:
            log.error("push state")
        if refs.has_pending() and not getattr(self, "_filling", False):
            self._filling = True
            self.app.fireCustomEvent(FILL_EVENT, "")

    def fill_labels(self):
        """A batch of slow hardware-detail reads; the panel updates once they're all in."""
        self._filling = False
        found, left = refs.fill_pending(0.15)
        refs.flush_label_cache()
        if left:
            self._filling = True
            self.app.fireCustomEvent(FILL_EVENT, "")
        else:
            self.push_state()

    # ------------------------------------------------------------ palette actions

    def handle(self, action, data):
        manual = self.load()
        step_id = data.get("stepId") or self.scene.step_id
        _, step = model.find_step(manual, step_id)
        rerender = False
        dirty = True

        if action in ("ready", "refresh"):
            if action == "refresh":
                self.scene.flush_cache()
                refs.clear_caches()
                rerender = self.scene.active
            dirty = False

        elif action == "renameManual":
            manual["title"] = data.get("title") or manual["title"]
        elif action == "addSection":
            model.add_section(manual, data.get("title"))
        elif action == "renameSection":
            sec = model.find_section(manual, data["id"])
            if sec:
                sec["title"] = data.get("title") or sec["title"]
        elif action == "deleteSection":
            sec = model.find_section(manual, data["id"])
            if sec and any(s["id"] == self.scene.step_id for s in sec["steps"]):
                self.scene.clear()
            model.delete_section(manual, data["id"])
        elif action == "moveSection":
            model.move_section(manual, data["id"], int(data.get("delta", 0)))
            rerender = self.scene.active
        elif action == "addStep":
            new = model.add_step(manual, data["sectionId"], data.get("title"))
            if new:
                self.save(manual)
                self.show_step(new["id"])
                return
        elif action == "renameStep":
            _, target = model.find_step(manual, data["id"])
            if target:
                target["title"] = data.get("title") or target["title"]
        elif action == "deleteStep":
            if data["id"] == self.scene.step_id:
                self.scene.clear()
            model.delete_step(manual, data["id"])
        elif action == "moveStep":
            model.move_step(manual, data["id"], data.get("sectionId"), data.get("index"), data.get("delta"))
            rerender = self.scene.active

        elif action == "openStep":
            self.show_step(data["id"], move_camera=True)
            return
        elif action == "saveCamera" and step:
            step["camera"] = capture.camera_to_dict(self.app.activeViewport.camera)
            capture.save_thumbnail(self, step["id"])
            self.notify("View saved for \u201c{}\u201d.".format(step["title"]))
        elif action == "clearCamera" and step:
            step["camera"] = None
        elif action == "goCamera" and step:
            capture.apply_camera(self.app.activeViewport, step.get("camera"))
            dirty = False
        elif action == "setRatio":
            manual["settings"].setdefault("image", {})["ratio"] = data.get("ratio", crop.VIEWPORT)
            rerender = self.scene.active
        elif action in ("exportStep", "exportAll"):
            steps = [step] if action == "exportStep" else [st for _, st in model.ordered_steps(manual)]
            if not steps or steps == [None]:
                return
            if data.get("ask") or manual["settings"].get("askFolder"):
                if not self._choose_folder(manual, "Save {} to folder".format(
                        "this step's image" if len(steps) == 1 else "all {} step images".format(len(steps)))):
                    return
                self.save(manual)
            self._export(steps)
            return
        elif action == "chooseExportFolder":
            if not self._choose_folder(manual, "Folder for exported step images"):
                return
        elif action == "exportManual":
            self._export_manual(manual)
            return
        elif action == "importManual":
            self._import_manual()
            return
        elif action == "removeLeftovers":
            self.remove_leftovers()
            return
        elif action == "openExportFolder":
            folder = capture.export_folder(manual)
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
            return
        elif action == "closeView":
            self.close_view()
            return
        elif action == "restoreVisibility":
            self.close_view()
            return

        elif action == "setPrep" and step:
            step["prep"] = bool(data.get("prep"))
            rerender = True
        elif action == "setContext" and step:
            for key in ("earlier", "later"):
                if key in data:
                    step[key] = data[key]
            rerender = True
        elif action == "setNotes" and step:
            step["notes"] = data.get("notes", "")
        elif action == "addSelected" and step:
            occs = refs.selected_occurrences(self.ui)
            if not occs:
                self.ui.messageBox("Select parts in the canvas or browser first.")
                return
            model.add_items(step, [refs.make_ref(o) for o in occs])
            rerender = True
        elif action == "addPaths" and step:
            index = refs.path_index(self.design())
            model.add_items(step, [refs.make_ref(index[p]) for p in data.get("paths", []) if p in index])
            rerender = True
        elif action == "removeItems" and step:
            model.remove_items(step, data.get("paths", []))
            rerender = True
        elif action == "resetOffsets" and step:
            # Take the parts out of every explode move (they stay in the step).
            paths = set(data.get("paths") or model.item_paths(step))
            for ex in step["explodes"]:
                ex["parts"] = [p for p in ex["parts"] if p["ref"].get("path") not in paths]
            rerender = True
        elif action == "setStepDistance" and step:
            value = self._parse_length(data.get("value", ""))
            if value is False:
                return
            step["distance"] = value
            rerender = True
        elif action == "setDefaultDistance":
            value = self._parse_length(data.get("value", ""))
            if value is False or value is None:
                self.push_state()
                return
            manual["settings"]["defaultDistance"] = value
            rerender = self.scene.active
        elif action == "setTrail" and step:
            # Trail lines on/off for these parts in every explode move of the step.
            paths = set(data.get("paths", []))
            for ex in step["explodes"]:
                for part in ex["parts"]:
                    if part["ref"].get("path") in paths:
                        part["trail"] = bool(data.get("trail"))
            rerender = True
        elif action == "checkItems":
            # Checked parts: select what Fusion can select, highlight the exploded copies.
            self.checked = list(data.get("paths", []))
            self._select(self.checked)
            self.scene.set_highlight(self.checked)
            self.app.activeViewport.refresh()
            return
        elif action == "selectItems":
            self._select(data.get("paths", []))
            dirty = False
        elif action == "setAnchor" and step:
            if model.find_item(step, data.get("path")):
                self.anchor.launch(step["id"], data["path"])
            return
        elif action == "editLines" and step:
            self.lines.launch(step["id"])
            return
        elif action == "pick" and step:
            self.picker.launch(step["id"])
            return
        elif action in ("addExplode", "explode") and step:
            index = refs.path_index(self.design())
            occs = [index[p] for p in data.get("paths", []) if p in index]
            after = [i for i, ex in enumerate(step["explodes"]) if ex["id"] == self.scene.upto]
            self.explode.launch(step["id"], None, occs, insert_at=after[0] + 1 if after else None)
            return
        elif action == "editExplode" and step:
            if model.find_explode(step, data.get("id")):
                self.explode.launch(step["id"], data["id"])
            return
        elif action == "scrubExplode" and step:
            upto = data.get("id")
            self.show_step(step["id"], upto=None if upto == self.scene.upto else upto)
            return
        elif action.endswith("Explode") or action in ("setPartDistance", "setPartTrail", "removeExplodePart"):
            if not step or not self._explode_action(manual, step, action, data):
                return
            rerender = True
        elif action == "setSettings":
            _deep_update(manual["settings"], data.get("settings", {}))
            rerender = self.scene.active
        else:
            dirty = False

        if dirty:
            self.save(manual)
        if rerender and self.scene.active:
            self.show_step(self.scene.step_id, upto=self.scene.upto)
        else:
            self.push_state()

    def _choose_folder(self, manual, title):
        """Ask for the export folder; it becomes the manual's default. False if cancelled."""
        dialog = self.ui.createFolderDialog()
        dialog.title = title
        dialog.initialDirectory = capture.export_folder(manual)
        if dialog.showDialog() != adsk.core.DialogResults.DialogOK:
            return False
        manual["settings"]["exportFolder"] = dialog.folder
        return True

    def _image_info(self, manual):
        """Short description of the exported image: ratio and size."""
        vp = self.app.activeViewport
        vw, vh = vp.width, vp.height
        image = manual["settings"].get("image", {})
        width = int(image.get("width", 1600))
        frac = capture.effective_crop(manual, vw, vh)
        if crop.is_full(frac):
            height = round(width * vh / float(vw))
            label = "Match viewport"
        else:
            height = round(width * frac["h"] * vh / (frac["w"] * vw))
            label = next((lab for key, lab, _ in crop.RATIOS if key == image.get("ratio")), "")
        return {"text": "{} · {} × {} px".format(label, width, height)}

    def _export(self, steps):
        """Write one PNG per step (at each step's saved view), then return to where we were."""
        manual = self.load()
        folder = capture.export_folder(manual)
        return_to = self.scene.step_id
        camera = capture.camera_to_dict(self.app.activeViewport.camera)
        written, no_view, done = [], 0, []
        try:
            for step in steps:
                if not step.get("camera"):
                    no_view += 1
                written.append(capture.export_step(self, manual, step, folder))
                done.append(step["id"])
        except Exception:
            log.error("export")
            self.ui.messageBox("BuildBook: export failed after {} image(s).\n\n{}".format(
                len(written), traceback.format_exc()))
        finally:
            if len(steps) > 1:
                if return_to:
                    self.show_step(return_to)
                else:
                    self.close_view()
                capture.apply_camera(self.app.activeViewport, camera, smooth=False)
        if done:
            # Remember what's been exported, for the step list's progress.
            fresh = self.load()
            stamp = time.strftime("%Y-%m-%d %H:%M")
            for _, st in model.ordered_steps(fresh):
                if st["id"] in done:
                    st["exportedAt"] = stamp
            self.save(fresh)
        if written:
            msg = "Saved {} to {}".format(
                os.path.basename(written[0]) if len(written) == 1 else "{} images".format(len(written)), folder)
            if no_view:
                msg += " ({} step(s) had no saved view and used the current one)".format(no_view)
            self.notify(msg)
        self.push_state()

    def _export_manual(self, manual):
        dialog = self.ui.createFileDialog()
        dialog.title = "Export BuildBook manual"
        dialog.filter = "BuildBook manual (*.json)"
        dialog.initialFilename = capture.safe_filename(manual.get("title") or "manual") + ".buildbook.json"
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        with open(dialog.filename, "w", encoding="utf-8") as handle:
            json.dump(manual, handle, indent=1)
        self.notify("Manual exported to " + dialog.filename)
        self.push_state()

    def _import_manual(self):
        dialog = self.ui.createFileDialog()
        dialog.title = "Import BuildBook manual"
        dialog.filter = "BuildBook manual (*.json)"
        if dialog.showOpen() != adsk.core.DialogResults.DialogOK:
            return
        try:
            with open(dialog.filename, encoding="utf-8") as handle:
                imported = model.from_json(handle.read())
        except Exception:
            log.error("import manual")
            self.ui.messageBox("That file isn't a BuildBook manual.")
            return
        steps = len(model.ordered_steps(imported))
        answer = self.ui.messageBox(
            "Replace this design's BuildBook manual with \"{}\" ({} sections, {} steps)?".format(
                imported.get("title", ""), len(imported["sections"]), steps),
            "Import manual", adsk.core.MessageBoxButtonTypes.YesNoButtonType)
        if answer != adsk.core.DialogResults.DialogYes:
            return
        self.scene.clear()
        self.save(imported)
        self.notify("Imported {} steps. Parts that don't exist in this design are listed as missing.".format(steps))
        self.push_state()

    def _parse_length(self, text):
        """Distance typed in the panel -> cm. None for blank, False if unreadable.

        Plain numbers are in the design's length units; "2 in", "30mm" etc.
        work too.
        """
        text = str(text).strip()
        if not text:
            return None
        units = self.design().unitsManager
        try:
            return units.evaluateExpression(text, units.defaultLengthUnits)
        except Exception:
            self.ui.messageBox("Couldn't read \"{}\" as a distance.".format(text))
            self.push_state()
            return False

    def _explode_action(self, manual, step, action, data):
        """Quick edits to one explode move from the panel. False if nothing changed."""
        ex = model.find_explode(step, data.get("id"))
        if ex is None:
            return False
        if action == "deleteExplode":
            step["explodes"].remove(ex)
            if self.scene.upto == ex["id"]:
                self.scene.upto = None
        elif action == "renameExplode":
            ex["name"] = data.get("name", "").strip()
        elif action == "moveExplode":
            explodes = step["explodes"]
            explodes.remove(ex)
            if "index" in data:
                index = int(data["index"])
            else:
                index = explodes.index(ex) if ex in explodes else len(explodes)
            explodes.insert(max(0, min(index, len(explodes))), ex)
        elif action == "setAxisExplode":
            axis = data.get("axis")
            if axis not in model.AXIS_VECTORS:
                return False
            ex["direction"] = model.new_direction(axis)
        elif action == "setDistanceExplode":
            value = self._parse_length(data.get("value", ""))
            if value is False:
                return False
            if ex["direction"].get("kind") == model.DIR_XYZ:
                vec = ex["direction"].get("vector") or [0.0, 0.0, 0.0]
                length = explode.length(vec)
                if value and length > 1e-9:
                    ex["direction"]["vector"] = [c * value / length for c in vec]
            else:
                ex["distance"] = value          # None = follow the step default
        elif action == "setSpacingExplode":
            if data.get("spacing") not in model.SPACINGS:
                return False
            ex["spacing"] = data["spacing"]
        elif action == "setTrailExplode":
            for part in ex["parts"]:
                part["trail"] = bool(data.get("trail"))
        elif action in ("setPartDistance", "setPartTrail", "removeExplodePart"):
            part = model.find_explode_part(ex, data.get("path"))
            if part is None:
                return False
            if action == "setPartDistance":
                value = self._parse_length(data.get("value", ""))
                if value is False:
                    return False
                part["distance"] = value        # None = the move's distance
            elif action == "setPartTrail":
                part["trail"] = bool(data.get("trail"))
            else:
                ex["parts"].remove(part)
        else:
            return False
        return True

    def _select(self, paths):
        index = refs.path_index(self.design())
        sels = self.ui.activeSelections
        sels.clear()
        for p in paths:
            occ = index.get(p)
            if occ is None:
                continue
            try:
                sels.add(occ)
            except Exception:
                pass  # hidden parts (e.g. exploded copies) can't be selected

    # ------------------------------------------------------------ document events

    def before_save(self):
        """Never let a save capture BuildBook graphics or hidden parts."""
        if self.scene.active:
            self._resume_step = self.scene.step_id
        self.crop_overlay.clear()
        self.scene.clear()          # restores visibility and sweeps every tagged group
        design = self.design()
        if design is not None:
            scene_mod.sweep(design)

    def after_save(self):
        if self._resume_step:
            step_id, self._resume_step = self._resume_step, None
            self.show_step(step_id)

    def on_document_switch(self):
        refs.forget_document()          # (hardware labels are per component: kept)
        self.crop_overlay.clear()
        self.scene.clear()
        self.scene.flush_cache()
        self.push_state()

    def remove_leftovers(self):
        """Clear the step view and delete every custom-graphics group in the design."""
        design = self.design()
        self.crop_overlay.clear()
        self.scene.clear()
        removed = 0
        if design is not None:
            log.info("remove leftovers: before")
            scene_mod.visibility_report(design)
            removed = scene_mod.sweep(design, everything=True)
            log.info("remove leftovers: removed {} group(s); after".format(removed))
            scene_mod.visibility_report(design)
        self.app.activeViewport.refresh()
        self.notify("Removed {} leftover graphics group(s).".format(removed) if removed
                    else "No leftover graphics found.")
        self.push_state()


def _deep_update(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _palette_url():
    """file:/// URL for the panel page.

    Fusion mangles a bare Windows path when the add-in is loaded through a
    junction ("file:///C:/%5CUsers..."), so hand it a proper URL instead.
    """
    here = pathlib.Path(os.path.abspath(__file__)).parent
    return (here / "palette" / "index.html").as_uri()


# ---------------------------------------------------------------- event handlers

class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            event = adsk.core.HTMLEventArgs.cast(args)
            if event.action == "response":
                return
            data = json.loads(event.data) if event.data else {}
            _ctrl.handle(event.action, data)
            event.returnData = "OK"
        except Exception:
            log.error("palette action " + str(getattr(args, "action", "?")))
            _ctrl.ui.messageBox("BuildBook failed:\n{}".format(traceback.format_exc()))


class ShowPaletteHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            ui = _ctrl.ui
            palette = ui.palettes.itemById(PALETTE_ID)
            if not palette:
                palette = ui.palettes.add(PALETTE_ID, PALETTE_NAME, _palette_url(), True, True, True, 420, 700)
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
                handler = PaletteHTMLHandler()
                palette.incomingFromHTML.add(handler)
                _handlers.append(handler)
                _add(palette.closed, PaletteClosedHandler())
            palette.isVisible = True
            _ctrl.push_state()          # (nothing is pushed while it's hidden)
        except Exception:
            log.error("show palette")
            _ctrl.ui.messageBox("BuildBook failed:\n{}".format(traceback.format_exc()))


class CameraChangedHandler(adsk.core.CameraEventHandler):
    """Keep the crop frame on screen as the view changes (it's drawn in the scene)."""

    def notify(self, args):
        # Fires continuously while orbiting: do nothing unless the frame is showing.
        if _ctrl is None or _ctrl.frame_ratio is None or not _ctrl.scene.active:
            return
        try:
            _ctrl.move_frame()
        except Exception:
            log.error("camera changed")


class PaletteClosedHandler(adsk.core.UserInterfaceGeneralEventHandler):
    """Closing the panel leaves the step view: the model goes back to normal."""

    def notify(self, args):
        try:
            _ctrl.crop_overlay.clear()
            _ctrl.scene.clear()
            _ctrl.app.activeViewport.refresh()
        except Exception:
            log.error("palette closed")


class DocSavingHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.before_save()
        except Exception:
            log.error("documentSaving")


class DocSavedHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.after_save()
        except Exception:
            log.error("documentSaved")


class FillLabelsHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            _ctrl.fill_labels()
        except Exception:
            log.error("fill labels")


class DocSwitchHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.on_document_switch()
        except Exception:
            log.error("document switch")


class DocDeactivatingHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.scene.clear()
        except Exception:
            log.error("documentDeactivating")


def _add(event, handler):
    event.add(handler)
    _handlers.append(handler)


def run(context):
    global _ctrl
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _ctrl = Controller(app)
        log.info("BuildBook starting")

        cmd_def = ui.commandDefinitions.itemById(COMMAND_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                COMMAND_ID, "BuildBook", "Build-manual sections, steps and exploded views")
        _add(cmd_def.commandCreated, ShowPaletteHandler())

        _ctrl.explode.register(ui)
        _ctrl.picker.register(ui)
        _ctrl.lines.register(ui)
        _ctrl.anchor.register(ui)
        _add(app.cameraChanged, CameraChangedHandler())

        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is not None:
            scene_mod.sweep(design)

        panel = ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")
        if panel and not panel.controls.itemById(COMMAND_ID):
            panel.controls.addCommand(cmd_def)

        _add(app.documentSaving, DocSavingHandler())
        _add(app.documentSaved, DocSavedHandler())
        _add(app.documentActivated, DocSwitchHandler())
        try:
            app.unregisterCustomEvent(FILL_EVENT)
        except Exception:
            pass
        _add(app.registerCustomEvent(FILL_EVENT), FillLabelsHandler())
        _add(app.documentOpened, DocSwitchHandler())
        _add(app.documentDeactivating, DocDeactivatingHandler())
    except Exception:
        log.error("run")
        ui.messageBox("BuildBook failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        try:
            app.unregisterCustomEvent(FILL_EVENT)
        except Exception:
            pass
        if _ctrl is not None:
            _ctrl.scene.clear()
            _ctrl.explode.unregister(ui)
            _ctrl.picker.unregister(ui)
            _ctrl.lines.unregister(ui)
            _ctrl.anchor.unregister(ui)
            _ctrl.crop_overlay.clear()

        palette = ui.palettes.itemById(PALETTE_ID)
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
        log.info("BuildBook stopped")
    except Exception:
        log.error("stop")
        ui.messageBox("BuildBook failed to stop:\n{}".format(traceback.format_exc()))
