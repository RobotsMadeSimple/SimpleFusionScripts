"""Bill of materials from the parts and the Browser+ folders. Pure Python (tests/test_bom.py).

One row per component (quantity = how many times it's used), for parts with
bodies and no child parts, and one row per assembly (a motor, a bought module):
an assembly counts as one part, its insides aren't listed, unless it's split
(layout "split"), then its parts are listed (and its sub-assemblies count as one).
Grouped, the sections follow the folders (a part inside a subassembly counts
in its subassembly's folder), then Hardware, then parts in no folder.
"""

import csv
import io

from . import hardware, layout as layout_mod

BUILTIN = [
    {"id": "qty", "name": "Qty"},
    {"id": "name", "name": "Name"},
    {"id": "partNumber", "name": "Part number"},
    {"id": "description", "name": "Description"},
    {"id": "material", "name": "Material"},
    {"id": "mass", "name": "Mass (kg)"},
]
NO_FOLDER = "Global"             # section for parts in no folder


def _row_key(row):
    return hardware.natural_key(row["hw"] or row["name"])


def counted(parts, split):
    """The parts that are BOM lines (see the module docstring)."""
    roots = layout_mod.one_part_roots(parts, split)
    out = []
    for part in parts:
        root = roots.get(part["path"])
        if root == part["path"] or (root is None and part.get("leaf") and part.get("bodies")):
            out.append(part)
    return out


def build(parts, placement, folders, info, layout, group=True):
    """parts: [{"path", "componentId", "leaf", "bodies"}]; placement from layout.place;
    folders from layout.folders_view; info: {componentId: {"name", "partNumber",
    "description", "material", "mass" (kg or None), "hw"}}; layout: for your columns.

    Returns {"columns", "sections": [{"id", "name", "rows"}], "totalQty", "unique"}; each row
    {"componentId", "name", "hw", "partNumber", "description", "material", "mass", "qty",
    "paths", "values"}.
    """
    sections = {}
    for part in counted(parts, layout.get("split")):
        folder_id = layout_mod.effective_folder(part["path"], placement) if group else "all"
        comp = part.get("componentId") or part["path"]
        rows = sections.setdefault(folder_id, {})
        row = rows.get(comp)
        if row is None:
            meta = info.get(comp, {})
            row = rows[comp] = {
                "componentId": comp,
                "name": meta.get("name") or part.get("component") or part["path"].split("+")[-1].split(":")[0],
                "hw": meta.get("hw"),
                "partNumber": meta.get("partNumber", ""),
                "description": meta.get("description", ""),
                "material": meta.get("material", ""),
                "mass": meta.get("mass"),
                "qty": 0,
                "paths": [],
                "values": dict(layout.get("values", {}).get(comp, {})),
            }
        row["qty"] += 1
        row["paths"].append(part["path"])

    order = _tree_order(folders)
    out = []
    for folder_id in order + [layout_mod.TOP, "all"]:
        rows = sections.get(folder_id)
        if not rows:
            continue
        name = ("All parts" if folder_id == "all" else NO_FOLDER if folder_id == layout_mod.TOP
                else layout_mod.folder_trail(folder_id, folders))
        out.append({"id": folder_id, "name": name, "rows": sorted(rows.values(), key=_row_key)})
    return {
        "columns": BUILTIN + [dict(c, custom=True) for c in layout.get("columns", [])],
        "sections": out,
        "totalQty": sum(r["qty"] for s in out for r in s["rows"]),
        "unique": len({r["componentId"] for s in out for r in s["rows"]}),
    }


def _tree_order(folders):
    """Folder ids depth first (each folder, then its sub-folders), your folders before Hardware."""
    children = {}
    for f in folders:
        children.setdefault(f["parent"], []).append(f)
    out = []

    def walk(parent):
        for f in children.get(parent, []):
            out.append(f["id"])
            walk(f["id"])

    walk(None)
    return out


def _cell(row, column):
    cid = column["id"]
    if column.get("custom"):
        return row["values"].get(cid, "")
    if cid == "name":
        return row["hw"] + " (" + row["name"] + ")" if row["hw"] and row["hw"] != row["name"] else row["name"]
    if cid == "mass":
        return "" if row["mass"] is None else "{:.4g}".format(row["mass"])
    return row.get(cid, "")


def to_csv(bom, grouped=True):
    """CSV text (Excel / Sheets): a Section column when grouped, then every column."""
    columns = [c for c in bom["columns"]
               if c["id"] != "mass" or any(r["mass"] is not None for s in bom["sections"] for r in s["rows"])]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow((["Section"] if grouped else []) + [c["name"] for c in columns])
    for section in bom["sections"]:
        for row in section["rows"]:
            writer.writerow(([section["name"]] if grouped else []) + [_cell(row, c) for c in columns])
    return buf.getvalue()
