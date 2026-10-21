"""Manual home branches: isolated persistence, concurrent retries and real FFmpeg."""
from concurrent.futures import ThreadPoolExecutor
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import home_api
import media
import storage as db
import workflow


class HomeCanvasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="home-canvas-")
        self.previous = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA)})
        self.env.start()
        db.initialize()
        self.ratios = patch.dict(workflow.RATIOS, {"1:1": (160, 160)})
        self.ratios.start()
        self.models = patch.object(workflow, "providers", side_effect=AssertionError("Manual editing must not call a model"))
        self.model_mock = self.models.start()
        self.assets = []
        for index, color in enumerate(("#2d3aeb", "#e8a340", "#438b74")):
            path = db.DATA / "assets" / f"sample-{index}.png"
            Image.new("RGB", (96, 96), color).save(path)
            self.assets.append(workflow.register_asset(path, path.name))

    def tearDown(self):
        deadline = time.monotonic() + 30
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.05)
        self.models.stop()
        self.ratios.stop()
        self.env.stop()
        db.DATA = self.previous
        self.tmp.cleanup()

    def payload(self):
        return {
            "submission_id": "home-example-001", "branch_id": "a", "branch_name": "顺序展开",
            "plan": {"title": "首页分支", "ratio": "1:1", "clips": [
                {"asset_id": asset["id"], "start": 0, "duration": .4, "end": .4,
                 "label": asset["name"], "caption": "", "transition": "cut"}
                for asset in self.assets]},
        }

    def wait(self, job_id):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = db.get("jobs", job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(.05)
        self.fail("Manual preview timed out")

    def test_concurrent_retry_one_project_then_real_versioned_preview(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, repeated = list(pool.map(home_api.create_home_preview, [copy.deepcopy(payload), copy.deepcopy(payload)]))
        self.assertEqual(first, repeated)
        self.assertEqual(len(db.records("projects")), 1)
        self.assertEqual(len(db.records("plans")), 1)
        self.assertEqual(len(db.records("jobs")), 1)
        job = self.wait(first["job_id"])
        self.assertEqual(job["status"], "succeeded", job)
        rendered = db.get("assets", job["result"]["asset_id"])
        self.assertAlmostEqual(media.probe(rendered["path"])["duration"], 1.2, delta=.15)
        self.assertEqual(len(workflow.project_view(first["project_id"])["renders"]), 1)

        changed = copy.deepcopy(payload)
        changed["plan"]["clips"].reverse()
        with self.assertRaisesRegex(ValueError, "相同提交编号"):
            home_api.create_home_preview(changed)
        changed.update(submission_id="home-example-002", project_id=first["project_id"], expected_plan_id="stale")
        with self.assertRaisesRegex(ValueError, "已有新版本"):
            home_api.create_home_preview(changed)
        self.assertEqual(len(db.records("plans")), 1)
        changed["expected_plan_id"] = first["plan_id"]
        second = home_api.create_home_preview(changed)
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertNotEqual(second["plan_id"], first["plan_id"])
        self.assertEqual(self.wait(second["job_id"])["status"], "succeeded")
        self.assertEqual(len(workflow.project_view(first["project_id"])["renders"]), 2)
        self.assertEqual(db.get("plans", second["plan_id"])["version"], 2)
        self.model_mock.assert_not_called()

    def test_invalid_or_reference_media_creates_no_project(self):
        payload = self.payload()
        payload["plan"]["clips"][0]["start"] = 1
        payload["plan"]["clips"][0]["end"] = 1.4
        with self.assertRaises(ValueError):
            home_api.create_home_preview(payload)
        self.assertEqual(db.records("projects"), [])
        payload = self.payload()
        db.update("assets", self.assets[0]["id"], {"purpose": "reference"})
        with self.assertRaisesRegex(ValueError, "样片"):
            home_api.create_home_preview(payload)
        self.assertEqual(db.records("projects"), [])
        self.assertEqual(db.records("plans"), [])
        self.assertEqual(db.records("jobs"), [])


if __name__ == "__main__":
    unittest.main()
