"""Real decoded-frame coverage across small-subject reference cuts."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ReferenceSamplingTest(unittest.TestCase):
    def test_small_subject_cuts_and_tail_are_sampled(self):
        with tempfile.TemporaryDirectory(prefix="media-sampling-") as temporary:
            root = Path(temporary)
            colors = [(34, 34, 239), (235, 155, 57), (58, 138, 116)]
            inputs = []
            for index, color in enumerate(colors):
                image = Image.new("RGB", (960, 540), (244, 244, 240))
                draw = ImageDraw.Draw(image)
                if index == 0:
                    draw.rounded_rectangle((365, 145, 595, 375), 12, fill=color)
                elif index == 1:
                    draw.ellipse((365, 145, 595, 375), fill=color)
                else:
                    draw.polygon(((480, 140), (350, 375), (610, 375)), fill=color)
                path = root / f"source-{index}.png"
                image.save(path)
                inputs.extend(["-loop", "1", "-framerate", "30", "-t", str([31, 90, 59][index] / 30), "-i", str(path)])
            video = root / "reference.mp4"
            subprocess.run(
                [media.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", *inputs,
                 "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
                capture_output=True, check=True, timeout=60,
            )
            metadata = media.probe(video)
            self.assertAlmostEqual(metadata["duration"], 6, delta=1 / 30)
            frames, boundaries = media.sample_frames(
                {**metadata, "path": str(video)}, root / "frames", reference=True
            )
            self.assertEqual(len(boundaries), 4, boundaries)
            self.assertAlmostEqual(boundaries[1], 31 / 30, delta=1 / 30)
            self.assertAlmostEqual(boundaries[2], 121 / 30, delta=1 / 30)
            self.assertTrue(any(frame["time"] > 5.5 for frame in frames), frames)
            seen = set()
            for frame in frames:
                with Image.open(frame["path"]) as image:
                    pixel = image.convert("RGB").getpixel((480, 270))
                nearest = min(range(3), key=lambda index: sum((pixel[c] - colors[index][c]) ** 2 for c in range(3)))
                seen.add(nearest)
            self.assertEqual(seen, {0, 1, 2}, "Evidence must include all three actual subjects")
            for start, end in zip(boundaries, boundaries[1:]):
                self.assertTrue(any(start < frame["time"] < end for frame in frames), (start, end))


if __name__ == "__main__":
    unittest.main()
