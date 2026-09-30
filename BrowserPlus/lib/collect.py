"""Read every joint-like thing in the design into plain records (see lib/analysis.py).

Joints live in the component that owns them, so for components used inside
subassemblies each instance is read through a proxy
(createForAssemblyContext) to get part paths from the top of the assembly.
Returns (records, parts, entities) where `entities` maps record id -> the
Fusion object, for actions.
"""

import json
import os
import time

import adsk.core
import adsk.fusion

from . import log

JOINT_TYPES = {
    adsk.fusion.JointTypes.RigidJointType: "Rigid",
    adsk.fusion.JointTypes.RevoluteJointType: "Revolute",
    adsk.fusion.JointTypes.SliderJointType: "Slider",
    adsk.fusion.JointTypes.CylindricalJointType: "Cylindrical",
    adsk.fusion.JointTypes.PinSlotJointType: "Pin-slot",
    adsk.fusion.JointTypes.PlanarJointType: "Planar",
    adsk.fusion.JointTypes.BallJointType: "Ball",
    adsk.fusion.JointTypes.InferredJointType: "Inferred",
}

HEALTH = {
    adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState: "ok",
    adsk.fusion.FeatureHealthStates.WarningFeatureHealthState: "warning",
    adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState: "error",
}


def _safe(read, default=None):
    """Read one property; some (e.g. a relationship's isMate) throw unless the
    timeline is rolled back to them, and one bad property must not drop the record."""
    try:
        return read()
    except Exception:
        return default


def _path(occ):
    return occ.fullPathName if occ is not None else ""


def _health(entity):
    try:
        return HEALTH.get(entity.healthState, "ok"), entity.errorOrWarningMessage or ""
    except Exception:
        return "ok", ""


def _proxy(entity, context):
    return entity.createForAssemblyContext(context) if context is not None else entity


def _record(entity, kind, context, parts, type_, details=None):
    health, message = _health(entity)
    locked = bool(_safe(lambda: entity.isLocked, False))
    token = _safe(lambda: entity.entityToken, "") or ""
    name = _safe(lambda: entity.name, "") or "(unnamed)"
    return {
        "id": "{}|{}|{}".format(kind, _path(context), token or name),
        "kind": kind,
        "name": name,
        "type": type_,
        "parts": [p for p in dict.fromkeys(parts)],
        "health": health,
        "message": message,
        "suppressed": bool(_safe(lambda: entity.isSuppressed, False)),
        "locked": locked,
        "context": _path(context),
        "details": details or [],
    }


def _joint_type(entity):
    try:
        return JOINT_TYPES.get(entity.jointMotion.jointType, "Joint")
    except Exception:
        return "Joint"


def _entity_occurrence(entity):
    occ = getattr(entity, "assemblyContext", None)
    return adsk.fusion.Occurrence.cast(occ) if occ is not None else None


# Relationship parts worked out so far: "document|token" -> [parts, details, how].
# Reading them can need the timeline rolled back, which is slow, so results
# (failures too) are kept on disk and reused until clear_cache() (the panel's
# refresh button, or a command that edits joints/relationships).
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "cache",
                           "relationships.json")
_relationship_cache = None
_cache_dirty = False


def _cache():
    global _relationship_cache
    if _relationship_cache is None:
        try:
            with open(_CACHE_FILE, encoding="utf-8") as handle:
                _relationship_cache = json.load(handle)
        except Exception:
            _relationship_cache = {}
    return _relationship_cache


def _save_cache():
    global _cache_dirty
    if not _cache_dirty:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(_cache(), handle)
        _cache_dirty = False
    except Exception:
        log.error("save relationship cache")


def clear_cache():
    global _relationship_cache, _cache_dirty
    _relationship_cache = {}
    _cache_dirty = True
    _save_cache()


def _document_key(design):
    doc = design.parentDocument
    key = _safe(lambda: doc.dataFile.id if doc.dataFile else "", "") or ""
    return key or _safe(lambda: doc.name, "design")


def _owner_component(entity):
    """The component a (native) face / edge / vertex / construction entity belongs to."""
    body = _safe(lambda: entity.body)
    if body is not None:
        return _safe(lambda: body.parentComponent)
    return _safe(lambda: entity.parentComponent)


def _entity_part(entity, by_component):
    """Occurrence path for a relationship's face/edge/etc.

    Uses its assembly context; failing that (a native entity), the one
    occurrence of its component. Returns (path, how) with how in
    "context" / "component" / "ambiguous" / "none".
    """
    occ = _safe(lambda: _entity_occurrence(entity))
    if occ is not None:
        return occ.fullPathName, "context"
    comp = _owner_component(entity)
    if comp is None:
        return "", "none"
    occs = by_component.get(_safe(lambda: comp.id, comp.name), [])
    if len(occs) == 1:
        return occs[0], "component"
    return "", "ambiguous" if occs else "none"

def _relationship_kind(rel, name):
    is_mate = _safe(lambda: rel.isMate)
    if is_mate is not None:
        return "Mate" if is_mate else "Flush"
    low = (name or "").lower()     # isMate can refuse to answer until the timeline is rolled back
    for word in ("mate", "flush", "angle", "tangent", "offset"):
        if word in low:
            return word.capitalize()
    return "Relationship"


def _read_relationships(entity, by_component):
    """(parts, details, notes) for a relationship; raises if Fusion refuses the reads."""
    parts, details, notes = [], [], []
    for rel in list(entity.geometricRelationships):
        one, two = rel.entityOne, rel.entityTwo          # may raise until rolled back
        a, how_a = _entity_part(one, by_component)
        b, how_b = _entity_part(two, by_component)
        notes.extend([how_a, how_b])
        name = _safe(lambda: rel.name, "") or ""
        kind = _relationship_kind(rel, name)
        offset = _safe(lambda: rel.offsetOrAngle.expression if rel.offsetOrAngle is not None else "", "") or ""
        parts.extend(p for p in (a, b) if p)
        details.append({"name": name, "kind": kind, "parts": [a, b], "offset": offset,
                        "suppressed": bool(_safe(lambda: rel.isSuppressed, False)),
                        # each side's face / edge / point, to highlight it later (faces.py)
                        "geometry": [_geometry_ref(one, a), _geometry_ref(two, b)]})
    return parts, details, notes


def _geometry_ref(entity, part):
    token = _safe(lambda: entity.entityToken, "")
    if not token:
        return None
    return {"token": token, "type": _safe(lambda: entity.objectType, ""), "part": part}


def _read_rolled_back(design, pending, by_component):
    """Read relationships Fusion refused to give directly, with the timeline rolled back.

    Fusion only hands out a relationship's faces with the timeline marker just
    before it (as when editing it). Going from the last relationship to the
    first keeps each step a small roll back; the marker is put back once at
    the end, so the model recomputes once. Returns {token: (parts, details, how)}.
    """
    timeline = design.timeline
    marker = timeline.markerPosition
    started = time.perf_counter()
    out = {}
    ordered = sorted(pending, key=lambda item: _safe(lambda: item[0].timelineObject.index, -1), reverse=True)
    try:
        for entity, token in ordered:
            name = _safe(lambda: entity.name, "?")
            result = None
            for before in (True, False):
                try:
                    entity.timelineObject.rollTo(before)
                    live = entity if entity.isValid else adsk.fusion.AssemblyConstraint.cast(
                        design.findEntityByToken(token)[0])
                    parts, details, notes = _read_relationships(live, by_component)
                    result = (parts, details, "rolled back " + ("before" if before else "after"))
                    break
                except Exception as error:
                    last_error = error
            if result is None:
                log.info("relationship {}: couldn't read its faces even rolled back ({})".format(name, last_error))
                result = ([], [], "failed")
            elif not result[0]:
                log.info("relationship {}: read ({}) but no parts matched".format(name, result[2]))
            out[token] = result
    finally:
        timeline.markerPosition = marker
    log.info("relationships: read {} with the timeline rolled back in {:.0f} ms".format(
        len(out), (time.perf_counter() - started) * 1000))
    return out


def _constraint_record(entity, context, parts, details):
    kinds = [d["kind"] for d in details]
    type_ = " + ".join(dict.fromkeys(kinds)) or "Relationship"
    return _record(entity, "constraint", context, parts, type_, details)


def collect(design, info=None):
    records, entities = [], {}
    root = design.rootComponent
    contexts = [(root, None)] + [(occ.component, occ) for occ in root.allOccurrences]
    by_component = {}           # component id -> [occurrence paths], for native relationship geometry
    for occ in root.allOccurrences:
        by_component.setdefault(_safe(lambda: occ.component.id, occ.component.name), []).append(occ.fullPathName)

    def add(entity, rec):
        records.append(rec)
        entities[rec["id"]] = entity

    constraints = []            # (relationship, context), read after everything else
    for comp, context in contexts:
        for joint in comp.joints:
            try:
                j = _proxy(joint, context)
                add(j, _record(j, "joint", context, [_path(j.occurrenceOne), _path(j.occurrenceTwo)],
                               _joint_type(j)))
            except Exception:
                log.error("read joint " + joint.name)
        for joint in comp.asBuiltJoints:
            try:
                j = _proxy(joint, context)
                add(j, _record(j, "asBuilt", context, [_path(j.occurrenceOne), _path(j.occurrenceTwo)],
                               "As-built " + _joint_type(j).lower()))
            except Exception:
                log.error("read as-built joint " + joint.name)
        for group in comp.rigidGroups:
            try:
                g = _proxy(group, context)
                add(g, _record(g, "rigidGroup", context, [_path(o) for o in g.occurrences], "Rigid group"))
            except Exception:
                log.error("read rigid group " + group.name)
        for link in comp.motionLinks:
            try:
                m = _proxy(link, context)
                joints = [j for j in (m.jointOne, m.jointTwo) if j is not None]
                parts = []
                for j in joints:
                    for occ in (getattr(j, "occurrenceOne", None), getattr(j, "occurrenceTwo", None)):
                        if occ is not None:
                            parts.append(_path(occ))
                details = [{"name": j.name, "kind": "linked joint"} for j in joints]
                add(m, _record(m, "motionLink", context, parts, "Motion link", details))
            except Exception:
                log.error("read motion link " + link.name)
        for constraint in comp.assemblyConstraints:
            try:
                constraints.append((_proxy(constraint, context), context))
            except Exception:
                log.error("read relationship " + constraint.name)

    # Relationships: cached, else a direct read, else (all together) rolled back.
    global _cache_dirty
    cache = _cache()
    doc = _document_key(design)
    known, pending = {}, []
    for c, context in constraints:
        token = _safe(lambda: c.entityToken, "") or ""
        hit = cache.get(doc + "|" + token) if token else None
        if hit is not None and all("geometry" in d for d in hit[1]):
            known[token] = hit
            continue
        try:
            parts, details, _ = _read_relationships(c, by_component)
            known[token] = (parts, details, "direct")
            if token:
                cache[doc + "|" + token] = known[token]
                _cache_dirty = True
        except Exception:
            pending.append((c, token))
    if pending:
        for token, result in _read_rolled_back(design, pending, by_component).items():
            known[token] = result
            if token:
                cache[doc + "|" + token] = result
                _cache_dirty = True
    _save_cache()
    for c, context in constraints:
        token = _safe(lambda: c.entityToken, "") or ""
        parts, details, _ = known.get(token, ([], [], "failed"))
        try:
            add(c, _constraint_record(c, context, list(parts), list(details)))
        except Exception:
            log.error("relationship record")

    parts = []
    for occ in root.allOccurrences:
        try:
            comp = occ.component
            basic = info.basic(comp) if info is not None else {"hw": None}
            parts.append({
                "path": occ.fullPathName,
                "name": occ.name,
                "component": comp.name,
                "componentId": _safe(lambda: comp.id, "") or comp.name,
                "grounded": bool(occ.isGrounded),
                "bodies": occ.bRepBodies.count > 0,
                "leaf": occ.childOccurrences.count == 0,
                "visible": bool(_safe(lambda: occ.isLightBulbOn, True)),
                "hw": basic["hw"],
            })
        except Exception:
            log.error("read part")
    return records, parts, entities
