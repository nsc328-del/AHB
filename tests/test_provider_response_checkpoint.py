"""Malformed paid output remains local evidence, never a validated result."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import usage


class ProviderResponseCheckpointTest(unittest.TestCase):
    def test_optional_frame_label_is_sent_and_only_labeled_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame_path = root/"frame.jpg"
            frame_path.write_bytes(b"offline-image")
            frame = {"path": str(frame_path), "time": 1.25}
            schema = providers._object({"summary": providers._string()})
            name, system, context = "reference_visual_reconcile", "fixed", {}
            asset = {"_analysis_cache_dir": directory}
            fingerprint = {"model": providers.DEEPSEEK_MODEL, "name": name, "schema": schema,
                           "system": system, "context": context, "evidence": [
                               {"time": 1.25, "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest()}]}
            old_key = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            wire_labels = []
            def reply(provider, method, endpoint, body):
                wire_labels.append(body["input"][0]["content"][1]["text"])
                return {"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"summary":"valid"}'}]}]}
            label = "同段顺序联系表，格内时间才是证据，不是新切点"
            with patch.object(providers, "_data_url", return_value="data:image/jpeg;base64,b2ZmbGluZQ=="), \
                 patch.object(providers, "_request", side_effect=reply):
                providers._analysis_response(asset, name, schema, system, context, [frame], lambda value: None)
                self.assertTrue((root/f"{old_key}.json").is_file())
                _, receipt = providers._analysis_response(
                    asset, name, schema, system, context, [dict(frame, label=label)], lambda value: None)
                self.assertFalse(receipt["cache_hit"])
                _, replay = providers._analysis_response(
                    asset, name, schema, system, context, [dict(frame, label=label)], lambda value: None)
                self.assertTrue(replay["cache_hit"])
            self.assertEqual(wire_labels, ["证据帧时间：1.250 秒", label])

    def test_reasoning_effort_is_scoped_and_high_cache_cannot_reuse_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = {"_analysis_cache_dir": directory}
            schema = providers._object({"summary": providers._string()})
            calls = []
            def reply(provider, method, endpoint, body):
                effort = body["reasoning"]["effort"]
                self.assertEqual(body["max_output_tokens"], 10000 if effort == "none" else 16000)
                calls.append(effort)
                return {"status": "completed", "output": [
                    {"type": "reasoning", "content": [{"type": "reasoning_text",
                                                      "text": "SYNTHETIC_DO_NOT_PERSIST_REASONING"}]},
                    {"type": "message", "content": [{"type": "output_text",
                                                    "text": json.dumps({"summary": effort})}]}]}
            args = ("reference_visual_reconcile", schema, "fixed-system", {"fixed": True}, None, lambda value: None)
            with patch.object(providers, "_request", side_effect=reply):
                normal, _ = providers._analysis_response(asset, *args)
                explicit, normal_receipt = providers._analysis_response(
                    dict(asset, _analysis_reasoning_effort="none"), *args)
                high, high_receipt = providers._analysis_response(
                    dict(asset, _analysis_reasoning_effort="high"), *args)
                replay, cached = providers._analysis_response(dict(asset, _analysis_reasoning_effort="high"), *args)
            self.assertEqual(calls, ["none", "high"])
            self.assertEqual(normal, explicit)
            self.assertTrue(normal_receipt["cache_hit"])
            self.assertFalse(high_receipt["cache_hit"])
            self.assertEqual(high, replay)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(high["summary"], "high")
            self.assertEqual(providers._ANALYSIS_REASONING_EFFORT.get(), "none")
            for path in root.glob("*.json"):
                self.assertNotIn("SYNTHETIC_DO_NOT_PERSIST_REASONING", path.read_text())
            with patch.object(providers, "_request", side_effect=providers.ProviderError("offline failure")):
                with self.assertRaisesRegex(providers.ProviderError, "offline failure"):
                    providers._analysis_response({"_analysis_reasoning_effort": "high"}, *args)
            self.assertEqual(providers._ANALYSIS_REASONING_EFFORT.get(), "none")

    def test_incomplete_text_is_auditable_but_never_promoted_to_validated_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = providers._object({"summary": providers._string()})
            name, system, context = "reference_visual_reconcile", "fixed", {"section": 1}
            # Even syntactically valid output is unusable when the provider
            # says the response is incomplete.
            text = '{"summary":"partial result"}'
            reply = {"status": "incomplete", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": text}]}]}
            fingerprint = {"model": providers.DEEPSEEK_MODEL, "name": name, "schema": schema,
                           "system": system, "context": context, "evidence": [],
                           "reasoning_effort": "low", "max_output_tokens": 16000}
            key = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            with usage.context(job_id="offline-incomplete"), patch.object(providers, "_request", return_value=reply) as request:
                for _ in range(2):
                    with self.assertRaisesRegex(providers.ProviderError, "未完成"):
                        providers._analysis_response(
                            {"_analysis_cache_dir": directory, "_analysis_reasoning_effort": "low"},
                            name, schema, system, context, None, lambda value: None)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(request.call_args.args[3]["max_output_tokens"], 16000)
            saved = json.loads((root/f"{key}.response.json").read_text())
            self.assertEqual(saved["result"]["output_text"], text)
            self.assertEqual(saved["result"]["provider_status"], "incomplete")
            self.assertEqual(saved["source_job_id"], "offline-incomplete")
            self.assertFalse((root/f"{key}.json").exists())
            self.assertFalse((root/f"{key}.raw.json").exists())
            self.assertEqual(providers._ANALYSIS_REASONING_EFFORT.get(), "none")

    def test_invalid_json_is_saved_before_parse_without_validated_cache_or_secret_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = providers._object({"summary": providers._string()})
            name, system, context = "reference_visual_verify", "test-only instruction", {"local": True}
            malformed = '{"summary": "unfinished'
            reply = {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": malformed}]}],
                "unneeded_provider_metadata": "DO_NOT_PERSIST_METADATA"}
            fingerprint = {"model": providers.DEEPSEEK_MODEL, "name": name, "schema": schema,
                           "system": system, "context": context, "evidence": []}
            key = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            with usage.context(job_id="offline-failed-job"), patch.object(providers, "_request", return_value=reply):
                with self.assertRaisesRegex(providers.ProviderError, "有效 JSON"):
                    providers._analysis_response({"_analysis_cache_dir": str(root)}, name, schema,
                                                 system, context, None, lambda value: None)
            saved_path = root/f"{key}.response.json"
            saved = json.loads(saved_path.read_text())
            self.assertEqual(saved["signature"], key)
            self.assertEqual(saved["source_job_id"], "offline-failed-job")
            self.assertEqual(saved["result"]["output_text"], malformed)
            self.assertEqual(saved["result"]["provider_status"], "completed")
            self.assertNotIn("DO_NOT_PERSIST_METADATA", saved_path.read_text())
            self.assertFalse((root/f"{key}.json").exists())
            self.assertFalse((root/f"{key}.raw.json").exists())
            self.assertIsNone(providers._ANALYSIS_TEXT_SINK.get())
            # A retry does not guess or repair the text; a new deliberate
            # response is needed and the previous paid attempt remains saved.
            valid = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"summary":"valid"}'}]}]}
            with usage.context(job_id="offline-retry"), patch.object(providers, "_request", return_value=valid) as request:
                value, receipt = providers._analysis_response({"_analysis_cache_dir": str(root)}, name, schema,
                                                              system, context, None, lambda value: None)
            self.assertEqual(request.call_count, 1)
            self.assertFalse(receipt["cache_hit"])
            self.assertEqual(value["summary"], "valid")
            attempts = [json.loads(path.read_text()) for path in root.glob("*.response.json")]
            self.assertTrue(any(row["result"]["output_text"] == malformed for row in attempts))
            # Simulate interruption after receiving valid text but before
            # writing parsed/validated checkpoints. Exact replay costs no call.
            (root/f"{key}.json").unlink()
            (root/f"{key}.raw.json").unlink()
            with patch.object(providers, "_request") as request:
                value, receipt = providers._analysis_response({"_analysis_cache_dir": str(root)}, name, schema,
                                                              system, context, None, lambda value: None)
            request.assert_not_called()
            self.assertTrue(receipt["cache_hit"])
            self.assertEqual(receipt["source_job_id"], "offline-retry")
            self.assertEqual(value["summary"], "valid")


if __name__ == "__main__":
    unittest.main()
