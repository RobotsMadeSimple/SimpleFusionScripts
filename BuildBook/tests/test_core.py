"""Offline tests for the pure-Python core (model + explode math)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import explode  # noqa: E402
import model  # noqa: E402


def ref(path):
    return {"path": path, "token": "t-" + path, "name": path.split("+")[-1]}


def build():
    """Frame section (step A: base; step B: roller subassembly) + Drive section (step C: motor)."""
    m = model.new_manual("Conveyor")
    frame = model.add_section(m, "Frame")
    drive = model.add_section(m, "Drive")
    a = model.add_step(m, frame["id"], "Base")
    b = model.add_step(m, frame["id"], "Rollers")
    c = model.add_step(m, drive["id"], "Motor")
    model.add_items(a, [ref("Base:1")])
    model.add_items(b, [ref("Roller:1"), ref("Roller:2")])
    model.add_items(c, [ref("Motor:1")])
    return m, a, b, c


class ModelTests(unittest.TestCase):

    def test_round_trip_keeps_structure(self):
        m, a, b, c = build()
        again = model.from_json(model.to_json(m))
        self.assertEqual([s["title"] for _, s in model.ordered_steps(again)], ["Base", "Rollers", "Motor"])
        self.assertEqual(model.item_paths(model.find_step(again, b["id"])[1]), ["Roller:1", "Roller:2"])

    def test_from_json_fills_new_settings(self):
        again = model.from_json('{"title":"Old","sections":[],"settings":{"trail":{"color":"#ff0000"}}}')
        self.assertEqual(again["settings"]["trail"]["color"], "#ff0000")
        self.assertEqual(again["settings"]["trail"]["style"], "dashed")
        self.assertIn("image", again["settings"])

    def test_old_default_unassigned_becomes_hidden(self):
        old = model.from_json('{"schema":2,"sections":[],"settings":{"unassigned":"asEarlier"}}')
        self.assertEqual(old["settings"]["unassigned"], model.HIDDEN)
        chosen = model.from_json('{"schema":3,"sections":[],"settings":{"unassigned":"asEarlier"}}')
        self.assertEqual(chosen["settings"]["unassigned"], model.AS_EARLIER)

    def test_empty_attribute_gives_new_manual(self):
        self.assertEqual(model.from_json("")["sections"], [])

    def test_add_items_skips_duplicates(self):
        _, a, _, _ = build()
        self.assertEqual(model.add_items(a, [ref("Base:1"), ref("Bolt:1")]), 1)
        self.assertEqual(model.item_paths(a), ["Base:1", "Bolt:1"])

    def test_leaf_states(self):
        m, a, b, c = build()
        self.assertEqual(model.leaf_state(m, b["id"], "Roller:1"), model.CURRENT)
        self.assertEqual(model.leaf_state(m, b["id"], "Base:1"), model.EARLIER)
        self.assertEqual(model.leaf_state(m, b["id"], "Motor:1"), model.LATER)
        self.assertEqual(model.leaf_state(m, b["id"], "Guard:1"), model.UNASSIGNED)

    def test_prep_step_parts_stay_unassigned_until_installed(self):
        m, a, b, c = build()          # Base, Rollers, Motor
        b["prep"] = True              # Rollers only get prepared
        self.assertEqual(model.leaf_state(m, b["id"], "Roller:1"), model.CURRENT)
        self.assertEqual(model.leaf_state(m, c["id"], "Roller:1"), model.UNASSIGNED)
        self.assertEqual(model.leaf_state(m, a["id"], "Roller:1"), model.LATER)
        model.add_items(c, [ref("Roller:1")])     # ...installed in Motor
        self.assertEqual(model.leaf_state(m, c["id"], "Roller:1"), model.CURRENT)
        d = model.add_step(m, m["sections"][1]["id"], "After")
        self.assertEqual(model.leaf_state(m, d["id"], "Roller:1"), model.EARLIER)
        self.assertEqual(model.leaf_state(m, d["id"], "Roller:2"), model.UNASSIGNED)
        # A prep step before the installing step: unassigned in between, not "later".
        self.assertEqual(model.leaf_state(m, b["id"], "Roller:2"), model.CURRENT)

    def test_subassembly_item_covers_children(self):
        m, a, b, c = build()
        model.add_items(c, [ref("Gearbox:1")])
        self.assertEqual(model.leaf_state(m, c["id"], "Gearbox:1+Shaft:1"), model.CURRENT)
        self.assertEqual(model.leaf_state(m, a["id"], "Gearbox:1+Shaft:1"), model.LATER)
        # Prefix of a name is not an ancestor.
        self.assertEqual(model.leaf_state(m, c["id"], "Gearbox:10"), model.UNASSIGNED)

    def test_subassembly_built_then_installed(self):
        m, a, b, c = build()
        model.add_items(a, [ref("Gearbox:1+Shaft:1")])
        model.add_items(c, [ref("Gearbox:1")])
        self.assertEqual(model.leaf_state(m, b["id"], "Gearbox:1+Shaft:1"), model.EARLIER)
        self.assertEqual(model.leaf_state(m, c["id"], "Gearbox:1+Shaft:1"), model.CURRENT)

    def test_ancestor_items_outermost_first(self):
        _, a, _, _ = build()
        model.add_items(a, [ref("G:1+S:1"), ref("G:1")])
        self.assertEqual([i["ref"]["path"] for i in model.ancestor_items(a, "G:1+S:1")], ["G:1", "G:1+S:1"])
        self.assertEqual([i["ref"]["path"] for i in model.ancestor_items(a, "G:1+S:1", strict=True)], ["G:1"])

    def test_context_modes_inherit_and_override(self):
        m, a, b, c = build()
        self.assertEqual(model.context_modes(m, b), (model.SHOWN, model.HIDDEN, model.HIDDEN))
        m["settings"]["unassigned"] = model.AS_EARLIER
        self.assertEqual(model.context_modes(m, b), (model.SHOWN, model.HIDDEN, model.SHOWN))
        b["earlier"] = model.GHOSTED
        m["settings"]["unassigned"] = model.HIDDEN
        self.assertEqual(model.context_modes(m, b), (model.GHOSTED, model.HIDDEN, model.HIDDEN))

    def test_move_step_crosses_sections(self):
        m, a, b, c = build()
        self.assertTrue(model.move_step(m, b["id"], delta=1))   # Rollers -> top of Drive
        self.assertEqual([s["title"] for s in m["sections"][1]["steps"]], ["Rollers", "Motor"])
        self.assertTrue(model.move_step(m, b["id"], delta=-1))  # back to end of Frame
        self.assertEqual([s["title"] for s in m["sections"][0]["steps"]], ["Base", "Rollers"])
        self.assertFalse(model.move_step(m, a["id"], delta=-1))

    def test_move_step_to_section_index(self):
        # Drop indexes are counted with the dragged step already removed.
        m, a, b, c = build()
        self.assertTrue(model.move_step(m, a["id"], m["sections"][0]["id"], 1))
        self.assertEqual([s["title"] for s in m["sections"][0]["steps"]], ["Rollers", "Base"])
        self.assertTrue(model.move_step(m, c["id"], m["sections"][0]["id"], 0))
        self.assertEqual([s["title"] for s in m["sections"][0]["steps"]], ["Motor", "Rollers", "Base"])
        self.assertEqual(m["sections"][1]["steps"], [])
        self.assertTrue(model.move_step(m, b["id"], m["sections"][1]["id"], 0))
        self.assertEqual([s["title"] for s in m["sections"][1]["steps"]], ["Rollers"])

    def test_covered(self):
        m, _, _, c = build()
        model.add_items(c, [ref("Gearbox:1")])
        covered = model.covered_paths(m)
        self.assertTrue(model.is_covered(covered, "Gearbox:1+Shaft:1"))
        self.assertFalse(model.is_covered(covered, "Guard:1"))


class ExplodeMoveTests(unittest.TestCase):

    def setUp(self):
        self.m, _, self.step, _ = build()      # step "Rollers": Roller:1, Roller:2
        self.centers = {"Roller:1": (0.0, 0.0, 1.0), "Roller:2": (0.0, 0.0, 3.0), "Bolt:1": (0.0, 0.0, 0.0)}

    def add(self, axis="+Z", parts=("Roller:1", "Roller:2"), **kw):
        ex = model.new_explode(model.new_direction(axis))
        ex.update(kw)
        model.set_explode_parts(self.step, ex, [ref(p) for p in parts])
        self.step["explodes"].append(ex)
        return ex

    def run_moves(self, upto=None):
        return model.evaluate(self.m, self.step, self.centers, upto)

    def test_uniform_move_follows_step_default(self):
        ex = self.add()
        moves = self.run_moves()
        self.assertEqual(moves["Roller:1"], [((0.0, 0.0, 5.0), True, ex["id"])])
        self.step["distance"] = 2.0
        self.assertEqual(self.run_moves()["Roller:2"][0][0], (0.0, 0.0, 2.0))

    def test_own_distance_and_per_part_override(self):
        ex = self.add("+X", distance=4.0)
        model.find_explode_part(ex, "Roller:2")["distance"] = 9.0
        moves = self.run_moves()
        self.assertEqual(moves["Roller:1"][0][0], (4.0, 0.0, 0.0))
        self.assertEqual(moves["Roller:2"][0][0], (9.0, 0.0, 0.0))

    def test_moves_chain_in_order(self):
        first = self.add("+X", distance=10.0)
        second = self.add("+Z", parts=("Roller:1",), distance=3.0)
        moves = self.run_moves()
        self.assertEqual([m[0] for m in moves["Roller:1"]], [(10.0, 0.0, 0.0), (0.0, 0.0, 3.0)])
        self.assertEqual([m[2] for m in moves["Roller:1"]], [first["id"], second["id"]])
        self.assertEqual(len(moves["Roller:2"]), 1)

    def test_upto_stops_after_that_move(self):
        first = self.add("+X")
        self.add("+Z")
        moves = self.run_moves(upto=first["id"])
        self.assertEqual(len(moves["Roller:1"]), 1)

    def test_stacked_by_position_uses_position_after_earlier_moves(self):
        self.add("+Z", parts=("Roller:2",), distance=-10.0)      # Roller:2 ends below Roller:1
        stack = self.add("+Z", spacing=model.STACKED, distance=1.0)
        moves = self.run_moves()
        by_id = {p: [m for m in moves[p] if m[2] == stack["id"]][0][0] for p in ("Roller:1", "Roller:2")}
        self.assertEqual(by_id["Roller:2"], (0.0, 0.0, 1.0))   # now lowest -> moves least
        self.assertEqual(by_id["Roller:1"], (0.0, 0.0, 2.0))

    def test_stacked_by_position_level_parts_move_together(self):
        self.centers["Roller:2"] = (4.0, 0.0, 1.004)               # level with Roller:1 along Z
        stack = self.add("+Z", parts=("Roller:1", "Roller:2", "Bolt:1"), spacing=model.STACKED, distance=1.0)
        moves = self.run_moves()
        got = {p: moves[p][0][0][2] for p in ("Roller:1", "Roller:2", "Bolt:1")}
        self.assertEqual(got, {"Bolt:1": 1.0, "Roller:1": 2.0, "Roller:2": 2.0})

    def test_stacked_reversed_moves_the_far_end_least(self):
        self.centers["Roller:2"] = (4.0, 0.0, 1.004)               # level with Roller:1 along Z
        self.add("+Z", parts=("Roller:1", "Roller:2", "Bolt:1"), spacing=model.STACKED_REVERSE, distance=1.0)
        moves = self.run_moves()
        got = {p: moves[p][0][0][2] for p in ("Roller:1", "Roller:2", "Bolt:1")}
        self.assertEqual(got, {"Bolt:1": 2.0, "Roller:1": 1.0, "Roller:2": 1.0})

    def test_stacked_base_distance_moves_everything_first(self):
        self.add("+Z", parts=("Roller:1", "Roller:2"), spacing=model.STACKED, distance=1.0, base=5.0)
        moves = self.run_moves()
        self.assertEqual(moves["Roller:1"][0][0], (0.0, 0.0, 6.0))     # lower: base + 1 tier
        self.assertEqual(moves["Roller:2"][0][0], (0.0, 0.0, 7.0))     # higher: base + 2 tiers

    def test_stacked_in_pick_order(self):
        self.add("+Y", spacing=model.STACKED_SELECTION, distance=2.0)
        moves = self.run_moves()
        self.assertEqual(moves["Roller:1"][0][0], (0.0, 2.0, 0.0))
        self.assertEqual(moves["Roller:2"][0][0], (0.0, 4.0, 0.0))

    def test_xyz_direction_uses_vector_length(self):
        ex = self.add(parts=("Roller:1",))
        ex["direction"] = {"kind": model.DIR_XYZ, "axis": None, "vector": [0.0, 3.0, 4.0],
                           "token": None, "flip": False}
        vec = self.run_moves()["Roller:1"][0][0]
        for got, want in zip(vec, (0.0, 3.0, 4.0)):
            self.assertAlmostEqual(got, want)
        self.assertEqual(model.direction_label(ex["direction"]), "X/Y/Z")

    def test_flip_and_labels(self):
        ex = self.add("+Z", parts=("Roller:1",), distance=1.0)
        ex["direction"]["flip"] = True
        self.assertEqual(self.run_moves()["Roller:1"][0][0], (-0.0, -0.0, -1.0))
        self.assertEqual(model.direction_label(ex["direction"]), "-Z")
        self.assertEqual(model.explode_label(self.step, ex), "Move 1")
        ex["name"] = "Rollers out"
        self.assertEqual(model.explode_label(self.step, ex), "Rollers out")

    def test_parts_missing_from_design_are_skipped(self):
        self.add(parts=("Roller:1", "Gone:1"))
        self.assertNotIn("Gone:1", self.run_moves())

    def test_set_explode_parts_keeps_settings_and_adds_to_step(self):
        ex = self.add(parts=("Roller:1",))
        model.find_explode_part(ex, "Roller:1")["trail"] = False
        model.set_explode_parts(self.step, ex, [ref("Roller:1"), ref("Bolt:1")])
        self.assertFalse(model.find_explode_part(ex, "Roller:1")["trail"])
        self.assertIn("Bolt:1", model.item_paths(self.step))

    def test_remove_items_also_leaves_explodes(self):
        ex = self.add()
        model.remove_items(self.step, ["Roller:1"])
        self.assertEqual([p["ref"]["path"] for p in ex["parts"]], ["Roller:2"])

    def test_schema4_moves_become_explodes(self):
        old = ('{"schema":4,"sections":[{"id":"s","title":"S","steps":[{"id":"a","title":"A","items":['
               '{"ref":{"path":"P:1"},"moves":[{"dir":[1,0,0],"distance":null,"stack":1,"trail":false},'
               '{"dir":[0,0,1],"distance":2,"stack":1,"trail":true}]},'
               '{"ref":{"path":"Q:1"},"moves":[{"dir":[1,0,0],"distance":null,"stack":2,"trail":true}]},'
               '{"ref":{"path":"R:1"},"moves":[]}]}]}]}')
        m = model.from_json(old)
        step = m["sections"][0]["steps"][0]
        self.assertTrue(all(set(i) == {"ref", "anchor", "bom"} for i in step["items"]))
        self.assertTrue(all(i["anchor"] is None for i in step["items"]))
        first, second = step["explodes"]
        self.assertEqual(model.direction_label(first["direction"]), "+X")
        self.assertEqual([(p["ref"]["path"], p["distance"], p["trail"]) for p in first["parts"]],
                         [("P:1", None, False), ("Q:1", 10.0, True)])
        self.assertEqual([(p["ref"]["path"], p["distance"]) for p in second["parts"]], [("P:1", 2)])
        moves = model.evaluate(m, step, {"P:1": (0, 0, 0), "Q:1": (0, 0, 0)})
        self.assertEqual([v for v, _, _ in moves["P:1"]], [(5.0, 0.0, 0.0), (0.0, 0.0, 2.0)])
        self.assertEqual(moves["Q:1"][0][0], (10.0, 0.0, 0.0))

    def test_schema1_offset_upgrades_to_explode(self):
        old = ('{"sections":[{"id":"s","title":"S","steps":[{"id":"a","title":"A","items":['
               '{"ref":{"path":"P:1"},"offset":[0,0,6],"trail":false}]}]}]}')
        m = model.from_json(old)
        step = m["sections"][0]["steps"][0]
        moves = model.evaluate(m, step, {"P:1": (0, 0, 0)})
        self.assertEqual(moves["P:1"], [((0.0, 0.0, 6.0), False, step["explodes"][0]["id"])])


class NamingTests(unittest.TestCase):

    def test_image_name_counts_steps_within_section(self):
        m, a, b, c = build()
        self.assertEqual(model.image_name(m, a), "Section 1 - Step 1 - Base.png")
        self.assertEqual(model.image_name(m, b), "Section 1 - Step 2 - Rollers.png")
        self.assertEqual(model.image_name(m, c), "Section 2 - Step 1 - Motor.png")

    def test_image_name_strips_illegal_characters(self):
        m, a, _, _ = build()
        a["title"] = 'Fit 1/2" bolts: tighten?'
        self.assertEqual(model.image_name(m, a), "Section 1 - Step 1 - Fit 12 bolts tighten.png")
        a["title"] = "  "
        self.assertEqual(model.image_name(m, a), "Section 1 - Step 1 - Untitled.png")


class ExplodeTests(unittest.TestCase):

    def test_uniform(self):
        self.assertEqual(explode.distances(explode.UNIFORM, 3, 2.0), [2.0, 2.0, 2.0])

    def test_stacked_orders_by_position(self):
        centers = [(0, 0, 5), (0, 0, 1), (0, 0, 3)]
        out = explode.distances(explode.STACKED, 3, 2.0, centers, (0, 0, 1))
        self.assertEqual(out, [6.0, 2.0, 4.0])

    def test_stacked_negative_direction_keeps_nearest_least(self):
        centers = [(0, 0, 5), (0, 0, 1), (0, 0, 3)]
        out = explode.distances(explode.STACKED, 3, 2.0, centers, (0, 0, -1))
        self.assertEqual(out, [2.0, 6.0, 4.0])

    def test_stacked_selection_order(self):
        self.assertEqual(explode.distances(explode.STACKED_SELECTION, 3, 1.5), [1.5, 3.0, 4.5])

    def test_away_axis(self):
        self.assertEqual(explode.away_axis((0.2, -3.0, 1.0)), ("Y", True))
        self.assertEqual(explode.away_axis((0.0, 0.0, 5.0)), ("Z", False))
        self.assertIsNone(explode.away_axis((0.0, 0.0, 0.0)))

    def test_stacked_level_parts_share_a_tier(self):
        # Two screws level with each other (same Z), a washer below them: washer 1x, screws 2x.
        centers = [(0, 0, 5.0), (3, 0, 5.004), (0, 0, 2.0)]
        self.assertEqual(explode.distances(explode.STACKED, 3, 1.0, centers, (0, 0, 1)), [2.0, 2.0, 1.0])
        self.assertEqual(explode.distances(explode.STACKED, 3, -1.0, centers, (0, 0, 1)), [-1.0, -1.0, -2.0])

    def test_parent_space_round_trip(self):
        # Parent rotated 90 degrees about Z.
        axes = ((0, 1, 0), (-1, 0, 0), (0, 0, 1))
        world = (3.0, 4.0, 5.0)
        local = explode.to_parent(world, axes)
        self.assertEqual(local, (4.0, -3.0, 5.0))
        back = explode.to_world(local, axes)
        for got, want in zip(back, world):
            self.assertAlmostEqual(got, want)

    def test_root_parent_is_identity(self):
        self.assertEqual(explode.to_world((1, 2, 3), None), (1, 2, 3))

    def test_normalize_zero_safe(self):
        self.assertEqual(explode.normalize((0, 0, 0)), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(explode.length(explode.normalize((3, 4, 0))), 1.0)


class SplitBodyTests(unittest.TestCase):
    """A split component's bodies are parts at "<occurrence>+#<body>": the occurrence is their ancestor."""

    def test_bodies_in_separate_steps(self):
        m = model.new_manual()
        s = model.add_section(m, "Gripper")
        base = model.add_step(m, s["id"], "Base")
        covers = model.add_step(m, s["id"], "Covers")
        model.add_items(base, [{"path": "Gripper:1+#Base", "name": "Base"}])
        model.add_items(covers, [{"path": "Gripper:1+#Cover1", "name": "Cover1"}])
        self.assertEqual(model.leaf_state(m, base["id"], "Gripper:1+#Base"), model.CURRENT)
        self.assertEqual(model.leaf_state(m, base["id"], "Gripper:1+#Cover1"), model.LATER)
        self.assertEqual(model.leaf_state(m, covers["id"], "Gripper:1+#Base"), model.EARLIER)

    def test_step_trail_look_overrides_the_manual(self):
        m, a, b, c = build()
        self.assertEqual(model.trail_look(m, a)["style"], m["settings"]["trail"]["style"])
        a["trail"] = {"color": "#ff0000"}
        look = model.trail_look(m, a)
        self.assertEqual(look["color"], "#ff0000")
        self.assertEqual(look["weight"], m["settings"]["trail"]["weight"])     # the rest: the manual's

    def test_add_step_before_and_after(self):
        m, a, b, c = build()
        sec = m["sections"][0]
        before = model.add_step(m, sec["id"], "Before B", before=b["id"])
        after = model.add_step(m, sec["id"], "After A", after=a["id"])
        self.assertEqual([st["title"] for st in sec["steps"]], ["Base", "After A", "Before B", "Rollers"])

    def test_overview_has_every_part_and_move(self):
        m, a, b, c = build()
        ex = model.new_explode(model.new_direction("+Z"))
        model.set_explode_parts(b, ex, [ref("Roller:1")])
        b["explodes"].append(ex)
        _, own = model.find_step(m, model.OVERVIEW_STEP)
        extra = model.new_explode(model.new_direction("+X"))
        model.set_explode_parts(own, extra, [ref("Motor:1")])
        own["explodes"].append(extra)
        step = model.effective_step(m, own)
        self.assertEqual(sorted(model.item_paths(step)), ["Base:1", "Motor:1", "Roller:1", "Roller:2"])
        self.assertEqual([e["id"] for e in step["explodes"]], [ex["id"], extra["id"]])  # steps' first
        self.assertEqual(len(own["explodes"]), 1)                     # its own moves stay its own
        m["overview"]["includeSteps"] = False
        self.assertEqual([e["id"] for e in model.effective_step(m, own)["explodes"]], [extra["id"]])
        self.assertEqual(model.leaf_state(m, model.OVERVIEW_STEP, "Loose:1"), model.UNASSIGNED)

    def test_prep_step_parts_stay_unassigned(self):
        m, a, b, c = build()
        a["prep"] = True
        covered = model.covered_paths(m)
        self.assertFalse(model.is_covered(covered, "Base:1"))       # only prepared so far
        self.assertTrue(model.is_covered(covered, "Roller:1"))
        self.assertEqual(model.prepared_in(m), {"Base:1": "Base"})
        model.add_items(c, [ref("Base:1")])                          # installed in a normal step
        self.assertTrue(model.is_covered(model.covered_paths(m), "Base:1"))

    def test_whole_component_item_covers_its_bodies(self):
        m = model.new_manual()
        s = model.add_section(m, "Gripper")
        step = model.add_step(m, s["id"], "All")
        model.add_items(step, [{"path": "Gripper:1", "name": "Gripper"}])
        self.assertEqual(model.leaf_state(m, step["id"], "Gripper:1+#Cover1"), model.CURRENT)
        self.assertTrue(model.is_covered(model.covered_paths(m), "Gripper:1+#Cover1"))


if __name__ == "__main__":
    unittest.main()
