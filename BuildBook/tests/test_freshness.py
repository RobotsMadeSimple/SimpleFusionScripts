"""Out-of-date previews / pictures and new parts (lib/freshness.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib import freshness, model  # noqa: E402


def manual_with_step():
    m = model.new_manual()
    sec = model.add_section(m, "A")
    st = model.add_step(m, sec["id"], "S")
    model.add_items(st, [{"path": "Base:1", "token": "t", "name": "Base"}])
    st["camera"] = {"eye": [1, 0, 0], "target": [0, 0, 0], "up": [0, 0, 1]}
    return m, st


class FreshnessTests(unittest.TestCase):
    def test_preview_goes_stale_when_the_design_or_step_changes(self):
        m, st = manual_with_step()
        st["thumbFp"] = freshness.step_print(m, st, "geo1")
        self.assertEqual(freshness.report(m, "geo1", [])["previews"], [])
        self.assertEqual(freshness.report(m, "geo2", [])["previews"], [st["id"]])   # the design changed
        st["explodes"].append(model.new_explode())
        self.assertEqual(freshness.report(m, "geo1", [])["previews"], [st["id"]])   # the step changed
        st["title"] = "Renamed"                                                          # (titles don't matter)
        st["explodes"] = []
        self.assertEqual(freshness.report(m, "geo1", [])["previews"], [])

    def test_new_parts_are_the_unknown_unassigned_ones(self):
        m, _ = manual_with_step()
        self.assertEqual(freshness.report(m, "g", ["New:1"])["newParts"], [])         # no baseline yet
        m["knownParts"] = ["Base:1", "Old:1"]
        self.assertEqual(freshness.report(m, "g", ["Old:1", "New:1"])["newParts"], ["New:1"])


if __name__ == "__main__":
    unittest.main()
