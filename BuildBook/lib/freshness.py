"""Keeping the manual up to date as the design changes.

A step's picture depends on the design's geometry and on the manual (the step's parts and moves,
earlier steps' parts, its view, the look settings). Both are boiled down to a fingerprint, stored
with a step when its preview is taken or its picture exported; when the fingerprint differs now,
that preview / exported picture is out of date. Parts added to the design since the manual last
"knew" its parts are reported too.
"""

import hashlib
import json

from . import log, model

LOOK_KEYS = ("earlier", "later", "unassigned", "ghostOpacity", "drawEdges", "trail", "image")


def geometry(design):
    """Fingerprint of what the design looks like: every component's revision and every
    occurrence's position."""
    h = hashlib.sha1()
    try:
        for occ in design.rootComponent.allOccurrences:
            h.update(occ.fullPathName.encode("utf-8", "replace"))
            try:
                h.update((occ.component.revisionId or "").encode("ascii", "replace"))
            except Exception:
                pass
            try:
                h.update(",".join("{:.4f}".format(v) for v in occ.transform2.asArray()).encode("ascii"))
            except Exception:
                pass
        root = design.rootComponent
        h.update((root.revisionId or "").encode("ascii", "replace"))
    except Exception:
        log.error("design fingerprint")
    return h.hexdigest()


def step_print(manual, step, geo):
    """Fingerprint of a step's picture: the design, its view, its parts and moves, the parts of
    the steps before it, and the look settings."""
    before = []
    for _, st in model.ordered_steps(manual):
        before.append([sorted(model.item_paths(st)), bool(st.get("prep"))])
        if st["id"] == step["id"]:
            break
    settings = manual.get("settings", {})
    data = {
        "geo": geo,
        "camera": step.get("camera"),
        "explodes": step.get("explodes", []),
        "trail": step.get("trail"),
        "context": [step.get("earlier"), step.get("later")],
        "steps": before,
        "look": {k: settings.get(k) for k in LOOK_KEYS},
    }
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def report(manual, geo, unassigned_paths):
    """{"previews": [step ids], "exports": [step ids], "newParts": [paths]} of what's out of date."""
    previews, exports = [], []
    for _, step in model.ordered_steps(manual):
        if not step.get("camera"):
            continue
        fp = step_print(manual, step, geo)
        if step.get("thumbFp") and step["thumbFp"] != fp:
            previews.append(step["id"])
        if step.get("exportedAt") and step.get("exportFp") and step["exportFp"] != fp:
            exports.append(step["id"])
    known = manual.get("knownParts")
    new_parts = [] if known is None else [p for p in unassigned_paths if p not in set(known)]
    return {"previews": previews, "exports": exports, "newParts": new_parts}
