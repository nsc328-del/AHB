"""Paid-submit deduplication using an offline provider; no H3 request is made."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import app
import storage as db
import workflow


class ProviderError(RuntimeError):
    pass


class SubmissionUnknown(ProviderError):
    pass


class GenerationIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cutroom-generation-idempotency-")
        self.previous_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA),
                                         "VIDEO_STUDIO_CONFIG": str(Path(self.tmp.name) / "private.json")})
        self.env.start()
        self.calls = []
        self.provider = types.SimpleNamespace(ProviderError=ProviderError, SubmissionUnknown=SubmissionUnknown,
                                              create_generation=self.unknown)
        self.provider_patch = patch.object(workflow, "providers", return_value=self.provider)
        self.provider_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        deadline = time.monotonic() + 5
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.01)
        self.client.__exit__(None, None, None)
        self.provider_patch.stop()
        self.env.stop()
        db.DATA = self.previous_data
        self.tmp.cleanup()

    def unknown(self, request, assets):
        self.calls.append(request)
        raise SubmissionUnknown("离线模拟：提交结果未知")

    def await_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.01)
        self.fail("Offline submission did not finish")

    def test_concurrent_same_key_returns_one_task_and_conflicting_payload_is_rejected(self):
        request = {"submission_id": str(uuid.uuid4()), "mode": "T2VA", "prompt": "离线样片", "duration": 4}
        barrier = threading.Barrier(6)
        def submit(index):
            barrier.wait()
            # Object key ordering must not create another paid request.
            body = request if index % 2 else dict(reversed(list(request.items())))
            return self.client.post("/api/generations", json=body)
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(submit, range(6)))
        self.assertEqual({r.status_code for r in responses}, {200})
        ids = {r.json()["job_id"] for r in responses}
        self.assertEqual(len(ids), 1)
        job = self.await_job(next(iter(ids)))
        self.assertEqual(job["status"], "unknown")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("submission_id", self.calls[0])
        self.assertNotIn("request_fingerprint", self.calls[0])
        self.assertEqual(len(job["payload"]["request_fingerprint"]), 64)
        self.assertEqual(len(db.records("jobs")), 1)
        conflict = self.client.post("/api/generations", json={**request, "prompt": "另一条内容"})
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(len(self.calls), 1)
        # Re-entering lifespan does not retry unknown paid intent.
        with TestClient(app):
            repeated = self.client.post("/api/generations", json=request)
            self.assertEqual(repeated.json()["job_id"], job["id"])
        self.assertEqual(len(self.calls), 1)

    def test_failed_or_interrupted_submission_key_does_not_auto_retry(self):
        def rejected(request, assets):
            self.calls.append(request)
            raise ProviderError("离线模拟：明确拒绝")
        self.provider.create_generation = rejected
        request = {"submission_id": str(uuid.uuid4()), "prompt": "离线失败"}
        first = self.client.post("/api/generations", json=request).json()["job_id"]
        self.assertEqual(self.await_job(first)["status"], "failed")
        self.assertEqual(self.client.post("/api/generations", json=request).json()["job_id"], first)
        self.assertEqual(len(self.calls), 1)
        class HeldExecutor:
            def submit(self, *args):
                self.saved = args
        interrupted_request = {**request, "submission_id": str(uuid.uuid4())}
        with patch.object(workflow, "EXECUTOR", HeldExecutor()):
            queued = workflow.generate(interrupted_request)
        db.mark_interrupted()
        repeated = workflow.generate(interrupted_request)
        self.assertEqual(repeated["id"], queued["id"])
        self.assertEqual(repeated["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        invalid = self.client.post("/api/generations", json={**request, "submission_id": "not-a-uuid"})
        self.assertEqual(invalid.status_code, 400)


if __name__ == "__main__":
    unittest.main()
