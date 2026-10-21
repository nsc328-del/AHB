"""Editorial boundaries never become detected cuts or rewrite source evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import reference_presentation
import reference_semantics as semantics
import storage as db
import workflow


class SemanticTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        change = patch.object(db, "DATA", Path(self.tmp.name))
        change.start()
        self.addCleanup(change.stop)
        db.initialize()
        self.analysis = {"analysis_id": "base", "reference_analysis": True,
            "content_segments": [{"start": 0, "end": 3, "description": "旧观察", "visual_description": "人物与海岸",
                "role": "旧叙事判断", "evidence_times": [.5, 2.5]},
                {"start": 3, "end": 6, "description": "室内人物", "role": "另一个旧判断", "evidence_times": [3.5, 5.5]}],
            "shots": [{"id": f"shot-{i:04}", "start": i/2, "end": (i+1)/2} for i in range(12)],
            "presentation": {"kind": "narrative", "label": "观点表达", "summary": "旧总结", "confidence": "medium",
                "basis": [], "limitations": [], "version": 3, "nodes": [], "links": []},
            "segments": [{"start": 0, "end": .5, "description": "细镜头"}], "evidence_frames": []}
        self.speech = {"segments": [
            {"start": 0, "end": 1.5, "text": "先说明一个问题。"},
            {"start": 1.5, "end": 3, "text": "继续解释这个问题。"},
            {"start": 3, "end": 6, "text": "最后说明另一种看法。"}]}
        self.audio = {"status": "used", "accepted_segment_count": 3, "excluded_segment_count": 0}
        self.asset = db.create("assets", {"kind": "video", "purpose": "reference", "duration": 6,
            "has_audio": True, "analysis": self.analysis})
        db.create("analyses", {"id": "base", "result": self.analysis})
        self.job = db.create("jobs", {"kind": "reference_semantics", "status": "running", "payload": {"asset_id": self.asset["id"]}})

    def proposal(self, context):
        ends = {row["end"]: row["id"] for row in context["speech"]}
        return {"mode": "speech_driven", "content_type": "opinion", "organizing_principle": "用两种看法对比", "thesis": "重新看待问题",
            "units": [{"label": "说明问题", "summary": "先说明问题并解释", "visual_support": "海岸人物", "boundary_reason": "建立第一个观点",
                       "last_speech_id": ends[3]},
                      {"label": "换一种看法", "summary": "提出另一种看法", "visual_support": "室内人物", "boundary_reason": "论点改变",
                       "last_speech_id": ends[6]}],
            "chapters": [{"label": "问题与看法", "summary": "从问题转向另一种看法", "purpose": "对比观点", "first_unit": 1, "last_unit": 2}],
            "limitations": ["沿用原始转写"]}

    def response(self, name, schema, system, user, frames=None):
        self.assertEqual(name, "reference_semantics")
        self.assertIsNone(frames)
        context = json.loads(user)
        self.assertNotIn("shots", context)
        self.assertNotIn("旧叙事判断", user)
        self.assertEqual(context["observations"][0]["description"], "人物与海岸")
        return self.proposal(context)

    def test_multishot_units_chapters_cache_and_immutable_sources(self):
        with patch.object(workflow, "_audio_input", return_value=(self.speech, self.audio)), \
             patch.object(providers, "_json_response", side_effect=self.response) as model:
            result = semantics._operation(self.asset["id"], "base", self.job["id"])
            head = db.get("assets", self.asset["id"])["analysis"]
            semantic = head["semantic_structure"]
            self.assertEqual(len(semantic["chapters"]), 1)
            self.assertEqual(len(semantic["units"][0]["shot_ids"]), 6)
            self.assertEqual([row["text"] for row in semantic["units"][0]["speech_evidence"]],
                             [row["text"] for row in self.speech["segments"][:2]])
            for key in ("shots", "segments", "content_segments", "evidence_frames"):
                self.assertEqual(head[key], self.analysis[key])
            self.assertEqual(db.get("analyses", "base")["result"], self.analysis)
            self.assertEqual(head["presentation"]["nodes"][0]["chapter_id"], "chapter-001")
            self.assertEqual(head["presentation"]["nodes"][0]["end"], 6)
            semantics._operation(self.asset["id"], result["analysis_id"], self.job["id"])
            self.assertEqual(model.call_count, 1)
            self.assertTrue(db.get("assets", self.asset["id"])["analysis"]["semantic_structure"]["provenance"]["cache_hit"])

    def test_boundaries_and_sentence_overlap_are_checked_before_publication(self):
        speech = {"segments": [*self.speech["segments"], {"start": .7, "end": 2.2, "text": "重叠句不能从中间切开"}]}
        with patch.object(workflow, "_audio_input", return_value=(speech, self.audio)):
            context = semantics._prepare(self.asset, self.analysis)
        self.assertNotIn("anchors", context)
        self.assertFalse(next(row for row in context["speech"] if row["end"] == 1.5)["can_end_unit"])
        value = self.proposal(context)
        semantics._validate(value, context)
        broken = copy.deepcopy(value)
        broken["units"][1]["last_speech_id"] = broken["units"][0]["last_speech_id"]
        with self.assertRaisesRegex(providers.ProviderError, "连续覆盖"):
            semantics._validate(broken, context)
        broken = copy.deepcopy(value)
        broken["chapters"][0]["last_unit"] = 1
        with self.assertRaisesRegex(providers.ProviderError, "覆盖所有语义段"):
            semantics._validate(broken, context)
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"]["analysis_id"], "base")

    def test_paid_semantics_alignment_freezes_prose_and_checks_source_scope(self):
        with patch.object(workflow, "_audio_input", return_value=(self.speech, self.audio)):
            context = semantics._prepare(self.asset, self.analysis)
        source = self.proposal(context)
        for row in source["units"]:
            row.pop("last_speech_id")
            row.update(start_anchor=999, end_anchor=1000)
        source_job = db.create("jobs", {"kind": "reference_semantics", "status": "failed", "payload": {
            "asset_id": self.asset["id"], "base_analysis_id": "base"}})
        folder = db.DATA / "analysis-cache" / self.asset["id"] / "semantics"
        folder.mkdir(parents=True)
        saved = {"source_job_id": source_job["id"], "result": {"provider_status": "completed", "output_text": json.dumps(source)}}
        path = folder / "paid.response.json"
        path.write_text(json.dumps(saved))
        frozen_bytes = path.read_bytes()
        alignment = {"units": [{"unit_index": 1, "last_speech_id": "speech-0002"}, {"unit_index": 2, "last_speech_id": "speech-0003"}]}
        with patch.object(workflow, "_audio_input", return_value=(self.speech, self.audio)), \
             patch.object(providers, "_json_response", return_value=alignment) as model:
            outcome = semantics._repair_operation(self.asset["id"], "base", source_job["id"], self.job["id"])
        self.assertEqual(model.call_count, 1)
        self.assertEqual(path.read_bytes(), frozen_bytes)
        result = outcome["semantic_structure"]
        self.assertEqual([row["summary"] for row in result["units"]], [row["summary"] for row in source["units"]])
        self.assertEqual(result["chapters"][0]["summary"], source["chapters"][0]["summary"])
        self.assertEqual(result["units"][0]["end"], 3)
        self.assertFalse(result["provenance"]["alignment_repair"]["new_semantic_generation"])
        with self.assertRaisesRegex(ValueError, "同一样片同一版本"):
            semantics._alignment_source(self.asset["id"], "other", source_job["id"])

    def test_motion_without_speech_is_reused_and_stale_results_do_not_publish(self):
        original = copy.deepcopy(self.analysis)
        original["presentation"]["kind"] = "motion_transition"
        db.update("assets", self.asset["id"], {"analysis": original})
        with patch.object(workflow, "_audio_input", return_value=(None, {"status": "uncertain"})), \
             patch.object(providers, "_json_response") as model:
            outcome = semantics._operation(self.asset["id"], "base", self.job["id"])
        model.assert_not_called()
        self.assertTrue(outcome["reused_presentation"])
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"], original)
        def late(name, schema, system, user, frames=None):
            db.update("assets", self.asset["id"], {"analysis": {**original, "analysis_id": "newer"}})
            return self.response(name, schema, system, user, frames)
        with patch.object(workflow, "_audio_input", return_value=(self.speech, self.audio)), \
             patch.object(providers, "_json_response", side_effect=late):
            with self.assertRaisesRegex(ValueError, "版本已变化"):
                semantics._operation(self.asset["id"], "base", self.job["id"])
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"]["analysis_id"], "newer")


if __name__ == "__main__":
    unittest.main()
