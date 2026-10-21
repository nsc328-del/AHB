"""Single-node scope, durable request deduplication and invalid-output rejection."""
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import home_ai
import providers
import storage as db
import usage
import workflow


class HomeNodeAITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="home-node-ai-")
        self.previous_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        db.initialize()
        self.assets = [
            db.create("assets", {"name": "开场.mp4", "kind": "video", "duration": 10, "purpose": "library"}),
            db.create("assets", {"name": "结尾.png", "kind": "image", "duration": 0, "purpose": "library"}),
            db.create("assets", {"name": "替换.mp4", "kind": "video", "duration": 5, "purpose": "library"}),
        ]
        self.provider_patch = patch.object(providers, "create_plan", side_effect=self.propose)
        self.model = self.provider_patch.start()

    def tearDown(self):
        deadline = time.monotonic() + 5
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.01)
        self.provider_patch.stop()
        db.DATA = self.previous_data
        self.tmp.cleanup()

    def payload(self, key="home-node-test-001"):
        return {
            "submission_id": key, "instruction": "第二个镜头展示 3 秒，加字幕：今天的答案",
            "clip_id": "node-b",
            "plan": {"title": "首页分支", "ratio": "9:16", "clips": [
                {"id": "node-a", "asset_id": self.assets[0]["id"], "start": 0, "end": 2, "duration": 2},
                {"id": "node-b", "asset_id": self.assets[1]["id"], "start": 0, "end": 2, "duration": 2},
            ]},
        }

    def propose(self, brief, assets, options, reference=None, previous=None):
        usage.record_response("deepseek", "/responses", response={
            "id": "offline-node-response", "model": "offline",
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        })
        clip = {**previous["clips"][0], "id": "model-must-not-replace-id",
                "duration": 3, "end": 3, "caption": "今天的答案"}
        return {"clips": [clip], "summary": "不用于替代实际修改清单", "music_asset_id": ""}

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.01)
        self.fail("Node edit did not complete")

    def test_concurrent_retries_one_call_scoped_input_and_reviewable_result(self):
        payload = self.payload()
        pristine = copy.deepcopy(payload)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(home_ai.create_node_edit, [copy.deepcopy(payload) for _ in range(4)]))
        self.assertEqual(len({item["job_id"] for item in receipts}), 1)
        receipt = receipts[0]
        job = self.wait(receipt["job_id"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(self.model.call_count, 1)
        result = job["result"]
        self.assertEqual(result["signature"], receipt["signature"])
        self.assertEqual(len(result["signature"]), 64)
        self.assertEqual(result["clip_id"], "node-b")
        self.assertEqual(result["clip"]["id"], "node-b")
        self.assertEqual(result["clip"]["timeline_start"], 2)
        self.assertEqual(result["clip"]["timeline_end"], 5)
        self.assertEqual(result["clip"]["caption"], "今天的答案")
        self.assertEqual(payload, pristine)
        self.assertEqual(db.records("projects"), [])
        self.assertEqual(db.records("plans"), [])
        self.assertEqual(db.records("renders"), [])
        args, kwargs = self.model.call_args
        self.assertEqual([asset["id"] for asset in args[1]], [self.assets[1]["id"]])
        self.assertEqual(len(kwargs["previous"]["clips"]), 1)
        self.assertEqual(args[2]["node_context"]["neighbors"]["before"]["id"], "node-a")
        records = db.records("usage_records")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["job_id"], receipt["job_id"])
        self.assertEqual(records[0]["operation"], "home_node_edit")
        # Persisted jobs still prevent another call after process state is reset.
        db.mark_interrupted()
        self.assertEqual(home_ai.create_node_edit(copy.deepcopy(payload)), receipt)
        changed = copy.deepcopy(payload)
        changed["instruction"] = "改成另一句字幕"
        with self.assertRaisesRegex(ValueError, "相同提交编号"):
            home_ai.create_node_edit(changed)
        changed["submission_id"] = "home-node-test-002"
        next_receipt = home_ai.create_node_edit(changed)
        self.assertNotEqual(next_receipt["signature"], receipt["signature"])
        self.assertEqual(self.wait(next_receipt["job_id"])["status"], "succeeded")

    def test_invalid_branch_or_reference_never_calls_model(self):
        bad = self.payload()
        bad["plan"]["clips"][0]["end"] = 50
        bad["plan"]["clips"][0]["duration"] = 50
        with self.assertRaisesRegex(ValueError, "实际时长"):
            home_ai.create_node_edit(bad)
        bad = self.payload()
        bad["plan"]["clips"][0]["id"] = "node-b"
        with self.assertRaisesRegex(ValueError, "重复"):
            home_ai.create_node_edit(bad)
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                db.update("assets", self.assets[2]["id"], {
                    "purpose": "library" if legacy else "reference",
                    "analysis": {"structure": [{"role": "开场"}]} if legacy else None,
                })
                bad = self.payload()
                bad["asset_ids"] = [self.assets[2]["id"]]
                with self.assertRaisesRegex(ValueError, "样片"):
                    home_ai.create_node_edit(bad)
        self.assertEqual(db.records("jobs"), [])
        self.model.assert_not_called()

    def test_out_of_scope_or_invalid_model_output_does_not_change_branch(self):
        base = self.payload()
        current = base["plan"]["clips"][1]
        outputs = [
            {"clips": [current, current]},
            {"clips": [{**current, "asset_id": self.assets[2]["id"]}]},
            {"clips": [{**current, "transition": "zoom"}]},
            {"clips": [{**current, "duration": -1, "end": -1}]},
        ]
        for index, output in enumerate(outputs):
            with self.subTest(index=index):
                self.model.side_effect = lambda *args, **kwargs: copy.deepcopy(output)
                payload = self.payload(f"home-invalid-node-{index}")
                receipt = home_ai.create_node_edit(payload)
                job = self.wait(receipt["job_id"])
                self.assertEqual(job["status"], "failed", job)
                self.assertIsNone(job["result"])
                calls = self.model.call_count
                self.assertEqual(home_ai.create_node_edit(payload), receipt)
                self.assertEqual(self.model.call_count, calls)
        self.assertEqual(db.records("projects"), [])
        self.assertEqual(db.records("plans"), [])
        self.assertEqual(db.records("renders"), [])

    def test_interrupted_queued_submission_is_not_automatically_reissued(self):
        class HeldExecutor:
            def submit(self, *args):
                self.saved = args
        payload = self.payload()
        with patch.object(workflow, "EXECUTOR", HeldExecutor()):
            receipt = home_ai.create_node_edit(payload)
        db.mark_interrupted()
        self.assertEqual(home_ai.create_node_edit(payload), receipt)
        self.assertEqual(db.get("jobs", receipt["job_id"])["status"], "failed")
        self.model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
