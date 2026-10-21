"""Batch acceptance: offline H3 receipts, real mixed-audio FFmpeg stitching."""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generation_batches as batches
import media
import providers
import storage as db
import workflow
from app import app
from tests.test_generation_review import board, PROMPT


class GenerationBatchesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="generation-batches-")
        self.previous_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA),
            "VIDEO_STUDIO_CONFIG": str(Path(self.tmp.name) / "private.json")})
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.source = Path(self.tmp.name) / "sources"
        self.source.mkdir()
        self.clips = {}
        for color, size, audio in (("red", "320x180", False), ("blue", "160x160", True), ("green", "320x180", True)):
            target = self.source / f"{color}.mp4"
            args = [media.FFMPEG, "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24"]
            if audio:
                args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
            args += ["-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1"]
            if audio:
                args += ["-c:a", "aac"]
            args.append(target)
            media.run(args)
            self.clips[color] = target
        storyboard = board(count=2)
        for index, shot in enumerate(storyboard["shots"]):
            shot["duration"] = 4
            shot["prompt"] = PROMPT.replace("blue", "red" if index == 0 else "blue")
        self.plan = db.create("jobs", {"kind": "generation_plan", "status": "succeeded", "payload": {"ratio": "16:9", "resolution": "768P"},
            "result": {"storyboard": storyboard}})
        self.endpoint = f"/api/generation-plans/{self.plan['id']}/batches"
        self.calls, self.receipts, self.query_calls = [], {}, []
        self.reject_once = set()
        self.unknown_once = set()
        self.query_fail_once = set()
        self.real_create = providers.create_generation
        self.create_patch = patch.object(providers, "create_generation", side_effect=self.create_fake)
        self.query_patch = patch.object(providers, "query_generation", side_effect=self.query_fake)
        self.download_patch = patch.object(workflow, "_download", side_effect=self.download_fake)
        for item in (self.create_patch, self.query_patch, self.download_patch):
            item.start()

    def tearDown(self):
        deadline = time.monotonic() + 20
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.02)
        self.client.__exit__(None, None, None)
        for item in (self.create_patch, self.query_patch, self.download_patch):
            item.stop()
        self.env.stop()
        db.DATA = self.previous_data
        self.tmp.cleanup()

    def create_fake(self, request, assets):
        self.calls.append(copy.deepcopy(request))
        color = next(color for color in ("red", "green", "blue") if color in request["prompt"])
        if color in self.reject_once:
            self.reject_once.remove(color)
            raise providers.ProviderError("离线模拟：供应商明确拒绝")
        if color in self.unknown_once:
            self.unknown_once.remove(color)
            raise providers.SubmissionUnknown("离线模拟：提交结果未知")
        ident = uuid.uuid4().hex
        self.receipts[ident] = color
        return {"task_id": ident}

    def query_fake(self, task_id):
        self.query_calls.append(task_id)
        color = self.receipts[task_id]
        if color in self.query_fail_once:
            self.query_fail_once.remove(color)
            raise providers.ProviderError("离线模拟：查询暂时不可用")
        return {"status": "succeeded", "url": "https://fixture.invalid/" + task_id}

    def download_fake(self, url, target):
        shutil.copyfile(self.clips[self.receipts[url.rsplit("/", 1)[-1]]], target)

    def post(self, path, payload, status=200):
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def start(self, **extra):
        request = {"expected_revision": workflow.get_generation_plan(self.plan["id"])["revision"],
                   "submission_id": str(uuid.uuid4()), **extra}
        return self.post(self.endpoint, request)["batch"], request

    def wait(self, ident, expected="succeeded"):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.client.get("/api/generation-batches/" + ident).json()["batch"]
            if result["status"] not in {"queued", "running", "assembling"}:
                self.assertEqual(result["status"], expected, result)
                return result
            time.sleep(.04)
        self.fail("Offline batch did not reach a terminal state")

    def rgb_at(self, path, second):
        result = media.run([media.FFMPEG, "-nostdin", "-v", "error", "-ss", str(second), "-i", path,
            "-vf", "crop=2:2:iw/2:ih/2,scale=1:1", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        return tuple(result.stdout[:3])

    def test_confirm_all_real_ordered_stitch_and_regenerate_only_one_segment(self):
        estimate = self.client.get(self.endpoint.replace("/batches", "/batch-quote")).json()["quote"]
        self.assertEqual(estimate["amount"], 4)
        self.assertEqual(len(db.records("jobs")), 1)  # A quote cannot confirm or submit.
        first, request = self.start()
        first = self.wait(first["id"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.post(self.endpoint, request)["batch"]["id"], first["id"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(shot["confirmed"] for shot in workflow.get_generation_plan(self.plan["id"])["storyboard"]["shots"]))
        original = db.get("assets", first["output"]["asset_id"])
        self.assertTrue(original["has_audio"])
        self.assertEqual((original["width"], original["height"]), (320, 180))
        self.assertAlmostEqual(original["duration"], 8, delta=.2)
        self.assertGreater(self.rgb_at(original["path"], 1)[0], 200)
        self.assertGreater(self.rgb_at(original["path"], 5)[2], 200)
        old_hash = media.file_hash(original["path"])
        view = workflow.save_generation_shot(self.plan["id"], "shot-1", {
            "script": "绿色纸方块在桌面展开。", "prompt": PROMPT.replace("blue", "green"), "expected_revision": first["review_revision"]})
        replacement = self.post(f"/api/generation-batches/{first['id']}/regenerate", {
            "shot_id": "shot-1", "expected_revision": view["revision"], "submission_id": str(uuid.uuid4())})["batch"]
        replacement = self.wait(replacement["id"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(replacement["version"], 2)
        self.assertEqual(replacement["shots"][1]["job_id"], first["shots"][1]["job_id"])
        self.assertEqual(replacement["quote"]["amount"], 2)
        self.assertEqual(replacement["quote"]["new_shot_count"], 1)
        new_asset = db.get("assets", replacement["output"]["asset_id"])
        self.assertGreater(self.rgb_at(new_asset["path"], 1)[1], 80)
        self.assertGreater(self.rgb_at(new_asset["path"], 5)[2], 200)
        self.assertEqual(media.file_hash(original["path"]), old_hash)
        listed = self.client.get(self.endpoint).json()["batches"]
        self.assertEqual([row["version"] for row in listed], [2, 1])
        self.assertEqual(listed[1]["output"], first["output"])
        with patch.object(workflow, "_schedule_job", side_effect=lambda job, operation, executor=None: job):
            newer, _ = self.start()
            self.assertEqual(newer["version"], 3)
            self.assertEqual(newer["family_id"], first["family_id"])
            # A historical version cannot bypass a newer in-flight batch.
            self.post(f"/api/generation-batches/{first['id']}/regenerate", {
                "shot_id": "shot-1", "expected_revision": workflow.get_generation_plan(self.plan["id"])["revision"],
                "submission_id": str(uuid.uuid4())}, 400)
            self.post(self.endpoint, {"expected_revision": workflow.get_generation_plan(self.plan["id"])["revision"],
                "submission_id": str(uuid.uuid4())}, 400)
            reviewed = workflow.get_generation_plan(self.plan["id"])
            shot = reviewed["storyboard"]["shots"][0]
            standalone = {"storyboard_job_id": self.plan["id"], "storyboard_shot_id": shot["id"],
                "review_revision": reviewed["revision"], "prompt": shot["prompt"], "duration": shot["duration"], "mode": shot["mode"]}
            before = len(db.records("jobs"))
            for extra in ({}, {"submission_id": str(uuid.uuid4())}):
                self.post("/api/generations", {**standalone, **extra}, 400)
            self.assertEqual(len(db.records("jobs")), before)

    def test_retry_only_failed_and_resume_unknown_only_with_existing_receipt(self):
        self.reject_once.add("blue")
        failed, _ = self.start()
        failed = self.wait(failed["id"], "failed")
        self.assertTrue(failed["can_retry"])
        successful_job = next(row["job_id"] for row in failed["shots"] if row["status"] == "succeeded")
        retried = self.post(f"/api/generation-batches/{failed['id']}/retry", {"submission_id": str(uuid.uuid4())})["batch"]
        retried = self.wait(retried["id"])
        self.assertEqual(len(self.calls), 3)
        self.assertIn(successful_job, [row["job_id"] for row in retried["shots"]])
        self.query_fail_once.add("blue")
        unknown, _ = self.start()
        unknown = self.wait(unknown["id"], "unknown")
        self.assertTrue(unknown["can_resume"])
        count = len(self.calls)
        self.post(f"/api/generation-batches/{failed['id']}/retry", {"submission_id": str(uuid.uuid4())}, 400)
        self.post(f"/api/generation-batches/{unknown['id']}/retry", {"submission_id": str(uuid.uuid4())}, 400)
        self.post(f"/api/generation-batches/{unknown['id']}/resume", {})
        self.wait(unknown["id"])
        self.assertEqual(len(self.calls), count)
        self.unknown_once.add("red")
        no_receipt, _ = self.start()
        no_receipt = self.wait(no_receipt["id"], "unknown")
        self.assertFalse(no_receipt["can_resume"])
        self.assertFalse(no_receipt["can_retry"])
        count = len(self.calls)
        self.post(f"/api/generation-batches/{no_receipt['id']}/resume", {}, 400)
        self.post(f"/api/generation-batches/{no_receipt['id']}/retry", {"submission_id": str(uuid.uuid4())}, 400)
        self.assertEqual(len(self.calls), count)
        # HTTP acceptance followed by a local usage write failure is still paid
        # uncertainty, never permission to submit again.
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.request.return_value.status_code = 200
        fake.request.return_value.json.return_value = {"task_id": "accepted-paid-task"}
        with patch.object(providers, "create_generation", self.real_create), \
             patch.object(providers, "_config", return_value={"minimax_base_url": "https://api.minimax.cn", "minimax_api_key": "offline-test"}), \
             patch.object(providers.httpx, "Client", return_value=fake), \
             patch.object(providers.usage, "record_response", side_effect=RuntimeError("离线模拟：用量记录失败")):
            paid = workflow.generate({"submission_id": str(uuid.uuid4()), "mode": "T2VA", "prompt": PROMPT, "duration": 4})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                paid = db.get("jobs", paid["id"])
                if paid["status"] not in {"queued", "running"}:
                    break
                time.sleep(.02)
        self.assertEqual(fake.request.call_count, 1)
        self.assertEqual(paid["status"], "unknown")
        self.assertTrue(paid["provider_state"]["submitting"])
        self.assertFalse(batches._safe_retry(paid))

    def test_atomic_concurrent_submit_and_restart_keep_frozen_snapshot_without_paid_replay(self):
        view = workflow.save_generation_shot(self.plan["id"], "shot-2", {"script": "改了脚本未改提示词", "expected_revision": 0})
        before = len(db.records("jobs"))
        self.post(self.endpoint, {"expected_revision": view["revision"], "submission_id": str(uuid.uuid4())}, 400)
        self.assertEqual(len(db.records("jobs")), before)
        self.assertFalse(any(shot["confirmed"] for shot in workflow.get_generation_plan(self.plan["id"])["storyboard"]["shots"]))
        view = workflow.save_generation_shot(self.plan["id"], "shot-2", {"prompt": PROMPT + "\n", "expected_revision": view["revision"]})
        request = {"expected_revision": view["revision"], "submission_id": str(uuid.uuid4())}
        barrier = threading.Barrier(4)
        def create(_):
            barrier.wait()
            return self.client.post(self.endpoint, json=request)
        with patch.object(workflow, "_schedule_job", side_effect=lambda job, operation, executor=None: job):
            with ThreadPoolExecutor(max_workers=4) as pool:
                replies = list(pool.map(create, range(4)))
            self.assertEqual({row.status_code for row in replies}, {200})
            ids = {row.json()["batch"]["id"] for row in replies}
            self.assertEqual(len(ids), 1)
            batch_id = ids.pop()
            self.assertEqual(len(db.records("generation_batches")), 1)
            self.assertEqual(len(db.records("jobs")), 3)
            frozen = db.get("generation_batches", batch_id)
            current = workflow.get_generation_plan(self.plan["id"])
            workflow.save_generation_shot(self.plan["id"], "shot-1", {
                "script": "计划现在改为绿色", "prompt": PROMPT.replace("blue", "green"), "expected_revision": current["revision"]})
            db.mark_interrupted()
            batches.reconcile_startup()
            repeated = self.post(self.endpoint, request)["batch"]
            self.assertEqual(repeated["id"], batch_id)
            self.assertEqual(repeated["status"], "failed")
            self.assertEqual(self.calls, [])
        retry = self.post(f"/api/generation-batches/{batch_id}/retry", {"submission_id": str(uuid.uuid4())})["batch"]
        retry = self.wait(retry["id"])
        self.assertEqual(len(self.calls), 2)
        self.assertIn("red", self.calls[0]["prompt"] + self.calls[1]["prompt"])
        self.assertNotIn("green", self.calls[0]["prompt"] + self.calls[1]["prompt"])
        self.assertEqual(db.get("generation_batches", batch_id)["segments"], frozen["segments"])
        standalone = db.create("jobs", {"kind": "generation", "status": "unknown",
            "payload": {"storyboard_job_id": self.plan["id"], "storyboard_shot_id": "shot-1"},
            "provider_state": {"submitting": True}, "result": None})
        count = len(self.calls)
        current = workflow.get_generation_plan(self.plan["id"])
        for status in ("queued", "running", "unknown"):
            db.update("jobs", standalone["id"], {"status": status})
            self.post(self.endpoint, {"expected_revision": current["revision"], "submission_id": str(uuid.uuid4())}, 400)
            self.post(f"/api/generation-batches/{batch_id}/retry", {"submission_id": str(uuid.uuid4())}, 400)
            self.post(f"/api/generation-batches/{retry['id']}/regenerate", {
                "shot_id": "shot-1", "expected_revision": current["revision"], "submission_id": str(uuid.uuid4())}, 400)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(db.records("generation_batches")), 2)


if __name__ == "__main__":
    unittest.main()
