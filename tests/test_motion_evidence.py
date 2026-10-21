"""Continuous background displacement is evidence; moving subjects are not pans."""
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motion_evidence import extract_motion_evidence


class MotionEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="motion-evidence-")
        self.root = Path(self.tmp.name)
        generator = np.random.default_rng(21)
        self.background = cv2.GaussianBlur(generator.integers(0, 256, (180, 320, 3), dtype=np.uint8), (3, 3), .5)
        self.subject = generator.integers(0, 256, (60, 40, 3), dtype=np.uint8)

    def tearDown(self):
        self.tmp.cleanup()

    def video(self, name, frame):
        path = self.root / f"{name}.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 180))
        self.assertTrue(writer.isOpened())
        for index in range(45):
            writer.write(frame(index))
        writer.release()
        return path

    def test_distributed_translation_and_moving_subject_are_distinct(self):
        pan = self.video("pan", lambda index: cv2.warpAffine(
            self.background, np.float32([[1, 0, -3*index], [0, 1, 0]]), (320, 180),
            borderMode=cv2.BORDER_WRAP))
        def person(index):
            frame = self.background.copy()
            x = 105 + index
            frame[60:120, x:x+40] = self.subject
            return frame
        static = self.video("subject", person)
        moving = extract_motion_evidence(pan, .2, 1.3, self.root/"pan-frames")
        fixed = extract_motion_evidence(static, .2, 1.3, self.root/"subject-frames")
        self.assertLess(moving["statistics"]["image_dx_widths_per_second_median"], -.2)
        self.assertGreater(moving["statistics"]["horizontal_sign_agreement"], .95)
        self.assertGreater(moving["statistics"]["qualified_pair_fraction"], .8)
        self.assertLess(abs(fixed["statistics"]["image_dx_widths_per_second_median"]), .01)
        self.assertGreater(fixed["statistics"]["center_residual_widths_per_second_p90_median"],
                           fixed["statistics"]["edge_residual_widths_per_second_median"] + .01)
        self.assertEqual(moving["model_calls"], 0)

    def test_known_cut_is_excluded_and_evidence_times_remain_absolute(self):
        other = np.flip(self.background, 0).copy()
        path = self.video("cut", lambda index: self.background if index < 21 else other)
        result = extract_motion_evidence(path, .3, 1.1, self.root/"cut-frames", cut_times=[.7])
        self.assertEqual(result["known_cuts_inside"], [.7])
        self.assertEqual(result["skipped_cut_pairs"], [.7])
        self.assertLessEqual(len(result["frames"]), 9)
        self.assertTrue(all(.3 <= frame["time"] < 1.1 for frame in result["frames"]))
        self.assertTrue(all(Path(frame["path"]).is_file() for frame in result["frames"]))
        self.assertFalse(any(pair["start"] < .7 <= pair["end"] for pair in result["pair_evidence"]))
        with self.assertRaises(ValueError):
            extract_motion_evidence(path, 0, 61, self.root/"too-long")


if __name__ == "__main__":
    unittest.main()
