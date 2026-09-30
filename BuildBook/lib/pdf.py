"""A small PDF writer: pages with text (Helvetica), lines, rectangles and PNG images.

Pure Python (Fusion's Python has no PDF library). Text uses the built-in
Helvetica / Helvetica-Bold fonts with WinAnsi encoding, so no fonts are
embedded; `text_width` uses their real metrics for wrapping. An RGB PNG is
embedded as is (its compressed data with the PNG predictor); an RGBA one is
decoded and flattened onto white.
"""

import struct
import zlib

try:
    from . import crop
except ImportError:                 # tests import lib/ directly
    import crop

LETTER = (612.0, 792.0)

# Advance widths (1/1000 em) for characters 32..126.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
         556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778,
         722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
         278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_HELV_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556,
              556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611,
              778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
              333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611,
              611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]


def _clean(text):
    """Text as WinAnsi (cp1252) bytes; characters it lacks become '?'."""
    return str(text).replace("\t", " ").encode("cp1252", errors="replace")


def text_width(text, size, bold=False):
    table = _HELV_BOLD if bold else _HELV
    total = 0
    for b in _clean(text):
        total += table[b - 32] if 32 <= b <= 126 else 556
    return total * size / 1000.0


def wrap(text, width, size, bold=False):
    """Lines of `text` that fit `width` (explicit newlines kept; long words split)."""
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            candidate = word if not line else line + " " + word
            if text_width(candidate, size, bold) <= width:
                line = candidate
                continue
            if line:
                out.append(line)
            while text_width(word, size, bold) > width and len(word) > 1:     # a very long word
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return out


def fit(text, width, size, bold=False):
    """`text` cut with an ellipsis to fit on one line."""
    if text_width(text, size, bold) <= width:
        return text
    while text and text_width(text + "...", size, bold) > width:
        text = text[:-1]
    return text + "..."


# Fill opacities, 5 % steps (Page.translucent).
_GSTATES = " ".join("/GS{0} << /ca {1} >>".format(p, _num_str) for p, _num_str in
                    ((p, ("%.2f" % (p / 100.0)).rstrip("0").rstrip(".")) for p in range(5, 101, 5)))


def _escape(data):
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _num(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _rgb(color):
    return " ".join(_num(c / 255.0) for c in color)


class Image:
    """An image ready to embed: from PNG bytes."""

    def __init__(self, png):
        self.width, self.height, self.data, self.params = _png_to_pdf(png)


def _png_to_pdf(png):
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, info = 8, [], None
    while pos < len(png):
        length = struct.unpack_from(">I", png, pos)[0]
        tag = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            info = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        pos += 12 + length
    width, height, depth, color_type, _, _, interlace = info
    if depth == 8 and color_type == 2 and not interlace:
        params = "/DecodeParms << /Predictor 15 /Colors 3 /BitsPerComponent 8 /Columns {} >>".format(width)
        return width, height, b"".join(idat), params
    # RGBA (or anything else crop.read_png handles): flatten onto white.
    w, h, channels, rows = crop.read_png(png)
    out = bytearray()
    for row in rows:
        if channels == 4:
            for i in range(0, len(row), 4):
                a = row[i + 3]
                out += bytes(((row[i + k] * a + 255 * (255 - a)) // 255) for k in range(3))
        else:
            out += row
    return w, h, zlib.compress(bytes(out), 6), ""


class Page:
    def __init__(self, size):
        self.size = size
        self.ops = []
        self.images = []            # Image objects used on this page

    def text(self, x, y, text, size=10, bold=False, color=(0, 0, 0)):
        """Text with its baseline at y (points from the bottom)."""
        self.ops.append("BT /{} {} Tf {} rg {} {} Td ({}) Tj ET".format(
            "F2" if bold else "F1", _num(size), _rgb(color), _num(x), _num(y),
            _escape(_clean(text)).decode("latin-1")))

    def text_right(self, x_right, y, text, size=10, bold=False, color=(0, 0, 0)):
        self.text(x_right - text_width(text, size, bold), y, text, size, bold, color)

    def line(self, x1, y1, x2, y2, width=0.5, color=(0, 0, 0)):
        self.ops.append("{} w {} RG {} {} m {} {} l S".format(
            _num(width), _rgb(color), _num(x1), _num(y1), _num(x2), _num(y2)))

    def rect(self, x, y, w, h, fill=None, stroke=None, width=0.5):
        parts = []
        if fill is not None:
            parts.append("{} rg".format(_rgb(fill)))
        if stroke is not None:
            parts.append("{} w {} RG".format(_num(width), _rgb(stroke)))
        op = "B" if fill is not None and stroke is not None else ("f" if fill is not None else "S")
        parts.append("{} {} {} {} re {}".format(_num(x), _num(y), _num(w), _num(h), op))
        self.ops.append(" ".join(parts))

    # ---- shapes (annotations): a path, then stroke / fill it
    _K = 0.5522847498                    # bezier circle constant

    def raw(self, op):
        self.ops.append(op)

    def style(self, color=(0, 0, 0), width=1.0, dash=None, cap_round=True):
        """Stroke + fill colour, line width and dash pattern for the next shapes."""
        self.ops.append("{} w {} RG {} rg {} J {} j [{}] 0 d".format(
            _num(width), _rgb(color), _rgb(color), 1 if cap_round else 0, 1 if cap_round else 0,
            " ".join(_num(d) for d in (dash or []))))

    def polyline(self, points, close=False, op="S"):
        path = " ".join("{} {} {}".format(_num(x), _num(y), "m" if i == 0 else "l") for i, (x, y) in enumerate(points))
        self.ops.append(path + (" h" if close else "") + " " + op)

    def ellipse(self, cx, cy, rx, ry, op="S"):
        k = self._K
        self.ops.append(" ".join([
            "{} {} m".format(_num(cx + rx), _num(cy)),
            "{} {} {} {} {} {} c".format(_num(cx + rx), _num(cy + k * ry), _num(cx + k * rx), _num(cy + ry), _num(cx), _num(cy + ry)),
            "{} {} {} {} {} {} c".format(_num(cx - k * rx), _num(cy + ry), _num(cx - rx), _num(cy + k * ry), _num(cx - rx), _num(cy)),
            "{} {} {} {} {} {} c".format(_num(cx - rx), _num(cy - k * ry), _num(cx - k * rx), _num(cy - ry), _num(cx), _num(cy - ry)),
            "{} {} {} {} {} {} c".format(_num(cx + k * rx), _num(cy - ry), _num(cx + rx), _num(cy - k * ry), _num(cx + rx), _num(cy)),
            op]))

    def translucent(self, on, alpha=0.15):
        """Fills at `alpha` opacity (rounded to 5 %), or opaque again."""
        pct = max(5, min(100, int(round(alpha * 20)) * 5)) if on else 100
        self.ops.append("/GS{} gs".format(pct))

    def image(self, image, x, y, w, h):
        """Draw an Image with its bottom-left corner at (x, y), scaled to w x h."""
        if image not in self.images:
            self.images.append(image)
        self.ops.append("q {} 0 0 {} {} {} cm /Im{} Do Q".format(
            _num(w), _num(h), _num(x), _num(y), id(image)))


class Document:
    def __init__(self, size=LETTER, title=""):
        self.size = size
        self.title = title
        self.pages = []

    def add_page(self):
        page = Page(self.size)
        self.pages.append(page)
        return page

    def to_bytes(self):
        objects = []                    # object bodies (bytes), numbered from 1

        def add(body):
            objects.append(body)
            return len(objects)

        font1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        font2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        image_obj = {}
        for page in self.pages:
            for img in page.images:
                if id(img) not in image_obj:
                    head = ("<< /Type /XObject /Subtype /Image /Width {} /Height {} /ColorSpace /DeviceRGB "
                            "/BitsPerComponent 8 /Filter /FlateDecode {} /Length {} >>\nstream\n").format(
                        img.width, img.height, img.params, len(img.data)).encode("latin-1")
                    image_obj[id(img)] = add(head + img.data + b"\nendstream")
        pages_id = len(objects) + 1 + 2 * len(self.pages)     # after every page and its content
        page_ids = []
        for page in self.pages:
            content = zlib.compress("\n".join(page.ops).encode("latin-1"), 6)
            content_id = add(b"<< /Filter /FlateDecode /Length %d >>\nstream\n" % len(content) + content
                             + b"\nendstream")
            xobjects = " ".join("/Im{} {} 0 R".format(id(img), image_obj[id(img)]) for img in page.images)
            page_ids.append(add((
                "<< /Type /Page /Parent {} 0 R /MediaBox [0 0 {} {}] /Contents {} 0 R "
                "/Resources << /Font << /F1 {} 0 R /F2 {} 0 R >> /XObject << {} >> "
                "/ExtGState << {} >> >> >>").format(
                    pages_id, _num(page.size[0]), _num(page.size[1]), content_id, font1, font2,
                    xobjects, _GSTATES).encode("latin-1")))
        assert add("<< /Type /Pages /Kids [{}] /Count {} >>".format(
            " ".join("{} 0 R".format(i) for i in page_ids), len(page_ids)).encode("latin-1")) == pages_id
        catalog = add("<< /Type /Catalog /Pages {} 0 R >>".format(pages_id).encode("latin-1"))
        info = add(b"<< /Title (" + _escape(_clean(self.title)) + b") /Producer (BuildBook) >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for n, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, catalog, info, xref)
        return bytes(out)
