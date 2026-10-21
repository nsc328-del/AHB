"""Offline reference analysis: full frame coverage, absolute time and evidence."""
from __future__ import annotations

import json
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import providers


def segment(start, end, times, role="同一叙事"):
    return {"start": start, "end": end, "timecode": "", "description": "仅依据画面",
            "tags": ["实拍"], "role": role, "shot_type": "中景", "motion": "单帧无法确认",
            "evidence_times": times}


class ReferenceChunkTest(unittest.TestCase):
    def test_dense_long_video_keeps_every_frame_and_merges_across_windows(self):
        # Dense early cutting and sparse later frames exercise both frame and
        # duration limits; the final tail must not disappear through subsampling.
        times = [i / 4 for i in range(180)] + list(range(45, 210, 3)) + [209.99]
        frames = [{"time": t, "path": "/offline/frame.jpg"} for t in times]
        asset = {"id": "long", "kind": "video", "duration": 210,
                 "analysis": {"summary": "must-not-prime-the-model"},
                 "audio_status": "succeeded", "transcript": {"language": "zh", "text": "完整转录",
                     "segments": [{"start": 5, "end": 7, "text": "开头", "words": [{"probability": .9}]},
                                  {"start": 150, "end": 153, "text": "后段", "words": [{"probability": .9}]}]}}
        requests = []

        def respond(name, schema, system, user, images=None):
            context = json.loads(user)
            requests.append((name, context, images))
            self.assertNotIn("analysis", context["asset"])
            if name == "reference_content_merge":
                # One semantic topic crosses all computational chunk seams.
                return {"summary": "整片主题", "tags": ["实拍"],
                        "content_segments": [segment(0, 210, [times[0], times[-1]])],
                        "structure": [{"role": "连续主题", "start": 0, "end": 210, "purpose": "同一叙事"}]}
            window = context["window"]
            part = segment(window["context_start"], window["context_end"],
                           [images[0]["time"], images[-1]["time"]])
            self.assertLessEqual(len(images), 48)
            self.assertLessEqual(window["end"] - window["start"], 45)
            for utterance in (context["asset"]["transcript"] or {}).get("segments", []):
                self.assertGreater(utterance["end"], window["context_start"])
                self.assertLess(utterance["start"], window["context_end"])
            return {"summary": "局部", "tags": ["实拍"], "segments": [part],
                    "structure": [{"role": "连续主题", "start": part["start"], "end": part["end"], "purpose": "局部"}]}

        with patch.object(providers, "_json_response", side_effect=respond):
            result = providers.analyze_asset(asset, frames, reference=True)
        self.assertEqual(result["analyzed_frame_times"], times)
        covered = [t for item in result["analysis_chunks"] for t in item["core_frame_times"]]
        self.assertEqual(covered, times)
        self.assertEqual(result["analysis_chunks"][0]["start"], 0)
        self.assertEqual(result["analysis_chunks"][-1]["end"], 210)
        self.assertEqual(len(result["content_segments"]), 1)
        self.assertGreater(len(result["segments"]), 1)
        self.assertEqual(len(requests), len(result["analysis_chunks"]) + 1)
        self.assertIsNone(requests[-1][2])
        self.assertEqual(requests[-1][1]["asset"]["transcript"]["text"], "开头 后段")

    def test_short_reference_is_one_request_with_distinct_content_layer(self):
        frames = [{"time": value, "path": "/offline/frame.jpg"} for value in [0.1, 2, 5, 11.9]]
        response = {"summary": "平行地点展示", "tags": ["旅行"],
                    "segments": [segment(0, 5, [.1, 2]), segment(5, 12, [5, 11.9])],
                    "content_segments": [segment(0, 12, [.1, 11.9], "地点串联")],
                    "structure": [{"role": "地点串联", "start": 0, "end": 12, "purpose": "平行展示"}]}
        asset = {"kind": "video", "duration": 12, "transcript": None,
                 "audio_evidence": {"status": "uncertain", "used": False}}
        with patch.object(providers, "_json_response", return_value=response) as model:
            result = providers.analyze_asset(asset, frames, reference=True)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(len(result["content_segments"]), 1)
        self.assertFalse(result["audio_analyzed"])
        self.assertNotIn("shots", result)
        context = json.loads(model.call_args.args[3])
        self.assertIsNone(context["asset"]["transcript"])
        self.assertEqual(context["audio_evidence"]["status"], "uncertain")

    def test_global_merge_cannot_invent_visual_evidence(self):
        frames = [{"time": value, "path": "/offline/frame.jpg"} for value in [0, 20, 40, 60, 80]]

        def respond(name, schema, system, user, images=None):
            if name == "reference_content_merge":
                return {"summary": "虚构证据", "tags": [],
                        "content_segments": [segment(0, 81, [72.321])], "structure": []}
            window = json.loads(user)["window"]
            return {"summary": "局部", "tags": [], "segments": [
                segment(window["start"], window["end"],
                        [image["time"] for image in images if window["start"] <= image["time"] <= window["end"]])],
                    "structure": []}

        with patch.object(providers, "_json_response", side_effect=respond):
            with self.assertRaisesRegex(providers.ProviderError, "真实证据帧"):
                providers.analyze_asset({"kind": "video", "duration": 81}, frames, reference=True)

    def test_uncertain_transcript_cannot_reenter_via_saved_analysis(self):
        raw = {"text": "不可信的歌词猜测", "language": "en", "language_probability": .3,
               "segments": [{"start": 0, "end": 4, "text": "不可信的歌词猜测",
                             "words": [{"probability": .9}]}]}
        asset = {"kind": "video", "duration": 4, "transcript": raw,
                 "analysis": {"summary": "保留画面结论", "transcript": raw,
                              "segments": [{"start": 0, "end": 4, "transcript": raw}]}}
        public = providers._public_asset(asset)
        self.assertIsNone(public["transcript"])
        self.assertEqual(public["analysis"]["summary"], "保留画面结论")
        self.assertNotIn("不可信的歌词猜测", json.dumps(public, ensure_ascii=False))
        asset["transcript"] = {"text": "缺少置信度的旧转录", "segments": [{"start": 0, "end": 4, "text": "旧转录"}]}
        self.assertIsNone(providers._public_asset(asset)["transcript"])

    def test_misaligned_evidence_is_excluded_but_unsupported_claim_is_rejected(self):
        asset = {"kind": "video", "duration": 10}
        partly_supported = {"segments": [segment(0, 3, [0, 2, 8])]}
        providers._validate_analysis(partly_supported, asset, [0, 2, 8], None)
        self.assertEqual(partly_supported["segments"][0]["evidence_times"], [0, 2])
        self.assertEqual(partly_supported["segments"][0]["excluded_evidence_times"], [8])
        with self.assertRaisesRegex(providers.ProviderError, r"0.000–3.000.*8.000"):
            providers._validate_analysis({"segments": [segment(0, 3, [8])]}, asset, [0, 2, 8], None)
        # A nonexistent timestamp is never silently discarded as merely outside.
        with self.assertRaisesRegex(providers.ProviderError, "真实证据帧"):
            providers._validate_analysis({"segments": [segment(0, 3, [0, 7])]}, asset, [0, 2, 8], None)

    def test_failed_later_window_resumes_without_repaying_valid_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = []
            for time in [0, 20, 40, 60, 80]:
                path = root / f"{time}.jpg"
                path.write_bytes(f"offline-image-at-{time}".encode())
                frames.append({"time": time, "path": str(path)})
            asset = {"id": "long", "kind": "video", "duration": 81,
                     "_analysis_cache_dir": root / "cache"}
            attempted, fail_once = [], [True]

            def respond(name, schema, system, user, images=None):
                context = json.loads(user)
                attempted.append((name, context.get("window", {}).get("start")))
                if name == "reference_content_merge":
                    return {"summary": "全片", "tags": [], "structure": [],
                            "content_segments": [segment(0, 81, [0, 80])]}
                window = context["window"]
                if window["start"] > 0 and fail_once[0]:
                    fail_once[0] = False
                    raise providers.ProviderError("离线模拟：后段临时失败")
                return {"summary": "局部", "tags": [], "structure": [],
                        "segments": [segment(window["start"], window["end"],
                                             [image["time"] for image in images])]}

            with patch.object(providers, "_json_response", side_effect=respond):
                with self.assertRaisesRegex(providers.ProviderError, "后段临时失败"):
                    providers.analyze_asset(asset, frames, reference=True)
                self.assertEqual(len(list((root / "cache").glob("*.json"))), 1)
                attempted.clear()
                resumed = providers.analyze_asset(asset, frames, reference=True)
                self.assertEqual(attempted, [("material_analysis", 45), ("reference_content_merge", None)])
                self.assertEqual(resumed["analysis_call_count"], 2)
                self.assertEqual(resumed["retained_call_count"], 1)
                attempted.clear()
                asset.update(audio_status="running", updated_at="changed", analysis={"summary": "stale"})
                replayed = providers.analyze_asset(asset, frames, reference=True)
                self.assertEqual(attempted, [])
                self.assertEqual(replayed["analysis_call_count"], 0)
                self.assertEqual(replayed["retained_call_count"], 3)
                # The same filename with changed image bytes is a new model input.
                Path(frames[0]["path"]).write_bytes(b"changed-first-image")
                providers.analyze_asset(asset, frames, reference=True)
                self.assertEqual(attempted, [("material_analysis", 0)])

    def test_extra_display_fields_normalize_and_raw_response_survives_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = {"kind": "video", "duration": 10, "_analysis_cache_dir": directory}
            schema = providers._analysis_schema(True, global_summary=True)
            raw = {"summary": "真实摘要", "tags": ["实拍"],
                   "content_segments": [segment(0, 10, [0, 10])],
                   "structure": [{"role": "展开", "start": 0, "end": 10, "purpose": "推进主题",
                                  "timecode": "多余展示字段"}]}
            def reply(value):
                return {"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}]}
            validate = lambda value: providers._validate_analysis(value, asset, [0, 10], None)
            with patch.object(providers, "_request", return_value=reply(raw)) as wire:
                clean, receipt = providers._analysis_response(
                    asset, "reference_content_merge", schema, "offline", {}, None, validate)
                self.assertNotIn("timecode", clean["structure"][0])
                self.assertEqual(receipt["discarded_fields"], ["$.structure[0].timecode"])
                raw_file = next(Path(directory).glob("*.raw.json"))
                self.assertEqual(json.loads(raw_file.read_text())["result"], raw)
                # A saved pre-validation response can be recovered without any HTTP call.
                raw_file.with_name(raw_file.name.replace(".raw.json", ".json")).unlink()
                replay, cached = providers._analysis_response(
                    asset, "reference_content_merge", schema, "offline", {}, None, validate)
                self.assertTrue(cached["cache_hit"])
                self.assertEqual(replay, clean)
                self.assertEqual(wire.call_count, 1)

            invalid = copy.deepcopy(raw)
            invalid["content_segments"][0].pop("evidence_times")
            with patch.object(providers, "_request", return_value=reply(invalid)):
                with self.assertRaisesRegex(providers.ProviderError, "required"):
                    providers._analysis_response(
                        asset, "reference_content_merge", schema, "offline", {"case": "missing evidence"},
                        None, validate)
            saved = [json.loads(path.read_text())["result"] for path in Path(directory).glob("*.raw.json")]
            self.assertIn(invalid, saved)


if __name__ == "__main__":
    unittest.main()
