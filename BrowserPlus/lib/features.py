"""Part mode: the active component's features and sketches, in timeline order.

Part mode is a design that is one part (no sub-components), or a component
activated inside an assembly. Each feature lists the sketches it's built from
(found through its inputs: profiles, paths, axes, sections...); sketches no
feature uses are items of their own.

Item: {"id" (entity token), "kind" ("feature" | "sketch" | "construction"), "name",
"type" ("Extrude", "Sketch", ...), "index" (timeline), "health", "message",
"suppressed", "rolledBack", "visible" (sketches / construction), "sketches": [ids],
"used" (a sketch some feature uses)}.
"""

import adsk.core
import adsk.fusion

from . import log

HEALTH = {
    adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState: "ok",
    adsk.fusion.FeatureHealthStates.WarningFeatureHealthState: "warning",
    adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState: "error",
}
# Feature properties that can lead to the sketches it's made from.
_INPUTS = ("profile", "profiles", "path", "guideRail", "axis", "loftSections", "centerLineOrRails", "rails",
           "holePositionDefinition", "sketchPoint", "sketchPoints", "sketchCurves", "profileCurve", "profileCurves",
           "curve", "curves", "entity", "inputEntities")
_SKIP_TYPES = ("Occurrence", "Joint", "AsBuiltJoint", "RigidGroup", "AssemblyConstraint", "MotionLink",
               "JointOrigin", "ContactSet", "Snapshot")


def _safe(read, default=None):
    try:
        return read()
    except Exception:
        return default


def mode(design):
    """("part", component) for a one-part design or an activated component, else ("assembly", root)."""
    root = design.rootComponent
    active = _safe(lambda: design.activeComponent, root) or root
    if active != root:
        return "part", active
    if root.occurrences.count == 0:
        return "part", root
    return "assembly", root


def _short_type(entity):
    kind = entity.objectType.split("::")[-1]
    if kind.endswith("Feature") and kind != "Feature":
        kind = kind[:-len("Feature")]
    return kind


def _sketches_in(value, found, depth=0):
    """Collect the sketches reachable from a feature input (profile, path, collection...)."""
    if value is None or depth > 3:
        return
    sketch = adsk.fusion.Sketch.cast(value)
    if sketch is not None:
        found.append(sketch)
        return
    parent = _safe(lambda: value.parentSketch)
    if parent is not None:
        found.append(parent)
        return
    count = _safe(lambda: value.count)
    if isinstance(count, int) and hasattr(value, "item"):
        for i in range(min(count, 50)):
            _sketches_in(_safe(lambda: value.item(i)), found, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for v in value[:50]:
            _sketches_in(v, found, depth + 1)
        return
    for attr in ("entity", "sketchPoint", "sketchPoints", "profile", "profiles"):
        inner = _safe(lambda: getattr(value, attr))
        if inner is not None and inner is not value:
            _sketches_in(inner, found, depth + 1)


def feature_sketches(feature):
    found = []
    for attr in _INPUTS:
        _sketches_in(_safe(lambda: getattr(feature, attr)), found)
    return found


def collect(design, component):
    """(items, entities {id: entity}) for the component, in timeline order."""
    items, entities, sketches = [], {}, []      # sketches: [(Sketch, id)]
    timeline = _safe(lambda: design.timeline)
    parametric = _safe(lambda: design.designType, None) == adsk.fusion.DesignTypes.ParametricDesignType
    objects = []
    if parametric and timeline is not None:
        for i in range(timeline.count):
            to = timeline.item(i)
            if _safe(lambda: to.isGroup, False):
                continue
            entity = _safe(lambda: to.entity)
            if entity is not None:
                objects.append((entity, to))
    else:                                       # direct modelling: no timeline, just the sketches
        objects = [(s, None) for s in component.sketches]

    for entity, to in objects:
        try:
            kind_name = entity.objectType.split("::")[-1]
            if kind_name in _SKIP_TYPES:
                continue
            if _safe(lambda: entity.parentComponent) != component:
                continue
            if kind_name == "Sketch":
                kind = "sketch"
            elif kind_name.startswith("Construction"):
                kind = "construction"
            elif kind_name.endswith("Feature"):
                kind = "feature"
            else:
                continue
            token = _safe(lambda: entity.entityToken, "") or "{}#{}".format(kind_name, _safe(lambda: to.index, 0))
            item = {
                "id": token,
                "kind": kind,
                "name": _safe(lambda: entity.name, "") or _safe(lambda: to.name, "") or kind_name,
                "type": _short_type(entity),
                "index": _safe(lambda: to.index, -1) if to is not None else -1,
                "health": HEALTH.get(_safe(lambda: to.healthState), "ok") if to is not None else "ok",
                "message": (_safe(lambda: to.errorOrWarningMessage, "") or "") if to is not None else "",
                "suppressed": bool(_safe(lambda: to.isSuppressed, False)) if to is not None else False,
                "rolledBack": bool(_safe(lambda: to.isRolledBack, False)) if to is not None else False,
                "visible": bool(_safe(lambda: entity.isLightBulbOn, True)) if kind != "feature" else None,
                "sketches": [],
                "used": False,
            }
            if kind == "sketch":
                sketches.append((entity, token))
            elif kind == "feature":
                for sketch in feature_sketches(entity):
                    sid = next((s_id for s, s_id in sketches if s == sketch), None)
                    if sid is None:
                        sid = _safe(lambda: sketch.entityToken, "")
                    if sid and sid not in item["sketches"]:
                        item["sketches"].append(sid)
            items.append(item)
            entities[token] = entity
        except Exception:
            log.error("read feature")
    used = {sid for item in items for sid in item["sketches"]}
    for item in items:
        if item["kind"] == "sketch" and item["id"] in used:
            item["used"] = True
    return items, entities


def bodies(component):
    """The component's bodies (the BOM in part mode): [(body, token)]."""
    out = []
    for body in component.bRepBodies:
        out.append((body, _safe(lambda: body.entityToken, "") or body.name))
    return out
