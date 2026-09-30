"""Things the panel can do to a joint / relationship or a part."""

import json
import math
import os

import adsk.core
import adsk.fusion

from . import log

# Words that mark Fusion's own joint / relationship commands (logged for diagnosis).
_EDIT_WORDS = ("joint", "constraint", "relationship")

# Fusion's edit dialog for each kind, in order of preference (ids as Fusion
# reports them; each opens the editor for the selected item). Rigid groups
# have no edit command.
EDIT_COMMANDS = {
    "joint": ["DcEditJointAssembleCmd", "EditJointAssembleCmd"],
    "asBuilt": ["DcEditJointAsBuiltCmd"],
    # Relationships: not known yet (GenEditConstraintCmd is Generative Design's
    # structural constraint editor). Until then Edit selects it for right-click.
    "constraint": ["DcEditAssemblyMateCmd"],   # seen on Fusion's own right-click menu
    "motionLink": ["DcEditMotionRelationshipCmd", "EditMotionRelationshipCmd"],
    "rigidGroup": [],
}


# ---------------------------------------------------------------- learned edit commands
# Fusion's edit command for a kind is learned from its own right-click menu:
# when the user right-clicks a joint / relationship, the menu's "edit"
# commands are remembered (on disk) and used by the panel's Edit from then on.

_LEARNED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "cache",
                             "edit_commands.json")
_learned = None

KIND_BY_TYPE = {
    adsk.fusion.Joint.classType(): "joint",
    adsk.fusion.AsBuiltJoint.classType(): "asBuilt",
    adsk.fusion.AssemblyConstraint.classType(): "constraint",
    adsk.fusion.RigidGroup.classType(): "rigidGroup",
    adsk.fusion.MotionLink.classType(): "motionLink",
}


def learned():
    global _learned
    if _learned is None:
        try:
            with open(_LEARNED_FILE, encoding="utf-8") as handle:
                _learned = json.load(handle)
        except Exception:
            _learned = {}
    return _learned


def edit_candidates(kind):
    """Commands to try for Edit: learned from Fusion's right-click menu first, then built-in guesses."""
    out = list(learned().get(kind, []))
    out += [c for c in EDIT_COMMANDS.get(kind, []) if c not in out]
    return out


_all_command_ids = None


def feature_edit_candidates(ui, entity):
    """Edit commands for a feature, best first: learned from its right-click menu, then Fusion's
    naming ("FusionDcFilletEditCommand", "FusionExtrudeEditCommand"), then any command named like it."""
    global _all_command_ids
    object_type = entity.objectType
    short = object_type.split("::")[-1]
    if short.endswith("Feature"):
        short = short[:-len("Feature")]
    out = list(learned().get("feature:" + object_type, []))
    # Newer features have a "FusionDc..." editor (fillet, hole); the plain one then opens the wrong or
    # no dialog. Features without a Dc editor (extrude) use "Fusion<Type>EditCommand".
    out += ["FusionDc{}EditCommand".format(short), "Fusion{}EditCommand".format(short),
            "{}EditCommand".format(short), "Fusion{}FeatureEditCommand".format(short)]
    if _all_command_ids is None:
        defs = ui.commandDefinitions
        _all_command_ids = [defs.item(i).id for i in range(defs.count)]
    low = short.lower()
    out += sorted((c for c in _all_command_ids if low in c.lower() and "edit" in c.lower()), key=len)
    existing = []
    for cid in dict.fromkeys(out):
        if ui.commandDefinitions.itemById(cid) is not None:
            existing.append(cid)
    return existing


def _menu_command_ids(controls, visible_only=True):
    ids = []
    for i in range(controls.count):
        control = controls.item(i)
        try:
            if visible_only and not control.isVisible:
                continue
        except Exception:
            pass
        cmd = adsk.core.CommandControl.cast(control)
        if cmd is not None:
            try:
                ids.append(cmd.commandDefinition.id)
            except Exception:
                pass
            continue
        drop = adsk.core.DropDownControl.cast(control)
        if drop is not None:
            ids.extend(_menu_command_ids(drop.controls, visible_only))
            continue
        split = adsk.core.SplitButtonControl.cast(control)
        if split is not None:
            try:
                ids.append(split.defaultCommandDefinition.id)
                ids.extend(d.id for d in split.additionalDefinitions)
            except Exception:
                pass
    return ids


def learn_from_menu(args, app=None):
    """Called when Fusion shows a right-click menu: remember the edit command for the
    kind of joint / relationship that's selected. Returns the kind learned, or None."""
    # Diagnosis: log every right-click menu, whatever is selected (type + visible commands),
    # and Fusion's own selection path for it (text command Selections.List).
    try:
        if app is not None:
            from . import collect, textselect
            paths = textselect.selection_paths(app)
            log.info("  selection path: {}".format(", ".join(paths) or "none"))
            chosen = list(args.selectedEntities)
            design = adsk.fusion.Design.cast(app.activeProduct)
            if (design is not None and len(paths) == 1 and len(chosen) == 1
                    and getattr(chosen[0], "objectType", "") == adsk.fusion.AssemblyConstraint.classType()):
                try:
                    token = chosen[0].entityToken
                except Exception:
                    token = ""
                textselect.learn_relationship(collect._document_key(design), paths[0], token)
        types = [getattr(e, "objectType", "?") for e in args.selectedEntities]
        all_ids = []
        for menu in (args.linearMarkingMenu, args.radialMarkingMenu):
            try:
                all_ids.extend(_menu_command_ids(menu.controls))
            except Exception:
                pass
        log.info("right-click on {}: visible menu {}".format(", ".join(types) or "nothing", ", ".join(all_ids) or "(empty)"))
        for ent in args.selectedEntities:
            index = ""
            try:
                index = ent.timelineObject.index
            except Exception:
                pass
            log.info("  selected: type {} name {} timeline index {} token {}".format(
                getattr(ent, "objectType", "?"), getattr(ent, "name", "?"), index, getattr(ent, "entityToken", "?")))
    except Exception:
        log.error("log right-click menu")
    kinds = set()
    for ent in args.selectedEntities:
        timeline_object = adsk.fusion.TimelineObject.cast(ent)
        if timeline_object is not None:                  # right-click on the timeline
            ent = timeline_object.entity
        object_type = getattr(ent, "objectType", "")
        kind = KIND_BY_TYPE.get(object_type)
        if kind is None and object_type.endswith("Feature"):
            kind = "feature:" + object_type          # part mode: each feature type has its own editor
        if kind:
            kinds.add(kind)
    if len(kinds) != 1:
        return None
    kind = kinds.pop()
    ids = []
    for menu in (args.linearMarkingMenu, args.radialMarkingMenu):
        try:
            ids.extend(_menu_command_ids(menu.controls))
        except Exception:
            pass
    log.info("right-click menu for a {}: {}".format(kind, ", ".join(ids) or "no commands"))
    edits = [c for c in dict.fromkeys(ids) if "edit" in c.lower()]
    if not edits or learned().get(kind) == edits:
        return None
    learned()[kind] = edits
    try:
        os.makedirs(os.path.dirname(_LEARNED_FILE), exist_ok=True)
        with open(_LEARNED_FILE, "w", encoding="utf-8") as handle:
            json.dump(learned(), handle)
    except Exception:
        log.error("save learned edit commands")
    log.info("learned edit command(s) for {}: {}".format(kind, ", ".join(edits)))
    return kind


def dump_text_commands(app, path):
    """Diagnosis: write Fusion's text-command list (hidden ones too) to a file."""
    out = []
    for command in ("TextCommands.List /hidden", "TextCommands.List"):
        try:
            result = app.executeTextCommand(command)
            out.append("=== {} ===\n{}".format(command, result))
            break
        except Exception as error:
            out.append("=== {} failed: {} ===".format(command, error))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out))
        log.info("text commands written to " + path)
    except Exception:
        log.error("write text commands")


def find_edit_commands(ui):
    """Command ids that look like Fusion's edit-joint/relationship commands (logged once for diagnosis)."""
    related, edit = [], []
    defs = ui.commandDefinitions
    for i in range(defs.count):
        try:
            cid = defs.item(i).id
        except Exception:
            continue
        low = cid.lower()
        if any(w in low for w in _EDIT_WORDS):
            related.append(cid)
            if "edit" in low:
                edit.append(cid)
    log.info("commands mentioning joints/relationships: {}".format(", ".join(sorted(related)) or "none"))
    return edit


def select(ui, entities, keep=False):
    """Replace (or with `keep`, add to) the Fusion selection (skipping any Fusion won't select)."""
    sels = ui.activeSelections
    if not keep:
        sels.clear()
    added = 0
    for ent in entities:
        if ent is None:
            continue
        try:
            sels.add(ent)
            added += 1
        except Exception as error:
            log.info("couldn't select {}: {}".format(getattr(ent, "objectType", "?"), error))
    return added


def select_item(ui, design, entity, token):
    """Select a joint / relationship so it highlights in the browser and timeline.

    Tries, in order: the object as stored, a fresh one found by its token,
    the non-proxy object, and its timeline entry. Logs what worked. Returns
    the selected object or None.
    """
    candidates = [("stored", entity)]
    if token:
        try:
            found = design.findEntityByToken(token)
            if found:
                candidates.append(("by token", found[0]))
        except Exception:
            pass
    native = getattr(entity, "nativeObject", None) if entity is not None else None
    if native is not None:
        candidates.append(("native", native))
    timeline_object = None
    try:
        timeline_object = entity.timelineObject if entity is not None else None
    except Exception:
        pass
    if timeline_object is not None:
        candidates.append(("timeline entry", timeline_object))
    sels = ui.activeSelections
    for how, candidate in candidates:
        if candidate is None:
            continue
        try:
            if not candidate.isValid:
                continue
            sels.clear()
            sels.add(candidate)
            if sels.count:
                log.info("selected {} ({})".format(getattr(candidate, "objectType", "?"), how))
                return candidate
        except Exception as error:
            log.info("select {} failed: {}".format(how, error))
    return None


def zoom_to(app, occurrences):
    """Aim the camera at the parts' combined bounding box, keeping the view direction."""
    boxes = [o.boundingBox for o in occurrences if o is not None]
    if not boxes:
        return False
    lo = [min(b.minPoint.x for b in boxes), min(b.minPoint.y for b in boxes), min(b.minPoint.z for b in boxes)]
    hi = [max(b.maxPoint.x for b in boxes), max(b.maxPoint.y for b in boxes), max(b.maxPoint.z for b in boxes)]
    center = adsk.core.Point3D.create(*[(a + b) / 2.0 for a, b in zip(lo, hi)])
    size = max(math.sqrt(sum((b - a) ** 2 for a, b in zip(lo, hi))), 0.1)
    viewport = app.activeViewport
    cam = viewport.camera
    view = cam.eye.vectorTo(cam.target)
    view.normalize()
    if cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType:
        distance = cam.eye.distanceTo(cam.target)
        cam.viewExtents = size * 1.3
    else:
        half = max(cam.perspectiveAngle / 2.0, 0.05)
        distance = size * 0.75 / math.tan(half)
    view.scaleBy(distance)
    eye = center.copy()
    eye.translateBy(adsk.core.Vector3D.create(-view.x, -view.y, -view.z))
    cam.target = center
    cam.eye = eye
    cam.isFitView = False
    cam.isSmoothTransition = True
    viewport.camera = cam
    return True


def rename(entity, name):
    try:
        entity.name = name
    except Exception:
        # A relationship / joint seen through a subassembly (proxy): rename the original.
        native = getattr(entity, "nativeObject", None)
        if native is None:
            raise
        native.name = name
    log.info("renamed to " + name)


def set_suppressed(entity, suppressed):
    entity.isSuppressed = bool(suppressed)


def delete(entity):
    return entity.deleteMe()


def roll_to(entity):
    """Move the timeline marker to just after this joint/relationship."""
    timeline_object = entity.timelineObject
    if timeline_object is None:
        return False
    return timeline_object.rollTo(False)


def roll_to_end(design):
    design.timeline.moveToEnd()


def edit(ui, design, entity, token, command_ids):
    """Open Fusion's own edit dialog: select the entity, run its edit command."""
    select_item(ui, design, entity, token)
    for cid in command_ids:
        cmd = ui.commandDefinitions.itemById(cid)
        if cmd is None:
            continue
        try:
            if cmd.execute():
                log.info("edit via " + cid)
                return True
        except Exception:
            log.error("edit via " + cid)
    return False
