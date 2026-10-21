import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from scripts import demo_data


class DemoDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.snapshot = self.root / "demo"
        self.target = self.root / "restored"
        self.source = self.file("assets/source.mp4", b"immutable original media bytes")
        self.render = self.file("renders/final.mp4", b"immutable processed media bytes")
        self.evidence = self.root / "output/review/sequence.jpg"
        self.evidence.parent.mkdir(parents=True)
        self.evidence.write_bytes(b"external review evidence")
        (self.evidence.parent / "receipt.json").write_text(json.dumps({"source": "/Users/previous-collaborator/Documents/AHB-old-branch/data/source.mp4", "evidence": str(self.evidence), "status": "failed"}))
        (self.evidence.parent / "excluded.txt").write_text("excluded historical report")
        (self.evidence.parent / "report.html").write_text('<p>Original analysis stays unchanged.</p><video src="../../data/assets/source.mp4?v=1&amp;x=2#clip"></video><a href="sequence.jpg">Evidence</a>')
        self.plan = {"id": "plan-1", "clips": [{"id": "clip-1", "asset_id": "source-1", "start": 1, "end": 2}], "cut_reason": "Keep the full action"}
        self.timeline = {"fps": 30, "frame_count": 30, "clips": [{"clip_id": "clip-1", "asset_id": "source-1", "start_frame": 0, "end_frame": 30, "source_start_seconds": 1, "source_end_seconds": 2}]}
        self.manifest = {"schema_version": 2, "plan_id": "plan-1", "plan": self.plan, "output_sha256": self.sha(self.render), "compiled_timeline": self.timeline, "assets": [{"asset_id": "source-1", "sha256": self.sha(self.source)}]}
        self.manifest_path = self.file("renders/final.manifest.json", json.dumps(self.manifest).encode())
        self.file("renders/report/index.html", b'<video src="preview.mp4"></video>')
        self.file("renders/report/preview.mp4", b"report-only dependency")
        self.file("tmp/temporary-secret.txt", b"not part of the demonstration")
        self.file("animation_sources/audit.json", json.dumps({"output_path": str(self.data / "generations/removed-failed-output.mp4"), "status": "failed"}).encode())
        self.records = {
            "assets": [{"id": "source-1", "path": str(self.source), "url": "/api/media/assets/source.mp4", "sha256": self.sha(self.source)}],
            "plans": [self.plan],
            "renders": [{"id": "render-1", "plan_id": "plan-1", "url": "/api/media/renders/final.mp4", "sha256": self.sha(self.render), "manifest_url": "/api/media/renders/final.manifest.json", "compiled_timeline": self.timeline}],
            "analyses": [{"id": "analysis-1", "asset_id": "source-1", "result": {"evidence": str(self.evidence)}}],
            "jobs": [{"id": "job-1", "status": "failed", "error": "Original failure retained", "provider_state": {"task_id": "existing-task-id"}}],
            "usage_records": [{"id": "usage-1", "input_tokens": 123, "output_tokens": 45, "status": "failed"}],
            "publication_cases": [{"id": "case-secret", "title": "PRIVATE BUSINESS HISTORY"}],
            "publication_observations": [{"id": "obs-secret", "publication_id": "case-secret"}],
            "publication_bindings": [],
        }
        self.write_database()

    def file(self, name, content):
        path = self.data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_database(self):
        with sqlite3.connect(self.data / "studio.sqlite") as conn:
            for table, records in self.records.items():
                conn.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
                for record in records:
                    conn.execute(f"INSERT INTO {table} VALUES(?,?,?,?)", (record["id"], json.dumps(record), "original-created-time", "original-updated-time"))

    def export(self):
        return demo_data.export_demo(self.snapshot, source_data=self.data, project_root=self.root,
                                     include_output=["output/review"], exclude_output=["output/review/excluded.txt"])

    def test_roundtrip_preserves_media_decisions_and_excludes_history(self):
        source_db_hash = self.sha(self.data / "studio.sqlite")
        report = self.export()
        self.assertEqual(report["render_manifests"], 1)
        self.assertEqual(report["html_local_links"], 3)
        self.assertEqual(report["counts"]["publication_cases"], 0)
        records_text = (self.snapshot / "records.json").read_text()
        self.assertNotIn("PRIVATE BUSINESS HISTORY", records_text)
        self.assertNotIn(str(self.root), records_text)
        self.assertFalse((self.snapshot / "files/tmp").exists())
        self.assertFalse((self.snapshot / "external/output/review/excluded.txt").exists())
        receipt = (self.snapshot / "external/output/review/receipt.json").read_text()
        self.assertNotIn("previous-collaborator", receipt)
        self.assertIn(demo_data.HISTORY_MARKER, receipt)
        self.assertIn("AHB-old-branch/data/source.mp4", receipt)
        metadata = json.loads((self.snapshot / "manifest.json").read_text())
        self.assertNotIn("excluded_publication_counts", metadata)
        self.assertTrue(metadata["path_transforms"])
        self.assertEqual((self.snapshot / "files/renders/final.manifest.json").read_bytes(), self.manifest_path.read_bytes())
        self.assertTrue((self.snapshot / "files/renders/report/preview.mp4").exists())
        demo_data.import_demo(self.snapshot, data_dir=self.target)
        demo_data.check_demo(self.snapshot, data_dir=self.target)
        exported_report = self.snapshot / "external/output/review/report.html"
        restored_report = self.target / "frames/imported-evidence/output/review/report.html"
        for document in (exported_report, restored_report):
            text = document.read_text()
            self.assertIn("Original analysis stays unchanged.", text)
            self.assertIn("?v=1&amp;x=2#clip", text)
            link = next(demo_data.HTML_LINK.finditer(text))[3]
            source = (document.parent / unquote(urlsplit(link).path)).resolve()
            self.assertEqual(source.read_bytes(), self.source.read_bytes())
        self.assertNotEqual(exported_report.read_text(), restored_report.read_text())
        self.assertEqual(self.sha(self.target / "assets/source.mp4"), self.sha(self.source))
        self.assertEqual(self.sha(self.target / "renders/final.mp4"), self.sha(self.render))
        self.assertEqual(self.sha(self.data / "studio.sqlite"), source_db_hash)
        with sqlite3.connect(self.target / "studio.sqlite") as conn:
            self.assertEqual(conn.execute("select count(*) from publication_cases").fetchone()[0], 0)
            row = conn.execute("select data,created_at,updated_at from analyses").fetchone()
            path = Path(json.loads(row[0])["result"]["evidence"])
            self.assertEqual(path.read_bytes(), self.evidence.read_bytes())
            self.assertTrue(path.is_relative_to(self.target))
            self.assertEqual(row[1:], ("original-created-time", "original-updated-time"))
            self.assertEqual(json.loads(conn.execute("select data from plans").fetchone()[0]), self.plan)

    def test_credentials_redacted_and_tampering_detected_without_overwrite(self):
        with sqlite3.connect(self.data / "studio.sqlite") as conn:
            record = self.records["jobs"][0] | {"provider_state": {"api_key": "example-sensitive-value", "download": "https://media.example/video?signature=private-signature&expires=200", "task_id": "existing-task-id"}}
            conn.execute("update jobs set data=?", (json.dumps(record),))
        report = self.export()
        self.assertEqual(report["redacted_fields"], 2)
        text = (self.snapshot / "records.json").read_text()
        self.assertNotIn("example-sensitive-value", text)
        self.assertNotIn("private-signature", text)
        self.assertIn("existing-task-id", text)
        self.target.mkdir()
        sentinel = self.target / "do-not-overwrite"
        sentinel.write_bytes(b"existing")
        with self.assertRaises(demo_data.DemoError):
            demo_data.import_demo(self.snapshot, data_dir=self.target)
        self.assertEqual(sentinel.read_bytes(), b"existing")
        (self.snapshot / "files/assets/source.mp4").write_bytes(b"changed")
        with self.assertRaises(demo_data.DemoError):
            demo_data.check_demo(self.snapshot)

    def test_installed_records_and_frozen_evidence_are_verified(self):
        self.export()
        demo_data.import_demo(self.snapshot, data_dir=self.target)
        with sqlite3.connect(self.target / "studio.sqlite") as conn:
            altered = self.plan | {"cut_reason": "Different reason"}
            conn.execute("update plans set data=?", (json.dumps(altered),))
        with self.assertRaises(demo_data.DemoError):
            demo_data.check_demo(self.snapshot, data_dir=self.target)
        # An invalid frozen timeline is rejected before an import directory exists.
        frozen = self.manifest | {"compiled_timeline": {"fps": 60}}
        self.manifest_path.write_text(json.dumps(frozen))
        different = self.root / "invalid-demo"
        with self.assertRaises(demo_data.DemoError):
            demo_data.export_demo(different, source_data=self.data, project_root=self.root)
        self.assertFalse(different.exists())
        self.manifest_path.write_text(json.dumps(self.manifest))
        with sqlite3.connect(self.data / "studio.sqlite") as conn:
            contaminated = self.plan | {"reference_case_snapshot": [{"title": "PRIVATE BUSINESS HISTORY"}]}
            conn.execute("update plans set data=?", (json.dumps(contaminated),))
        with self.assertRaisesRegex(demo_data.DemoError, "Nonempty publication history reference"):
            demo_data.export_demo(different, source_data=self.data, project_root=self.root)
        self.assertFalse(different.exists())


if __name__ == "__main__":
    unittest.main()
