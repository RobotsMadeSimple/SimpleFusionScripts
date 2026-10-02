"""Draw BuildBook's icons: an exploded box (its lid lifted off, with a dashed trail line).

    python store/BuildBook/make_icons.py

Writes the toolbar icons Fusion uses (BuildBook/resources/BuildBook/16x16.png, 32x32.png,
64x64.png and the @2x versions) and the store icons (store/BuildBook/icon-*.png).
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
# White tints on a blue tile for the store icon.
TILE = (6, 150, 215)
STORE = {"top": (255, 255, 255), "left": (214, 238, 250), "right": (170, 218, 243), "edge": (3, 92, 134),
         "trail": (255, 255, 255)}


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


def store_icon(size):
    S = size * 4
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.2, fill=TILE)
    g = glyph(S, STORE, scale=2, pad=0.2)
    tile.alpha_composite(g)
    return tile.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(RES, exist_ok=True)
    for size, name in ((16, "16x16.png"), (32, "32x32.png"), (64, "64x64.png"),
                       (32, "16x16@2x.png"), (64, "32x32@2x.png")):
        # Small sizes without the dashed lines (they'd only add noise at 16 px).
        fitted(size, GLYPH, dashes=size >= 32).save(os.path.join(RES, name))
    for size in (120, 512, 1024):
        store_icon(size).save(os.path.join(HERE, "icon-{}.png".format(size)))
    print("icons written to", RES, "and", HERE)


if __name__ == "__main__":
    main()
