"""Distances that follow Fusion parameters (lib/params.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib import model, params  # noqa: E402


class _Units:
    defaultLengthUnits = "mm"
    values = {"bb_gap": 2.5, "-(bb_gap)": -2.5, "bb_gap * 2": 5.0}

    def isValidExpression(self, text, units):
        return text in self.values

    def evaluateExpression(self, text, units):
        return self.values[text]


class _Design:
    unitsManager = _Units()


class ParamTests(unittest.TestCase):
    def test_plain_numbers_are_not_expressions(self):
        for text in ("25", "25 mm", "-3.5in", ".5 cm"):
            self.assertFalse(params.is_expression(text), text)
        for text in ("bb_gap", "bb_gap * 2", "rack_len + 10 mm"):
            self.assertTrue(params.is_expression(text), text)

    def test_flip_wraps_and_unwraps(self):
        self.assertEqual(params.flip("bb_gap"), "-(bb_gap)")
        self.assertEqual(params.flip("-(bb_gap)"), "bb_gap")

    def test_resolve_follows_the_parameter_and_its_sign(self):
        m = model.new_manual()
        sec = model.add_section(m, "A")
        st = model.add_step(m, sec["id"], "S")
        ex = model.new_explode(model.new_direction("+Z"))
        ex.update(distance=1.0, distanceExpr="-(bb_gap)", baseExpr="bb_gap * 2")
        st["explodes"].append(ex)
        bad = model.new_explode(model.new_direction("+X"))
        bad.update(distance=3.0, distanceExpr="gone_param")
        st["explodes"].append(bad)
        self.assertEqual(params.resolve(_Design(), m), 3)
        self.assertEqual(ex["distance"], 2.5)
        self.assertTrue(model.is_negative(ex["direction"]))       # the expression's sign is the direction
        self.assertEqual(ex["base"], 5.0)
        self.assertEqual(bad["distance"], 3.0)                     # unreadable: keeps its last number
        self.assertTrue(bad["exprError"])


if __name__ == "__main__":
    unittest.main()
