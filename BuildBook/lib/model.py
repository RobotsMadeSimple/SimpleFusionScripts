"""Build manual data model: Manual -> Sections -> Steps -> Items.

Pure Python (no adsk import) so it can be unit-tested outside Fusion. The
manual is stored as one JSON attribute on the design, so it travels with the
.f3d and survives edits to the geometry.

Items reference occurrences by `path` (Occurrence.fullPathName, e.g.
"Frame:1+Roller:2") with `token` (entityToken) as a rename-proof fallback.

A step's `items` say which parts belong to it. How they come apart is the
step's `explodes`: an ordered list of explode moves, each moving a group of
parts along one direction. Moves run in order and chain: a part in moves 1
and 2 goes move 1's way, then move 2's way from where it landed.

An explode move has a direction spec (an axis, X/Y/Z amounts, or a picked
edge/face/axis kept as its world direction plus an entity token so it can be
re-selected), a distance (None = the step default, else the manual default),
a spacing (uniform or stacked) and its parts. Each part may have its own
distance and its own trail line. Directions are world vectors. Lengths are
centimetres, Fusion's internal unit.
"""

import json
import math
import re
import uuid

SCHEMA = 5
PATH_SEP = "+"

# Context display modes for parts outside the current step.
SHOWN = "shown"
GHOSTED = "ghosted"
HIDDEN = "hidden"
CONTEXT_MODES = (SHOWN, GHOSTED, HIDDEN)
INHERIT = "inherit"

# Unassigned parts can follow the "earlier" rule or have a fixed mode.
AS_EARLIER = "asEarlier"

# Per-leaf states within a step view.
CURRENT = "current"
EARLIER = "earlier"
LATER = "later"
UNASSIGNED = "unassigned"

# Explode move directions and spacing.
AXIS_VECTORS = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}
DIR_AXIS = "axis"        # one of AXIS_VECTORS
DIR_XYZ = "xyz"          # `vector` holds X/Y/Z amounts (cm); its length is the distance
DIR_ENTITY = "entity"    # `vector` is a picked edge/face/axis direction; `token` re-selects it
UNIFORM = "uniform"
STACKED = "stacked"                      # 1x, 2x, 3x... ordered by position along the direction
STACKED_SELECTION = "stackedSelection"   # 1x, 2x, 3x... in pick order
SPACINGS = (UNIFORM, STACKED, STACKED_SELECTION)


def new_id():
    return uuid.uuid4().hex[:10]


def default_settings():
    return {
        "earlier": SHOWN,
        "later": HIDDEN,
        "unassigned": HIDDEN,          # parts in no step stay out of step views
        "defaultDistance": 5.0,        # cm; explode distance when a step has none
        "askFolder": False,            # ask for the export folder on every export
        "showCropFrame": False,        # draw the crop-ratio frame in the canvas
        "shortHardwareNames": True,    # show "M3x12 SHCS" instead of long fastener names
        "ghostOpacity": 0.2,
        "drawEdges": True,
        "trail": {
            "color": "#404040",
            "weight": 1.5,
            "style": "dashed",
            "scale": 1.0,
        },
        "annotation": {                # style for new annotations (palette/annot_draw.js)
            "color": "#d9463e", "weight": 3, "dashed": False, "size": 28, "bold": False, "box": False,
        },
        "nameSource": "name",          # name | partNumber | description
        "partLabels": {},              # component key -> display label override
        "image": {
            "width": 1600,
            "height": 1200,
            "transparent": False,
            "visualStyle": "shadedWithVisibleEdges",
            "ratio": "viewport",       # exported area: centred crop of this ratio (lib/crop.py RATIOS)
        },
    }


def new_item(ref):
    # anchor: where the part's trail lines start, in its own component
    # coordinates; None = the centre of its bounding box.
    return {"ref": dict(ref), "anchor": None}


def new_direction(axis="+Z"):
    return {"kind": DIR_AXIS, "axis": axis, "vector": None, "token": None, "flip": False}


def new_explode(direction=None, spacing=UNIFORM):
    return {
        "id": new_id(),
        "name": "",
        "direction": dict(direction) if direction else new_direction(),
        "distance": None,       # cm; None = step default
        "spacing": spacing,
        "parts": [],
    }


def new_explode_part(ref, distance=None, trail=True):
    return {"ref": dict(ref), "distance": distance, "trail": bool(trail)}


def new_step(title="New step"):
    return {
        "id": new_id(),
        "title": title,
        "notes": "",
        "items": [],
        "earlier": INHERIT,
        "later": INHERIT,
        "distance": None,       # cm; None = manual default
        "prep": False,          # preparation step: later steps treat its parts as unassigned
        "explodes": [],         # ordered explode moves (see module docstring)
        "camera": None,
        "annotations": [],      # drawn on the step's picture (palette/annot_draw.js)
    }


def new_picture(enabled=False):
    """A picture that isn't a step (the cover, a section): its own view and annotations.
    A section's shows the assembly as it stands at the end of the section, nothing exploded;
    the cover's shows the whole assembly."""
    return {"enabled": enabled, "camera": None, "annotations": []}


def new_section(title="New section"):
    return {"id": new_id(), "title": title, "steps": [], "image": new_picture()}


def new_manual(title="Build manual"):
    return {
        "schema": SCHEMA,
        "title": title,
        "sections": [],
        "settings": default_settings(),
        "cover": new_picture(enabled=True),
    }


def _merge_defaults(target, defaults):
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target[key], dict) and key != "partLabels":
            _merge_defaults(target[key], value)


def from_json(text):
    """Parse a stored manual, filling in any settings added since it was saved."""
    if not text:
        return new_manual()
    data = json.loads(text)
    data.setdefault("schema", 1)          # the first format had no schema number
    data.setdefault("title", "Build manual")
    data.setdefault("sections", [])
    data.setdefault("settings", {})
    if data["schema"] < 3 and data["settings"].get("unassigned") == AS_EARLIER:
        # Before schema 3 "as earlier" was only the default, not a choice.
        data["settings"]["unassigned"] = HIDDEN
    _merge_defaults(data["settings"], default_settings())
    for key, value in new_picture(enabled=True).items():
        data.setdefault("cover", {}).setdefault(key, value)
    for section in data["sections"]:
        section.setdefault("steps", [])
        for key, value in new_picture().items():
            section.setdefault("image", {}).setdefault(key, value)
        for step in section["steps"]:
            template = new_step()
            for key, value in template.items():
                step.setdefault(key, value)
            if data["schema"] < 5:
                for item in step["items"]:
                    _upgrade_item(item)
                _moves_to_explodes(data, step)
            for item in step["items"]:
                item.setdefault("anchor", None)
            step.pop("lastDir", None)
            step.pop("crop", None)       # per-step crops were replaced by the manual's crop ratio
            for ex in step["explodes"]:
                ex.setdefault("name", "")
                ex.setdefault("distance", None)
                ex.setdefault("spacing", UNIFORM)
                ex.setdefault("parts", [])
                for key, value in new_direction().items():
                    ex.setdefault("direction", new_direction()).setdefault(key, value)
                for part in ex["parts"]:
                    part.setdefault("distance", None)
                    part.setdefault("trail", True)
    data["schema"] = SCHEMA
    return data


def _moves_to_explodes(manual, step):
    """Schema 4 kept an ordered list of moves on each item; group them into explode moves.

    Moves with the same position in their part's list and the same direction
    become one explode move. A move that didn't follow the default (its own
    distance, or stacked) keeps its exact length as the part's own distance.
    """
    groups = {}
    order = []
    for item in step["items"]:
        for k, move in enumerate(item.pop("moves", []) or []):
            unit = [round(c, 4) for c in move["dir"]]
            key = (k, tuple(unit))
            if key not in groups:
                axis = next((name for name, vec in AXIS_VECTORS.items() if list(vec) == unit), None)
                direction = new_direction(axis) if axis else {
                    "kind": DIR_ENTITY, "axis": None, "vector": list(move["dir"]), "token": None, "flip": False}
                groups[key] = new_explode(direction)
                order.append(key)
            stack = move.get("stack", 1)
            if move.get("distance") is None and stack == 1:
                distance = None
            else:
                base = move["distance"] if move.get("distance") is not None else step_distance(manual, step)
                distance = base * stack
            groups[key]["parts"].append(new_explode_part(item["ref"], distance, move.get("trail", True)))
    order.sort(key=lambda key: key[0])     # stable: first moves first, in first-seen order
    step["explodes"] = [groups[key] for key in order]


def _upgrade_item(item):
    """Bring a pre-schema-5 item up to schema 4's moves list (then grouped into explodes).

    Schema 1 stored a raw `offset` vector; schemas 2-3 stored a single
    dir/distance/stack plus an item-level `trail`. Both become one move.
    """
    trail = item.pop("trail", True)
    if "moves" not in item:
        item["moves"] = []
        offset = item.pop("offset", None)
        if offset is not None and _length(offset) > 1e-9:
            n = _length(offset)
            item["moves"].append({"dir": [c / n for c in offset], "distance": n, "stack": 1, "trail": trail})
        elif item.get("dir"):
            item["moves"].append({"dir": item["dir"], "distance": item.get("distance"),
                                  "stack": item.get("stack", 1), "trail": trail})
    for key in ("dir", "distance", "stack", "offset"):
        item.pop(key, None)
    for move in item["moves"]:
        move.setdefault("distance", None)
        move.setdefault("stack", 1)
        move.setdefault("trail", True)


def _length(v):
    return math.sqrt(sum(c * c for c in v))


def to_json(manual):
    return json.dumps(manual, separators=(",", ":"))


# ---------------------------------------------------------------- lookups

def ordered_steps(manual):
    """[(section, step), ...] in manual order."""
    return [(sec, step) for sec in manual["sections"] for step in sec["steps"]]


def find_section(manual, section_id):
    for sec in manual["sections"]:
        if sec["id"] == section_id:
            return sec
    return None


def find_step(manual, step_id):
    """(section, step) or (None, None)."""
    for sec in manual["sections"]:
        for step in sec["steps"]:
            if step["id"] == step_id:
                return sec, step
    return None, None


def step_index(manual, step_id):
    for i, (_, step) in enumerate(ordered_steps(manual)):
        if step["id"] == step_id:
            return i
    return -1


def context_modes(manual, step):
    """Effective (earlier, later, unassigned) display modes for a step."""
    settings = manual["settings"]
    earlier = step.get("earlier", INHERIT)
    later = step.get("later", INHERIT)
    if earlier not in CONTEXT_MODES:
        earlier = settings["earlier"]
    if later not in CONTEXT_MODES:
        later = settings["later"]
    unassigned = settings.get("unassigned", AS_EARLIER)
    if unassigned not in CONTEXT_MODES:
        unassigned = earlier
    return earlier, later, unassigned


# ---------------------------------------------------------------- naming

_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def step_numbers(manual, step_id):
    """(section number, step number within its section), both 1-based, or (0, 0)."""
    for si, sec in enumerate(manual["sections"], 1):
        for ti, step in enumerate(sec["steps"], 1):
            if step["id"] == step_id:
                return si, ti
    return 0, 0


def safe_filename(text):
    """Drop characters Windows/macOS don't allow in file names."""
    return _BAD_FILENAME_CHARS.sub("", text).strip(" .")


def image_name(manual, step):
    """"Section 1 - Step 2 - Step name.png" for a step's exported image."""
    si, ti = step_numbers(manual, step["id"])
    title = safe_filename(step["title"].strip()) or "Untitled"
    return "Section {} - Step {} - {}.png".format(si, ti, title)


# ---------------------------------------------------------------- explode moves

def step_distance(manual, step):
    """The step's default explode distance (cm), falling back to the manual's."""
    d = step.get("distance")
    return d if d is not None else manual["settings"]["defaultDistance"]


def direction_unit(direction):
    """World unit vector of a direction spec (flip applied), or (0, 0, 0)."""
    kind = direction.get("kind", DIR_AXIS)
    if kind == DIR_AXIS:
        vec = AXIS_VECTORS.get(direction.get("axis") or "+Z", AXIS_VECTORS["+Z"])
    else:
        vec = tuple(direction.get("vector") or (0.0, 0.0, 0.0))
    n = _length(vec)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    sign = -1.0 if direction.get("flip") else 1.0
    return tuple(sign * c / n for c in vec)


def direction_label(direction):
    """Short text for a direction: "+Z", "X/Y/Z", "Along edge"."""
    kind = direction.get("kind", DIR_AXIS)
    if kind == DIR_AXIS:
        axis = direction.get("axis") or "+Z"
        if direction.get("flip"):
            axis = ("-" if axis[0] == "+" else "+") + axis[1:]
        return axis
    if kind == DIR_XYZ:
        return "X/Y/Z"
    return "Picked direction" if direction.get("token") else "Custom direction"


def explode_distance(manual, step, ex):
    """Base distance (cm) of an explode move before per-part overrides and stacking."""
    if ex["direction"].get("kind") == DIR_XYZ:
        return _length(ex["direction"].get("vector") or (0.0, 0.0, 0.0))
    return ex["distance"] if ex.get("distance") is not None else step_distance(manual, step)


def explode_label(step, ex):
    if ex.get("name"):
        return ex["name"]
    return "Move {}".format(step["explodes"].index(ex) + 1)


def find_explode(step, explode_id):
    for ex in step.get("explodes", []):
        if ex["id"] == explode_id:
            return ex
    return None


def find_explode_part(ex, path):
    for part in ex["parts"]:
        if part["ref"].get("path") == path:
            return part
    return None


def evaluate(manual, step, centers, upto=None):
    """Run a step's explode moves in order.

    `centers` maps part path -> world centre at home (for stacked ordering);
    parts missing from it are skipped. Stops after the move with id `upto`.
    Returns path -> [(world vector, trail, explode id), ...].
    """
    moves = {}
    offsets = {}
    for ex in step.get("explodes", []):
        unit = direction_unit(ex["direction"])
        if _length(unit) > 0:
            base = explode_distance(manual, step, ex)
            parts = [p for p in ex["parts"] if p["ref"].get("path") in centers]
            for part, factor in zip(parts, _spacing_factors(ex, parts, centers, offsets, unit)):
                path = part["ref"]["path"]
                d = part["distance"] if part.get("distance") is not None else base * factor
                vec = tuple(c * d for c in unit)
                moves.setdefault(path, []).append((vec, part.get("trail", True), ex["id"]))
                o = offsets.get(path, (0.0, 0.0, 0.0))
                offsets[path] = (o[0] + vec[0], o[1] + vec[1], o[2] + vec[2])
        if ex["id"] == upto:
            break
    return moves


def _spacing_factors(ex, parts, centers, offsets, unit):
    spacing = ex.get("spacing", UNIFORM)
    if spacing == UNIFORM:
        return [1] * len(parts)
    if spacing == STACKED_SELECTION:
        return list(range(1, len(parts) + 1))
    # Stacked by position: where each part is now (after earlier moves), along the direction.
    def along(part):
        path = part["ref"]["path"]
        c = centers[path]
        o = offsets.get(path, (0.0, 0.0, 0.0))
        return (c[0] + o[0]) * unit[0] + (c[1] + o[1]) * unit[1] + (c[2] + o[2]) * unit[2]
    order = sorted(range(len(parts)), key=lambda i: along(parts[i]))
    factors = [0] * len(parts)
    for rank, i in enumerate(order):
        factors[i] = rank + 1
    return factors


def explode_paths(step):
    """Every part path that some explode move of the step moves."""
    return {p["ref"].get("path") for ex in step.get("explodes", []) for p in ex["parts"]}


def set_explode_parts(step, ex, refs):
    """Make an explode move's parts exactly `refs`, keeping per-part settings of those that stay.

    Parts not yet in the step are added to it.
    """
    old = {p["ref"].get("path"): p for p in ex["parts"]}
    ex["parts"] = [old.get(r["path"]) or new_explode_part(r) for r in refs]
    add_items(step, refs)


# ---------------------------------------------------------------- editing

def add_section(manual, title=None):
    sec = new_section(title or "Section {}".format(len(manual["sections"]) + 1))
    manual["sections"].append(sec)
    return sec


def add_step(manual, section_id, title=None):
    sec = find_section(manual, section_id)
    if sec is None:
        return None
    count = len(ordered_steps(manual))
    step = new_step(title or "Step {}".format(count + 1))
    sec["steps"].append(step)
    return step


def delete_section(manual, section_id):
    manual["sections"] = [s for s in manual["sections"] if s["id"] != section_id]


def delete_step(manual, step_id):
    for sec in manual["sections"]:
        sec["steps"] = [s for s in sec["steps"] if s["id"] != step_id]


def move_section(manual, section_id, delta):
    secs = manual["sections"]
    for i, sec in enumerate(secs):
        if sec["id"] == section_id:
            j = max(0, min(len(secs) - 1, i + delta))
            secs.insert(j, secs.pop(i))
            return True
    return False


def move_step(manual, step_id, target_section_id=None, index=None, delta=None):
    """Move a step by `delta` within the flat order, or to (section, index).

    With `delta`, crossing a section boundary moves the step into the
    neighbouring section, so up/down buttons walk through the whole manual.
    """
    sec, step = find_step(manual, step_id)
    if step is None:
        return False

    if delta is not None:
        secs = manual["sections"]
        si = secs.index(sec)
        pos = sec["steps"].index(step)
        new_pos = pos + delta
        if 0 <= new_pos < len(sec["steps"]):
            sec["steps"].insert(new_pos, sec["steps"].pop(pos))
            return True
        # Walk into the neighbouring section.
        ni = si + (1 if delta > 0 else -1)
        if not 0 <= ni < len(secs):
            return False
        sec["steps"].pop(pos)
        if delta > 0:
            secs[ni]["steps"].insert(0, step)
        else:
            secs[ni]["steps"].append(step)
        return True

    target = find_section(manual, target_section_id) if target_section_id else sec
    if target is None:
        return False
    sec["steps"].remove(step)
    if index is None or index > len(target["steps"]):
        index = len(target["steps"])
    target["steps"].insert(max(0, index), step)
    return True


def item_paths(step):
    return [item["ref"].get("path", "") for item in step["items"]]


def add_items(step, refs):
    """Add occurrence refs to a step, skipping ones already in it. Returns added count."""
    existing = set(item_paths(step))
    added = 0
    for ref in refs:
        if ref.get("path") in existing:
            continue
        step["items"].append(new_item(ref))
        existing.add(ref.get("path"))
        added += 1
    return added


def find_item(step, path):
    for item in step["items"]:
        if item["ref"].get("path") == path:
            return item
    return None


def remove_items(step, paths):
    """Take parts out of a step, and out of its explode moves."""
    drop = set(paths)
    step["items"] = [i for i in step["items"] if i["ref"].get("path") not in drop]
    for ex in step.get("explodes", []):
        ex["parts"] = [p for p in ex["parts"] if p["ref"].get("path") not in drop]


# ---------------------------------------------------------------- path logic

def is_self_or_ancestor(candidate, path):
    """True if occurrence path `candidate` is `path` or one of its ancestors."""
    return path == candidate or path.startswith(candidate + PATH_SEP)


def ancestor_items(step, path, strict=False):
    """Items in `step` that are `path` or an ancestor of it, outermost first."""
    found = []
    for item in step["items"]:
        p = item["ref"].get("path", "")
        if not p:
            continue
        if strict and p == path:
            continue
        if is_self_or_ancestor(p, path):
            found.append(item)
    found.sort(key=lambda i: i["ref"]["path"].count(PATH_SEP))
    return found


def leaf_state(manual, current_step_id, path):
    """Classify an occurrence path relative to a step: current/earlier/later/unassigned.

    A part that so far has only been in preparation steps counts as
    unassigned (not yet installed); an ordinary earlier step installs it.
    """
    steps = ordered_steps(manual)
    cur = step_index(manual, current_step_id)
    state = UNASSIGNED
    prepared = False
    for i, (_, step) in enumerate(steps):
        if not ancestor_items(step, path):
            continue
        if i == cur:
            return CURRENT
        if i < cur:
            if step.get("prep"):
                prepared = True
            else:
                state = EARLIER
        elif state == UNASSIGNED:
            state = LATER
    if prepared and state != EARLIER:
        return UNASSIGNED
    return state


def covered_paths(manual):
    """Every item path in the manual (for unassigned-part detection)."""
    return {p for _, step in ordered_steps(manual) for p in item_paths(step) if p}


def is_covered(covered, path):
    return any(is_self_or_ancestor(c, path) for c in covered)
