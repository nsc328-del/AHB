"""Offline wire contracts: all HTTP uses MockTransport and all keys are synthetic.

Authoritative contracts checked 2026-10-20:
https://api-docs.deepseek.com/api/create-response/
https://platform.minimax.cn/docs/api-reference/video-generation-v2-create
https://platform.minimax.cn/docs/api-reference/video-generation-v2-query
Run: python3 -m unittest discover -s tests -p test_providers.py -v
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import providers as p
from skill_catalog import ROOT as SKILL_ROOT, get_skills, skill_instructions


class ProviderContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="provider-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for replacement in (
            patch.object(p, "CONFIG_PATH", self.root / "private" / "providers.json"),
            patch.object(p, "_legacy_keys", return_value={}),
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "MINIMAX_API_KEY": ""}),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.requests = []
        self.handler = lambda request: self.fail("Unexpected HTTP request")
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: self.handler(request))
        replacement = patch.object(p.httpx, "Client", side_effect=lambda **kw: real_client(transport=transport, trust_env=False, **kw))
        replacement.start()
        self.addCleanup(replacement.stop)
        p.save_config({"deepseek_api_key": "fake-deepseek-secret-000", "minimax_api_key": "fake-minimax-secret-111"})
        self.image = self.root / "shape.png"
        Image.new("RGB", (640, 480), (25, 95, 205)).save(self.image)
        self.asset = {"id": "img-1", "name": "测试图", "kind": "image", "path": str(self.image),
                      "duration": 0, "width": 640, "height": 480}

    def deepseek_reply(self, data, status="completed"):
        def handle(request):
            self.requests.append(request)
            self.assertEqual(str(request.url), "https://api.deepseek.com/responses")
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["text"]["format"]["type"], "json_schema")
            self.assertIn("schema", body["text"]["format"])
            self.assertNotIn("response_format", body)
            return httpx.Response(200, json={"status": status, "output": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "not an output JSON"}]},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(data)}]},
            ]})
        self.handler = handle

    def test_private_config_and_official_skill_snapshots(self):
        status = p.get_config()
        self.assertTrue(status["deepseek_configured"] and status["minimax_configured"])
        self.assertEqual(status["minimax_base_url"], "https://api.minimax.cn")
        self.assertNotIn("secret", json.dumps(status))
        self.assertEqual(p.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(p.CONFIG_PATH.parent.stat().st_mode & 0o777, 0o700)
        p.save_config({"deepseek_api_key": ""})
        self.assertTrue(p.get_config()["deepseek_configured"])
        with self.assertRaises(p.ConfigurationError):
            p.save_config({"deepseek_base_url": "https://example.invalid"})
        skills = get_skills()
        self.assertEqual(len(skills), 9)
        for skill in skills:
            with self.subTest(skill=skill["id"]):
                self.assertIn("本项目适配规则", skill_instructions(skill["id"]))
                self.assertTrue(skill["source_hash"])

    def test_visual_response_wire_schema_and_transcript_evidence(self):
        data = {"summary": "蓝色画面", "tags": ["蓝色"], "segments": [{
            "start": 0, "end": 0, "timecode": "00:00", "description": "纯色",
            "tags": ["蓝色"], "role": "背景", "shot_type": "静态", "motion": "无",
            "evidence_times": [0],
        }]}
        self.deepseek_reply(data)
        result = p.analyze_asset(self.asset, [{"path": str(self.image), "time": 0}])
        self.assertFalse(result["audio_analyzed"])
        body = json.loads(self.requests[-1].content)
        self.assertEqual(body["input"][0]["role"], "user")
        image = next(part for part in body["input"][0]["content"] if part["type"] == "input_image")
        self.assertIsInstance(image["image_url"], str)
        self.assertTrue(image["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(image["detail"], "original")
        self.assertNotIn(str(self.root), body["input"][0]["content"][0]["text"])
        bad = copy.deepcopy(data)
        bad["segments"][0]["evidence_times"] = [10]
        self.deepseek_reply(bad)
        with self.assertRaises(p.ProviderError):
            p.analyze_asset(self.asset, [{"path": str(self.image), "time": 0}])
        self.deepseek_reply({"summary": "missing schema fields"})
        with self.assertRaises(p.ProviderError):
            p.analyze_asset(self.asset, [{"path": str(self.image), "time": 0}])
        audio = {"id": "audio-1", "kind": "audio", "duration": 5, "audio_status": "succeeded",
                 "transcript": {"text": "早上好", "segments": [{"start": 1, "end": 2, "text": "早上好"}], "model": "local-test"}}
        data["segments"][0].update(start=1, end=2, evidence_times=[1, 2], shot_type="不适用", motion="不适用")
        self.deepseek_reply(data)
        self.assertTrue(p.analyze_asset(audio, [])["audio_analyzed"])
        self.assertIn("早上好", json.loads(self.requests[-1].content)["input"][0]["content"][0]["text"])

    def test_plan_real_intervals_partial_selection_and_reference_transcript(self):
        video = {"id": "video-1", "kind": "video", "duration": 10, "width": 640, "height": 480}
        other = {**video, "id": "video-2"}
        reference = {**video, "id": "reference", "analysis": {"summary": "开场到收束"},
                     "audio_status": "succeeded", "transcript": {"text": "真实参考台词", "segments": []}}
        clip = {"asset_id": "video-1", "start": 2, "end": 5, "duration": 3,
                "label": "动作", "caption": "", "role": "开场", "transition": "cut"}
        data = {"title": "测试计划", "summary": "来源连续", "clips": [clip, {**clip, "asset_id": "video-2"}], "music_asset_id": ""}
        options = {"selection": [{"asset_id": "video-1", "start": 2, "end": 6}], "audio_incomplete": False}
        self.deepseek_reply(data)
        previous = {**data, "decision_events": [{"reason": "local-only"}],
                    "reference_cases": [{"title": "private-history"}], "origin": {"batch_id": "local"}}
        result = p.create_plan("剪辑", [video, other], options, reference=reference, previous=previous)
        self.assertEqual(result["duration"], 6)
        context = json.loads(json.loads(self.requests[-1].content)["input"][0]["content"][0]["text"])
        self.assertEqual(context["reference"]["transcript"]["text"], "真实参考台词")
        self.assertEqual(context["previous"]["clips"], data["clips"])
        self.assertNotIn("decision_events", context["previous"])
        self.assertNotIn("reference_cases", context["previous"])
        self.assertNotIn("origin", context["previous"])
        data["clips"][0].update(start=1, end=4)
        self.deepseek_reply(data)
        with self.assertRaises(p.ProviderError):
            p.create_plan("剪辑", [video, other], options)
        data["clips"][0].update(start=9, end=12)
        self.deepseek_reply(data)
        with self.assertRaises(p.ProviderError):
            p.create_plan("剪辑", [video, other], {})

    def test_skill_storyboard_translates_ids_to_generation_contract(self):
        data = {"title": "产品镜头", "summary": "一个镜头", "character_anchors": [], "scene_anchors": ["产品图"],
                "missing_inputs": [], "shots": [{"id": "shot-1", "label": "细节", "duration": 4,
                "script": "固定白色桌面构图，蓝色方块位于中央，镜头在四秒内缓慢推近后停止；无配乐。",
                "purpose": "细节展示", "continuity": "保持产品颜色", "visual": "缓慢推进", "audio": "无旁白",
                "mode": "Ref2VA", "reference_asset_ids": [self.asset["id"]], "first_asset_id": "", "last_asset_id": "",
                "prompt": "subject_definitions: <Subject 1> is the blue object in <Picture 1>.\n"
                          "summary: [reference generation] A close-up shows <Subject 1>.\n"
                          "retention_analysis: <Subject 1>: fully_preserved - keep its blue color.\n"
                          "detailed_description: Live-action. [Shot 1] The camera slowly pushes toward <Subject 1> on a white table.\n"
                          "overall_soundscape: Quiet room tone.\nnon_diegetic_music: N/A"}]}
        self.deepseek_reply(data)
        params = {"skill_id": "minimalist-product-ad-generator", "prompt": "只要一个4秒产品镜头，不要音乐", "duration": 4}
        result = p.create_generation_storyboard(params, [self.asset])
        request = json.loads(self.requests[-1].content)
        context = json.loads(request["input"][0]["content"][0]["text"])
        self.assertTrue(all(context["request"][key] == value for key, value in params.items()))
        self.assertEqual(context["creative_reference"], skill_instructions(params["skill_id"]))
        self.assertNotIn(context["creative_reference"], request["instructions"])
        self.assertIn("用户明确4秒单镜头", request["instructions"])
        supplied_schema = json.loads(request["instructions"].split("JSON Schema:\n", 1)[1])
        self.assertEqual(supplied_schema, request["text"]["format"]["schema"])
        self.assertEqual(result["scene_notes"], ["产品图"])
        self.assertEqual([s["id"] for s in result["skill_sources"]], ["h3-prompt-writing", params["skill_id"]])
        for relative in ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"):
            self.assertIn((SKILL_ROOT / "h3-prompt-writing" / "upstream" / relative).read_text(), context["creative_reference"])
        shot = result["shots"][0]
        self.assertEqual(shot["title"], "细节")
        self.assertEqual(shot["reference_image_ids"], [self.asset["id"]])
        body, _ = p._generation_body(shot, [self.asset])
        self.assertEqual(body["content"][1]["role"], "reference_image")
        invalid = copy.deepcopy(data)
        invalid["shots"][0]["reference_asset_ids"] = "private-model-value-must-not-leak"
        self.deepseek_reply(invalid)
        with self.assertRaises(p.ProviderError) as caught:
            p.create_generation_storyboard(params, [self.asset])
        self.assertIn("$.shots[0].reference_asset_ids", str(caught.exception))
        self.assertIn("type", str(caught.exception))
        self.assertNotIn("private-model-value-must-not-leak", str(caught.exception))
        # Minimal-environment validation must preserve the same safe error location.
        with self.assertRaises(p.ProviderError) as fallback:
            p._validate_shape(invalid, supplied_schema)
        self.assertIn("$.shots[0].reference_asset_ids", str(fallback.exception))
        self.assertNotIn("private-model-value-must-not-leak", str(fallback.exception))

    def test_free_generation_official_formats_modes_duration_and_reference_binding(self):
        base = ("integrated_multimodal_description: [Shot 1] Live-action, a static close-up shows a blue cube on a white table. "
                "A soft shadow moves slowly right and stops at the end of the four-second shot.\n"
                "overall_soundscape: N/A\nnon_diegetic_music: N/A")
        ref = ("subject_definitions: <Subject 1> is the blue cube in <Picture 1>.\n"
               "summary: [reference generation] <Subject 1> stands on a table.\n"
               "retention_analysis: <Subject 1>: fully_preserved - retain the blue surface.\n"
               "detailed_description: Live-action. [Shot 1] The camera holds a static close-up on <Subject 1> as its shadow moves slowly.\n"
               "overall_soundscape: N/A\nnon_diegetic_music: N/A")
        second = {**self.asset, "id": "img-2"}
        cases = [
            ("T2VA", {}, base, "", "", []),
            ("I2VA", {"first_frame_id": "img-1"}, "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + base, "img-1", "", []),
            ("L2VA", {"last_frame_id": "img-1"}, "How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the 4.00-second mark of the target video.\n\n" + base, "", "img-1", []),
            ("FL2VA", {"first_frame_id": "img-1", "last_frame_id": "img-2"}, "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 4.00-second mark of the target video.\n\n" + base, "img-1", "img-2", []),
            ("Ref2VA", {"reference_image_ids": ["img-1"]}, ref, "", "", ["img-1"]),
        ]
        for mode, extra, prompt, first, last, refs in cases:
            with self.subTest(mode=mode):
                data = {"title": "自由生成", "summary": "一镜到底", "character_anchors": [], "scene_anchors": [],
                    "missing_inputs": [], "shots": [{"id": "shot-1", "label": "蓝色方块", "script": "白色桌面上的蓝色方块，固定机位拍摄四秒；影子缓慢右移后停止，全程静音。",
                    "duration": 4, "purpose": "展示", "continuity": "完整连续动作", "visual": "影子缓移", "audio": "静音",
                    "mode": mode, "first_asset_id": first, "last_asset_id": last, "reference_asset_ids": refs, "prompt": prompt}]}
                self.deepseek_reply(data)
                params = {"skill_id": "h3-prompt-writing", "mode": mode, "duration": 4, "prompt": "蓝色方块四秒静音镜头", **extra}
                result = p.create_generation_storyboard(params, [self.asset, second])
                self.assertEqual(result["shots"][0]["script"], data["shots"][0]["script"])
                self.assertEqual(result["shots"][0]["mode"], mode)
                self.assertEqual(len(result["skill_sources"]), 1)
                wire = json.loads(self.requests[-1].content)
                context = json.loads(wire["input"][0]["content"][0]["text"])
                self.assertEqual(context["request"]["first_asset_id"], first)
                self.assertEqual(context["request"]["last_asset_id"], last)
                self.assertEqual(context["request"]["reference_asset_ids"], refs)
                for relative in ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"):
                    self.assertIn((SKILL_ROOT / "h3-prompt-writing" / "upstream" / relative).read_text(), context["creative_reference"])
        self.assertEqual(result["shots"][0]["reference_bindings"], [{"label": "<Picture 1>", "asset_id": "img-1"}])
        for bad in ({"duration": float("nan")}, {"duration": 16}, {"mode": "I2VA"}, {"mode": "Ref2VA", "reference_image_ids": ["missing"]}):
            before = len(self.requests)
            with self.assertRaises(p.ProviderError):
                p.create_generation_storyboard({"duration": 4, "mode": "T2VA", **bad}, [self.asset])
            self.assertEqual(len(self.requests), before)
        for replacement in ({"prompt": "A beautiful blue object."}, {"prompt": ref.replace("<Picture 1>", "<Picture 2>")},
                            {"duration": 5}, {"reference_asset_ids": ["missing"]},
                            {"prompt": ref.replace("overall_soundscape:", "non_diegetic_music:")},
                            {"prompt": ref.replace("overall_soundscape:", "[Shot 2] At 00:04.000, the camera cuts to a wall.\noverall_soundscape:")}):
            bad = copy.deepcopy(data)
            bad["shots"][0].update(replacement)
            self.deepseek_reply(bad)
            with self.assertRaises(p.ProviderError):
                p.create_generation_storyboard(params, [self.asset, second])

    def test_three_stills_keep_two_second_durations_and_reject_invalid_ranges(self):
        assets = [{**self.asset, "id": f"image-{i}"} for i in range(3)]
        data = {"title": "几何色彩", "summary": "三图各两秒", "music_asset_id": "", "clips": [
            {"asset_id": asset["id"], "start": i * 2, "end": (i + 1) * 2, "duration": 2,
             "label": asset["id"], "caption": "", "role": "展示", "transition": "cut"}
            for i, asset in enumerate(assets)
        ]}
        self.deepseek_reply(data)
        result = p.create_plan("三图各两秒", assets, {"duration": 6})
        self.assertEqual(result["duration"], 6)
        self.assertEqual([c["asset_id"] for c in result["clips"]], [a["id"] for a in assets])
        self.assertEqual([(c["start"], c["end"], c["duration"]) for c in result["clips"]], [(0, 2, 2)] * 3)
        for interval in ((-1, 1, 2), (2, 4, 1), (0, .19, .19), (0, 121, 121), (2, 2, 0)):
            with self.subTest(interval=interval):
                invalid = copy.deepcopy(data)
                invalid["clips"][0].update(zip(("start", "end", "duration"), interval))
                self.deepseek_reply(invalid)
                with self.assertRaises(p.ProviderError):
                    p.create_plan("三图各两秒", assets, {"duration": 6})

    def test_h3_modes_use_content_roles_and_current_cn_endpoint(self):
        video_path = self.root / "transport-fixture.mp4"
        audio_path = self.root / "transport-fixture.wav"
        video_path.write_bytes(b"offline wire fixture, media probing is covered separately")
        audio_path.write_bytes(b"offline wire fixture")
        video = {"id": "v", "path": str(video_path), "kind": "video", "duration": 3, "width": 640, "height": 480, "video_codec": "h264", "audio_codec": "aac"}
        audio = {"id": "a", "path": str(audio_path), "kind": "audio", "duration": 3}
        asset2 = {**self.asset, "id": "img-2"}
        assets = [self.asset, asset2, video, audio]
        base = {"prompt": "光线缓缓移动", "duration": 5, "resolution": "768P", "ratio": "9:16"}
        cases = [
            ("T2VA", {}, []),
            ("I2VA", {"first_frame_id": "img-1"}, ["first_frame"]),
            ("L2VA", {"last_frame_id": "img-1"}, ["last_frame"]),
            ("FL2VA", {"first_frame_id": "img-1", "last_frame_id": "img-2"}, ["first_frame", "last_frame"]),
            ("Ref2VA", {"reference_image_ids": ["img-1"], "reference_video_ids": ["v"], "reference_audio_ids": ["a"]}, ["reference_image", "reference_video", "reference_audio"]),
        ]
        for mode, extra, roles in cases:
            with self.subTest(mode=mode):
                def handle(request):
                    self.requests.append(request)
                    self.assertEqual(str(request.url), "https://api.minimax.cn/v2/video_generation")
                    body = json.loads(request.content)
                    self.assertNotIn("input", body)
                    self.assertEqual(body["model"], "MiniMax-H3")
                    self.assertEqual(body["content"][0], {"type": "text", "text": base["prompt"]})
                    self.assertEqual([part["role"] for part in body["content"][1:]], roles)
                    for part in body["content"][1:]:
                        self.assertIsInstance(part[part["type"]], dict)
                        self.assertTrue(part[part["type"]]["url"].startswith("data:"))
                    self.assertEqual(body["ratio"], "adaptive" if mode in {"I2VA", "L2VA", "FL2VA"} else "9:16")
                    return httpx.Response(200, json={"task_id": "123456"})
                self.handler = handle
                result = p.create_generation({**base, "mode": mode, **extra}, assets)
                self.assertEqual(result["task_id"], "123456")
                self.assertEqual(result["currency"], "CNY")
        before = len(self.requests)
        for params in ({**base, "mode": "T2VA", "ratio": "adaptive"},
                       {**base, "mode": "Ref2VA", "first_frame_id": "img-1", "reference_image_ids": ["img-2"]},
                       {**base, "mode": "T2VA", "duration": 3}):
            with self.assertRaises(p.ProviderError):
                p.create_generation(params, assets)
        self.assertEqual(len(self.requests), before)

    def test_reviewed_storyboard_prompt_is_rechecked_before_submission(self):
        base = ("integrated_multimodal_description: [Shot 1] A blue cube rests on a white table.\n"
                "overall_soundscape: N/A\nnon_diegetic_music: N/A")
        prefix = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) "
                  "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) "
                  "aligns with the 4.00-second mark of the target video.\n\n")
        assets = [self.asset, {**self.asset, "id": "img-2"}]
        params = {"mode": "FL2VA", "duration": 4, "first_frame_id": "img-1",
                  "last_frame_id": "img-2", "prompt": prefix + base,
                  "require_h3_prompt_contract": True}
        body, _ = p._generation_body(params, assets)
        self.assertEqual(body["content"][0]["text"], params["prompt"])
        self.assertNotIn("require_h3_prompt_contract", body)
        # Invalid edits fail locally; setUp's transport fails any unexpected HTTP.
        for edits in ({"duration": 15}, {"mode": "T2VA", "first_frame_id": "", "last_frame_id": ""},
                      {"prompt": "An unstructured replacement."},
                      {"prompt": prefix + base.replace("overall_soundscape:", "[Shot 2] At 00:04.000, cut.\noverall_soundscape:")}):
            with self.subTest(edits=tuple(edits)):
                with self.assertRaises(p.ProviderError):
                    p.create_generation({**params, **edits}, assets)
        ref = ("subject_definitions: <Subject 1> is the cube in <Picture 2>.\n"
               "summary: [reference generation] Show <Subject 1>.\n"
               "retention_analysis: <Subject 1>: fully_preserved - keep the blue color.\n"
               "detailed_description: [Shot 1] Hold on <Subject 1>.\n"
               "overall_soundscape: N/A\nnon_diegetic_music: N/A")
        with self.assertRaises(p.ProviderError):
            p.create_generation({"mode": "Ref2VA", "duration": 4, "prompt": ref,
                                 "reference_image_ids": ["img-1"], "source": "storyboard"}, assets)
        # Existing direct/manual integrations retain their plain-prompt contract.
        body, _ = p._generation_body({"mode": "T2VA", "duration": 4, "prompt": "A blue cube."}, [])
        self.assertEqual(body["content"][0]["text"], "A blue cube.")

    def test_h3_submission_uncertainty_no_retry_and_query_states(self):
        params = {"mode": "T2VA", "prompt": "测试", "duration": 4, "resolution": "768P", "ratio": "9:16"}
        for status in (400, 402, 429, 503, 408, 302, 200):
            with self.subTest(status=status):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(status, json={"error": "fake-secret-do-not-return"})
                self.handler = handle
                expected = p.ProviderError if status in {400, 402, 429} else p.SubmissionUnknown
                with self.assertRaises(expected) as raised:
                    p.create_generation(params, [])
                self.assertNotIn("fake-secret", str(raised.exception))
                if status in {400, 402, 429}:
                    self.assertNotIsInstance(raised.exception, p.SubmissionUnknown)
                self.assertEqual(len(calls), 1)
        self.handler = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("fake-secret"))
        with self.assertRaises(p.SubmissionUnknown):
            p.create_generation(params, [])
        for status in ("queued", "running", "succeeded", "failed", "cancelled"):
            with self.subTest(task_status=status):
                def query(request):
                    self.assertEqual(request.method, "GET")
                    self.assertEqual(str(request.url), "https://api.minimax.cn/v2/query/video_generation/123456")
                    return httpx.Response(200, json={"task": {"status": status, "content": {"url": "https://filecdn.minimax.chat/fixture.mp4"}, "error": {"message": "fake-secret"}}})
                self.handler = query
                result = p.query_generation("123456")
                self.assertEqual(result["status"], status)
                self.assertNotIn("fake-secret", json.dumps(result))
                self.assertEqual("url" in result, status == "succeeded")


if __name__ == "__main__":
    unittest.main()
