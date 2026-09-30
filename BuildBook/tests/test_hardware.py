"""Offline tests for hardware short names and natural sorting."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import hardware  # noqa: E402


class ShortNameTests(unittest.TestCase):

    def check(self, want, *texts):
        self.assertEqual(hardware.short_name(*texts), want)

    def test_metric_screws(self):
        self.check("M3x12 SHCS", "ISO 4762 Hexagon Socket Head Cap Screw M3 x 12")
        self.check("M3x8 BHCS", "M3-0.5 x 8mm Button Head Screw")
        self.check("M3x12 SHCS", "M3 x 0.5 x 12 SHCS")
        self.check("M2.5x6 FHCS", "M2.5x6 Flat head")
        self.check("M3x12", "M3x12")

    def test_metric_nuts_washers_inserts(self):
        self.check("M5 nut", "Hex Nut M5")
        self.check("M3 nut", "M3-0.5 Hex Nut")
        self.check("M3 washer", "M3 Washer")
        self.check("M3 insert", "M3 Heat Set Insert")
        self.check("M5 T-nut", "M5 Drop-in T-Nut")

    def test_imperial(self):
        self.check("1/4-20x1-1/2 FHCS", '1/4"-20 x 1-1/2" Flat Head Screw')
        self.check("#4-40x1/2 pan head", "#4-40 x 1/2 Pan Head")
        self.check("10-32x3/8 set screw", "10-32 x 3/8 set screw")

    def test_description_fallback_mcmaster(self):
        self.check("M3x12 SHCS", "91290A113", "Alloy Steel Socket Head Screw, M3 x 0.5 mm Thread, 12 mm Long")

    def test_ordinary_parts_untouched(self):
        for name in ("V-SLOT 2020", "12mm 100mm Rod", "Belt Roller", "40 Teeth GT2 Pulley 10mm",
                     "NEMA17", "KP001_12", "Motor Side", "2020-3 extrusion", "GT2 20T5B_dz2020", ""):
            self.assertIsNone(hardware.short_name(name), name)


class NeedsDetailsTests(unittest.TestCase):

    def test_only_hardware_like_names_need_details(self):
        self.assertTrue(hardware.needs_details("91290A113"))
        self.assertTrue(hardware.needs_details("Socket Head Screw"))
        self.assertFalse(hardware.needs_details("M3x12 SHCS"))        # name already has the size
        for name in ("V-SLOT 2020", "Belt Roller", "Motor Side", "NEMA17", "KP001_12", ""):
            self.assertFalse(hardware.needs_details(name), name)


class NaturalSortTests(unittest.TestCase):

    def test_numbers_by_value(self):
        names = ["M3x12 SHCS", "M3x8 SHCS", "Belt Roller", "M10 nut", "M3 nut", "Part 10", "Part 2"]
        self.assertEqual(sorted(names, key=hardware.natural_key),
                         ["Belt Roller", "M3 nut", "M3x8 SHCS", "M3x12 SHCS", "M10 nut", "Part 2", "Part 10"])


if __name__ == "__main__":
    unittest.main()
