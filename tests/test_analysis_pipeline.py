"""Analysis ordering, evidence truth and asset separation; no external model calls."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import storage as db
import transcription
import workflow


def transcript(probability=.98):
    return {"text": "今天出发", "language": "zh", "language_probability": probability,
            "segments": [{"start": 0, "end": 1, "text": "今天出发",
                          "words": [{"word": "今天出发", "probability": .9}]}]}


class AnalysisPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = patch.object(db, "DATA", Path(self.tmp.name))
        self.data.start()
        self.addCleanup(self.data.stop)
        db.initialize()

    def asset(self, **fields):
        return db.create("assets", {"name": "clip.mp4", "path": str(db.DATA / "clip.mp4"),
            "kind": "video", "duration": 2., "has_audio": True, "audio_status": "not_transcribed",
            "analysis": None, "purpose": "reference", **fields})

    def settled(self, job):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            current = db.get("jobs", job["id"])
            if current["status"] not in {"queued", "running"}:
                self.assertEqual(current["status"], "succeeded", current.get("error"))
                return current
            time.sleep(.025)
        self.fail("job did not settle")

    @staticmethod
    def frames(asset, directory, reference):
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "frame.jpg"
        target.write_bytes(b"synthetic")
        return [{"path": str(target), "time": .5}], [0., 2.]

    def test_upload_waits_for_speech_before_model_and_keeps_analysis_snapshot(self):
        asset = self.asset()
        started, release, called = threading.Event(), threading.Event(), threading.Event()
        def asr(*args, **kwargs):
            started.set()
            release.wait(5)
            return transcript()
        def analyze(enriched, frames, reference=False):
            self.assertTrue(release.is_set())
            self.assertEqual(enriched["transcript"]["text"], "今天出发")
            called.set()
            return {"summary": "画面与对白融合", "tags": [], "segments": [], "structure": []}
        with patch.object(transcription, "transcribe", side_effect=asr), \
             patch.object(media := workflow.media, "sample_frames", side_effect=self.frames), \
             patch.object(providers, "analyze_asset", side_effect=analyze):
            jobs = workflow.queue_uploaded_asset(asset["id"])
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(called.wait(.4))
            finally:
                release.set()
            for job in jobs:
                self.settled(job)
            stored = db.get("assets", asset["id"])["analysis"]
            self.assertTrue(stored["audio_used_in_analysis"])
            self.assertEqual(stored["audio_evidence"]["status"], "used")
            self.assertEqual(len(stored["shots"]), 1)
            self.assertEqual(db.get("analyses", stored["analysis_id"])["result"], stored)
            with patch.object(transcription, "transcribe", return_value=transcript(.2)):
                self.settled(workflow.transcribe_asset(asset["id"]))
            # New ASR cannot pretend that an older model call consumed it.
            self.assertEqual(db.get("assets", asset["id"])["analysis"], stored)

    def test_uncertain_music_asr_is_preserved_but_not_sent_and_reanalysis_is_versioned(self):
        raw = transcript(.3)
        asset = self.asset(audio_status="succeeded", transcript=raw, analysis={"summary": "旧结果", "stale": True})
        observed = []
        def analyze(enriched, frames, reference=False):
            observed.append(enriched["transcript"])
            return {"summary": "只依据画面", "tags": [], "segments": [], "structure": []}
        with patch.object(workflow.media, "sample_frames", side_effect=self.frames), \
             patch.object(providers, "analyze_asset", side_effect=analyze):
            self.settled(workflow.analyze(asset["id"], True))
            first = db.get("assets", asset["id"])["analysis"]
            self.settled(workflow.analyze(asset["id"], True))
        final = db.get("assets", asset["id"])["analysis"]
        self.assertEqual(observed, [None, None])
        self.assertFalse(final["audio_used_in_analysis"])
        self.assertEqual(final["audio_evidence"]["status"], "uncertain")
        self.assertEqual(final["transcript"], raw)
        self.assertNotIn("stale", final)
        self.assertNotEqual(first["analysis_id"], final["analysis_id"])
        self.assertEqual(db.get("analyses", first["analysis_id"])["result"], first)

    def test_reference_identity_cannot_be_used_as_editing_material(self):
        for fields in ({"purpose": "reference"}, {"purpose": "library", "analysis": {"reference_analysis": True}},
                       {"purpose": None, "analysis": {"structure": [{"role": "开场"}]}}):
            asset = self.asset(**fields)
            with self.assertRaisesRegex(ValueError, "样片"):
                workflow.create_project({"brief": "测试", "asset_ids": [asset["id"]]})
            with self.assertRaisesRegex(ValueError, "样片"):
                workflow.validate_plan({"clips": [{"asset_id": asset["id"], "start": 0, "end": 1}]}, [asset])
        accepted, state = transcription.analysis_evidence({
            **transcript(), "segments": transcript()["segments"] +
            [{"text": "不确定", "start": 1, "end": 2, "words": [{"probability": .1}]}]})
        self.assertEqual(state["status"], "partial")
        self.assertEqual(len(accepted["segments"]), 1)
