"""Crop ratio area and image cropping, in pure Python (no adsk import).

Export captures the largest rectangle of the manual's crop ratio centred in
the viewport, handled as fractions of the viewport: {"x", "y", "w", "h"} with
x/y the top-left corner (y down). Fusion's Python has no image library, so
cropping reads the uncompressed BMP Fusion can render and writes the PNG with
zlib; a pure-Python PNG decoder is the (slow) fallback for transparent images.
"""

import struct
import zlib

VIEWPORT = "viewport"
FREE = "free"           # older manuals only; treated as the whole view

# (key, label, width / height or None)
RATIOS = [
    (VIEWPORT, "Match viewport", None),
    ("16:9", "16:9", 16 / 9),
    ("3:2", "3:2", 3 / 2),
    ("4:3", "4:3", 4 / 3),
    ("1:1", "Square", 1.0),
    ("4:5", "4:5 portrait", 4 / 5),
    ("letterL", "Letter landscape", 11 / 8.5),
    ("letterP", "Letter portrait", 8.5 / 11),
]


def ratio_value(key, vw, vh):
    """Pixel width/height ratio for a ratio key, or None for free."""
    if key == VIEWPORT:
        return vw / float(vh)
    for k, _, value in RATIOS:
        if k == key:
            return value
    return None


# ---------------------------------------------------------------- rectangles (pixels)

def centered_rect(ratio, vw, vh, fill=1.0):
    """Largest rectangle of `ratio` centred in the viewport, scaled by `fill`."""
    if not ratio:
        w, h = vw, vh
    elif vw / float(vh) > ratio:
        h = vh
        w = h * ratio
    else:
        w = vw
        h = w / ratio
    w, h = w * fill, h * fill
    return ((vw - w) / 2.0, (vh - h) / 2.0, w, h)


def to_pixels(crop, vw, vh):
    return (crop["x"] * vw, crop["y"] * vh, crop["w"] * vw, crop["h"] * vh)


def from_pixels(rect, vw, vh):
    left, top, w, h = rect
    return {"x": left / vw, "y": top / vh, "w": w / vw, "h": h / vh}


def is_full(crop, tol=1e-3):
    return (crop is None or (abs(crop["x"]) < tol and abs(crop["y"]) < tol
                             and abs(crop["w"] - 1) < tol and abs(crop["h"] - 1) < tol))


# ---------------------------------------------------------------- export plan

MAX_SIDE = 8000
MAX_PIXELS = 40_000_000


def render_plan(crop, out_width, vw, vh):
    """How to render a crop so it comes out `out_width` pixels wide.

    Returns (render_w, render_h, box) where box = (left, top, width, height)
    in render pixels. The render keeps the viewport's shape so the crop maps
    exactly; very large renders are scaled down (the output gets narrower).
    """
    cw = crop["w"] * vw
    scale = out_width / cw
    rw, rh = vw * scale, vh * scale
    shrink = min(1.0, MAX_SIDE / rw, MAX_SIDE / rh, (MAX_PIXELS / (rw * rh)) ** 0.5)
    rw, rh = int(round(rw * shrink)), int(round(rh * shrink))
    left = int(round(crop["x"] * rw))
    top = int(round(crop["y"] * rh))
    w = max(1, min(int(round(crop["w"] * rw)), rw - left))
    h = max(1, min(int(round(crop["h"] * rh)), rh - top))
    return rw, rh, (left, top, w, h)


# ---------------------------------------------------------------- BMP -> cropped PNG

def read_bmp(data):
    """(width, height, channels, rows_top_down) from an uncompressed 24/32-bit BMP.

    Rows are bytes in BGR / BGRA order.
    """
    if data[:2] != b"BM":
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if bpp not in (24, 32) or compression not in (0, 3):
        raise ValueError("unsupported BMP ({} bpp, compression {})".format(bpp, compression))
    channels = bpp // 8
    stride = ((bpp * width + 31) // 32) * 4
    bottom_up = height > 0
    height = abs(height)
    rows = [data[offset + r * stride: offset + r * stride + width * channels] for r in range(height)]
    if bottom_up:
        rows.reverse()
    return width, height, channels, rows


def crop_rows(rows, channels, box, swap_bgr=False, keep_alpha=True):
    """Cut `box` out of pixel rows; optionally BGR(A) -> RGB(A) and drop alpha."""
    left, top, w, h = box
    out = []
    out_channels = channels if keep_alpha else min(channels, 3)
    for row in rows[top:top + h]:
        seg = row[left * channels:(left + w) * channels]
        if swap_bgr or out_channels != channels:
            src = seg
            seg = bytearray(w * out_channels)
            if swap_bgr:
                seg[0::out_channels] = src[2::channels]
                seg[1::out_channels] = src[1::channels]
                seg[2::out_channels] = src[0::channels]
            else:
                for c in range(3):
                    seg[c::out_channels] = src[c::channels]
            if out_channels == 4:
                seg[3::4] = src[3::channels]
        out.append(bytes(seg))
    return out, out_channels


def has_alpha(rows, channels):
    """True if a 4-channel image actually uses its alpha channel.

    All-opaque or all-zero alpha (some BMP writers leave it blank) means no.
    """
    if channels != 4:
        return False
    values = set()
    for row in rows[:: max(1, len(rows) // 64)]:
        values.update(row[3::4])
    return bool(values - {255}) and values != {0}


def write_png(rows, width, channels):
    """Encode rows (RGB or RGBA bytes) as a PNG file's bytes."""
    color_type = 6 if channels == 4 else 2
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, len(rows), 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- PNG decode (fallback)

def read_png(data):
    """(width, height, channels, rows) from an 8-bit RGB/RGBA non-interlaced PNG.

    Pure Python, so slow on big images; only used when BMP can't carry the
    transparency.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, width = 8, [], None
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or color_type not in (2, 6) or interlace:
                raise ValueError("unsupported PNG")
            channels = 4 if color_type == 6 else 3
        elif tag == b"IDAT":
            idat.append(body)
        pos += 12 + length
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows, prev = [], bytearray(stride)
    for r in range(height):
        start = r * (stride + 1)
        ftype = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows
