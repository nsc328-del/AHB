"""Upload-triggered queues, real synthetic media, and mocked model/ASR contracts.

No supplier API, paid generation, model download, or production database is used.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
from app import app
import storage as db
import transcription
import workflow


class ProviderError(RuntimeError):
    pass


class ConfigurationError(ProviderError):
    pass


class AutoAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cutroom-upload-analysis-")
        self.previous_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA),
                                         "VIDEO_STUDIO_CONFIG": str(Path(self.tmp.name) / "private.json")})
        self.env.start()
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
        self.provider = types.SimpleNamespace(ProviderError=ProviderError, ConfigurationError=ConfigurationError,
                                              analyze_asset=self.analysis, create_plan=self.plan)
        self.mock = patch.object(workflow, "providers", return_value=self.provider)
        self.mock.start()
        self.asr = patch.object(transcription, "transcribe", return_value={
            "text": "", "segments": [], "language": "zh", "model": "mock-local-asr",
            "duration": .2, "engine": "mock-local-asr", "local": True, "has_speech": False})
        self.asr_mock = self.asr.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.gate.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not workflow.ACTIVE and not any(j["status"] in {"queued", "running"} for j in db.records("jobs")):
                break
            time.sleep(.02)
        self.client.__exit__(None, None, None)
        self.asr.stop()
        self.mock.stop()
        self.env.stop()
        db.DATA = self.previous_data
        self.tmp.cleanup()

    def image(self, color="blue"):
        output = io.BytesIO()
        Image.new("RGB", (120, 80), color).save(output, format="PNG")
        return output.getvalue()

    def analysis(self, asset, frames, reference=False):
        self.calls.append((asset["id"], reference))
        self.gate.wait(5)
        return {"summary": "合成素材的离线分析", "tags": ["合成"], "segments": [],
                **({"structure": [{"start": 0, "end": asset["duration"], "role": "展示"}]} if reference else {})}

    def plan(self, brief, assets, options, reference=None, previous=None):
        return {"title": "离线剪辑", "summary": "契约测试", "clips": [
            {"asset_id": assets[0]["id"], "start": 0, "end": 1, "duration": 1,
             "caption": "", "role": "opening", "label": "合成图", "transition": "cut"}]}

    def await_job(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.02)
        self.fail("Mock job did not finish")

    def test_uploads_queue_without_browser_and_concurrent_plan_reuses_analysis(self):
        legacy = db.create("assets", {"kind": "image", "analysis_status": "not_analyzed", "analysis": None})
        self.gate.clear()
        response = self.client.post("/api/assets", data={"purpose": "library"}, files=[
            ("files", ("one.png", self.image(), "image/png")),
            ("files", ("two.png", self.image("red"), "image/png")),
        ])
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["jobs"]), 2)
        self.assertEqual(payload["job_ids"], [job["id"] for job in payload["jobs"]])
        first = payload["assets"][0]
        self.assertEqual(first["purpose"], "library")
        self.assertEqual(first["analysis_job_id"], payload["job_ids"][0])
        self.assertEqual({job["kind"] for job in payload["jobs"]}, {"analysis"})
        with ThreadPoolExecutor(max_workers=6) as pool:
            duplicate_ids = list(pool.map(lambda _: workflow.analyze(first["id"])["id"], range(6)))
        self.assertEqual(set(duplicate_ids), {first["analysis_job_id"]})
        planned = self.client.post("/api/projects", json={"brief": "合成契约", "asset_ids": [first["id"]],
                                                         "duration": 1, "ratio": "9:16"}).json()
        # No additional browser-side /analyze request is needed for either upload.
        self.gate.set()
        for job_id in payload["job_ids"]:
            self.assertEqual(self.await_job(job_id)["status"], "succeeded")
        self.assertEqual(self.await_job(planned["job_id"])["status"], "succeeded")
        self.assertEqual(Counter(asset_id for asset_id, _ in self.calls),
                         Counter({asset["id"]: 1 for asset in payload["assets"]}))
        self.assertEqual(db.get("assets", legacy["id"])["analysis_status"], "not_analyzed")
        self.assertFalse(any(job.get("payload", {}).get("asset_id") == legacy["id"] for job in db.records("jobs")))

    def test_reference_upload_uses_reference_analysis_and_audio_only_uses_local_asr(self):
        video = Path(self.tmp.name) / "reference.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=0.4",
                        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=20)
        response = self.client.post("/api/assets", data={"purpose": "reference"},
                                    files={"files": ("reference.mp4", video.read_bytes(), "video/mp4")})
        self.assertEqual(response.status_code, 200, response.text)
        uploaded = response.json()
        self.assertEqual(uploaded["jobs"][0]["kind"], "reference")
        self.assertEqual(self.await_job(uploaded["job_ids"][0])["status"], "succeeded")
        asset = db.get("assets", uploaded["assets"][0]["id"])
        self.assertEqual(asset["purpose"], "reference")
        self.assertTrue(asset["analysis"]["reference_analysis"])
        self.assertEqual(self.calls, [(asset["id"], True)])
        before = len(db.records("assets"))
        denied = self.client.post("/api/assets", data={"purpose": "reference"},
                                 files={"files": ("wrong.png", self.image(), "image/png")})
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(len(db.records("assets")), before)
        output = io.BytesIO()
        with wave.open(output, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 3200)
        audio = self.client.post("/api/assets", files={"files": ("voice.wav", output.getvalue(), "audio/wav")}).json()
        self.assertEqual([job["kind"] for job in audio["jobs"]], ["transcription"])
        self.assertEqual(self.await_job(audio["job_ids"][0])["status"], "succeeded")
        self.assertEqual(db.get("assets", audio["assets"][0]["id"])["audio_status"], "succeeded")
        self.assertEqual(len(self.calls), 1)
        self.asr_mock.assert_called_once()

    def test_configuration_failure_and_restart_preserve_upload_and_job(self):
        attempted = []
        self.gate.clear()
        def unconfigured(*args, **kwargs):
            attempted.append(args[0]["id"])
            self.gate.wait(5)
            raise ConfigurationError("请配置素材视觉分析模型")
        self.provider.analyze_asset = unconfigured
        response = self.client.post("/api/assets", files={"files": ("new.png", self.image(), "image/png")}).json()
        waiting = threading.Event()
        original_lock = workflow._asset_lock
        def observed_lock(asset_id, phase):
            if phase == "analysis_work" and threading.current_thread().name.startswith("video-studio"):
                waiting.set()
            return original_lock(asset_id, phase)
        with patch.object(workflow, "_asset_lock", side_effect=observed_lock):
            planned = self.client.post("/api/projects", json={"brief": "等待同一分析的方案",
                                      "asset_ids": [response["assets"][0]["id"]], "duration": 1}).json()
            self.assertTrue(waiting.wait(3))
            self.gate.set()
            self.assertEqual(self.await_job(planned["job_id"])["status"], "needs_configuration")
        job = self.await_job(response["job_ids"][0])
        self.assertEqual(job["status"], "needs_configuration")
        self.assertEqual(attempted, [response["assets"][0]["id"]])
        asset = db.get("assets", response["assets"][0]["id"])
        self.assertIsNone(asset["analysis"])
        self.assertTrue(Path(asset["path"]).is_file())
        self.assertEqual(asset["analysis_status"], "needs_configuration")
        class HeldExecutor:
            def submit(self, *args):
                self.saved = args
        with patch.object(workflow, "ANALYSIS_EXECUTOR", HeldExecutor()):
            queued = self.client.post("/api/assets", files={"files": ("queued.png", self.image(), "image/png")}).json()
        self.assertEqual(db.get("jobs", queued["job_ids"][0])["status"], "queued")
        db.mark_interrupted()
        self.assertEqual(db.get("jobs", queued["job_ids"][0])["status"], "failed")
        self.assertEqual(db.get("assets", queued["assets"][0]["id"])["analysis_status"], "failed")
        with TestClient(app):
            self.assertEqual(db.get("jobs", queued["job_ids"][0])["status"], "failed")
            self.assertEqual(len(db.records("jobs")), 3)


if __name__ == "__main__":
    unittest.main()
