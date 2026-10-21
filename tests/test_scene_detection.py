"""Decoded-frame checks for pans, rapid edits, and long-video evidence coverage."""
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import media


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SceneDetectionTest(unittest.TestCase):
    def write_video(self, path, frames, fps=30):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 180))
        self.assertTrue(writer.isOpened())
        for frame in frames:
            writer.write(frame)
        writer.release()

    def test_pan_is_one_shot_but_adjacent_fast_cuts_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pan-and-fast-cuts.avi"
            rng = np.random.default_rng(912)
            canvases = []
            for index in range(4):
                image = rng.integers(0, 255, (45, 200, 3), dtype=np.uint8)
                canvases.append(cv2.resize(image, (800, 180), interpolation=cv2.INTER_NEAREST))
            frames = []
            for index, length in enumerate([60, 6, 6, 48]):
                for offset in range(length):
                    left = offset * 5
                    frames.append(canvases[index][:, left:left+320])
            self.write_video(path, frames)
            result = media.scene_detection(path, 4)
            np.testing.assert_allclose(result["boundaries"], [0, 2, 2.2, 2.4, 4], atol=1/30)
            self.assertEqual(result["decoded_frames"], 120)

    def test_long_sampling_retains_each_shot_and_gradual_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "many-shots.avi"
            rng = np.random.default_rng(414)
            scenes = []
            for index in range(26):
                image = rng.integers(0, 255, (18, 32, 3), dtype=np.uint8)
                scenes.append(cv2.resize(image, (320, 180), interpolation=cv2.INTER_NEAREST))
            frames = [scene for scene in scenes[:25] for _ in range(20)]
            # A gradual two-second change at the tail must receive dense evidence,
            # without pretending that it has one frame-exact hard-cut boundary.
            frames.extend(cv2.addWeighted(scenes[24], 1-i/39, scenes[25], i/39, 0) for i in range(40))
            self.write_video(path, frames, fps=20)
            detected = media.scene_detection(path, 27)
            self.assertTrue(any(x["start"] < 26 < x["end"] for x in detected["gradual_transitions"]))
            sampled, boundaries = media.sample_frames({"path": str(path), "kind": "video", "duration": 27}, root / "frames", reference=True)
            self.assertGreater(len(sampled), 48)
            self.assertEqual(len(boundaries), 26)
            for second in range(25):
                self.assertTrue(any(second < frame["time"] < second+1 for frame in sampled), second)
            tail_times = [x["time"] for x in sampled if x["time"] >= 25]
            self.assertGreaterEqual(len(tail_times), 3)
            self.assertGreater(max(tail_times), 26.8)
            self.assertTrue(all(Path(frame["path"]).is_file() for frame in sampled))


if __name__ == "__main__":
    unittest.main()
