"""Browser+ -- find, check and tidy joints and relationships in Fusion.

A docked panel answers "what holds this part?" (follows the canvas selection),
lists every joint, as-built joint, relationship, rigid group and motion link
with search and filters, flags problems (errors, floating parts, duplicate
joints), and draws a clickable connection map. Rows can be highlighted,
zoomed to, opened in Fusion's own edit dialog, suppressed, renamed, deleted
or rolled to in the timeline.

The Tree tab groups parts into folders of your own (plus an automatic Hardware
folder) without changing the design's structure, and the BOM tab lists the parts
with quantities, your own columns and CSV export. Folders and columns are kept in
the design's attributes (lib/layout.py).
"""

import json
import os
import pathlib
import time
import traceback

import adsk.core
import adsk.fusion

from .lib import actions, analysis, bom, collect, components, faces, hardware, layout, log, textselect, visibility

PALETTE_ID = "browserPlusPalette"
PALETTE_NAME = "Browser+"
COMMAND_ID = "browserPlusShow"
DELETE_COMMAND_ID = "browserPlusDeleteParts"   # deleting parts as one Fusion command = one undo step
FILL_EVENT = "browserPlusFillDetails"    # custom event: read component details a batch at a time
FILL_BUDGET = 0.15                       # seconds of slow reads per batch, so Fusion stays responsive

_handlers = []
_ctrl = None

# Commands that never change joints (don't re-read the design after them).
_QUIET_COMMANDS = ("SelectCommand", "CommitCommand", "PanCommand", "OrbitCommand", "ZoomCommand",
                   "FreeOrbitCommand", "LookAtCommand", "ViewCubeCommand")


class Controller:
    def __init__(self, app):
        self.app = app
        self.ui = app.userInterface
        self.records, self.parts, self.entities = [], [], {}
        self.dirty = True
        self.focus = None           # part path the Part tab is about
        self.follow = True          # follow the canvas selection
        self._selecting = False     # our own selection changes: don't follow them
        self.edit_commands = None
        self._notice = None
        self.mates = faces.MateHighlight(app)   # coloured faces of the highlighted relationship
        self.mate_ghost = True      # mate view: mated parts see-through
        self.mate_isolate = False   # mate view: hide every other part
        self._mate_rid = None
        self.info = components.ComponentInfo()      # names / hardware / BOM facts per component
        self.layout = None          # Browser+ folders + BOM columns, kept in the design (lib/layout.py)
        self._layout_doc = None
        self.bom_open = False       # the BOM tab is showing: include the (slower) BOM in the state
        self.bom_group = True
        self.iso = visibility.Hider()   # "isolate" from the tree
        self.isolated = None        # what's isolated (label for the panel)
        self._filling = False       # a background details read is queued
        self._to_delete = []        # part paths for the delete command

    def design(self):
        return adsk.fusion.Design.cast(self.app.activeProduct)

    # ------------------------------------------------------------ data

    def refresh_data(self, force=False):
        design = self.design()
        if design is None:
            self.records, self.parts, self.entities = [], [], {}
            return
        if self.dirty or force:
            self.info.set_document(collect._document_key(design))
            with log.timed("collect"):
                self.records, self.parts, self.entities = collect.collect(design, self.info)
            self._start_fill()
            self.dirty = False
            self._load_layout(design)

    # ------------------------------------------------------------ folders (layout) and BOM

    def _load_layout(self, design):
        """Read the layout when the document changes; re-key parts whose path changed."""
        doc = collect._document_key(design)
        if self.layout is None or self._layout_doc != doc:
            attr = design.attributes.itemByName("BrowserPlus", "layout")
            self.layout = layout.load(attr.value if attr else None)
            self._layout_doc = doc

        def resolve(token):
            try:
                found = design.findEntityByToken(token)
                occ = adsk.fusion.Occurrence.cast(found[0]) if found else None
                return occ.fullPathName if occ is not None else None
            except Exception:
                return None
        if layout.heal(self.layout, {p["path"] for p in self.parts}, resolve):
            self._save_layout()

    def _save_layout(self):
        design = self.design()
        if design is None or self.layout is None:
            return
        try:
            design.attributes.add("BrowserPlus", "layout", layout.dump(self.layout))
        except Exception:
            log.error("save layout")
            self._notice = "Couldn't save the folders in the design (see the log)."

    def occurrences(self, paths):
        """Occurrences for paths (one pass over the design), skipping any not found."""
        design = self.design()
        wanted = set(p for p in paths if p)
        if design is None or not wanted:
            return []
        found = {occ.fullPathName: occ for occ in design.rootComponent.allOccurrences
                 if occ.fullPathName in wanted}
        return [found[p] for p in paths if p in found]

    def _tree_state(self):
        if self.layout is None:
            self._load_layout(self.design())
        placement = layout.place(self.parts, self.layout)
        return {
            "folders": layout.folders_view(self.layout, placement),
            "place": placement,
            "hardwareAuto": bool(self.layout.get("hardwareAuto")),
        }, placement

    def _bom_state(self, placement, folders):
        comps = self.info.components()
        wanted = self._bom_keys()
        info = {key: self.info.full(comp) for key, comp in comps.items() if key in wanted}
        data = bom.build(self.parts, placement, folders, info, self.layout, self.bom_group)
        data["grouped"] = self.bom_group
        data["hasMass"] = self.info.has_mass()
        left = sum(1 for key in wanted if info.get(key, {}).get("pending"))
        data["loading"] = {"left": left, "total": len(wanted)} if left else None
        if left:
            self._start_fill()
        return data

    def _bom_keys(self):
        return {p["componentId"] for p in bom.counted(self.parts, (self.layout or {}).get("split"))}

    # ------------------------------------------------------------ background details

    def _fill_targets(self):
        """Components whose slow facts are wanted now: all BOM parts while the BOM is open,
        else only hardware-looking names without a size (for the tree's short names)."""
        if self.bom_open:
            return self.info.pending(self._bom_keys())
        return [c for c in self.info.pending() if hardware.needs_details(c.name)]

    def _start_fill(self):
        if self._filling or not self._fill_targets():
            return
        self._filling = True
        self.app.fireCustomEvent(FILL_EVENT, "")

    def fill_step(self):
        """One batch of slow reads, then an updated panel; queues the next batch if any are left."""
        self._filling = False
        targets = self._fill_targets()
        if not targets:
            return
        started = time.perf_counter()
        for comp in targets:
            try:
                self.info.read_details(comp)
            except Exception:
                log.error("details of " + getattr(comp, "name", "?"))
                break
            if time.perf_counter() - started > FILL_BUDGET:
                break
        self.info.flush()
        comps = self.info.components()
        for part in self.parts:                 # short hardware names may have changed
            comp = comps.get(part["componentId"])
            if comp is not None:
                part["hw"] = self.info.basic(comp)["hw"]
        if self._fill_targets():
            self._filling = True
            self.app.fireCustomEvent(FILL_EVENT, "")
        else:
            self.info.log_timings()
        self.push_state()

    def _handle_tree(self, action, data):
        """Folder, visibility and BOM actions. Returns False if `action` isn't one of them."""
        lay = self.layout
        if action == "folderAdd":
            fid = layout.add_folder(lay, data.get("name"), data.get("parent"))
            if data.get("paths"):
                self._assign(data["paths"], fid)
            self._save_layout()
        elif action == "folderRename":
            layout.rename_folder(lay, data.get("id"), data.get("name"))
            self._save_layout()
        elif action == "folderDelete":
            layout.delete_folder(lay, data.get("id"))
            self._save_layout()
        elif action == "folderMove":
            if layout.move_folder(lay, data.get("id"), data.get("parent"), data.get("before")):
                self._save_layout()
        elif action == "assign":
            self._assign(data.get("paths") or [], data.get("folder"))
            self._save_layout()
        elif action == "asOne":
            # "on" = count the assembly as one part (the default), off = split it into its parts
            layout.set_split(lay, data.get("componentId"), not data.get("on"))
            self._save_layout()
            roots = layout.one_part_roots(self.parts, lay["split"])
            mine = [p for p in self.parts if p["componentId"] == data.get("componentId")]
            log.info("count as one part {}: component {} ({}), {} uses, {} pieces inside, children flags {}".format(
                "on" if data.get("on") else "off", data.get("componentId"), mine[0]["component"] if mine else "?",
                len(mine), sum(1 for p, r in roots.items() if r != p and any(r == m["path"] for m in mine)),
                [(m["path"], m["leaf"], m["bodies"]) for m in mine][:3]))
        elif action == "hardwareAuto":
            lay["hardwareAuto"] = bool(data.get("on"))
            self._save_layout()
        elif action == "setVisible":
            on = bool(data.get("on"))
            by_path = {p["path"]: p for p in self.parts}
            self._selecting = True
            try:
                for occ in self.occurrences(data.get("paths") or []):
                    occ.isLightBulbOn = on
                    by_path[occ.fullPathName]["visible"] = on
            except Exception:
                log.error("set visible")
            finally:
                self._selecting = False
        elif action == "selectParts":
            self._select(self.occurrences(data.get("paths") or []))
            return True                 # nothing in the panel changes
        elif action == "isolate":
            self._clear_mates()
            self.iso.restore()
            self._selecting = True
            try:
                with log.timed("isolate"):
                    self.iso.hide_others(self.design(), set(data.get("paths") or []))
                self.isolated = data.get("label") or "selection"
            except Exception:
                log.error("isolate")
            finally:
                self._selecting = False
        elif action == "unisolate":
            self._unisolate()
        elif action == "deleteParts":
            self._clear_mates()
            self._unisolate()
            self._to_delete = list(data.get("paths") or [])
            cmd = self.ui.commandDefinitions.itemById(DELETE_COMMAND_ID)
            if cmd is not None and self._to_delete:
                cmd.execute()       # runs delete_parts (DeletePartsHandler); Fusion then re-reads the design
            return True
        elif action == "bomOpen":
            self.bom_open = bool(data.get("open", True))
            if "group" in data:
                self.bom_group = bool(data.get("group"))
        elif action == "bomMass":
            with log.timed("masses"):
                for key, comp in self.info.components().items():
                    try:
                        self.info.calculate_mass(comp)
                    except Exception:
                        log.error("mass of " + comp.name)
        elif action == "bomColumnAdd":
            layout.add_column(lay, data.get("name"))
            self._save_layout()
        elif action == "bomColumnRename":
            layout.rename_column(lay, data.get("id"), data.get("name"))
            self._save_layout()
        elif action == "bomColumnDelete":
            layout.delete_column(lay, data.get("id"))
            self._save_layout()
        elif action == "bomValue":
            layout.set_value(lay, data.get("componentId"), data.get("column"), (data.get("value") or "").strip())
            self._save_layout()
        elif action == "bomExport":
            self._export_bom()
        else:
            return False
        self.push_state()
        return True

    def _assign(self, paths, folder_id):
        tokens = {}
        if folder_id is not None:
            for occ in self.occurrences(paths):
                tokens[occ.fullPathName] = collect._safe(lambda: occ.entityToken, "") or ""
        if not layout.assign(self.layout, paths, folder_id, tokens):
            self._notice = "Parts can't be put in the automatic Hardware folders."

    def delete_parts(self):
        """Inside the delete command: delete the parts (a part inside another deleted one goes with it)."""
        paths = self._to_delete
        self._to_delete = []
        top = [p for p in paths if not any(p.startswith(q + "+") for q in paths if q != p)]
        deleted = []
        for occ in self.occurrences(top):
            path = occ.fullPathName
            try:
                if occ.deleteMe():
                    deleted.append(path)
            except Exception:
                log.error("delete " + path)
        if self.layout is not None and deleted:
            layout.assign(self.layout, [p for p in self.layout["items"] if any(
                p == d or p.startswith(d + "+") for d in deleted)], None)
            self._save_layout()
        log.info("deleted {} of {} parts: {}".format(len(deleted), len(top), ", ".join(deleted)[:300]))
        self._notice = ("Deleted {} part{} (Ctrl+Z in Fusion brings {} back).".format(
            len(deleted), "" if len(deleted) == 1 else "s", "it" if len(deleted) == 1 else "them")
            if deleted else "Fusion wouldn't delete that (see the log).")
        self.dirty = True

    def _unisolate(self):
        was = self.iso.active
        restored = self.iso.paths()
        self._selecting = True
        try:
            self.iso.restore()
        finally:
            self._selecting = False
        self._mark_visible(restored)
        self.isolated = None
        return was

    def _export_bom(self):
        _, placement = self._tree_state()
        data = self._bom_state(placement, layout.folders_view(self.layout, placement))
        dialog = self.ui.createFileDialog()
        dialog.title = "Export BOM"
        dialog.filter = "CSV (*.csv)"
        name = (self.app.activeDocument.name if self.app.activeDocument else "BOM") + " BOM.csv"
        dialog.initialFilename = "".join(c for c in name if c not in '\\/:*?"<>|')
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        try:
            with open(dialog.filename, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(bom.to_csv(data, self.bom_group))
            self._notice = "BOM saved to " + dialog.filename
            log.info("BOM exported to " + dialog.filename)
        except Exception:
            log.error("export BOM")
            self._notice = "Couldn't write the BOM file (see the log)."

    def entity(self, record_id):
        """The live Fusion object for a record, re-reading the design if it went stale."""
        ent = self.entities.get(record_id)
        if ent is None or not ent.isValid:
            self.refresh_data(force=True)
            ent = self.entities.get(record_id)
        return ent if ent is not None and ent.isValid else None

    def _show_mates(self, rec):
        """Mate view of a relationship (or back to normal for anything else)."""
        self._selecting = True      # light bulbs going off can change Fusion's selection
        try:
            if rec is None or rec["kind"] != "constraint":
                self._clear_mates()
                return
            self._mate_rid = rec["id"]
            with log.timed("mate view"):
                drawn = self.mates.show(self.design(), rec["name"], rec["details"], rec["parts"], self.occurrence,
                                        ghost=self.mate_ghost, isolate=self.mate_isolate)
            if not drawn and rec["details"]:
                log.info("no mated faces to show for {}".format(rec["name"]))
        except Exception:
            log.error("show mated faces")
        finally:
            self._selecting = False

    def _clear_mates(self):
        """Leave mate view (parts visible again). True if it was on."""
        was = self.mates.active
        restored = self.mates.hidden_paths()
        self._selecting = True
        try:
            self.mates.clear()
        finally:
            self._selecting = False
        self._mark_visible(restored)
        self._mate_rid = None
        return was

    def _mark_visible(self, paths):
        """Parts whose light bulb we just put back on: update what was read while they were off."""
        if paths:
            for part in self.parts:
                if part["path"] in paths:
                    part["visible"] = True

    def _text_select(self, rid, entity):
        """Select a relationship through Fusion's text command Selections.Set (verified)."""
        rec = self.record(rid)
        doc = collect._document_key(self.design())
        self._selecting = True
        try:
            # The token from the record: reading entity.entityToken again can throw
            # ("InternalValidationError ... referenceKey") on relationship proxies.
            return textselect.select(self.app, self.ui, doc, self._token(rid), entity.objectType,
                                     rec["name"], not rec.get("context"))
        except Exception:
            log.error("text select")
            return False
        finally:
            self._selecting = False

    def _token(self, record_id):
        """Entity token part of a record id ("kind|context|token")."""
        return record_id.split("|", 2)[-1] if record_id else ""

    def occurrence(self, path):
        design = self.design()
        if design is None or not path:
            return None
        for occ in design.rootComponent.allOccurrences:
            if occ.fullPathName == path:
                return occ
        return None

    def record(self, record_id):
        return next((r for r in self.records if r["id"] == record_id), None)

    # ------------------------------------------------------------ state

    def state(self):
        design = self.design()
        if design is None:
            return {"error": "Open a Fusion design to use Browser+."}
        self.refresh_data()
        tree, placement = self._tree_state()
        roots = layout.one_part_roots(self.parts, self.layout.get("split"))
        # Parts only hidden for the moment (mate view, isolate) are shown as visible: that's their real state.
        temp_hidden = self.mates.hidden_paths() | self.iso.paths()
        paths = {p["path"] for p in self.parts}
        if self.focus and self.focus not in paths:
            self.focus = None
        focus = None
        if self.focus:
            held = analysis.for_part(self.records, self.focus)
            part = next(p for p in self.parts if p["path"] == self.focus)
            focus = {
                "path": self.focus,
                "name": part["name"],
                "component": part["component"],
                "grounded": part["grounded"],
                "direct": [[other, [r["id"] for r in recs]] for other, recs in held["direct"]],
                "inherited": [[anc, [r["id"] for r in recs]] for anc, recs in held["inherited"]],
            }
        return {
            "error": "",
            "document": self.app.activeDocument.name if self.app.activeDocument else "",
            "records": self.records,
            "parts": {p["path"]: {"name": p["name"], "component": p["component"], "grounded": p["grounded"],
                                  "componentId": p["componentId"], "leaf": p["leaf"], "bodies": p["bodies"],
                                  "visible": p["visible"] or p["path"] in temp_hidden, "hw": p["hw"],
                                  "asOne": roots.get(p["path"]) == p["path"],     # an assembly counted as one part
                                  "split": not p["leaf"] and p["path"] not in roots,  # an assembly listed by its parts
                                  "inOne": roots.get(p["path"], p["path"]) != p["path"]}
                      for p in self.parts},
            "tree": tree,
            "bom": self._bom_state(placement, tree["folders"]) if self.bom_open else None,
            "isolated": self.isolated if self.iso.active else None,
            "summary": analysis.summary(self.records),
            "problems": [r["id"] for r in analysis.problems(self.records)],
            "floating": analysis.floating(self.records, self.parts),
            "duplicates": analysis.duplicates(self.records),
            "graph": analysis.graph(self.records, self.parts),
            "focus": focus,
            "follow": self.follow,
            "canEdit": bool(self.edit_commands),
            "rolledBack": self._rolled_back(design),
            "mateView": {"ghost": self.mate_ghost, "isolate": self.mate_isolate,
                         "showing": self.mates.name if self.mates.active else None},
            "notice": self._notice,
        }

    def _rolled_back(self, design):
        """True when the timeline marker isn't at the end (Roll to end is offered)."""
        try:
            timeline = design.timeline
            return timeline.markerPosition < timeline.count
        except Exception:
            return False

    def push_state(self):
        palette = self.ui.palettes.itemById(PALETTE_ID)
        if palette is None or not palette.isVisible:
            return
        try:
            with log.timed("panel state"):
                data = json.dumps(self.state())
            palette.sendInfoToHTML("state", data)
            self._notice = None
        except Exception:
            log.error("push state")

    # ------------------------------------------------------------ actions

    def _select(self, entities):
        self._selecting = True
        try:
            return actions.select(self.ui, entities)
        finally:
            self._selecting = False

    def handle(self, action, data):
        rid = data.get("id")
        if self.layout is None and self.design() is not None:
            self.refresh_data()     # a tree / BOM action can arrive before the first state push
            if self.layout is None:
                self._load_layout(self.design())
        if self.layout is not None and self._handle_tree(action, data):
            return
        if action in ("ready", "refresh"):
            if action == "refresh":
                self.info.clear()
                self._clear_mates()
                self.dirty = True
                collect.clear_cache()
        elif action == "focus":
            self.focus = data.get("path") or None
            if self.focus and data.get("select"):
                self._select([self.occurrence(self.focus)])
        elif action == "follow":
            self.follow = bool(data.get("on"))
        elif action == "highlight":
            rec = self.record(rid)
            ent = self.entity(rid)
            was = self.mates.active
            self._show_mates(rec)
            # In mate view the parts are hidden (redrawn see-through), so only the relationship is selected.
            parts = [] if self.mates.active and self.mate_ghost else [self.occurrence(p) for p in rec["parts"]] if rec else []
            if rec and ent:
                self._selecting = True
                try:
                    picked = actions.select_item(self.ui, self.design(), ent, self._token(rid))
                    if parts:
                        actions.select(self.ui, parts, keep=True)
                finally:
                    self._selecting = False
                if picked is None and rec["kind"] == "constraint" and self._text_select(rid, ent):
                    picked = ent
                    if parts:
                        self._selecting = True
                        try:
                            actions.select(self.ui, parts, keep=True)
                        finally:
                            self._selecting = False
                if picked is None:
                    self._notice = "Fusion wouldn't select it (the parts are selected instead). See the log."
                    self.push_state()
                    return
            if was or self.mates.active:
                self.push_state()   # the mate-view bar
            return
        elif action == "mateView":
            self.mate_ghost = bool(data.get("ghost", self.mate_ghost))
            self.mate_isolate = bool(data.get("isolate", self.mate_isolate))
            if self.mates.active and self._mate_rid:
                self._show_mates(self.record(self._mate_rid))
        elif action == "clearMates":
            self._clear_mates()
            return
        elif action == "selectPart":
            self._select([self.occurrence(data.get("path"))])
            return
        elif action == "zoom":
            rec = self.record(rid) if rid else None
            paths = rec["parts"] if rec else [data.get("path")]
            actions.zoom_to(self.app, [self.occurrence(p) for p in paths if p])
            return
        elif action == "edit":
            self._clear_mates()     # the editor needs the real parts to pick from
            ent = self.entity(rid)
            if ent is None:
                return
            rec = self.record(rid)
            if rec and rec["kind"] == "constraint":
                # Fusion's API can't select relationships, but its text command
                # Selections.Set can: select it that way, then open Fusion's editor.
                if self._text_select(rid, ent):
                    cmd = self.ui.commandDefinitions.itemById("DcEditAssemblyMateCmd")
                    try:
                        if cmd is not None and cmd.execute():
                            log.info("edit relationship via DcEditAssemblyMateCmd")
                            return
                    except Exception:
                        log.error("DcEditAssemblyMateCmd")
                # Fallback: put the timeline marker right after it; its icon then sits
                # next to the marker, ready to double-click.
                try:
                    actions.roll_to(ent)
                    self._notice = ("Fusion doesn't let add-ins select relationships, so the timeline is rolled "
                                    "to \u201c{}\u201d: double-click its icon just left of the marker to edit it, "
                                    "then use Roll to end.".format(rec["name"]))
                except Exception:
                    log.error("roll to relationship")
                    self._notice = "Couldn't roll the timeline to it; find \u201c{}\u201d in the browser.".format(rec["name"])
                self.dirty = True
                self.push_state()
                return
            candidates = actions.edit_candidates(rec["kind"] if rec else "")
            self._selecting = True
            try:
                ok = actions.edit(self.ui, self.design(), ent, self._token(rid), candidates)
            finally:
                self._selecting = False
            if not ok:
                self._notice = ("It's selected in Fusion now: right-click it (in the canvas or browser) and choose "
                                "Edit. Browser+ learns that editor from the right-click menu, so next time Edit "
                                "opens it directly.")
        elif action in ("suppress", "rename", "delete", "rollTo"):
            if action != "rename":
                self._clear_mates()
            ent = self.entity(rid)
            if ent is None:
                self._notice = "That joint no longer exists."
            else:
                try:
                    if action == "suppress":
                        actions.set_suppressed(ent, data.get("on"))
                    elif action == "rename":
                        actions.rename(ent, data.get("name", "").strip() or ent.name)
                        if rid == self._mate_rid:
                            self.mates.name = ent.name
                    elif action == "delete":
                        actions.delete(ent)
                    else:
                        actions.roll_to(ent)
                        self._notice = "Timeline rolled to this joint. Use “Roll to end” when you're done."
                except Exception:
                    log.error(action)
                    self._notice = "Fusion couldn't {} it (see the log).".format(
                        {"suppress": "change", "rename": "rename", "delete": "delete", "rollTo": "roll to"}[action])
                self.dirty = True
        elif action == "rollEnd":
            actions.roll_to_end(self.design())
            self.dirty = True
        self.push_state()

    # ------------------------------------------------------------ events

    def on_selection(self):
        if self._selecting:
            return
        sels = self.ui.activeSelections
        relationship = adsk.fusion.AssemblyConstraint.classType()
        if (not any(getattr(sels.item(i).entity, "objectType", "") == relationship for i in range(sels.count))
                and self._clear_mates()):   # the user picked something else in Fusion
            self.push_state()
        if sels.count == 0:
            return
        ent = sels.item(sels.count - 1).entity
        if sels.count == 1 and getattr(ent, "objectType", "") == adsk.fusion.AssemblyConstraint.classType():
            # The user picked a relationship in Fusion: learn its selection path for Edit.
            try:
                paths = textselect.selection_paths(self.app)
                if len(paths) == 1:
                    textselect.learn_relationship(collect._document_key(self.design()), paths[0], ent.entityToken)
            except Exception:
                log.error("learn relationship path")
        if not self.follow:
            return
        occ = adsk.fusion.Occurrence.cast(ent)
        if occ is None:
            ctx = getattr(ent, "assemblyContext", None)
            occ = adsk.fusion.Occurrence.cast(ctx) if ctx is not None else None
        if occ is not None and occ.fullPathName != self.focus:
            self.focus = occ.fullPathName
            self.push_state()

    def on_command_done(self, command_id):
        if command_id in _QUIET_COMMANDS or command_id.startswith(COMMAND_ID):
            return
        log.info("command finished: " + command_id)    # to learn Fusion's own edit commands
        low = command_id.lower()
        if any(w in low for w in ("joint", "constraint", "relationship")):
            collect.clear_cache()       # a relationship may have changed parts
        self.dirty = True
        self.push_state()

    def on_document_switch(self):
        self._clear_mates()
        self._unisolate()
        self.layout = None          # component facts are per design (set_document on the next read)
        collect.clear_cache()
        self.dirty = True
        self.focus = None
        self.push_state()


def _palette_url():
    here = pathlib.Path(os.path.abspath(__file__)).parent
    return (here / "palette" / "index.html").as_uri()


# ---------------------------------------------------------------- event handlers

class PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            event = adsk.core.HTMLEventArgs.cast(args)
            if event.action == "response":
                return
            _ctrl.handle(event.action, json.loads(event.data) if event.data else {})
            event.returnData = "OK"
        except Exception:
            log.error("palette action " + str(getattr(args, "action", "?")))
            _ctrl.ui.messageBox("Browser+ failed:\n{}".format(traceback.format_exc()))


class ShowPaletteHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            ui = _ctrl.ui
            palette = ui.palettes.itemById(PALETTE_ID)
            if not palette:
                palette = ui.palettes.add(PALETTE_ID, PALETTE_NAME, _palette_url(), True, True, True, 420, 700)
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
                _add(palette.incomingFromHTML, PaletteHTMLHandler())
            palette.isVisible = True
            _ctrl.dirty = True
        except Exception:
            log.error("show palette")
            _ctrl.ui.messageBox("Browser+ failed:\n{}".format(traceback.format_exc()))


class SelectionHandler(adsk.core.ActiveSelectionEventHandler):
    def notify(self, args):
        try:
            _ctrl.on_selection()
        except Exception:
            log.error("selection changed")


class MarkingMenuHandler(adsk.core.MarkingMenuEventHandler):
    """Learn Fusion's edit command for a joint / relationship from its right-click menu."""

    def notify(self, args):
        try:
            kind = actions.learn_from_menu(adsk.core.MarkingMenuEventArgs.cast(args), _ctrl.app)
            if kind:
                _ctrl._notice = "Learned how to edit {}: Edit in Browser+ opens it directly now.".format(
                    {"constraint": "relationships", "joint": "joints", "asBuilt": "as-built joints",
                     "motionLink": "motion links", "rigidGroup": "rigid groups"}[kind])
                _ctrl.push_state()
        except Exception:
            log.error("right-click menu")


class CommandTerminatedHandler(adsk.core.ApplicationCommandEventHandler):
    def notify(self, args):
        try:
            args = adsk.core.ApplicationCommandEventArgs.cast(args)
            _ctrl.on_command_done(args.commandId)
        except Exception:
            log.error("command terminated")


class DeletePartsCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            cmd.isRepeatable = False
            _add(cmd.execute, DeletePartsExecuteHandler())
        except Exception:
            log.error("delete command")


class DeletePartsExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _ctrl.delete_parts()
        except Exception:
            log.error("delete parts")


class FillHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            if _ctrl is not None:
                _ctrl.fill_step()
        except Exception:
            log.error("fill details")


class DocSwitchHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            _ctrl.on_document_switch()
        except Exception:
            log.error("document switch")


def _add(event, handler):
    event.add(handler)
    _handlers.append(handler)


def run(context):
    global _ctrl
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _ctrl = Controller(app)
        log.info("Browser+ starting")
        cmd_def = ui.commandDefinitions.itemById(COMMAND_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                COMMAND_ID, "Browser+", "Find, check and tidy joints and relationships")
        _add(cmd_def.commandCreated, ShowPaletteHandler())
        panel = ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")
        if panel and not panel.controls.itemById(COMMAND_ID):
            panel.controls.addCommand(cmd_def)
        _ctrl.edit_commands = actions.find_edit_commands(ui)
        actions.dump_text_commands(app, os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs",
                                                     "textcommands.txt"))
        del_def = ui.commandDefinitions.itemById(DELETE_COMMAND_ID)
        if not del_def:
            del_def = ui.commandDefinitions.addButtonDefinition(DELETE_COMMAND_ID, "Browser+ delete parts",
                                                                "Delete parts picked in Browser+")
        _add(del_def.commandCreated, DeletePartsCreatedHandler())
        _add(ui.activeSelectionChanged, SelectionHandler())
        _add(ui.commandTerminated, CommandTerminatedHandler())
        _add(ui.markingMenuDisplaying, MarkingMenuHandler())
        _add(app.documentActivated, DocSwitchHandler())
        try:
            app.unregisterCustomEvent(FILL_EVENT)
        except Exception:
            pass
        _add(app.registerCustomEvent(FILL_EVENT), FillHandler())
    except Exception:
        log.error("run")
        ui.messageBox("Browser+ failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        try:
            app.unregisterCustomEvent(FILL_EVENT)
        except Exception:
            pass
        if _ctrl is not None:
            _ctrl.info.flush()
            _ctrl.mates.clear()
            _ctrl.iso.restore()
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
        del_def = ui.commandDefinitions.itemById(DELETE_COMMAND_ID)
        if del_def:
            del_def.deleteMe()
        log.info("Browser+ stopped")
    except Exception:
        log.error("stop")
        ui.messageBox("Browser+ failed to stop:\n{}".format(traceback.format_exc()))
