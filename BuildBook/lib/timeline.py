"""Animation timeline: which step is shown, how far exploded and where the camera is, at any time.

Pure Python (no Fusion), so it's unit-tested. Assembly (forward): each step starts with its parts
exploded and they move into place, last move first. Disassembly (reverse): the steps run backwards
and their parts move out. The camera glides between the steps' saved views.

A step's explode state is `p` moves: 0 = assembled, n = all n moves done; 1.5 = the first move
done and the second half way (Scene.set_progress draws it).
"""

DEFAULTS = {"moveSeconds": 1.0, "cameraSeconds": 1.0, "pauseSeconds": 0.5, "fps": 30, "videoWidth": 1280}


def _ease(f):
    """Smooth start and stop."""
    f = min(1.0, max(0.0, f))
    return f * f * (3 - 2 * f)


def _lerp(a, b, f):
    return a + (b - a) * f


def lerp_camera(c0, c1, f):
    """A camera part way from c0 to c1 (dicts as capture.camera_to_dict makes)."""
    if not c0 or not c1 or f >= 1.0:
        return c1
    if f <= 0.0:
        return c0
    out = dict(c1)
    for key in ("eye", "target", "up"):
        out[key] = [_lerp(a, b, f) for a, b in zip(c0[key], c1[key])]
    length = sum(v * v for v in out["up"]) ** 0.5 or 1.0
    out["up"] = [v / length for v in out["up"]]
    if c0.get("extents") and c1.get("extents"):
        out["extents"] = _lerp(c0["extents"], c1["extents"], f)
    return out


def build(steps, reverse=False, timing=None):
    """steps: [(step id, number of moves, saved camera or None[, all moves at once])] in book order.
    Returns the segments: [{"start", "end", "step", "p0", "p1", "cam0", "cam1"}]."""
    t = dict(DEFAULTS, **(timing or {}))
    move, cam_time, pause = float(t["moveSeconds"]), float(t["cameraSeconds"]), float(t["pauseSeconds"])
    order = list(reversed(steps)) if reverse else list(steps)
    segments, now, camera = [], 0.0, None

    def add(duration, step, p0, p1, cam0, cam1):
        nonlocal now
        segments.append({"start": now, "end": now + duration, "step": step, "p0": p0, "p1": p1,
                         "cam0": cam0, "cam1": cam1})
        now += duration

    for entry in order:
        step_id, moves, cam = entry[:3]
        together = len(entry) > 3 and entry[3]
        start, end = (0.0, float(moves)) if reverse else (float(moves), 0.0)
        target = cam or camera
        # Glide to the step's view (the first step jumps straight there).
        add(cam_time if camera and target and target != camera else 0.0, step_id, start, start, camera or target, target)
        add(pause, step_id, start, start, target, target)
        add(move * (min(moves, 1) if together else moves), step_id, start, end, target, target)
        add(pause, step_id, end, end, target, target)
        camera = target
    return segments


def total(segments):
    return segments[-1]["end"] if segments else 0.0


def at(segments, time):
    """(step id, p, camera) at `time` seconds."""
    if not segments:
        return None, 0.0, None
    for seg in segments:
        if time < seg["end"] or seg is segments[-1]:
            length = seg["end"] - seg["start"]
            f = _ease((time - seg["start"]) / length) if length > 0 else 1.0
            return seg["step"], _lerp(seg["p0"], seg["p1"], f), lerp_camera(seg["cam0"], seg["cam1"], f)
    return None, 0.0, None
