"""Line Start command: choose where a part's trail lines start.

By default a part's trail lines start at the centre of its bounding box.
This command picks another point: a circular edge or arc (its centre), a
vertex, a sketch or construction point, or a cylindrical face (the point on
its axis nearest the part's centre) -- e.g. the offset hole a shaft goes
into. The point is stored in the part's own component coordinates, so it
moves with the part through every explode move.

The model is shown assembled while it runs, with "Show only this part" (on
by default) hiding everything else; untick it to pick a point on another
part, like the hole a shaft goes into. The hiding happens in executePreview
(Fusion only keeps visibility changes made there) and Fusion undoes it when
the command closes. A small cross marks the chosen point. OK saves, Cancel
doesn't.
"""

import traceback

import adsk.core
import adsk.fusion

from ..lib import log, model, refs, scene

CMD_ID = "buildBookAnchor"
CMD_NAME = "Line Start"
CMD_TIP = "Choose where a part's trail lines start (e.g. the hole a shaft goes into)"
MARK_GROUP_ID = "BuildBookAnchor"
MARK_COLOR = (255, 120, 0)
MARK_PIXELS = 12        # size of the marker cross on screen


class AnchorCommand:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.step_id = None
        self.path = None
        self._handlers = []
        self._reset_session()

    def _reset_session(self):
        self.point = None        # chosen world point (x, y, z), or None
        self.use_centre = False
        self.inputs = None
        self._mark = None

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

    def launch(self, step_id, path):
        self.step_id = step_id
        self.path = path
        self.ctrl.close_view()      # assembled model: every real part is clickable
        self.ctrl.ui.commandDefinitions.itemById(CMD_ID).execute()

    # ------------------------------------------------------------ helpers

    def occurrence(self):
        return refs.path_index(self.ctrl.design()).get(self.path)

    def point_from(self, entity):
        """World point for a picked entity, or None."""
        try:
            edge = adsk.fusion.BRepEdge.cast(entity)
            if edge:
                geom = edge.geometry
                circle = adsk.core.Circle3D.cast(geom) or adsk.core.Arc3D.cast(geom)
                return _xyz(circle.center) if circle else None
            vertex = adsk.fusion.BRepVertex.cast(entity)
            if vertex:
                return _xyz(vertex.geometry)
            sketch_point = adsk.fusion.SketchPoint.cast(entity)
            if sketch_point:
                return _xyz(sketch_point.worldGeometry)
            cpoint = adsk.fusion.ConstructionPoint.cast(entity)
            if cpoint:
                return _xyz(cpoint.geometry)
            face = adsk.fusion.BRepFace.cast(entity)
            if face:
                cyl = adsk.core.Cylinder.cast(face.geometry)
                occ = self.occurrence()
                if cyl and occ:
                    # The point on the cylinder's axis nearest the part's centre.
                    o, a = cyl.origin, cyl.axis
                    a.normalize()
                    c = refs.bbox_center(occ)
                    t = (c[0] - o.x) * a.x + (c[1] - o.y) * a.y + (c[2] - o.z) * a.z
                    return (o.x + a.x * t, o.y + a.y * t, o.z + a.z * t)
        except Exception:
            log.error("line start point")
        return None

    def draw_mark(self):
        self.clear_mark()
        point = self.point
        if point is None or self.use_centre:
            occ = self.occurrence()
            point = refs.bbox_center(occ) if occ else None
        if point is None:
            return
        design = self.ctrl.design()
        group = design.rootComponent.customGraphicsGroups.add()
        group.id = MARK_GROUP_ID
        s = MARK_PIXELS / 2.0
        coords = [-s, 0, 0, s, 0, 0, 0, -s, 0, 0, s, 0]
        lines = group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [], False)
        lines.weight = 3.0
        lines.isSelectable = False
        lines.depthPriority = 5
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(MARK_COLOR[0], MARK_COLOR[1], MARK_COLOR[2], 255))
        anchor = adsk.core.Point3D.create(0, 0, 0)
        billboard = adsk.fusion.CustomGraphicsBillBoard.create(anchor)
        billboard.billBoardStyle = adsk.fusion.CustomGraphicsBillBoardStyles.ScreenBillBoardStyle
        lines.billBoarding = billboard
        lines.viewScale = adsk.fusion.CustomGraphicsViewScale.create(1.0, anchor)
        lines.transform = _translation(point)
        self._mark = group
        self.ctrl.app.activeViewport.refresh()

    def clear_mark(self):
        """Delete every marker, not just the last one: Fusion's preview rollback
        can bring back markers already deleted, which would then linger."""
        try:
            if self._mark is not None and self._mark.isValid:
                self._mark.deleteMe()
        except Exception:
            log.error("line start mark")
        self._mark = None
        design = self.ctrl.design()
        if design is not None:
            scene.sweep(design, ids=(MARK_GROUP_ID,))

    def isolate(self):
        """Hide everything except the part (called from executePreview)."""
        occ = self.occurrence()
        if occ is None:
            return
        design = self.ctrl.design()
        path = occ.fullPathName
        for other in design.rootComponent.allOccurrences:
            p = other.fullPathName
            if model.is_self_or_ancestor(p, path) or model.is_self_or_ancestor(path, p):
                continue    # the part, its parents (needed to see it) and its own sub-parts
            parent = other.assemblyContext
            parent_path = parent.fullPathName if parent is not None else ""
            # Hide each unrelated branch once, at its top (hiding a parent hides its children).
            if parent is None or model.is_self_or_ancestor(parent_path, path):
                if other.isLightBulbOn:
                    other.isLightBulbOn = False
        for body in design.rootComponent.bRepBodies:
            if body.isLightBulbOn:
                body.isLightBulbOn = False

    def apply(self):
        manual = self.ctrl.load()
        _, step = model.find_step(manual, self.step_id)
        item = model.find_item(step, self.path) if step else None
        occ = self.occurrence()
        if item is None or occ is None:
            return
        if self.use_centre or self.point is None:
            item["anchor"] = None
        else:
            # World point -> the part's own coordinates, so it moves with the part.
            inverse = occ.transform2.copy()
            inverse.invert()
            p = adsk.core.Point3D.create(*self.point)
            p.transformBy(inverse)
            item["anchor"] = [p.x, p.y, p.z]
        self.ctrl.save(manual)
        log.info("line start: {} -> {}".format(self.path, item["anchor"]))


def _xyz(p):
    return (p.x, p.y, p.z)


def _translation(point):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(*point)
    return m


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
            owner.session = getattr(owner, "session", 0) + 1     # (see _Destroy)
            manual = ctrl.load()
            _, step = model.find_step(manual, owner.step_id)
            item = model.find_item(step, owner.path) if step else None
            occ = owner.occurrence()
            if item is None or occ is None:
                ctrl.ui.messageBox("That part isn't in the open step any more.")
                return
            owner._reset_session()
            if item.get("anchor"):
                p = adsk.core.Point3D.create(*item["anchor"])
                p.transformBy(occ.transform2)
                owner.point = _xyz(p)

            inputs = cmd.commandInputs
            owner.inputs = inputs
            inputs.addTextBoxCommandInput(
                "hint", "", "Trail lines of <b>{}</b> start at the point you pick: a hole or arc edge "
                            "(its centre), a vertex, a sketch or construction point, or a cylindrical "
                            "face (its axis).".format(occ.name), 3, True)
            pick = inputs.addSelectionInput("point", "Start point", "Pick where the lines start")
            for f in ("CircularEdges", "Vertices", "SketchPoints", "ConstructionPoints", "CylindricalFaces"):
                pick.addSelectionFilter(f)
            pick.setSelectionLimits(0, 1)
            inputs.addBoolValueInput("centre", "Use part centre", True, "", False)
            only = inputs.addBoolValueInput("isolate", "Show only this part", True, "", True)
            only.tooltip = "Hide every other part while picking. Untick to pick a point on another part."
            owner.draw_mark()

            for event, cls in ((cmd.inputChanged, _InputChanged), (cmd.executePreview, _Preview),
                               (cmd.execute, _Execute), (cmd.destroy, _Destroy)):
                handler = cls(owner)
                event.add(handler)
                owner._handlers.append(handler)
        except Exception:
            _fail(ctrl, "Opening Line Start")


class _InputChanged(adsk.core.InputChangedEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        owner = self.owner
        try:
            args = adsk.core.InputChangedEventArgs.cast(args)
            changed = args.input
            if changed.id == "point":
                pick = adsk.core.SelectionCommandInput.cast(changed)
                if pick.selectionCount:
                    point = owner.point_from(pick.selection(0).entity)
                    if point is not None:
                        owner.point = point
                        owner.use_centre = False
                        adsk.core.BoolValueCommandInput.cast(owner.inputs.itemById("centre")).value = False
            elif changed.id == "centre":
                owner.use_centre = adsk.core.BoolValueCommandInput.cast(changed).value
            owner.draw_mark()
        except Exception:
            log.error("line start inputChanged")


class _Preview(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            args = adsk.core.CommandEventArgs.cast(args)
            if adsk.core.BoolValueCommandInput.cast(self.owner.inputs.itemById("isolate")).value:
                self.owner.isolate()
            # Not a result: execute() still runs; the preview rollback shows everything again.
            args.isValidResult = False
        except Exception:
            log.error("line start preview")


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def notify(self, args):
        try:
            self.owner.apply()
        except Exception:
            _fail(self.owner.ctrl, "Line Start")


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
            owner.clear_mark()
            owner.inputs = None
            owner.ctrl.show_step(owner.step_id)
        except Exception:
            log.error("line start destroy")
