"""Pick Parts command: fast part picking for a step on the full model.

Left-click toggles a whole part (every body of it, or of its top-level
subassembly). Hover and press H to hide the part under the cursor so you can
burrow inward; U brings back the last one. Hiding is applied in the
command's executePreview (Fusion ignores visibility changes made from key
events mid-command), so Fusion's preview rollback also restores it on close. (Right-click can't be used: Fusion
clears the selection and shows its own menu, and the API can't stop either.) Hidden parts stay picked. OK makes
the step's part list exactly the picked set, keeping existing explode offsets.
Everything hidden here is shown again when the command closes.
"""

import time
import traceback

import adsk.core
import adsk.fusion

from ..lib import log, model, refs

CMD_ID = "buildBookPick"
CMD_NAME = "Pick Parts"
CMD_TIP = "Pick the parts for the current BuildBook step: left-click selects, hover + H hides"

# Preselection can end a moment before the key event arrives, so a hover
# that ended this recently still counts.
HOVER_GRACE_S = 0.35

LEVEL_PART = "Part"
LEVEL_TOP = "Top-level subassembly"


def _bodies_under(occ, visible_only=True):
    """Every body in an occurrence and its descendants (root-context proxies)."""
    out = [b for b in occ.bRepBodies if b.isVisible or not visible_only]
    for child in occ.childOccurrences:
        out.extend(_bodies_under(child, visible_only))
    return out


class PickCommand:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.step_id = None
        self.level = LEVEL_PART
        self._handlers = []
        self._reset_session()

    def _reset_session(self):
        self.picked = {}         # path -> occurrence
        self.hidden = []         # occurrences hidden here, in order (for U)
        self.hovered = None      # body proxy under the cursor
        self.hover_ended = None  # perf_counter time preselection ended, or None
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

    def launch(self, step_id):
        self.step_id = step_id
        self.ctrl.ui.commandDefinitions.itemById(CMD_ID).execute()

    # ------------------------------------------------------------ picking logic

    def occ_for_body(self, body):
        """The occurrence a click on `body` stands for at the current level."""
        occ = body.assemblyContext
        if occ is None:
            return None  # root-component body: not a part we can track
        if refs.is_split(occ) and (self.level != LEVEL_TOP or model.PATH_SEP not in occ.fullPathName):
            return refs.BodyPart(occ, body)     # a split component: each body is a part
        if self.level == LEVEL_TOP:
            top = occ.fullPathName.split(model.PATH_SEP)[0]
            return refs.path_index(self.ctrl.design()).get(top, occ)
        return occ

    def pick(self, body, additional):
        occ = self.occ_for_body(body)
        if occ is None:
            return False
        self.picked[occ.fullPathName] = occ
        for b in _bodies_under(occ):
            if b != body:
                additional.add(b)
        self.update_status()
        return True

    def unpick(self, body, additional):
        occ = body.assemblyContext
        if occ is None:
            return
        path = occ.fullPathName
        # Drop whatever picked entry covers this body: itself, an ancestor
        # (a subassembly picked earlier) or, at top level, its descendants.
        target = self.occ_for_body(body)
        drop = [p for p in self.picked
                if model.is_self_or_ancestor(p, path)
                or (target is not None and model.is_self_or_ancestor(target.fullPathName, p))]
        for p in drop:
            for b in _bodies_under(self.picked.pop(p)):
                if b != body:
                    additional.add(b)
        self.update_status()

    def refresh_preview(self):
        """Re-run executePreview, which applies `self.hidden` to the model."""
        self.update_status()
        if self.command is not None:
            self.command.doExecutePreview()

    def hide_hovered(self):
        body = self.hovered
        ended = self.hover_ended
        self.hovered = None
        if body is None or not body.isValid:
            log.info("pick: H with nothing under the cursor")
            return False
        if ended is not None and time.perf_counter() - ended > HOVER_GRACE_S:
            log.info("pick: H ignored, hover ended {:.2f}s ago".format(time.perf_counter() - ended))
            return False
        occ = self.occ_for_body(body)
        if occ is None:
            log.info("pick: H on a root-component body, can't hide")
            return False
        if any(o.fullPathName == occ.fullPathName for o in self.hidden):
            return False
        self.hidden.append(occ)
        log.info("pick: hide " + occ.fullPathName)
        self.refresh_preview()
        return True

    def unhide_last(self):
        if self.hidden:
            self.hidden.pop()
        self.refresh_preview()

    def unhide_all(self):
        self.hidden = []
        self.refresh_preview()

    def apply_hidden(self):
        """Called from executePreview: turn off every hidden occurrence."""
        for occ in self.hidden:
            try:
                if occ.isValid and occ.isLightBulbOn:
                    occ.isLightBulbOn = False
            except Exception:
                log.error("pick: hide " + occ.name)

    def restore_hidden(self):
        """Safety net on close; normally the preview rollback already did this."""
        for occ in self.hidden:
            try:
                if occ.isValid and not occ.isLightBulbOn:
                    occ.isLightBulbOn = True
            except Exception:
                log.error("pick: restore " + occ.name)
        self.hidden = []

    def hide_other_steps(self):
        manual = self.ctrl.load()
        design = self.ctrl.design()
        index = refs.path_index(design)
        for _, step in model.ordered_steps(manual):
            if step["id"] == self.step_id:
                continue
            for item in step["items"]:
                occ, _ = refs.resolve(design, item["ref"], index)
                if occ is not None and occ.fullPathName not in self.picked and occ.isVisible:
                    if not any(o.fullPathName == occ.fullPathName for o in self.hidden):
                        self.hidden.append(occ)
        self.refresh_preview()

    def update_status(self, hovered_name=None):
        if self.inputs is None:
            return
        status = adsk.core.TextBoxCommandInput.cast(self.inputs.itemById("status"))
        if status is None:
            return
        text = "<b>{}</b> picked &nbsp;·&nbsp; <b>{}</b> hidden".format(len(self.picked), len(self.hidden))
        if hovered_name:
            text += "<br>Under cursor: {}".format(hovered_name)
        status.formattedText = text

    def select_existing(self, sel_input):
        """Preselect the step's current parts so the pick edits the step."""
        manual = self.ctrl.load()
        _, step = model.find_step(manual, self.step_id)
        if step is None:
            return
        design = self.ctrl.design()
        index = refs.path_index(design)
        for item in step["items"]:
            occ, _ = refs.resolve(design, item["ref"], index)
            if occ is None:
                continue
            self.picked[occ.fullPathName] = occ
            for b in _bodies_under(occ):
                try:
                    sel_input.addSelection(b)
                except Exception:
                    pass

    def apply(self):
        """Make the step's items exactly the picked set."""
        manual = self.ctrl.load()
        _, step = model.find_step(manual, self.step_id)
        if step is None:
            return
        keep = set(self.picked)
        model.remove_items(step, [p for p in model.item_paths(step) if p not in keep])
        model.add_items(step, [refs.make_ref(o) for o in self.picked.values() if o.isValid])
        self.ctrl.save(manual)
        log.info("pick: {} now has {} parts".format(step["title"], len(step["items"])))


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
            manual = ctrl.load()
            _, step = model.find_step(manual, owner.step_id)
            if step is None:
                ctrl.ui.messageBox("Open a step in the BuildBook panel first.")
                return

            owner._reset_session()
            ctrl.scene.clear()          # full model, nothing exploded
            ctrl.app.activeViewport.refresh()

            owner.command = cmd
            inputs = cmd.commandInputs
            owner.inputs = inputs
            inputs.addTextBoxCommandInput(
                "hint", "",
                "Step: <b>{}</b><br>"
                "<b>Click</b> pick / unpick &nbsp; <b>Hover + H</b> hide "
                "&nbsp; <b>U</b> unhide last".format(step["title"]), 3, True)

            sel = inputs.addSelectionInput("parts", "Parts", "Click parts to pick them")
            sel.addSelectionFilter("Bodies")
            sel.setSelectionLimits(0, 0)

            level = inputs.addDropDownCommandInput(
                "level", "Pick level", adsk.core.DropDownStyles.TextListDropDownStyle)
            for name in (LEVEL_PART, LEVEL_TOP):
                level.listItems.add(name, name == owner.level)

            inputs.addBoolValueInput("unhideLast", "Unhide last", False, "", False)
            inputs.addBoolValueInput("unhideAll", "Show all hidden", False, "", False)
            inputs.addBoolValueInput("hideOthers", "Hide parts in other steps", False, "", False)
            inputs.addTextBoxCommandInput("status", "", "", 2, True)

            owner.select_existing(sel)
            owner.update_status()

            for event, cls in ((cmd.select, _Select), (cmd.unselect, _Unselect),
                               (cmd.preSelect, _PreSelect), (cmd.preSelectEnd, _PreSelectEnd),
                               (cmd.keyDown, _KeyDown),
                               (cmd.inputChanged, _InputChanged), (cmd.executePreview, _Preview),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
        except Exception:
            _fail(ctrl, "Opening Pick Parts")


class _Select(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            body = adsk.fusion.BRepBody.cast(args.selection.entity)
            if body is None:
                return
            extra = adsk.core.ObjectCollection.create()
            if not self.owner.pick(body, extra):
                args.isSelectable = False
                return
            if extra.count:
                args.additionalEntities = extra
        except Exception:
            log.error("pick select")


class _Unselect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            body = adsk.fusion.BRepBody.cast(args.selection.entity)
            if body is None:
                return
            extra = adsk.core.ObjectCollection.create()
            self.owner.unpick(body, extra)
            if extra.count:
                args.additionalEntities = extra
        except Exception:
            log.error("pick unselect")


class _PreSelect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            body = adsk.fusion.BRepBody.cast(args.selection.entity)
            self.owner.hovered = body
            self.owner.hover_ended = None
            occ = self.owner.occ_for_body(body) if body else None
            self.owner.update_status(occ.name if occ else None)
        except Exception:
            log.error("pick preselect")


class _PreSelectEnd(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        self.owner.hover_ended = time.perf_counter()


class _KeyDown(adsk.core.KeyboardEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.KeyboardEventArgs.cast(args)
            log.info("pick: key {}".format(args.keyCode))
            if args.keyCode == adsk.core.KeyCodes.HKeyCode:
                self.owner.hide_hovered()
            elif args.keyCode == adsk.core.KeyCodes.UKeyCode:
                self.owner.unhide_last()
        except Exception:
            log.error("pick keyDown")


class _InputChanged(adsk.core.InputChangedEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.InputChangedEventArgs.cast(args)
            cid = args.input.id
            if cid == "unhideLast":
                owner.unhide_last()
            elif cid == "unhideAll":
                owner.unhide_all()
            elif cid == "hideOthers":
                owner.hide_other_steps()
            elif cid == "level":
                owner.level = adsk.core.DropDownCommandInput.cast(args.input).selectedItem.name
        except Exception:
            log.error("pick inputChanged")


class _Preview(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.CommandEventArgs.cast(args)
            self.owner.apply_hidden()
            # Not a result: execute() still runs and writes the step, and the
            # rollback before it brings the hidden parts back.
            args.isValidResult = False
        except Exception:
            log.error("pick preview")


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.apply()
        except Exception:
            _fail(self.owner.ctrl, "Pick Parts")


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
            owner.restore_hidden()
            owner.inputs = None
            owner.command = None
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("pick destroy")
