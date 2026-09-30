"""Browser+ folders: a view of the design's parts that never changes the design itself.

Pure Python (tests/test_layout.py). The layout is kept as JSON in the design's
attributes (group "BrowserPlus", name "layout"), so it travels with the file.

    {
      "version": 1,
      "folders": [{"id": "f1", "name": "Frame", "parent": null}, ...],   # list order = display order
      "items": {"<occurrence path>": {"folder": "f1", "token": "<entity token>"}},
      "hardwareAuto": true,
      "columns": [{"id": "c1", "name": "Vendor"}],                      # BOM columns of your own
      "values": {"<component id>": {"c1": "McMaster"}},                  # their values, per component
      "split": ["<component id>"]      # assemblies listed piece by piece in the BOM (the rest count as one part)
    }

Where a part shows (`place`):
1. an explicit item puts it in that folder ("" = top level), pulled out of its parent assembly;
2. else, with hardwareAuto on, a hardware part with no child parts goes in the automatic
   Hardware folder, one sub-folder per kind ("hw:screws", ...);
3. else a top-level part is at the top level, and a nested part stays under its parent.
An assembly counts as one part (a motor, a bought module) unless you split it; parts inside
one that counts as one aren't pulled into Hardware (they aren't BOM lines either).
Folders deleted from under items drop those items back to rule 2/3.
"""

import json

from . import hardware

PATH_SEP = "+"
HW_ROOT = "hw"
TOP = ""                     # the top level (no folder)
VERSION = 1


def empty():
    return {"version": VERSION, "folders": [], "items": {}, "hardwareAuto": True, "columns": [], "values": {},
            "split": []}


def load(text):
    """Layout from stored JSON; anything missing or broken falls back to the defaults."""
    layout = empty()
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    if isinstance(data, dict):
        for key, default in empty().items():
            value = data.get(key, default)
            if isinstance(value, type(default)):
                layout[key] = value
    layout["folders"] = [f for f in layout["folders"] if isinstance(f, dict) and f.get("id")]
    return layout


def dump(layout):
    return json.dumps(layout, separators=(",", ":"), sort_keys=True)


def parent_path(path):
    return path.rsplit(PATH_SEP, 1)[0] if PATH_SEP in path else None


def is_auto(folder_id):
    return folder_id == HW_ROOT or folder_id.startswith(HW_ROOT + ":")


# ------------------------------------------------------------ folders

def _ids(layout):
    return {f["id"] for f in layout["folders"]}


def folder(layout, folder_id):
    return next((f for f in layout["folders"] if f["id"] == folder_id), None)


def new_id(layout, prefix):
    taken = _ids(layout) | {c["id"] for c in layout["columns"]}
    n = 1
    while "{}{}".format(prefix, n) in taken:
        n += 1
    return "{}{}".format(prefix, n)


def add_folder(layout, name, parent=None):
    if parent is not None and folder(layout, parent) is None:
        parent = None
    fid = new_id(layout, "f")
    layout["folders"].append({"id": fid, "name": (name or "New folder").strip() or "New folder", "parent": parent})
    return fid


def rename_folder(layout, folder_id, name):
    f = folder(layout, folder_id)
    if f is not None and name and name.strip():
        f["name"] = name.strip()


def delete_folder(layout, folder_id):
    """Remove a folder; its sub-folders and parts move up to its parent."""
    f = folder(layout, folder_id)
    if f is None:
        return
    for child in layout["folders"]:
        if child.get("parent") == folder_id:
            child["parent"] = f.get("parent")
    for item in layout["items"].values():
        if item.get("folder") == folder_id:
            item["folder"] = f.get("parent") or TOP
    layout["folders"].remove(f)


def descendants(layout, folder_id):
    out, todo = set(), [folder_id]
    while todo:
        current = todo.pop()
        for f in layout["folders"]:
            if f.get("parent") == current and f["id"] not in out:
                out.add(f["id"])
                todo.append(f["id"])
    return out


def move_folder(layout, folder_id, parent=None, before=None):
    """Put a folder under `parent` (None = top level), just before sibling `before` (else last).
    Moving a folder into itself or its own sub-folder is ignored."""
    f = folder(layout, folder_id)
    if f is None or parent == folder_id or (parent is not None and parent in descendants(layout, folder_id)):
        return False
    if parent is not None and folder(layout, parent) is None:
        return False
    layout["folders"].remove(f)
    f["parent"] = parent
    index = next((i for i, g in enumerate(layout["folders"]) if g["id"] == before), len(layout["folders"]))
    layout["folders"].insert(index, f)
    return True


def assign(layout, paths, folder_id, tokens=None):
    """Put parts in a folder (TOP = top level). None puts them back where they'd be by default."""
    tokens = tokens or {}
    if folder_id is not None and folder_id != TOP and (is_auto(folder_id) or folder(layout, folder_id) is None):
        return False
    for path in paths:
        if folder_id is None:
            layout["items"].pop(path, None)
        else:
            layout["items"][path] = {"folder": folder_id, "token": tokens.get(path, "")}
    return True


def heal(layout, current_paths, resolve):
    """Re-key items whose part path changed (renamed component, moved part): `resolve(token)`
    gives the part's path now, or None. Items that can't be found are kept (the part may be
    back later, e.g. after undo). Returns True if anything changed."""
    changed = False
    for path in [p for p in layout["items"] if p not in current_paths]:
        item = layout["items"][path]
        new = resolve(item.get("token")) if item.get("token") else None
        if new and new in current_paths and new not in layout["items"]:
            layout["items"][new] = layout["items"].pop(path)
            changed = True
    return changed


# ------------------------------------------------------------ placement

def one_part_roots(parts, split):
    """{path: path of the outermost assembly (self included) that counts as one part} for parts
    in or at one. An assembly (a part with parts in it, some with bodies) counts as one part
    unless its component is in `split`."""
    split = set(split or [])
    by_path = {p["path"]: p for p in parts}
    solid = set()                   # assemblies with bodies somewhere inside
    for p in parts:
        if p.get("bodies"):
            bits = p["path"].split(PATH_SEP)
            solid.update(PATH_SEP.join(bits[:i]) for i in range(1, len(bits)))
    out = {}
    for path in by_path:
        bits = path.split(PATH_SEP)
        for i in range(1, len(bits) + 1):
            candidate = PATH_SEP.join(bits[:i])
            part = by_path.get(candidate)
            if (part is not None and not part.get("leaf") and candidate in solid
                    and part.get("componentId") not in split):
                out[path] = candidate
                break
    return out


def set_split(layout, component_id, on):
    """List an assembly piece by piece in the BOM (on) or count it as one part again (off)."""
    ids = [c for c in layout.get("split", []) if c != component_id]
    if on and component_id:
        ids.append(component_id)
    layout["split"] = ids


def place(parts, layout):
    """{path: {"folder": id} or {"under": parent path}} for every part; placed by you adds
    "explicit": True, by the Hardware rule "auto": True.

    parts: [{"path", "hw" (short hardware name or None), "leaf" (no child parts)}].
    """
    ids = _ids(layout)
    known = {p["path"] for p in parts}
    roots = one_part_roots(parts, layout.get("split"))
    out = {}
    for part in parts:
        path = part["path"]
        item = layout["items"].get(path)
        if item is not None and (item.get("folder") == TOP or item.get("folder") in ids):
            out[path] = {"folder": item["folder"], "explicit": True}
            continue
        inside_one = roots.get(path, path) != path
        cat = (hardware.category(part.get("hw"))
               if layout.get("hardwareAuto") and part.get("leaf") and not inside_one else None)
        if cat:
            out[path] = {"folder": HW_ROOT + ":" + cat, "auto": True}
            continue
        parent = parent_path(path)
        out[path] = {"under": parent} if parent in known else {"folder": TOP}
    return out


def effective_folder(path, placement):
    """The folder a part ends up in (following "under" up to a placed ancestor)."""
    seen = set()
    while path is not None and path not in seen:
        seen.add(path)
        spot = placement.get(path)
        if spot is None:
            return TOP
        if "folder" in spot:
            return spot["folder"]
        path = spot["under"]
    return TOP


def folders_view(layout, placement):
    """Folders for the panel: yours (in order), then the automatic Hardware folders in use."""
    out = [{"id": f["id"], "name": f["name"], "parent": f.get("parent"), "auto": False} for f in layout["folders"]]
    used = {spot["folder"] for spot in placement.values() if is_auto(spot.get("folder", ""))}
    if used:
        out.append({"id": HW_ROOT, "name": "Hardware", "parent": None, "auto": True})
        for cat, label in hardware.CATEGORIES:
            if HW_ROOT + ":" + cat in used:
                out.append({"id": HW_ROOT + ":" + cat, "name": label, "parent": HW_ROOT, "auto": True})
    return out


def folder_trail(folder_id, folders):
    """"Frame › Brackets" for a folder id (folders as from folders_view)."""
    by_id = {f["id"]: f for f in folders}
    names, seen = [], set()
    while folder_id and folder_id in by_id and folder_id not in seen:
        seen.add(folder_id)
        names.append(by_id[folder_id]["name"])
        folder_id = by_id[folder_id]["parent"]
    return " › ".join(reversed(names))


# ------------------------------------------------------------ BOM columns

def add_column(layout, name):
    cid = new_id(layout, "c")
    layout["columns"].append({"id": cid, "name": (name or "Column").strip() or "Column"})
    return cid


def rename_column(layout, column_id, name):
    for c in layout["columns"]:
        if c["id"] == column_id and name and name.strip():
            c["name"] = name.strip()


def delete_column(layout, column_id):
    layout["columns"] = [c for c in layout["columns"] if c["id"] != column_id]
    for values in layout["values"].values():
        values.pop(column_id, None)


def set_value(layout, component_id, column_id, value):
    values = layout["values"].setdefault(component_id, {})
    if value:
        values[column_id] = value
    else:
        values.pop(column_id, None)
        if not values:
            layout["values"].pop(component_id, None)
