"""The tools BuildBook offers agents over MCP (lib/mcp_server.py), run on Fusion's main thread.

Reading and capturing are always there; editing tools only when "Allow changes" is on. Edits go
through the same actions as the panel, so they land in Ctrl+Z (undo) and the panel updates.
"""

import base64
import json
import os
import tempfile
import uuid

from . import capture, freshness, log, model, params, pictures, refs

AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
SPACINGS = [model.UNIFORM, model.STACKED, model.STACKED_REVERSE, model.STACKED_SELECTION]


def _tool(name, description, props=None, required=None, edit=False):
    return {"name": name, "description": description, "edit": edit,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


STEP_ID = {"type": "string", "description": "A step id (from list_steps)"}
WIDTH = {"type": "integer", "description": "Picture width in pixels (default 1200)", "minimum": 200, "maximum": 4000}
PATHS = {"type": "array", "items": {"type": "string"}, "description": "Part paths (from list_parts / get_step)"}
DISTANCE = {"type": "string", "description": "A distance: '25 mm', '1 in', a plain number in the design's units, "
                                            "or a Fusion parameter expression such as 'bb_gap * 2'"}

TOOLS = [
    _tool("list_steps", "Every section and step of the build manual, in order: ids, numbers, titles, part and move "
          "counts, whether a view is saved."),
    _tool("get_step", "One step in detail: its parts (paths and names) and explode moves.", {"step_id": STEP_ID}, ["step_id"]),
    _tool("get_manual", "The whole manual as JSON (sections, steps, moves, settings)."),
    _tool("list_parts", "Parts in the design (paths, names, components) and whether they're in a step yet.",
          {"filter": {"type": "string", "description": "Only names / paths containing this"},
           "unassigned_only": {"type": "boolean", "description": "Only parts not in any step yet"}}),
    _tool("capture_step", "A picture of a step as the manual shows it (exploded, trail lines, its crop).",
          {"step_id": STEP_ID, "width": WIDTH,
           "saved_view": {"type": "boolean", "description": "At the step's saved view (default true) or the current camera"}},
          ["step_id"]),
    _tool("capture_view", "A picture of the Fusion canvas as it is right now.", {"width": WIDTH}),
    _tool("capture_picture", "The cover, a section's picture or the exploded view of the whole assembly.",
          {"kind": {"type": "string", "enum": ["cover", "section", "overview"]},
           "section_id": {"type": "string", "description": "For kind 'section'"}, "width": WIDTH}, ["kind"]),
    _tool("get_changes", "What's out of date since the design changed: step previews, exported pictures, new parts, "
          "parts missing from the design."),
    _tool("show_step", "Open a step on screen (exploded) at its saved view.", {"step_id": STEP_ID}, ["step_id"]),
    _tool("set_view", "Point the camera: a standard view (Fusion's own, so 'top' is the design's up), optionally "
          "turned by yaw / pitch degrees, framed on some parts, a step (as drawn, exploded) or the whole design. "
          "Returns a picture of the result. Use save_view afterwards to keep it as a step's view.",
          {"view": {"type": "string", "enum": ["iso", "iso_top_right", "iso_top_left", "iso_bottom_right",
                                               "iso_bottom_left", "front", "back", "left", "right", "top", "bottom",
                                               "current"],
                    "description": "Default iso (top right)"},
           "yaw": {"type": "number", "description": "Turn around the up direction, degrees (+ = to the right)"},
           "pitch": {"type": "number", "description": "Tilt, degrees (+ = look more from above)"},
           "step_id": {"type": "string", "description": "Show this step (exploded) and frame it"},
           "paths": {"type": "array", "items": {"type": "string"}, "description": "Frame these parts"},
           "margin": {"type": "number", "description": "Space around what's framed (default 1.3 = 30 %)"},
           "capture": {"type": "boolean", "description": "Return a picture (default true)"},
           "width": WIDTH}),
    _tool("get_part_info", "Geometry and relationships of parts, to plan a build: each part's centre, size, bodies, "
          "which steps hold it, its joints, and which parts touch it (bounding boxes within 0.5 mm). Also the "
          "design's up direction.",
          {"paths": {"type": "array", "items": {"type": "string"},
                     "description": "These parts (default: every part with bodies, up to 300)"},
           "measure": {"type": "boolean", "description": "Measure the real gap for 'touches' (slow: ~0.1 s a pair; "
                                                         "best with a few paths). Default: bounding boxes only ('near')"}}),
    _tool("backup_manual", "Save a copy of the whole build manual (a JSON file in BuildBook's data folder), "
          "e.g. before making big changes.", {"label": {"type": "string", "description": "A short note for it"}}),
    _tool("list_backups", "The saved copies of this design's build manual, newest first."),

    _tool("add_section", "Add a section at the end.", {"title": {"type": "string"}}, ["title"], edit=True),
    _tool("add_step", "Add a step to a section: at its end, or right after / before another step.",
          {"section_id": {"type": "string"}, "title": {"type": "string"},
           "after_step_id": {"type": "string"}, "before_step_id": {"type": "string"}}, ["section_id"], edit=True),
    _tool("update_step", "Change a step's title, notes, repeat count or preparation flag.",
          {"step_id": STEP_ID, "title": {"type": "string"}, "notes": {"type": "string"},
           "repeat": {"type": "integer", "minimum": 1}, "prep": {"type": "boolean"},
           "earlier": {"type": "string", "enum": ["inherit", "shown", "ghosted", "hidden"],
                       "description": "How earlier steps' parts show in this step (inherit = the manual's setting)"},
           "later": {"type": "string", "enum": ["inherit", "shown", "ghosted", "hidden"],
                     "description": "How later steps' parts show in this step"}}, ["step_id"], edit=True),
    _tool("delete_step", "Delete a step (Ctrl+Z in the panel brings it back).", {"step_id": STEP_ID}, ["step_id"], edit=True),
    _tool("add_parts", "Add parts to a step.", {"step_id": STEP_ID, "paths": PATHS}, ["step_id", "paths"], edit=True),
    _tool("remove_parts", "Take parts out of a step.", {"step_id": STEP_ID, "paths": PATHS}, ["step_id", "paths"], edit=True),
    _tool("add_explode_move", "Add an explode move to a step: these parts move along an axis.",
          {"step_id": STEP_ID, "paths": PATHS, "axis": {"type": "string", "enum": AXES},
           "distance": DISTANCE, "spacing": {"type": "string", "enum": SPACINGS},
           "base": DISTANCE, "name": {"type": "string"}}, ["step_id", "paths", "axis"], edit=True),
    _tool("update_explode_move", "Change an explode move's parts, axis, distance, spacing, base distance or name.",
          {"step_id": STEP_ID, "move_id": {"type": "string"}, "paths": PATHS,
           "axis": {"type": "string", "enum": AXES}, "distance": DISTANCE,
           "spacing": {"type": "string", "enum": SPACINGS}, "base": DISTANCE, "name": {"type": "string"}},
          ["step_id", "move_id"], edit=True),
    _tool("delete_explode_move", "Delete an explode move.", {"step_id": STEP_ID, "move_id": {"type": "string"}},
          ["step_id", "move_id"], edit=True),
    _tool("save_view", "Save the current camera as a step's view (and take its preview).", {"step_id": STEP_ID},
          ["step_id"], edit=True),
    _tool("reload_addin", "Reload BuildBook's code from disk (after its files changed), like Settings > Reload "
          "BuildBook. Answers first, then reloads a moment later; the connection is back within a few seconds.",
          edit=True),
    _tool("restore_backup", "Replace the build manual with a saved copy (the one in use is backed up first, and "
          "Ctrl+Z in the panel undoes it).", {"backup": {"type": "string", "description": "A file name from list_backups"}},
          ["backup"], edit=True),
]


def definitions(allow_edit):
    return [{k: v for k, v in t.items() if k != "edit"} for t in TOOLS if allow_edit or not t["edit"]]


# ---------------------------------------------------------------- results

def _text(value):
    text = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _image(png, note=""):
    content = [{"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"}]
    if note:
        content.append({"type": "text", "text": note})
    return {"content": content}


def _fail(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ---------------------------------------------------------------- running a tool

def run(ctrl, name, args, allow_edit):
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return _fail("No tool called " + str(name))
    if tool["edit"] and not allow_edit:
        return _fail("Changes are switched off: turn on “Allow changes” in BuildBook's Settings.")
    if ctrl.design() is None:
        return _fail("No design is open in Fusion.")
    return globals()["_" + name](ctrl, args)


def _units(ctrl):
    um = ctrl.design().unitsManager
    return um, um.defaultLengthUnits


def _shown(ctrl, cm):
    um, units = _units(ctrl)
    return round(um.convert(cm, "cm", units), 4)


def _step(ctrl, manual, sid):
    _, step = model.find_step(manual, sid)
    if step is None:
        raise ValueError("No step with id {} (see list_steps)".format(sid))
    return step


def _list_steps(ctrl, args):
    manual = ctrl.load()
    out = []
    for si, sec in enumerate(manual["sections"], 1):
        out.append({"section_id": sec["id"], "section": "{}  {}".format(si, sec["title"]), "steps": [
            {"step_id": st["id"], "number": "{}.{}".format(si, ti), "title": st["title"],
             "parts": len(st["items"]), "moves": len(st.get("explodes", [])),
             "view_saved": bool(st.get("camera")), "prep": bool(st.get("prep")),
             "repeat": st.get("repeat", 1)}
            for ti, st in enumerate(sec["steps"], 1)]})
    return _text({"title": manual.get("title"), "units": _units(ctrl)[1], "sections": out})


def _get_step(ctrl, args):
    manual = ctrl.load()
    step = _step(ctrl, manual, args.get("step_id"))
    index = refs.path_index(ctrl.design())
    parts = []
    for item in step["items"]:
        path = item["ref"].get("path", "")
        occ = index.get(path)
        parts.append({"path": path, "name": refs.display_name(occ, item["ref"].get("name", path)),
                      "component": occ.component.name if occ is not None else None, "missing": occ is None})
    moves = []
    for n, ex in enumerate(step.get("explodes", []), 1):
        sign = -1 if model.is_negative(ex["direction"]) else 1
        moves.append({"move_id": ex["id"], "number": n, "name": ex.get("name") or model.explode_label(step, ex),
                      "direction": model.direction_label(ex["direction"]),
                      "distance": sign * _shown(ctrl, model.explode_distance(manual, step, ex)),
                      "distance_expression": ex.get("distanceExpr"),
                      "spacing": ex.get("spacing"), "base": _shown(ctrl, ex.get("base") or 0.0),
                      "parts": [p["ref"].get("path") for p in ex["parts"]]})
    return _text({"step_id": step["id"], "title": step["title"], "notes": step.get("notes", ""),
                  "units": _units(ctrl)[1], "parts": parts, "moves": moves,
                  "earlier": step.get("earlier"), "later": step.get("later"),
                  "prep": bool(step.get("prep")), "repeat": step.get("repeat", 1),
                  "view_saved": bool(step.get("camera"))})


def _get_manual(ctrl, args):
    manual = ctrl.load()
    manual.get("settings", {}).pop("logo", None)        # (a big picture: not useful as text)
    manual.pop("knownParts", None)
    return _text(manual)


def _list_parts(ctrl, args):
    manual = ctrl.load()
    covered = model.covered_paths(manual)
    text = (args.get("filter") or "").lower()
    out = []
    for path, part in refs.path_index(ctrl.design()).items():
        in_step = model.is_covered(covered, path)
        if args.get("unassigned_only") and in_step:
            continue
        name = getattr(part, "name", path)
        if text and text not in (path + " " + name).lower():
            continue
        out.append({"path": path, "name": name, "component": part.component.name, "in_step": in_step})
        if len(out) >= 400:
            break
    return _text(out)


def _render(ctrl, manual, width, path_fn):
    """PNG bytes of the canvas as it is now, in the manual's crop, `width` wide."""
    settings = json.loads(json.dumps(manual))
    settings["settings"].setdefault("image", {}).update({"width": int(width or 1200), "transparent": False})
    tmp = os.path.join(tempfile.gettempdir(), "buildbook-mcp-{}.png".format(uuid.uuid4().hex[:8]))
    try:
        ctrl.crop_overlay.clear()
        capture.save_png(ctrl.app, settings, None, tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        for leftover in (tmp, tmp + ".png"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        ctrl.update_overlay()


def _capture_step(ctrl, args):
    manual = ctrl.load()
    step = _step(ctrl, manual, args.get("step_id"))
    width = int(args.get("width") or 1200)
    if args.get("saved_view", True) and step.get("camera"):
        png = pictures.render(ctrl, manual, "step", step["id"], width)
        note = "Step {} at its saved view.".format(step["title"])
    else:
        ctrl.show_step(step["id"], move_camera=False)
        png = _render(ctrl, manual, width, None)
        note = "Step {} at the current camera{}.".format(
            step["title"], "" if step.get("camera") else " (it has no saved view)")
    return _image(png, note)


def _capture_view(ctrl, args):
    return _image(_render(ctrl, ctrl.load(), args.get("width"), None), "The canvas as it is now.")


def _capture_picture(ctrl, args):
    manual = ctrl.load()
    kind = args.get("kind")
    pid = args.get("section_id") if kind == "section" else None
    if kind == "section" and model.find_section(manual, pid) is None:
        return _fail("No section with id {}".format(pid))
    png = pictures.render(ctrl, manual, kind, pid, int(args.get("width") or 1200))
    return _image(png)


def _get_changes(ctrl, args):
    state = ctrl.state()
    titles = {st["id"]: st["title"] for _, st in model.ordered_steps(state["manual"])}
    ch = state.get("changes") or {}
    return _text({"previews_out_of_date": [titles.get(i, i) for i in ch.get("previews", [])],
                  "exports_out_of_date": [titles.get(i, i) for i in ch.get("exports", [])],
                  "new_parts": ch.get("newParts", []),
                  "missing_parts": state.get("missing", [])})


def _show_step(ctrl, args):
    manual = ctrl.load()
    step = _step(ctrl, manual, args.get("step_id"))
    ctrl.show_step(step["id"], move_camera=True)
    return _text("Showing step " + step["title"])


# ---------------------------------------------------------------- edits (through the panel's actions)

def _add_section(ctrl, args):
    ctrl.handle("addSection", {"title": args.get("title")})
    sec = ctrl.load()["sections"][-1]
    return _text({"section_id": sec["id"], "title": sec["title"]})


def _add_step(ctrl, args):
    before = {st["id"] for _, st in model.ordered_steps(ctrl.load())}
    ctrl.handle("addStep", {"sectionId": args.get("section_id"), "title": args.get("title"),
                            "after": args.get("after_step_id"), "before": args.get("before_step_id")})
    new = [st for _, st in model.ordered_steps(ctrl.load()) if st["id"] not in before]
    if not new:
        return _fail("No section with id {}".format(args.get("section_id")))
    return _text({"step_id": new[0]["id"], "title": new[0]["title"]})


def _update_step(ctrl, args):
    sid = args.get("step_id")
    _step(ctrl, ctrl.load(), sid)
    if args.get("title"):
        ctrl.handle("renameStep", {"id": sid, "title": args["title"]})
    if "notes" in args:
        ctrl.handle("setNotes", {"stepId": sid, "notes": args.get("notes") or ""})
    if args.get("repeat"):
        ctrl.handle("setRepeat", {"stepId": sid, "repeat": args["repeat"]})
    if "prep" in args:
        ctrl.handle("setPrep", {"stepId": sid, "prep": bool(args["prep"])})
    context = {k: args[k] for k in ("earlier", "later") if args.get(k)}
    if context:
        context["stepId"] = sid
        ctrl.handle("setContext", context)
    return _get_step(ctrl, {"step_id": sid})


def _delete_step(ctrl, args):
    step = _step(ctrl, ctrl.load(), args.get("step_id"))
    ctrl.handle("deleteStep", {"id": step["id"]})
    return _text("Deleted step {} (Ctrl+Z in the panel brings it back).".format(step["title"]))


def _known_paths(ctrl, paths):
    index = refs.path_index(ctrl.design())
    unknown = [p for p in paths or [] if p not in index]
    if unknown:
        raise ValueError("Not parts in the design: {} (see list_parts)".format(", ".join(unknown[:10])))
    return index


def _add_parts(ctrl, args):
    _step(ctrl, ctrl.load(), args.get("step_id"))
    _known_paths(ctrl, args.get("paths"))
    ctrl.handle("addPaths", {"stepId": args["step_id"], "paths": args.get("paths") or []})
    return _get_step(ctrl, {"step_id": args["step_id"]})


def _remove_parts(ctrl, args):
    _step(ctrl, ctrl.load(), args.get("step_id"))
    ctrl.handle("removeItems", {"stepId": args["step_id"], "paths": args.get("paths") or []})
    return _get_step(ctrl, {"step_id": args["step_id"]})


def _length(ctrl, text):
    """(cm, expression or None) for a distance argument."""
    text = str(text).strip()
    value = params.evaluate(ctrl.design(), text)
    if value is None:
        raise ValueError("Can't read {!r} as a distance".format(text))
    return value, (text if params.is_expression(text) else None)


def _apply_move(ctrl, ex, step, args, index):
    if args.get("axis"):
        if args["axis"] not in AXES:
            raise ValueError("axis must be one of " + ", ".join(AXES))
        ex["direction"] = model.signed_direction(model.new_direction("+" + args["axis"][1]),
                                                 args["axis"][0] == "-")
    if args.get("distance") not in (None, ""):
        value, expr = _length(ctrl, args["distance"])
        ex["distance"] = abs(value)
        if value < 0:
            ex["direction"] = model.signed_direction(ex["direction"], not model.is_negative(ex["direction"]))
        if expr:
            ex["distanceExpr"] = expr
        else:
            ex.pop("distanceExpr", None)
    if args.get("spacing"):
        if args["spacing"] not in SPACINGS:
            raise ValueError("spacing must be one of " + ", ".join(SPACINGS))
        ex["spacing"] = args["spacing"]
    if args.get("base") not in (None, ""):
        value, expr = _length(ctrl, args["base"])
        ex["base"] = abs(value)
        if expr:
            ex["baseExpr"] = expr
        else:
            ex.pop("baseExpr", None)
    if "name" in args:
        ex["name"] = args.get("name") or ""
    if args.get("paths"):
        model.set_explode_parts(step, ex, [refs.make_ref(index[p]) for p in args["paths"]])


def _add_explode_move(ctrl, args):
    manual = ctrl.load()
    step = _step(ctrl, manual, args.get("step_id"))
    index = _known_paths(ctrl, args.get("paths"))
    ex = model.new_explode(model.new_direction("+Z"))
    _apply_move(ctrl, ex, step, args, index)
    step["explodes"].append(ex)
    ctrl.save(manual)
    ctrl.show_step(step["id"])
    return _text({"move_id": ex["id"], "step": _json(_get_step(ctrl, {"step_id": step["id"]}))})


def _update_explode_move(ctrl, args):
    manual = ctrl.load()
    step = _step(ctrl, manual, args.get("step_id"))
    ex = model.find_explode(step, args.get("move_id"))
    if ex is None:
        return _fail("No move with id {} in that step (see get_step)".format(args.get("move_id")))
    index = _known_paths(ctrl, args.get("paths"))
    _apply_move(ctrl, ex, step, args, index)
    ctrl.save(manual)
    ctrl.show_step(step["id"])
    return _get_step(ctrl, {"step_id": step["id"]})


def _delete_explode_move(ctrl, args):
    step = _step(ctrl, ctrl.load(), args.get("step_id"))
    ctrl.handle("deleteExplode", {"stepId": step["id"], "id": args.get("move_id")})
    return _get_step(ctrl, {"step_id": step["id"]})


def _save_view(ctrl, args):
    step = _step(ctrl, ctrl.load(), args.get("step_id"))
    ctrl.handle("saveCamera", {"stepId": step["id"]})
    return _text("Saved the current camera as the view of " + step["title"])


def _json(result):
    try:
        return json.loads(result["content"][0]["text"])
    except Exception:
        return None


# ---------------------------------------------------------------- camera

def _views():
    import adsk.core
    V = adsk.core.ViewOrientations
    return {"iso": V.IsoTopRightViewOrientation, "iso_top_right": V.IsoTopRightViewOrientation,
            "iso_top_left": V.IsoTopLeftViewOrientation, "iso_bottom_right": V.IsoBottomRightViewOrientation,
            "iso_bottom_left": V.IsoBottomLeftViewOrientation, "front": V.FrontViewOrientation,
            "back": V.BackViewOrientation, "left": V.LeftViewOrientation, "right": V.RightViewOrientation,
            "top": V.TopViewOrientation, "bottom": V.BottomViewOrientation}


def _box_union(boxes):
    lo, hi = None, None
    for (a, b) in boxes:
        lo = list(a) if lo is None else [min(x, y) for x, y in zip(lo, a)]
        hi = list(b) if hi is None else [max(x, y) for x, y in zip(hi, b)]
    return lo, hi


def _corners(box, offset=(0.0, 0.0, 0.0)):
    return ((box.minPoint.x + offset[0], box.minPoint.y + offset[1], box.minPoint.z + offset[2]),
            (box.maxPoint.x + offset[0], box.maxPoint.y + offset[1], box.maxPoint.z + offset[2]))


def _rotate(v, axis, deg):
    """Rodrigues: v turned `deg` degrees about unit `axis`."""
    import math
    t = math.radians(deg)
    c, s_ = math.cos(t), math.sin(t)
    dot = sum(a * b for a, b in zip(v, axis))
    cross = (axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0])
    return tuple(v[i] * c + cross[i] * s_ + axis[i] * dot * (1 - c) for i in range(3))


def _unit(v):
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return tuple(x / n for x in v)


def _settle(viewport, rounds=3):
    """Give Fusion time to finish showing / hiding parts before measuring or capturing."""
    import time
    for _ in range(rounds):
        viewport.refresh()
        capture.pump()
        time.sleep(0.05)


def _set_view(ctrl, args):
    import math
    import adsk.core
    viewport = ctrl.app.activeViewport
    manual = ctrl.load()
    boxes = []
    if args.get("step_id"):
        step = _step(ctrl, manual, args["step_id"])
        ctrl.show_step(step["id"], move_camera=False)
        _settle(viewport)
        # Everything the step shows: moved copies where they're drawn, and every part still on
        # screen (its own parts at home, earlier steps' parts as context).
        from . import boxselect
        for path, occ, off in ctrl.scene.copy_places:
            boxes.append(_corners(occ.boundingBox, off))
        for path, part, box in boxselect.leaf_parts(ctrl.design()):
            boxes.append(_corners(box))
    if args.get("paths"):
        index = _known_paths(ctrl, args["paths"])
        boxes += [_corners(index[p].boundingBox) for p in args["paths"]]
    if not boxes:
        boxes.append(_corners(ctrl.design().rootComponent.boundingBox))
    lo, hi = _box_union(boxes)
    centre = tuple((a + b) / 2.0 for a, b in zip(lo, hi))
    radius = max(1e-3, 0.5 * sum((b - a) ** 2 for a, b in zip(lo, hi)) ** 0.5)

    cam = viewport.camera
    name = args.get("view") or "iso"
    if name != "current":
        cam.viewOrientation = _views()[name]
        cam.isSmoothTransition = False
        viewport.camera = cam
        viewport.refresh()
        capture.pump()                  # (let Fusion finish turning before reading the camera back)
        cam = viewport.camera
    eye, target, up = cam.eye, cam.target, cam.upVector
    back = _unit((eye.x - target.x, eye.y - target.y, eye.z - target.z))
    upv = _unit((up.x, up.y, up.z))
    if args.get("yaw"):
        back = _rotate(back, upv, -float(args["yaw"]))
    if args.get("pitch"):
        right = _unit((upv[1] * back[2] - upv[2] * back[1], upv[2] * back[0] - upv[0] * back[2],
                       upv[0] * back[1] - upv[1] * back[0]))
        back = _unit(_rotate(back, right, -float(args["pitch"])))
    margin = float(args.get("margin") or 1.3)
    ortho = cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType
    if ortho:
        dist = radius * 6.0
    else:
        dist = radius * margin / max(0.05, math.tan(cam.perspectiveAngle / 2.0))
    cam.target = adsk.core.Point3D.create(*centre)
    cam.eye = adsk.core.Point3D.create(*(centre[i] + back[i] * dist for i in range(3)))
    cam.upVector = adsk.core.Vector3D.create(*upv)
    if ortho:
        cam.viewExtents = 2.0 * radius * margin     # (Fusion's extents span the whole view, not half)
    cam.isFitView = False
    cam.isSmoothTransition = False
    viewport.camera = cam
    viewport.refresh()
    capture.pump()
    # Fit: measure what's framed along the camera's own right / up directions, centre the view on it
    # and size the zoom so it fills the picture's crop (Fusion's orthographic extents are the height
    # of the whole view).
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    right = _unit((upv[1] * back[2] - upv[2] * back[1], upv[2] * back[0] - upv[0] * back[2],
                   upv[0] * back[1] - upv[1] * back[0]))
    up2 = _unit((back[1] * right[2] - back[2] * right[1], back[2] * right[0] - back[0] * right[2],
                 back[0] * right[1] - back[1] * right[0]))
    us = [sum(c[i] * right[i] for i in range(3)) for c in corners]
    vs = [sum(c[i] * up2[i] for i in range(3)) for c in corners]
    width, height = max(us) - min(us), max(vs) - min(vs)
    mid_u, mid_v = (max(us) + min(us)) / 2.0, (max(vs) + min(vs)) / 2.0
    cu = sum(centre[i] * right[i] for i in range(3))
    cv = sum(centre[i] * up2[i] for i in range(3))
    centre = tuple(centre[i] + right[i] * (mid_u - cu) + up2[i] * (mid_v - cv) for i in range(3))
    from . import crop as crop_mod
    vw, vh = viewport.width, viewport.height
    _, _, rw, rh = crop_mod.centered_rect(crop_mod.ratio_value(
        manual["settings"].get("image", {}).get("ratio") or crop_mod.VIEWPORT, vw, vh), vw, vh)
    need = max(height * vh / max(rh, 1.0), width * vh / max(rw, 1.0)) * margin     # view height needed
    cam = viewport.camera
    if ortho:
        cam.viewExtents = need
        dist = max(radius * 6.0, 1.0)
    else:
        dist = (need / 2.0) / max(0.05, math.tan(cam.perspectiveAngle / 2.0)) + radius
    cam.target = adsk.core.Point3D.create(*centre)
    cam.eye = adsk.core.Point3D.create(*(centre[i] + back[i] * dist for i in range(3)))
    cam.upVector = adsk.core.Vector3D.create(*up2)
    cam.isFitView = False
    cam.isSmoothTransition = False
    viewport.camera = cam
    viewport.refresh()
    capture.pump()
    log.info("set_view: framed {:.1f} x {:.1f} cm in a {:.0f} x {:.0f} crop of {} x {}: view height {:.1f}".format(
        width, height, rw, rh, vw, vh, need))
    got = viewport.camera
    log.info("set_view: target asked ({:.1f}, {:.1f}, {:.1f}) got ({:.1f}, {:.1f}, {:.1f}), extents {:.1f}".format(
        centre[0], centre[1], centre[2], got.target.x, got.target.y, got.target.z, got.viewExtents))
    ctrl.update_overlay()
    note = "View {}{}{}, framed on {}.".format(
        name, " yaw {}".format(args["yaw"]) if args.get("yaw") else "",
        " pitch {}".format(args["pitch"]) if args.get("pitch") else "",
        "step " + args["step_id"] if args.get("step_id") else ("those parts" if args.get("paths") else "the design"))
    if args.get("capture", True):
        _settle(viewport)               # (the step's drawing settles first: half-drawn parts showed)
        return _image(_render(ctrl, manual, args.get("width") or 900, None), note)
    return _text(note)


# ---------------------------------------------------------------- geometry and relationships

def _get_part_info(ctrl, args):
    import adsk.core
    import adsk.fusion
    design = ctrl.design()
    manual = ctrl.load()
    um, units = _units(ctrl)

    def d(cm):
        return round(um.convert(cm, "cm", units), 3)
    index = refs.path_index(design)
    if args.get("paths"):
        _known_paths(ctrl, args["paths"])
        paths = list(args["paths"])
    else:
        paths = [p for p, part in index.items()
                 if not refs.is_split(getattr(part, "occ", part)) or isinstance(part, refs.BodyPart)]
        paths = [p for p in paths if isinstance(index[p], refs.BodyPart) or index[p].bRepBodies.count][:300]
    steps_of = {}
    for _, st in model.ordered_steps(manual):
        for item_path in model.item_paths(st):
            for p in paths:
                if model.is_self_or_ancestor(item_path, p):
                    steps_of.setdefault(p, []).append(st["title"])
    boxes = {}
    out = {}
    for p in paths:
        part = index[p]
        box = part.boundingBox
        lo, hi = _corners(box)
        boxes[p] = (lo, hi)
        bodies = [part.body] if isinstance(part, refs.BodyPart) else list(part.bRepBodies)
        out[p] = {"name": refs.display_name(part, getattr(part, "name", p)),
                  "component": part.component.name,
                  "centre": [d((a + b) / 2.0) for a, b in zip(lo, hi)],
                  "size": [d(b - a) for a, b in zip(lo, hi)],
                  "bodies": len(bodies), "in_steps": steps_of.get(p, []), "joints": [], "touches": []}
    # Touching: boxes close enough first (cheap), then the real gap between the bodies (Fusion's
    # minimum distance), so a compact assembly isn't "everything touches everything".
    import time
    tol = 0.05                                   # cm
    measure = ctrl.app.measureManager
    names = list(boxes)
    started, exact = time.perf_counter(), True

    def geometry(path):
        part = index[path]
        return part.body if isinstance(part, refs.BodyPart) else part
    for i, p in enumerate(names):
        a0, a1 = boxes[p]
        for q in names[i + 1:]:
            b0, b1 = boxes[q]
            if model.is_self_or_ancestor(p, q) or model.is_self_or_ancestor(q, p):
                continue
            if not all(a0[k] - tol <= b1[k] and b0[k] - tol <= a1[k] for k in range(3)):
                continue
            close = True
            if not args.get("measure"):
                exact = False
            elif exact and time.perf_counter() - started < 60.0:
                try:
                    close = measure.measureMinimumDistance(geometry(p), geometry(q)).value <= tol
                except Exception:
                    close = True                 # (couldn't measure: the boxes say close)
            else:
                exact = False                    # (too long: the rest by boxes only)
            if close:
                out[p]["touches"].append(q)
                out[q]["touches"].append(p)
    type_names = {}
    for name in ("Rigid", "Revolute", "Slider", "Cylindrical", "PinSlot", "Planar", "Ball"):
        value = getattr(adsk.fusion.JointTypes, name + "JointType", None)
        if value is not None:
            type_names[value] = name.lower()
    joints = []
    root = design.rootComponent
    for kind, collection in (("joint", root.allJoints), ("as-built joint", root.allAsBuiltJoints)):
        for j in collection:
            try:
                one = j.occurrenceOne.fullPathName if j.occurrenceOne else "(root)"
                two = j.occurrenceTwo.fullPathName if j.occurrenceTwo else "(root)"
                jtype = type_names.get(j.jointMotion.jointType, "other")
                joints.append({"name": j.name, "kind": kind, "type": jtype, "parts": [one, two],
                               "suppressed": bool(getattr(j, "isSuppressed", False))})
                for a, b in ((one, two), (two, one)):
                    for p in out:
                        if model.is_self_or_ancestor(a, p) or model.is_self_or_ancestor(p, a):
                            out[p]["joints"].append({"name": j.name, "type": jtype, "with": b})
            except Exception:
                continue
    try:
        orient = ctrl.app.preferences.generalPreferences.defaultModelingOrientation
        up = "Y" if orient == adsk.core.DefaultModelingOrientations.YUpModelingOrientation else "Z"
    except Exception:
        up = "unknown"
    return _text({"units": units, "up": up,
                  "touching": "measured gaps (within 0.5 mm)" if exact else
                  "bounding boxes overlap (near, not necessarily touching: pass measure with fewer paths)",
                  "parts": out, "joints": joints})


# ---------------------------------------------------------------- backups

def _backup_dir(ctrl):
    from . import paths
    doc = ctrl.app.activeDocument
    try:
        key = doc.dataFile.id if doc.dataFile is not None else doc.name
    except Exception:
        key = doc.name
    folder = paths.data_dir("backups", model.safe_filename(key)[:80] or "design")
    os.makedirs(folder, exist_ok=True)
    return folder


def backup(ctrl, manual, label=""):
    """Write the manual to the backups folder; returns the file name."""
    import time
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    name = "{}{}.json".format(stamp, (" - " + model.safe_filename(label)[:60]) if label else "")
    with open(os.path.join(_backup_dir(ctrl), name), "w", encoding="utf-8") as f:
        f.write(model.to_json(manual))
    log.info("backup: " + name)
    return name


def _backup_manual(ctrl, args):
    manual = ctrl.load()
    name = backup(ctrl, manual, args.get("label") or "")
    return _text({"backup": name, "folder": _backup_dir(ctrl),
                  "steps": len(model.ordered_steps(manual))})


def _list_backups(ctrl, args):
    folder = _backup_dir(ctrl)
    out = []
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                m = model.from_json(f.read())
            out.append({"backup": name, "sections": len(m["sections"]), "steps": len(model.ordered_steps(m))})
        except Exception:
            out.append({"backup": name, "unreadable": True})
    return _text({"folder": folder, "backups": out})


def _restore_backup(ctrl, args):
    name = os.path.basename(args.get("backup") or "")
    path = os.path.join(_backup_dir(ctrl), name)
    if not name or not os.path.isfile(path):
        return _fail("No backup called {!r} (see list_backups)".format(name))
    with open(path, encoding="utf-8") as f:
        restored = model.from_json(f.read())
    current = ctrl.load()
    kept = backup(ctrl, current, "before restoring " + name[:19])
    ctrl.undo_stack = (ctrl.undo_stack + [(model.to_json(current), "restored a backup")])[-30:]
    ctrl.save(restored)
    ctrl.close_view()
    return _text("Restored {}. The manual it replaced is backed up as {} (and Ctrl+Z in the panel undoes this)."
                 .format(name, kept))


# ---------------------------------------------------------------- the add-in itself

def _reload_addin(ctrl, args):
    """Reload a moment after answering: the reload stops this server, so it mustn't happen while
    the answer is still being sent."""
    import threading
    import adsk.core

    def later():
        adsk.core.Application.get().fireCustomEvent("buildBookReload", "")
    threading.Timer(0.8, later).start()
    log.info("mcp: reload asked for")
    return _text("Reloading BuildBook in a moment; call any tool again in a few seconds.")
