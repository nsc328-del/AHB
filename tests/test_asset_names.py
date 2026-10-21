import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
import storage as db


class AssetNameTest(unittest.TestCase):
    def test_rename_preserves_media_and_rejects_invalid_or_stale_changes(self):
        previous_data = db.DATA
        with tempfile.TemporaryDirectory(prefix="asset-name-") as directory:
            try:
                db.DATA = Path(directory)
                db.initialize()
                path = db.DATA / "assets" / "source.mp4"
                original_bytes = b"immutable source bytes"
                path.write_bytes(original_bytes)
                original = db.create("assets", {
                    "name": "reference.mp4", "kind": "video", "path": str(path),
                    "url": db.media_url(path), "sha256": "fixture-digest",
                    "analysis": {"summary": "现有分析", "tags": ["测试"]},
                })
                client = TestClient(app)
                endpoint = f"/api/assets/{original['id']}/name"
                response = client.patch(endpoint, json={
                    "name": "  纸艺样片.mp4  ", "expected_name": "reference.mp4",
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["name"], "纸艺样片.mp4")
                self.assertNotIn("path", response.json())
                current = db.get("assets", original["id"])
                self.assertEqual(current["original_name"], "reference.mp4")
                for key in ("path", "url", "sha256", "analysis"):
                    self.assertEqual(current[key], original[key])
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(client.get(f"/api/assets/{original['id']}").json()["name"], "纸艺样片.mp4")
                for name in ("", " ", "../other.mp4", "bad\nname", "a" * 241, None):
                    with self.subTest(name=name):
                        self.assertEqual(client.patch(endpoint, json={"name": name}).status_code, 400)
                stale = client.patch(endpoint, json={"name": "旧页面改名", "expected_name": "reference.mp4"})
                self.assertEqual(stale.status_code, 400)
                self.assertEqual(db.get("assets", original["id"])["name"], "纸艺样片.mp4")
                self.assertEqual(client.patch("/api/assets/missing/name", json={"name": "新名称"}).status_code, 404)
            finally:
                db.DATA = previous_data


if __name__ == "__main__":
    unittest.main()
