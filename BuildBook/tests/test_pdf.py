"""Offline tests for the PDF writer and the manual layout.

python tests/test_pdf.py [out.pdf]   (also writes a sample manual when given a path)
"""

import os
import re
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import crop  # noqa: E402
import manual_pdf  # noqa: E402
import pdf  # noqa: E402


def png(width, height, channels=3, shade=(90, 140, 200)):
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            inside = (width // 4 < x < 3 * width // 4) and (height // 4 < y < 3 * height // 4)
            color = shade if inside else (250, 250, 250)
            row += bytes(color) + (bytes([255 if inside else 0]) if channels == 4 else b"")
        rows.append(bytes(row))
    return crop.write_png(rows, width, channels)


def sample(steps_per_section=4, long_bom=False):
    bom = [{"qty": 8, "name": "M5x12 SHCS"}, {"qty": 4, "name": "M5 T-nut"}, {"qty": 1, "name": "Motor mount plate"}]
    sections = []
    for s in range(1, 3):
        steps = []
        for t in range(1, steps_per_section + 1):
            steps.append({
                "number": "{}.{}".format(s, t),
                "title": "Attach the {} bracket".format(["left", "right", "top", "base"][t % 4]),
                "notes": "Slide the T-nuts into the rail first. Hand-tighten the screws, square the bracket, "
                         "then torque to 4 Nm.\nCheck the belt clears the bracket." if t % 2 else "",
                "image": png(160, 120, 4 if t == 2 else 3),
                "bom": bom * (12 if long_bom and t == 3 else 1),
            })
        sections.append({"number": s, "title": "Frame" if s == 1 else "Drive and tensioner",
                         "bom": bom, "steps": steps})
    anns = [
        {"type": "arrow", "x1": 0.1, "y1": 0.1, "x2": 0.3, "y2": 0.3, "color": "#d9463e", "weight": 4},
        {"type": "line", "x1": 0.8, "y1": 0.1, "x2": 0.9, "y2": 0.5, "color": "#0696d7", "weight": 3,
         "dashed": True, "heads": "both"},
        {"type": "rect", "x1": 0.55, "y1": 0.6, "x2": 0.9, "y2": 0.9, "color": "#3c9a2e", "weight": 3, "fill": True},
        {"type": "ellipse", "x1": 0.3, "y1": 0.3, "x2": 0.7, "y2": 0.7, "color": "#d99a1e", "weight": 5},
        {"type": "text", "x1": 0.05, "y1": 0.75, "x2": 0.3, "y2": 0.62, "text": "Hand-tight only\nthen 4 Nm",
         "color": "#222222", "size": 32, "box": True, "leader": True, "weight": 2},
        {"type": "callout", "x1": 0.85, "y1": 0.2, "x2": 0.7, "y2": 0.35, "number": 1, "color": "#d9463e",
         "size": 34, "weight": 3},
    ]
    sections[0]["image"] = png(200, 120, 3, (120, 120, 120))
    sections[0]["annotations"] = anns[:2]
    sections[0]["steps"][0]["annotations"] = anns
    return {"title": "Simple Conveyor Assembly", "design": "Simple Conveyor Assembly v12",
            "date": "2026-09-30", "sections": sections, "bom": bom * 2,
            "cover_image": png(240, 160, 3, (60, 60, 60)), "cover_annotations": anns[5:]}


class PdfTests(unittest.TestCase):
    def test_wrap_and_fit(self):
        lines = pdf.wrap("one two three four five six seven", 60, 10)
        self.assertTrue(all(pdf.text_width(l, 10) <= 60 for l in lines))
        self.assertEqual(" ".join(lines), "one two three four five six seven")
        self.assertTrue(pdf.fit("a very long part name indeed", 50, 10).endswith("..."))
        self.assertEqual(pdf.wrap("a\nb", 100, 10), ["a", "b"])

    def test_png_embedding(self):
        rgb = pdf.Image(png(20, 10, 3))
        self.assertIn("/Predictor 15", rgb.params)           # embedded as is
        rgba = pdf.Image(png(20, 10, 4))
        self.assertEqual(rgba.params, "")                    # flattened onto white
        raw = zlib.decompress(rgba.data)
        self.assertEqual(len(raw), 20 * 10 * 3)
        self.assertEqual(raw[:3], b"\xff\xff\xff")           # transparent -> white

    def test_manual_structure(self):
        data = manual_pdf.build(sample(long_bom=True))
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        count = int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", data).group(1))
        self.assertGreaterEqual(count, 5)
        # xref offsets point at their objects
        xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
        table = data[xref:].split(b"\n")
        first = int(table[2 + 1][:10])
        self.assertTrue(data[first:].startswith(b"1 0 obj"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].endswith(".pdf"):
        with open(sys.argv.pop(1), "wb") as handle:
            handle.write(manual_pdf.build(sample(long_bom=True)))
    unittest.main()
