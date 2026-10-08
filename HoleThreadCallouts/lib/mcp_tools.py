"""The tools Hole & Thread Callouts offers agents over MCP (lib/mcp_server.py), run on Fusion's main thread.

A part is an occurrence path from list_parts ("Servo Base:1", "Arm:1+Bracket:2"); "" is the root
component (a single-part design). Views, hole marks and callouts are the same ones the panel shows
for that component (kept in the design). While a tool looks at a part, only that part is shown
(optionally only some of its bodies); everything's visibility is put back afterwards.
"""

import base64
import json
import threading
import uuid
from contextlib import contextmanager

import adsk.core
import adsk.fusion

from . import holes as holes_mod, log, mcp_server

VIEWS = ["iso_top_right", "iso_top_left", "iso_bottom_right", "iso_bottom_left",
         "top", "bottom", "front", "back", "left", "right"]

PART = {"type": "string", "description": "An occurrence path from list_parts (\"\" = the root component)"}
BODIES = {"type": "array", "items": {"type": "string"},
          "description": "Only these bodies of the part (by name), e.g. one printed body of a multi-body "
                         "component (default: all of them)"}


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("list_parts", "Components with bodies in the design (one row each, with how many times it's used) and "
          "their threaded / dowel holes by size, e.g. {\"M3\": 6}.",
          {"filter": {"type": "string", "description": "Only names / paths containing this"}}),
    _tool("get_holes", "A part's threaded and dowel holes: each hole's id, size, diameter, and where the size "
          "came from (thread = modelled thread, tapped = tapped hole feature, guess = tap-drill diameter, "
          "manual = marked), plus a count per size.", {"part": PART, "bodies": BODIES}, ["part"]),
    _tool("list_views", "A part's views (thread views in export order, then the shaded view).",
          {"part": PART}, ["part"]),
    _tool("auto_views", "Set up thread views that between them show every threaded hole (picked from the "
          "standard and isometric views, fewest first), plus a shaded iso view at the end. Keeps the part's "
          "existing views unless replace is true.",
          {"part": PART, "bodies": BODIES,
           "replace": {"type": "boolean", "description": "Replace the part's existing views (default false)"},
           "shaded": {"type": "boolean", "description": "Add a shaded view at the end (default true)"}},
          ["part"]),
    _tool("add_view", "Add a view of the part from a standard direction (fitted to the part).",
          {"part": PART, "bodies": BODIES, "view": {"type": "string", "enum": VIEWS},
           "kind": {"type": "string", "enum": ["thread", "shaded"], "description": "Default thread"}},
          ["part", "view"]),
    _tool("delete_view", "Delete one of the part's views.",
          {"part": PART, "view_id": {"type": "string", "description": "From list_views"}}, ["part", "view_id"]),
    _tool("mark_holes", "Mark holes (ids from get_holes) with a size (\"M4\"), as a dowel, as not threaded "
          "(\"none\") or back to automatic (\"auto\").",
          {"part": PART, "hole_ids": {"type": "array", "items": {"type": "string"}},
           "as": {"type": "string", "description": "\"M3\", \"M4\", ..., \"dowel\", \"none\" or \"auto\""}},
          ["part", "hole_ids", "as"]),
    _tool("export_image", "Render the part's callout image (its views side by side, holes filled in their "
          "size's colour, one \"2x M3\" callout per size) and save it as a PNG. Sets up views first "
          "(auto_views) if the part has none. Returns the picture and the file's path.",
          {"part": PART, "bodies": BODIES,
           "path": {"type": "string", "description": "Where to save the PNG (default: the add-in's data folder, "
                                                     "exports/<design> - <part> threads.png)"},
           "height": {"type": "integer", "minimum": 100, "maximum": 4000,
                      "description": "Image height in px (default: the panel's setting)"}},
          ["part"]),
    _tool("reload_addin", "Reload the add-in's code from disk (after its files changed). Answers first, then "
          "reloads a moment later; the connection is back within a few seconds."),
]


def definitions():
    return TOOLS


# ---------------------------------------------------------------- results

def _text(value):
    text = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _fail(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ---------------------------------------------------------------- running a tool

def run(ctrl, name, args):
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return _fail("No tool called " + str(name))
    if ctrl.design() is None:
        return _fail("No design is open in Fusion.")
    return globals()["_" + name](ctrl, args)


def _occurrence(ctrl, path):
    if not path:
        return None
    for occ in ctrl.design().rootComponent.allOccurrences:
        if occ.fullPathName == path:
            return occ
    raise ValueError("No part {!r} in the design (see list_parts)".format(path))


@contextmanager
def _on(ctrl, path):
    """The controller working on part `path` (as if it were activated in Fusion)."""
    before = ctrl.target
    ctrl.target = _occurrence(ctrl, path)
    try:
        yield ctrl.target
    finally:
        ctrl.target = before


@contextmanager
def _isolated(ctrl, occ, body_names=None):
    """Only `occ` (and only `body_names` of its bodies) shown; visibility put back afterwards."""
    root = ctrl.design().rootComponent
    saved = []
    target = occ.fullPathName if occ is not None else None

    def keep(path):
        return target is None or path == target or target.startswith(path + "+") or path.startswith(target + "+")
    try:
        if target is not None:
            for body in root.bRepBodies:
                saved.append((body, body.isLightBulbOn))
                body.isLightBulbOn = False
        for o in root.allOccurrences:
            saved.append((o, o.isLightBulbOn))
            o.isLightBulbOn = keep(o.fullPathName)
        if body_names:
            names = set(body_names)
            mine = list(occ.bRepBodies) if occ is not None else list(root.bRepBodies)
            missing = names - {b.name for b in mine}
            if missing:
                raise ValueError("No bodies called {} in that part".format(", ".join(sorted(missing))))
            for body in mine:
                saved.append((body, body.isLightBulbOn))
                body.isLightBulbOn = body.name in names
        _settle(ctrl)
        yield
    finally:
        for entity, on in reversed(saved):
            try:
                entity.isLightBulbOn = on
            except Exception:
                pass
        ctrl.app.activeViewport.refresh()


def _settle(ctrl):
    viewport = ctrl.app.activeViewport
    for _ in range(3):
        viewport.refresh()
        adsk.doEvents()


def _found(ctrl):
    data = ctrl.load()
    return ctrl._find_holes(data, ctrl.part(data))


def _summary(found):
    out = {}
    for h in found:
        label = h["label"] + (" dowel" if h["kind"] == "dowel" else "")
        out[label] = out.get(label, 0) + 1
    return out


def _views():
    V = adsk.core.ViewOrientations
    return {"iso_top_right": V.IsoTopRightViewOrientation, "iso_top_left": V.IsoTopLeftViewOrientation,
            "iso_bottom_right": V.IsoBottomRightViewOrientation, "iso_bottom_left": V.IsoBottomLeftViewOrientation,
            "front": V.FrontViewOrientation, "back": V.BackViewOrientation, "left": V.LeftViewOrientation,
            "right": V.RightViewOrientation, "top": V.TopViewOrientation, "bottom": V.BottomViewOrientation}


def _look(ctrl, name):
    """Camera to a standard view, fitted to what's shown; returns the camera as the panel stores it."""
    viewport = ctrl.app.activeViewport
    cam = viewport.camera
    cam.viewOrientation = _views()[name]
    cam.isSmoothTransition = False
    viewport.camera = cam
    _settle(ctrl)
    viewport.fit()
    _settle(ctrl)
    return _camera_dict(ctrl)


MAIN = [None]           # the add-in's main module (Fusion loads it under a made-up name), set when MCP starts


def _m():
    """The main module: camera_to_dict / apply_camera / RELOAD_EVENT live with the controller."""
    return MAIN[0]


def _camera_dict(ctrl):
    return _m().camera_to_dict(ctrl.app.activeViewport.camera)


# ---------------------------------------------------------------- tools

def _list_parts(ctrl, args):
    design = ctrl.design()
    root = design.rootComponent
    text = (args.get("filter") or "").lower()
    rows = {}
    candidates = [("", None, root)] if root.bRepBodies.count else []
    candidates += [(o.fullPathName, o, o.component) for o in root.allOccurrences if o.component.bRepBodies.count]
    for path, occ, comp in candidates:
        if text and text not in (path + " " + comp.name).lower():
            continue
        key = comp.id + "|" + comp.name
        if key in rows:
            rows[key]["qty"] += 1
            continue
        with _on(ctrl, path):
            try:
                found = _found(ctrl)
            except Exception:
                log.error("mcp list_parts holes " + path)
                found = []
            data = ctrl.load()
            views = len([v for v in ctrl.part(data)["views"] if v["kind"] == "thread"])
        rows[key] = {"part": path, "component": comp.name, "qty": 1,
                     "bodies": [b.name for b in comp.bRepBodies], "holes": _summary(found), "thread_views": views}
    return _text(list(rows.values()))


def _get_holes(ctrl, args):
    with _on(ctrl, args.get("part")) as occ, _isolated(ctrl, occ, args.get("bodies")):
        found = _found(ctrl)
    holes = [{"id": h["id"], "kind": h["kind"], "size": h["label"], "source": h["source"],
              "diameter_mm": h["diameter"], "openings": len(h["openings"])} for h in found]
    return _text({"part": args.get("part"), "count_by_size": _summary(found), "holes": holes})


def _list_views(ctrl, args):
    with _on(ctrl, args.get("part")):
        part = ctrl.part(ctrl.load())
        out, n = [], 0
        for v in ctrl.ordered(part):
            n += v["kind"] == "thread"
            out.append({"view_id": v["id"], "kind": v["kind"],
                        "name": "View {}".format(n) if v["kind"] == "thread" else "Shaded",
                        "own_annotations": len([a for a in v.get("annotations", []) if not a.get("auto")])})
    return _text(out)


def _new_view(kind, camera):
    return {"id": uuid.uuid4().hex[:8], "kind": kind, "camera": camera, "annotations": []}


def _auto(ctrl, occ, replace=False, shaded=True):
    """Pick views that show every hole (greedy, isometric views first); returns (added, uncovered)."""
    viewport = ctrl.app.activeViewport
    camera, style = _camera_dict(ctrl), viewport.visualStyle
    data = ctrl.load()
    part = ctrl.part(data)
    found = ctrl._find_holes(data, part)
    threads = found
    bodies = holes_mod.bodies(ctrl.design(), ctrl.target)
    seen = {}
    try:
        for name in VIEWS:
            cam = _look(ctrl, name)
            projection = holes_mod.project(ctrl.design(), viewport, threads, bodies)
            seen[name] = (cam, {h["id"] for h in projection["holes"]})
        if replace:
            part["views"] = []
        covered = set()
        for v in part["views"]:                 # what the views kept already show
            if v["kind"] == "thread" and v.get("camera"):
                _m().apply_camera(viewport, v["camera"])
                _settle(ctrl)
                covered |= {h["id"] for h in holes_mod.project(ctrl.design(), viewport, threads, bodies)["holes"]}
        wanted = {h["id"] for h in threads}
        added = []
        while wanted - covered:
            best = max(VIEWS, key=lambda n: len(seen[n][1] & (wanted - covered)))
            gain = seen[best][1] & (wanted - covered)
            if not gain:
                break
            part["views"].append(_new_view("thread", seen[best][0]))
            added.append({"view": best, "holes": len(gain)})
            covered |= gain
        if shaded and not [v for v in part["views"] if v["kind"] == "shaded"]:
            part["views"].append(_new_view("shaded", seen["iso_top_right"][0]))
            added.append({"view": "iso_top_right", "kind": "shaded"})
        ctrl.save(data)
    finally:
        viewport.visualStyle = style
        _m().apply_camera(viewport, camera)
    return added, sorted(wanted - covered)


def _auto_views(ctrl, args):
    with _on(ctrl, args.get("part")) as occ, _isolated(ctrl, occ, args.get("bodies")):
        added, uncovered = _auto(ctrl, occ, bool(args.get("replace")), args.get("shaded", True))
    ctrl.push_state()
    out = {"added": added}
    if uncovered:
        out["not_visible_in_any_view"] = uncovered
    return _text(out)


def _add_view(ctrl, args):
    kind = "shaded" if args.get("kind") == "shaded" else "thread"
    viewport = ctrl.app.activeViewport
    with _on(ctrl, args.get("part")) as occ, _isolated(ctrl, occ, args.get("bodies")):
        before = _camera_dict(ctrl)
        try:
            camera = _look(ctrl, args["view"])
        finally:
            _m().apply_camera(viewport, before)
        data = ctrl.load()
        part = ctrl.part(data)
        if kind == "shaded":
            part["views"] = [v for v in part["views"] if v["kind"] != "shaded"]
        view = _new_view(kind, camera)
        part["views"].append(view)
        ctrl.save(data)
    ctrl.push_state()
    return _text({"view_id": view["id"], "kind": kind})


def _delete_view(ctrl, args):
    with _on(ctrl, args.get("part")):
        data = ctrl.load()
        part = ctrl.part(data)
        keep = [v for v in part["views"] if v["id"] != args.get("view_id")]
        if len(keep) == len(part["views"]):
            return _fail("No view {} (see list_views)".format(args.get("view_id")))
        part["views"] = keep
        ctrl.save(data)
    ctrl.push_state()
    return _text("Deleted view " + args["view_id"])


def _mark_holes(ctrl, args):
    mark = args.get("as")
    with _on(ctrl, args.get("part")):
        data = ctrl.load()
        part = ctrl.part(data)
        for key in args.get("hole_ids") or []:
            if mark in (None, "", "auto"):
                part["marks"].pop(key, None)
            else:
                part["marks"][key] = mark
        ctrl.save(data)
    ctrl.push_state()
    return _text("{} hole(s) marked {}".format(len(args.get("hole_ids") or []), mark))


def _export_image(ctrl, args):
    path_arg = args.get("part") or ""
    with _on(ctrl, path_arg) as occ, _isolated(ctrl, occ, args.get("bodies")):
        data = ctrl.load()
        if not [v for v in ctrl.part(data)["views"] if v["kind"] == "thread"]:
            _auto(ctrl, occ)
            data = ctrl.load()
        if not [v for v in ctrl.part(data)["views"] if v["kind"] == "thread"]:
            return _fail("The part has no threaded holes to show (see get_holes).")
        rendered = ctrl.render()
        found = _found(ctrl)
        comp_name = occ.component.name if occ is not None else ctrl.design().rootComponent.name
    height = int(args.get("height") or data["settings"].get("height", 900))
    job = {"views": [{"kind": r["view"]["kind"], "image": r["image"], "crop": r["crop"],
                      "annotations": r["annotations"]} for r in rendered],
           "height": height, "gap": int(data["settings"].get("gap", 24))}
    path = args.get("path")
    if not path:
        doc = ctrl.app.activeDocument
        name = "{} - {}{} threads.png".format(doc.name if doc else "design", comp_name,
                                               " (" + ", ".join(args["bodies"]) + ")" if args.get("bodies") else "")
        path = mcp_server.data_dir("exports", "".join(c for c in name if c not in '\\/:*?"<>|'))
    summary = _summary(found)

    def done(png, saved):
        note = "Saved {} ({} views, {:.0f} KB). Holes: {}".format(
            saved, len(job["views"]), len(png) / 1024.0,
            ", ".join("{}x {}".format(n, s) for s, n in summary.items()) or "none")
        return {"content": [{"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"},
                            {"type": "text", "text": note}]}
    deferred = mcp_server.Deferred(done)
    ctrl.stitch_for_agent(job, path, deferred)
    return deferred


def _reload_addin(ctrl, args):
    """Reload a moment after answering: the reload stops this server, so it mustn't happen while
    the answer is still being sent."""
    event = _m().RELOAD_EVENT

    def later():
        adsk.core.Application.get().fireCustomEvent(event, "")
    threading.Timer(0.8, later).start()
    log.info("mcp: reload asked for")
    return _text("Reloading Hole & Thread Callouts in a moment; call any tool again in a few seconds.")
