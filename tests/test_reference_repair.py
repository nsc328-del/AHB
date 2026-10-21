"""Selected repairs cannot leak future speech or replace untouched evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import reference_repair as repair
import storage as db


class ReferenceRepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(db, "DATA", Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)
        db.initialize()
        (db.DATA / "frames").mkdir(exist_ok=True)
        frames = []
        for time in [.2, .7, 1.2, 1.7]:
            path = db.DATA / "frames" / f"{time}.jpg"
            path.write_bytes(b"cached-frame")
            frames.append({"time": time, "url": db.media_url(path)})
        segments = [{"start": i, "end": i+1, "description": "旧视觉和未来旁白",
                     "motion": "不改", "role": "不改", "evidence_times": [i+.2]} for i in range(2)]
        self.base = {"analysis_id": "base", "summary": "保留全文", "segments": copy.deepcopy(segments),
                     "content_segments": segments, "evidence_frames": frames, "shot_boundaries": [0, 1, 2],
                     "shots": [{"start": 0, "end": 1}, {"start": 1, "end": 2}], "transcript": {"text": "未来旁白"}}
        self.asset = db.create("assets", {"kind": "video", "duration": 2, "purpose": "reference",
                                        "has_audio": False, "analysis": self.base})
        db.create("analyses", {"id": "base", "result": self.base})
        self.job = db.create("jobs", {"kind": "reference_repair", "status": "running"})

    def respond(self, name, schema, system, user, images=None):
        context = json.loads(user)
        self.assertNotIn("未来旁白", user)
        self.assertNotIn("旧视觉", user)
        return {"sections": [{"id": w["id"], "description": "本段可见人物",
                              "motion": "横向取景", "role": "人物", "evidence_times": w["frame_times"]}
                             for w in context["sections"]]}

    def targets(self, index=0):
        return [{"collection": c, "index": index, "issues": ["speech"]}
                for c in ["segments", "content_segments"]]

    def test_selected_intervals_only_word_evidence_and_shared_call(self):
        with patch.object(providers, "_json_response", side_effect=self.respond) as model:
            outcome = repair._operation(self.asset["id"], "base", self.targets(), [], self.job["id"])
        self.assertEqual(model.call_count, 1)
        result = db.get("assets", self.asset["id"])["analysis"]
        self.assertEqual(db.get("analyses", "base")["result"], self.base)
        for collection in repair.COLLECTIONS:
            self.assertEqual(result[collection][1], self.base[collection][1])
            self.assertEqual(result[collection][0]["motion"], "不改")
            self.assertEqual(result[collection][0]["speech_evidence"], [])
        self.assertEqual(result["shots"], self.base["shots"])
        self.assertEqual(outcome["repair"]["new_provider_calls"], 1)

    def test_invalid_scope_fails_atomically_and_cached_groups_survive_retry(self):
        targets = self.targets()
        # Force separate groups, so a failure occurs after a successful checkpoint.
        def separate(windows):
            return ([window] for window in windows)
        targets += self.targets(1)
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            result = self.respond(*args, **kwargs)
            if count == 2:
                result["sections"][0]["evidence_times"] = [42]
            return result
        with patch.object(repair, "_batches", side_effect=separate), \
             patch.object(providers, "_json_response", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "选段外"):
                repair._operation(self.asset["id"], "base", targets, [], self.job["id"])
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"], self.base)
        with patch.object(repair, "_batches", side_effect=separate), \
             patch.object(providers, "_json_response", side_effect=self.respond) as model:
            outcome = repair._operation(self.asset["id"], "base", targets, [], self.job["id"])
        self.assertEqual(model.call_count, 1)
        self.assertEqual(outcome["repair"]["new_provider_calls"], 1)

    def test_exact_local_edit_and_stale_base_do_not_call_model(self):
        edit = {"collection": "content_segments", "index": 0,
                "expected_sha256": repair.digest(self.base["content_segments"][0]),
                "fields": {"description": "旧视觉"}, "reason": "移出范围外旁白"}
        with patch.object(providers, "_json_response") as model:
            repair._operation(self.asset["id"], "base", [], [edit], self.job["id"])
            with self.assertRaisesRegex(ValueError, "版本已变化"):
                repair._operation(self.asset["id"], "base", self.targets(), [], self.job["id"])
        model.assert_not_called()
        result = db.get("assets", self.asset["id"])["analysis"]
        self.assertEqual(result["segments"], self.base["segments"])
        self.assertEqual(result["content_segments"][0]["description"], "旧视觉")
        original_id = result["analysis_id"]
        repair.record_evidence_review(self.asset["id"], original_id, [
            {"collection": "content_segments", "index": 0, "expected_sha256": repair.digest(result["content_segments"][0]),
             "fields": {"description": "核对后的视觉"}, "reason": "连续帧复核", "evidence": [".2.jpg", ".7.jpg"]}])
        self.assertEqual(db.get("analyses", original_id)["result"], result)
        self.assertEqual(db.get("assets", self.asset["id"])["analysis"]["targeted_repairs"][-1]["review_provider_calls"], 0)

    def test_asr_window_does_not_copy_adjacent_full_sentence(self):
        raw = {"text": "现在出发以后回家", "segments": [{"start": 0, "end": 6, "text": "现在出发以后回家",
               "words": [{"word": "现在出发", "start": 0, "end": 2},
                         {"word": "以后回家", "start": 4, "end": 6}]}]}
        full = providers._transcript_window(raw, 0, 6, full=True)
        window = providers._transcript_window(full, 0, 3, full=False)
        self.assertEqual(window["text"], "现在出发")
        self.assertEqual(window["segments"][0]["end"], 3)
        self.assertEqual(raw["segments"][0]["text"], "现在出发以后回家")
