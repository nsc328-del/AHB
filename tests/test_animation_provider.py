"""Paid animation responses: recover complete fenced JSON, reject bad receipts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import animation_provider as animation
import providers


class AnimationProviderCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="animation-response-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.valid = {
            "html": '<!doctype html><html><head><meta charset="UTF-8"></head><body><div id="stage">离线动画</div><script>window.renderAt = t => {};</script></body></html>',
            "design_notes": "离线响应，不调用供应商",
        }

    def request(self, checkpoint):
        return animation._request("animation_source", animation.SOURCE_SCHEMA,
                                  "offline-test", {"scene": "synthetic"}, checkpoint=checkpoint)

    def test_complete_fenced_response_recovers_once_then_replays_without_call(self):
        checkpoint = self.root / "attempt-1"
        text = "```json\n" + json.dumps(self.valid, ensure_ascii=False) + "\n```"
        prior_sink = providers._ANALYSIS_TEXT_SINK.get()

        def rejected_json(*args, **kwargs):
            providers._ANALYSIS_TEXT_SINK.get()(text, "completed")
            raise providers.ProviderError("离线模拟：返回内容不是有效 JSON")

        with patch.object(providers, "_json_response", side_effect=rejected_json) as request:
            result = self.request(checkpoint)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result, self.valid)
        self.assertEqual(json.loads(checkpoint.with_suffix(".json").read_text()), self.valid)
        self.assertEqual(json.loads(checkpoint.with_suffix(".response.json").read_text()),
                         {"status": "completed", "text": text})
        # Simulate the parsed checkpoint being lost after the paid response.
        checkpoint.with_suffix(".json").unlink()
        with patch.object(providers, "_json_response", side_effect=AssertionError("Cached response must not be billed again")) as request:
            self.assertEqual(self.request(checkpoint), self.valid)
        request.assert_not_called()
        self.assertIs(providers._ANALYSIS_TEXT_SINK.get(), prior_sink)

    def test_exact_source_patch_preserves_unrelated_code_and_requires_unique_anchor(self):
        original = '<h1>旧文案</h1><script>window.renderAt=t=>draw(t);</script>'
        changed = animation.apply_source_edits(original, [{'old':'<h1>旧文案</h1>','new':'<h1>新文案</h1>'}])
        self.assertEqual(changed, original.replace('旧文案', '新文案'))
        for anchor in ('', '不存在', 'a'):
            with self.assertRaises(ValueError):
                animation.apply_source_edits('aa', [{'old':anchor,'new':'X'}])

    def test_noncomplete_invalid_schema_and_partial_wrappers_are_not_accepted(self):
        valid_text = json.dumps(self.valid, ensure_ascii=False)
        cases = [
            ("incomplete", valid_text),
            (None, valid_text),
            ("completed", json.dumps({"html": self.valid["html"]})),
            ("completed", json.dumps({**self.valid, "unexpected": "not allowed"})),
            ("completed", "说明：\n```json\n" + valid_text + "\n```"),
            ("completed", "```json\n" + valid_text + "\n```\n额外内容"),
            ("completed", '{"html":"unfinished'),
            ("completed", ""),
        ]
        for index, (status, text) in enumerate(cases):
            with self.subTest(status=status, index=index):
                checkpoint = self.root / f"invalid-{index}"
                prior_sink = providers._ANALYSIS_TEXT_SINK.get()
                def rejected(*args, **kwargs):
                    providers._ANALYSIS_TEXT_SINK.get()(text, status)
                    raise providers.ProviderError("离线模拟：响应不可用")
                with patch.object(providers, "_json_response", side_effect=rejected) as request:
                    with self.assertRaisesRegex(providers.ProviderError, "响应不可用"):
                        self.request(checkpoint)
                self.assertEqual(request.call_count, 1)
                self.assertTrue(checkpoint.with_suffix(".response.json").is_file())
                self.assertFalse(checkpoint.with_suffix(".json").exists())
                self.assertIs(providers._ANALYSIS_TEXT_SINK.get(), prior_sink)

    def test_unusable_saved_completed_response_requires_a_valid_new_response(self):
        checkpoint = self.root / "saved-invalid"
        checkpoint.with_suffix(".response.json").write_text(json.dumps({
            "status": "completed", "text": '```json\n{"design_notes":"缺少源码"}\n```'}))
        with patch.object(providers, "_json_response", return_value=self.valid) as request:
            self.assertEqual(self.request(checkpoint), self.valid)
        request.assert_called_once()
        self.assertEqual(json.loads(checkpoint.with_suffix(".json").read_text()), self.valid)


if __name__ == "__main__":
    unittest.main()
