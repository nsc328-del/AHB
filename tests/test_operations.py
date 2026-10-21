"""Operations API contracts with disposable SQLite and synthetic provider receipts.

The API, background executor, structured-output validator and usage ledger are real.
Only public-feed retrieval and provider HTTP are replaced; no model/network calls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
import operations
import operations_hotspots
import providers
import storage as db
import usage
import workflow


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="operations-contract-")
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.requests = []
        self.mode = "valid"
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.patches = [
            patch.object(db, "DATA", Path(self.directory.name)),
            patch.object(workflow, "EXECUTOR", self.executor),
            patch.object(operations, "today", return_value="2026-10-21"),
            patch.object(providers, "get_config", return_value={"deepseek_configured": True}),
            patch.object(providers, "_request", side_effect=self.provider_response),
            patch.object(operations_hotspots, "collect_hotspots", side_effect=self.hotspots),
        ]
        for item in self.patches:
            item.start()
        self.collector = operations_hotspots.collect_hotspots
        db.initialize()
        self.client = TestClient(app)

    def tearDown(self):
        self.release.set()
        self.executor.shutdown(wait=True)
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def hotspots(self):
        now = datetime.now(timezone.utc)
        return {
            "fetched_at": now.isoformat(),
            "items": [{
                "id": "source-lunch", "title": "写字楼午餐的日常观察",
                "summary": "离线测试来源，仅用于验证引用与日期链路。",
                "url": "https://www.chinanews.com.cn/test/operations-lunch.shtml",
                "source": "离线新闻夹具", "source_id": "soso_douyin",
                "published_at": (now - timedelta(days=1)).isoformat(),
                "fetched_at": now.isoformat(), "time_basis": "aggregator_snapshot", "kind": "platform_hotspot", "rank": 3, "freshness": "recent",
            }],
            "sources": [{"id": "fixture-feed", "name": "离线新闻夹具", "status": "ok", "count": 1}],
        }

    def provider_response(self, provider, method, endpoint, body, **kwargs):
        self.assertEqual((provider, method, endpoint), ("deepseek", "POST", "/responses"))
        model_input = json.loads(body["input"][0]["content"][0]["text"])
        self.requests.append({"body": copy.deepcopy(body), "input": model_input})
        sequence = len(self.requests)
        if self.mode == "block":
            self.blocked.set()
            if not self.release.wait(8):
                raise RuntimeError("Test did not release blocked response")
        if self.mode == "failure":
            usage.record_response(provider, endpoint, body, http_status=503)
            raise providers.ProviderError("离线供应商接口失败")
        request = model_input["request"]
        proposal = {"ideas": [
            {"title": f"第{sequence}批{day[-5:]}镜头{index}", "angle": "跟拍端饭落桌，再近拍夹起一口。",
             "category": "日常一餐", "date": day, "platform": "douyin", "duration": 20,
             "shots": ["手端饭到桌边", "碗落到桌面", "夹起一口饭"],
             "basis": "热点", "source_ids": ["source-lunch"]}
            for day in request["dates"] for index in range(request["per_day"])
        ]}
        if self.mode == "bad_source":
            proposal["ideas"][0]["source_ids"] = ["not-in-evidence"]
        if self.mode == "bad_shape":
            proposal["ideas"][0]["shots"] = ["只有一帧"]
        if self.mode == "repeat_deleted":
            proposal["ideas"][0]["title"] = self.deleted_title
        result = {
            "id": f"offline-operations-response-{sequence}", "model": "offline", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(proposal, ensure_ascii=False)}]}],
            "usage": {"input_tokens": 120, "input_tokens_details": {"cached_tokens": 100},
                      "output_tokens": 30, "total_tokens": 150},
        }
        usage.record_response(provider, endpoint, body, result, http_status=200)
        return result

    def state(self):
        response = self.client.get("/api/operations")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def await_job(self, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.01)
        self.fail(f"Operations job did not finish: {job_id}")

    def generate(self, scope="week", day="2026-10-21"):
        response = self.client.post("/api/operations/generate", json={"scope": scope, "date": day})
        self.assertEqual(response.status_code, 200, response.text)
        return self.await_job(response.json()["job_id"])

    def test_sourced_calendar_usage_and_manual_changes_survive_regeneration(self):
        initial = self.state()
        self.assertEqual(initial["annual_plan"]["status"], "draft")
        refreshed = self.client.post("/api/operations/hotspots/refresh")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(self.await_job(refreshed.json()["job_id"])["status"], "succeeded")
        job = self.generate()
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"]["added"], 5)
        self.assertEqual(self.collector.call_count, 1)
        content = self.requests[0]
        self.assertEqual(content["input"]["task_facts"]["annual_plan"]["status"], "draft")
        self.assertIn("不是品牌已执行的活动", content["body"]["instructions"])
        self.assertEqual(content["input"]["request"]["dates"], [f"2026-10-{day}" for day in range(21, 26)])
        report = self.client.get("/api/usage", params={"scope": "job", "job_id": job["id"]}).json()
        self.assertEqual(report["aggregates"]["total_tokens"], 150)
        self.assertEqual(report["aggregates"]["cache_hit_tokens"], 100)
        self.assertEqual(report["records"][0]["operation"], "operations_content_plan")
        self.assertEqual(job["result"]["request_context"][0]["budget_kind"], "conservative_estimate_not_provider_usage")
        original = self.state()["ideas"]
        first = original[0]
        self.assertEqual(first["sources"][0], content["input"]["hotspot_snapshot"]["items"][0])
        self.assertNotEqual(first["sources"][0]["published_at"], first["sources"][0]["fetched_at"])
        edited = self.client.patch(f"/api/operations/ideas/{first['id']}", json={
            "expected_revision": first["revision"], "title": "人工保留的店员选题", "date": "2026-11-03",
            "platform": "channels", "duration": 35, "shots": ["店员介绍自己的吃法"],
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        saved = edited.json()["idea"]
        conflict = self.client.patch(f"/api/operations/ideas/{first['id']}", json={"expected_revision": first["revision"], "title": "过期页面覆盖"})
        self.assertEqual(conflict.status_code, 409)
        deleted = self.client.delete(f"/api/operations/ideas/{first['id']}", params={"expected_revision": saved["revision"]})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(first["id"], {item["id"] for item in self.state()["ideas"]})
        restore_url = f"/api/operations/ideas/{first['id']}/restore"
        self.assertEqual(self.client.post(restore_url, json={"expected_revision": saved["revision"]}).status_code, 409)
        restored = self.client.post(restore_url, json={"expected_revision": deleted.json()["idea"]["revision"]})
        self.assertEqual(restored.status_code, 200, restored.text)
        saved = restored.json()["idea"]
        tombstone = original[1]
        self.assertEqual(self.client.delete(f"/api/operations/ideas/{tombstone['id']}", params={"expected_revision": 1}).status_code, 200)
        regenerated = self.generate()
        self.assertEqual(regenerated["status"], "succeeded", regenerated)
        self.assertEqual(regenerated["result"]["added"], 2)
        extra = self.generate("day", "2026-11-03")
        self.assertEqual(extra["status"], "succeeded", extra)
        self.assertEqual(extra["result"]["added"], 3)
        self.mode, self.deleted_title = "repeat_deleted", tombstone["title"]
        repeated = self.generate("day", "2026-11-03")
        self.assertEqual(repeated["status"], "succeeded", repeated)
        self.assertEqual((repeated["result"]["added"], repeated["result"]["skipped"]), (2, 1))
        self.assertTrue(any(item.get("deleted") and item["title"] == tombstone["title"]
                            for item in self.requests[-1]["input"]["existing_ideas"]))
        with TestClient(app) as reopened:
            final = reopened.get("/api/operations").json()
        self.assertEqual(next(item for item in final["ideas"] if item["id"] == saved["id"]), saved)
        self.assertNotIn(tombstone["id"], {item["id"] for item in final["ideas"]})
        self.assertNotIn(tombstone["title"], {item["title"] for item in final["ideas"]})
        self.assertEqual(operations.production_context(saved["id"], saved["revision"])["snapshot"], saved)
        stale_production = self.client.post("/api/projects", json={
            "operations_idea_id": saved["id"], "operations_idea_revision": 1, "brief": "过期选题不能提交制作",
        })
        self.assertEqual(stale_production.status_code, 409)
        deleted_production = self.client.post("/api/projects", json={
            "operations_idea_id": tombstone["id"], "operations_idea_revision": 2, "brief": "已删选题不能提交制作",
        })
        self.assertEqual(deleted_production.status_code, 400)
        self.assertEqual(db.records("projects"), [])
        persisted = db.get("operations_states", operations.BRAND_ID)
        self.assertTrue(next(item for item in persisted["ideas"] if item["id"] == tombstone["id"])["deleted"])
        self.assertTrue(any(event["action"] == "idea_edit" and event["result"]["before"]["date"] == "2026-10-21"
                            for event in db.records("operations_events")))

    def test_invalid_model_and_feed_failures_keep_existing_calendar(self):
        first = self.generate()
        self.assertEqual(first["status"], "succeeded", first)
        baseline = self.state()
        for mode in ("bad_source", "bad_shape", "failure"):
            with self.subTest(mode=mode):
                self.mode = mode
                job = self.generate("day")
                self.assertEqual(job["status"], "failed", job)
                self.assertIsNone(job["result"])
                after = self.state()
                self.assertEqual(after["ideas"], baseline["ideas"])
                self.assertEqual(after["last_generation"], baseline["last_generation"])
                receipt = self.client.get("/api/usage", params={"scope": "job", "job_id": job["id"]}).json()
                self.assertEqual(receipt["records_total"], 1)
                self.assertEqual(receipt["aggregates"]["total_tokens"], None if mode == "failure" else 150)
        self.mode = "valid"
        calls = len(self.requests)
        old_snapshot = copy.deepcopy(baseline["hotspots"])
        old_snapshot["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        db.update("operations_states", operations.BRAND_ID, transform=lambda value: {**value, "hotspots": old_snapshot})
        self.collector.side_effect = lambda: {"items": [], "sources": [{"id": "fixture-feed", "status": "failed"}], "fetched_at": db.now()}
        failed = self.generate("day")
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(len(self.requests), calls)
        after = self.state()
        self.assertEqual(after["ideas"], baseline["ideas"])
        self.assertEqual(after["hotspots"]["items"], old_snapshot["items"])
        self.assertEqual(after["hotspots"]["fetched_at"], old_snapshot["fetched_at"])
        self.assertTrue(after["hotspots"]["stale"])
        self.collector.side_effect = RuntimeError("离线采集器失败")
        refresh = self.client.post("/api/operations/hotspots/refresh").json()
        self.assertEqual(self.await_job(refresh["job_id"])["status"], "failed")
        self.assertEqual(self.state()["ideas"], baseline["ideas"])

    def test_context_change_rejects_inflight_result_and_temporal_boundaries(self):
        initial = self.state()
        self.mode = "block"
        receipt = self.client.post("/api/operations/generate", json={"scope": "day", "date": "2026-10-21"})
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertTrue(self.blocked.wait(4), "Provider request never started")
        context = {"expected_revision": initial["revision"], "brand": {"profile": "门店仅拍店员和出餐，不拍顾客。"},
                   "annual_plan": {**initial["annual_plan"], "status": "official"}}
        changed = self.client.put("/api/operations/context", json=context)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["annual_plan"]["status"], "draft")
        self.assertEqual(self.client.put("/api/operations/context", json=context).status_code, 409)
        self.release.set()
        completed = self.await_job(receipt.json()["job_id"])
        self.assertEqual(completed["status"], "failed", completed)
        self.assertIn("品牌资料或年度计划已修改", completed["error"])
        self.assertEqual(self.state()["ideas"], [])
        self.assertIsNone(self.state()["last_generation"])
        count = len(db.records("jobs"))
        for day in ("2026-02-30", "2026-13-01", "2026-1-02", "2027-01-02", "2019-12-31"):
            with self.subTest(invalid_date=day):
                self.assertEqual(self.client.post("/api/operations/generate", json={"scope": "day", "date": day}).status_code, 400)
        self.assertEqual(len(db.records("jobs")), count)
        broken_context = {**context, "expected_revision": changed.json()["revision"], "annual_plan": copy.deepcopy(initial["annual_plan"])}
        broken_context["annual_plan"]["months"][1]["events"] = [{"day": 30, "label": "非法日期"}]
        self.assertEqual(self.client.put("/api/operations/context", json=broken_context).status_code, 400)
        self.assertEqual(self.state()["revision"], changed.json()["revision"])
        future_snapshot = copy.deepcopy(self.state()["hotspots"])
        future_snapshot["fetched_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        db.update("operations_states", operations.BRAND_ID, transform=lambda value: {**value, "hotspots": future_snapshot})
        self.mode = "valid"
        calls = self.collector.call_count
        self.assertEqual(self.generate("day")["status"], "succeeded")
        self.assertEqual(self.collector.call_count, calls + 1)



    def test_restaurant_clues_exclude_news_and_keep_empty_success(self):
        titles = ["冬天第一串车厘子糖葫芦", "瞬移滑镜慢动作挑战", "进入面要多吃面要多见时代",
                  "黄金消费降温", "饭撒摇手势舞", "娃圈秋日穿搭大赏", "食品中毒事故", "中国经济新动能孕育新机遇"]
        rows = [{"id": str(i), "source_id": "soso_douyin", "kind": "platform_hotspot",
                 "title": title, "rank": i+1, "freshness": "recent"} for i, title in enumerate(titles)]
        # The same restaurant word in an industry article must not enter the trend feed.
        rows.append({**rows[0], "id": "industry", "kind": "industry_news", "source_id": "chinahotel"})
        kept = operations_hotspots.select_trends(rows)
        self.assertEqual([x["title"] for x in kept], titles[:3])
        self.assertEqual([x["content_category"] for x in kept], ["吃什么", "怎么拍", "吃什么"])
        self.assertEqual([x["rank"] for x in kept], [1, 2, 3])
        self.collector.side_effect = lambda: {"items": [], "sources": [{"id": "soso_douyin", "status": "ok", "count": 5}], "fetched_at": db.now()}
        refresh = self.client.post("/api/operations/hotspots/refresh").json()
        self.assertEqual(self.await_job(refresh["job_id"])["status"], "succeeded")
        self.assertEqual(self.state()["hotspots"]["items"], [])
        self.assertTrue(operations._fresh(self.state()["hotspots"]))
        # No unrelated stories are resurrected to fill the cards or the model input.
        self.assertEqual(operations_hotspots.select_trends(rows[3:8]), [])

    def test_annual_priorities_survive_context_edit(self):
        state = self.state()
        plan = state["annual_plan"]
        self.assertIn("开学", plan["months"][8]["theme"])
        self.assertIn("校园", plan["months"][8]["focus"])
        self.assertIn("618", plan["months"][5]["theme"])
        self.assertIn("双11", plan["months"][10]["theme"])
        self.assertIn({"day":25, "label":"中秋"}, plan["months"][8]["events"])
        plan["months"][10]["focus"] = "本年不参加双11，优先写字楼午餐复购"
        saved = self.client.put("/api/operations/context", json={"brand":state["brand"], "annual_plan":plan, "expected_revision":state["revision"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.state()["annual_plan"], plan)


if __name__ == "__main__":
    unittest.main()
