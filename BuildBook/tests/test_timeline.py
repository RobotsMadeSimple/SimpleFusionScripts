"""Animation timeline (lib/timeline.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import timeline  # noqa: E402

CAM_A = {"eye": [10, 0, 0], "target": [0, 0, 0], "up": [0, 0, 1], "type": 1, "extents": 10}
CAM_B = {"eye": [0, 10, 0], "target": [0, 0, 0], "up": [0, 0, 1], "type": 1, "extents": 20}
TIMING = {"moveSeconds": 1.0, "cameraSeconds": 2.0, "pauseSeconds": 0.5}


class TimelineTests(unittest.TestCase):
    def test_assembly_runs_moves_backwards(self):
        segs = timeline.build([("a", 2, CAM_A), ("b", 1, CAM_B)], timing=TIMING)
        # a: jump 0 + pause .5 + moves 2 + pause .5 = 3; b: glide 2 + .5 + 1 + .5 = 4
        self.assertAlmostEqual(timeline.total(segs), 7.0)
        self.assertEqual(timeline.at(segs, 0.1)[:2], ("a", 2.0))            # starts exploded
        step, p, _ = timeline.at(segs, 1.5)                                   # half way through a's moves
        self.assertEqual(step, "a")
        self.assertAlmostEqual(p, 1.0)
        self.assertEqual(timeline.at(segs, 2.9)[:2], ("a", 0.0))            # assembled
        step, p, cam = timeline.at(segs, 4.0)                                 # gliding to b's view
        self.assertEqual((step, p), ("b", 1.0))
        self.assertAlmostEqual(cam["extents"], 15.0)
        self.assertEqual(timeline.at(segs, 99)[:2], ("b", 0.0))

    def test_disassembly_runs_steps_backwards_and_moves_out(self):
        segs = timeline.build([("a", 2, CAM_A), ("b", 1, CAM_B)], reverse=True, timing=TIMING)
        self.assertEqual(timeline.at(segs, 0.1)[:2], ("b", 0.0))
        self.assertEqual(timeline.at(segs, 99)[:2], ("a", 2.0))

    def test_moves_together_take_one_move_time(self):
        segs = timeline.build([("a", 3, CAM_A, True)], timing=TIMING)
        self.assertAlmostEqual(timeline.total(segs), 2.0)                     # .5 + 1 + .5
        self.assertEqual(timeline.at(segs, 99)[:2], ("a", 0.0))

    def test_step_without_a_view_keeps_the_camera(self):
        segs = timeline.build([("a", 1, CAM_A), ("b", 1, None)], timing=TIMING)
        self.assertEqual(timeline.at(segs, 99)[2], CAM_A)


if __name__ == "__main__":
    unittest.main()
