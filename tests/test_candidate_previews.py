"""Mocked planning plus real isolated FFmpeg candidate previews; no paid calls."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image

from app import app
import decisions
import diversity
import renderer
import storage as db
import workflow


class CandidatePreviewsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="candidate-previews-")
        self.original_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA),
                                          "VIDEO_STUDIO_CONFIG": str(Path(self.tmp.name) / "private.json")})
        self.env.start()
        self.ratios = patch.dict(workflow.RATIOS, {"1:1": (160, 160)})
        self.ratios.start()
        self.calls = []
        self.provider = types.SimpleNamespace(create_plan=self.plan_response, ProviderError=RuntimeError)
        self.model = patch.object(workflow, "providers", return_value=self.provider)
        self.model.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.assets = []
        for index, color in enumerate(("#223bea", "#eb9e44", "#458878")):
            path = db.DATA / "assets" / f"fixture-{index}.png"
            Image.new("RGB", (96, 96), color).save(path)
            asset = workflow.register_asset(path, path.name)
            asset = db.update("assets", asset["id"], {"analysis": {"summary": "合成色块"}, "audio_status": "no_audio"})
            self.assets.append(asset)

    def tearDown(self):
        deadline = time.monotonic() + 30
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        self.client.__exit__(None, None, None)
        self.model.stop()
        self.ratios.stop()
        self.env.stop()
        db.DATA = self.original_data
        self.tmp.cleanup()

    def plan_response(self, brief, assets, options, reference=None, previous=None):
        self.calls.append(copy.deepcopy(options))
        key = options["candidate_strategy"]["key"]
        order = {"evidence": [0, 1, 2], "question": [1, 2, 0], "process": [2, 0, 1],
                 "contrast": [0, 2, 1], "exploration": [2, 1, 0]}[key]
        return {"title": key, "clips": [{"asset_id": self.assets[i]["id"], "start": 0,
                                         "end": .4, "duration": .4, "caption": "",
                                         "role": "opening" if index == 0 else "body",
                                         "transition": "cut"} for index, i in enumerate(order)]}

    def wait(self, job_id):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.05)
        self.fail("Isolated candidate preview job timed out")

    def start(self, count=3, **extra):
        payload = {"brief": "使用三张合成图制作短片", "asset_ids": [item["id"] for item in self.assets],
                   "candidate_count": count, "duration": 1.2, "ratio": "1:1", **extra}
        response = self.client.post("/api/projects", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def view(self, batch_id):
        return self.client.get(f"/api/candidate-batches/{batch_id}").json()

    def test_three_and_five_real_previews_require_explicit_human_choice(self):
        hashes = [asset["sha256"] for asset in self.assets]
        for count in (3, 5):
            started = self.start(count)
            result = self.wait(started["job_id"])
            self.assertEqual(result["status"], "succeeded", result)
            view = self.view(started["batch_id"])
            self.assertEqual(len(view["plans"]), count)
            self.assertEqual(len(view["batch"]["display_order"]), count)
            self.assertEqual(sum(bool(p["candidate_strategy"]["exploration"]) for p in view["plans"]), 1)
            exploration = next(p for p in view["plans"] if p["candidate_strategy"]["exploration"])
            self.assertGreaterEqual(exploration["diversity"]["minimum_batch_distance"], diversity.EXPLORATION_DISTANCE)
            for plan in view["plans"]:
                preview = view["previews"][plan["id"]]
                self.assertEqual(preview["status"], "succeeded")
                self.assertEqual(plan["diversity"]["quality"]["preview_validation"], "passed")
                self.assertIsNone(plan["diversity"]["history_similarity"])
                video = self.client.get(preview["render"]["url"])
                self.assertEqual(video.status_code, 200)
                self.assertGreater(len(video.content), 1000)
            project = workflow.project_view(started["project_id"])
            self.assertIsNone(project["plan_id"])
            self.assertIsNone(project["latest_render"])
            self.assertEqual(project["renders"], [])
            self.assertEqual(len(project["candidate_renders"]), count)
            self.assertTrue(project["candidate_pending"])
            endpoint = f"/api/candidate-batches/{started['batch_id']}/events"
            chosen = view["plans"][1]["id"]
            self.assertEqual(self.client.post(endpoint, json={"event_type": "selected", "plan_id": chosen}).status_code, 400)
            self.assertEqual(self.client.post(endpoint, json={"event_type": "viewed", "plan_id": chosen}).status_code, 200)
            self.assertEqual(self.client.post(endpoint, json={"event_type": "selected", "plan_id": chosen}).status_code, 200)
            project = workflow.project_view(started["project_id"])
            self.assertEqual(project["plan_id"], chosen)
            self.assertEqual(project["latest_render"]["id"], view["previews"][chosen]["render"]["id"])
            self.assertEqual(len(project["renders"]), 1)
            self.assertFalse(project["candidate_pending"])
            self.assertEqual(project["latest_candidate_batch_count"], count)
        self.assertEqual(len(self.calls), 8)  # No redundant initial draft.
        self.assertEqual([db.get("assets", asset["id"])["sha256"] for asset in self.assets], hashes)
        for invalid in (2, 6, "3", 3.0, True, None):
            before = len(db.records("projects"))
            response = self.client.post("/api/projects", json={"brief": "invalid", "asset_ids": [self.assets[0]["id"]],
                                                               "candidate_count": invalid})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(db.records("projects")), before)

    def test_partial_render_failure_retry_uses_same_plans_and_successful_previews(self):
        original = renderer.render_plan
        failed_once = []
        def flaky(plan, *args, **kwargs):
            if plan["candidate_strategy"]["key"] == "question" and not failed_once:
                failed_once.append(True)
                raise ValueError("合成的单条预览失败")
            return original(plan, *args, **kwargs)
        with patch.object(renderer, "render_plan", side_effect=flaky):
            started = self.start()
            result = self.wait(started["job_id"])
        self.assertEqual(result["status"], "failed", result)
        view = self.view(started["batch_id"])
        self.assertEqual(view["batch"]["status"], "failed")
        self.assertEqual(len(view["plans"]), 3)
        failed = [pid for pid, preview in view["previews"].items() if preview["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        successful = {pid: preview["render"]["id"] for pid, preview in view["previews"].items() if preview["status"] == "succeeded"}
        endpoint = f"/api/candidate-batches/{started['batch_id']}/events"
        viewed = next(iter(successful))
        self.assertEqual(self.client.post(endpoint, json={"event_type": "viewed", "plan_id": viewed}).status_code, 200)
        self.assertEqual(self.client.post(endpoint, json={"event_type": "selected", "plan_id": viewed}).status_code, 400)
        with patch.object(self.provider, "create_plan", side_effect=AssertionError("Retry must not call a model")):
            response = self.client.post(f"/api/candidate-batches/{started['batch_id']}/retry-previews",
                                        json={"plan_ids": failed})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.wait(response.json()["job_id"])["status"], "succeeded")
        retried = self.view(started["batch_id"])
        self.assertEqual(retried["batch"]["display_order"], view["batch"]["display_order"])
        self.assertEqual(retried["batch"]["plan_ids"], view["batch"]["plan_ids"])
        self.assertEqual({pid: retried["previews"][pid]["render"]["id"] for pid in successful}, successful)
        self.assertEqual(len(self.calls), 3)
        self.assertIsNone(db.get("projects", started["project_id"])["plan_id"])
        self.assertEqual(self.client.post(endpoint, json={"event_type": "selected", "plan_id": viewed}).status_code, 200)
        history = decisions._history_snapshot(db.get("projects", started["project_id"]))
        self.assertEqual(len(history), 1)
        plan = db.get("plans", viewed)
        same = diversity.evaluate(plan, history, [])
        self.assertEqual(same["history_similarity"], 1)
        self.assertEqual(same["novelty_penalty"], .08)
        self.assertEqual(same["ranking_score"], .92)
        other = db.create("projects", {"title": "不相关作品", "plan_id": None})
        self.assertEqual(decisions._history_snapshot(other), [])
        copied = copy.deepcopy(plan)
        copied["clips"].reverse()
        for clip, duration in zip(copied["clips"], (.8, .2, .2)):
            clip["duration"] = clip["end"] = duration
        self.assertLess(diversity.evaluate(copied, history, [])["history_similarity"], 1)

    def test_exploration_requires_structure_not_label_or_small_jitter(self):
        first = self.plan_response("", [], {"candidate_strategy": {"key": "evidence"}})
        first = workflow.validate_plan(dict(first, ratio="1:1"), self.assets)
        first["source_kinds"] = {asset["id"]: "image" for asset in self.assets}
        fake = copy.deepcopy(first)
        fake["title"] = "探索"
        for clip in fake["clips"]:
            clip.update(role="new", caption="different", transition="fade")
        self.assertEqual(diversity.editorial_distance(first, fake), 0)
        self.assertFalse(decisions._distinct(first, fake))
        split = copy.deepcopy(first)
        head = dict(split["clips"][0], duration=.2, end=.2)
        split["clips"] = [head, dict(head, role="other"), *split["clips"][1:]]
        self.assertEqual(diversity.editorial_distance(first, split), 0)
        self.assertFalse(decisions._distinct(first, split))
        self.provider.create_plan = lambda *args, **kwargs: copy.deepcopy(fake)
        started = self.start()
        result = self.wait(started["job_id"])
        self.assertEqual(result["status"], "failed")
        view = self.view(started["batch_id"])
        self.assertEqual(view["batch"]["phase"], "planning")
        self.assertEqual(len(view["plans"]), 1)
        self.assertEqual(db.records("renders"), [])
        self.assertEqual(self.client.post(f"/api/candidate-batches/{started['batch_id']}/retry-previews", json={}).status_code, 400)

    def test_legacy_plan_batch_can_add_previews_without_replanning(self):
        project = db.create("projects", {"title": "历史计划", "brief": "历史要求", "plan_id": None,
                                         "asset_ids": [asset["id"] for asset in self.assets], "selection": []})
        plans = []
        for key in ("evidence", "question", "exploration"):
            proposed = self.plan_response("", [], {"candidate_strategy": {"key": key}})
            proposed["ratio"] = "1:1"
            plans.append(workflow.save_plan(project["id"], proposed, activate=False))
        batch = db.create("candidate_batches", {"project_id": project["id"], "status": "succeeded",
                                                "plan_ids": [plan["id"] for plan in plans], "job_id": None,
                                                "decision_status": "pending", "strategy_version": "three-structures-v1"})
        view = self.view(batch["id"])
        self.assertEqual(view["batch"]["count"], 3)
        self.assertEqual(view["batch"]["status"], "failed")
        self.assertTrue(all(preview["retryable"] for preview in view["previews"].values()))
        with patch.object(self.provider, "create_plan", side_effect=AssertionError("Do not replan legacy candidates")):
            response = self.client.post(f"/api/candidate-batches/{batch['id']}/retry-previews", json={})
            self.assertEqual(response.status_code, 200, response.text)
            job = self.wait(response.json()["job_id"])
            self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(self.view(batch["id"])["batch"]["status"], "succeeded")
        self.assertIsNone(db.get("projects", project["id"])["plan_id"])


if __name__ == "__main__":
    unittest.main()
