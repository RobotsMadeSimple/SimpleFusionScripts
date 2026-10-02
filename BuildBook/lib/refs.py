"""Occurrence references and design-level lookups (Fusion-facing)."""

import json
import os
import re
import time

import adsk.core
import adsk.fusion

from . import hardware, model


def make_ref(occ):
    return {
        "path": occ.fullPathName,
        "token": occ.entityToken,
        "name": occ.name,
    }


# ---------------------------------------------------------------- split components
# Components marked "split into bodies" (manual setting splitBodies: component keys): each of
# their bodies is a part of its own, at path "<occurrence path>+#<body name>" (so the
# occurrence counts as its ancestor everywhere paths are compared).

BODY_MARK = model.PATH_SEP + "#"
_split = set()


def set_split(keys):
    _split.clear()
    _split.update(keys or [])


def component_key(comp):
    try:
        return comp.id or comp.name
    except Exception:
        return comp.name


def is_split(occ):
    return occ is not None and not isinstance(occ, BodyPart) and component_key(occ.component) in _split


class _NoChildren(list):
    count = 0


class BodyPart:
    """One body of a split component, standing in for an occurrence wherever BuildBook uses
    parts (scene, explode, trails, picking, parts lists). Its light bulb is the body's."""

    def __init__(self, occ, body):
        self.occ, self.body = occ, body

    fullPathName = property(lambda self: self.occ.fullPathName + BODY_MARK + self.body.name)
    name = property(lambda self: self.body.name)
    component = property(lambda self: self.occ.component)
    transform2 = property(lambda self: self.occ.transform2)
    assemblyContext = property(lambda self: self.occ)   # its "parent": explode offsets are in its occurrence's space
    bRepBodies = property(lambda self: [self.body])
    childOccurrences = property(lambda self: _NoChildren())
    boundingBox = property(lambda self: self.body.boundingBox)
    entityToken = property(lambda self: self.body.entityToken)
    appearance = property(lambda self: self.occ.appearance)
    isVisible = property(lambda self: self.body.isVisible and self.occ.isVisible)
    isValid = property(lambda self: self.occ.isValid and self.body.isValid)

    @property
    def isLightBulbOn(self):
        return self.body.isLightBulbOn

    @isLightBulbOn.setter
    def isLightBulbOn(self, on):
        self.body.isLightBulbOn = on

    def __eq__(self, other):
        return isinstance(other, BodyPart) and other.fullPathName == self.fullPathName

    def __hash__(self):
        return hash(self.fullPathName)


def part_for_body(body):
    """The part a body stands for: itself (a BodyPart) in a split component, else its occurrence."""
    occ = body.assemblyContext
    if occ is None:
        return None
    return BodyPart(occ, body) if is_split(occ) else occ


def split_body_at(occ, point, tolerance=0.02):
    """The BodyPart under a clicked point when `occ` is (or contains) a split component, else None.
    (Fusion reports a click as the top-level occurrence; this finds the body actually clicked.)"""
    if occ is None or point is None or not _split:
        return None
    inside = (adsk.fusion.PointContainment.PointInsidePointContainment,
              adsk.fusion.PointContainment.PointOnPointContainment)
    stack = [occ]
    best = None
    while stack:
        o = stack.pop()
        if is_split(o):
            for body in o.bRepBodies:
                try:
                    if body.pointContainment(point) in inside:
                        return BodyPart(o, body)
                    # a click on an edge can sit a hair outside: take the nearest body within tolerance
                    box = body.boundingBox
                    if box.contains(point) and best is None:
                        best = BodyPart(o, body)
                except Exception:
                    pass
        stack.extend(o.childOccurrences)
    return best


def path_index(design):
    """fullPathName -> occurrence proxy (root context) for every occurrence, plus a BodyPart per
    body of a split component."""
    index = {}
    for occ in design.rootComponent.allOccurrences:
        index[occ.fullPathName] = occ
        if is_split(occ):
            for body in occ.bRepBodies:
                part = BodyPart(occ, body)
                index[part.fullPathName] = part
    return index


# Caches. Missing tokens last a session; hardware labels are also kept on disk
# (keyed by component id + name) so later sessions skip the slow property
# reads. clear_caches() (the panel's refresh button) empties both.
_missing_tokens = set()     # tokens a design-wide search already failed to find
_labels = None              # "component id|name" -> short hardware label ("" = none)
_labels_dirty = False
_LABEL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "cache", "labels.json")


def _label_cache():
    global _labels
    if _labels is None:
        try:
            with open(_LABEL_FILE, encoding="utf-8") as handle:
                _labels = json.load(handle)
        except Exception:
            _labels = {}
    return _labels


def flush_label_cache():
    """Write new hardware labels to disk (once per panel refresh, not per part)."""
    global _labels_dirty
    if not _labels_dirty or _labels is None:
        return
    try:
        os.makedirs(os.path.dirname(_LABEL_FILE), exist_ok=True)
        with open(_LABEL_FILE, "w", encoding="utf-8") as handle:
            json.dump(_labels, handle)
        _labels_dirty = False
    except Exception:
        pass


_pending = {}               # "component id|name" -> component whose description / part number to read


def forget_document():
    """Another document: forget what's per design (labels are per component, kept)."""
    _missing_tokens.clear()
    _pending.clear()


def has_pending():
    return bool(_pending)


def fill_pending(budget):
    """Read description / part number (slow in Fusion) for hardware-looking names, a batch at a
    time (`budget` seconds). Returns (labels found, components still to read)."""
    global _labels_dirty
    labels = _label_cache()
    started, found = time.perf_counter(), 0
    for key in list(_pending):
        comp = _pending.pop(key)
        try:
            label = hardware.short_name(comp.name, comp.description, comp.partNumber)
        except Exception:
            label = None
        labels[key] = label or ""
        _labels_dirty = True
        found += 1 if label else 0
        if time.perf_counter() - started > budget:
            break
    return found, len(_pending)


def clear_caches():
    global _labels, _labels_dirty
    _missing_tokens.clear()
    _pending.clear()
    _labels = {}
    _labels_dirty = True        # also empties the file on the next flush


def resolve(design, ref, index):
    """Find the occurrence for a ref: by path first, then by entity token.

    If it's found by token (the part was renamed or re-parented), the ref's
    path is updated in place and True is returned as the second value so the
    caller knows to save.
    """
    occ = index.get(ref.get("path", ""))
    if occ is not None:
        return occ, False
    token = ref.get("token")
    if token and token not in _missing_tokens:
        try:
            for ent in design.findEntityByToken(token):
                found = adsk.fusion.Occurrence.cast(ent)
                body = adsk.fusion.BRepBody.cast(ent)
                if found is None and body is not None and is_split(body.assemblyContext):
                    found = BodyPart(body.assemblyContext, body)
                if found and found.isValid:
                    ref["path"] = found.fullPathName
                    ref["name"] = found.name
                    return found, True
        except Exception:
            pass
        _missing_tokens.add(token)     # don't search the whole design again this session
    return None, False


def resolve_manual(design, manual):
    """Resolve every item. Returns (index, missing, changed).

    `missing` lists {step, path, name} for refs that no longer exist.
    `changed` is True if any ref path was repaired and the manual should be saved.
    """
    index = path_index(design)
    missing = []
    changed = False
    for _, step in model.ordered_steps(manual):
        for item in step["items"]:
            occ, repaired = resolve(design, item["ref"], index)
            changed = changed or repaired
            if occ is None:
                missing.append({
                    "stepId": step["id"],
                    "step": step["title"],
                    "path": item["ref"].get("path", ""),
                    "name": item["ref"].get("name", ""),
                })
        # Explode moves hold their own refs; repair renamed paths there too.
        for ex in step.get("explodes", []):
            for part in ex["parts"]:
                _, repaired = resolve(design, part["ref"], index)
                changed = changed or repaired
    return index, missing, changed


def display_name(occ, fallback, short=True):
    if isinstance(occ, BodyPart):
        return occ.name                 # a split component's body: its own name
    return _display_name(occ, fallback, short)


def _display_name(occ, fallback, short=True):
    """Name to show for a part: a short hardware label ("M3x12 SHCS") when one is
    found in the component's name, description or part number, else `fallback`."""
    if occ is None:
        return fallback
    if short:
        global _labels_dirty
        comp = occ.component
        name = comp.name
        try:
            key = "{}|{}".format(comp.id, name)
        except Exception:
            key = name
        labels = _label_cache()
        if key not in labels:
            label = hardware.short_name(name)
            if label is None and hardware.needs_details(name):
                # The description / part number can take very long to read (seconds per document):
                # read them in the background (fill_pending) and show the plain name meanwhile.
                _pending[key] = comp
                return fallback
            labels[key] = label or ""
            _labels_dirty = True
        if labels[key]:
            return labels[key]
    return fallback


def bom_line(occ, ref_name, short=True):
    """(key, label, is_hardware) for a parts list: one line per component, labelled with the
    component's (short hardware) name, not the instance's ("KP001_12", not "KP001_12:4")."""
    if occ is None:
        base = re.sub(r":\d+$", "", ref_name or "")
        return base, base, hardware.short_name(base) is not None
    if isinstance(occ, BodyPart):         # a split component's body: one line per body name
        return component_key(occ.component) + "#" + occ.name, occ.name, hardware.short_name(occ.name) is not None
    comp = occ.component
    try:
        key = "{}|{}".format(comp.id, comp.name)
    except Exception:
        key = comp.name
    label = display_name(occ, comp.name, short)
    is_hw = bool(_label_cache().get(key)) or hardware.short_name(comp.name) is not None
    return key, label, is_hw


def unassigned(manual, index):
    """Leaf occurrences not covered by any step item (self or ancestor), sorted by name."""
    covered = model.covered_paths(manual)
    short = manual["settings"].get("shortHardwareNames", True)
    out = []
    for path, occ in index.items():
        if occ.childOccurrences.count or is_split(occ):
            continue
        if model.is_covered(covered, path):
            continue
        out.append({"path": path, "name": display_name(occ, occ.name, short), "component": occ.component.name})
    out.sort(key=lambda p: (hardware.natural_key(p["name"]), p["path"]))
    return out


def selected_occurrences(ui):
    """Occurrences in the active selection, deduplicated, in selection order."""
    out = []
    seen = set()
    for i in range(ui.activeSelections.count):
        ent = ui.activeSelections.item(i).entity
        occ = adsk.fusion.Occurrence.cast(ent)
        if occ is None:
            # A body/face/edge was picked: use the occurrence that owns it -- or, in a split
            # component, that body.
            ctx = getattr(ent, "assemblyContext", None)
            occ = adsk.fusion.Occurrence.cast(ctx) if ctx else None
            body = adsk.fusion.BRepBody.cast(ent) or getattr(ent, "body", None)
            if occ is not None and body is not None and is_split(occ):
                occ = BodyPart(occ, body)
        if occ is None:
            continue
        path = occ.fullPathName
        if path not in seen:
            seen.add(path)
            out.append(occ)
    return out


def parent_axes(occ):
    """World unit axes of the occurrence's parent component, or None for root."""
    parent = occ.assemblyContext
    if parent is None:
        return None
    _, x, y, z = parent.transform2.getAsCoordinateSystem()
    return (_vec(x), _vec(y), _vec(z))


def bbox_center(occ):
    box = occ.boundingBox
    a, b = box.minPoint, box.maxPoint
    return ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0, (a.z + b.z) / 2.0)


def _vec(v):
    return (v.x, v.y, v.z)
