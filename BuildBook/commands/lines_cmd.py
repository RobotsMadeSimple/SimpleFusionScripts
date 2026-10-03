"""Edit Lines command: click trail lines to hide them.

Each trail segment is one part's leg of one explode move (see lib/model.py),
drawn as its own custom-graphics line. Clicking a shown line hides it; hidden
lines draw faint and dotted and can't be clicked, so where lines overlap a
click always lands on a shown one (click again to hide the next). To bring
lines back: U undoes the last click, "Show all lines" restores every line,
and "Click hidden lines to show them" makes the faint ones clickable.
Changes are kept in memory and written on OK; Cancel leaves the manual
untouched.

Clicks and hover are read from the command's own mouse events and matched to the
nearest line on screen (within PICK_PX pixels); the lines are not selectable in
Fusion. Every change of look is applied in executePreview (from the clicks, hover
and pending changes kept here): Fusion undoes changes made to the drawing from
other command events, so restyling there didn't stick. (Using Fusion's selection made a clicked line snap back to its old look:
Fusion restores the look an entity had when its hover / selection began.)

Older notes, kept for the record: restyling happened a moment after the click (through a custom event): during
the click Fusion treats the line as selected, and when that selection ends
it puts back the look the line had before the click, undoing a restyle made
inside the select event. Fusion does the same when the mouse leaves a line it
highlighted on hover (it restores the look from when the hover began), so a
changed line is restyled again once the hover ends.
"""

import time
import traceback

import adsk.core
import adsk.fusion

from ..lib import log, model
from ..lib.scene import parse_segment_id

CMD_ID = "buildBookLines"
CMD_NAME = "Edit Trail Lines"
CMD_TIP = "Click trail lines to hide them"
PICK_PX = 10                    # how close (screen pixels) a click must be to a line
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
        self.last_click = (None, 0.0)   # (segment id, time): one click reported twice counts once
        self.hovered = None      # segment drawn highlighted under the cursor
        self.command = None

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

    def click(self, seg_id):
        """Record the change now; restyle after Fusion has finished with the click."""
        now = time.perf_counter()
        last_id, last_at = self.last_click
        if seg_id == last_id and now - last_at < 0.5:
            # Fusion can report one click twice (e.g. on the line drawn again under the cursor):
            # a second toggle would show the line straight back.
            log.info("lines: second click on {} {:.0f} ms after the first, ignored".format(
                seg_id, (now - last_at) * 1000))
            return
        self.last_click = (seg_id, now)
        log.info("lines: click {} -> {}".format(seg_id, "hidden" if self.is_on(seg_id) else "shown"))
        self.history.append((seg_id, self.is_on(seg_id)))
        self.pending[seg_id] = not self.is_on(seg_id)
        self.restyle.add(seg_id)
        adsk.core.Application.get().fireCustomEvent(RESTYLE_EVENT)

    def nearest(self, pos):
        """The trail line nearest a viewport position (Point2D, pixels), within PICK_PX: shown
        lines, plus hidden ones when "Click hidden lines to show them" is on."""
        scene = self.ctrl.scene
        viewport = self.ctrl.app.activeViewport
        best, best_d = None, PICK_PX
        for seg_id, seg in scene.segments.items():
            if not self.is_on(seg_id) and not scene.pick_hidden:
                continue
            try:
                a = viewport.modelToViewSpace(adsk.core.Point3D.create(*seg["start"]))
                b = viewport.modelToViewSpace(adsk.core.Point3D.create(*seg["end"]))
            except Exception:
                continue
            d = _point_segment_distance(pos.x, pos.y, a.x, a.y, b.x, b.y)
            if d < best_d:
                best, best_d = seg_id, d
        return best

    def mouse_click(self, pos):
        seg_id = self.nearest(pos)
        if seg_id is None:
            return
        on = not self.is_on(seg_id)
        self.history.append((seg_id, self.is_on(seg_id)))
        self.pending[seg_id] = on
        log.info("lines: click {} -> {}".format(seg_id, "shown" if on else "hidden"))
        self.hovered = None             # shown in its new look; the next move highlights again
        self.redraw()

    def mouse_move(self, pos):
        seg_id = self.nearest(pos)
        if seg_id == self.hovered:
            return
        self.hovered = seg_id
        label = None
        if seg_id is not None:
            _, step = model.find_step(self.ctrl.load(), self.step_id)
            label = _segment_label(seg_id, step)
        self.update_status(label)
        self.redraw()

    def redraw(self):
        """Ask Fusion for a preview, where every line gets its look (see apply_looks)."""
        if self.command is not None:
            self.command.doExecutePreview()

    def apply_looks(self):
        """Called from executePreview: each line as it should look now."""
        scene = self.ctrl.scene
        for seg_id in scene.segments:
            state = "hover" if seg_id == self.hovered else ("on" if self.is_on(seg_id) else "off")
            scene.style_segment(seg_id, state)
        self.ctrl.app.activeViewport.refresh()

    def apply_restyle(self):
        # Clear the selection first: ending a selection makes Fusion put the line's old look
        # back, so restyling has to come after it.
        if self.inputs is not None:
            sel = adsk.core.SelectionCommandInput.cast(self.inputs.itemById("lines"))
            if sel is not None and sel.selectionCount:
                sel.clearSelection()
        for seg_id in list(self.restyle):
            # A fresh line, not a restyle: Fusion would put the clicked line's old look back.
            self.ctrl.scene.replace_segment(seg_id, "on" if self.is_on(seg_id) else "off")
        self.restyle.clear()
        self.refresh()

    def hover_ended(self, seg_id):
        """Fusion just put back the look a line had when the hover began: if the user changed
        it meanwhile, restyle it (after Fusion is done, through the custom event)."""
        return      # (replace_segment draws a new line, which Fusion's hover reset can't touch)

    def undo(self):
        if not self.history:
            return
        seg_id, on = self.history.pop()
        self.set_on(seg_id, on, record=False)
        self.update_status()
        self.redraw()

    def show_all(self):
        for seg_id in self.ctrl.scene.segments:
            if not self.is_on(seg_id):
                self.set_on(seg_id, True)
        self.update_status()
        self.redraw()

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
            # Started without the panel (e.g. Fusion repeating the last command): use the open step.
            if model.find_step(ctrl.load(), owner.step_id)[1] is None and ctrl.scene.step_id:
                owner.step_id = ctrl.scene.step_id
            owner.session = getattr(owner, "session", 0) + 1     # (see _Destroy)
            _, step = model.find_step(ctrl.load(), owner.step_id)
            if step is None:
                ctrl.ui.messageBox("Open a step in the BuildBook panel first.")
                return
            owner._reset_session()
            owner.command = cmd
            ctrl.scene.start_lines_edit()
            ctrl.app.activeViewport.refresh()

            inputs = cmd.commandInputs
            owner.inputs = inputs
            inputs.addTextBoxCommandInput(
                "hint", "",
                "Step: <b>{}</b><br><b>Click</b> a trail line to hide it (overlapping lines: click again "
                "for the next). <b>U</b> undoes. Hidden lines show faint and dotted.".format(step["title"]),
                3, True)
            pick_hidden = inputs.addBoolValueInput("pickHidden", "Click hidden lines to show them", True, "", False)
            pick_hidden.tooltip = "Make the faint hidden lines clickable, so a click brings one back."
            inputs.addBoolValueInput("showAll", "Show all lines", False, "", False)
            inputs.addTextBoxCommandInput("status", "", "", 2, True)
            owner.update_status()

            for event, cls in ((cmd.mouseClick, _MouseClick), (cmd.mouseMove, _MouseMove),
                               (cmd.executePreview, _Preview),
                               (cmd.keyDown, _KeyDown), (cmd.inputChanged, _InputChanged),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
        except Exception:
            _fail(ctrl, "Opening Edit Lines")


def _point_segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


class _Preview(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.apply_looks()
            # Not a result: OK still runs execute(), which writes the changes to the manual.
            adsk.core.CommandEventArgs.cast(args).isValidResult = False
        except Exception:
            log.error("lines preview")


class _MouseClick(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.MouseEventArgs.cast(args)
            if args.button == adsk.core.MouseButtons.LeftMouseButton:
                self.owner.mouse_click(args.viewportPosition)
        except Exception:
            log.error("lines click")


class _MouseMove(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.mouse_move(adsk.core.MouseEventArgs.cast(args).viewportPosition)
        except Exception:
            log.error("lines hover")


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


class _PreSelectEnd(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            seg_id = _segment_of(adsk.core.SelectionEventArgs.cast(args))
            if seg_id:
                self.owner.hover_ended(seg_id)
        except Exception:
            log.error("lines preselect end")


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
        self.session = getattr(owner, "session", 0)

    def notify(self, args):
        owner = self.owner
        if self.session != getattr(owner, "session", 0):
            return      # an earlier run closing after a new one started: leave the new one's state alone
        try:
            owner.inputs = None
            owner.command = None
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("lines destroy")
