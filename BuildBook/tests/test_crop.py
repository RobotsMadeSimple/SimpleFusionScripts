"""Offline tests for crop rectangles and the BMP/PNG crop pipeline."""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import crop  # noqa: E402


def make_bmp(width, height, pixel, bpp=24, top_down=False):
    """BMP where pixel(x, y) -> (b, g, r[, a]) with y counted from the top."""
    channels = bpp // 8
    stride = ((bpp * width + 31) // 32) * 4
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend(pixel(x, y)[:channels])
        row.extend(b"\x00" * (stride - len(row)))
        rows.append(bytes(row))
    if not top_down:
        rows.reverse()
    body = b"".join(rows)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height if top_down else height, 1, bpp, 0,
                       len(body), 2835, 2835, 0, 0)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(body), 0, 0, 54)
    return header + info + body


class RectTests(unittest.TestCase):

    def test_centered_rect(self):
        self.assertEqual(crop.centered_rect(1.0, 800, 600), (100.0, 0.0, 600, 600))
        self.assertEqual(crop.centered_rect(None, 800, 600), (0.0, 0.0, 800, 600))
        self.assertEqual(crop.centered_rect(2.0, 800, 600), (0.0, 100.0, 800, 400.0))

    def test_fraction_round_trip_and_full(self):
        c = crop.from_pixels((80, 60, 400, 300), 800, 600)
        self.assertEqual(crop.to_pixels(c, 800, 600), (80, 60, 400, 300))
        self.assertFalse(crop.is_full(c))
        self.assertTrue(crop.is_full(crop.from_pixels((0, 0, 800, 600), 800, 600)))
        self.assertTrue(crop.is_full(None))

    def test_render_plan_scales_to_output_width(self):
        c = crop.from_pixels((200, 150, 400, 300), 800, 600)
        rw, rh, box = crop.render_plan(c, 1600, 800, 600)
        self.assertEqual((rw, rh), (3200, 2400))
        self.assertEqual(box, (800, 600, 1600, 1200))

    def test_render_plan_caps_huge_renders(self):
        c = crop.from_pixels((0, 0, 40, 30), 800, 600)
        rw, rh, box = crop.render_plan(c, 4000, 800, 600)
        self.assertLessEqual(max(rw, rh), crop.MAX_SIDE)
        self.assertLess(box[2], 4000)

    def test_ratio_value(self):
        self.assertAlmostEqual(crop.ratio_value("viewport", 800, 600), 4 / 3)
        self.assertIsNone(crop.ratio_value("free", 800, 600))
        self.assertAlmostEqual(crop.ratio_value("16:9", 800, 600), 16 / 9)
        self.assertNotIn("free", [key for key, _, _ in crop.RATIOS])


class ImageTests(unittest.TestCase):

    def pixel(self, x, y):
        return (x, y, (x + y) % 256, 255)   # b, g, r, a

    def check_crop(self, bmp):
        width, height, channels, rows = crop.read_bmp(bmp)
        self.assertEqual((width, height), (7, 5))
        out, ch = crop.crop_rows(rows, channels, (2, 1, 3, 2), swap_bgr=True, keep_alpha=False)
        self.assertEqual(ch, 3)
        # Row 0 of the crop is y=1, x=2..4, as RGB: r=(x+y), g=y, b=x.
        self.assertEqual(out[0], bytes([3, 1, 2, 4, 1, 3, 5, 1, 4]))
        png = crop.write_png(out, 3, ch)
        w, h, c, back = crop.read_png(png)
        self.assertEqual((w, h, c), (3, 2, 3))
        self.assertEqual(back, out)

    def test_bottom_up_24bit(self):
        self.check_crop(make_bmp(7, 5, self.pixel))

    def test_top_down_32bit(self):
        self.check_crop(make_bmp(7, 5, self.pixel, bpp=32, top_down=True))

    def test_alpha_detection(self):
        _, _, ch, rows = crop.read_bmp(make_bmp(4, 4, self.pixel, bpp=32))
        self.assertFalse(crop.has_alpha(rows, ch))
        _, _, ch, rows = crop.read_bmp(make_bmp(4, 4, lambda x, y: (1, 2, 3, 0), bpp=32))
        self.assertFalse(crop.has_alpha(rows, ch))       # blank alpha = not used
        _, _, ch, rows = crop.read_bmp(make_bmp(4, 4, lambda x, y: (1, 2, 3, 128 if x else 255), bpp=32))
        self.assertTrue(crop.has_alpha(rows, ch))

    def test_png_filters_decode(self):
        # Sub/Up/Average/Paeth rows must decode back to the same pixels.
        import zlib
        rows = [bytes([10, 20, 30, 40, 50, 60]), bytes([11, 22, 33, 44, 55, 66]),
                bytes([200, 100, 50, 25, 12, 6]), bytes([1, 2, 3, 4, 5, 6])]

        def encode(ftype, line, prev):
            out = bytearray(len(line))
            for i in range(len(line)):
                a = line[i - 3] if i >= 3 else 0
                b = prev[i]
                c = prev[i - 3] if i >= 3 else 0
                if ftype == 1:
                    pred = a
                elif ftype == 2:
                    pred = b
                elif ftype == 3:
                    pred = (a + b) >> 1
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                out[i] = (line[i] - pred) & 0xFF
            return bytes([ftype]) + bytes(out)

        prev = bytes(6)
        raw = b""
        for ftype, line in zip((1, 2, 3, 4), rows):
            raw += encode(ftype, line, prev)
            prev = line
        png = crop.write_png(rows, 2, 3)
        start = png.index(b"IDAT") - 4
        length = struct.unpack(">I", png[start:start + 4])[0]
        body = zlib.compress(raw)
        idat = struct.pack(">I", len(body)) + b"IDAT" + body + struct.pack(">I", zlib.crc32(b"IDAT" + body))
        png = png[:start] + idat + png[start + 12 + length:]
        self.assertEqual(crop.read_png(png)[3], rows)


if __name__ == "__main__":
    unittest.main()
