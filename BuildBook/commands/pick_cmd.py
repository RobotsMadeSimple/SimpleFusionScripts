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

from ..lib import hidden_record, log, model, refs, scene

HIGHLIGHT_GROUP_ID = "BuildBookPickHighlight"

CMD_ID = "buildBookPick"
CMD_NAME = "Pick Parts"
CMD_TIP = "Pick the parts for the current BuildBook step: left-click selects, hover + H hides"

# Preselection can end a moment before the key event arrives, so a hover
# that ended this recently still counts.
HOVER_GRACE_S = 0.35



def _bodies_under(occ, visible_only=True):
    """Every body in an occurrence and its descendants (root-context proxies)."""
    out = [b for b in occ.bRepBodies if b.isVisible or not visible_only]
    for child in occ.childOccurrences:
        out.extend(_bodies_under(child, visible_only))
    return out


FACE_OPACITY = 0.35     # picked parts' tinted faces


def _body_key(body):
    """Path of a body proxy, as a split part's path: its occurrence path plus the body name
    (so "hidden with H" checks against occurrence paths work on it)."""
    occ = body.assemblyContext
    return (occ.fullPathName if occ is not None else "") + refs.BODY_MARK + body.name


class PickCommand:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.step_id = None
        self.step_paths = set()     # the step's parts when the command opened
        self._handlers = []
        self._reset_session()

    def _reset_session(self):
        self.picked = {}         # path -> occurrence
        self.hidden = []         # occurrences hidden here, in order (for U)
        self.hovered = None      # body proxy under the cursor
        self.ghosted = set()     # _body_key of picked bodies hidden and drawn see-through
        self.status_changed = False
        self.hover_ended = None  # perf_counter time preselection ended, or None
        self.hover_at = 0.0      # last hover ray test
        self.mouse_down = None   # (x, y, time) of the left button press
        self.inputs = None
        self.command = None
        self.syncing = False     # sync_box is refilling the Parts box: ignore its selection events
        self.hover_warned = False

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
        # Parts selected in Fusion before Pick start picked (read now: the command clears them).
        try:
            self.preselected = refs.selected_occurrences(self.ctrl.ui)
        except Exception:
            log.error("pick: read the Fusion selection")
            self.preselected = []
        self.ctrl.ui.commandDefinitions.itemById(CMD_ID).execute()

    # ------------------------------------------------------------ picking logic

    @property
    def level(self):
        return getattr(self.ctrl, "pick_level", refs.PICK_WHOLE)

    @level.setter
    def level(self, value):
        self.ctrl.pick_level = value

    def occ_for_body(self, body):
        """The occurrence a click on `body` stands for at the current level."""
        occ = body.assemblyContext
        if occ is None:
            return None  # root-component body: not a part we can track
        if refs.is_split(occ):
            return refs.BodyPart(occ, body)     # a split component: each body is a part (at any level)
        # No "known" parts here: this command decides the step's parts, so a whole-assembly click
        # always means the top-level assembly, even if some of its parts are in the step already.
        return refs.pick_unit(occ, self.level)

    def _target(self, entity, point=None):
        """(part to pick at the current level, path of the part clicked) for a clicked body or
        occurrence, or (None, None)."""
        body = adsk.fusion.BRepBody.cast(entity)
        if body is not None:
            occ = self.occ_for_body(body)
            if occ is None:
                return None, None
            clicked = occ.fullPathName if isinstance(occ, refs.BodyPart) else body.assemblyContext.fullPathName
            return occ, clicked
        occ = adsk.fusion.Occurrence.cast(entity)
        if occ is None:
            return None, None
        split = refs.split_body_at(occ, point)
        if split is not None:
            return split, split.fullPathName
        return refs.pick_unit(occ, self.level), occ.fullPathName

    def toggle(self, entity, point=None):
        """A click: unpick whatever picked part covers it, else pick it at the current level (an
        assembly replaces any of its parts picked before). The click itself isn't selected (Fusion
        drops a click that adds bodies of several parts); sync_box puts the picked parts in the
        Parts box instead, so Fusion highlights each whole, and draw_highlight outlines them."""
        occ, clicked = self._target(entity, point)
        if occ is None:
            return
        path = occ.fullPathName
        covering = [p for p in self.picked
                    if model.is_self_or_ancestor(p, clicked) or model.is_self_or_ancestor(path, p)]
        if any(model.is_self_or_ancestor(p, clicked) for p in covering):
            for p in covering:
                del self.picked[p]
            log.info("pick: clicked {} -> unpicked {}".format(clicked, ", ".join(covering)))
        else:
            for p in covering:
                del self.picked[p]
            self.picked[path] = occ
            log.info("pick: clicked {} -> {} ({})".format(clicked, path, self.level))
        self.update_status()
        self.redraw()                           # draws the outline

    def redraw(self):
        """Run one preview. doExecutePreview from a mouse event is ignored but any changed input
        brings one, so: if the status text just changed that's it, else flip the hidden "nudge"
        input. (Doing both, plus doExecutePreview, ran the preview three or four times.)"""
        if self.status_changed:
            self.status_changed = False
            return
        try:
            nudge = adsk.core.BoolValueCommandInput.cast(self.inputs.itemById("nudge"))
            nudge.value = not nudge.value
        except Exception:
            if self.command is not None:
                self.command.doExecutePreview()

    def body_under(self, pos):
        try:
            # Picked parts are hidden and drawn see-through: they still count under the cursor.
            ghosted = self.ghosted
            return refs.body_at(self.ctrl.app.activeViewport, self.ctrl.design(), pos,
                                lambda b: b.isVisible or _body_key(b) in ghosted)
        except Exception:
            log.error("pick: find the part under the cursor")
            return None

    def unselected(self, entity):
        """Fusion took something out of the Parts box (a click on a highlighted part, or its
        clear button): unpick the parts it stood for."""
        occ, clicked = self._target(entity)
        path = occ.fullPathName if occ is not None else None
        drop = [p for p in self.picked if path and (p == path or model.is_self_or_ancestor(p, clicked)
                                                     or model.is_self_or_ancestor(path, p))]
        for p in drop:
            del self.picked[p]
        if drop:
            log.info("pick: unselected -> unpicked {}".format(", ".join(drop)))
        self.sync_box()
        self.update_status()
        if self.command is not None:
            self.command.doExecutePreview()

    def sync_box(self):
        """(The Parts box is hidden and stays empty: picks show as outlines.)"""
        return

    def _old_sync_box(self):
        """Former: make the Parts box hold exactly the picked parts."""
        if self.inputs is None:
            return
        box = adsk.core.SelectionCommandInput.cast(self.inputs.itemById("parts"))
        if box is None:
            return
        self.syncing = True
        failed = 0
        try:
            box.clearSelection()
            for part in self.picked.values():
                try:
                    if not box.addSelection(part.body if isinstance(part, refs.BodyPart) else part):
                        failed += 1
                except Exception:
                    failed += 1         # hidden (H) parts can't be selected; they stay picked
        except Exception:
            log.error("pick sync box")
        finally:
            self.syncing = False
        log.info("pick: box holds {} of {} picked{}".format(
            box.selectionCount, len(self.picked), " ({} not selectable)".format(failed) if failed else ""))

    def draw_highlight(self, see_through=False):
        """Draw every picked part in selection blue: see-through tinted faces plus outlined
        edges (other parts in front hide them). With `see_through` (from executePreview, which
        rolls it back) the real bodies are hidden so the picked parts read as transparent."""
        design = self.ctrl.design()
        clear_highlight(design)
        self.ghosted = set()
        if not self.picked:
            return
        group = design.rootComponent.customGraphicsGroups.add()
        group.id = HIGHLIGHT_GROUP_ID
        drawn = 0
        blue = adsk.core.Color.create(*scene.HIGHLIGHT_COLOR, 255)
        color = adsk.fusion.CustomGraphicsSolidColorEffect.create(blue)
        faces = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            blue, blue, adsk.core.Color.create(255, 255, 255, 255), adsk.core.Color.create(0, 0, 0, 255),
            10.0, FACE_OPACITY)
        hidden = [h.fullPathName for h in self.hidden]
        t_data = t_draw = t_bulb = 0.0          # where a preview's time goes (logged below)
        for part in list(self.picked.values()):
            try:
                if isinstance(part, refs.BodyPart):
                    bodies = [part.body] if part.body.isVisible else []
                else:
                    bodies = _bodies_under(part)
                for body in bodies:
                    key = _body_key(body)
                    if any(model.is_self_or_ancestor(h, key) for h in hidden):
                        continue                # hidden with H: stays hidden
                    native = body.nativeObject or body
                    matrix = body.assemblyContext.transform2 if body.assemblyContext else adsk.core.Matrix3D.create()
                    if see_through:
                        t = time.perf_counter()
                        mesh = self.ctrl.scene._mesh(native)
                        self.ctrl.scene._edges(native)
                        t_data += time.perf_counter() - t
                        t = time.perf_counter()
                        if mesh is not None:
                            coords, indices, normals = mesh
                            shell = self._add(group, native, "mesh", coords,
                                              lambda g, c: g.addMesh(c, indices, normals, indices))
                            shell.transform = matrix
                            shell.isSelectable = False
                            shell.color = faces
                            if isinstance(part, refs.BodyPart):
                                body.isLightBulbOn = False
                            self.ghosted.add(key)
                        t_draw += time.perf_counter() - t
                    t = time.perf_counter()
                    coords, lengths = self.ctrl.scene._edges(native)
                    if not lengths:
                        continue
                    lines = self._add(group, native, "edges", coords,
                                      lambda g, c: g.addLines(c, [], True, lengths))
                    lines.transform = matrix
                    lines.isSelectable = False
                    lines.weight = 2.5
                    lines.depthPriority = 0     # hidden behind other parts, like the model
                    lines.color = color
                    drawn += 1
                    t_draw += time.perf_counter() - t
                # A whole part / assembly: one light bulb hides all its bodies (one per body was slow).
                if see_through and not isinstance(part, refs.BodyPart) and bodies and part.isLightBulbOn:
                    t = time.perf_counter()
                    part.isLightBulbOn = False
                    t_bulb += time.perf_counter() - t
            except Exception:
                log.error("pick highlight")
        log.info("pick: drew {} bodies of {} picked (mesh/edge data {:.0f} ms, drawing {:.0f} ms, hiding {:.0f} ms)".format(
            drawn, len(self.picked), t_data * 1000, t_draw * 1000, t_bulb * 1000))

    def _add(self, group, native, kind, coords, add):
        """add(group, coordinates), with the coordinates made fresh each time. (Reusing one across
        previews crashed Fusion: it belongs to the graphics group it was first drawn in.)"""
        return add(group, adsk.fusion.CustomGraphicsCoordinates.create(coords))

    def warm(self, body):
        """Work out the mesh and edges of what a click on `body` would pick, while the cursor is
        on it, so the click itself draws from the cache (the first draw of a part was ~0.4 s)."""
        try:
            occ, _ = self._target(body)
            if occ is None:
                return
            t = time.perf_counter()
            bodies = [occ.body] if isinstance(occ, refs.BodyPart) else _bodies_under(occ)
            for b in bodies[:200]:
                native = b.nativeObject or b
                self.ctrl.scene._mesh(native)
                self.ctrl.scene._edges(native)
            ms = (time.perf_counter() - t) * 1000
            if ms > 20:
                log.info("pick: readied {} ({} bodies) in {:.0f} ms".format(occ.fullPathName, len(bodies), ms))
        except Exception:
            log.error("pick warm")

    def unpick(self, body, additional):     # (only for selections Fusion still holds)
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
        self.redraw()

    def hide_hovered(self):
        entity = self.hovered
        ended = self.hover_ended
        self.hovered = None
        if entity is None or not entity.isValid:
            log.info("pick: H with nothing under the cursor")
            return False
        if ended is not None and time.perf_counter() - ended > HOVER_GRACE_S:
            log.info("pick: H ignored, hover ended {:.2f}s ago".format(time.perf_counter() - ended))
            return False
        # H hides the single part under the cursor (to dig inward), whatever the pick level.
        body = adsk.fusion.BRepBody.cast(entity)
        if body is not None:
            occ = body.assemblyContext
            if occ is not None and refs.is_split(occ):
                occ = refs.BodyPart(occ, body)
        else:
            occ = adsk.fusion.Occurrence.cast(entity)
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

    def save_record(self):
        """Note on disk what this preview switched off, so a crash can't leave it hidden."""
        try:
            occs, bodies = [], []
            for h in self.hidden:
                if isinstance(h, refs.BodyPart):
                    bodies.append(hidden_record.body_entry(h.body))
                else:
                    occs.append(h.fullPathName)
            for part in self.picked.values():
                if isinstance(part, refs.BodyPart):
                    bodies.append(hidden_record.body_entry(part.body))
                else:
                    occs.append(part.fullPathName)
            hidden_record.record(self.ctrl.design(), "pick", occs, [b for b in bodies if b])
        except Exception:
            log.error("pick: save the hidden-parts record")

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
        # Plain text: setting formattedText fired two input changes, so two full previews.
        text = "{} picked  ·  {} hidden".format(len(self.picked), len(self.hidden))
        if hovered_name:
            text += "\nUnder cursor: {}".format(hovered_name)
        if status.text != text:
            status.text = text
            self.status_changed = True          # (a changed input already brings a preview)

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
            self.picked[occ.fullPathName] = occ     # shown by draw_highlight, not Fusion's selection
        added = 0
        for occ in getattr(self, "preselected", None) or []:
            path = occ.fullPathName
            if any(model.is_self_or_ancestor(p, path) for p in self.picked):
                continue                            # already in (or inside an assembly that is)
            for p in [p for p in self.picked if model.is_self_or_ancestor(path, p)]:
                del self.picked[p]                  # the selected assembly replaces its parts
            self.picked[path] = occ
            added += 1
        if added:
            log.info("pick: started with {} part(s) selected in Fusion".format(added))
        self.preselected = []

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


def clear_highlight(design):
    try:
        groups = design.rootComponent.customGraphicsGroups
        for i in range(groups.count - 1, -1, -1):
            if groups.item(i).id == HIGHLIGHT_GROUP_ID:
                groups.item(i).deleteMe()
    except Exception:
        log.error("clear pick highlight")


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
            owner.step_paths = set(model.item_paths(step))
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
            sel.addSelectionFilter("Occurrences")
            sel.setSelectionLimits(0, 0)
            # Hidden: Fusion's selection highlight drew over nearby parts and made them hard to
            # click. Picks are found from the mouse (click / hover), shown by the outline only.
            sel.isVisible = False

            level = inputs.addDropDownCommandInput(
                "level", "Click picks", adsk.core.DropDownStyles.TextListDropDownStyle)
            for name in refs.PICK_LEVELS:
                level.listItems.add(name, name == owner.level)
            level.tooltip = refs.PICK_TIP

            inputs.addBoolValueInput("unhideLast", "Unhide last", False, "", False)
            inputs.addBoolValueInput("unhideAll", "Show all hidden", False, "", False)
            inputs.addBoolValueInput("hideOthers", "Hide parts in other steps", False, "", False)
            inputs.addTextBoxCommandInput("status", "", "", 2, True)
            nudge = inputs.addBoolValueInput("nudge", "", True, "", False)   # flipped to force a preview
            nudge.isVisible = False

            owner.select_existing(sel)
            owner.sync_box()
            owner.update_status()
            owner.draw_highlight()

            for event, cls in ((cmd.mouseDown, _MouseDown), (cmd.mouseUp, _MouseUp), (cmd.mouseMove, _MouseMove),
                               (cmd.keyDown, _KeyDown),
                               (cmd.inputChanged, _InputChanged), (cmd.executePreview, _Preview),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
        except Exception:
            _fail(ctrl, "Opening Pick Parts")


class _MouseDown(adsk.core.MouseEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        args = adsk.core.MouseEventArgs.cast(args)
        p = args.viewportPosition
        self.owner.mouse_down = (p.x, p.y, time.perf_counter()) \
            if args.button == adsk.core.MouseButtons.LeftMouseButton else None


class _MouseUp(adsk.core.MouseEventHandler):
    """A click (press and release within a few pixels): toggle the part under the cursor."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.MouseEventArgs.cast(args)
            down, owner.mouse_down = owner.mouse_down, None
            p = args.viewportPosition
            if (down is None or args.button != adsk.core.MouseButtons.LeftMouseButton
                    or abs(p.x - down[0]) + abs(p.y - down[1]) > 6 or time.perf_counter() - down[2] > 1.0):
                return                          # a drag (orbit, box select...), not a click
            body = owner.body_under(p)
            if body is None:
                log.info("pick: click at ({:.0f}, {:.0f}) on nothing".format(p.x, p.y))
                return
            owner.toggle(body)
        except Exception:
            log.error("pick click")


class _MouseMove(adsk.core.MouseEventHandler):
    """Hover: remember the body under the cursor (for H). No redraw or status change: either
    would make Fusion run a preview on every move."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            now = time.perf_counter()
            if now - owner.hover_at < 0.06:
                return                          # (a ray test per mouse move is more than needed)
            owner.hover_at = now
            body = owner.body_under(adsk.core.MouseEventArgs.cast(args).viewportPosition)
            changed = body is not None and (owner.hovered is None or owner.hovered != body)
            owner.hovered = body
            owner.hover_ended = None if body is not None else now
            if changed:
                owner.warm(body)
        except Exception:
            log.error("pick hover")


class _Select(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            if self.owner.syncing:
                return
            args.isSelectable = False       # the picked parts go in the box instead (sync_box)
            self.owner.toggle(args.selection.entity, args.selection.point)
        except Exception:
            log.error("pick select")


class _Unselect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            if self.owner.syncing:
                return
            args = adsk.core.SelectionEventArgs.cast(args)
            self.owner.unselected(args.selection.entity)
        except Exception:
            log.error("pick unselect")


class _PreSelect(adsk.core.SelectionEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.SelectionEventArgs.cast(args)
            entity = args.selection.entity
            self.owner.hovered = entity
            self.owner.hover_ended = None
            occ, clicked = self.owner._target(entity, args.selection.point)
            self.owner.update_status(occ.name if occ else None)
            # Hover shows what a click would pick: the whole assembly (or sub-assembly), not just
            # the part under the cursor.
            if occ is not None and not isinstance(occ, refs.BodyPart) and occ.fullPathName != clicked:
                extra = adsk.core.ObjectCollection.create()
                extra.add(occ)
                try:
                    args.additionalEntities = extra
                except Exception:
                    if not self.owner.hover_warned:
                        self.owner.hover_warned = True
                        log.error("pick preselect: highlight the whole assembly")
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
            log.info("pick: input changed " + cid)
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
            t0 = time.perf_counter()
            self.owner.status_changed = False
            self.owner.apply_hidden()
            self.owner.draw_highlight(see_through=True)
            self.owner.save_record()
            log.info("pick: preview {:.0f} ms".format((time.perf_counter() - t0) * 1000))
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
            clear_highlight(owner.ctrl.design())
            hidden_record.record(owner.ctrl.design(), "pick", [], [])
            owner.inputs = None
            owner.command = None
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("pick destroy")
