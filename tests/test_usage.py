"""Usage receipts and scope isolation, with synthetic HTTP and disposable SQLite only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

import providers
import storage as db
import usage
import workflow
from app import app


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="usage-contract-")
        self.addCleanup(self.directory.cleanup)
        self.data_patch = patch.object(db, "DATA", Path(self.directory.name))
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        db.initialize()
        self.config_patch = patch.object(providers, "_config", return_value={
            "deepseek_api_key": "synthetic-test-key", "deepseek_base_url": "https://api.deepseek.com",
            "minimax_api_key": "synthetic-test-key", "minimax_base_url": "https://api.minimax.cn"})
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.handler = lambda request: httpx.Response(200, json={})
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: self.handler(request))
        self.client_patch = patch.object(providers.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, trust_env=False, **kwargs))
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def test_real_response_fields_and_failure_receipts(self):
        payload = {"id": "response-1", "model": "deepseek-flash", "status": "completed",
                   "usage": {"input_tokens": 120, "input_tokens_details": {"cached_tokens": 100},
                             "output_tokens": 30, "total_tokens": 150}}
        self.handler = lambda request: httpx.Response(200, json=payload)
        with usage.context(job_id="j1", project_id="p1", asset_id="a1", batch_id="b1"):
            providers._request("deepseek", "POST", "/responses", {"model": "deepseek-flash"})
        item = usage.summary("asset", asset_id="a1")["records"][0]
        self.assertEqual((item["total_tokens"], item["cache_miss_tokens"]), (150, 20))
        self.assertEqual(item["derived_fields"], ["cache_miss_tokens"])
        # Usage is persisted before either HTTP errors or structured-output errors.
        self.handler = lambda request: httpx.Response(400, json={**payload, "id": "failed-http"})
        with usage.context(job_id="j2", project_id="p1"):
            with self.assertRaises(providers.ProviderError):
                providers._request("deepseek", "POST", "/responses", {})
        self.handler = lambda request: httpx.Response(200, json={**payload, "id": "incomplete", "status": "incomplete"})
        with usage.context(job_id="j3", project_id="p1"):
            with self.assertRaises(providers.ProviderError):
                providers._json_response("edit_plan", {"type": "object"}, "synthetic", "synthetic")
        result = usage.summary("project", project_id="p1")
        self.assertEqual(result["aggregates"]["total_tokens"], 450)
        self.assertEqual({record["status"] for record in result["records"]}, {"completed", "failed", "incomplete"})
        self.assertNotIn("synthetic-test-key", json.dumps(result))
        # An invalid status type must not prevent saving an otherwise valid receipt.
        self.handler = lambda request: httpx.Response(200, json={**payload, "id": "bad-status", "status": []})
        with usage.context(job_id="j4"):
            with self.assertRaises(providers.ProviderError):
                providers._json_response("edit_plan", {"type": "object"}, "synthetic", "synthetic")
        self.assertEqual(usage.summary("job", job_id="j4")["aggregates"]["total_tokens"], 150)

    def test_missing_partial_legacy_and_scope_aggregation(self):
        old = db.create("jobs", {"id": "legacy", "kind": "analysis", "created_at": "2020-01-01T00:00:00+00:00", "payload": {"asset_id": "old"}})
        before = usage.summary("asset", asset_id="old")
        self.assertEqual(before["coverage"]["scope_status"], "unrecorded")
        self.assertIsNone(before["aggregates"]["total_tokens"])
        self.assertEqual(usage.summary("asset", asset_id="new")["coverage"]["scope_status"], "empty")
        with usage.context(job_id="j1", project_id="p1"):
            usage.record_response("deepseek", "/responses", response={"id": "empty", "status": "completed"})
            usage.record_response("deepseek", "/responses", response={"id": "full", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}})
            usage.record_response("deepseek", "/responses", response={"id": "no-cache", "usage": {"input_tokens": 900, "output_tokens": 90, "total_tokens": 990}})
            usage.record_response("deepseek", "/responses", response={"id": "no-total", "usage": {"input_tokens": 30, "output_tokens": 20}})
        result = usage.summary("project", project_id="p1", limit=1)
        self.assertEqual(result["aggregates"]["total_tokens"], 1100)
        self.assertEqual(result["aggregates"]["cache_hit_rate"], .8)
        self.assertEqual(result["coverage"]["token_unknown_calls"], 1)
        self.assertEqual(result["coverage"]["cache_reported_calls"], 1)
        self.assertEqual(result["coverage"]["token_total_unknown_calls"], 2)
        self.assertEqual(result["coverage"]["token_partial_calls"], 1)
        self.assertEqual(result["coverage"]["metric_reported_calls"]["total_tokens"], 2)
        self.assertEqual(result["records_total"], 4)
        self.assertTrue(result["has_more"])
        self.assertIsNone(usage.summary("project", project_id="another")["aggregates"]["total_tokens"])
        self.assertEqual(usage.normalize({"input_tokens": True, "total_tokens": -4, "output_tokens": float("nan")})["total_tokens"], None)
        self.assertIsNone(usage.normalize({"input_tokens": 4, "input_tokens_details": {"cached_tokens": 5}})["cache_hit_tokens"])

    def test_h3_polling_is_one_receipt_and_generated_asset_inherits_it(self):
        task = {"id": "h3-task", "model": "MiniMax-H3", "status": "succeeded", "usage": {
            "total_seconds": 5, "input_seconds": 0, "output_seconds": 5, "input_image_count": 1,
            "total_tokens": 273890, "prompt_tokens": 13500, "completion_tokens": 260390}}
        with usage.context(job_id="generation-job", operation="generation"):
            usage.record_response("minimax", "/v2/video_generation", response={"task_id": "h3-task"})
            for _ in range(3):
                usage.record_response("minimax", "/v2/query/video_generation/h3-task", response={"task": task})
            usage.record_response("minimax", "/v2/query/video_generation/h3-task", transport_error=True)
        db.create("assets", {"id": "generated", "generation_id": "generation-job"})
        result = usage.summary("asset", asset_id="generated")
        self.assertEqual(result["coverage"]["recorded_calls"], 1)
        self.assertEqual(result["aggregates"]["total_tokens"], 273890)
        self.assertEqual(result["aggregates"]["video_seconds"], 5)
        self.assertIsNone(result["aggregates"]["cache_hit_rate"])
        self.assertEqual(result["records"][0]["status"], "succeeded")
        with usage.context(job_id="seconds-only"):
            usage.record_response("minimax", "/v2/query/video_generation/seconds", response={"task": {"id": "seconds", "status": "succeeded", "usage": {"total_seconds": 4}}})
        seconds = usage.summary("job", job_id="seconds-only")
        self.assertIsNone(seconds["aggregates"]["total_tokens"])
        self.assertEqual(seconds["coverage"]["token_unknown_calls"], 0)
        self.assertEqual(seconds["coverage"]["token_total_unknown_calls"], 1)
        self.assertEqual(seconds["coverage"]["seconds_only_calls"], 1)

    def test_animation_scope_includes_failed_and_retry_receipts_without_other_projects(self):
        rows = [
            ("a-plan", "animation-a", "animation_plan", "succeeded", 100),
            ("a-failed", "animation-a", "animation_render", "failed", 50),
            ("a-retry", "animation-a", "animation_render", "succeeded", 25),
            ("b-render", "animation-b", "animation_render", "succeeded", 900),
            ("other-job", None, "plan", "succeeded", 700),
        ]
        for ident, animation_id, kind, status, tokens in rows:
            job = db.create("jobs", {"id": ident, "kind": kind, "status": status,
                "payload": {"project_id": "same-workspace", **({"animation_id": animation_id} if animation_id else {})}})
            with usage.job_context(job):
                usage.record_response("deepseek", "/responses", response={"id": "receipt-" + ident,
                    "status": "incomplete" if status == "failed" else "completed",
                    "usage": {"input_tokens": tokens - 1, "output_tokens": 1, "total_tokens": tokens}})
        db.create("jobs", {"id": "legacy-a", "kind": "animation_plan", "created_at": "2020-01-01T00:00:00+00:00",
                           "payload": {"animation_id": "animation-a"}})
        db.create("jobs", {"id": "legacy-b", "kind": "animation_plan", "created_at": "2020-01-01T00:00:00+00:00",
                           "payload": {"animation_id": "animation-b"}})
        self.handler = lambda request: self.fail("Animation usage reads must not call a provider")
        client = TestClient(app)
        response = client.get("/api/usage", params={"scope": "animation", "animation_id": "animation-a", "limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["filters"], {"animation_id": "animation-a"})
        self.assertEqual(result["aggregates"]["total_tokens"], 175)
        self.assertEqual(result["records_total"], 3)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["coverage"]["legacy_jobs_count"], 1)
        full = usage.summary("animation", animation_id="animation-a")
        self.assertEqual({record["job_id"] for record in full["records"]}, {"a-plan", "a-failed", "a-retry"})
        self.assertIn("incomplete", {record["status"] for record in full["records"]})
        self.assertEqual(usage.summary("animation", animation_id="animation-b")["aggregates"]["total_tokens"], 900)
        self.assertEqual(usage.summary("animation", animation_id="missing")["coverage"]["scope_status"], "empty")
        self.assertEqual(client.get("/api/usage", params={"scope": "animation"}).status_code, 400)

    def test_workflow_context_is_thread_local_and_scopes_validate(self):
        def operation(job_id):
            for number in range(2):
                with usage.context(**({"asset_id": "asset-" + job_id} if number == 0 else {})):
                    usage.record_response("deepseek", "/responses", response={"id": job_id + str(number), "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}})
            return {"ok": True}
        jobs = [db.create("jobs", {"kind": "candidates", "status": "queued", "payload": {"project_id": f"p{i}", "batch_id": f"b{i}"}}) for i in range(4)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(workflow._execute, job["id"], operation) for job in jobs]
            for future in futures:
                future.result()
        for i, job in enumerate(jobs):
            result = usage.summary("project", project_id=f"p{i}")
            self.assertEqual(result["aggregates"]["total_tokens"], 6)
            self.assertEqual({record["batch_id"] for record in result["records"]}, {f"b{i}"})
            self.assertEqual({record["job_id"] for record in result["records"]}, {job["id"]})
            self.assertEqual(usage.summary("asset", asset_id="asset-" + job["id"])["aggregates"]["total_tokens"], 3)
        self.assertIsNone(usage.CONTEXT.get())
        # The read API never polls a provider and accepts a bounded recent-record list.
        self.handler = lambda request: self.fail("Read usage must not access network")
        client = TestClient(app)
        self.assertEqual(client.get("/api/usage", params={"scope": "project", "project_id": "p1"}).json()["aggregates"]["total_tokens"], 6)
        self.assertEqual(client.get("/api/usage", params={"scope": "job"}).status_code, 400)
        self.assertEqual(client.get("/api/usage", params={"limit": 101}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
