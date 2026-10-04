"""The build manual as a PDF (Letter portrait). Pure Python; tests/test_pdf.py.

Input (built by BuildBook.py from the manual and the design):
    {"title", "design", "date",
     "cover_image": PNG bytes or None, "cover_annotations": [...],
     "sections": [{"number": 2, "title", "bom": [{"qty", "name"}], "image": PNG or None, "annotations",
                   "steps": [{"number": "2.3", "title", "notes", "image": PNG bytes or None,
                              "annotations": [...], "bom": [...]}]}],
     "bom": [...]}                                      # the whole manual's parts
Annotations (palette/annot_draw.js) are drawn over the pictures as vector graphics.

Pages: a cover (title, design, date, the assembly's picture) with the contents and
page numbers, then each section's own page (title, picture, parts list), its steps flowing
one after another (a step moves to the next page when its heading, notes and
image don't fit; a long parts list carries on over the page), and the full
parts list at the end. Every page but the cover has a footer:
"<title> · Page N of M".
"""

try:
    from . import pdf
except ImportError:                 # tests import lib/ directly
    import pdf

W, H = pdf.LETTER
MARGIN = 54.0                       # 0.75 in
CONTENT_W = W - 2 * MARGIN
TOP = H - MARGIN
BOTTOM = MARGIN + 22                # room for the footer
IMAGE_MAX_H = 320.0

INK = (34, 34, 34)
MUTED = (110, 110, 110)
ACCENT = (6, 150, 215)
RULE = (205, 205, 205)
ZEBRA = (244, 246, 248)

ROW_H = 15.0
TABLE_SIZE = 9.5
QTY_W = 42.0


class _Flow:
    """A cursor over pages: y goes down from TOP; new_page() when something doesn't fit."""

    def __init__(self, doc):
        self.doc = doc
        self.page, self.y = None, 0.0

    def new_page(self):
        self.page = self.doc.add_page()
        self.y = TOP

    def room(self):
        return self.y - BOTTOM

    def need(self, height):
        if self.page is None or self.room() < height:
            self.new_page()


def _table(flow, rows, heading):
    """A Qty / Part table; continues on the next page (with its header again) when needed."""
    def header():
        flow.need(ROW_H * 2 + 18)
        p = flow.page
        p.text(MARGIN, flow.y - 11, heading, 10.5, True, INK)
        flow.y -= 18
        p.rect(MARGIN, flow.y - ROW_H, CONTENT_W, ROW_H, fill=(232, 236, 240))
        p.text(MARGIN + 6, flow.y - ROW_H + 4.5, "Qty", 8.5, True, MUTED)
        p.text(MARGIN + QTY_W, flow.y - ROW_H + 4.5, "Part", 8.5, True, MUTED)
        flow.y -= ROW_H

    header()
    if not rows:
        flow.page.text(MARGIN + 6, flow.y - ROW_H + 4.5, "No parts.", TABLE_SIZE, False, MUTED)
        flow.y -= ROW_H
    stripe = 0
    for i, row in enumerate(rows):
        if flow.room() < ROW_H * (2 if row.get("hw") and not rows[i - 1].get("hw", False) else 1):
            flow.new_page()
            header()
        p = flow.page
        if row.get("hw") and (i == 0 or not rows[i - 1].get("hw")):
            # Hardware (screws, nuts, washers...) under its own heading, after the parts.
            flow.y -= 4
            p.text(MARGIN + 6, flow.y - ROW_H + 4.5, "HARDWARE", 8, True, ACCENT)
            p.line(MARGIN, flow.y - ROW_H + 1, MARGIN + CONTENT_W, flow.y - ROW_H + 1, 0.5, RULE)
            flow.y -= ROW_H
            stripe = 0
        stripe += 1
        if stripe % 2 == 0:
            p.rect(MARGIN, flow.y - ROW_H, CONTENT_W, ROW_H, fill=ZEBRA)
        p.text_right(MARGIN + QTY_W - 14, flow.y - ROW_H + 4.5, str(row["qty"]), TABLE_SIZE, True, INK)
        p.text(MARGIN + QTY_W, flow.y - ROW_H + 4.5,
               pdf.fit(row["name"], CONTENT_W - QTY_W - 6, TABLE_SIZE), TABLE_SIZE, False, INK)
        flow.y -= ROW_H
    flow.page.line(MARGIN, flow.y, MARGIN + CONTENT_W, flow.y, 0.5, RULE)
    flow.y -= 14


def _step(flow, step, image):
    """Draw a step; returns the page number its heading is on."""
    repeat = int(step.get("repeat") or 1)
    title_lines = pdf.wrap("{}  {}{}".format(step["number"], step["title"] or "Untitled step",
                                             "  ×{}".format(repeat) if repeat > 1 else ""),
                           CONTENT_W, 14, True)
    notes = pdf.wrap(step["notes"], CONTENT_W, 10) if (step.get("notes") or "").strip() else []
    img_w = img_h = 0
    if image is not None:
        img_w = CONTENT_W
        img_h = img_w * image.height / float(image.width)
        if img_h > IMAGE_MAX_H:
            img_h = IMAGE_MAX_H
            img_w = img_h * image.width / float(image.height)
    block = len(title_lines) * 18 + len(notes) * 13 + (img_h + 10 if img_h else 0) + 8 + ROW_H * 3
    flow.need(min(block, TOP - BOTTOM))
    start = len(flow.doc.pages)
    p = flow.page
    for line in title_lines:
        p.text(MARGIN, flow.y - 14, line, 14, True, INK)
        flow.y -= 18
    if repeat > 1:
        p.text(MARGIN, flow.y - 10, "Repeat this step {} times.".format(repeat), 10, True, ACCENT)
        flow.y -= 15
    for line in notes:
        if flow.room() < 13:
            flow.new_page()
            p = flow.page
        p.text(MARGIN, flow.y - 10, line, 10, False, (60, 60, 60))
        flow.y -= 13
    if img_h:
        flow.y -= 4
        if flow.room() < img_h:
            flow.new_page()
            p = flow.page
        p.image(image, MARGIN + (CONTENT_W - img_w) / 2, flow.y - img_h, img_w, img_h)
        _annotations(p, step.get("annotations"), MARGIN + (CONTENT_W - img_w) / 2, flow.y - img_h, img_w, img_h)
        flow.y -= img_h + 8
    _table(flow, step["bom"], "Parts for this step" + (" (each time)" if repeat > 1 else ""))
    flow.y -= 10
    return start


def _hex(color, default=(217, 70, 62)):
    try:
        c = str(color).lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except (ValueError, IndexError):
        return default


def _num_or(v, d):
    return v if isinstance(v, (int, float)) else d


def _arrow(p, x1, y1, x2, y2, heads, weight, U):
    """A line from (x1,y1) to (x2,y2) (PDF points) with arrow heads, as annot_draw.js."""
    import math
    L = (4 * weight + 6) * U
    s, e = (x1, y1), (x2, y2)

    def shorten(ax, ay, bx, by, d):
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            return bx, by
        k = max(0.0, length - d) / length
        return ax + (bx - ax) * k, ay + (by - ay) * k

    if heads in ("end", "both"):
        e = shorten(x1, y1, x2, y2, 0.8 * L)
    if heads == "both":
        s = shorten(x2, y2, x1, y1, 0.8 * L)
    p.polyline([s, e])
    p.raw("[] 0 d")
    for want, ax, ay, bx, by in (("end", x1, y1, x2, y2), ("start", x2, y2, x1, y1)):
        if not (heads == "both" or (heads == "end" and want == "end")):
            continue
        a = math.atan2(by - ay, bx - ax)
        w = 0.45 * L
        cx, cy = bx - math.cos(a) * L, by - math.sin(a) * L
        p.polyline([(bx, by), (cx + math.sin(a) * w, cy - math.cos(a) * w),
                    (cx - math.sin(a) * w, cy + math.cos(a) * w)], close=True, op="f")


def _annotations(p, anns, x, y, w, h):
    """Draw annotations (palette/annot_draw.js) over an image placed at x, y (bottom-left), w x h."""
    import math
    U = w / 1000.0
    for a in anns or []:
        try:
            kind = a.get("type")
            weight = _num_or(a.get("weight"), 3)
            color = _hex(a.get("color"))
            stroke = weight * U
            X1 = x + _num_or(a.get("x1"), 0) * w
            Y1 = y + (1 - _num_or(a.get("y1"), 0)) * h
            X2 = x + _num_or(a.get("x2", a.get("x1")), 0) * w
            Y2 = y + (1 - _num_or(a.get("y2", a.get("y1")), 0)) * h
            size = _num_or(a.get("size"), 28) * U
            p.raw("q")
            p.style(color, stroke, [4 * stroke, 3 * stroke] if a.get("dashed") else None)
            if kind in ("arrow", "line"):
                heads = a.get("heads") or ("end" if kind == "arrow" else "none")
                _arrow(p, X1, Y1, X2, Y2, heads, weight, U)
            elif kind in ("rect", "ellipse"):
                bx, by, bw, bh = min(X1, X2), min(Y1, Y2), abs(X2 - X1), abs(Y2 - Y1)
                for fill in ((True, False) if a.get("fill") else (False,)):
                    if fill:
                        p.translucent(True, _num_or(a.get("fillOpacity"), 0.15))
                    op = "f" if fill else "S"
                    if kind == "rect":
                        p.polyline([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)], close=True, op=op)
                    else:
                        p.ellipse(bx + bw / 2, by + bh / 2, max(bw / 2, 0.5), max(bh / 2, 0.5), op)
                    if fill:
                        p.translucent(False)
            elif kind == "text":
                bold = bool(a.get("bold"))
                lines = str(a.get("text") or "").split("\n")
                lh = 1.25 * size
                pad = 0.3 * size if a.get("box") else 0
                bw = max([pdf.text_width(l, size, bold) for l in lines] + [0]) + 2 * pad
                bh = len(lines) * lh + 2 * pad
                if a.get("leader"):
                    tx = max(X1, min(X2, X1 + bw))
                    ty = max(Y1 - bh, min(Y2, Y1))
                    _arrow(p, tx, ty, X2, Y2, "end", weight, U)
                    p.style(color, stroke)
                if a.get("box"):
                    p.raw("[] 0 d 1 1 1 rg")
                    p.polyline([(X1, Y1), (X1 + bw, Y1), (X1 + bw, Y1 - bh), (X1, Y1 - bh)], close=True, op="B")
                for i, line in enumerate(lines):
                    p.text(X1 + pad, Y1 - pad - (i + 0.8) * lh, line, size, bold, color)
            elif kind == "callout":
                r = 0.8 * size
                if math.hypot(X2 - X1, Y2 - Y1) > r:
                    d = math.hypot(X2 - X1, Y2 - Y1)
                    sx, sy = X1 + (X2 - X1) * r / d, Y1 + (Y2 - Y1) * r / d
                    p.polyline([(sx, sy), (X2, Y2)])
                    p.raw("[] 0 d")
                    dot = 1.4 * weight * U
                    p.ellipse(X2, Y2, dot, dot, "f")
                p.raw("[] 0 d 1 1 1 rg")
                p.ellipse(X1, Y1, r, r, "B")
                label = str(a.get("number", ""))
                fs = 0.9 * size
                p.text(X1 - pdf.text_width(label, fs, True) / 2, Y1 - 0.05 * r - 0.36 * fs, label, fs, True, color)
            p.raw("Q")
        except Exception:
            p.raw("Q")


def _picture(flow, image, anns, max_h):
    """An image (with its annotations) centred at the cursor; returns nothing."""
    img_w = CONTENT_W
    img_h = img_w * image.height / float(image.width)
    if img_h > max_h:
        img_h = max_h
        img_w = img_h * image.width / float(image.height)
    if flow.room() < img_h:
        flow.new_page()
    x = MARGIN + (CONTENT_W - img_w) / 2
    flow.page.image(image, x, flow.y - img_h, img_w, img_h)
    _annotations(flow.page, anns, x, flow.y - img_h, img_w, img_h)
    flow.y -= img_h + 10


def _image(pic):
    """A picture: PNG bytes, or {"png": bytes, "crop": (left, top, w, h) or None} (a whole render,
    cut to its crop in the PDF itself)."""
    if not pic:
        return None
    try:
        if isinstance(pic, dict):
            return pdf.Image(pic["png"], pic.get("crop"), opaque=True)
        return pdf.Image(pic)
    except Exception:
        return None


def build(data):
    """PDF bytes for the manual (see the module docstring for `data`)."""
    title = data.get("title") or "Build manual"
    by = ", ".join(v for v in (data.get("author"), data.get("company")) if v)
    doc = pdf.Document(pdf.LETTER, title, by)
    images = {}
    for sec in data["sections"]:
        images[id(sec)] = _image(sec.get("image"))
        for step in sec["steps"]:
            images[id(step)] = _image(step.get("image"))
    cover_image = _image(data.get("cover_image"))

    # Contents: one line per section and per step. Work out how many pages it needs first,
    # so the page numbers of everything after it are known when it's drawn. With a cover
    # picture, the contents start on the page after the cover.
    toc = []
    for sec in data["sections"]:
        toc.append(("section", sec))
        toc.extend(("step", st) for st in sec["steps"])
    toc.append(("bom", None))
    per_page = int((TOP - 30 - BOTTOM) // 16)
    if cover_image is not None:
        toc_pages = 1 + max(1, -(-len(toc) // per_page))
    else:
        first_page_lines = int((TOP - 250 - BOTTOM) // 16)
        toc_pages = 1 + max(0, -(-(len(toc) - first_page_lines) // per_page))

    cover_pages = [doc.add_page() for _ in range(toc_pages)]
    starts = {}                     # id(section / step) or "bom" -> page number (1-based)

    flow = _Flow(doc)
    for sec in data["sections"]:
        # The section's own page: its title, its picture, its parts; its steps start on the next page.
        flow.new_page()
        starts[id(sec)] = len(doc.pages)
        p = flow.page
        p.text(MARGIN, flow.y - 10, "SECTION {}".format(sec["number"]), 9, True, ACCENT)
        flow.y -= 16
        for line in pdf.wrap(sec["title"] or "Untitled section", CONTENT_W, 22, True):
            p.text(MARGIN, flow.y - 22, line, 22, True, INK)
            flow.y -= 28
        p.line(MARGIN, flow.y - 2, MARGIN + CONTENT_W, flow.y - 2, 1.2, ACCENT)
        flow.y -= 18
        if images.get(id(sec)) is not None:
            _picture(flow, images[id(sec)], sec.get("annotations"), 360.0)
        _table(flow, sec["bom"], "Parts for this section")
        if sec["steps"]:
            flow.new_page()
        for step in sec["steps"]:
            starts[id(step)] = _step(flow, step, images.get(id(step)))
    flow.new_page()
    starts["bom"] = len(doc.pages)
    flow.page.text(MARGIN, flow.y - 22, "Full parts list", 22, True, INK)
    flow.y -= 30
    flow.page.line(MARGIN, flow.y + 2, MARGIN + CONTENT_W, flow.y + 2, 1.2, ACCENT)
    flow.y -= 14
    _table(flow, data.get("bom") or [], "All parts in this manual")

    _cover(cover_pages, data, title, toc, starts, cover_image)
    total = len(doc.pages)
    for n, page in enumerate(doc.pages, 1):
        if n > 1:                       # not on the cover
            page.line(MARGIN, MARGIN + 8, W - MARGIN, MARGIN + 8, 0.5, RULE)
            foot = title + ("  \u00b7  " + data["company"] if data.get("company") else "")
            page.text(MARGIN, MARGIN - 4, pdf.fit(foot, CONTENT_W - 90, 8), 8, False, MUTED)
            page.text_right(W - MARGIN, MARGIN - 4, "Page {} of {}".format(n, total), 8, False, MUTED)
    return doc.to_bytes()


def _logo(data):
    """The logo (a PNG data URL) as an image, or None."""
    url = data.get("logo") or ""
    if not url.startswith("data:image/png;base64,"):
        return None
    try:
        import base64
        return _image(base64.b64decode(url.split(",", 1)[1]))
    except Exception:
        return None


def _cover(pages, data, title, toc, starts, image=None):
    p = pages[0]
    logo = _logo(data)
    if logo is not None:
        # Top right, at most 160 x 64 pt, keeping its shape.
        scale = min(160.0 / logo.width, 64.0 / logo.height)
        lw, lh = logo.width * scale, logo.height * scale
        p.image(logo, W - MARGIN - lw, TOP - lh + 10, lw, lh)
    y = TOP - 60 if image is not None else TOP - 120
    if logo is not None:
        y = min(y, TOP - 64 - 34)       # (the title starts below the logo)
    for line in pdf.wrap(title, CONTENT_W, 32, True):
        p.text(MARGIN, y, line, 32, True, INK)
        y -= 38
    if data.get("design"):
        p.text(MARGIN, y - 4, data["design"], 13, False, MUTED)
        y -= 22
    if data.get("company"):
        p.text(MARGIN, y - 4, pdf.fit(data["company"], CONTENT_W, 12, True), 12, True, INK)
        y -= 20
    if data.get("author"):
        p.text(MARGIN, y - 4, pdf.fit("By " + data["author"], CONTENT_W, 11), 11, False, MUTED)
        y -= 18
    if data.get("date"):
        p.text(MARGIN, y - 4, data["date"], 11, False, MUTED)
        y -= 18
    if image is not None:
        # The whole assembly, as big as the page allows; the contents follow on the next page.
        top = y - 20
        img_w = CONTENT_W
        img_h = img_w * image.height / float(image.width)
        max_h = top - BOTTOM
        if img_h > max_h:
            img_h = max_h
            img_w = img_h * image.width / float(image.height)
        x = MARGIN + (CONTENT_W - img_w) / 2
        p.image(image, x, top - img_h, img_w, img_h)
        _annotations(p, data.get("cover_annotations"), x, top - img_h, img_w, img_h)
        index, p, y = 1, pages[1], TOP - 10
        p.text(MARGIN, y - 12, "Contents", 18, True, INK)
        y -= 40
    else:
        index = 0
        p.line(MARGIN, TOP - 230, MARGIN + CONTENT_W, TOP - 230, 1.2, ACCENT)
        p.text(MARGIN, TOP - 250 + 4, "Contents", 12, True, INK)
        y = TOP - 250 - 16

    for kind, item in toc:
        if y < BOTTOM:
            index += 1
            p = pages[index]
            y = TOP - 30
        if kind == "section":
            label, size, bold, indent = "{}  {}".format(item["number"], item["title"] or "Untitled section"), 10.5, True, 0
            page_no = starts.get(id(item))
        elif kind == "step":
            label, size, bold, indent = "{}  {}".format(item["number"], item["title"] or "Untitled step"), 9.5, False, 16
            page_no = starts.get(id(item))
        else:
            label, size, bold, indent = "Full parts list", 10.5, True, 0
            page_no = starts.get("bom")
        num = str(page_no or "")
        right = W - MARGIN
        text = pdf.fit(label, CONTENT_W - indent - 40, size, bold)
        p.text(MARGIN + indent, y, text, size, bold, INK)
        p.text_right(right, y, num, size, bold, INK)
        # dotted leader between the label and the page number
        start = MARGIN + indent + pdf.text_width(text, size, bold) + 6
        end = right - pdf.text_width(num, size, bold) - 6
        if end > start:
            dots = "." * int((end - start) / pdf.text_width(".", size))
            p.text(start, y, dots, size, False, RULE)
        y -= 16
