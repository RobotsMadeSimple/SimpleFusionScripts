"""Offline tests for Browser+ folders (layout) and the BOM."""

import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib import bom, layout  # noqa: E402


def part(path, hw=None, leaf=True, comp=None, bodies=True):
    return {"path": path, "hw": hw, "leaf": leaf, "bodies": bodies,
            "componentId": comp or path.split("+")[-1].split(":")[0], "component": path.split(":")[0]}


PARTS = [
    part("Frame:1", leaf=False),
    part("Frame:1+Rail:1", comp="Rail"),
    part("Frame:1+Rail:2", comp="Rail"),
    part("Frame:1+Screw:1", hw="M5x12 SHCS", comp="Screw"),
    part("Motor:1", comp="Motor"),
    part("Screw:2", hw="M5x12 SHCS", comp="Screw"),
    part("Nut:1", hw="M5 nut", comp="Nut"),
    part("Washer:1", hw="M5 washer", comp="Washer"),
]


class LayoutTests(unittest.TestCase):
    def test_load_tolerates_junk(self):
        self.assertEqual(layout.load(None), layout.empty())
        self.assertEqual(layout.load("not json"), layout.empty())
        got = layout.load('{"folders": "bad", "hardwareAuto": false}')
        self.assertEqual(got["folders"], [])
        self.assertFalse(got["hardwareAuto"])
        self.assertEqual(layout.load(layout.dump(got)), got)

    def test_default_placement(self):
        lay = layout.empty()
        lay["split"] = ["Frame"]            # (assemblies count as one part unless split)
        spots = layout.place(PARTS, lay)
        self.assertEqual(spots["Frame:1"], {"folder": ""})
        self.assertEqual(spots["Frame:1+Rail:1"], {"under": "Frame:1"})
        self.assertEqual(spots["Frame:1+Screw:1"]["folder"], "hw:screws")
        self.assertEqual(spots["Nut:1"]["folder"], "hw:nuts")
        self.assertEqual(spots["Washer:1"]["folder"], "hw:washers")
        lay["hardwareAuto"] = False
        self.assertEqual(layout.place(PARTS, lay)["Frame:1+Screw:1"], {"under": "Frame:1"})
        lay["hardwareAuto"], lay["split"] = True, []
        self.assertEqual(layout.place(PARTS, lay)["Frame:1+Screw:1"], {"under": "Frame:1"})   # inside a one-part

    def test_hardware_folders_listed_only_when_used(self):
        lay = layout.empty()
        lay["split"] = ["Frame"]
        view = layout.folders_view(lay, layout.place(PARTS, lay))
        ids = [f["id"] for f in view]
        self.assertEqual(ids, ["hw", "hw:screws", "hw:nuts", "hw:washers"])
        self.assertEqual(layout.folders_view(lay, layout.place(PARTS[:3], lay)), [])

    def test_assign_and_effective_folder(self):
        lay = layout.empty()
        frame = layout.add_folder(lay, "Frame")
        self.assertTrue(layout.assign(lay, ["Frame:1", "Screw:2"], frame, {"Frame:1": "tok"}))
        self.assertFalse(layout.assign(lay, ["Motor:1"], "hw:screws"))     # auto folders are automatic
        self.assertFalse(layout.assign(lay, ["Motor:1"], "nope"))
        spots = layout.place(PARTS, lay)
        self.assertEqual(spots["Screw:2"], {"folder": frame, "explicit": True})   # beats auto hardware
        self.assertEqual(layout.effective_folder("Frame:1+Rail:1", spots), frame)
        self.assertEqual(layout.effective_folder("Motor:1", spots), "")
        layout.assign(lay, ["Screw:2"], None)
        self.assertEqual(layout.place(PARTS, lay)["Screw:2"]["folder"], "hw:screws")

    def test_delete_folder_moves_contents_up(self):
        lay = layout.empty()
        a = layout.add_folder(lay, "A")
        b = layout.add_folder(lay, "B", a)
        c = layout.add_folder(lay, "C", b)
        layout.assign(lay, ["Motor:1"], b)
        layout.delete_folder(lay, b)
        self.assertEqual(layout.folder(lay, c)["parent"], a)
        self.assertEqual(lay["items"]["Motor:1"]["folder"], a)
        layout.delete_folder(lay, a)
        self.assertEqual(lay["items"]["Motor:1"]["folder"], "")
        self.assertIsNone(layout.folder(lay, c)["parent"])

    def test_move_folder_rejects_cycles_and_orders(self):
        lay = layout.empty()
        a = layout.add_folder(lay, "A")
        b = layout.add_folder(lay, "B", a)
        c = layout.add_folder(lay, "C")
        self.assertFalse(layout.move_folder(lay, a, b))
        self.assertFalse(layout.move_folder(lay, a, a))
        self.assertTrue(layout.move_folder(lay, c, None, before=a))
        self.assertEqual([f["id"] for f in lay["folders"]], [c, a, b])
        self.assertEqual(layout.folder_trail(b, layout.folders_view(lay, {})), "A › B")

    def test_heal_rekeys_renamed_parts(self):
        lay = layout.empty()
        f = layout.add_folder(lay, "F")
        layout.assign(lay, ["Old:1", "Gone:1"], f, {"Old:1": "t-old", "Gone:1": "t-gone"})
        changed = layout.heal(lay, {"New:1"}, {"t-old": "New:1"}.get)
        self.assertTrue(changed)
        self.assertIn("New:1", lay["items"])
        self.assertIn("Gone:1", lay["items"])      # kept for later (undo)

    def test_columns(self):
        lay = layout.empty()
        cid = layout.add_column(lay, "Vendor")
        layout.set_value(lay, "Screw", cid, "McMaster")
        layout.rename_column(lay, cid, "Supplier")
        self.assertEqual(lay["columns"], [{"id": cid, "name": "Supplier"}])
        layout.set_value(lay, "Screw", cid, "")
        self.assertEqual(lay["values"], {})
        layout.set_value(lay, "Screw", cid, "x")
        layout.delete_column(lay, cid)
        self.assertEqual(lay["columns"], [])
        self.assertEqual(lay["values"], {"Screw": {}})


class FeatureTreeTests(unittest.TestCase):
    def test_feature_folders_and_healing(self):
        lay = layout.empty()
        tree = layout.feature_tree(lay, "comp")
        f = layout.add_folder(tree, "Base")
        self.assertTrue(layout.assign_features(tree, ["t1", "t2"], f, {"t1": "Extrude1", "t2": "Sketch1"}))
        self.assertFalse(layout.assign_features(tree, ["t3"], "nope"))
        items = [{"id": "t1", "name": "Extrude1"}, {"id": "new2", "name": "Sketch1"}, {"id": "t3", "name": "Fillet1"}]
        spots = layout.place_features(items, tree)
        self.assertEqual(spots, {"t1": f, "new2": f, "t3": ""})     # t2 -> new2 by name
        layout.delete_folder(tree, f)
        self.assertEqual(layout.place_features(items, tree)["t1"], "")
        self.assertIs(layout.feature_tree(lay, "comp"), tree)
        self.assertEqual(layout.load(layout.dump(lay))["featureTrees"]["comp"]["folders"], [])


class BomTests(unittest.TestCase):
    def setUp(self):
        self.lay = layout.empty()
        self.lay["split"] = ["Frame"]
        self.info = {"Screw": {"name": "Screw", "hw": "M5x12 SHCS", "partNumber": "91290A228", "mass": None},
                     "Rail": {"name": "Rail", "material": "Aluminum 6061", "mass": 0.25}}

    def build(self, group=True):
        spots = layout.place(PARTS, self.lay)
        return bom.build(PARTS, spots, layout.folders_view(self.lay, spots), self.info, self.lay, group)

    def test_flat_rollup(self):
        b = self.build(group=False)
        rows = {r["componentId"]: r for r in b["sections"][0]["rows"]}
        self.assertEqual(rows["Screw"]["qty"], 2)
        self.assertEqual(rows["Rail"]["qty"], 2)
        self.assertNotIn("Frame", rows)             # subassembly: a container, not a line
        self.assertEqual(b["totalQty"], 7)
        self.assertEqual(b["unique"], 5)

    def test_grouped_sections_follow_folders(self):
        frame = layout.add_folder(self.lay, "Frame")
        layout.assign(self.lay, ["Frame:1"], frame)
        b = self.build()
        names = [s["name"] for s in b["sections"]]
        self.assertEqual(names, ["Frame", "Hardware › Screws", "Hardware › Nuts", "Hardware › Washers",
                                 bom.NO_FOLDER])
        self.assertEqual([r["componentId"] for r in b["sections"][0]["rows"]], ["Rail"])

    def test_assemblies_count_as_one_unless_split(self):
        parts = PARTS + [part("Motor:1+Housing:1", comp="Housing"), part("Motor:1+Screw:9", hw="M3x6 SHCS", comp="Screw")]
        parts[4] = part("Motor:1", comp="Motor", leaf=False, bodies=False)
        spots = layout.place(parts, self.lay)
        self.assertEqual(spots["Motor:1+Screw:9"], {"under": "Motor:1"})     # not pulled into Hardware
        b = bom.build(parts, spots, layout.folders_view(self.lay, spots), self.info, self.lay, False)
        rows = {r["componentId"]: r for r in b["sections"][0]["rows"]}
        self.assertEqual(rows["Motor"]["qty"], 1)
        self.assertNotIn("Housing", rows)
        self.assertEqual(rows["Screw"]["qty"], 2)                              # the motor's screw isn't counted
        layout.set_split(self.lay, "Motor", True)
        b = bom.build(parts, layout.place(parts, self.lay), [], self.info, self.lay, False)
        rows = {r["componentId"]: r for r in b["sections"][0]["rows"]}
        self.assertNotIn("Motor", rows)
        self.assertEqual(rows["Screw"]["qty"], 3)
        layout.set_split(self.lay, "Motor", False)
        self.assertEqual(self.lay["split"], ["Frame"])
        lay = layout.empty()                                                   # default: Frame is one part
        b = bom.build(PARTS, layout.place(PARTS, lay), [], self.info, lay, False)
        self.assertIn("Frame", {r["componentId"] for r in b["sections"][0]["rows"]})

    def test_csv(self):
        cid = layout.add_column(self.lay, "Vendor")
        layout.set_value(self.lay, "Screw", cid, "McMaster")
        b = self.build()
        rows = list(csv.reader(io.StringIO(bom.to_csv(b))))
        self.assertEqual(rows[0], ["Section", "Qty", "Name", "Part number", "Description", "Material",
                                   "Mass (kg)", "Vendor"])
        screw = next(r for r in rows if r[2].startswith("M5x12"))
        self.assertEqual(screw[1], "2")
        self.assertEqual(screw[2], "M5x12 SHCS (Screw)")
        self.assertEqual(screw[-1], "McMaster")
        self.info["Rail"]["mass"] = None
        header = next(csv.reader(io.StringIO(bom.to_csv(self.build(), grouped=False))))
        self.assertNotIn("Mass (kg)", header)
        self.assertNotIn("Section", header)


if __name__ == "__main__":
    unittest.main()
