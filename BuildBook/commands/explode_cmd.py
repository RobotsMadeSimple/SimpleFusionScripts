"""Explode Move editor: create or edit one explode move of a step.

A step's explode moves (lib/model.py) run in order and chain. This command
edits one of them, or a new one, with everything restored from the manual:
parts, direction (axis, X/Y/Z amounts, or the picked edge/face/axis, which is
re-selected), distance (or "follow the step default"), spacing, trail lines.

The canvas shows the step as it stands just after this move: parts in it are
blue at their new place, everything moved earlier sits where earlier moves
left it, and moved copies can be clicked to pick those parts. The arrow starts
where the picked parts are now.

"Next move: same parts" / "Next move: new parts" keep this move and start
another right after it, so a chain can be built in one go. OK saves them all;
Cancel saves nothing. Nothing in the model moves.
"""

import copy
import os
import re
import time
import traceback

import adsk.core
import adsk.fusion

from ..lib import explode, log, model, paths, refs

CMD_ID = "buildBookExplode"
AFTER_DRAG_EVENT = "buildBookExplodeAfterDrag"   # custom event: refill the box once a drag is done
CMD_NAME = "Explode Move"
CMD_TIP = "Create or edit an explode move of the current BuildBook step"

PICKED = "Picked edge / face / axis"
XYZ = "X / Y / Z amounts"
AXES = ["X", "Y", "Z"]                  # one per axis: a negative distance goes the other way
SPACING_LABELS = [             # (key, button name, icon folder)
    (model.UNIFORM, "Uniform: all move the same distance", "spacing_uniform"),
    (model.STACKED, "Stacked by position: the farther along, the farther it moves (level parts together)", "spacing_stacked"),
    (model.STACKED_REVERSE, "Stacked by position, reversed: the farther along, the less it moves (level parts together)", "spacing_reverse"),
    (model.STACKED_SELECTION, "Stacked in pick order: each part picked moves one step farther", "spacing_order"),
]
XYZ_INPUTS = ("dx", "dy", "dz")


class ExplodeCommand:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._handlers = []
        self.step_id = None
        self.explode_id = None       # the move being edited; None = a new one
        self.insert_at = None        # index for new moves; None = at the end
        self.preselect = []
        self._reset_session()

    def _reset_session(self):
        self.working = None          # the explode move being edited (a copy)
        self.pending = []            # moves finished with "Next move", saved before `working`
        self.picked = {}             # path -> occurrence, in pick order
        self.syncing = False         # sync_box is refilling the Parts box: ignore its select events
        self.last_unpickable = None  # (diagnostics: last kind of drawing hovered that isn't a copy)
        self.arrow_off_reason = None
        self.box_logged = None
        self.ignore_select_until = 0.0
        self.arrow_place = None      # (centre + direction) the arrow was last placed at
        self.dragging_until = 0.0    # distance changes keep coming while the arrow is dragged
        self.hover = None            # part whose drawn copy is under the cursor (drawn highlighted)
        self.dragged = False         # the arrow was dragged since the last mouse up
        self.mouse_down_at = 0.0     # last mouse press in the canvas (copy clicks vs. the box's own removals)
        self.mouse_down_pos = None   # where the left button went down (a release nearby = a click)
        self.drawn_logged = None
        self.distance_touched = False
        self.dir_hidden = []         # parts hidden with H while picking a direction
        self.dir_hover = None        # part under the cursor while picking a direction
        self.saved_along = False     # editing a move along an edge / face: its saved direction is used
                                     # (not re-selected: selecting it showed the part it's on)
        self.trail_touched = False   # trail checkbox changed: apply it to every part
        self.inputs = None
        self.command = None

    # ------------------------------------------------------------ wiring

    def register(self, ui):
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TIP)
        handler = _Created(self)
        cmd_def.commandCreated.add(handler)
        self._handlers.append(handler)
        app = adsk.core.Application.get()
        try:
            app.unregisterCustomEvent(AFTER_DRAG_EVENT)
        except Exception:
            pass
        after = _AfterDrag(self)
        app.registerCustomEvent(AFTER_DRAG_EVENT).add(after)
        self._handlers.append(after)

    def unregister(self, ui):
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        try:
            adsk.core.Application.get().unregisterCustomEvent(AFTER_DRAG_EVENT)
        except Exception:
            pass

    def launch(self, step_id, explode_id=None, occs=None, insert_at=None):
        """Edit move `explode_id`, or start a new one with `occs` (default: the Fusion selection)."""
        self.step_id = step_id
        self.explode_id = explode_id
        self.insert_at = insert_at
        self.preselect = occs if occs else ([] if explode_id else refs.selected_occurrences(self.ctrl.ui))
        self.ctrl.ui.commandDefinitions.itemById(CMD_ID).execute()

    # ------------------------------------------------------------ reading inputs

    def _step(self):
        manual = self.ctrl.load()
        _, step = model.find_step(manual, self.step_id)
        return manual, step

    def _input(self, cls, input_id):
        found = self.inputs.itemById(input_id)
        if found is None:
            found = _find_input(self.inputs, input_id)      # inside the "More" group / a table
        return cls.cast(found)

    def axis_choice(self):
        return self._input(adsk.core.ButtonRowCommandInput, "axis").selectedItem.name

    def direction_from_inputs(self):
        """Direction spec for the current inputs (keeps the remembered pick if nothing is selected)."""
        old = self.working["direction"]
        flip = self._input(adsk.core.DistanceValueCommandInput, "distance").value < 0    # minus = other way
        choice = self.axis_choice()
        if choice == XYZ:
            vec = [self._input(adsk.core.ValueCommandInput, i).value for i in XYZ_INPUTS]
            return {"kind": model.DIR_XYZ, "axis": None, "vector": vec, "token": None, "flip": False}
        if choice == PICKED:
            pick = self._input(adsk.core.SelectionCommandInput, "dirEntity")
            if pick.selectionCount:
                ent = pick.selection(0).entity
                vec = _entity_direction(ent)
                if vec is not None:
                    token = None
                    try:
                        token = ent.entityToken
                    except Exception:
                        pass
                    return {"kind": model.DIR_ENTITY, "axis": None, "vector": list(vec), "token": token, "flip": flip}
            if old.get("kind") == model.DIR_ENTITY and old.get("vector"):
                return dict(old, flip=flip)
            return model.signed_direction(model.new_direction("+Z"), flip)
        return {"kind": model.DIR_AXIS, "axis": "+" + choice, "vector": None, "token": None, "flip": flip}

    def spacing(self):
        item = self._input(adsk.core.ButtonRowCommandInput, "spacing").selectedItem
        name = item.name if item is not None else ""
        return next((key for key, label, _ in SPACING_LABELS if label == name), model.UNIFORM)

    def follows_default(self):
        """A move that followed the step default keeps following it until its distance is changed
        here (dragged, typed or flipped). (No "Follow step default" box: clearing the distance in
        the panel's move list makes a move follow the default again.)"""
        return self.working.get("distance") is None and not self.distance_touched

    def working_state(self):
        """The edited move as the inputs describe it now."""
        ex = copy.deepcopy(self.working)
        ex["name"] = self._input(adsk.core.StringValueCommandInput, "name").value.strip()
        ex["direction"] = self.direction_from_inputs()
        ex["spacing"] = self.spacing()
        if ex["direction"]["kind"] == model.DIR_XYZ or self.follows_default():
            ex["distance"] = None
        else:
            ex["distance"] = abs(self._input(adsk.core.DistanceValueCommandInput, "distance").value)
        old = {p["ref"].get("path"): p for p in ex["parts"]}
        ex["parts"] = [old.get(path) or model.new_explode_part(refs.make_ref(occ))
                       for path, occ in self.picked.items() if occ.isValid]
        if self.trail_touched:
            on = self._input(adsk.core.BoolValueCommandInput, "trail").value
            for part in ex["parts"]:
                part["trail"] = on
        return ex

    # ------------------------------------------------------------ previewing

    def sequence(self, manual, step):
        """Working copies of (manual, step) with the pending moves and this one in place."""
        tmp = copy.deepcopy(manual)
        _, tstep = model.find_step(tmp, self.step_id)
        explodes = tstep["explodes"]
        # The saved move being replaced: the one opened for editing. After "Next
        # move" the working move has a new id, but the opened one must still go.
        target = self.explode_id or self.working["id"]
        index = next((i for i, ex in enumerate(explodes) if ex["id"] == target), None)
        if index is not None:
            explodes.pop(index)
        elif self.explode_id is None and self.insert_at is not None:
            index = min(self.insert_at, len(explodes))
        else:
            index = len(explodes)
        new = [copy.deepcopy(ex) for ex in self.pending] + [self.working_state()]
        explodes[index:index] = new
        for ex in new:
            model.add_items(tstep, [p["ref"] for p in ex["parts"]])
        return tmp, tstep

    def positions_before(self, tmp, tstep, occs):
        """World centres of `occs` after every move before the working one."""
        index = next(i for i, ex in enumerate(tstep["explodes"]) if ex["id"] == self.working["id"])
        earlier = dict(tstep, explodes=tstep["explodes"][:index])
        centers = {o.fullPathName: refs.bbox_center(o) for o in occs}
        moves = model.evaluate(tmp, earlier, centers)
        out = []
        for occ in occs:
            c = centers[occ.fullPathName]
            for vec, _, _ in moves.get(occ.fullPathName, []):
                c = explode.add(c, vec)
            out.append(c)
        return out

    def refresh(self, preview=True):
        """Update arrow, visibility of inputs and status; redraw the canvas."""
        manual, step = self._step()
        tmp, tstep = self.sequence(manual, step)
        xyz = self.axis_choice() == XYZ
        for i in XYZ_INPUTS:
            self.inputs.itemById(i).isVisible = xyz
        along = self._input(adsk.core.SelectionCommandInput, "dirEntity")
        if along is not None:
            # only for "along an edge / face", and not while the saved one is used (the
            # "Pick a different edge or face" button stands in for it)
            along.isVisible = self.axis_choice() == PICKED and not self.saved_along
        dist = self._input(adsk.core.DistanceValueCommandInput, "distance")
        occs = [o for o in self.picked.values() if o.isValid]
        unit = model.direction_unit(model.signed_direction(self.direction_from_inputs(), False))
        if occs and not xyz and explode.length(unit) > 0:
            center = explode.centroid(self.positions_before(tmp, tstep, occs))
            dist.isVisible = True
            place = tuple(round(v, 6) for v in tuple(center) + tuple(unit))
            if place != self.arrow_place:
                # Placing the arrow again while it's being dragged stops the drag (the part
                # snapped back), so only when where it starts or which way it points changes.
                self.arrow_place = place
                dist.setManipulator(adsk.core.Point3D.create(*center), adsk.core.Vector3D.create(*unit))
        else:
            self.arrow_place = None
            dist.isVisible = False
            why = "no parts picked" if not occs else "XYZ amounts" if xyz else "no direction"
            if why != self.arrow_off_reason:
                self.arrow_off_reason = why
                log.info("explode: arrow hidden ({})".format(why))
        self.update_status(tstep)
        if preview and self.picking_direction():
            # Picking "Along": the whole model as built (lines on other parts too), minus the
            # parts hidden with H to reach inside. Applied each preview (rolled back with it).
            self.ctrl.scene.clear()
            for occ in self.dir_hidden:
                try:
                    if occ.isValid and occ.isLightBulbOn:
                        occ.isLightBulbOn = False
                except Exception:
                    pass
            self.ctrl.app.activeViewport.refresh()
            return
        if preview:
            if self.dir_hidden and not self.picking_direction():
                self.dir_hidden = []            # a direction was picked: those parts come back
            self.ctrl.scene.show(self.ctrl.design(), tmp, self.step_id, edit=True,
                                 current=set(self.picked), upto=self.working["id"], hover=self.hover)
            self.sync_box()             # the new copies stand for the moved parts in the box
            try:
                drawn = (round(self._input(adsk.core.DistanceValueCommandInput, "distance").value, 4),
                         self.follows_default(), tuple(sorted(self.picked)))
                if drawn != self.drawn_logged:
                    self.drawn_logged = drawn
                    log.info("explode: drew the move at distance {} cm (follows default: {}), parts {}".format(*drawn))
            except Exception:
                pass
            self.ctrl.app.activeViewport.refresh()

    def picking_direction(self):
        """Direction "Picked" with nothing picked yet: the user is choosing a line / face."""
        try:
            if self.axis_choice() != PICKED or self.saved_along:
                return False
            along = self._input(adsk.core.SelectionCommandInput, "dirEntity")
            return along is not None and along.selectionCount == 0
        except Exception:
            return False

    def use_new_along(self):
        """Drop the saved Along direction and pick a new edge / face (the assembled view shows)."""
        self.saved_along = False
        self._input(adsk.core.BoolValueCommandInput, "repickAlong").isVisible = False
        along = self._input(adsk.core.SelectionCommandInput, "dirEntity")
        along.isVisible = True
        along.hasFocus = True
        self.refresh()

    def hide_hovered_for_direction(self):
        occ = self.dir_hover
        if occ is None or not self.picking_direction():
            return
        if all(o.fullPathName != occ.fullPathName for o in self.dir_hidden):
            self.dir_hidden.append(occ)
            log.info("explode: picking a direction, hid " + occ.fullPathName)
            self.redraw()

    def unhide_for_direction(self):
        if self.dir_hidden and self.picking_direction():
            self.dir_hidden.pop()
            self.redraw()

    def update_status(self, tstep):
        status = self._input(adsk.core.TextBoxCommandInput, "status")
        if self.picking_direction():
            status.formattedText = ("Pick an <b>edge, face or axis</b> to move along (any part: the whole model "
                                    "is shown). Hover a part + <b>H</b> hides it, <b>U</b> brings it back.")
            return
        position = next(i for i, ex in enumerate(tstep["explodes"]) if ex["id"] == self.working["id"]) + 1
        n = len(self.picked)
        text = "Move <b>{}</b> of {} &nbsp;·&nbsp; <b>{}</b> part{} picked".format(
            position, len(tstep["explodes"]), n, "" if n == 1 else "s")
        if self.pending:
            text += " &nbsp;·&nbsp; {} more move(s) ready to save".format(len(self.pending))
        text += ("<br>Click a part (or its copy) to add it; click a copy outlined in <b>blue</b> to take "
                 "it out. Drag the arrow to set the distance.")
        status.formattedText = text

    # ------------------------------------------------------------ actions

    def copy_at(self, pos):
        """The part whose drawn copy is under a viewport position: the copy's outline on screen
        (its bounding box, moved, projected) contains it; the smallest one wins."""
        viewport = self.ctrl.app.activeViewport
        best, best_area = None, None
        for path, occ, off in self.ctrl.scene.copy_places:
            try:
                box = occ.boundingBox
                xs, ys = [], []
                for x in (box.minPoint.x, box.maxPoint.x):
                    for y in (box.minPoint.y, box.maxPoint.y):
                        for z in (box.minPoint.z, box.maxPoint.z):
                            p = viewport.modelToViewSpace(adsk.core.Point3D.create(x + off[0], y + off[1], z + off[2]))
                            xs.append(p.x)
                            ys.append(p.y)
            except Exception:
                continue
            if min(xs) - 3 <= pos.x <= max(xs) + 3 and min(ys) - 3 <= pos.y <= max(ys) + 3:
                area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if best_area is None or area < best_area:
                    best, best_area = path, area
        return best

    def hover_copy(self, pos):
        """Mouse moved: the copy under the cursor draws highlighted (what a click takes out of the
        move). One redraw each time the cursor moves onto / off a copy; nothing in between (the
        status text stays put: changing it on every move re-ran the preview)."""
        if time.perf_counter() < self.dragging_until or self.mouse_down_pos is not None \
                or self.picking_direction():
            return                          # (dragging the arrow / orbiting / picking "Along")
        leaf = self.copy_at(pos)
        unit = self.path_for_graphics(leaf) if leaf else None
        if unit == self.hover:
            return
        self.hover = unit
        self.redraw()

    def redraw(self):
        """Run a preview now. doExecutePreview from a mouse event is ignored; a changed input
        always brings one, so flip the hidden "nudge" input."""
        try:
            nudge = self._input(adsk.core.BoolValueCommandInput, "nudge")
            nudge.value = not nudge.value
        except Exception:
            log.error("explode redraw")
        if self.command is not None:
            self.command.doExecutePreview()

    def part_at(self, pos):
        """The visible body under a viewport position (nearest along the view ray), or None.
        Parts in this move are drawn as copies and their originals hidden: a click where an
        original sits goes through it to whatever is behind (Fusion's ray test still hit the
        hidden original, so clicking there took the part out of the move)."""
        scene = self.ctrl.scene
        # (the editor's record only grows: count the ones actually switched off now)
        hidden = {p for p, o in scene._hidden_occs.items() if o.isValid and not o.isLightBulbOn}
        picked = list(self.picked)

        def shown(body):
            if not body.isVisible:
                return False
            occ = body.assemblyContext
            if occ is None:
                return True
            path = occ.fullPathName
            if refs.is_split(occ):
                path = refs.BodyPart(occ, body).fullPathName
            return not any(model.is_self_or_ancestor(p, path) for p in picked) and                 not any(model.is_self_or_ancestor(h, path) for h in hidden)

        return refs.body_at(self.ctrl.app.activeViewport, self.ctrl.design(), pos, shown)

    def click_at(self, pos):
        """A click in the canvas: a drawn copy or a real part toggles in the move (the whole
        assembly / sub-assembly / single part, per "Click picks")."""
        try:
            along = self._input(adsk.core.SelectionCommandInput, "dirEntity")
            if self.picking_direction():
                return      # picking a direction (edge / face) for "Along", not a part
            # (once one is picked, clicks pick parts again even if the Along box keeps the focus)
        except Exception:
            pass
        leaf = self.copy_at(pos)
        if leaf is not None:
            path, part = self.path_for_graphics(leaf), None
            what = "copy of " + path
        else:
            try:
                body = self.part_at(pos)
            except Exception:
                log.error("explode: find the part under the cursor")
                body = None
            occ = body.assemblyContext if body is not None else None
            if occ is None:
                log.info("explode: click at ({:.0f}, {:.0f}): nothing to pick".format(pos.x, pos.y))
                return
            if refs.is_split(occ):
                part = refs.BodyPart(occ, body)
            else:
                _, step = self._step()
                known = set(self.picked) | (set(model.item_paths(step)) if step is not None else set())
                part = refs.pick_unit(occ, getattr(self.ctrl, "pick_level", refs.PICK_WHOLE), known)
            path, what = part.fullPathName, occ.fullPathName
        if path in self.picked:
            del self.picked[path]
            log.info("explode: clicked {} -> {} taken out of the move".format(what, path))
        else:
            self.pick_path(path, part)
            log.info("explode: clicked {} -> {} added to the move".format(what, path))
        self.redraw()

    def click_copy(self, pos):
        """A click in the canvas: on a drawn copy, pick / unpick its part. True if it was one."""
        leaf = self.copy_at(pos)
        log.info("explode: click at ({:.0f}, {:.0f}); {} drawn copies; {}".format(
            pos.x, pos.y, len(self.ctrl.scene.copy_places), "on copy of " + leaf if leaf else "not on a copy"))
        if leaf is None:
            for path, occ, off in self.ctrl.scene.copy_places[:3]:
                try:
                    c = occ.boundingBox
                    m = adsk.core.Point3D.create((c.minPoint.x + c.maxPoint.x) / 2 + off[0],
                                                 (c.minPoint.y + c.maxPoint.y) / 2 + off[1],
                                                 (c.minPoint.z + c.maxPoint.z) / 2 + off[2])
                    v = self.ctrl.app.activeViewport.modelToViewSpace(m)
                    log.info("    copy of {} centre on screen ({:.0f}, {:.0f})".format(path, v.x, v.y))
                except Exception:
                    log.error("copy position")
            return False
        path = self.path_for_graphics(leaf)
        if path in self.picked:
            del self.picked[path]
            log.info("explode: clicked copy of {} -> taken out of the move".format(path))
        else:
            self.pick_path(path)
            log.info("explode: clicked copy of {} -> added to the move".format(path))
        self.ignore_select_until = time.perf_counter() + 1.0   # (the same click as a Fusion select)
        self.redraw()
        return True

    def sync_box(self):
        """(The Parts box is hidden and kept empty: see the "parts" input.)"""
        return

    def _old_sync_box(self):
        """Former: make the Parts box show exactly the picked occurrences."""
        if time.perf_counter() < self.dragging_until:
            # Mid-drag the box is left alone: any change to it (even adding) interrupts the arrow
            # drag, and the part snapped back. The copy held in it is redrawn each frame, so the
            # box shows nothing until the mouse is let go (_MouseUp refills it).
            return
        box = self._input(adsk.core.SelectionCommandInput, "parts")
        try:
            # Up to date only if the box holds exactly the picked parts, each as something live:
            # a visible part, or the current drawing of a moved one.
            def held(entity):
                if not entity.isValid:
                    return None
                occ = adsk.fusion.Occurrence.cast(entity)
                if occ is not None:
                    return occ.fullPathName if occ.isVisible else None
                leaf = self.ctrl.scene.pickable_path(entity)
                return self.path_for_graphics(leaf) if leaf else None
            paths = [held(box.selection(i).entity) for i in range(box.selectionCount)]
            fresh = None not in paths and sorted(paths) == sorted(self.picked)
        except Exception:
            fresh = False
        if fresh:
            return
        self.syncing = True
        try:
            box.clearSelection()
            for path, occ in self.picked.items():
                try:
                    if adsk.fusion.Occurrence.cast(occ) is not None and occ.isVisible:
                        box.addSelection(occ)
                        continue
                except Exception:
                    pass
                # A moved part is hidden at home: its drawn copy goes in the box instead.
                for entity in self.ctrl.scene.copy_entities(path):
                    try:
                        if box.addSelection(entity):
                            break
                    except Exception:
                        continue
        except Exception:
            log.error("explode sync box")
        finally:
            self.syncing = False
        try:
            held = (box.selectionCount, len(self.picked))
            self.box_logged = held
            log.info("explode: box refilled: holds {} of {} picked".format(*held))
        except Exception:
            pass

    def pick_path(self, path, occ=None):
        if occ is None:
            occ = refs.path_index(self.ctrl.design()).get(path)
        if occ is not None:
            self.picked[path] = occ

    def path_for_graphics(self, leaf_path):
        """The part a click on a drawn copy stands for: the innermost step part owning it."""
        _, step = self._step()
        candidates = set(self.picked)
        if step is not None:
            candidates.update(model.item_paths(step))
            candidates.update(model.explode_paths(step))
        owners = [c for c in candidates if model.is_self_or_ancestor(c, leaf_path)]
        return max(owners, key=len) if owners else leaf_path

    def next_move(self):
        """Keep this move and start a new, empty one right after it."""
        done = self.working_state()
        if not done["parts"]:
            return
        self.pending.append(done)
        direction = done["direction"]
        choice = self.axis_choice()
        if choice in AXES:
            # The next move goes along the next axis (X -> Y -> Z -> X), the plus way: chains like
            # "slide out along X, then drop along Z" need at most one change.
            nxt = AXES[(AXES.index(choice) + 1) % len(AXES)]
            direction = model.new_direction("+" + nxt)
            axis = self._input(adsk.core.ButtonRowCommandInput, "axis")
            for i in range(axis.listItems.count):
                if axis.listItems.item(i).name == nxt:
                    axis.listItems.item(i).isSelected = True
            dist = self._input(adsk.core.DistanceValueCommandInput, "distance")
            dist.value = abs(dist.value)
            self.arrow_place = None             # the arrow points along the new axis
        self.working = model.new_explode(direction, done["spacing"])
        self.trail_touched = False
        self._input(adsk.core.StringValueCommandInput, "name").value = ""
        self.picked = {}
        self._input(adsk.core.SelectionCommandInput, "parts").clearSelection()
        self.refresh()

    def save(self):
        manual, step = self._step()
        if step is None:
            return 0
        tmp, tstep = self.sequence(manual, step)
        # Drop empty moves; keep everything else exactly as previewed.
        tstep["explodes"] = [ex for ex in tstep["explodes"] if ex["parts"]]
        step["explodes"] = tstep["explodes"]
        step["items"] = tstep["items"]
        last = self.working_state()
        manual["settings"]["lastExplode"] = {"direction": last["direction"], "spacing": last["spacing"]}
        self.ctrl.save(manual)
        return len(self.pending) + 1


def _entity_direction(ent):
    try:
        edge = adsk.fusion.BRepEdge.cast(ent)
        if edge:
            line = adsk.core.Line3D.cast(edge.geometry)
            return _line_dir(line) if line else None
        face = adsk.fusion.BRepFace.cast(ent)
        if face:
            ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
            return (normal.x, normal.y, normal.z) if ok else None
        sketch_line = adsk.fusion.SketchLine.cast(ent)
        if sketch_line:
            return _line_dir(sketch_line.worldGeometry)
        axis = adsk.fusion.ConstructionAxis.cast(ent)
        if axis:
            d = axis.geometry.direction
            return (d.x, d.y, d.z)
        plane = adsk.fusion.ConstructionPlane.cast(ent)
        if plane:
            n = plane.geometry.normal
            return (n.x, n.y, n.z)
    except Exception:
        log.error("direction from entity")
    return None


def _line_dir(line):
    a, b = line.startPoint, line.endPoint
    return explode.normalize((b.x - a.x, b.y - a.y, b.z - a.z))


def _find_input(inputs, input_id):
    """An input by id anywhere: also inside groups and tables (in case itemById only looks at
    the top level)."""
    for i in range(inputs.count):
        item = inputs.item(i)
        if item.id == input_id:
            return item
        group = adsk.core.GroupCommandInput.cast(item)
        table = adsk.core.TableCommandInput.cast(item)
        children = group.children if group is not None else (table.commandInputs if table is not None else None)
        if children is not None:
            found = children.itemById(input_id) or _find_input(children, input_id)
            if found is not None:
                return found
    return None


def _fail(ctrl, where):
    log.error(where)
    ctrl.ui.messageBox("BuildBook: {} failed.\n\n{}".format(where, traceback.format_exc()))


# ---------------------------------------------------------------- handlers

class _Created(adsk.core.CommandCreatedEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        ctrl = owner.ctrl
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            # Started without the panel (e.g. Fusion repeating the last command): use the open step.
            if model.find_step(ctrl.load(), owner.step_id)[1] is None and ctrl.scene.step_id:
                owner.step_id = ctrl.scene.step_id
            owner.session = getattr(owner, "session", 0) + 1     # (see _Destroy)
            manual, step = owner._step()
            if step is None:
                ctrl.ui.messageBox("Open a step in the BuildBook panel first.")
                return
            owner._reset_session()
            owner.command = cmd
            design = ctrl.design()

            existing = model.find_explode(step, owner.explode_id) if owner.explode_id else None
            if existing is not None:
                owner.working = copy.deepcopy(existing)
                title = "Editing <b>{}</b>".format(model.explode_label(step, existing))
            else:
                last = manual["settings"].get("lastExplode") or {}
                owner.working = model.new_explode(last.get("direction"), last.get("spacing", model.UNIFORM))
                owner.explode_id = None
                title = "New explode move"
            index = refs.path_index(design)
            for part in owner.working["parts"]:
                occ, _ = refs.resolve(design, part["ref"], index)
                if occ is not None:
                    owner.pick_path(occ.fullPathName, occ)
            for occ in owner.preselect:
                owner.pick_path(occ.fullPathName, occ)
            direction = owner.working["direction"]

            inputs = cmd.commandInputs
            owner.inputs = inputs
            hint = inputs.addTextBoxCommandInput(
                "hint", "", "{} in step <b>{}</b>".format(title, step["title"]), 1, True)
            inputs.addStringValueInput("name", "Name", owner.working.get("name", ""))
            level = inputs.addDropDownCommandInput(
                "pickLevel", "Click picks", adsk.core.DropDownStyles.TextListDropDownStyle)
            for name in refs.PICK_LEVELS:
                level.listItems.add(name, name == getattr(ctrl, "pick_level", refs.PICK_WHOLE))
            level.tooltip = refs.PICK_TIP

            parts = inputs.addSelectionInput("parts", "Parts", "Parts in this move (click moved copies too)")
            # Whole parts / assemblies only (as Fusion reports a click); a click on a split component
            # is narrowed to the body under the cursor in _Select (refs.split_body_at).
            parts.addSelectionFilter("Occurrences")
            parts.addSelectionFilter(adsk.core.SelectionCommandInput.CustomGraphics)
            parts.setSelectionLimits(0, 0)
            # Hidden: the box couldn't show moved parts (selections made in a preview are undone
            # with it) and pressing on a selected copy started Fusion's own drag, which snapped
            # the part back. Clicks are found from the mouse (click_at); the count is in the status.
            parts.isVisible = False
            # Direction: one icon button per choice (the one in use is highlighted).
            axis = inputs.addButtonRowCommandInput("axis", "Direction", False)
            kind = direction.get("kind", model.DIR_AXIS)
            chosen = model.axis_letter(direction) if kind == model.DIR_AXIS else (XYZ if kind == model.DIR_XYZ else PICKED)
            icon_for = {"X": "axis_x", "Y": "axis_y", "Z": "axis_z", XYZ: "axis_xyz", PICKED: "axis_picked"}
            for name in AXES + [XYZ, PICKED]:
                axis.listItems.add(name, name == chosen, os.path.join(paths.ADDIN_DIR, "resources", icon_for[name]))
            axis.tooltip = ("X / Y / Z: along that axis (a negative distance goes the other way). "
                            "X/Y/Z: separate amounts. Picked: along an edge, face or axis you pick.")

            pick = inputs.addSelectionInput("dirEntity", "Along", "An edge, face, axis or plane to move along")
            pick.isVisible = kind == model.DIR_ENTITY
            for f in ("LinearEdges", "PlanarFaces", "SketchLines", "ConstructionLines", "ConstructionPlanes"):
                pick.addSelectionFilter(f)
            pick.setSelectionLimits(0, 1)
            # A move along an edge / face keeps its saved direction: selecting the edge again
            # showed the part it's on, even when that part is hidden in this step's view.
            owner.saved_along = kind == model.DIR_ENTITY and bool(direction.get("vector"))
            repick = inputs.addBoolValueInput("repickAlong", "", False, "", False)
            repick.text = "Pick a different edge or face"
            repick.isFullWidth = True
            repick.isVisible = owner.saved_along
            repick.tooltip = "This move goes along the edge / face it was made with. Click to choose another."

            units = design.unitsManager.defaultLengthUnits
            vector = direction.get("vector") if kind == model.DIR_XYZ else None
            for i, label, value in zip(XYZ_INPUTS, ("X", "Y", "Z"), vector or (0.0, 0.0, 0.0)):
                field = inputs.addValueInput(i, label, units, adsk.core.ValueInput.createByReal(value))
                field.tooltip = "Signed distance along {}. All three move together in one straight line.".format(label)
            default = model.step_distance(manual, step)
            own = owner.working.get("distance")
            sign = -1.0 if model.is_negative(direction) else 1.0      # minus = the other way
            dist = inputs.addDistanceValueCommandInput(
                "distance", "Distance", adsk.core.ValueInput.createByReal(sign * (own if own is not None else default)))
            dist.tooltip = ("Drag the arrow or type. Negative goes the other way along the axis. "
                            "Changing it gives this move its own distance.")
            flip_btn = inputs.addBoolValueInput("flipDistance", "Flip direction", False,
                                                os.path.join(paths.ADDIN_DIR, "resources", "flip"), False)
            flip_btn.tooltip = "The other way along the axis (makes the distance negative, or positive again)"

            # Spacing: one icon button per choice, like the direction.
            spacing = inputs.addButtonRowCommandInput("spacing", "Spacing", False)
            for key, label, icon in SPACING_LABELS:
                spacing.listItems.add(label, key == owner.working.get("spacing", model.UNIFORM),
                                      os.path.join(paths.ADDIN_DIR, "resources", icon))
            spacing.tooltip = "How the parts' distances relate (hover a button for what it does)."
            trails = [p.get("trail", True) for p in owner.working["parts"]]
            trail = inputs.addBoolValueInput("trail", "Trail lines", True, "", all(trails) if trails else True)
            trail.tooltip = "Applies to every part in this move. Per-part lines: open the move in the panel, or Edit lines."

            nxt = inputs.addBoolValueInput("nextNew", "", False,
                                           os.path.join(paths.ADDIN_DIR, "resources", "next_new"), False)
            nxt.text = "+ Add new move"
            nxt.isFullWidth = True
            nxt.tooltip = ("Save this move and start a new one right after it, with no parts picked. "
                           "Both moves are kept when you press OK.")

            inputs.addTextBoxCommandInput("status", "", "", 2, True)
            # Hidden: flipped to make Fusion run a preview (one asked for from a mouse event doesn't).
            nudge = inputs.addBoolValueInput("nudge", "", True, "", False)
            nudge.isVisible = False

            ctrl.scene.sticky = True
            owner.refresh()

            # mouseMove: the copy under the cursor draws highlighted (hover_copy redraws only when
            # that changes; the status text isn't touched, which re-ran the preview on every move).
            # Clicks on copies come from press + release (_MouseDown / _MouseUp): Fusion's own
            # mouseClick doesn't fire if the mouse moved a hair, which made copies "sometimes" clickable.
            for event, cls in ((cmd.activate, _Activate), (cmd.mouseUp, _MouseUp), (cmd.mouseDown, _MouseDown),
                               (cmd.mouseMove, _MouseMove), (cmd.keyDown, _KeyDown),
                               (cmd.preSelect, _PreSelect), (cmd.select, _Select), (cmd.unselect, _Unselect),
                               (cmd.inputChanged, _InputChanged), (cmd.executePreview, _Preview),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy),
                               (cmd.validateInputs, _Validate)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
            log.info("explode: {} in {}".format("edit " + owner.explode_id if owner.explode_id else "new move",
                                               step["title"]))
        except Exception:
            _fail(ctrl, "Opening Explode Move")


class _MouseClick(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.MouseEventArgs.cast(args)
            if args.button == adsk.core.MouseButtons.LeftMouseButton:
                self.owner.click_copy(args.viewportPosition)
        except Exception:
            log.error("explode click")


class _MouseMove(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.hover_copy(adsk.core.MouseEventArgs.cast(args).viewportPosition)
        except Exception:
            log.error("explode hover")


class _KeyDown(adsk.core.KeyboardEventHandler):
    """While picking a direction: H hides the part under the cursor, U brings the last one back."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.KeyboardEventArgs.cast(args)
            if args.keyCode == adsk.core.KeyCodes.HKeyCode:
                self.owner.hide_hovered_for_direction()
            elif args.keyCode == adsk.core.KeyCodes.UKeyCode:
                self.owner.unhide_for_direction()
        except Exception:
            log.error("explode key")


class _MouseDown(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.MouseEventArgs.cast(args)
            self.owner.mouse_down_at = time.perf_counter()
            p = args.viewportPosition
            self.owner.mouse_down_pos = (p.x, p.y) if args.button == adsk.core.MouseButtons.LeftMouseButton else None
        except Exception:
            log.error("explode mouse down")


class _AfterDrag(adsk.core.CustomEventHandler):
    """After a drag has been finished by Fusion: refill the selection box (through a preview)."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            if owner.command is None:
                return
            owner.dragging_until = 0.0
            try:
                dist = owner._input(adsk.core.DistanceValueCommandInput, "distance").value
                log.info("explode: after the drag, distance {:.3f} cm; redrawing".format(dist))
            except Exception:
                pass
            owner.redraw()
        except Exception:
            log.error("explode after drag")


class _MouseUp(adsk.core.MouseEventHandler):
    """A drag of the arrow ended: redraw once more, which brings the selection box up to date."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            owner = self.owner
            args = adsk.core.MouseEventArgs.cast(args)
            down, p = owner.mouse_down_pos, args.viewportPosition
            owner.mouse_down_pos = None
            if (not owner.dragged and down is not None and args.button == adsk.core.MouseButtons.LeftMouseButton
                    and abs(p.x - down[0]) + abs(p.y - down[1]) <= 6
                    and time.perf_counter() - owner.mouse_down_at < 1.0):
                owner.click_at(p)               # a click (not a drag): toggle the part / copy under it
                return
            if owner.dragged:
                # The selection box can only be refilled from a preview. Not from here: changing an
                # input while Fusion is still finishing the drag cancelled it (the part snapped
                # back), so a moment later, through a custom event.
                owner.dragged = False
                try:
                    dist = owner._input(adsk.core.DistanceValueCommandInput, "distance").value
                    log.info("explode: drag ended at distance {:.3f} cm".format(dist))
                except Exception:
                    pass
                adsk.core.Application.get().fireCustomEvent(AFTER_DRAG_EVENT, "")
        except Exception:
            log.error("explode mouse up")


class _Activate(adsk.core.CommandEventHandler):
    """The dialog is up: set the arrow again (one set while the command was being created
    doesn't show until an input changes)."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.arrow_place = None       # place it now that the dialog shows it
            self.owner.refresh(preview=False)
            if self.owner.command is not None:
                self.owner.command.doExecutePreview()
        except Exception:
            log.error("explode activate")


class _PreSelect(adsk.core.SelectionEventHandler):
    """Only real parts and single drawn part copies may highlight, never the whole overlay."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            if args.activeInput is not None and args.activeInput.id == "dirEntity":
                if adsk.core.SelectionCommandInput.cast(args.activeInput).selectionCount or self.owner.saved_along:
                    # Already has its direction: clicks now pick parts, so Fusion mustn't swap
                    # the edge for whatever was clicked (clear the Along box to pick another).
                    args.isSelectable = False
                    return
                ctx = getattr(args.selection.entity, "assemblyContext", None)
                self.owner.dir_hover = adsk.fusion.Occurrence.cast(ctx) if ctx is not None else None
                return
            if args.activeInput is None or args.activeInput.id != "parts":
                return
            entity = args.selection.entity
            kind = getattr(entity, "objectType", "?")
            copy_of = self.owner.ctrl.scene.pickable_path(entity) if "CustomGraphics" in kind else None
            if "CustomGraphics" in kind:
                key = (kind, copy_of)
                if key != self.owner.last_unpickable:
                    self.owner.last_unpickable = key
                    log.info("explode: hovering drawing {} (id {!r}) -> {}".format(
                        kind, getattr(entity, "id", ""), "copy of " + copy_of if copy_of else "not a part copy"))
            if adsk.fusion.Occurrence.cast(entity) is None and copy_of is None:
                args.isSelectable = False
        except Exception:
            log.error("explode preselect")


class _Select(adsk.core.SelectionEventHandler):
    """A click on a part toggles it in the move. Fusion never puts anything in the Parts box
    itself: the box is refilled from the picked parts after each preview (sync_box), so it
    always matches them (letting Fusion add some while sync_box added others got it out of step).
    """

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            if owner.syncing:
                return
            args = adsk.core.SelectionEventArgs.cast(args)
            args.isSelectable = False
            if time.perf_counter() < owner.ignore_select_until:
                return                          # this click was on a drawn copy (click_copy did it)
            if args.activeInput is None or args.activeInput.id != "parts":
                return
            entity = args.selection.entity
            occ = adsk.fusion.Occurrence.cast(entity)
            body = adsk.fusion.BRepBody.cast(entity)
            if occ is None and body is not None:
                occ = refs.part_for_body(body)
            split_part = refs.split_body_at(occ, args.selection.point) if adsk.fusion.Occurrence.cast(occ) else None
            if split_part is not None:
                path, part = split_part.fullPathName, split_part   # a split component: that body
            elif occ is not None:
                _, step = owner._step()
                known = set(owner.picked) | (set(model.item_paths(step)) if step is not None else set())
                unit = refs.pick_unit(occ, getattr(owner.ctrl, "pick_level", refs.PICK_WHOLE), known)
                path, part = unit.fullPathName, unit
            else:
                # A drawn copy: its click was handled already, from the mouse event (click_copy).
                # Fusion reports the same click as a selection too, sometimes after the redraw,
                # which toggled the part straight back.
                return
            if path in owner.picked:
                del owner.picked[path]
                log.info("explode: clicked {} -> taken out of the move".format(path))
            else:
                owner.pick_path(path, part)
                log.info("explode: clicked {} -> added to the move".format(path))
            owner.redraw()                      # the preview refills the box from the picks
        except Exception:
            log.error("explode select")


class _Unselect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            if owner.syncing:
                return
            args = adsk.core.SelectionEventArgs.cast(args)
            occ = adsk.fusion.Occurrence.cast(args.selection.entity)
            body = adsk.fusion.BRepBody.cast(args.selection.entity)
            if occ is None and body is not None:
                occ = refs.part_for_body(body)
            if occ is not None and occ.fullPathName in owner.picked:
                del owner.picked[occ.fullPathName]
                log.info("explode: {} unselected -> taken out of the move".format(occ.fullPathName))
                owner.redraw()
                return
            leaf = owner.ctrl.scene.pickable_path(args.selection.entity)
            if leaf is not None and time.perf_counter() - owner.mouse_down_at < 1.0:
                return      # a click on the copy in the canvas: click_copy toggles it (only once)
            if leaf is not None:
                path = owner.path_for_graphics(leaf)
                if path in owner.picked:
                    del owner.picked[path]
                    log.info("explode: copy of {} unselected -> taken out of the move".format(path))
                    owner.redraw()
        except Exception:
            log.error("explode unselect")


def _ctrl_backspace(box):
    """Ctrl+Backspace in a dialog text box: Fusion's box types a DEL character (a little square)
    instead of deleting the word before the cursor. Do the word delete it meant."""
    value = box.value
    if "\x7f" not in value:
        return
    at = value.index("\x7f")
    before = re.sub(r"\S*\s*$", "", value[:at])          # the word before (and the spaces after it)
    box.value = before + value[at + 1:].replace("\x7f", "")
    log.info("explode: ctrl+backspace in the name -> {!r}".format(box.value))


class _InputChanged(adsk.core.InputChangedEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.InputChangedEventArgs.cast(args)
            changed = args.input
            cid = changed.id
            if owner.syncing and cid == "parts":
                return                      # sync_box refilling the box (re-setting the arrow would stop a drag)
            if cid == "name":
                _ctrl_backspace(adsk.core.StringValueCommandInput.cast(changed))
                return
            if cid == "nextNew":
                owner.next_move()
                return
            if cid == "pickLevel":
                owner.ctrl.pick_level = adsk.core.DropDownCommandInput.cast(changed).selectedItem.name
                return
            if cid == "dirEntity":
                pick = adsk.core.SelectionCommandInput.cast(changed)
                axis = owner._input(adsk.core.ButtonRowCommandInput, "axis")
                if pick.selectionCount:
                    for i in range(axis.listItems.count):
                        if axis.listItems.item(i).name == PICKED:
                            axis.listItems.item(i).isSelected = True
            elif cid == "repickAlong":
                owner.use_new_along()
            elif cid == "axis":
                pick = owner._input(adsk.core.SelectionCommandInput, "dirEntity")
                if changed.selectedItem.name != PICKED and owner.saved_along:
                    owner.saved_along = False
                    owner._input(adsk.core.BoolValueCommandInput, "repickAlong").isVisible = False
                if changed.selectedItem.name == PICKED:
                    pick.isVisible = True       # (shown before it can take the focus)
                    pick.hasFocus = True
                elif pick.selectionCount:
                    pick.clearSelection()
            elif cid == "distance":
                owner.dragging_until = time.perf_counter() + 0.6
                owner.dragged = True
                owner.distance_touched = True   # dragged / typed: this move has its own distance now
            elif cid == "flipDistance":
                dist = owner._input(adsk.core.DistanceValueCommandInput, "distance")
                dist.value = -dist.value
            elif cid == "trail":
                owner.trail_touched = True
            owner.refresh(preview=False)
        except Exception:
            log.error("explode inputChanged")


class _Validate(adsk.core.ValidateInputsEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        args = adsk.core.ValidateInputsEventArgs.cast(args)
        try:
            args.areInputsValid = True      # (no parts: OK saves nothing; previews keep running)
        except Exception:
            args.areInputsValid = False


class _Preview(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.CommandEventArgs.cast(args)
            self.owner.refresh(preview=True)
            # Keep execute() running: the result is written to the manual, not the model.
            args.isValidResult = False
        except Exception:
            log.error("explode preview")


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            if not owner.picked and not owner.pending:
                log.info("explode: nothing picked, nothing saved")
                return
            count = owner.save()
            log.info("explode: saved {} move(s) in step {}".format(count, owner.step_id))
        except Exception:
            _fail(owner.ctrl, "Explode Move")


class _Destroy(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.session = getattr(owner, "session", 0)

    def notify(self, args):
        owner = self.owner
        if self.session != getattr(owner, "session", 0):
            return      # an earlier run closing after a new one started: leave the new one's state alone
        try:
            owner._reset_session()
            owner.preselect = []
            owner.ctrl.scene.end_edit()
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("explode destroy")
