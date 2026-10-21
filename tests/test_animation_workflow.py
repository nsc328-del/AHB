"""Animation state boundaries: offline models, actual FFmpeg media and recovery."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import animation_workflow as animations
import animation_api
import media
import storage as db
import workflow


class AnimationWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="animation-workflow-")
        self.original_data = db.DATA
        db.DATA = Path(self.tmp.name) / "data"
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_DATA": str(db.DATA)})
        self.tables = patch.object(db, "TABLES", db.TABLES | {"animation_projects", "animation_revisions"})
        self.ratios = patch.dict(workflow.RATIOS, {"16:9": (160, 90)})
        for patched in (self.env, self.tables, self.ratios):
            patched.start()
        db.initialize()
        self.calls, self.plan_calls, self.revise_calls, self.model_sources = [], 0, 0, []
        self.fail_once = set()
        self.pause_started, self.pause_release = threading.Event(), threading.Event()
        self.pause_revise = False
        self.fixture = Path(self.tmp.name) / "fixture.mp4"
        media.run([media.FFMPEG, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x90:r=30",
                   "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", self.fixture])
        self.provider_patch = patch.object(animations, "provider", return_value=self)
        self.provider_patch.start()

    def tearDown(self):
        self.pause_release.set()
        deadline = time.monotonic() + 30
        while workflow.ACTIVE and time.monotonic() < deadline:
            time.sleep(.02)
        self.provider_patch.stop()
        for patched in (self.ratios, self.tables, self.env):
            patched.stop()
        db.DATA = self.original_data
        self.tmp.cleanup()

    def plan(self, params):
        self.plan_calls += 1
        return {"title": "两段动画", "scenes": [
            {"id": "one", "title": "开场", "description": "蓝色矩形进入", "duration": 2},
            {"id": "two", "title": "结尾", "description": "蓝色矩形缩小", "duration": 2}]}

    def revise_scene(self, scene, context, instruction):
        self.revise_calls += 1
        if self.pause_revise:
            self.pause_started.set()
            if not self.pause_release.wait(15):
                raise ValueError("离线测试等待超时")
        return {**scene, "description": instruction}

    def generate_and_render(self, scene, context, output_path, progress=None, prior_source=None, instruction=None):
        self.calls.append({"scene": copy.deepcopy(scene), "context": copy.deepcopy(context), "output_path": str(output_path),
                           "prior_source": prior_source, "instruction": instruction})
        source = Path(context["source_path"])
        if not source.is_file():
            self.model_sources.append((context["revision_id"], scene["id"]))
            source.write_text("<!doctype html><title>PRIVATE_SOURCE</title>" + scene["description"])
        if scene["id"] in self.fail_once:
            self.fail_once.remove(scene["id"])
            raise ValueError("离线模拟：渲染失败，源码已保存")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.fixture, output_path)
        html = source.read_text()
        return {"source_html": html, "source_hash": hashlib.sha256(html.encode()).hexdigest(), "renderer": {}, "audit": {"passed": True}}

    def request(self, **kwargs):
        return {"submission_id": str(uuid.uuid4()), **kwargs}

    def wait(self, response, expected="succeeded"):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = db.get("jobs", response["job_id"])
            if job["status"] not in {"queued", "running"}:
                self.assertEqual(job["status"], expected, job)
                return job
            time.sleep(.03)
        self.fail("动画任务未完成")

    def new(self):
        response = animations.create(self.request(brief="测试动画", style="type", ratio="16:9", duration=4))
        self.wait(response)
        return animations.get_animation(response["animation_id"])

    def test_concurrent_submission_real_film_and_private_versioned_sources(self):
        request = self.request(brief="测试动画", style="type", ratio="16:9", duration=4)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(animations.create, [copy.deepcopy(request), copy.deepcopy(request)]))
        self.assertEqual(a["job_id"], b["job_id"])
        self.wait(a)
        view = animations.get_animation(a["animation_id"])
        self.assertEqual(self.plan_calls, 1)
        revision = view["revision"]
        request = self.request(revision_id=revision["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda payload: animations.render(view["id"], payload), [copy.deepcopy(request), copy.deepcopy(request)]))
        self.assertEqual(a["job_id"], b["job_id"])
        job = self.wait(a)
        self.assertEqual(job["result"]["revision_id"], revision["id"])
        complete = animations.get_animation(view["id"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(complete["revision"]["renders"]), 2)
        asset = db.get("assets", complete["revision"]["output"]["asset_id"])
        self.assertAlmostEqual(media.probe(asset["path"])["duration"], 4, delta=.1)
        workspace = workflow.project_view(complete["workspace_project_id"])
        self.assertEqual(len(workspace["renders"]), 1)
        self.assertEqual(len(workspace["plan"]["clips"]), 2)
        self.assertNotIn("PRIVATE_SOURCE", json.dumps(complete))
        self.assertNotIn("source_path", json.dumps(complete))
        download = animation_api.animation_source(view["id"], revision["id"])
        self.assertTrue(download.headers["content-type"].startswith("application/json"))
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(download.headers["x-content-type-options"], "nosniff")
        self.assertIn("PRIVATE_SOURCE", download.body.decode())
        self.assertNotIn("source_path", download.body.decode())
        self.assertEqual(animations._progress_fraction({"stage": "frames", "progress": .5}), .5)
        self.assertEqual(animations._progress_fraction({"stage": "source", "progress": 5}), .05)
        portrait = animations._context({**db.get("animation_revisions", revision["id"]), "ratio": "4:5"})
        self.assertEqual((portrait["width"], portrait["height"]), (800, 1000))
        private = db.get("animation_revisions", revision["id"])["scenes"][0]["source_path"]
        with self.assertRaises(FileNotFoundError):
            db.safe_media_path(str(Path(private).relative_to(db.DATA)))
        replay = animations.render(view["id"], self.request(revision_id=revision["id"]))
        self.assertEqual(replay["job_id"], a["job_id"])
        changed = copy.deepcopy(complete["revision"]["scenes"])
        changed[1]["description"] = "矩形变小后消失"
        edited = animations.edit(view["id"], revision["id"], {"expected_revision_id": revision["id"], "scenes": changed})
        self.assertNotEqual(edited["revision"]["id"], revision["id"])
        self.assertEqual([r["scene_id"] for r in edited["revision"]["renders"]], ["one"])
        self.assertEqual(db.get("animation_revisions", revision["id"])["scenes"][1]["description"], "蓝色矩形缩小")
        changed_private = db.get("animation_revisions", edited["revision"]["id"])["scenes"][1]
        self.assertEqual(changed_private["prior_source_path"], db.get("animation_revisions", revision["id"])["scenes"][1]["source_path"])
        self.assertNotIn("source_path", changed_private)
        finished = self.wait(animations.render(view["id"], self.request(revision_id=edited["revision"]["id"])))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1]["prior_source"], animations._source(changed_private["prior_source_path"]))
        self.assertIn("矩形变小后消失", self.calls[-1]["instruction"])
        self.assertEqual(len(workflow.project_view(complete["workspace_project_id"])["renders"]), 2)
        self.assertEqual(finished["result"]["revision_id"], edited["revision"]["id"])

    def test_failed_scene_retry_preserves_success_and_same_provider_fingerprint(self):
        view = self.new()
        self.fail_once.add("two")
        failed = self.wait(animations.render(view["id"], self.request(revision_id=view["revision"]["id"])), "failed")
        partial = animations.get_animation(view["id"])["revision"]
        self.assertEqual([r["scene_id"] for r in partial["renders"]], ["one"])
        request = self.request(job_id=failed["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda payload: animations.retry(view["id"], payload), [copy.deepcopy(request), copy.deepcopy(request)]))
        self.assertEqual(a["job_id"], b["job_id"])
        self.wait(a)
        self.assertEqual([call["scene"]["id"] for call in self.calls], ["one", "two", "two"])
        self.assertEqual(self.calls[1], self.calls[2])
        self.assertEqual(len(self.model_sources), 2)
        self.assertEqual(animations.retry(view["id"], self.request(job_id=failed["id"]))["job_id"], a["job_id"])
        with self.assertRaises(ValueError):
            animations.retry(view["id"], {**request, "job_id": a["job_id"]})

    def test_late_scene_revision_never_overwrites_new_manual_head(self):
        view = self.new()
        first = self.wait(animations.render(view["id"], self.request(revision_id=view["revision"]["id"])))
        base = animations.get_animation(view["id"])["revision"]
        self.pause_revise = True
        revise = animations.revise(view["id"], self.request(revision_id=base["id"], scene_id="two", instruction="矩形变为圆形"))
        self.assertTrue(self.pause_started.wait(10))
        manual = copy.deepcopy(base["scenes"])
        manual[0]["description"] = "用户的新开场"
        edited = animations.edit(view["id"], base["id"], {"expected_revision_id": base["id"], "scenes": manual})
        self.pause_release.set()
        job = self.wait(revise)
        head = animations.get_animation(view["id"])
        self.assertEqual(head["current_revision_id"], edited["revision"]["id"])
        repaired = animations.get_animation(view["id"], job["result"]["revision_id"])["revision"]
        self.assertEqual(repaired["parent_revision_id"], base["id"])
        self.assertEqual(repaired["scenes"][0]["description"], "蓝色矩形进入")
        self.assertEqual(self.revise_calls, 1)
        self.assertEqual([c["scene"]["id"] for c in self.calls], ["one", "two", "two"])
        self.assertIn("PRIVATE_SOURCE", self.calls[-1]["prior_source"])
        self.assertEqual(self.calls[-1]["instruction"], "矩形变为圆形")
        # A higher-numbered historical branch cannot become the current film.
        self.wait(animations.render(view["id"], self.request(revision_id=edited["revision"]["id"])))
        workspace = workflow.project_view(head["workspace_project_id"])
        self.assertEqual(workspace["plan"]["origin"]["revision_id"], edited["revision"]["id"])
        with self.assertRaises(ValueError):
            animations.edit(view["id"], base["id"], {"expected_revision_id": base["id"], "scenes": manual})
        broken = copy.deepcopy(head["revision"]["scenes"])
        broken[0]["id"] = "replacement"
        with self.assertRaises(ValueError):
            animations.edit(view["id"], head["current_revision_id"], {"expected_revision_id": head["current_revision_id"], "scenes": broken})


if __name__ == "__main__":
    unittest.main()
