"""Review persistence and cut contracts; local fixtures, no provider requests."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import providers
import storage as db
import workflow
from app import app
from skill_catalog import ROOT as SKILL_ROOT


PROMPT = ("integrated_multimodal_description: [Shot 1] A blue paper cube unfolds into a flat square "
          "on a white tabletop in a fixed overhead view. Stop motion, visible paper grain and folds.\n"
          "overall_soundscape: Dry paper rustling.\nnon_diegetic_music: N/A")


def board(skill="papercraft-stop-motion-explainer", count=2):
    return {"title": "纸艺展开", "summary": "按切镜拆分", "character_anchors": [], "scene_anchors": [],
            "missing_inputs": [], "skill_id": skill, "shots": [
                {"id": f"shot-{index + 1}", "label": f"片段{index + 1}", "script": "俯拍蓝色纸方块展开为平面，纸张纹理清晰。",
                 "duration": 6, "purpose": "展示", "continuity": "白色桌面", "visual": "俯拍", "audio": "纸张摩擦",
                 "mode": "T2VA", "first_asset_id": "", "last_asset_id": "", "reference_asset_ids": [], "prompt": PROMPT}
                for index in range(count)]}


class CutContractTest(unittest.TestCase):
    def test_generation_task_exposes_template_without_private_payload(self):
        raw = {"id": "pending", "kind": "generation_plan", "status": "running",
               "payload": {"skill_id": "papercraft-stop-motion-explainer", "prompt": "private description"}}
        public = db.public_job(raw)
        self.assertEqual(public["skill_id"], "papercraft-stop-motion-explainer")
        self.assertNotIn("payload", public)
        self.assertNotIn("prompt", public)
        self.assertNotIn("skill_id", db.public_job({**raw, "kind": "analysis"}))

    def test_multiple_cuts_keep_full_skills_and_refuse_hidden_cuts(self):
        for skill in ("h3-prompt-writing", "papercraft-stop-motion-explainer"):
            response = board(skill)
            response.pop("skill_id")
            with patch.object(providers, "_json_response", return_value=response) as model:
                result = providers.create_generation_storyboard(
                    {"skill_id": skill, "prompt": "两个切镜：纸方块展开，再切换纸帆船。每段六秒。", "duration": 6}, [])
            self.assertEqual(len(result["shots"]), 2)
            context = json.loads(model.call_args.args[3])
            for relative in ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"):
                text = (SKILL_ROOT / "h3-prompt-writing" / "upstream" / relative).read_text()
                self.assertIn(text, context["creative_reference"])
            if skill != "h3-prompt-writing":
                self.assertIn((SKILL_ROOT / skill / "upstream" / "SKILL.md").read_text(), context["creative_reference"])
            invalid = copy.deepcopy(response)
            invalid["shots"][0]["prompt"] = PROMPT.replace(
                "\noverall_soundscape:", " [Shot 2] At 00:03.000, cut to a paper boat.\noverall_soundscape:")
            with patch.object(providers, "_json_response", return_value=invalid):
                with self.assertRaisesRegex(providers.ProviderError, "切镜拆成独立片段"):
                    providers.create_generation_storyboard({"skill_id": skill, "duration": 6}, [])


class GenerationReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="generation-review-")
        self.old_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.client = TestClient(app)
        self.client.__enter__()
        self.original = board()
        self.job = db.create("jobs", {"kind": "generation_plan", "status": "succeeded",
                                     "payload": {"skill_id": self.original["skill_id"]},
                                     "result": {"storyboard": copy.deepcopy(self.original)}})
        self.url = "/api/generation-plans/" + self.job["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DATA = self.old_data
        self.tmp.cleanup()

    def update(self, payload, code=200):
        result = self.client.put(self.url + "/shots/shot-1", json=payload)
        self.assertEqual(result.status_code, code, result.text)
        return result.json()

    def test_review_survives_reload_keeps_original_and_revokes_confirmation_on_edit(self):
        initial = self.client.get(self.url).json()
        self.assertEqual(initial["revision"], 0)
        self.assertFalse(initial["storyboard"]["shots"][0]["confirmed"])
        saved = self.update({"script": "先折叠，再展开。", "prompt": PROMPT.replace("unfolds", "folds then unfolds"),
                             "expected_revision": 0})
        confirmed = self.update({"confirmed": True, "expected_revision": saved["revision"]})
        self.assertTrue(confirmed["storyboard"]["shots"][0]["confirmed"])
        self.assertEqual(self.client.get(self.url).json(), confirmed)
        listed = self.client.get("/api/generation-plans", params={"skill_id": self.original["skill_id"]}).json()
        self.assertEqual(listed["plans"], [confirmed])
        self.assertEqual(db.get("jobs", self.job["id"])["result"]["storyboard"], self.original)
        self.update({"script": "过期页面试图覆盖", "expected_revision": 0}, 400)
        draft = self.update({"prompt": "", "expected_revision": confirmed["revision"]})
        self.assertFalse(draft["storyboard"]["shots"][0]["confirmed"])
        self.assertIsNone(draft["storyboard"]["shots"][0]["confirmed_at"])
        self.update({"confirmed": True, "expected_revision": draft["revision"]}, 400)
        self.assertEqual(self.client.get(self.url).json()["revision"], draft["revision"])
        self.update({"duration": 3}, 400)
        self.update({"mode": "unsupported"}, 400)
        self.update({"id": "replace-shot"}, 400)

    def test_paid_submit_requires_matching_confirmation_but_replays_prior_submission(self):
        request = {"prompt": PROMPT, "duration": 6, "mode": "T2VA", "source": "storyboard",
                   "storyboard_job_id": self.job["id"], "storyboard_shot_id": "shot-1",
                   "review_revision": 0, "submission_id": str(uuid.uuid4())}
        self.assertEqual(self.client.post("/api/generations", json=request).status_code, 400)
        view = self.update({"confirmed": True, "expected_revision": 0})
        request["review_revision"] = view["revision"]
        with patch.object(workflow, "_schedule_job", side_effect=lambda job, operation: job) as schedule:
            rejected = self.client.post("/api/generations", json={**request, "prompt": PROMPT + " altered"})
            self.assertEqual(rejected.status_code, 400)
            self.assertFalse(schedule.called)
            accepted = self.client.post("/api/generations", json=request)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(schedule.call_count, 1)
            self.update({"script": "修改后必须再次确认", "expected_revision": view["revision"]})
            replay = self.client.post("/api/generations", json=request)
            self.assertEqual(replay.json(), accepted.json())
            self.assertEqual(schedule.call_count, 1)
            stale = self.client.post("/api/generations", json={**request, "submission_id": str(uuid.uuid4())})
            self.assertEqual(stale.status_code, 400)

    def test_missing_inputs_block_confirmation_and_legacy_confirmed_submission(self):
        incomplete = copy.deepcopy(self.original)
        incomplete["missing_inputs"] = ["产品外观参考图"]
        db.update("jobs", self.job["id"], {"result": {"storyboard": incomplete}})
        draft = self.update({"script": "仍可保存草稿。", "expected_revision": 0})
        denied = self.update({"confirmed": True, "expected_revision": draft["revision"]}, 400)
        self.assertIn("补齐", denied["detail"])
        # Old persisted confirmations must not bypass the check on a new paid submit.
        db.update("jobs", self.job["id"], {"generation_review": {
            "revision": 2, "shots": {"shot-1": {"confirmed": True, "confirmed_at": db.now()}}}})
        request = {"prompt": PROMPT, "duration": 6, "mode": "T2VA", "source": "storyboard",
                   "storyboard_job_id": self.job["id"], "storyboard_shot_id": "shot-1",
                   "review_revision": 2, "submission_id": str(uuid.uuid4())}
        with patch.object(workflow, "_schedule_job") as schedule:
            result = self.client.post("/api/generations", json=request)
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIn("补齐", result.json()["detail"])
        schedule.assert_not_called()

    def test_script_edits_require_prompt_sync_and_rewrite_never_overwrites_a_newer_edit(self):
        script = "一只蓝色纸帆船在桌面上缓慢滑行，固定机位，保留纸张摩擦声。"
        draft = self.update({"script": script, "expected_revision": 0})
        self.assertTrue(draft["storyboard"]["shots"][0]["prompt_stale"])
        self.update({"confirmed": True, "expected_revision": draft["revision"]}, 400)
        pending = []

        def schedule(job, operation, executor=None):
            pending.append((job, operation))
            return job

        def queue(view):
            response = self.client.post(self.url + "/shots/shot-1/rewrite-prompt",
                                        json={"expected_revision": view["revision"]})
            self.assertEqual(response.status_code, 200, response.text)
            return pending[-1]

        rewritten = board(count=1)
        rewritten["shots"][0]["prompt"] = PROMPT.replace("cube unfolds", "boat unfolds")
        with patch.object(workflow, "_schedule_job", side_effect=schedule):
            job, operation = queue(draft)
            with patch.object(providers, "_json_response", return_value=copy.deepcopy(rewritten)) as model:
                workflow._execute(job["id"], operation)
            self.assertEqual(db.get("jobs", job["id"])["status"], "succeeded")
            context = json.loads(model.call_args.args[3])
            self.assertEqual(context["request"]["prompt"], script)
            self.assertEqual(context["request"]["shot_count"], 1)
            self.assertNotIn("brief", context["request"])
            synced = self.client.get(self.url).json()
            shot = synced["storyboard"]["shots"][0]
            self.assertEqual(shot["script"], script)
            self.assertEqual(shot["prompt"], rewritten["shots"][0]["prompt"])
            self.assertEqual(shot["prompt_job_id"], job["id"])
            self.assertFalse(shot["prompt_stale"])
            self.assertFalse(shot["confirmed"])
            self.assertEqual(db.get("jobs", self.job["id"])["result"]["storyboard"], self.original)

            edited = self.update({"script": "第二次编辑", "expected_revision": synced["revision"]})
            next_job, next_operation = queue(edited)

            def concurrent_edit(*args, **kwargs):
                self.update({"script": "请求期间的第三次编辑", "expected_revision": edited["revision"]})
                return copy.deepcopy(rewritten)

            with patch.object(providers, "_json_response", side_effect=concurrent_edit):
                workflow._execute(next_job["id"], next_operation)
            self.assertEqual(db.get("jobs", next_job["id"])["status"], "failed")
            kept = self.client.get(self.url).json()
            self.assertEqual(kept["storyboard"]["shots"][0]["script"], "请求期间的第三次编辑")
            self.assertEqual(kept["storyboard"]["shots"][0]["prompt_job_id"], job["id"])
            self.assertTrue(kept["storyboard"]["shots"][0]["prompt_stale"])

            manual = self.update({"prompt": PROMPT, "expected_revision": kept["revision"]})
            self.assertFalse(manual["storyboard"]["shots"][0]["prompt_stale"])
            retimed = self.update({"duration": 8, "expected_revision": manual["revision"]})
            self.assertTrue(retimed["storyboard"]["shots"][0]["prompt_stale"])
            self.update({"confirmed": True, "expected_revision": retimed["revision"]}, 400)


if __name__ == "__main__":
    unittest.main()
