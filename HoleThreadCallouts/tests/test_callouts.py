"""Offline tests for lib/callouts.py: python tests/test_callouts.py"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import callouts  # noqa: E402


def hole(hid, x, y, label="M3", kind="thread"):
    return {"id": hid, "kind": kind, "label": label, "center": [x, y], "r": 0.02,
            "outline": [[x - 0.02, y], [x, y - 0.02], [x + 0.02, y], [x, y + 0.02]]}


class SizeTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(callouts.size_label("M3x0.5"), "M3")
        self.assertEqual(callouts.size_label("m2.5x0.45"), "M2.5")
        self.assertEqual(callouts.size_label("1/4-20 UNC"), "1/4-20")
        self.assertEqual(callouts.size_label(""), "?")

    def test_tap_drill_guess(self):
        self.assertEqual(callouts.guess_size(2.5), "M3")
        self.assertEqual(callouts.guess_size(3.32), "M4")
        self.assertEqual(callouts.guess_size(4.2), "M5")
        self.assertIsNone(callouts.guess_size(3.0))

    def test_colours(self):
        self.assertEqual(callouts.color_for("M3"), "#F0E442")
        self.assertEqual(callouts.color_for("M4"), "#56B4E9")
        self.assertEqual(callouts.color_for("M5"), "#E69F00")
        self.assertEqual(callouts.color_for("M6"), "#CC79A7")
        self.assertEqual(callouts.color_for("M3", {"M3": "#000000"}), "#000000")
        self.assertEqual(callouts.color_for("M7"), callouts.color_for("M7"))        # stable spare colour

    def test_size_order(self):
        self.assertEqual(sorted(["M10", "M3", "M2.5", "1/4-20"], key=callouts.size_key), ["M2.5", "M3", "M10", "1/4-20"])


class AssignTests(unittest.TestCase):
    def test_called_out_once_in_first_view(self):
        visible = {"v1": {"a", "b"}, "v2": {"b", "c"}}
        self.assertEqual(callouts.assign(["v1", "v2"], visible), {"a": "v1", "b": "v1", "c": "v2"})
        self.assertEqual(callouts.assign(["v2", "v1"], visible), {"a": "v1", "b": "v2", "c": "v2"})

    def test_pinned(self):
        visible = {"v1": {"a", "b"}, "v2": {"b"}}
        self.assertEqual(callouts.assign(["v1", "v2"], visible, {"b": "v2"})["b"], "v2")
        self.assertEqual(callouts.assign(["v1", "v2"], visible, {"a": "v2"})["a"], "v1")   # not visible there


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.view = {"id": "v1", "kind": "thread"}
        self.holes = [hole("a", 0.3, 0.3), hole("b", 0.6, 0.4), hole("c", 0.5, 0.7, "M5"),
                      hole("d", 0.7, 0.7, "Ø5", "dowel")]
        self.assigned = {"a": "v1", "b": "v1", "c": "v1", "d": "v1"}

    def test_marks_and_callouts(self):
        anns = callouts.build(self.view, self.holes, self.assigned)
        marks = [a for a in anns if a["auto"].startswith("hole:")]
        self.assertEqual(len(marks), 3)
        self.assertEqual({m["fillColor"] for m in marks}, {"#F0E442", "#E69F00"})
        self.assertTrue(all(m["locked"] for m in marks))
        notes = {a["auto"]: a for a in anns if a["auto"].startswith("callout:")}
        self.assertEqual(notes["callout:thread:M3"]["text"], "2x M3")
        self.assertEqual(notes["callout:thread:M5"]["text"], "1x M5")
        self.assertEqual(notes["callout:dowel:Ø5"]["text"], "1x Ø5 dowel")
        self.assertEqual(notes["callout:thread:M3"]["target"], "a")            # the top M3 hole
        self.assertEqual([a["type"] for a in anns if a["auto"].startswith("dowel:")], ["dowel"])
        for n in notes.values():
            self.assertTrue(0 <= n["x1"] <= 1 and 0 <= n["y1"] <= 1)

    def test_only_holes_assigned_here(self):
        anns = callouts.build(self.view, self.holes, {"a": "v1", "b": "v2"})
        self.assertEqual([a["text"] for a in anns if a.get("type") == "text"], ["1x M3"])

    def test_moved_callout_and_own_annotations_survive(self):
        first = callouts.build(self.view, self.holes, self.assigned)
        edited = json.loads(json.dumps(first))                     # what the editor sends back
        note = next(a for a in edited if a["auto"] == "callout:thread:M3")
        note.update(x1=0.05, y1=0.05, text="2x M3 thru", x2=0.6, y2=0.4)   # moved, new text, re-aimed at b
        own = {"type": "arrow", "x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2}
        saved = callouts.merge_saved(first, edited + [own])
        self.assertFalse(any(a.get("auto", "").startswith("hole:") for a in saved))
        again = callouts.build(self.view, self.holes, self.assigned, saved)
        note = next(a for a in again if a.get("auto") == "callout:thread:M3")
        self.assertEqual((note["x1"], note["y1"]), (0.05, 0.05))
        self.assertEqual(note["text"], "2x M3 thru")
        self.assertEqual(note["target"], "b")
        self.assertIn(own, again)
        # a hole disappears: the count updates, a custom text stays
        fewer = callouts.build(self.view, self.holes[1:], self.assigned, saved)
        note = next(a for a in fewer if a.get("auto") == "callout:thread:M3")
        self.assertEqual(note["count"], 1)

    def test_shaded_view_has_no_marks(self):
        own = {"type": "text", "x1": 0.1, "y1": 0.1, "text": "hi"}
        self.assertEqual(callouts.build({"id": "s", "kind": "shaded"}, self.holes, self.assigned, [own]), [own])


class RemapTests(unittest.TestCase):
    def test_same_spot_after_padding(self):
        old, new = [0.2, 0.2, 0.5, 0.5], [0.1, 0.15, 0.7, 0.6]     # more room left / top / right
        a = {"type": "text", "x1": 0.5, "y1": 0.5, "x2": 1.0, "y2": 0.0, "points": [[0, 0], [1, 1]], "r": 0.1}
        b = callouts.remap([a], old, new)[0]

        def spot(crop, x, y):
            return (round(crop[0] + x * crop[2], 9), round(crop[1] + y * crop[3], 9))
        self.assertEqual(spot(new, b["x1"], b["y1"]), spot(old, 0.5, 0.5))
        self.assertEqual(spot(new, b["x2"], b["y2"]), spot(old, 1.0, 0.0))
        self.assertEqual(spot(new, *b["points"][1]), spot(old, 1, 1))
        self.assertAlmostEqual(b["r"] * new[2], 0.1 * old[2])
        self.assertEqual(callouts.remap([a], None, new), [a])            # first render: nothing to move


if __name__ == "__main__":
    unittest.main()
