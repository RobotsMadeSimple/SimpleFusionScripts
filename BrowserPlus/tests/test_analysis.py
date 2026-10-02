"""Offline tests for joint analysis."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analysis  # noqa: E402


def rec(id_, kind, parts, name=None, health="ok", suppressed=False):
    return {"id": id_, "kind": kind, "name": name or id_, "type": "", "parts": parts,
            "health": health, "message": "", "suppressed": suppressed, "locked": False,
            "context": "", "details": []}


def part(path, grounded=False, bodies=True):
    return {"path": path, "name": path.split("+")[-1], "component": "", "grounded": grounded, "bodies": bodies}


RECORDS = [
    rec("j1", "joint", ["Frame:1", "Motor:1"], "Motor mount"),
    rec("j2", "joint", ["Motor:1", "Pulley:1"], "Pulley on shaft"),
    rec("c1", "constraint", ["Frame:1", "Motor:1"], "Motor flush", health="warning"),
    rec("g1", "rigidGroup", ["Frame:1", "Rail:1", "Rail:2"], "Frame group"),
    rec("j3", "joint", ["Gearbox:1"], "Gearbox to origin", health="error"),
    rec("j4", "joint", ["Gearbox:1+Shaft:1", "Pulley:2"], "Shaft pulley"),
    rec("s1", "joint", ["Belt:1", "Pulley:1"], "Old belt joint", suppressed=True),
]

PARTS = [part("Frame:1", grounded=True), part("Motor:1"), part("Pulley:1"), part("Rail:1"), part("Rail:2"),
         part("Gearbox:1", bodies=False), part("Gearbox:1+Shaft:1"), part("Pulley:2"),
         part("Belt:1"), part("Loose bolt:1")]


class ForPartTests(unittest.TestCase):

    def test_groups_by_other_part(self):
        out = analysis.for_part(RECORDS, "Motor:1")
        groups = dict(out["direct"])
        self.assertEqual([r["id"] for r in groups["Frame:1"]], ["j1", "c1"])
        self.assertEqual([r["id"] for r in groups["Pulley:1"]], ["j2"])
        self.assertEqual(out["inherited"], [])

    def test_rigid_group_lists_each_other_part(self):
        groups = dict(analysis.for_part(RECORDS, "Rail:1")["direct"])
        self.assertEqual(sorted(groups), ["Frame:1", "Rail:2"])

    def test_single_part_record_groups_under_empty_and_sorts_last(self):
        out = analysis.for_part(RECORDS, "Gearbox:1")
        self.assertEqual(out["direct"][-1][0], "")

    def test_assembly_counts_its_parts(self):
        records = RECORDS + [rec("i1", "constraint", ["Gearbox:1+Shaft:1", "Gearbox:1+Gear:1"], "Inside")]
        out = analysis.for_part(records, "Gearbox:1")
        self.assertEqual([r["id"] for r in dict(out["direct"])["Pulley:2"]], ["j4"])
        self.assertEqual([r["id"] for r in out["internal"]], ["i1"])

    def test_inherited_from_parent_assembly(self):
        out = analysis.for_part(RECORDS, "Gearbox:1+Shaft:1")
        self.assertEqual([r["id"] for r in dict(out["direct"])["Pulley:2"]], ["j4"])
        self.assertEqual([(a, [r["id"] for r in rs]) for a, rs in out["inherited"]], [("Gearbox:1", ["j3"])])


class ChecksTests(unittest.TestCase):

    def test_floating_ignores_grounded_held_and_suppressed_only(self):
        # Belt:1 is only in a suppressed joint, Loose bolt:1 in nothing.
        self.assertEqual(analysis.floating(RECORDS, PARTS), ["Belt:1", "Loose bolt:1"])

    def test_floating_respects_parent_grounding(self):
        parts = [part("Sub:1", grounded=True, bodies=False), part("Sub:1+Plate:1")]
        self.assertEqual(analysis.floating([], parts), [])

    def test_duplicates(self):
        self.assertEqual(analysis.duplicates(RECORDS), [{"pair": ["Frame:1", "Motor:1"], "ids": ["j1", "c1"]}])

    def test_problems_errors_first(self):
        self.assertEqual([r["id"] for r in analysis.problems(RECORDS)], ["j3", "c1"])

    def test_summary(self):
        self.assertEqual(analysis.summary(RECORDS), {"total": 7, "error": 1, "warning": 1, "suppressed": 1})


class GraphTests(unittest.TestCase):

    def test_nodes_and_edges(self):
        g = analysis.graph(RECORDS, PARTS)
        ids = [n["id"] for n in g["nodes"]]
        self.assertIn("", ids)                        # ground node for the one-part joint
        self.assertIn("Loose bolt:1", ids)            # floating parts show up too
        rigid = [e for e in g["edges"] if e["record"] == "g1"]
        self.assertEqual([(e["source"], e["target"]) for e in rigid], [("Frame:1", "Rail:1"), ("Rail:1", "Rail:2")])
        frame = [n for n in g["nodes"] if n["id"] == "Frame:1"][0]
        self.assertTrue(frame["grounded"])
        self.assertEqual(frame["degree"], 3)
        loose = [n for n in g["nodes"] if n["id"] == "Loose bolt:1"][0]
        self.assertTrue(loose["floating"])


if __name__ == "__main__":
    unittest.main()
