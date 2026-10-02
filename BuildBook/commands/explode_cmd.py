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
import traceback

import adsk.core
import adsk.fusion

from ..lib import explode, log, model, refs

CMD_ID = "buildBookExplode"
CMD_NAME = "Explode Move"
CMD_TIP = "Create or edit an explode move of the current BuildBook step"

PICKED = "Picked edge / face / axis"
XYZ = "X / Y / Z amounts"
AXES = list(model.AXIS_VECTORS.keys())
SPACING_LABELS = [
    (model.UNIFORM, "Uniform (all move the same distance)"),
    (model.STACKED, "Stacked by position"),
    (model.STACKED_SELECTION, "Stacked in pick order"),
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

    def unregister(self, ui):
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

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
        return cls.cast(self.inputs.itemById(input_id))

    def axis_choice(self):
        return self._input(adsk.core.DropDownCommandInput, "axis").selectedItem.name

    def direction_from_inputs(self):
        """Direction spec for the current inputs (keeps the remembered pick if nothing is selected)."""
        old = self.working["direction"]
        flip = self._input(adsk.core.BoolValueCommandInput, "flip").value
        choice = self.axis_choice()
        if choice == XYZ:
            vec = [self._input(adsk.core.ValueCommandInput, i).value for i in XYZ_INPUTS]
            return {"kind": model.DIR_XYZ, "axis": None, "vector": vec, "token": None, "flip": flip}
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
            return model.new_direction("+Z")
        return {"kind": model.DIR_AXIS, "axis": choice, "vector": None, "token": None, "flip": flip}

    def spacing(self):
        name = self._input(adsk.core.DropDownCommandInput, "spacing").selectedItem.name
        return next((key for key, label in SPACING_LABELS if label == name), model.UNIFORM)

    def follows_default(self):
        return self._input(adsk.core.BoolValueCommandInput, "followDefault").value

    def working_state(self):
        """The edited move as the inputs describe it now."""
        ex = copy.deepcopy(self.working)
        ex["name"] = self._input(adsk.core.StringValueCommandInput, "name").value.strip()
        ex["direction"] = self.direction_from_inputs()
        ex["spacing"] = self.spacing()
        if ex["direction"]["kind"] == model.DIR_XYZ or self.follows_default():
            ex["distance"] = None
        else:
            ex["distance"] = self._input(adsk.core.DistanceValueCommandInput, "distance").value
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
        self.inputs.itemById("followDefault").isVisible = not xyz
        dist = self._input(adsk.core.DistanceValueCommandInput, "distance")
        occs = [o for o in self.picked.values() if o.isValid]
        unit = model.direction_unit(self.direction_from_inputs())
        if occs and not xyz and explode.length(unit) > 0:
            center = explode.centroid(self.positions_before(tmp, tstep, occs))
            dist.isVisible = True
            dist.setManipulator(adsk.core.Point3D.create(*center), adsk.core.Vector3D.create(*unit))
        else:
            dist.isVisible = False
        self.update_status(tstep)
        if preview:
            self.ctrl.scene.show(self.ctrl.design(), tmp, self.step_id, edit=True,
                                 current=set(self.picked), upto=self.working["id"])
            self.ctrl.app.activeViewport.refresh()

    def update_status(self, tstep):
        status = self._input(adsk.core.TextBoxCommandInput, "status")
        position = next(i for i, ex in enumerate(tstep["explodes"]) if ex["id"] == self.working["id"]) + 1
        text = "Move <b>{}</b> of {} &nbsp;·&nbsp; <b>{}</b> part(s) picked".format(
            position, len(tstep["explodes"]), len(self.picked))
        if self.pending:
            text += " &nbsp;·&nbsp; {} more move(s) ready to save".format(len(self.pending))
        text += "<br>Click parts (moved ones too) to pick them."
        status.formattedText = text

    # ------------------------------------------------------------ actions

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

    def next_move(self, same_parts):
        """Keep this move and start a new one right after it."""
        done = self.working_state()
        if not done["parts"]:
            return
        self.pending.append(done)
        self.working = model.new_explode(done["direction"], done["spacing"])
        self.trail_touched = False
        self._input(adsk.core.StringValueCommandInput, "name").value = ""
        if not same_parts:
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

            parts = inputs.addSelectionInput("parts", "Parts", "Parts in this move (click moved copies too)")
            # Whole parts / assemblies only (as Fusion reports a click); a click on a split component
            # is narrowed to the body under the cursor in _Select (refs.split_body_at).
            parts.addSelectionFilter("Occurrences")
            parts.addSelectionFilter(adsk.core.SelectionCommandInput.CustomGraphics)
            parts.setSelectionLimits(0, 0)

            axis = inputs.addDropDownCommandInput(
                "axis", "Direction", adsk.core.DropDownStyles.TextListDropDownStyle)
            kind = direction.get("kind", model.DIR_AXIS)
            chosen = direction.get("axis") if kind == model.DIR_AXIS else (XYZ if kind == model.DIR_XYZ else PICKED)
            for name in AXES + [XYZ, PICKED]:
                axis.listItems.add(name, name == chosen)

            pick = inputs.addSelectionInput("dirEntity", "Along", "An edge, face, axis or plane to move along")
            for f in ("LinearEdges", "PlanarFaces", "SketchLines", "ConstructionLines", "ConstructionPlanes"):
                pick.addSelectionFilter(f)
            pick.setSelectionLimits(0, 1)
            if kind == model.DIR_ENTITY:
                restored = False
                if direction.get("token"):
                    try:
                        for ent in design.findEntityByToken(direction["token"]):
                            if _entity_direction(ent) is not None:
                                pick.addSelection(ent)
                                restored = True
                                break
                    except Exception:
                        pass
                if not restored:
                    hint.formattedText += "<br><i>Using the saved picked direction.</i>"
                    hint.numRows = 2

            units = design.unitsManager.defaultLengthUnits
            vector = direction.get("vector") if kind == model.DIR_XYZ else None
            for i, label, value in zip(XYZ_INPUTS, ("X", "Y", "Z"), vector or (0.0, 0.0, 0.0)):
                field = inputs.addValueInput(i, label, units, adsk.core.ValueInput.createByReal(value))
                field.tooltip = "Signed distance along {}. All three move together in one straight line.".format(label)
            inputs.addBoolValueInput("flip", "Flip direction", True, "", bool(direction.get("flip")))

            default = model.step_distance(manual, step)
            own = owner.working.get("distance")
            dist = inputs.addDistanceValueCommandInput(
                "distance", "Distance", adsk.core.ValueInput.createByReal(own if own is not None else default))
            dist.tooltip = "Drag the arrow or type. Changing it gives this move its own distance."
            follow = inputs.addBoolValueInput("followDefault", "Follow step default", True, "", own is None)
            follow.tooltip = "Use the step's default distance (and move if the default changes later)."

            spacing = inputs.addDropDownCommandInput(
                "spacing", "Spacing", adsk.core.DropDownStyles.TextListDropDownStyle)
            for key, label in SPACING_LABELS:
                spacing.listItems.add(label, key == owner.working.get("spacing", model.UNIFORM))

            trails = [p.get("trail", True) for p in owner.working["parts"]]
            trail = inputs.addBoolValueInput("trail", "Trail lines", True, "", all(trails) if trails else True)
            trail.tooltip = "Applies to every part in this move. Per-part lines: expand the move in the panel, or Edit lines."

            chain = inputs.addBoolValueInput("nextSame", "Next move: same parts", False, "", False)
            chain.tooltip = "Keep this move and start another right after it with the same parts, from where they land."
            nxt = inputs.addBoolValueInput("nextNew", "Next move: new parts", False, "", False)
            nxt.tooltip = "Keep this move and start another right after it with different parts."
            inputs.addTextBoxCommandInput("status", "", "", 2, True)

            for occ in owner.picked.values():
                try:
                    parts.addSelection(occ)
                except Exception:
                    pass   # moved parts are hidden at home; they're picked all the same

            ctrl.scene.sticky = True
            owner.refresh()
            parts.hasFocus = True

            for event, cls in ((cmd.preSelect, _PreSelect), (cmd.select, _Select), (cmd.unselect, _Unselect),
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


class _PreSelect(adsk.core.SelectionEventHandler):
    """Only real parts and single drawn part copies may highlight, never the whole overlay."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            if args.activeInput is None or args.activeInput.id != "parts":
                return
            entity = args.selection.entity
            if adsk.fusion.Occurrence.cast(entity) is None and self.owner.ctrl.scene.pickable_path(entity) is None:
                args.isSelectable = False
        except Exception:
            log.error("explode preselect")


class _Select(adsk.core.SelectionEventHandler):
    """Real parts join the pick; a click on a drawn (moved) copy toggles its part."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            if args.activeInput is None or args.activeInput.id != "parts":
                return
            entity = args.selection.entity
            occ = adsk.fusion.Occurrence.cast(entity)
            body = adsk.fusion.BRepBody.cast(entity)
            if occ is None and body is not None:
                occ = refs.part_for_body(body)
            split_part = refs.split_body_at(occ, args.selection.point) if adsk.fusion.Occurrence.cast(occ) else None
            if split_part is not None:
                # A click on a split component (even through its assembly): that body is the part.
                path = split_part.fullPathName
                if path in owner.picked:
                    del owner.picked[path]
                else:
                    owner.pick_path(path, split_part)
                args.isSelectable = False       # the box can't hold a body stand-in; the preview shows it
            elif occ is not None:
                owner.pick_path(occ.fullPathName, occ)
            else:
                leaf = owner.ctrl.scene.pickable_path(entity)
                if leaf is None:
                    args.isSelectable = False
                    return
                path = owner.path_for_graphics(leaf)
                if path in owner.picked:
                    del owner.picked[path]
                else:
                    owner.pick_path(path)
                args.isSelectable = False   # drawn copies are redrawn, so they can't stay in the box
            owner.command.doExecutePreview()
        except Exception:
            log.error("explode select")


class _Unselect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            occ = adsk.fusion.Occurrence.cast(args.selection.entity)
            body = adsk.fusion.BRepBody.cast(args.selection.entity)
            if occ is None and body is not None:
                occ = refs.part_for_body(body)
            if occ is not None and occ.fullPathName in owner.picked:
                del owner.picked[occ.fullPathName]
                owner.command.doExecutePreview()
        except Exception:
            log.error("explode unselect")


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
            if cid in ("nextSame", "nextNew"):
                owner.next_move(same_parts=cid == "nextSame")
                return
            if cid == "dirEntity":
                pick = adsk.core.SelectionCommandInput.cast(changed)
                axis = owner._input(adsk.core.DropDownCommandInput, "axis")
                if pick.selectionCount:
                    for i in range(axis.listItems.count):
                        if axis.listItems.item(i).name == PICKED:
                            axis.listItems.item(i).isSelected = True
            elif cid == "axis":
                pick = owner._input(adsk.core.SelectionCommandInput, "dirEntity")
                if changed.selectedItem.name == PICKED:
                    pick.hasFocus = True
                elif pick.selectionCount:
                    pick.clearSelection()
            elif cid == "distance":
                # Dragging or typing a distance means this move has its own.
                owner._input(adsk.core.BoolValueCommandInput, "followDefault").value = False
            elif cid == "followDefault" and changed.value:
                manual, step = owner._step()
                owner._input(adsk.core.DistanceValueCommandInput, "distance").value = model.step_distance(manual, step)
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
            args.areInputsValid = bool(self.owner.picked or self.owner.pending)
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
