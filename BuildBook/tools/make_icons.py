"""Draw BuildBook's icons: an exploded box (its lid lifted off, with a dashed trail line).

    python BuildBook/tools/make_icons.py

Writes the toolbar icons Fusion uses (BuildBook/resources/BuildBook/16x16.png, 32x32.png,
64x64.png and the @2x versions) and the explode dialog's button icons (axis_*, flip,
spacing_*, next_new).
Each size is drawn 8x larger and scaled down, so small sizes stay clean. Needs Pillow.
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RES = os.path.join(ROOT, "BuildBook", "resources", "BuildBook")

# Fusion-like blues for the toolbar glyph (transparent background).
GLYPH = {"top": (225, 242, 251), "left": (120, 196, 236), "right": (6, 150, 215), "edge": (4, 98, 142),
         "trail": (4, 98, 142)}


def _prism(draw, cx, yc, e, h, c, stroke):
    """Isometric box: top diamond centred at (cx, yc), edge e, height h (all in px)."""
    dx, dy = e * 0.866, e * 0.5
    top, right, bottom, left = (cx, yc - dy), (cx + dx, yc), (cx, yc + dy), (cx - dx, yc)
    down = lambda p: (p[0], p[1] + h)
    draw.polygon([left, bottom, down(bottom), down(left)], fill=c["left"])
    draw.polygon([bottom, right, down(right), down(bottom)], fill=c["right"])
    draw.polygon([top, right, bottom, left], fill=c["top"])
    for a, b in ((top, right), (right, bottom), (bottom, left), (left, top), (left, down(left)),
                 (bottom, down(bottom)), (right, down(right)), (down(left), down(bottom)), (down(bottom), down(right))):
        draw.line([a, b], fill=c["edge"], width=stroke)


def glyph(size, colors, scale=8, pad=0.08, dashes=True):
    """The exploded box filling a size x size square (transparent), drawn at `scale` x."""
    S = size * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    usable = S * (1 - 2 * pad)
    e = usable / 1.95                    # edge of the box's diamond
    body_h, lid_h, gap = e * 0.62, e * 0.24, e * 0.42
    cx = S / 2
    # Vertical layout: lid top diamond ... gap ... body top diamond ... body sides
    total = e * 0.5 + lid_h + gap + e * 0.5 + body_h + e * 0.5
    y0 = (S - total) / 2 + e * 0.25
    lid_yc = y0 + e * 0.25
    body_yc = lid_yc + lid_h + gap + e * 0.5 + e * 0.0
    stroke = max(1, round(S * 0.018))
    if dashes:                           # trail lines: lid corners down to the body
        for x in (cx - e * 0.866, cx + e * 0.866):
            y = lid_yc + lid_h
            while y < body_yc - e * 0.05:
                d.line([(x, y), (x, min(y + S * 0.035, body_yc))], fill=colors["trail"], width=max(1, stroke))
                y += S * 0.07
    _prism(d, cx, body_yc, e, body_h, colors, stroke)
    _prism(d, cx, lid_yc, e, lid_h, colors, stroke)
    return img.resize((size, size), Image.LANCZOS)


def fitted(size, colors, margin=0.04, **kw):
    """The glyph trimmed to its drawing and scaled to fill the square (toolbar icons)."""
    big = glyph(size * 8, colors, scale=1, pad=0.02, **kw)
    big = big.crop(big.getbbox())
    S = size * 8
    room = S * (1 - 2 * margin)
    k = room / max(big.width, big.height)
    big = big.resize((max(1, round(big.width * k)), max(1, round(big.height * k))), Image.LANCZOS)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(big, ((S - big.width) // 2, (S - big.height) // 2))
    return out.resize((size, size), Image.LANCZOS)


def flip_icon(size, color=(60, 60, 60)):
    """Up / down arrows for the explode dialog's Flip button (drawn 8x, scaled down)."""
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(2, round(S * 0.09))
    for x, up in ((S * 0.32, True), (S * 0.68, False)):
        top, bottom = S * 0.12, S * 0.88
        d.line([(x, top), (x, bottom)], fill=color, width=w)
        tip = top if up else bottom
        back = S * 0.22 * (1 if up else -1)
        d.line([(x, tip), (x - S * 0.16, tip + back)], fill=color, width=w)
        d.line([(x, tip), (x + S * 0.16, tip + back)], fill=color, width=w)
    return img.resize((size, size), Image.LANCZOS)


def next_icon(size, same, color=(60, 60, 60)):
    """Explode dialog "next move" buttons: a part with a plus (these parts again: the part is
    solid; different parts: an empty outline)."""
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(2, round(S * 0.08))
    box = [S * 0.06, S * 0.30, S * 0.62, S * 0.86]
    if same:
        d.rectangle(box, fill=color)
    else:
        d.rectangle(box, outline=color, width=w)
    plus = (40, 150, 70)
    cx, cy, r = S * 0.76, S * 0.26, S * 0.2
    d.line([(cx - r, cy), (cx + r, cy)], fill=plus, width=round(w * 1.4))
    d.line([(cx, cy - r), (cx, cy + r)], fill=plus, width=round(w * 1.4))
    return img.resize((size, size), Image.LANCZOS)


def spacing_icon(size, kind, color=(60, 60, 60)):
    """Explode dialog spacing buttons: uniform (equal gaps), stacked by position (growing gaps),
    stacked in pick order (1 2 3)."""
    from PIL import ImageFont
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(2, round(S * 0.07))
    if kind == "order":
        try:
            font = ImageFont.truetype("arialbd.ttf", int(S * 0.42))
        except Exception:
            font = ImageFont.load_default()
        for i, x in enumerate((0.2, 0.5, 0.8)):
            d.text((S * x, S * (0.78 - 0.25 * i)), str(i + 1), fill=color, font=font, anchor="mm")
        return img.resize((size, size), Image.LANCZOS)
    # three parts in a column, the gaps between them equal or growing
    tops = {"uniform": (0.70, 0.46, 0.22), "stacked": (0.74, 0.56, 0.12), "reverse": (0.12, 0.30, 0.74)}[kind]
    for t in tops:
        d.rectangle([S * 0.28, S * t, S * 0.72, S * (t + 0.14)], fill=color)
    return img.resize((size, size), Image.LANCZOS)


AXIS_COLORS = {"X": (214, 62, 62), "Y": (64, 160, 64), "Z": (40, 110, 220)}     # Fusion's axis colours


def axis_icon(size, kind, dark=False):
    """Explode dialog direction buttons: X / Y / Z (axis arrow + letter), XYZ amounts, Picked."""
    from PIL import ImageFont
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (225, 225, 225) if dark else (60, 60, 60)
    w = max(2, round(S * 0.08))

    def arrow(x0, y0, x1, y1, color):
        d.line([(x0, y0), (x1, y1)], fill=color, width=w)
        import math
        a = math.atan2(y1 - y0, x1 - x0)
        for da in (2.6, -2.6):
            d.line([(x1, y1), (x1 + S * 0.16 * math.cos(a + da), y1 + S * 0.16 * math.sin(a + da))], fill=color, width=w)

    try:
        font = ImageFont.truetype("arialbd.ttf", int(S * 0.62))
    except Exception:
        font = ImageFont.load_default()
    if kind in AXIS_COLORS:
        arrow(S * 0.12, S * 0.88, S * 0.42, S * 0.58, AXIS_COLORS[kind])       # a little axis arrow
        d.text((S * 0.62, S * 0.42), kind, fill=ink, font=font, anchor="mm")
    elif kind == "XYZ":
        o = (S * 0.3, S * 0.7)
        arrow(o[0], o[1], S * 0.9, o[1], AXIS_COLORS["X"])
        arrow(o[0], o[1], S * 0.3, S * 0.1, AXIS_COLORS["Z"])
        arrow(o[0], o[1], S * 0.08, S * 0.92, AXIS_COLORS["Y"])
    else:                                    # Picked: along an edge you pick
        d.line([(S * 0.1, S * 0.9), (S * 0.9, S * 0.1)], fill=ink, width=w)
        d.polygon([(S * 0.52, S * 0.48), (S * 0.52, S * 0.95), (S * 0.64, S * 0.82), (S * 0.75, S * 0.98),
                   (S * 0.82, S * 0.94), (S * 0.71, S * 0.78), (S * 0.88, S * 0.76)], fill=ink)
    return img.resize((size, size), Image.LANCZOS)


def main():
    for kind in ("X", "Y", "Z", "XYZ", "picked"):
        folder = os.path.join(ROOT, "BuildBook", "resources", "axis_" + kind.lower())
        os.makedirs(folder, exist_ok=True)
        for size, name in ((16, "16x16.png"), (32, "32x32.png"), (32, "16x16@2x.png"), (64, "32x32@2x.png")):
            axis_icon(size, kind).save(os.path.join(folder, name))
            axis_icon(size, kind, dark=True).save(os.path.join(folder, name.replace(".png", "-dark.png")))
    flip_dir = os.path.join(ROOT, "BuildBook", "resources", "flip")
    os.makedirs(flip_dir, exist_ok=True)
    for size, name in ((16, "16x16.png"), (32, "32x32.png"), (32, "16x16@2x.png"), (64, "32x32@2x.png")):
        flip_icon(size).save(os.path.join(flip_dir, name))
        flip_icon(size, (220, 220, 220)).save(os.path.join(flip_dir, name.replace(".png", "-dark.png")))
    for kind in ("uniform", "stacked", "reverse", "order"):
        folder = os.path.join(ROOT, "BuildBook", "resources", "spacing_" + kind)
        os.makedirs(folder, exist_ok=True)
        for size, name in ((16, "16x16.png"), (32, "32x32.png"), (32, "16x16@2x.png"), (64, "32x32@2x.png")):
            spacing_icon(size, kind).save(os.path.join(folder, name))
            spacing_icon(size, kind, (220, 220, 220)).save(os.path.join(folder, name.replace(".png", "-dark.png")))
    for name_, same in (("next_new", False),):
        folder = os.path.join(ROOT, "BuildBook", "resources", name_)
        os.makedirs(folder, exist_ok=True)
        for size, name in ((16, "16x16.png"), (32, "32x32.png"), (32, "16x16@2x.png"), (64, "32x32@2x.png")):
            next_icon(size, same).save(os.path.join(folder, name))
            next_icon(size, same, (220, 220, 220)).save(os.path.join(folder, name.replace(".png", "-dark.png")))
    os.makedirs(RES, exist_ok=True)
    for size, name in ((16, "16x16.png"), (32, "32x32.png"), (64, "64x64.png"),
                       (32, "16x16@2x.png"), (64, "32x32@2x.png")):
        # Small sizes without the dashed lines (they'd only add noise at 16 px).
        fitted(size, GLYPH, dashes=size >= 32).save(os.path.join(RES, name))

    print("icons written to", os.path.dirname(RES))


if __name__ == "__main__":
    main()
