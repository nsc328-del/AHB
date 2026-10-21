"""Offline integration: numeric rhythm evidence and reference postprocessing."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import reference_reasoning
import rhythm_analysis as rhythm
import rhythm_evidence
import storage as db
import workflow


def evidence():
    return {
        "status": "analyzed", "duration": 3,
        "alignment_summary": {"tolerance_seconds": .1},
        "local_windows": [], "limits": ["未提供原音"],
        "cut_matches": [
            {"cut_time": 1, "onset": {"time": 1.08, "offset_ms": 900, "within_tolerance": True},
             "beat_candidate": {"time": .9, "offset_ms": -900, "within_tolerance": True}},
            {"cut_time": 2, "onset": {"time": 1.97, "offset_ms": -900, "within_tolerance": True},
             "beat_candidate": {"time": 1.6, "offset_ms": 400, "within_tolerance": False}},
        ],
    }


class RhythmAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patched = patch.object(db, "DATA", Path(self.temp.name))
        patched.start()
        self.addCleanup(patched.stop)
        db.initialize()
        self.path = db.DATA/"assets"/"source.mp4"
        self.path.write_bytes(b"offline-media-fixture")

    def asset(self):
        return db.create("assets", {
            "path": str(self.path), "kind": "video", "purpose": "reference",
            "duration": 3, "has_audio": True, "audio_status": "succeeded",
            "analysis": None, "analysis_status": "not_analyzed",
            "transcript": {"text": "ASR_DO_NOT_SEND_TO_RHYTHM", "language": "zh",
                           "language_probability": .99, "segments": []},
        })

    def test_classification_checks_real_distance_not_a_cached_boolean(self):
        rows = evidence()["cut_matches"]
        result = {"matches": [{"cut_id": 0, "classification": "beat_aligned"},
                              {"cut_id": 1, "classification": "beat_aligned"}]}
        rows[1]["beat_candidate"]["within_tolerance"] = True
        with self.assertRaisesRegex(providers.ProviderError, "容差"):
            rhythm.validate(result, rows)
        result["matches"][1]["classification"] = "onset_aligned"
        # -100 ms and +100 ms remain accepted despite floating-point error.
        rows[0]["beat_candidate"]["time"] = 1.1
        rows[0]["beat_candidate"]["within_tolerance"] = False
        rhythm.validate(result, rows)
        rows[0]["beat_candidate"] = None
        with self.assertRaisesRegex(providers.ProviderError, "不存在"):
            rhythm.validate(result, rows)

    def test_mapping_context_and_replay_keep_real_times_without_asr(self):
        asset = self.asset()
        analysis = {"shot_boundaries": [0, 1, 2, 3],
                    "summary": "OLD_ANALYSIS_DO_NOT_SEND", "transcript": asset["transcript"]}
        def reply(name, schema, system, user, images=None):
            self.assertEqual(name, "reference_rhythm")
            self.assertNotIn("DO_NOT_SEND", user)
            context = json.loads(user)
            self.assertEqual(context["audio_input"], "measured_time_series_only")
            self.assertIsNone(images)
            self.assertEqual([m["cut_time"] for m in context["cut_matches"]], [1, 2])
            # Return out of order: mapping must follow IDs, never array position.
            return {"summary": "候选时序关系", "editing_pattern": "mixed", "confidence": "low",
                    "matches": [{"cut_id": 1, "classification": "onset_aligned", "reason": "接近起音"},
                                {"cut_id": 0, "classification": "beat_aligned", "reason": "接近候选"}]}
        with patch.object(rhythm_evidence, "analyze_audio_rhythm", return_value=evidence()) as extract, \
             patch.object(providers, "_json_response", side_effect=reply) as model:
            result = rhythm._compute(asset, analysis, "offline-job")
            replay = rhythm._compute(asset, analysis, "offline-replay")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(model.call_count, 1)
        self.assertTrue(replay["cache_hit"])
        self.assertEqual([m["cut_time"] for m in result["matches"]], [1, 2])
        self.assertEqual(result["matches"][0]["onset_offset_ms"], -80)
        self.assertEqual(result["matches"][0]["beat_offset_ms"], 100)
        self.assertEqual(result["matches"][1]["onset_offset_ms"], 30)
        self.assertFalse(result["raw_audio_sent"])

    @staticmethod
    def frames(asset, directory, reference):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/"frame.jpg"
        path.write_bytes(b"offline-image")
        return [{"path": str(path), "time": .2}, {"path": str(path), "time": .8}], [0, 1, 3]

    @staticmethod
    def model_result(model=True):
        segment = {"start": 0, "end": 1, "description": "初次视觉结果", "motion": "未知",
                   "role": "开场", "evidence_times": [.2, .8]}
        result = {"summary": "已完成画面分析", "tags": [], "segments": [segment],
                  # Two content labels share a span: the detail target is unique.
                  "content_segments": [copy.deepcopy(segment), copy.deepcopy(segment)], "structure": []}
        if model:
            result["model"] = providers.DEEPSEEK_MODEL
        return result

    def review(self, asset, windows, frames):
        return {"sections": [{
            "id": window["id"], "description": "DS复核的画面", "motion": "连续取景变化",
            "role": "开场", "evidence_times": window["frame_times"],
            "structured_motion": {}, "verification": {"status": "verified"},
        } for window in windows]}, {
            "new_provider_calls": 2,
            "stages": {"verify": {"source_job_id": "offline-job"}},
        }

    def run_workflow(self, *, model=True, fail=False):
        asset = self.asset()
        job = db.create("jobs", {"kind": "reference", "status": "running",
                                "payload": {"asset_id": asset["id"]}})
        db.update("assets", asset["id"], {"analysis_job_id": job["id"]})
        results, errors = [], []
        def rhythmic(current, analysis, job_id):
            self.assertTrue(analysis["targeted_repairs"][-1]["automatic_model_review"])
            if fail:
                raise providers.ProviderError("offline rhythm failure")
            return {"status": "completed", "summary": "节奏时序", "matches": []}
        def work():
            try:
                results.append(workflow._analysis(asset["id"], True, job["id"], force=True))
            except Exception as error:
                errors.append(error)
        def organization(name, schema, system, user, images=None):
            self.assertEqual(name, "reference_organization")
            context = json.loads(user)
            self.assertEqual(context["rhythm"]["status"], "completed")
            return {"kind": "uncertain", "label": "镜头浏览", "summary": "离线证据不充分", "confidence": "low",
                    "basis": [], "limitations": ["部分镜头缺少图像"],
                    "nodes": [{"first_shot": i+1, "last_shot": i+1, "label": "画面",
                               "description": "离线画面", "evidence_times": shot["frame_times"]}
                              for i, shot in enumerate(context["shots"])],
                    "links": [{"kind": "uncertain",
                               "description": "证据不足", "evidence_times": [.2, .8]}]}
        with patch.object(workflow.media, "sample_frames", side_effect=self.frames), \
             patch.object(providers, "analyze_asset", return_value=self.model_result(model)), \
             patch.object(providers, "_json_response", side_effect=organization), \
             patch.object(reference_reasoning, "review_sections", side_effect=self.review) as review, \
             patch("motion_evidence.extract_motion_evidence", return_value={"frames": [], "statistics": {}}), \
             patch.object(rhythm, "_compute", side_effect=rhythmic) as compute:
            # Actual repair/rhythm _operation methods reacquire analysis_work in
            # this same worker; a non-reentrant lock would fail this deadline.
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            worker.join(4)
            self.assertFalse(worker.is_alive(), "reference postprocessing deadlocked")
        return db.get("assets", asset["id"]), results, errors, review.call_count, compute.call_count

    def test_automatic_reference_chain_is_reentrant_and_versions_each_stage(self):
        asset, results, errors, review_count, rhythm_count = self.run_workflow()
        self.assertFalse(errors)
        self.assertEqual((review_count, rhythm_count), (1, 1))
        self.assertEqual(asset["analysis_status"], "succeeded")
        self.assertEqual(asset["analysis"], results[0]["analysis"])
        self.assertEqual(asset["analysis"]["rhythm_analysis"]["status"], "completed")
        versions = db.records("analyses")
        self.assertEqual(len(versions), 4)
        self.assertEqual(len({r["id"] for r in versions}), 4)
        self.assertEqual(asset["analysis"]["presentation"]["kind"], "uncertain")
        base = next(r["result"] for r in versions if "targeted_repairs" not in r["result"])
        self.assertEqual(base["segments"][0]["description"], "初次视觉结果")

    def test_failed_postprocess_keeps_completed_visual_result_and_rules_skip_models(self):
        asset, results, errors, review_count, rhythm_count = self.run_workflow(fail=True)
        self.assertFalse(results)
        self.assertEqual(len(errors), 1)
        self.assertEqual((review_count, rhythm_count), (1, 1))
        self.assertEqual(asset["analysis_status"], "failed")
        self.assertIn("offline rhythm failure", asset["analysis_error"])
        self.assertEqual(asset["analysis"]["segments"][0]["description"], "DS复核的画面")
        self.assertNotIn("rhythm_analysis", asset["analysis"])
        self.assertEqual(db.get("analyses", asset["analysis"]["analysis_id"])["result"], asset["analysis"])
        rules, results, errors, review_count, rhythm_count = self.run_workflow(model=False)
        self.assertFalse(errors)
        self.assertEqual((review_count, rhythm_count), (0, 0))
        self.assertEqual(rules["analysis_status"], "succeeded")
        self.assertNotIn("targeted_repairs", rules["analysis"])


if __name__ == "__main__":
    unittest.main()
