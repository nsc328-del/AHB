"""Timing/abstention tests use known synthetic sources, not estimated labels."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

import rhythm_evidence as rhythm


class RhythmEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def clicks(self, at, duration):
        data = np.zeros(round(duration*rhythm.SAMPLE_RATE), np.float32)
        rng = np.random.default_rng(7)
        click = rng.normal(size=round(.025*rhythm.SAMPLE_RATE))*np.exp(-np.arange(round(.025*rhythm.SAMPLE_RATE))/65)
        for time in at:
            index = round(time*rhythm.SAMPLE_RATE)
            data[index:index+len(click)] += .4*click[:max(0, min(len(click), len(data)-index))]
        return data

    def analyze(self, samples, cuts=()):
        path = self.root/"source.wav"
        wavfile.write(path, rhythm.SAMPLE_RATE, samples)
        return rhythm.analyze_audio_rhythm(path, self.root/"evidence", cuts)

    def test_local_pulse_changes_and_cut_offset_sign(self):
        first = np.arange(.4, 12, .5)
        second = np.arange(12.4, 24, .75)
        result = self.analyze(self.clicks(np.r_[first, second], 24), [0, 2.43, 14.62, 24])
        windows = result["local_windows"]
        self.assertAlmostEqual(windows[0]["bpm"], 120, delta=2)
        self.assertAlmostEqual(windows[-1]["bpm"], 80, delta=2)
        self.assertIsNone(result["summary"]["global_bpm"])
        match = result["cut_matches"][0]["onset"]
        self.assertTrue(match["within_tolerance"])
        self.assertGreater(match["cut_offset_seconds"], 0)
        self.assertLess(match["cut_offset_seconds"], .06)
        self.assertEqual(result["alignment_summary"]["cut_count"], 2)
        self.assertEqual(len(result["alignment_summary"]["shifted_cut_baseline"]), 6)
        self.assertEqual(result["alignment_summary"]["tolerance_seconds"], .1)
        matches, _ = rhythm._match_cuts([0, 1, 2, 3], 3,
                                       [{"time": 1.1, "strength": 1}, {"time": 1.9, "strength": 1}], [])
        self.assertTrue(all(row["onset"]["within_tolerance"] for row in matches))
        local = rhythm.rhythm_for_interval(result, 12, 16)
        self.assertTrue(all(12 <= row["time"] < 16 for row in local["onsets"]))
        self.assertEqual(result["sound_source_type"], "unclassified")

    def test_silence_tone_and_irregular_transients_do_not_force_bpm(self):
        silence = self.analyze(np.zeros(rhythm.SAMPLE_RATE*12, np.float32))
        self.assertFalse(silence["onsets"])
        self.assertFalse(silence["beat_candidates"])
        self.assertTrue(all(window["bpm"] is None for window in silence["local_windows"]))
        tiny = self.analyze(np.zeros(100, np.float32))
        self.assertFalse(tiny["beat_candidates"])
        t = np.arange(12*rhythm.SAMPLE_RATE)/rhythm.SAMPLE_RATE
        tone = self.analyze((.2*np.sin(2*np.pi*220*t)).astype(np.float32))
        self.assertTrue(all(window["bpm"] is None for window in tone["local_windows"]))
        rng = np.random.default_rng(12)
        at = np.cumsum(rng.uniform(.18, .95, 30))
        irregular = self.analyze(self.clicks(at[at < 11.8], 12))
        self.assertTrue(all(window["bpm"] is None for window in irregular["local_windows"]))
        # Intense transients remain onsets, without being promoted to music.
        self.assertGreater(len(irregular["onsets"]), 8)

    def test_decoding_uses_original_timeline_and_no_audio_is_explicit(self):
        source = self.root/"audio.wav"
        wavfile.write(source, rhythm.SAMPLE_RATE, self.clicks([.4, 1.4, 2.4], 5))
        target = self.root/"delayed.mp4"
        rhythm._run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-f", "lavfi", "-i", "color=black:s=32x32:r=25:d=7",
                     "-itsoffset", "1.25", "-i", source, "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", "mpeg4", "-c:a", "aac", "-t", "7", target])
        result = rhythm.analyze_audio_rhythm(target, self.root/"delay")
        closest = min(result["onsets"], key=lambda row: abs(row["time"]-1.65))
        self.assertAlmostEqual(closest["time"], 1.65, delta=.035)
        self.assertFalse(any(row["time"] < 1.1 for row in result["onsets"]))
        self.assertGreater(result["timing"]["audio_stream_start_time"], 1)
        with patch.object(rhythm, "_decode", return_value=(None, 6, {})):
            result = rhythm.analyze_audio_rhythm(self.root/"silent-video.mp4", self.root/"none", [0, 3, 6])
        self.assertEqual(result["status"], "no_audio")
        self.assertIsNone(result["cut_matches"][0]["onset"])


if __name__ == "__main__":
    unittest.main()
