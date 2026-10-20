"""Local API contracts with synthetic media and explicitly mocked model providers.

These tests never call a model API and do not claim model quality or paid generation
acceptance. Upload, SQLite, media probing, FFmpeg rendering and downloads are real.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from fastapi.testclient import TestClient
import storage as db
import workflow
from app import app


class ProviderError(RuntimeError):
    pass


class ConfigurationError(ProviderError):
    pass


class SubmissionUnknown(ProviderError):
    pass


class APIIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="video-studio-api-")
        self.root = Path(self.tmp.name)
        self.original_data = db.DATA
        db.DATA = self.root / "data"
        self.environment = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA), "VIDEO_STUDIO_CONFIG": str(self.root / "private-config.json")})
        self.environment.start()
        self.provider = types.SimpleNamespace(
            ProviderError=ProviderError, ConfigurationError=ConfigurationError, SubmissionUnknown=SubmissionUnknown,
            get_config=lambda: {"configured": False}, save_config=lambda payload: {"configured": bool(payload)},
            analyze_asset=lambda asset, frames, reference=False: {"summary": "人工构造的契约测试返回值", "tags": ["测试"], "segments": [{"start": 0, "end": asset["duration"], "description": "合成素材", "tags": ["测试"], "evidence_times": [f["time"] for f in frames]}]},
            create_plan=self.plan_response,
            create_generation=lambda params, assets: {"task_id": "test-provider-task"},
            query_generation=lambda task_id: {"status": "failed", "error": "测试供应商明确失败"},
            create_generation_storyboard=lambda params, assets: {"title": "测试分镜", "shots": [{"id": "shot-1", "duration": 4, "prompt": "synthetic test shot"}]},
        )
        self.mock_provider = patch.object(workflow, "providers", return_value=self.provider)
        self.mock_provider.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        deadline = time.monotonic() + 10
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        self.client.__exit__(None, None, None)
        self.mock_provider.stop()
        self.environment.stop()
        db.DATA = self.original_data
        self.tmp.cleanup()

    def plan_response(self, brief, assets, options, reference=None, previous=None):
        return {"title": "改后标题" if previous else "测试作品", "summary": "测试计划", "clips": [{"asset_id": assets[0]["id"], "start": 0, "end": 1, "duration": 1, "label": "合成图片", "caption": "", "role": "opening", "transition": "cut"}]}

    def upload_image(self):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480), (35, 84, 170)).save(buffer, format="PNG")
        response = self.client.post("/api/assets", files={"files": ("测试图片.png", buffer.getvalue(), "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["kind"], "image")
        self.assertNotIn("path", asset)
        self.assertEqual(self.client.get(asset["url"]).status_code, 200)
        return asset

    def await_job(self, job_id, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.08)
        self.fail(f"job {job_id} did not finish")

    def test_upload_plan_versions_real_render_and_revision(self):
        asset = self.upload_image()
        original_hash = asset["sha256"]
        response = self.client.post("/api/projects", json={"brief": "制作测试作品", "asset_ids": [asset["id"]], "template_id": "free", "duration": 1, "ratio": "9:16", "auto_render": True})
        self.assertEqual(response.status_code, 200, response.text)
        first = self.await_job(response.json()["job_id"])
        self.assertEqual(first["status"], "succeeded", first)
        project_id = first["result"]["project_id"]
        plan1 = first["result"]["plan"]
        self.assertEqual(plan1["duration"], 1)
        self.assertEqual(plan1["clips"][0]["timeline_end"], 1)
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}").json()["analysis_status"], "succeeded")
        unchanged = self.client.put(f"/api/projects/{project_id}/plan", json=plan1).json()["plan"]
        self.assertEqual(unchanged["id"], plan1["id"])
        job_id = first["result"]["render_job_id"]
        rendered = self.await_job(job_id)
        self.assertEqual(rendered["status"], "succeeded", rendered)
        response = self.client.get(rendered["result"]["url"])
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.content), 1000)
        old_result_hash = rendered["result"]["sha256"]
        revise_id = self.client.post(f"/api/projects/{project_id}/revise", json={"brief": "只修改作品标题"}).json()["job_id"]
        revised = self.await_job(revise_id)
        self.assertEqual(revised["status"], "succeeded", revised)
        plan2 = revised["result"]["plan"]
        self.assertEqual(plan2["base_version_id"], plan1["id"])
        project = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(len(project["versions"]), 2)
        self.assertEqual(project["renders"][0]["sha256"], old_result_hash)
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}").json()["sha256"], original_hash)
        invalid = dict(plan2, clips=[dict(plan2["clips"][0], asset_id="missing")])
        self.assertEqual(self.client.put(f"/api/projects/{project_id}/plan", json=invalid).status_code, 400)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json()["plan_id"], plan2["id"])
        replacement = self.upload_image()
        changed = dict(plan2, clips=[dict(plan2["clips"][0], asset_id=replacement["id"])])
        replaced = self.client.put(f"/api/projects/{project_id}/plan", json=changed)
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertIn(replacement["id"], self.client.get(f"/api/projects/{project_id}").json()["asset_ids"])
        db.update("projects", project_id, {"reference_id": rendered["result"]["asset_id"]})
        forbidden = dict(plan2, clips=[dict(plan2["clips"][0], asset_id=rendered["result"]["asset_id"])])
        denied = self.client.put(f"/api/projects/{project_id}/plan", json=forbidden)
        self.assertEqual(denied.status_code, 400)
        self.assertIn("参考样片", denied.json()["detail"])

    def test_missing_configuration_is_visible_and_does_not_fake_analysis(self):
        def unconfigured(*args, **kwargs):
            raise ConfigurationError("请配置素材视觉分析模型")
        self.provider.analyze_asset = unconfigured
        asset = self.upload_image()
        result = self.client.post(f"/api/assets/{asset['id']}/analyze").json()
        job = self.await_job(result["job_id"])
        self.assertEqual(job["status"], "needs_configuration")
        stored = self.client.get(f"/api/assets/{asset['id']}").json()
        self.assertEqual(stored["analysis_status"], "needs_configuration")
        self.assertIsNone(stored["analysis"])
        self.assertNotIn("payload", job)

    def test_restart_and_resume_only_queries_accepted_task(self):
        calls = {"submit": 0, "query": 0}
        def unknown(*args, **kwargs):
            calls["submit"] += 1
            raise SubmissionUnknown("提交结果未知")
        self.provider.create_generation = unknown
        job_id = self.client.post("/api/generations", json={"mode": "T2VA", "prompt": "合成测试", "duration": 4}).json()["job_id"]
        unknown_job = self.await_job(job_id)
        self.assertEqual(unknown_job["status"], "unknown")
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/resume").status_code, 400)
        self.assertEqual(calls["submit"], 1)
        accepted = db.create("jobs", {"kind": "generation", "status": "running", "progress": 10, "message": "测试", "result": None, "error": None, "payload": {}, "provider_state": {"task_id": "already-paid", "submitting": False}})
        db.mark_interrupted()
        self.assertEqual(db.get("jobs", accepted["id"])["status"], "unknown")
        def query(task_id):
            self.assertEqual(task_id, "already-paid")
            calls["query"] += 1
            return {"status": "failed", "error": "供应商明确失败"}
        self.provider.query_generation = query
        response = self.client.post(f"/api/jobs/{accepted['id']}/resume")
        self.assertEqual(response.status_code, 200)
        finished = self.await_job(accepted["id"])
        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(calls, {"submit": 1, "query": 1})
        self.assertEqual(self.client.post(f"/api/jobs/{accepted['id']}/resume").status_code, 400)
        for error, expected in [(ValueError("参数错误"), "failed"), (ConfigurationError("模型未配置"), "needs_configuration"), (ProviderError("供应商明确拒绝"), "failed")]:
            def rejected(*args, **kwargs):
                raise error
            self.provider.create_generation = rejected
            rejected_id = self.client.post("/api/generations", json={"prompt": "测试"}).json()["job_id"]
            rejected_job = self.await_job(rejected_id)
            self.assertEqual(rejected_job["status"], expected)
            self.assertFalse(db.get("jobs", rejected_id)["provider_state"].get("submitting"))

    def test_generation_storyboard_and_selected_interval_validation(self):
        result = self.client.post("/api/generation-plans", json={"prompt": "合成测试", "skill_id": "h3-prompt-writing", "duration": 4}).json()
        job = self.await_job(result["job_id"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"]["storyboard"]["shots"][0]["duration"], 4)
        self.assertEqual(self.client.get(job["result"]["url"]).status_code, 200)
        assets = [{"id": "video", "kind": "video", "duration": 10, "has_audio": False}]
        plan = {"ratio": "9:16", "clips": [{"asset_id": "video", "start": 1, "end": 4, "duration": 3}]}
        with self.assertRaisesRegex(ValueError, "选定"):
            workflow.validate_plan(plan, assets, [{"asset_id": "video", "start": 2, "end": 4}])
        checked = workflow.validate_plan(plan, assets, [{"asset_id": "video", "start": 1, "end": 4}])
        self.assertEqual(checked["duration"], 3)
        unselected_asset = [{"id": "whole", "kind": "video", "duration": 10, "has_audio": False}]
        whole_plan = {"ratio": "9:16", "clips": [{"asset_id": "whole", "start": 1, "end": 4, "duration": 3}]}
        self.assertEqual(workflow.validate_plan(whole_plan, unselected_asset, [{"asset_id": "video", "start": 1, "end": 4}])["duration"], 3)
        self.assertEqual(self.client.get("/api/media/../studio.sqlite").status_code, 404)

    def test_local_transcription_job_success_and_failure_preserve_visual_analysis(self):
        import transcription
        asset = self.upload_image()
        self.assertEqual(self.await_job(asset["analysis_job_id"])["status"], "succeeded")
        # The ASR contract is mocked here; a separate real local speech sample verifies Whisper.
        db.update("assets", asset["id"], {"has_audio": True, "audio_status": "not_transcribed", "analysis": {"summary": "已有画面分析", "tags": ["蓝色"]}, "analysis_status": "succeeded"})
        result = {"text": "本机测试转录", "segments": [{"start": 0, "end": 1, "text": "本机测试转录", "words": []}], "language": "zh", "model": "base", "engine": "faster-whisper", "local": True, "duration": 1}
        with patch.object(transcription, "transcribe", return_value=result):
            job_id = self.client.post(f"/api/assets/{asset['id']}/transcribe", json={"model": "base"}).json()["job_id"]
            self.assertEqual(self.await_job(job_id)["status"], "succeeded")
        updated = self.client.get(f"/api/assets/{asset['id']}").json()
        self.assertEqual(updated["audio_status"], "succeeded")
        self.assertEqual(updated["transcript"]["text"], "本机测试转录")
        self.assertEqual(updated["analysis"]["summary"], "已有画面分析")
        with patch.object(transcription, "transcribe", side_effect=RuntimeError("公开模型暂时下载失败")):
            job_id = self.client.post(f"/api/assets/{asset['id']}/transcribe", json={"model": "small"}).json()["job_id"]
            self.assertEqual(self.await_job(job_id)["status"], "failed")
        failed = self.client.get(f"/api/assets/{asset['id']}").json()
        self.assertEqual(failed["audio_status"], "failed")
        self.assertEqual(failed["analysis_status"], "succeeded")
        self.assertEqual(failed["analysis"]["summary"], "已有画面分析")
        self.assertEqual(failed["transcript"]["text"], "本机测试转录", "旧转录版本不能因新一次失败而丢失")

    def test_retry_failed_initial_plan_preserves_project_and_rejects_duplicates(self):
        original_analyzer = self.provider.analyze_asset
        def missing(*args, **kwargs):
            raise ConfigurationError("请配置模型")
        self.provider.analyze_asset = missing
        asset = self.upload_image()
        submitted = self.client.post("/api/projects", json={"brief": "恢复原项目", "asset_ids": [asset["id"]], "duration": 12, "ratio": "16:9", "template_id": "story"}).json()
        first = self.await_job(submitted["job_id"])
        self.assertEqual(first["status"], "needs_configuration")
        project_id = submitted["project_id"]
        failed_project = self.client.get(f"/api/projects/{project_id}").json()
        self.assertIsNone(failed_project["plan_id"])
        self.assertTrue(failed_project["can_retry_plan"])
        self.provider.analyze_asset = original_analyzer
        started, release = threading.Event(), threading.Event()
        captured = {}
        def wait_then_plan(brief, assets, options, reference=None, previous=None):
            captured.update(brief=brief, asset_ids=[item["id"] for item in assets], options=options)
            started.set()
            release.wait(5)
            return self.plan_response(brief, assets, options, reference, previous)
        self.provider.create_plan = wait_then_plan
        retried = self.client.post(f"/api/projects/{project_id}/retry-plan")
        self.assertEqual(retried.status_code, 200, retried.text)
        retry_id = retried.json()["job_id"]
        self.assertTrue(started.wait(5))
        try:
            duplicate = self.client.post(f"/api/projects/{project_id}/retry-plan")
            self.assertEqual(duplicate.status_code, 400)
            self.assertIn("正在处理", duplicate.json()["detail"])
            active = self.client.get(f"/api/projects/{project_id}").json()
            self.assertFalse(active["can_retry_plan"])
            self.assertEqual(active["active_plan_job_id"], retry_id)
        finally:
            release.set()
        completed = self.await_job(retry_id)
        self.assertEqual(completed["status"], "succeeded", completed)
        self.assertEqual(completed["result"]["project_id"], project_id)
        self.assertEqual(captured["brief"], "恢复原项目")
        self.assertEqual(captured["asset_ids"], [asset["id"]])
        self.assertEqual(captured["options"]["ratio"], "16:9")
        self.assertEqual(captured["options"]["duration"], 12)
        self.assertEqual(captured["options"]["template_id"], "story")
        self.assertEqual(len(self.client.get("/api/projects").json()["projects"]), 1)
        final_project = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(len(final_project["versions"]), 1)
        self.assertEqual(len([item for item in final_project["messages"] if item["role"] == "user"]), 1)
        denied = self.client.post(f"/api/projects/{project_id}/retry-plan")
        self.assertEqual(denied.status_code, 400)
        self.assertIn("已有成功方案", denied.json()["detail"])

    def test_revision_uses_selected_version_and_rejects_foreign_or_stale_head(self):
        asset = self.upload_image()
        submitted = self.client.post("/api/projects", json={"brief": "版本测试", "asset_ids": [asset["id"]], "duration": 1, "ratio": "9:16"}).json()
        first = self.await_job(submitted["job_id"])
        self.assertEqual(first["status"], "succeeded", first)
        project_id = submitted["project_id"]
        v1 = first["result"]["plan"]
        v2 = self.client.put(f"/api/projects/{project_id}/plan", json={"title": "第二版", "ratio": "16:9", "clips": [dict(v1["clips"][0], caption="仅第二版拥有的字幕")]}).json()["plan"]
        seen = {}
        def selected_plan(brief, assets, options, reference=None, previous=None):
            seen.update(previous=previous, options=options)
            return {"title": "基于第一版修改", "summary": "从所选版本修改", "clips": previous["clips"]}
        self.provider.create_plan = selected_plan
        job_id = self.client.post(f"/api/projects/{project_id}/revise", json={"brief": "按当前看到的第一版改标题", "base_plan_id": v1["id"]}).json()["job_id"]
        revised = self.await_job(job_id)
        self.assertEqual(revised["status"], "succeeded", revised)
        v3 = revised["result"]["plan"]
        self.assertEqual(seen["previous"]["id"], v1["id"])
        self.assertEqual(seen["options"]["ratio"], "9:16")
        self.assertEqual(v3["base_version_id"], v1["id"])
        self.assertEqual(v3["version"], v2["version"] + 1)
        self.assertEqual(v3["clips"][0]["caption"], "")
        foreign = db.create("plans", {"project_id": "different-project", "version": 1, "clips": v1["clips"]})
        rejected = self.client.post(f"/api/projects/{project_id}/revise", json={"brief": "不能跨项目", "base_plan_id": foreign["id"]})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("不属于当前作品", rejected.json()["detail"])
        started, release = threading.Event(), threading.Event()
        def delayed_plan(brief, assets, options, reference=None, previous=None):
            started.set()
            release.wait(5)
            return {"title": "迟到的模型结果", "summary": "测试", "clips": previous["clips"]}
        self.provider.create_plan = delayed_plan
        pending = self.client.post(f"/api/projects/{project_id}/revise", json={"brief": "等待时人工另改", "base_plan_id": v3["id"]}).json()["job_id"]
        self.assertTrue(started.wait(5))
        try:
            manual = self.client.put(f"/api/projects/{project_id}/plan", json={"title": "后续人工编辑"}).json()["plan"]
        finally:
            release.set()
        stale = self.await_job(pending)
        self.assertEqual(stale["status"], "failed")
        self.assertIn("已发生修改", stale["error"])
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json()["plan_id"], manual["id"])


if __name__ == "__main__":
    unittest.main()
