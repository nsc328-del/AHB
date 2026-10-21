"""Offline checks for evidence-bound model review and resumable billing."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import providers
import reference_reasoning as reasoning


def response(identifier="shot", *, direction="left", relative="shared_image_displacement", verify=False):
    row = {
        "id": identifier, "description": "人物保持姿态，背景横向扫过。", "role": "街景人物",
        "evidence_times": [.1, .7], "visible_text": [],
        "structured_motion": {
            "image_direction": direction, "view_change": "horizontal_sweep", "evidence_times": [.1, .7],
            "subject_relative_movement": relative, "subject_evidence_basis": "shared_motion_only",
            "subject_evidence_times": [.1, .7], "subject_observation": "姿态保持，与背景共同位移。",
            "viewpoint": "external", "viewpoint_observation": "人物全身可见。",
        },
    }
    if verify:
        row.update(checks={key: "consistent" for key in ("image_direction", "subject_motion", "viewpoint", "visible_text")},
                   review_note="连续帧支持背景位移；不认定人物步行。")
    return {"sections": [row]}


class ReferenceReasoningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        contacts = patch.object(reasoning, "_temporal_contact_frames", side_effect=lambda asset, windows, images: images)
        self.contact_builder = contacts.start()
        self.addCleanup(contacts.stop)
        root = Path(self.temp.name)
        self.frames = []
        for time in [.1, .7]:
            path = root / f"{time}.jpg"
            path.write_bytes(b"offline-evidence")
            self.frames.append({"path": str(path), "time": time})
        self.asset = {"_analysis_cache_dir": root / "cache", "transcript": "DO_NOT_SEND_SPEECH",
                      "analysis": {"description": "DO_NOT_SEND_OLD_ANSWER"}}
        self.windows = [{"id": "shot", "start": 0, "end": 1, "frame_times": [.1, .7], "cut_times": [],
                         "description": "DO_NOT_SEND_MANUAL_ANSWER",
                         "motion_evidence": {"statistics": {
                             "qualified_pairs": 20, "qualified_pair_fraction": .95,
                             "background_inlier_ratio_median": .93,
                             "coherent_view_change_pair_fraction": .9, "horizontal_sign_agreement": 1,
                             "image_dx_widths_per_second_median": -.5,
                         }, "limits": ["仅为图像位移"]}}]

    def test_model_repairs_direction_and_result_uses_image_coordinate_labels(self):
        requests = []
        def reply(name, schema, system, user, images=None):
            data = json.loads(user)
            requests.append(name)
            self.assertNotIn("DO_NOT_SEND", user)
            self.assertFalse(data["audio_provided"])
            if name == "reference_visual_observe":
                return response(direction="right")
            self.assertIn("image_direction_opposes_reliable_image_flow",
                          data["validation_flags"][0]["codes"])
            return response(verify=True)
        with patch.object(providers, "_json_response", side_effect=reply):
            result, receipt = reasoning.review_sections(self.asset, self.windows, self.frames)
            replay, cached = reasoning.review_sections(self.asset, self.windows, self.frames)
        self.assertEqual(requests, ["reference_visual_observe", "reference_visual_verify"])
        self.assertEqual(receipt["new_provider_calls"], 2)
        self.assertEqual(cached["new_provider_calls"], 0)
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(result, replay)
        motion = result["sections"][0]["motion"]
        self.assertIn("背景主要向画面左侧位移", motion)
        self.assertNotIn("摄影机向左", motion)
        self.assertEqual(result["sections"][0]["verification"]["status"], "verified")

    def test_failed_reconciliation_reuses_paid_review_and_unknown_is_valid(self):
        attempts = []
        fail = [True]
        def reply(name, schema, system, user, images=None):
            attempts.append(name)
            if name == "reference_visual_observe":
                return response()
            if name == "reference_visual_verify":
                return response(direction="right", verify=True)
            if fail[0]:
                raise providers.ProviderError("offline transport failure")
            result = response(direction="unknown", verify=True)
            result["sections"][0]["structured_motion"]["view_change"] = "unknown"
            result["sections"][0]["checks"]["image_direction"] = "uncertain"
            return result
        with patch.object(providers, "_json_response", side_effect=reply):
            with self.assertRaisesRegex(providers.ProviderError, "offline transport"):
                reasoning.review_sections(self.asset, self.windows, self.frames)
            fail[0] = False
            result, receipt = reasoning.review_sections(self.asset, self.windows, self.frames)
        self.assertEqual(attempts, ["reference_visual_observe", "reference_visual_verify",
                                    "reference_visual_reconcile", "reference_visual_reconcile"])
        self.assertEqual(receipt["new_provider_calls"], 1)
        self.assertTrue(receipt["stages"]["observe"]["cache_hit"])
        self.assertTrue(receipt["stages"]["verify"]["cache_hit"])
        self.assertEqual(result["sections"][0]["verification"]["status"], "uncertain")

    def test_invalid_observation_time_is_flagged_and_only_conflicting_review_is_reconciled(self):
        second = copy.deepcopy(self.windows[0])
        second.update(id="second", start=1, end=2, frame_times=[1.1, 1.7])
        windows = self.windows + [second]
        frames = self.frames + [{"time": time, "path": self.frames[0]["path"]} for time in [1.1, 1.7]]
        calls = []
        original_good = response(verify=True)["sections"][0]

        def with_second(verify):
            value = response(verify=verify)
            other = response(identifier="second", verify=verify)["sections"][0]
            other["evidence_times"] = [1.1, 1.7]
            other["structured_motion"]["evidence_times"] = [1.1, 1.7]
            other["structured_motion"]["subject_evidence_times"] = [1.1, 1.7]
            value["sections"].append(other)
            return value

        def reply(name, schema, system, user, images=None):
            context = json.loads(user)
            calls.append(name)
            if name == "reference_visual_observe":
                value = with_second(False)
                value["sections"][0]["visible_text"] = [
                    {"text": "字幕", "legibility": "clear", "evidence_times": [.333]}]
                # This matches the real long-video failure: shape is usable,
                # but a cited time was not among supplied evidence images.
                return value
            if name == "reference_visual_verify":
                self.assertIn("evidence_time_not_provided_in_section",
                              context["validation_flags"][0]["codes"])
                value = with_second(True)
                value["sections"][1]["description"] = "人物随后走出画面左侧。"
                value["sections"][1]["structured_motion"].update(
                    subject_relative_movement="pose_change_only", subject_evidence_basis="articulation_only")
                return value
            self.assertEqual([w["id"] for w in context["sections"]], ["second"])
            self.assertEqual([frame["time"] for frame in images], [1.1, 1.7])
            self.assertEqual([row["id"] for row in context["previous_review"]["sections"]], ["second"])
            return {"sections": [with_second(True)["sections"][1]]}

        with patch.object(providers, "_json_response", side_effect=reply):
            result, receipt = reasoning.review_sections(self.asset, windows, frames)
            replay, cached = reasoning.review_sections(self.asset, windows, frames)
        self.assertEqual(calls, ["reference_visual_observe", "reference_visual_verify", "reference_visual_reconcile"])
        self.assertEqual(receipt["new_provider_calls"], 3)
        self.assertEqual(receipt["reconciled_section_ids"], ["second"])
        self.assertEqual(receipt["section_sources"]["shot"]["stage"], "verify")
        self.assertEqual(receipt["section_sources"]["second"]["stage"], "reconcile_1")
        self.assertTrue(all(source["cache_hit"] for source in cached["section_sources"].values()))
        self.assertEqual(cached["new_provider_calls"], 0)
        self.assertEqual(result, replay)
        final_good = result["sections"][0]
        for key in ("description", "role", "evidence_times", "structured_motion", "visible_text"):
            self.assertEqual(final_good[key], original_good[key])

    def test_reconciliation_is_bounded_and_invalid_results_never_pass(self):
        attempts = []
        def reply(name, schema, system, user, images=None):
            attempts.append(name)
            return response(direction="right", verify=name != "reference_visual_observe")
        with patch.object(providers, "_json_response", side_effect=reply):
            for replay in range(2):
                with self.assertRaisesRegex(providers.ProviderError, "needs_review.*opposes"):
                    reasoning.review_sections(self.asset, self.windows, self.frames)
        self.assertEqual(attempts, ["reference_visual_observe", "reference_visual_verify",
                                    "reference_visual_reconcile", "reference_visual_reconcile"])

    def test_affirmative_prose_cannot_bypass_motion_viewpoint_or_caption_contract(self):
        cases = [
            ({"description": "人物走出画面左侧，远处招牌无法确认。"},
             "description_locomotion_conflicts_with_subject_evidence"),
            ({"structured_motion": {"subject_observation": "人物走出画面左侧。"}},
             "subject_observation_locomotion_conflicts_with_subject_evidence"),
            ({"description": "第一人称从座舱内向外拍摄。"},
             "first_person_prose_conflicts_with_external_viewpoint"),
            ({"description": "画面文字“往长了看”。", "visible_text": [
                {"text": "不往长了看", "legibility": "clear", "evidence_times": [.1]}]},
             "description_quote_not_supported_by_clear_visible_text"),
            ({"description": "盒子上标有“几行文字”。", "visible_text": [
                {"text": "几行文字", "legibility": "partial", "evidence_times": [.1]}]},
             "description_quote_not_supported_by_clear_visible_text"),
        ]
        for edits, expected in cases:
            with self.subTest(expected=expected):
                value = response(verify=True)
                for key, item in edits.items():
                    if key == "structured_motion":
                        value["sections"][0][key].update(item)
                    else:
                        value["sections"][0][key] = item
                self.assertIn(expected, reasoning._flags(value, self.windows)[0]["codes"])
                # The already-paid observation/verification signature remains
                # unchanged; new checks run on reviewed output instead.
                self.assertEqual(reasoning._flags(value, self.windows, legacy=True), [])
        valid = response(verify=True)
        valid["sections"][0]["description"] = (
            "无法确认第一人称视角。未见人物走动，人物并非走出，而是随取景移出画面。画面文字“不往长了看”。")
        valid["sections"][0]["visible_text"] = [
            {"text": "不往长了看", "legibility": "clear", "evidence_times": [.1]}]
        self.assertEqual(reasoning._flags(valid, self.windows), [])
        self.assertFalse(reasoning._asserts("第一人称视角无法确认。", r"第一人称"))
        for text in ("非第一人称。", "从外部观察，非第一人称。", "机外观察而非第一人称。"):
            self.assertFalse(reasoning._asserts(text, r"第一人称"))
        for text in (
            "单腿抬起、身体前倾的奔跑姿态。",
            "画面显示人物行走姿势。",
            "人物奔跑动作定格。",
            "该镜头仅有单帧，无法确认持续跑动或独立位移。",
            "无法确认人物正在实际步行。",
        ):
            self.assertFalse(reasoning._asserts(text, reasoning._LOCOMOTION))
        self.assertTrue(reasoning._asserts("人物走出画面，远处文字无法确认。", reasoning._LOCOMOTION))

    def test_second_reconciliation_adds_derived_pairs_and_low_effort_only(self):
        windows = copy.deepcopy(self.windows)
        windows[0]["cut_times"] = [.5]
        adapters, requests = [], []
        original_adapter = providers._analysis_response

        def adapter(asset, *args, **kwargs):
            adapters.append(asset.get("_analysis_reasoning_effort"))
            return original_adapter(asset, *args, **kwargs)

        def reply(name, schema, system, user, images=None):
            context = json.loads(user)
            requests.append((name, context))
            value = response(verify=name != "reference_visual_observe")
            value["sections"][0]["description"] = "画面文字“局部字样”。"
            value["sections"][0]["visible_text"] = [
                {"text": "局部字样", "legibility": "partial", "evidence_times": [.1]}]
            if context.get("attempt") == 2:
                diagnostic = context["evidence_diagnostics"][0]
                self.assertEqual([g["frame_times"] for g in diagnostic["same_shot_frame_groups"]], [[.1], [.7]])
                self.assertEqual(diagnostic["available_adjacent_pair_count"], 0)
                self.assertEqual(diagnostic["cited_subject_same_shot_pairs"], [])
                self.assertEqual(diagnostic["partial_text"], value["sections"][0]["visible_text"])
                self.assertIn("没有任何同镜头连续对照帧", diagnostic["temporal_limit"])
                value["sections"][0]["description"] = "单帧可见人物与不完整文字。"
                value["sections"][0]["structured_motion"].update(
                    image_direction="unknown", view_change="unknown",
                    subject_relative_movement="unknown", subject_evidence_basis="insufficient",
                    subject_observation="没有同镜头对照，无法确认主体动作。")
                value["sections"][0]["checks"]["subject_motion"] = "uncertain"
            else:
                self.assertNotIn("evidence_diagnostics", context)
            return value

        with patch.object(providers, "_analysis_response", side_effect=adapter), \
             patch.object(providers, "_json_response", side_effect=reply):
            result, receipt = reasoning.review_sections(self.asset, windows, self.frames)
            replay, cached = reasoning.review_sections(self.asset, windows, self.frames)
        self.assertEqual(adapters[:4], [None, None, None, "low"])
        self.assertNotIn("_analysis_reasoning_effort", self.asset)
        self.assertEqual(len(requests), 4)
        self.assertEqual(receipt["stages"]["reconcile_2"]["reasoning_effort"], "low")
        self.assertEqual(cached["new_provider_calls"], 0)
        self.assertEqual(result, replay)

    def test_independent_motion_risk_skips_first_reconcile_and_is_not_a_negative_answer(self):
        second = copy.deepcopy(self.windows[0])
        second.update(id="second", start=1, end=2, frame_times=[1.1, 1.7])
        windows = self.windows + [second]
        frames = self.frames + [{"time": time, "path": self.frames[0]["path"]} for time in [1.1, 1.7]]
        calls = []

        def reply(name, schema, system, user, images=None):
            context = json.loads(user)
            calls.append((name, context.get("attempt")))
            risk = response(relative="independent_translation", verify=name != "reference_visual_observe")["sections"][0]
            risk["structured_motion"]["subject_evidence_basis"] = "landmark_relative_displacement"
            if name == "reference_visual_reconcile":
                self.assertEqual(context["attempt"], 2)
                self.assertEqual(context["validation_flags"], [])
                self.assertIn("即使validation_flags为空", system)
                self.assertIn("必须排查视差", system)
                self.assertIn("脚未入镜或被遮挡时，不能编造", system)
                self.assertEqual([w["id"] for w in context["sections"]], ["shot"])
                self.assertEqual([frame["time"] for frame in images], [.1, .7])
                self.assertFalse(context["review_risks"][0]["is_hard_failure"])
                # The model may uphold independent motion with evidence. The
                # risk rule cannot force a negative answer or cause a new loop.
                return {"sections": [risk]}
            self.assertNotIn("必须排查视差", system)
            safe = response(identifier="second", verify=name != "reference_visual_observe")["sections"][0]
            safe["evidence_times"] = [1.1, 1.7]
            safe["structured_motion"]["evidence_times"] = [1.1, 1.7]
            safe["structured_motion"]["subject_evidence_times"] = [1.1, 1.7]
            return {"sections": [risk, safe]}

        with patch.object(providers, "_json_response", side_effect=reply):
            result, receipt = reasoning.review_sections(self.asset, windows, frames)
            replay, cached = reasoning.review_sections(self.asset, windows, frames)
        self.assertEqual(calls, [("reference_visual_observe", None), ("reference_visual_verify", None),
                                 ("reference_visual_reconcile", 2)])
        self.assertNotIn("reconcile_1", receipt["stages"])
        self.assertEqual(receipt["risk_reviewed_section_ids"], ["shot"])
        self.assertEqual(receipt["section_sources"]["shot"]["stage"], "reconcile_2")
        self.assertEqual(receipt["section_sources"]["second"]["stage"], "verify")
        self.assertEqual(result["sections"][0]["structured_motion"]["subject_relative_movement"], "independent_translation")
        self.assertEqual(cached["new_provider_calls"], 0)
        self.assertEqual(result, replay)

    def test_cross_cut_movement_or_unseen_evidence_cannot_be_verified(self):
        windows = copy.deepcopy(self.windows)
        windows[0]["cut_times"] = [.5]
        bad = response(verify=True)
        reasoning._validate_evidence(bad, windows, verify=True)
        flags = reasoning._flags(bad, windows)[0]["codes"]
        self.assertIn("motion_requires_same_shot_temporal_evidence", flags)
        self.assertIn("subject_change_requires_same_shot_temporal_evidence", flags)
        bad["sections"][0]["evidence_times"] = [42]
        with self.assertRaisesRegex(providers.ProviderError, "窗口.*外"):
            reasoning._validate_evidence(bad, self.windows, verify=True)
        bad = response(relative="independent_translation", verify=True)
        self.assertIn("subject_translation_requires_relative_or_gait_evidence",
                      reasoning._flags(bad, self.windows)[0]["codes"])


if __name__ == "__main__":
    unittest.main()
