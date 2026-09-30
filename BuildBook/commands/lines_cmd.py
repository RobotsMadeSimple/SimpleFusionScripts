"""Edit Lines command: click trail lines to hide them.

Each trail segment is one part's leg of one explode move (see lib/model.py),
drawn as its own custom-graphics line. Clicking a shown line hides it; hidden
lines draw faint and dotted and can't be clicked, so where lines overlap a
click always lands on a shown one (click again to hide the next). To bring
lines back: U undoes the last click, "Show all lines" restores every line,
and "Click hidden lines to show them" makes the faint ones clickable.
Changes are kept in memory and written on OK; Cancel leaves the manual
untouched.

Restyling happens a moment after the click (through a custom event): during
the click Fusion treats the line as selected, and when that selection ends
it puts back the look the line had before the click, undoing a restyle made
inside the select event.
"""

import traceback

import adsk.core
import adsk.fusion

from ..lib import log, model
from ..lib.scene import parse_segment_id

CMD_ID = "buildBookLines"
CMD_NAME = "Edit Trail Lines"
CMD_TIP = "Click trail lines to hide them"
RESTYLE_EVENT = "buildBookLinesRestyle"


class LinesCommand:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.step_id = None
        self._handlers = []
        self._reset_session()

    def _reset_session(self):
        self.pending = {}        # segment id -> on (only segments the user changed)
        self.history = []        # [(segment id, previous on)], for U
        self.inputs = None
        self.restyle = set()     # segment ids to restyle once the click is over

    def register(self, ui):
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TIP)
        handler = _Created(self)
        cmd_def.commandCreated.add(handler)
        self._handlers.append(handler)
        app = adsk.core.Application.get()
        try:
            app.unregisterCustomEvent(RESTYLE_EVENT)
        except Exception:
            pass
        event = app.registerCustomEvent(RESTYLE_EVENT)
        restyle = _Restyle(self)
        event.add(restyle)
        self._handlers.append(restyle)

    def unregister(self, ui):
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        try:
            adsk.core.Application.get().unregisterCustomEvent(RESTYLE_EVENT)
        except Exception:
            pass

    def launch(self, step_id):
        self.step_id = step_id
        # Show the whole step normally first; the command then makes its lines clickable.
        if self.ctrl.scene.step_id != step_id or self.ctrl.scene.edit or self.ctrl.scene.upto:
            self.ctrl.show_step(step_id)
        self.ctrl.ui.commandDefinitions.itemById(CMD_ID).execute()

    # ------------------------------------------------------------ state

    def is_on(self, seg_id):
        if seg_id in self.pending:
            return self.pending[seg_id]
        seg = self.ctrl.scene.segments.get(seg_id)
        return bool(seg and seg["on"])

    def set_on(self, seg_id, on, record=True):
        if record:
            self.history.append((seg_id, self.is_on(seg_id)))
        self.pending[seg_id] = on
        self.ctrl.scene.style_segment(seg_id, "on" if on else "off")

    def click(self, seg_id):
        """Record the change now; restyle after Fusion has finished with the click."""
        self.history.append((seg_id, self.is_on(seg_id)))
        self.pending[seg_id] = not self.is_on(seg_id)
        self.restyle.add(seg_id)
        adsk.core.Application.get().fireCustomEvent(RESTYLE_EVENT)

    def apply_restyle(self):
        for seg_id in list(self.restyle):
            self.ctrl.scene.style_segment(seg_id, "on" if self.is_on(seg_id) else "off")
        self.restyle.clear()
        if self.inputs is not None:
            sel = adsk.core.SelectionCommandInput.cast(self.inputs.itemById("lines"))
            if sel is not None:
                sel.clearSelection()
        self.refresh()

    def undo(self):
        if not self.history:
            return
        seg_id, on = self.history.pop()
        self.set_on(seg_id, on, record=False)
        self.refresh()

    def show_all(self):
        for seg_id in self.ctrl.scene.segments:
            if not self.is_on(seg_id):
                self.set_on(seg_id, True)
        self.refresh()

    def refresh(self):
        self.ctrl.app.activeViewport.refresh()
        self.update_status()

    def update_status(self, hovered_label=None):
        if self.inputs is None:
            return
        status = adsk.core.TextBoxCommandInput.cast(self.inputs.itemById("status"))
        if status is None:
            return
        segs = self.ctrl.scene.segments
        on = sum(1 for sid in segs if self.is_on(sid))
        text = "<b>{}</b> of {} lines shown".format(on, len(segs))
        changed = sum(1 for sid, v in self.pending.items() if segs.get(sid) and segs[sid]["on"] != v)
        if changed:
            text += " &nbsp;·&nbsp; {} changed".format(changed)
        if hovered_label:
            text += "<br>Under cursor: {}".format(hovered_label)
        status.formattedText = text

    def apply(self):
        manual = self.ctrl.load()
        _, step = model.find_step(manual, self.step_id)
        if step is None:
            return
        count = 0
        for seg_id, on in self.pending.items():
            path, explode_id = parse_segment_id(seg_id)
            ex = model.find_explode(step, explode_id)
            part = model.find_explode_part(ex, path) if ex is not None else None
            if part is not None and part.get("trail", True) != on:
                part["trail"] = on
                count += 1
        self.ctrl.save(manual)
        log.info("lines: {} trail lines changed in {}".format(count, step["title"]))


def _segment_label(seg_id, step=None):
    path, explode_id = parse_segment_id(seg_id)
    ex = model.find_explode(step, explode_id) if step is not None else None
    move = model.explode_label(step, ex) if ex is not None else "move"
    return "{} &nbsp;({})".format(path.split(model.PATH_SEP)[-1], move)


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
            _, step = model.find_step(ctrl.load(), owner.step_id)
            if step is None:
                ctrl.ui.messageBox("Open a step in the BuildBook panel first.")
                return
            owner._reset_session()
            ctrl.scene.start_lines_edit()
            ctrl.app.activeViewport.refresh()

            inputs = cmd.commandInputs
            owner.inputs = inputs
            inputs.addTextBoxCommandInput(
                "hint", "",
                "Step: <b>{}</b><br><b>Click</b> a trail line to hide it (overlapping lines: click again "
                "for the next). <b>U</b> undoes. Hidden lines show faint and dotted.".format(step["title"]),
                3, True)
            sel = inputs.addSelectionInput("lines", "Lines", "Click trail lines")
            sel.addSelectionFilter(adsk.core.SelectionCommandInput.CustomGraphics)
            sel.setSelectionLimits(0, 0)
            pick_hidden = inputs.addBoolValueInput("pickHidden", "Click hidden lines to show them", True, "", False)
            pick_hidden.tooltip = "Make the faint hidden lines clickable, so a click brings one back."
            inputs.addBoolValueInput("showAll", "Show all lines", False, "", False)
            inputs.addTextBoxCommandInput("status", "", "", 2, True)
            owner.update_status()

            for event, cls in ((cmd.preSelect, _PreSelect), (cmd.select, _Select),
                               (cmd.keyDown, _KeyDown), (cmd.inputChanged, _InputChanged),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
        except Exception:
            _fail(ctrl, "Opening Edit Lines")


def _segment_of(args):
    entity = adsk.fusion.CustomGraphicsLines.cast(args.selection.entity)
    return entity.id if entity is not None else None


class _PreSelect(adsk.core.SelectionEventHandler):
    """Only trail lines are clickable; the status line names the one under the cursor."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            seg_id = _segment_of(args)
            if seg_id not in owner.ctrl.scene.segments:
                args.isSelectable = False
                return
            _, step = model.find_step(owner.ctrl.load(), owner.step_id)
            owner.update_status(_segment_label(seg_id, step))
        except Exception:
            log.error("lines preselect")


class _Select(adsk.core.SelectionEventHandler):
    """A click hides (or, for a clickable hidden line, shows) it; it's never kept as a selection."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            seg_id = _segment_of(args)
            args.isSelectable = False
            if seg_id in self.owner.ctrl.scene.segments:
                self.owner.click(seg_id)
        except Exception:
            log.error("lines select")


class _Restyle(adsk.core.CustomEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            if self.owner.inputs is not None:
                self.owner.apply_restyle()
        except Exception:
            log.error("lines restyle")


class _KeyDown(adsk.core.KeyboardEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.KeyboardEventArgs.cast(args)
            if args.keyCode == adsk.core.KeyCodes.UKeyCode:
                self.owner.undo()
        except Exception:
            log.error("lines keyDown")


class _InputChanged(adsk.core.InputChangedEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.InputChangedEventArgs.cast(args)
            if args.input.id == "pickHidden":
                owner.ctrl.scene.set_pick_hidden(adsk.core.BoolValueCommandInput.cast(args.input).value)
                owner.refresh()
            elif args.input.id == "showAll":
                owner.show_all()
        except Exception:
            log.error("lines inputChanged")


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.apply()
        except Exception:
            _fail(self.owner.ctrl, "Edit Lines")


class _Destroy(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            owner.inputs = None
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("lines destroy")
