"""Thread callouts: sizes, colours, which view calls out which hole, and the annotations.

Pure Python (tests/test_callouts.py). Fusion-facing code (lib/holes.py) finds the
holes and projects them into each view; this decides what's drawn:

- every threaded hole is called out once: in the first thread view it's visible in
  (unless the user pinned it to another view);
- in a view, each called-out hole gets a filled outline in its size's colour
  (colour-blind-safe Okabe-Ito colours: M3 yellow, M4 sky blue, M5 orange, M6
  reddish purple...), dowels get the quartered-circle symbol;
- each size (and each dowel diameter) gets one callout, "2x M3", with an arrow to
  one of its holes. Callouts the user moved keep their place (and their arrow's hole,
  and their text if they changed it); new ones go beside the part, in the room left
  and right of it (side_room), on the side nearer their hole, clear of each other;
- labels are sized for the page: label_scale() makes them bigger on a wide image (many
  views side by side), which a page shrinks to fit its width.

Coordinates are 0..1 of the view's (cropped) image, y down, as in palette/annot_draw.js.
"""

import math
import re

# Okabe & Ito, "Color Universal Design": distinguishable with every common colour-vision deficiency.
OKABE_ITO = {
    "yellow": "#F0E442", "sky blue": "#56B4E9", "orange": "#E69F00", "reddish purple": "#CC79A7",
    "bluish green": "#009E73", "blue": "#0072B2", "vermillion": "#D55E00", "black": "#000000",
}
DEFAULT_COLORS = {
    "M2": "#009E73", "M2.5": "#0072B2", "M3": "#F0E442", "M4": "#56B4E9", "M5": "#E69F00",
    "M6": "#CC79A7", "M8": "#D55E00", "M10": "#0072B2", "M12": "#009E73",
}
SPARE_COLORS = ["#D55E00", "#009E73", "#0072B2", "#CC79A7", "#56B4E9", "#E69F00", "#F0E442"]

# Metric coarse tap drills (mm) -> size, for holes with no thread modelled.
TAP_DRILLS = [(1.6, "M2"), (2.05, "M2.5"), (2.5, "M3"), (3.3, "M4"), (4.2, "M5"), (5.0, "M6"),
              (6.8, "M8"), (8.5, "M10"), (10.2, "M12")]
TAP_TOLERANCE = 0.06            # mm

TEXT = {"color": "#222222", "size": 48, "bold": True, "box": True, "leader": True, "weight": 9}
CHAR_W = 0.62           # average width of a bold Helvetica character, in text heights
EDGE = 0.015            # labels' distance from the image's side (0..1 of the view's width)
GAP = 0.12              # room between a label and the part (in image heights)
PAGE_ASPECT = 2.6       # width / height a page shows an image at before shrinking it to fit
ROW_ASPECT = 4.2        # wider than this, the views wrap into another row (below that, labels grow instead)
MAX_SCALE = 2.2
MARK_OUTLINE = "#333333"


def size_label(designation):
    """"M3x0.5" -> "M3", "M2.5x0.45" -> "M2.5", "1/4-20 UNC" -> "1/4-20", "" -> "?"."""
    text = (designation or "").strip()
    m = re.match(r"(?i)^(M\d+(?:\.\d+)?)", text)
    if m:
        return "M" + m.group(1)[1:]
    m = re.match(r"^(#?\d+(?:/\d+)?-\d+)", text)
    if m:
        return m.group(1)
    return text or "?"


def guess_size(diameter_mm):
    """Thread size whose tap drill matches a plain hole's diameter, or None."""
    for drill, size in TAP_DRILLS:
        if abs(diameter_mm - drill) <= TAP_TOLERANCE:
            return size
    return None


def dowel_label(diameter_mm):
    return "Ø{:g}".format(round(diameter_mm, 2))


def size_key(label):
    """Sort sizes by number: M2.5 before M3 before M10; others after, by name."""
    m = re.match(r"M(\d+(?:\.\d+)?)", label or "")
    return (0, float(m.group(1)), "") if m else (1, 0.0, label or "")


def color_for(label, colors=None):
    colors = dict(DEFAULT_COLORS, **(colors or {}))
    if label in colors:
        return colors[label]
    # A size without a colour: a spare one, the same every time for that size.
    return SPARE_COLORS[sum(ord(c) for c in label) % len(SPARE_COLORS)]


def assign(view_order, visible, pinned=None):
    """{hole id: view id}: each hole goes to the first view (in order) it's visible in.

    view_order: [view id] (thread views, export order); visible: {view id: set(hole ids)};
    pinned: {hole id: view id} chosen by the user (used if that hole is visible there).
    """
    out = {}
    pinned = pinned or {}
    for hole, view in pinned.items():
        if hole in visible.get(view, ()):
            out[hole] = view
    for view in view_order:
        for hole in sorted(visible.get(view, ())):
            out.setdefault(hole, view)
    return out


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def rows(aspects, gap=0.04, target=None, last_stays=True):
    """The stitched image's rows: [[view index, ...], ...], views in order, a new row when the next
    view would make the row wider than `target` (width / height, default ROW_ASPECT; a view wider than
    that gets a row of its own). With last_stays the last view (the shaded one) always ends the last
    row, on the right. Keeps a many-view image from getting so wide that a page shrinks it to nothing."""
    target = ROW_ASPECT if target is None else target
    out, width = [], 0.0
    for i, a in enumerate(aspects):
        last = last_stays and i == len(aspects) - 1 and out
        if out and (last or width + gap + a <= target):
            out[-1].append(i)
            width += gap + a
        else:
            out.append([i])
            width = a
    return out


def _row_aspect(aspects, row, gap):
    return sum(aspects[i] for i in row) + gap * max(0, len(row) - 1)


def label_scale(aspects, gap=0.04):
    """How much bigger labels must be on a stitched image of views with these width/height ratios
    (gaps as a fraction of the height), laid out in rows(): a page shrinks an image to fit its width,
    so a row wider than PAGE_ASPECT would make the text small. 1 when every row fits."""
    widest = max((_row_aspect(aspects, r, gap) for r in rows(aspects, gap)), default=0.0)
    return max(1.0, min(MAX_SCALE, widest / PAGE_ASPECT))


def label_width(text, size):
    """A label's width in image heights (size: text height in 1/1000 of the image height), its box included."""
    longest = max((len(line) for line in str(text).splitlines()), default=0)
    return (CHAR_W * longest + 0.6) * size / 1000.0


def label_side(xs):
    """"left" or "right": the side of the view nearer a group of holes (their x, 0..1 of the view)."""
    return "left" if min(xs) <= 1.0 - max(xs) else "right"


def side_room(labels, scale, part_aspect):
    """Padding for a view's sides (fractions of the part's width on screen, as holes.project takes it)
    so its labels fit beside the part: labels = [(text, "left"|"right")]; each side gets room for
    its widest label plus a gap for the arrow, and none if it has no label."""
    room = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    for text, side in labels:
        need = (label_width(text, TEXT["size"] * scale) + GAP + EDGE) / max(part_aspect, 1e-3)
        room[side] = max(room[side], need)
    return room


def _place_side(target, text, size, aspect, taken, side=None):
    """A new label's top-left: in the room beside the part on `side` (default: the side nearer its
    hole), level with the hole, inside the image and clear of the labels already placed."""
    w = label_width(text, size) / max(aspect, 1e-3)              # (0..1 of the view's width)
    h = (1.25 + 0.6) * size / 1000.0                             # (0..1 of its height, box included)
    left = (side == "left") if side else target[0] <= 0.5
    x = EDGE if left else max(EDGE, 1.0 - EDGE - w)
    y = min(max(0.01, target[1] - h / 2), 0.99 - h)
    for _ in range(40):                     # step clear of the labels on the same side
        clash = [t for t in taken if t[0] == left and abs(t[1] - y) < h * 1.15]
        if not clash:
            break
        y = max(c[1] for c in clash) + h * 1.2
        if y > 0.99 - h:
            y = 0.01 + (y - (0.99 - h)) % max(0.01, 0.98 - h)
    taken.append((left, y))
    return x, y


def _place_text(target, centre, taken):
    """Where a new callout's text goes: out from the part's middle, past its hole, clear of others."""
    dx, dy = target[0] - centre[0], target[1] - centre[1]
    length = math.hypot(dx, dy) or 1.0
    dx, dy = dx / length, dy / length
    if abs(dx) < 0.25 and abs(dy) < 0.25:
        dx, dy = -0.7, -0.7
    x = min(0.82, max(0.02, target[0] + dx * 0.18))
    y = min(0.92, max(0.03, target[1] + dy * 0.18))
    for _ in range(12):                     # step down until clear of the other callouts
        if all(_dist((x, y), t) > 0.09 for t in taken):
            break
        y = y + 0.09 if y < 0.8 else max(0.03, y - 0.5)
    return x, y


def build(view, holes, assigned, previous=None, colors=None, aspect=None, scale=1.0, sides=None):
    """The annotations of one view.

    view: {"id", "kind": "thread"|"shaded"}.
    holes: [{"id", "kind": "thread"|"dowel", "label" ("M3" / "Ø5"), "center": [x, y],
             "outline": [[x, y], ...], "r": radius (0..1 of the image width)}] -- those visible here.
    assigned: {hole id: view id} (assign()).
    previous: the view's annotations as last saved (the user's own, and moved callouts).
    aspect: the view's width / height: new labels go beside the part (_place_side); None = the old
    placement near their hole. scale: label_scale() of the whole image. sides: {(kind, label): "left" |
    "right"}, the side each label goes on (where side_room made room); default the nearer side.
    Returns user annotations (kept as they are) + auto ones (each with "auto": "hole:<id>",
    "dowel:<id>" or "callout:<kind>:<label>", locked for hole marks).
    """
    previous = previous or []
    own = [a for a in previous if not a.get("auto")]
    if view.get("kind") != "thread":
        return own
    mine = [h for h in holes if assigned.get(h["id"]) == view["id"]]
    old = {a["auto"]: a for a in previous if a.get("auto")}
    out = []
    for h in mine:
        if h["kind"] == "dowel":
            out.append({"type": "dowel", "auto": "dowel:" + h["id"], "locked": True,
                        "x1": h["center"][0], "y1": h["center"][1], "r": h["r"], "color": "#111111", "weight": 2})
        else:
            out.append({"type": "poly", "auto": "hole:" + h["id"], "locked": True, "points": h["outline"],
                        "color": MARK_OUTLINE, "weight": 1.5, "fill": True, "fillColor": color_for(h["label"], colors),
                        "fillOpacity": 0.9})
    groups = {}
    for h in mine:
        groups.setdefault((h["kind"], h["label"]), []).append(h)
    centre = (sum(h["center"][0] for h in mine) / len(mine), sum(h["center"][1] for h in mine) / len(mine)) \
        if mine else (0.5, 0.5)
    taken, placed = [], []
    for (kind, label), group in sorted(groups.items(), key=lambda kv: (kv[0][0] == "dowel", size_key(kv[0][1]))):
        key = "callout:{}:{}".format(kind, label)
        text = "{}x {}".format(len(group), label + (" dowel" if kind == "dowel" else ""))
        prev = old.get(key)
        by_id = {h["id"]: h for h in group}
        if prev is not None:
            target = by_id.get(prev.get("target")) or min(group, key=lambda h: _dist(h["center"], (prev["x2"], prev["y2"])))
            note = dict(prev)
            note["target"] = target["id"]
            note["x2"], note["y2"] = target["center"]
            if not prev.get("customText"):
                note["text"] = text
            note["count"] = len(group)
        else:
            if aspect:
                # the label goes beside the part on its side, level with the group's hole nearest that side
                side = (sides or {}).get((kind, label)) or label_side([h["center"][0] for h in group])
                target = min(group, key=lambda h: (h["center"][0] if side == "left" else -h["center"][0],
                                                   h["center"][1]))
                size = TEXT["size"] * scale
                x, y = _place_side(target["center"], text, size, aspect, placed, side)
                note = dict(TEXT, type="text", auto=key, text=text, x1=x, y1=y, x2=target["center"][0],
                            y2=target["center"][1], target=target["id"], count=len(group),
                            size=round(size, 1), weight=round(TEXT["weight"] * scale, 1))
            else:
                target = min(group, key=lambda h: (h["center"][1], h["center"][0]))     # the top one
                x, y = _place_text(target["center"], centre, taken)
                note = dict(TEXT, type="text", auto=key, text=text, x1=x, y1=y, x2=target["center"][0],
                            y2=target["center"][1], target=target["id"], count=len(group))
        taken.append((note["x1"], note["y1"]))
        out.append(note)
    return own + out


def remap(annotations, old_crop, new_crop):
    """Annotations placed relative to one crop of a view, moved to another crop of the same render
    geometry (padding or camera framing changed), so they stay on the same spot of the picture.
    Crops are [x, y, w, h] in the same (viewport) coordinates."""
    if not old_crop or not new_crop or list(old_crop) == list(new_crop):
        return annotations
    ox, oy, ow, oh = old_crop
    nx, ny, nw, nh = new_crop

    def pt(x, y):
        return (ox + x * ow - nx) / nw, (oy + y * oh - ny) / nh

    out = []
    for a in annotations:
        a = dict(a)
        for kx, ky in (("x1", "y1"), ("x2", "y2")):
            if isinstance(a.get(kx), (int, float)) and isinstance(a.get(ky), (int, float)):
                a[kx], a[ky] = pt(a[kx], a[ky])
        if isinstance(a.get("points"), list):
            a["points"] = [list(pt(p[0], p[1])) for p in a["points"]]
        if isinstance(a.get("r"), (int, float)):
            a["r"] = a["r"] * ow / nw
        out.append(a)
    return out


def merge_saved(previous_auto, saved):
    """After the editor saves: keep what the user did to auto callouts (moved text, re-aimed arrow,
    new text) and their own annotations; hole marks are always regenerated.

    Returns the list to store. An arrow dragged onto another hole of its group is re-aimed
    at it (the nearest hole); the editor sends x2/y2, and build() re-snaps on the next build.
    """
    out = []
    for a in saved:
        auto = a.get("auto", "")
        if auto.startswith("hole:") or auto.startswith("dowel:"):
            continue
        if auto.startswith("callout:"):
            before = next((p for p in previous_auto if p.get("auto") == auto), None)
            a = dict(a)
            if before is not None and a.get("text") != before.get("text"):
                a["customText"] = True
            a["target"] = None if before is None or (a.get("x2"), a.get("y2")) != (before.get("x2"), before.get("y2")) \
                else before.get("target")
        out.append(a)
    return out
