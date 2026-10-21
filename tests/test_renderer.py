"""Critical-path integration checks using generated media, without model APIs."""
from __future__ import annotations

import array
import json
from copy import deepcopy
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer import RenderError, render_plan


def ffmpeg(*args: str, binary: bool = False):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                          capture_output=True, check=True, text=not binary).stdout


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class RenderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="renderer-test-")
        cls.root = Path(cls.tmp.name)
        cls.image = cls.root / "image with 'quotes'.png"
        cls.voiced = cls.root / "voiced.mp4"
        cls.silent = cls.root / "silent.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=0x143650:s=640x360", "-frames:v", "1", str(cls.image))
        ffmpeg("-f", "lavfi", "-i", "color=c=0x184228:s=320x240:r=30:d=1.5", "-f", "lavfi", "-i",
               "sine=frequency=440:sample_rate=48000:duration=1.5", "-vf", "drawbox=c=0x8f1828:t=fill:enable='lt(t,0.3)'",
               "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.voiced))
        ffmpeg("-f", "lavfi", "-i", "color=c=0x501b24:s=320x240:r=30:d=1.5", "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(cls.silent))
        cls.assets = [
            {"id": "image", "path": str(cls.image), "kind": "image", "duration": 0},
            {"id": "voice", "path": str(cls.voiced), "kind": "video", "duration": 1.5},
            {"id": "silent", "path": str(cls.silent), "kind": "video", "duration": 1.5},
        ]
        cls.plan = {
            "id": "test", "title": "三类素材", "ratio": "9:16", "width": 720, "height": 1280, "duration": 2.4,
            "clips": [
                {"id": "a", "asset_id": "image", "start": 0, "end": .8, "duration": .8, "caption": "第一段字幕", "transition": "cut"},
                {"id": "b", "asset_id": "voice", "start": .2, "end": 1, "duration": .8, "caption": "", "transition": "cut"},
                {"id": "c", "asset_id": "silent", "start": .3, "end": 1.1, "duration": .8, "caption": "第三段字幕", "transition": "fade"},
            ],
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def audio_rms(self, path: Path, start: float, duration: float = .15) -> float:
        data = ffmpeg("-ss", str(start), "-i", str(path), "-t", str(duration), "-vn", "-ac", "1", "-ar", "8000",
                      "-f", "f32le", "pipe:1", binary=True)
        samples = array.array("f")
        samples.frombytes(data)
        return math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))

    def subtitle_pixels(self, path: Path, timestamp: float) -> int:
        data = ffmpeg("-ss", str(timestamp), "-i", str(path), "-frames:v", "1", "-vf", "crop=720:200:0:1060,format=gray",
                      "-f", "rawvideo", "pipe:1", binary=True)
        return sum(value > 230 for value in data)

    def center_pixel(self, path: Path, timestamp: float) -> bytes:
        return ffmpeg("-ss", str(timestamp), "-i", str(path), "-frames:v", "1", "-vf", "crop=2:2:360:640,format=rgb24",
                      "-f", "rawvideo", "pipe:1", binary=True)[:3]

    def test_three_media_types_timing_subtitles_and_audio(self):
        path = self.root / "mixed.mp4"
        progress = []
        result = render_plan(self.plan, self.assets, path, progress.append)
        self.assertEqual((result["width"], result["height"], result["has_audio"]), (720, 1280, True))
        self.assertAlmostEqual(result["duration"], 2.4, delta=.1)
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(manifest["compiled_timeline"]["frame_count"], 72)
        self.assertEqual([(c["start_frame"], c["end_frame"]) for c in manifest["compiled_timeline"]["clips"]], [(0, 24), (24, 48), (48, 72)])
        self.assertEqual(manifest["compiled_timeline"]["clips"][1]["source_start_seconds"], .2)
        self.assertEqual(len(manifest["output_sha256"]), 64)
        self.assertEqual(len(manifest["assets"]), 3)
        self.assertEqual(progress[-1]["progress"], 1)
        self.assertLess(self.audio_rms(path, .2), .001)
        self.assertGreater(self.audio_rms(path, 1), .03)
        self.assertLess(self.audio_rms(path, 2), .001)
        # Captions are burned into their actual clip intervals, including the cut.
        self.assertGreater(self.subtitle_pixels(path, .70), 150)
        self.assertLess(self.subtitle_pixels(path, .90), 30)
        self.assertGreater(self.subtitle_pixels(path, 2.10), 150)
        # The second clip starts at source 0.2: its red prefix lasts only 0.1s.
        early = self.center_pixel(path, .85)
        later = self.center_pixel(path, 1.05)
        self.assertGreater(early[0], early[1] * 2)
        self.assertGreater(later[1], later[0] * 2)
        self.assertFalse(any(self.root.glob("render-*")), "temporary output should be removed")

    def test_music_mixes_into_silent_image(self):
        plan = deepcopy(self.plan)
        plan.update(width=1280, height=720, ratio="16:9", duration=.8, music_asset_id="voice")
        plan["clips"] = [plan["clips"][0]]
        plan["clips"][0]["caption"] = ""
        path = self.root / "music.mp4"
        result = render_plan(plan, self.assets, path)
        self.assertAlmostEqual(result["duration"], .8, delta=.1)
        self.assertGreater(self.audio_rms(path, .35), .005)
        self.assertEqual((result["width"], result["height"]), (1280, 720))

    def test_speed_exact_cut_and_separate_audio_gains(self):
        plan = deepcopy(self.plan)
        plan.update(duration=2.4, music_asset_id='voice', music_volume=.5, music_start=.1)
        plan['clips'] = [
            {'id':'fast', 'asset_id':'voice', 'start':0, 'end':1.2, 'duration':.6,
             'speed':2, 'original_volume':0, 'music_gain':1},
            {'id':'slow', 'asset_id':'voice', 'start':0, 'end':.6, 'duration':1.2,
             'speed':.5, 'original_volume':0, 'music_gain':.15},
            {'id':'sound', 'asset_id':'voice', 'start':.3, 'end':.9, 'duration':.6,
             'speed':1, 'original_volume':1, 'music_gain':0}]
        path = self.root/'speed-audio.mp4'
        receipt = render_plan(plan, self.assets, path)
        # Same source color change occurs at half/double the source time.
        for at, red in [(.1,True),(.2,False),(17/30,False),(18/30,True),(1.0,True),(1.3,False)]:
            pixel = self.center_pixel(path, at)
            self.assertGreater(pixel[0] if red else pixel[1], 2*(pixel[1] if red else pixel[0]), at)
        normal, ducked, original = [self.audio_rms(path,t) for t in (.4,1.05,2.0)]
        self.assertGreater(normal,ducked*4)
        self.assertGreater(original,normal*1.5)
        frozen=json.loads(Path(receipt['manifest_path']).read_text())
        self.assertEqual([c['speed'] for c in frozen['compiled_timeline']['clips']],[2,.5,1])
        self.assertEqual(frozen['music_start'],.1)

    def test_rejects_invalid_plan_before_writing_output(self):
        variants = []
        out_of_bounds = deepcopy(self.plan)
        out_of_bounds["clips"][1].update(start=1.2, end=2.0)
        variants.append((out_of_bounds, "超出真实素材时长"))
        inconsistent = deepcopy(self.plan)
        inconsistent["duration"] = 10
        variants.append((inconsistent, "总时长"))
        unsupported = deepcopy(self.plan)
        unsupported["clips"][0]["transition"] = "wipe"
        variants.append((unsupported, "不支持转场"))
        bad_caption = deepcopy(self.plan)
        bad_caption["clips"][0]["caption"] = "字" * 200
        variants.append((bad_caption, "字幕超过三行"))
        for index, (plan, expected) in enumerate(variants):
            with self.subTest(expected=expected):
                output = self.root / f"invalid-{index}.mp4"
                with self.assertRaisesRegex(RenderError, expected):
                    render_plan(plan, self.assets, output)
                self.assertFalse(output.exists())
        with self.assertRaisesRegex(RenderError, "已存在"):
            render_plan(self.plan, self.assets, self.voiced)


if __name__ == "__main__":
    unittest.main()
