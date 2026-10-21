"""Presentation is evidence-led, versioned and independently resumable."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import providers
import reference_presentation as presentation
import reference_presentation_repair as correction
import reference_repair
import storage as db
import workflow
from app import app


class PresentationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(db, "DATA", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.initialize()
        frames = []
        for time in [.1, .4, .7, .9, 1.1, 1.4, 1.7, 1.9]:
            path = db.DATA / "frames" / f"{time}.jpg"
            path.write_bytes(b"image-evidence")
            frames.append({"time": time, "url": db.media_url(path)})
        segments = [{"start": i, "end": i+1, "description": "具体画面",
                     "role": "旧成果展示", "motion": "旧不确定动作", "evidence_times": [i+.1],
                     "visual_verification": {"structured_motion": {"image_direction": "left", "view_change": "horizontal_sweep"}}}
                    for i in range(2)]
        self.base = {"analysis_id": "base", "reference_analysis": True, "summary": "旧开场建立",
                     "shots": [{"id": f"shot-{i+1:04}", "start": i, "end": i+1} for i in range(2)],
                     "evidence_frames": frames, "content_segments": segments, "segments": copy.deepcopy(segments),
                     "structure": [{"role": "成果展示"}], "shot_boundaries": [0, 1, 2]}
        self.asset = db.create("assets", {"purpose": "reference", "kind": "video", "duration": 2,
                                          "has_audio": False, "analysis": self.base})
        db.create("analyses", {"id": "base", "result": self.base})
        self.job = db.create("jobs", {"kind": "reference_presentation", "status": "running",
                                      "payload": {"asset_id": self.asset["id"], "base_analysis_id": "base"}})

    def response(self, name, schema, system, user, images=None):
        context = json.loads(user)
        self.assertNotIn("开场建立", user)
        self.assertNotIn("成果展示", user)
        self.assertNotIn("旧不确定动作", user)
        self.assertNotIn("analysis_id", user)
        self.assertEqual(len(images), 8)
        return {"kind": "motion_transition", "label": "运动接续", "summary": "两处场景由同向画面运动连接",
                "confidence": "medium", "basis": ["镜头内有连续位移"], "limitations": ["未判定物理摄影机方向"],
                "nodes": [{"first_shot": i+1, "last_shot": i+1, "label": f"场景{i+1}",
                           "description": "可见场景", "evidence_times": shot["frame_times"]}
                          for i, shot in enumerate(context["shots"])],
                "links": [{"kind": "motion_match",
                           "description": "画面位移接续", "evidence_times": [.7, .9, 1.1, 1.4]}]}

    def test_auto_reference_failure_and_stale_failure_status_ownership(self):
        job = db.create("jobs", {"kind": "reference", "status": "queued",
            "payload": {"asset_id": self.asset["id"]}, "provider_state": {}})
        with patch.object(correction, "response", side_effect=providers.ProviderError("离线模拟失败")), \
             patch("generation_batches.on_job_finished"):
            workflow._execute(job["id"], lambda jid: presentation._operation(self.asset["id"], "base", jid))
        failed = db.get("assets", self.asset["id"])
        self.assertEqual(failed["presentation_job_id"], job["id"])
        self.assertEqual(failed["presentation_status"], "failed")
        self.assertEqual(db.get("jobs", job["id"])["status"], "failed")
        db.update("assets", self.asset["id"], {"presentation_job_id": "new-success", "presentation_status": "succeeded"})
        def old_failure(jid):
            raise providers.ProviderError("旧任务迟到失败")
        with patch("generation_batches.on_job_finished"):
            workflow._execute(job["id"], old_failure)
        current = db.get("assets", self.asset["id"])
        self.assertEqual(current["presentation_job_id"], "new-success")
        self.assertEqual(current["presentation_status"], "succeeded")
        # A scheduler rejection also clears queued ownership before any worker
        # has an opportunity to enter _operation.
        class StoppedExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("stopped")
        db.update("jobs", self.job["id"], {"status": "failed"})
        with patch.object(workflow, "ANALYSIS_EXECUTOR", StoppedExecutor()):
            queued = presentation.analyze_presentation(self.asset["id"], "base")
        self.assertEqual(queued["status"], "failed")
        self.assertEqual(db.get("assets", self.asset["id"])["presentation_status"], "failed")

    def test_api_scope_cache_and_immutable_evidence(self):
        with patch.object(presentation.workflow, "enqueue", return_value={"id": "queued"}) as enqueue:
            response = TestClient(app).post(f"/api/references/{self.asset['id']}/presentation", json={"base_analysis_id": "other"})
            self.assertEqual(response.status_code, 400)
            enqueue.assert_not_called()
        with patch.object(providers, "_json_response", side_effect=self.response) as model:
            outcome = presentation._operation(self.asset["id"], "base", self.job["id"])
            head = db.get("assets", self.asset["id"])["analysis"]
            for key in self.base:
                if key != "analysis_id":
                    self.assertEqual(head[key], self.base[key])
            self.assertEqual(db.get("analyses", "base")["result"], self.base)
            repeated = presentation._operation(self.asset["id"], outcome["analysis_id"], self.job["id"])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(repeated["presentation"]["new_provider_calls"], 0)
        self.assertTrue(repeated["presentation"]["cache_hit"])
        self.assertEqual(repeated["presentation"]["nodes"][1]["start"], 1)
        self.assertEqual(repeated["presentation"]["nodes"][1]["shot_ids"], ["shot-0002"])
        self.assertEqual(repeated["presentation"]["nodes"][1]["image_motion"]["direction"], "left")

    def test_invalid_motion_and_late_version_cannot_replace_head(self):
        context, _ = presentation._prepare(self.asset, self.base)
        value = self.response(None, None, None, json.dumps(context), [None]*8)
        value["links"][0]["evidence_times"] = [.9, 1.1]
        with self.assertRaisesRegex(providers.ProviderError, "连续图像"):
            presentation._validate(value, context)
        value["links"][0]["evidence_times"] = [.7, .9, 42]
        with self.assertRaisesRegex(providers.ProviderError, "不相邻"):
            presentation._validate(value, context)
        value = self.response(None, None, None, json.dumps(context), [None]*8)
        value["nodes"][1]["last_shot"] = 3
        with self.assertRaises(providers.ProviderError):
            presentation._validate(value, context)
        value = self.response(None, None, None, json.dumps(context), [None]*8)
        value["nodes"][0]["description"] = "女生迈步出画"
        with self.assertRaisesRegex(providers.ProviderError, "静态场景"):
            presentation._validate(value, context)
        def race(*args, **kwargs):
            response = self.response(*args, **kwargs)
            db.update("assets", self.asset["id"], {"analysis": {**self.base, "analysis_id": "newer"}})
            return response
        with patch.object(providers, "_json_response", side_effect=race):
            with self.assertRaisesRegex(ValueError, "未覆盖新版本"):
                presentation._operation(self.asset["id"], "base", self.job["id"])
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"]["analysis_id"], "newer")
        with patch.object(providers, "_json_response") as model:
            recovered = presentation._operation(self.asset["id"], "newer", self.job["id"])
        model.assert_not_called()
        self.assertTrue(recovered["presentation"]["cache_hit"])

    def test_visual_repair_invalidates_organization_without_deleting_history(self):
        before = {**self.base, "presentation": {"kind": "scene_collection", "source_analysis_id": "older"}}
        db.update("assets", self.asset["id"], {"analysis": before})
        edit = {"collection": "content_segments", "index": 0,
                "expected_sha256": reference_repair.digest(before["content_segments"][0]),
                "fields": {"description": "新观察"}, "reason": "证据修正"}
        reference_repair._operation(self.asset["id"], "base", [], [edit], self.job["id"])
        head = db.get("assets", self.asset["id"])["analysis"]
        self.assertNotIn("presentation", head)
        self.assertEqual(head["presentation_invalidated"]["reason"], "visual_evidence_changed")
        self.assertEqual(head["shots"], self.base["shots"])

    def test_paid_response_recovery_and_one_targeted_correction(self):
        context, frames = presentation._prepare(self.asset, self.base)
        original = self.response(None, None, None, json.dumps(context), [None]*8)
        # Supported screen-background displacement is not a fresh physical
        # camera claim and must not trigger another paid model call.
        original["nodes"][0]["description"] = "背景画面向左位移，人物保持点颊姿态"
        directory = db.DATA / "checkpoint"
        directory.mkdir()
        asset = {"_analysis_cache_dir": str(directory)}
        schema = presentation._schema(2)
        path, signature = correction._path(asset, "reference_presentation", schema, presentation.SYSTEM, context, frames)
        def save(value):
            path.write_text(json.dumps({"signature": signature, "source_job_id": "paid-original",
                "result": {"provider_status": "completed", "output_text": json.dumps(value)}}))
        save(original)
        with patch.object(providers, "_json_response") as model:
            recovered, receipt = correction.response(asset, context, frames)
        model.assert_not_called()
        self.assertEqual(recovered, original)
        self.assertEqual(receipt["new_provider_calls"], 0)
        # A malformed reference and an extra link require only a small patch;
        # the valid first node and top-level interpretation stay byte-for-byte.
        invalid = copy.deepcopy(original)
        invalid["nodes"][1]["evidence_times"] = [42]
        invalid["links"].append(copy.deepcopy(invalid["links"][0]))
        save(invalid)
        raw_before = path.read_bytes()
        def fix(name, schema, system, user, images=None):
            self.assertEqual(name, "reference_presentation_correction")
            self.assertIsNone(images)
            partial = json.loads(user)
            self.assertEqual(partial["allowed_node_indices"], [2])
            self.assertEqual(partial["allowed_link_indices"], [1])
            patch_value = {"nodes": [{"index": 2, **original["nodes"][1]}],
                           "links": [{"after_node": 1, **original["links"][0]}],
                           "discarded_links": [{"index": 2, "reason": "重复的多余连接"}]}
            providers._ANALYSIS_TEXT_SINK.get()(json.dumps(patch_value), "completed")
            return patch_value
        with patch.object(providers, "_json_response", side_effect=fix) as model:
            fixed, receipt = correction.response(asset, context, frames)
            replay, replay_receipt = correction.response(asset, context, frames)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(fixed, original)
        self.assertEqual(replay, original)
        self.assertEqual(receipt["new_provider_calls"], 1)
        self.assertEqual(replay_receipt["new_provider_calls"], 0)
        self.assertEqual(path.read_bytes(), raw_before)

    def test_narrative_reuses_classification_and_preserves_semantic_boundaries(self):
        context, frames = presentation._prepare(self.asset, self.base)
        value = self.response(None, None, None, json.dumps(context), [None]*8)
        value.update(kind="narrative", label="人物自述", summary="由已有旁白展开观点")
        value["nodes"][1]["evidence_times"] = [42]
        value["links"].append(copy.deepcopy(value["links"][0]))
        directory = db.DATA / "narrative-cache"
        directory.mkdir()
        asset = {"_analysis_cache_dir": str(directory)}
        path, signature = correction._path(asset, "reference_presentation", presentation._schema(2), presentation.SYSTEM, context, frames)
        path.write_text(json.dumps({"signature": signature, "source_job_id": "paid-classification",
            "result": {"provider_status": "completed", "output_text": json.dumps(value)}}))
        with patch.object(providers, "_json_response") as model:
            classification, receipt = correction.response(asset, context, frames)
        model.assert_not_called()
        self.assertEqual(classification["nodes"], [])
        self.assertEqual(classification["links"], [])
        self.assertEqual(receipt["new_provider_calls"], 0)
        self.assertEqual(receipt["unpublished_response_audit"]["unpublished_node_count"], 2)
        source = copy.deepcopy(self.base)
        source["content_segments"] = [
            {"start": 0, "end": 1.3, "description": "原始海岸描述", "tags": ["海岸", "人物", "蓝色"], "evidence_times": [.1]},
            {"start": 1, "end": 1.7, "description": "原始室内描述", "tags": ["室内"], "evidence_times": [1.1]},
            {"start": 1.9, "end": 2, "description": "原始结尾描述", "tags": [], "evidence_times": [1.9]},
        ]
        before = copy.deepcopy(source)
        projected = presentation._project_narrative(classification, source)
        self.assertEqual(source, before)
        self.assertEqual([(n["start"], n["end"], n["description"]) for n in projected["nodes"]],
                         [(n["start"], n["end"], n["description"]) for n in source["content_segments"]])
        self.assertEqual(projected["nodes"][0]["label"], "海岸 · 人物")
        self.assertEqual(projected["nodes"][0]["shot_ids"], ["shot-0001", "shot-0002"])
        self.assertEqual(projected["nodes"][1]["shot_ids"], ["shot-0002"])
        self.assertTrue(all(l["kind"] == "sequence" and l["description"] == "" and not l["evidence_times"]
                            for l in projected["links"]))
        self.assertFalse(projected["provenance"]["model_regrouped_content"])
        later = presentation._project_narrative(classification, {**source, "analysis_id": "later"})
        self.assertEqual([n["id"] for n in projected["nodes"]], [n["id"] for n in later["nodes"]])
