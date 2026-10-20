"""Offline source/metric/version contracts. No network and no production writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage as db
import publication_data as p


class PublicationDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for replacement in (patch.object(db, "DATA", self.root / "studio"),
                            patch.object(db, "TABLES", db.TABLES | p.TABLES)):
            replacement.start()
            self.addCleanup(replacement.stop)
        db.initialize()
        self.source = {"kind": "test", "label": "离线来源", "location": "test.sqlite"}
        self.pub = {"id": "publication-1", "platform": "douyin", "account": "测试账号",
                    "title": "标题", "published_at": "2026-10-19T12:00:00+08:00",
                    "status": "已发布", "duration_seconds": 12, "last_seen": "2026-10-20T12:00:00+08:00"}

    def payload(self, metrics=None, captured_at="2026-10-20T12:00:00+08:00"):
        metrics = metrics if metrics is not None else {
            "播放": {"value": 12000, "raw": "1.2万", "approximate": True},
            "点赞": {"value": 0, "raw": "0"},
            "完播率": {"value": .25, "raw": "25%"},
            "人均观看时长": {"value": 0, "zero_may_mean_unavailable": True},
        }
        return {"publications": [self.pub], "observations": [{
            "publication_id": self.pub["id"], "captured_at": captured_at, "source": "实采测试",
            "metrics_json": json.dumps(metrics), "raw_json": json.dumps({"title": "当次标题", "metrics": metrics}),
        }], "read_mode": "read_only"}

    def test_idempotent_history_units_ambiguity_and_no_observation_cap(self):
        payload = self.payload()
        self.assertEqual(p._ingest(payload, self.source), (1, 1))
        self.assertEqual(p._ingest(payload, self.source), (0, 0))
        detail = p.history_detail(self.pub["id"])
        case = detail["case"]
        self.assertEqual(case["age_hours"], 24)
        self.assertIsNone(case["binding"])
        self.assertTrue(case["metrics"]["views"]["approximate"])
        self.assertEqual(case["metrics"]["likes"]["value"], 0)
        self.assertFalse(case["metrics"]["likes"]["unknown"])
        self.assertEqual(case["metrics"]["completion_rate"]["unit"], "ratio")
        for source_value in (.123, 12.3):
            self.assertAlmostEqual(p._metric_set({"完播率": {"value": source_value, "raw": "12.3%"}}, {})["completion_rate"]["value"], .123)
        self.assertIsNone(case["metrics"]["saves"]["value"])
        self.assertTrue(case["metrics"]["avg_watch_seconds"]["unknown"])
        self.assertEqual(case["metrics"]["avg_watch_seconds"]["unknown_reason"], "zero_may_mean_unavailable")
        unit_unknown = p._metric_set({"人均观看时长": {"value": 1250}}, {})
        self.assertIsNone(unit_unknown["avg_watch_seconds"]["value"])
        self.assertEqual(unit_unknown["avg_watch_seconds"]["raw"], 1250)
        self.assertEqual(unit_unknown["avg_watch_seconds"]["unknown_reason"], "unit_unknown")
        older = self.payload({"播放": {"value": 10}}, "2026-10-19T13:00:00+08:00")
        p._ingest(older, self.source)
        self.assertEqual(p.history_detail(self.pub["id"])["case"]["metrics"]["views"]["value"], 12000)
        # The source already has thousands of observations: never use storage.records' 5000 cap.
        with db.connection() as conn:
            for index in range(5001):
                p._put(conn, "publication_observations", {"id": f"old-{index}", "publication_id": self.pub["id"],
                    "captured_at": "2026-10-19T12:00:00+08:00", "captured_epoch": index, "age_hours": 0,
                    "metrics": {}, "source": self.source})
        self.assertEqual(len(p.history_detail(self.pub["id"])["observations"]), 5003)
        self.assertEqual(p.history_status()["observations_count"], 5003)
        self.assertEqual(p.list_history(query="标题")["total"], 1)
        self.assertEqual(p.list_history(account="其他账号")["total"], 0)
        self.assertIsNone(p.list_history(limit=1)["next_offset"])
        self.assertEqual(p.list_history(offset=1)["cases"], [])

    def test_readonly_source_sync_failures_preserve_cache_and_import_is_atomic(self):
        source = self.root / "source.sqlite"
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE publications(id,platform,account,published_at,title,status,duration_seconds,last_seen)")
            conn.execute("CREATE TABLE observations(publication_id,captured_at,source,metrics_json,raw_json)")
            conn.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?,?)",
                tuple(self.pub[k] for k in ("id", "platform", "account", "published_at", "title", "status", "duration_seconds", "last_seen")))
            o = self.payload()["observations"][0]
            conn.execute("INSERT INTO observations VALUES(?,?,?,?,?)", tuple(o.values()))
        original = source.read_bytes()
        copied = p._read_sqlite(source)
        self.assertEqual(source.read_bytes(), original)
        with patch.object(p, "_fetch_source", return_value=(copied, self.source)):
            self.assertEqual(p.sync_history()["status"], "ok")
        with patch.object(p, "_fetch_source", side_effect=p.SourceError("连接不可用")):
            failed = p.sync_history()
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["cases_count"], 1)
        self.assertEqual(failed["error"], "连接不可用")
        broken = self.payload()
        broken["observations"].append({**broken["observations"][0], "publication_id": "unknown"})
        with self.assertRaises(p.SourceError):
            p._ingest(broken, self.source)
        self.assertEqual(p.history_status()["observations_count"], 1)
        self.assertIn("source_changed_during_read", p.REMOTE_EXPORT)
        self.assertIn("has_wal()", p.REMOTE_EXPORT)

    def test_business_workspace_keeps_other_sources_out(self):
        p._ingest(self.payload(), self.source)
        self.pub = {**self.pub, "id": "star-1", "account": "星尚账号"}
        p._ingest(self.payload(), {"kind": "alpha_sqlite", "label": "星尚", "location": p.SOURCE_LOCATION})
        cases = p.list_history(workspace_id="xingshang")
        self.assertEqual([c["id"] for c in cases["cases"]], ["star-1"])
        self.assertEqual(cases["facets"]["accounts"], ["星尚账号"])
        workspace = p.business_workspaces()["workspaces"][0]
        self.assertEqual(workspace["cases_count"], 1)
        self.assertTrue(workspace["view_only"])
        with self.assertRaises(KeyError):
            p.list_history(workspace_id="not-a-workspace")

    def test_exact_render_binding_confirmation_scope_hash_and_revoke_audit(self):
        p._ingest(self.payload(), self.source)
        db.create("projects", {"id": "project-1"})
        db.create("projects", {"id": "project-2"})
        plan = db.create("plans", {"id": "plan-1", "project_id": "project-1", "version": 1, "clips": []})
        path = db.DATA / "renders" / "render.mp4"
        path.write_bytes(b"synthetic render fixture")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        db.create("assets", {"id": "asset-1", "path": str(path), "sha256": sha, "kind": "video"})
        db.create("renders", {"id": "render-1", "project_id": "project-1", "plan_id": plan["id"],
                  "asset_id": "asset-1", "sha256": sha, "version": 1, "url": "/api/media/renders/render.mp4"})
        with self.assertRaises(ValueError):
            p.bind_publication("project-1", self.pub["id"], "render-1", False)
        with self.assertRaises(ValueError):
            p.bind_publication("project-2", self.pub["id"], "render-1", True)
        binding = p.bind_publication("project-1", self.pub["id"], "render-1", True)
        again = p.bind_publication("project-1", self.pub["id"], "render-1", True)
        self.assertEqual(binding["id"], again["id"])
        self.assertEqual(binding["sha256"], sha)
        self.assertTrue(binding["publication"]["version_confirmed"])
        self.assertEqual(binding["publication"]["binding"]["id"], binding["id"])
        self.assertEqual(binding["plan_sha256"], p._hash(plan))
        self.assertNotIn("plan_snapshot", binding)
        self.assertEqual(len(p.project_publications("project-1")["bindings"]), 1)
        self.assertTrue(p.history_detail(self.pub["id"])["case"]["version_confirmed"])
        db.create("renders", {"id": "render-2", "project_id": "project-1", "plan_id": plan["id"],
                  "asset_id": "asset-1", "sha256": sha, "version": 1})
        with self.assertRaises(p.BindingConflict):
            p.bind_publication("project-1", self.pub["id"], "render-2", True)
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            p.bind_publication("project-1", self.pub["id"], "render-1", True)
        revoked = p.unbind_publication("project-1", binding["id"])
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(p.project_publications("project-1")["bindings"], [])
        self.assertEqual(len(p._records("publication_events")), 2)
        self.assertEqual(len(p.history_detail(self.pub["id"])["observations"]), 1)
        self.assertEqual(p.unbind_publication("project-1", binding["id"])["status"], "revoked")
        self.assertEqual(len(p._records("publication_events")), 2)


if __name__ == "__main__":
    unittest.main()
