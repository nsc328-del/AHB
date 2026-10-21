"""Local speech alignment is review evidence, never a rewrite of model claims."""
import copy
import unittest

import workflow


def transcript(segments):
    return {"language": "zh", "language_probability": .98, "created_at": "asr-snapshot",
            "segments": segments}


def speech(start, end, text, words):
    return {"start": start, "end": end, "text": text, "words": [
        {"start": a, "end": b, "word": word, "probability": probability}
        for a, b, word, probability in words]}


class ContentEvidenceTest(unittest.TestCase):
    def test_word_alignment_clips_bounds_not_whole_sentence_and_preserves_model(self):
        source = {"analysis_id": "original", "content_segments": [
            {"start": 1.5, "end": 3.5, "description": "模型原解读，不据此改写"}]}
        raw = transcript([speech(0, 5, "先出发再回家", [
            (0, 1, "先", .9), (1, 2, "出发", .9), (2, 3, "再", .9),
            (3, 4, "回家", .9), (4, 5, "吧", .9)])])
        before, raw_before = copy.deepcopy(source), copy.deepcopy(raw)
        annotated = workflow.annotate_content_evidence(source, raw)
        content = annotated["content_segments"][0]
        entry = content["speech_evidence"][0]
        self.assertEqual(entry["text"], "出发再回家")
        self.assertEqual((entry["start"], entry["end"]), (1.5, 3.5))
        self.assertEqual((entry["words"][0]["source_start"], entry["words"][-1]["source_end"]), (1, 4))
        self.assertTrue(entry["boundary_clipped"])
        self.assertEqual(content["description"], source["content_segments"][0]["description"])
        self.assertEqual(content["speech_evidence_status"], "aligned")
        self.assertEqual(source, before)
        self.assertEqual(raw, raw_before)
        self.assertEqual(workflow.annotate_content_evidence(annotated, raw), annotated)

    def test_uncertain_and_untimed_speech_never_becomes_precise_evidence(self):
        source = {"content_segments": [{"start": 0, "end": 2}]}
        low = transcript([speech(0, 2, "低可信对白", [(0, 2, "低可信对白", .1)])])
        content = workflow.annotate_content_evidence(source, low)["content_segments"][0]
        self.assertEqual(content["speech_evidence"], [])
        self.assertEqual(content["speech_evidence_status"], "unavailable")
        untimed = transcript([{"start": 0, "end": 4, "text": "不能把整句硬贴进来",
                               "words": [{"word": "不能把整句硬贴进来", "probability": .9}]}])
        content = workflow.annotate_content_evidence(source, untimed)["content_segments"][0]
        self.assertEqual(content["speech_evidence"], [])
        self.assertEqual(content["speech_evidence_unaligned_count"], 1)
        self.assertEqual(content["speech_evidence_status"], "unavailable")
        empty = workflow.annotate_content_evidence(source, transcript([]))["content_segments"][0]
        self.assertEqual(empty["speech_evidence_status"], "no_speech")
        failed = workflow.annotate_content_evidence(source, untimed, {"status": "failed"})["content_segments"][0]
        self.assertEqual(failed["speech_evidence"], [])
        self.assertEqual(failed["speech_evidence_status"], "unavailable")

    def test_overlap_is_marked_without_trimming_and_touching_is_not_overlap(self):
        source = {"content_segments": [
            {"start": 120.467, "end": 129.8, "description": "A"},
            {"start": 128.1, "end": 131.733, "description": "B"},
            {"start": 131.733, "end": 140, "description": "C"}]}
        annotated = workflow.annotate_content_evidence(source, transcript([]))
        first, second, third = annotated["content_segments"]
        self.assertEqual(first["overlaps"], [{"segment_index": 1, "start": 128.1, "end": 129.8}])
        self.assertEqual(second["overlaps"], [{"segment_index": 0, "start": 128.1, "end": 129.8}])
        self.assertEqual(third["overlaps"], [])
        self.assertEqual(annotated["content_evidence_annotation"]["overlap_pair_count"], 1)
        self.assertEqual([(s["start"], s["end"], s["description"]) for s in annotated["content_segments"]],
                         [(s["start"], s["end"], s["description"]) for s in source["content_segments"]])
