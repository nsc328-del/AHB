"""Offline contracts for request prefix stability, budget gating and reuse."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
import task_context


def response(value):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}]}


class TaskContextTest(unittest.TestCase):
    def test_generation_reuses_real_skill_and_schema_prefix_across_changed_requests(self):
        shot = {"id": "one", "label": "纸艺", "script": "俯拍白桌上的纸方块展开。", "duration": 6,
                "purpose": "展开", "continuity": "白桌", "visual": "俯拍", "audio": "纸声",
                "mode": "T2VA", "reference_asset_ids": [], "first_asset_id": "", "last_asset_id": "",
                "prompt": "integrated_multimodal_description: [Shot 1] A blue paper cube unfolds on a white table.\n"
                          "overall_soundscape: Paper rustling.\nnon_diegetic_music: N/A"}
        board = {"title": "纸艺", "summary": "展开", "character_anchors": [], "scene_anchors": [],
                 "missing_inputs": [], "shots": [shot]}
        bodies = []
        def reply(provider, method, endpoint, body):
            bodies.append(copy.deepcopy(body))
            return response(board)
        with patch.object(providers, "_request", side_effect=reply):
            first = providers.create_generation_storyboard(
                {"skill_id": "papercraft-stop-motion-explainer", "prompt": "展开纸方块", "duration": 6, "shot_count": 1}, [])
            providers.create_generation_storyboard(
                {"duration": 6, "prompt": "改成纸船", "skill_id": "papercraft-stop-motion-explainer"}, [])
        self.assertEqual(bodies[0]["instructions"], bodies[1]["instructions"])
        self.assertEqual(bodies[0]["text"], bodies[1]["text"])
        texts = [body["input"][0]["content"][0]["text"] for body in bodies]
        self.assertEqual(texts[0].split(',"request":')[0], texts[1].split(',"request":')[0])
        self.assertTrue(texts[0].startswith('{"creative_reference":'))
        self.assertIn("references/base-en.txt", texts[0])
        self.assertNotEqual(texts[0], texts[1])
        self.assertEqual(first["request_context"]["budget_kind"], "conservative_estimate_not_provider_usage")
        self.assertNotIn("total_tokens", first["request_context"])

    def test_canonical_input_and_budget_gate_never_truncate_evidence_or_call_provider(self):
        left = {"request": {"prompt": "改一处", "duration": 6}, "assets": [{"z": 2, "a": 1}],
                "creative_reference": "固定规则"}
        right = {"creative_reference": "固定规则", "assets": [{"a": 1, "z": 2}],
                 "request": {"duration": 6, "prompt": "改一处"}}
        self.assertEqual(task_context.stable_json(left), task_context.stable_json(right))
        self.assertEqual(json.loads(task_context.stable_json(left)), left)
        schema = providers._object({"summary": providers._string()})
        with patch.object(providers, "_request") as network:
            with self.assertRaisesRegex(providers.ProviderError, "上下文预算"):
                providers._json_response("large", schema, "fixed", "x" * task_context.INPUT_BUDGET)
        network.assert_not_called()
        metadata = task_context.request_metadata("test", "deepseek-flash", "fixed", "content", schema,
                [{"label": "frame", "sha256": "hash"}], reasoning_effort="none", output_reserve=10000)
        self.assertEqual(metadata["image_token_reserve"], 1024)
        self.assertEqual(metadata["output_token_reserve"], 10000)

    def test_checkpoint_replays_same_context_without_request_and_keeps_metadata_private(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = {"_analysis_cache_dir": directory}
            schema = providers._object({"summary": providers._string()})
            args = (asset, "reference_semantics", schema, "private test instruction",
                    {"script": "private evidence text"}, None, lambda value: None)
            with patch.object(providers, "_request", return_value=response({"summary": "complete"})) as network:
                first, original = providers._analysis_response(*args)
                second, replay = providers._analysis_response(*args)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(first, second)
            self.assertTrue(replay["cache_hit"])
            self.assertEqual(original["request_context"], replay["request_context"])
            metadata = next(Path(directory).glob("*.request.json")).read_text()
            self.assertNotIn("private evidence text", metadata)
            self.assertNotIn("private test instruction", metadata)


if __name__ == "__main__":
    unittest.main()
