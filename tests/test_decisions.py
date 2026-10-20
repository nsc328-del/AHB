"""Candidate/selection lineage with local fixtures and mocked model responses.

No model or publishing API is called. These tests verify records and contracts,
not the editorial quality of a generated video.
"""
from __future__ import annotations

import copy
import json
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
import decisions
import storage as db
import workflow
from app import app


class ProviderError(RuntimeError):
    pass


class ConfigurationError(ProviderError):
    pass


class DecisionContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cutroom-decisions-")
        self.previous_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA),
                                         "VIDEO_STUDIO_CONFIG": str(Path(self.tmp.name) / "private.json")})
        self.env.start()
        self.calls = []
        self.provider = types.SimpleNamespace(ProviderError=ProviderError, ConfigurationError=ConfigurationError,
                                              create_plan=self.model_plan)
        self.mock = patch.object(workflow, "providers", return_value=self.provider)
        self.mock.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.assets = [db.create("assets", {"name": f"合成图 {i}", "kind": "image", "duration": 0, "width": 100,
                                           "height": 100, "has_audio": False, "sha256": f"fixture-{i}",
                                           "analysis": {"analysis_id": f"analysis-{i}", "summary": "合成契约素材"},
                                           "audio_status": "no_audio"}) for i in range(3)]
        self.project = db.create("projects", {"title": "合成作品", "brief": "按三张合成图剪一条短片",
                                             "asset_ids": [a["id"] for a in self.assets], "selection": [],
                                             "target_duration": 3, "ratio": "9:16", "template_id": "free",
                                             "reference_id": None, "plan_id": None, "messages": []})
        self.base = workflow.save_plan(self.project["id"], self.plan([0, 1, 2], "初稿"))
        self.case = db.create("publication_cases", {"id": "local-case-only", "platform": "douyin",
                                                   "metrics": {"plays": 12}, "observed_at": "2026-10-20",
                                                   "title": "不应发送给模型的历史原始文案"})

    def tearDown(self):
        deadline = time.monotonic() + 10
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.02)
        self.client.__exit__(None, None, None)
        self.mock.stop()
        self.env.stop()
        db.DATA = self.previous_data
        self.tmp.cleanup()

    def plan(self, order, title):
        return {"title": title, "summary": "合成契约方案", "ratio": "9:16",
                "clips": [{"asset_id": self.assets[i]["id"], "start": 0, "end": 1, "duration": 1,
                           "label": str(i), "caption": "", "role": "body", "transition": "cut"} for i in order]}

    def model_plan(self, brief, assets, options, reference=None, previous=None):
        self.calls.append(copy.deepcopy({"brief": brief, "assets": assets, "options": options, "reference": reference}))
        if previous:
            result = copy.deepcopy(previous)
            result["title"] = "AI 修改了历史初稿"
            return result
        key = options["candidate_strategy"]["key"]
        return self.plan({"evidence": [0, 1, 2], "question": [1, 2, 0], "process": [2, 0, 1]}[key], key)

    def wait(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.02)
        self.fail("Local fixture job did not complete")

    def start(self):
        response = self.client.post(f"/api/projects/{self.project['id']}/candidates",
                                    json={"count": 3, "reference_case_ids": [self.case["id"]]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_frozen_candidates_selection_and_historical_revision_lineage(self):
        started, release = threading.Event(), threading.Event()
        original = self.provider.create_plan
        def blocked(*args, **kwargs):
            started.set()
            release.wait(5)
            return original(*args, **kwargs)
        self.provider.create_plan = blocked
        result = self.start()
        self.assertTrue(started.wait(3))
        self.assertEqual(db.get("projects", self.project["id"])["plan_id"], self.base["id"])
        release.set()
        job = self.wait(result["job_id"])
        self.assertEqual(job["status"], "succeeded", job)
        batch_id = result["batch_id"]
        response = self.client.get(f"/api/candidate-batches/{batch_id}").json()
        plans = response["plans"]
        self.assertEqual(len(plans), 3)
        self.assertEqual(len({json.dumps(decisions._structure(p)) for p in plans}), 3)
        self.assertEqual({p["base_version_id"] for p in plans}, {self.base["id"]})
        self.assertEqual(db.get("projects", self.project["id"])["plan_id"], self.base["id"])
        self.assertEqual(response["batch"]["input_snapshot"]["assets"][0]["analysis_id"], "analysis-0")
        self.assertNotIn(self.case["title"], json.dumps(response["batch"], ensure_ascii=False))
        self.assertNotIn(self.case["id"], json.dumps(self.calls, ensure_ascii=False))
        self.assertNotIn(self.case["title"], json.dumps(self.calls, ensure_ascii=False))
        endpoint = f"/api/candidate-batches/{batch_id}/events"
        selected = plans[1]
        self.assertEqual(self.client.post(endpoint, json={"event_type": "rejected_all"}).status_code, 200)
        self.assertEqual(db.get("projects", self.project["id"])["plan_id"], self.base["id"])
        self.assertEqual(self.client.post(endpoint, json={"event_type": "selected", "plan_id": selected["id"]}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={"event_type": "viewed", "plan_id": self.base["id"]}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={"event_type": "viewed", "plan_id": selected["id"]}).status_code, 200)
        event = self.client.post(endpoint, json={"event_type": "selected", "plan_id": selected["id"], "reason": "查看后选择"}).json()
        self.assertEqual(event["current_plan_id"], selected["id"])
        repeated = self.client.post(endpoint, json={"event_type": "selected", "plan_id": selected["id"]}).json()
        self.assertEqual(repeated["id"], event["id"])
        modified = copy.deepcopy(selected)
        modified["clips"][0]["caption"] = "人工修改"
        manual = self.client.put(f"/api/projects/{self.project['id']}/plan", json=modified).json()["plan"]
        self.assertEqual(manual["base_version_id"], selected["id"])
        self.assertEqual(manual["origin"]["root_candidate_plan_id"], selected["id"])
        self.assertEqual(manual["origin"]["source"], "manual")
        self.assertIn("修改字幕", manual["change_summary"])
        revise = self.client.post(f"/api/projects/{self.project['id']}/revise",
                                  json={"brief": "只改标题", "base_plan_id": self.base["id"]}).json()
        revision = self.wait(revise["job_id"])
        self.assertEqual(revision["status"], "succeeded", revision)
        revised = revision["result"]["plan"]
        self.assertEqual(revised["base_version_id"], self.base["id"])
        self.assertEqual(revised["origin"]["source"], "ai_revision")
        self.assertNotIn("batch_id", revised["origin"])
        self.assertEqual(self.client.post(endpoint, json={"event_type": "rejected_all"}).status_code, 400)
        self.assertEqual(db.get("projects", self.project["id"])["plan_id"], revised["id"])
        decision_view = self.client.get(f"/api/projects/{self.project['id']}/decisions").json()
        self.assertEqual(decision_view["batches"][0]["decision_status"], "selected")
        self.assertTrue(any(item["plan_id"] == manual["id"] and item["base_plan_id"] == selected["id"] for item in decision_view["revisions"]))
        versions = [p["version"] for p in db.records("plans")]
        self.assertEqual(len(versions), len(set(versions)))

    def test_duplicate_failure_and_restart_preserve_current_and_evidence(self):
        self.provider.create_plan = lambda *args, **kwargs: self.plan([0, 1, 2], "仅改名字")
        started = self.start()
        job = self.wait(started["job_id"])
        self.assertEqual(job["status"], "failed", job)
        batch = self.client.get(f"/api/candidate-batches/{started['batch_id']}").json()["batch"]
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(len(batch["plan_ids"]), 1)
        self.assertEqual(db.get("projects", self.project["id"])["plan_id"], self.base["id"])
        self.assertEqual(self.client.post(f"/api/candidate-batches/{batch['id']}/events",
                                         json={"event_type": "selected", "plan_id": batch["plan_ids"][0]}).status_code, 400)
        fake_job = db.create("jobs", {"kind": "candidates", "status": "running", "payload": {"project_id": self.project["id"]},
                                     "progress": 1, "provider_state": {}})
        interrupted = db.create("candidate_batches", {"project_id": self.project["id"], "status": "running",
                                                       "job_id": fake_job["id"], "plan_ids": []})
        db.mark_interrupted()
        decisions.reconcile_batches()
        self.assertEqual(db.get("candidate_batches", interrupted["id"])["status"], "failed")
        orphan = db.create("candidate_batches", {"project_id": self.project["id"], "status": "queued", "job_id": None, "plan_ids": []})
        decisions.reconcile_batches(startup=True)
        self.assertEqual(db.get("candidate_batches", orphan["id"])["status"], "failed")
        self.assertIsNotNone(db.get("jobs", started["job_id"]))

    def test_missing_config_and_invalid_case_fail_without_fake_success(self):
        response = self.client.post(f"/api/projects/{self.project['id']}/candidates",
                                    json={"count": 3, "reference_case_ids": ["missing"]})
        self.assertEqual(response.status_code, 400)
        def missing_config(*args, **kwargs):
            raise ConfigurationError("请配置普通剪辑模型")
        self.provider.create_plan = missing_config
        started = self.start()
        job = self.wait(started["job_id"])
        self.assertEqual(job["status"], "needs_configuration")
        batch = self.client.get(f"/api/candidate-batches/{started['batch_id']}").json()["batch"]
        self.assertEqual(batch["status"], "needs_configuration")
        self.assertEqual(batch["plan_ids"], [])
        response = self.client.post("/api/projects", json={"brief": "带本地案例引用的新作品",
                                     "asset_ids": [self.assets[0]["id"]], "ratio": "9:16", "duration": 3,
                                     "reference_case_ids": [self.case["id"]], "platform": "视频号", "account": "合成账号"})
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()
        self.wait(created["job_id"])
        project = db.get("projects", created["project_id"])
        self.assertEqual(project["platform"], "channels")
        self.assertEqual(project["reference_case_ids"], [self.case["id"]])
        self.assertEqual(project["reference_case_snapshot"][0]["metrics"], {"plays": 12})
        self.assertNotIn(self.case["title"], json.dumps(project, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
