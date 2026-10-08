"""The part's threaded (and dowel) holes, and where they are in a view.

A hole is a concave cylindrical face (in the active component, or the activated
component's occurrence, in world coordinates). Its id is geometric (axis line +
radius, rounded), so it survives recomputes that don't move it. Its size comes from:
1. a thread feature on it (internal thread): "M3x0.5" -> M3;
2. a tapped hole feature (holeTapType tapped / taper tapped);
3. with `guess` on, a plain hole whose diameter is a tap drill (2.5 mm -> M3, ...);
4. the user's marks (override everything): a size ("M4"), "dowel", or "none".
Openings are the hole's circular edges with air on both sides (not a blind hole's
bottom); each knows which way is out.
"""

import math
import re

import adsk.core
import adsk.fusion

from . import callouts, log

EPS = 0.02          # cm: step off an opening to test which side is air
SCREW = re.compile(r"(?i)screw|shcs|bhcs|fhcs|bolt|cap head|\bM\d+(\.\d+)?\s*x\s*\d")    # screw-like part names
CLEAR = (0.1, 0.3, 0.6, 1.0, 1.5)  # cm out from an opening: another part there blocks a screw going in
OUTLINE_POINTS = 28


def _safe(read, default=None):
    try:
        return read()
    except Exception:
        return default


def _v(p):
    return (p.x, p.y, p.z)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b, k=1.0):
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    length = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _p3(t):
    return adsk.core.Point3D.create(t[0], t[1], t[2])


# ------------------------------------------------------------ the part

def context(design, target=None):
    """(component, occurrence or None): `target` (an occurrence, e.g. an agent's choice), else the
    activated component (its occurrence, for world coordinates), else the root component."""
    occ = target if target is not None else _safe(lambda: design.activeOccurrence)
    if occ is not None:
        return occ.component, occ
    return design.rootComponent, None


def bodies(design, target=None):
    comp, occ = context(design, target)
    out = [b for b in (occ.bRepBodies if occ is not None else comp.bRepBodies) if _safe(lambda: b.isSolid, True)]
    if not out and occ is None:                 # a part modelled as one component under the root
        for o in comp.allOccurrences:
            out.extend(b for b in o.bRepBodies if _safe(lambda: b.isSolid, True))
    if target is not None:      # an agent's part: hidden or not, but only the bodies it has switched on
        return [b for b in out if _safe(lambda: b.isLightBulbOn, True)]
    return [b for b in out if _safe(lambda: b.isVisible, True)]


def _proxy(face, occ):
    return face.createForAssemblyContext(occ) if occ is not None and face.assemblyContext is None else face


# ------------------------------------------------------------ hole geometry

def _hole_geometry(face):
    """{"key", "line", "t0", "t1", "radius" (cm), "axis", "openings": [(centre, outward)], "face", ...}
    for a concave cylindrical face, else None."""
    cyl = adsk.core.Cylinder.cast(_safe(lambda: face.geometry))
    if cyl is None:
        return None
    origin, axis, radius = _v(cyl.origin), _norm(_v(cyl.axis)), cyl.radius
    p = _safe(lambda: face.pointOnFace)
    if p is None:
        return None
    ok, normal = face.evaluator.getNormalAtPoint(p)
    on_axis = _add(origin, axis, _dot(_sub(_v(p), origin), axis))
    radial = _sub(_v(p), on_axis)
    if ok and _dot(_v(normal), radial) > 0:     # normal points away from the axis: a boss, not a hole
        return None
    body = face.body
    centres = []
    for edge in face.edges:
        geo = edge.geometry
        circle = adsk.core.Circle3D.cast(geo) or adsk.core.Arc3D.cast(geo)
        if circle is None or abs(circle.radius - radius) > 1e-4:
            continue
        c = _v(circle.center)
        if all(_dist3(c, other) > 1e-4 for other in centres):
            centres.append(c)
    if not centres:
        return None
    mid = tuple(sum(c[i] for c in centres) / len(centres) for i in range(3))
    if len(centres) == 1:
        box = face.boundingBox
        mid = tuple((a + b) / 2 for a, b in zip(_v(box.minPoint), _v(box.maxPoint)))
    openings = []
    outside = adsk.fusion.PointContainment.PointOutsidePointContainment
    for c in centres:
        sides = [body.pointContainment(_p3(_add(c, axis, s * EPS))) == outside for s in (1, -1)]
        if not all(sides):
            continue                            # a blind hole's bottom: material beyond it
        sign = 1 if _dot(_sub(c, mid), axis) >= 0 else -1
        openings.append((c, (axis[0] * sign, axis[1] * sign, axis[2] * sign)))
    # The axis line: its point nearest the world origin + direction (one way round) + radius, rounded first
    # and -0.0 made 0.0 (two faces of one hole differ in the last bits). Holes on one line (e.g. at both
    # ends of a part) share it; where along the line a face is ("t", cm) tells them apart.
    foot = _sub(origin, tuple(a * _dot(origin, axis) for a in axis))
    tidy = tuple(round(a, 5) + 0.0 for a in axis)
    flip = -1 if tidy < (0, 0, 0) else 1
    direction = _norm(tuple(a * flip for a in tidy))
    line = "{:.3f},{:.3f},{:.3f}|{:.3f},{:.3f},{:.3f}|{:.4f}".format(
        *(round(v, 3) + 0.0 for v in (foot[0], foot[1], foot[2]) + direction), radius)
    ts = [_dot(_sub(c, foot), direction) for c in centres] + [_dot(_sub(_v(p), foot), direction)]
    t0, t1 = min(ts), max(ts)
    return {"key": "{}|{:.3f}".format(line, round(t0, 3) + 0.0), "line": line, "t0": t0, "t1": t1,
            "radius": radius, "axis": axis, "direction": direction, "foot": foot, "openings": openings, "face": face}


def _dist3(a, b):
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


TOUCH = 0.01        # cm: faces on one line this close (or overlapping) are one hole
BOTH_ENDS = 4.0     # a through hole at least this many diameters long is tapped from each end


def _merge(faces):
    """Holes from hole faces: faces on one axis line that touch (a hole split into a threaded and a plain
    part, or cut by a slot) are one hole; faces apart on the line (holes at both ends of a part) are not.
    A merged hole's openings are only those at its two ends."""
    lines = {}
    for geo in faces.values():
        lines.setdefault(geo["line"], []).append(geo)
    out = []
    for line, group in lines.items():
        group.sort(key=lambda g: g["t0"])
        runs = [[group[0]]]
        for geo in group[1:]:
            if geo["t0"] <= max(g["t1"] for g in runs[-1]) + TOUCH:
                runs[-1].append(geo)
            else:
                runs.append([geo])
        for run in runs:
            t0, t1 = run[0]["t0"], max(g["t1"] for g in run)
            first = run[0]
            ends = []
            for geo in run:
                for centre, outward in geo["openings"]:
                    t = _dot(_sub(centre, first["foot"]), first["direction"])
                    if (abs(t - t0) < 1e-3 or abs(t - t1) < 1e-3) and                             all(_dist3(centre, c) > 1e-4 for c, _ in ends):
                        ends.append((centre, outward))
            tagged = next((g for g in run if g.get("label")), {})
            out.append({"id": first["key"], "members": [g["key"] for g in run], "radius": first["radius"],
                        "axis": first["axis"], "openings": ends, "length": t1 - t0,
                        "label": tagged.get("label"), "source": tagged.get("source"),
                        "start": next((g["start"] for g in run if g.get("start")), None)})
    return out


def find(design, guess=True, marks=None, target=None):
    """[hole] with {"id", "kind" ("thread"|"dowel"), "label", "source", "diameter" (mm),
    "radius" (cm), "axis", "openings", "length" (cm)} -- only holes with a size (or marked as dowels).

    A long through hole (BOTH_ENDS diameters or more, e.g. across a part's whole width) is tapped from
    each end, so it's two holes ("<id>|1", "<id>|2"), each with one opening."""
    marks = marks or {}
    comp, occ = context(design, target)
    faces = {}
    for body in bodies(design, target):
        for face in body.faces:
            try:
                geo = _hole_geometry(face)
            except Exception:
                geo = None
            if geo is not None and geo["key"] not in faces:
                faces[geo["key"]] = geo

    def tag(feature_faces, label, source, start=None):
        for face in feature_faces:
            try:
                geo = _hole_geometry(_proxy(face, occ))
            except Exception:
                continue
            if geo is not None and geo["key"] in faces:
                faces[geo["key"]].setdefault("label", label)
                faces[geo["key"]].setdefault("source", source)
                if start is not None:
                    faces[geo["key"]].setdefault("start", start)

    features = comp.features
    for thread in _safe(lambda: features.threadFeatures, []) or []:
        info = _safe(lambda: thread.threadInfo)
        if info is None or not _safe(lambda: info.isInternal, True):
            continue
        label = callouts.size_label(_safe(lambda: info.threadDesignation, "") or _safe(lambda: info.threadSize, ""))
        # (the thread's own faces: its inputCylindricalFaces only read with the timeline rolled back to it)
        tag(_safe(lambda: list(thread.faces), []) or [], label, "thread")
    tapped = (adsk.fusion.HoleTapTypes.TappedHoleTapType, adsk.fusion.HoleTapTypes.TaperTappedHoleTapType)
    for hole_feature in _safe(lambda: features.holeFeatures, []) or []:
        if _safe(lambda: hole_feature.holeTapType) not in tapped:
            continue
        info = _safe(lambda: hole_feature.tappedHoleInfo)
        label = callouts.size_label(_safe(lambda: info.threadDesignation, "") if info else "")
        tag(_safe(lambda: list(hole_feature.sideFaces), []) or [], label, "tapped", _start(hole_feature, occ))

    out = []
    others = None
    for h in _merge(faces):
        diameter = round(h["radius"] * 20.0, 3)          # cm radius -> mm diameter
        mark = next((marks[k] for k in [h["id"]] + h["members"] if k in marks), None)
        kind = "thread"
        if mark == "none":
            continue
        if mark == "dowel":
            kind, label, source = "dowel", callouts.dowel_label(diameter), "manual"
        elif mark:
            label, source = mark, "manual"
        elif h.get("label"):
            label, source = h["label"], h["source"]
        elif guess and callouts.guess_size(diameter):
            label, source = callouts.guess_size(diameter), "guess"
        else:
            continue
        hole = {"id": h["id"], "kind": kind, "label": label, "source": source, "diameter": diameter,
                "radius": h["radius"], "axis": h["axis"], "openings": h["openings"], "length": h["length"]}
        if kind == "thread" and len(h["openings"]) == 2 and h["length"] * 10.0 >= BOTH_ENDS * diameter:
            for n, opening in enumerate(h["openings"], 1):
                out.append(dict(hole, id="{}|{}".format(h["id"], n), openings=[opening], tapped_from="both ends"))
            continue
        if kind == "thread" and len(h["openings"]) == 2:
            if others is None:
                others = _others(design, occ)
            end, why = _tap_end(h, others)
            if end is not None:
                hole = dict(hole, openings=[h["openings"][end]], tapped_from=why)
        out.append(hole)
    out.sort(key=lambda h: (h["kind"], callouts.size_key(h["label"]), h["id"]))
    return out


def _start(hole_feature, occ):
    """Where a hole feature starts (world coordinates), or None."""
    p = _safe(lambda: hole_feature.position)
    if p is None:
        return None
    p = p.copy()
    if occ is not None:
        p.transformBy(occ.transform2)
    return _v(p)


def _others(design, occ):
    """[(name, is a screw, [bodies], (lo, hi) box)] of the assembly's other parts (shown or not)."""
    target = occ.fullPathName if occ is not None else None
    out = []
    for o in design.rootComponent.allOccurrences:
        path = o.fullPathName
        if target and (path == target or path.startswith(target + "+") or target.startswith(path + "+")):
            continue
        bodies = [b for b in o.bRepBodies if _safe(lambda: b.isSolid, True)]
        if not bodies:
            continue
        box = o.boundingBox
        out.append((path, bool(SCREW.search(o.component.name)), bodies, (_v(box.minPoint), _v(box.maxPoint))))
    return out


def _in_box(p, box, pad=0.0):
    return all(box[0][i] - pad <= p[i] <= box[1][i] + pad for i in range(3))


def _tap_end(hole, others):
    """Which opening (0 / 1) a through hole is tapped from, and why -- the end a screw goes in from:
    1. a screw in the hole: the end its head is at;
    2. another part right outside one end (a screw can't go in there): the other end;
    3. the end the hole feature was started from.
    (None, "") when nothing tells."""
    (c1, o1), (c2, o2) = hole["openings"]
    mid = tuple((a + b) / 2.0 for a, b in zip(c1, c2))
    axis_points = [tuple(c1[i] + (c2[i] - c1[i]) * k / 4.0 for i in range(3)) for k in range(1, 4)]
    for name, screw, bodies, box in others:
        if screw and any(_in_box(p, box, 0.02) for p in axis_points):     # (a short screw: part of it)
            centre = tuple((a + b) / 2.0 for a, b in zip(*box))
            return (0 if _dot(_sub(centre, mid), o1) > 0 else 1), "screw"
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    blocked = []
    for centre, outward in hole["openings"]:
        hit = False
        for d in CLEAR:
            p = _add(centre, outward, d)
            for name, screw, bodies, box in others:
                if screw or not _in_box(p, box):
                    continue
                if any(_safe(lambda: b.pointContainment(_p3(p)) == inside, False) for b in bodies):
                    hit = True
                    break
            if hit:
                break
        blocked.append(hit)
    if blocked[0] != blocked[1]:
        return (1 if blocked[0] else 0), "other end blocked"
    if hole.get("start"):
        return (0 if _dist3(c1, hole["start"]) <= _dist3(c2, hole["start"]) else 1), "hole feature start"
    return None, ""


def key_of(entity):
    """The hole id of a selected cylindrical face, or of a circular edge's hole face; else None."""
    face = adsk.fusion.BRepFace.cast(entity)
    faces = [face] if face is not None else []
    edge = adsk.fusion.BRepEdge.cast(entity)
    if edge is not None:
        faces = list(edge.faces)
    for f in faces:
        geo = _safe(lambda: _hole_geometry(f))
        if geo is not None:
            return geo["key"]
    return None


# ------------------------------------------------------------ a view

def fit_inside(viewport, part_bodies, margin=0.03):
    """Zoom the camera out (and centre it on the part) until the part is wholly inside the viewport:
    a view saved in a bigger window, or framed tight, would otherwise lose the part's ends in the
    capture. Returns True if the camera changed."""
    changed = False
    vw, vh = float(viewport.width), float(viewport.height)
    for _ in range(4):
        xs, ys, lo, hi = [], [], None, None
        for body in part_bodies:
            box = body.boundingBox
            a, b = _v(box.minPoint), _v(box.maxPoint)
            lo = a if lo is None else tuple(map(min, lo, a))
            hi = b if hi is None else tuple(map(max, hi, b))
        if lo is None:
            return changed
        for i in range(8):
            q = viewport.modelToViewSpace(_p3((hi[0] if i & 1 else lo[0], hi[1] if i & 2 else lo[1],
                                               hi[2] if i & 4 else lo[2])))
            xs.append(q.x / vw)
            ys.append(q.y / vh)
        if min(xs) >= margin and min(ys) >= margin and max(xs) <= 1 - margin and max(ys) <= 1 - margin:
            return changed
        need = max((max(xs) - min(xs)) / (1 - 2 * margin), (max(ys) - min(ys)) / (1 - 2 * margin), 1.0) * 1.04
        cam = viewport.camera
        eye, target = _v(cam.eye), _v(cam.target)
        look = _norm(_sub(target, eye))
        centre = tuple((a + b) / 2.0 for a, b in zip(lo, hi))
        off = _sub(centre, target)
        shift = _sub(off, tuple(look[i] * _dot(off, look) for i in range(3)))   # (across the view only)
        target = _add(target, shift)
        if cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType:
            cam.viewExtents = cam.viewExtents * need
            eye = _add(eye, shift)
        else:
            eye = _add(target, _sub(_add(eye, shift), target), need)
        cam.eye = _p3(eye)
        cam.target = _p3(target)
        cam.isFitView = False
        cam.isSmoothTransition = False
        viewport.camera = cam
        viewport.refresh()
        changed = True
    return changed


def project(design, viewport, holes, part_bodies, padding=None):
    """Where things are in the viewport (camera already set): 0..1 of its width / height.

    padding: {"left", "top", "right", "bottom"}: extra room around the part, as a fraction of its
    size on screen (the crop can then reach past the viewport; the panel fills that in).
    Returns {"crop": [x, y, w, h] (the part, with a margin), "holes": [{"id", "kind", "label",
    "center", "outline", "r"}]} with hole coordinates already relative to the crop. Holes
    show if an opening faces the camera and nothing is in front of it.
    """
    vw, vh = float(viewport.width), float(viewport.height)
    cam = viewport.camera
    view_dir = _norm(_sub(_v(cam.target), _v(cam.eye)))

    def to_view(p):
        q = viewport.modelToViewSpace(_p3(p))
        return (q.x / vw, q.y / vh)

    # The part's extent on screen: its bounding box corners.
    xs, ys, span = [], [], 0.0
    for body in part_bodies:
        box = body.boundingBox
        lo, hi = _v(box.minPoint), _v(box.maxPoint)
        span = max(span, _dist3(lo, hi))
        for i in range(8):
            corner = (hi[0] if i & 1 else lo[0], hi[1] if i & 2 else lo[1], hi[2] if i & 4 else lo[2])
            x, y = to_view(corner)
            xs.append(x)
            ys.append(y)
    if xs:
        pad = padding or {}
        pw, ph = max(xs) - min(xs), max(ys) - min(ys)
        margin = 0.04 * max(pw, ph)
        x0 = max(0.0, min(xs) - margin) - float(pad.get("left", 0)) * pw
        y0 = max(0.0, min(ys) - margin) - float(pad.get("top", 0)) * ph
        x1 = min(1.0, max(xs) + margin) + float(pad.get("right", 0)) * pw
        y1 = min(1.0, max(ys) + margin) + float(pad.get("bottom", 0)) * ph
    else:
        x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
    cw, ch = max(1e-6, x1 - x0), max(1e-6, y1 - y0)

    def to_crop(p):
        x, y = to_view(p)
        return [(x - x0) / cw, (y - y0) / ch]

    root = design.rootComponent
    far = max(span, 1.0) * 10.0
    out = []
    for h in holes:
        best = None
        for centre, outward in h["openings"]:
            facing = -_dot(outward, view_dir)
            if facing > 0.3 and (best is None or facing > best[0]):
                best = (facing, centre, outward)
        if best is None:
            continue
        _, centre, outward = best
        origin = _add(centre, view_dir, -far)
        hits = adsk.core.ObjectCollection.create()
        try:
            root.findBRepUsingRay(_p3(origin), adsk.core.Vector3D.create(*view_dir),
                                  adsk.fusion.BRepEntityTypes.BRepFaceEntityType, 0.0005, True, hits)
        except Exception:
            log.error("ray")
        blocked = any(_dist3(_v(hp), origin) < far - h["radius"] * 0.3 for hp in hits)
        if blocked:
            continue
        a = h["axis"]
        u = _norm(_cross(a, (1, 0, 0) if abs(a[0]) < 0.9 else (0, 1, 0)))
        v = _cross(a, u)
        r = h["radius"]
        ring = [_add(_add(centre, u, r * math.cos(t)), v, r * math.sin(t))
                for t in (2 * math.pi * i / OUTLINE_POINTS for i in range(OUTLINE_POINTS))]
        outline = [to_crop(p) for p in ring]
        c = to_crop(centre)
        radius = max(math.hypot((p[0] - c[0]) * cw * vw, (p[1] - c[1]) * ch * vh) for p in outline) / (cw * vw)
        out.append({"id": h["id"], "kind": h["kind"], "label": h["label"], "center": c, "outline": outline, "r": radius})
    return {"crop": [x0, y0, cw, ch], "holes": out}
