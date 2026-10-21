"""Versioned local structure heuristics; never an estimate of audience response."""
from __future__ import annotations

from difflib import SequenceMatcher

VERSION = "local-structure-v1"
NOVELTY_WEIGHT = .08
EXPLORATION_DISTANCE = .25
POLICY = {
    "version": VERSION,
    "exploration_per_batch": 1,
    "exploration_min_distance": EXPLORATION_DISTANCE,
    "novelty_weight": NOVELTY_WEIGHT,
    "history_scope": "adopted_rendered_same_project_or_explicit_platform_account",
    "score_kind": "structure_heuristic",
    "uses_publication_metrics": False,
    "predicts_performance": False,
}


def editorial_clips(plan):
    """Collapse invisible subdivisions before measuring editing diversity."""
    kinds = plan.get("source_kinds") or {}
    clips = []
    for original in plan["clips"]:
        clip = dict(original)
        previous = clips[-1] if clips else None
        same_source = previous is not None and previous["asset_id"] == clip["asset_id"]
        still = kinds.get(clip["asset_id"]) == "image"
        contiguous = previous is not None and abs(float(previous["end"]) - float(clip["start"])) < .02
        if same_source and (still or contiguous):
            previous["duration"] = float(previous["duration"]) + float(clip["duration"])
            previous["end"] = previous["duration"] if still else clip["end"]
        else:
            clips.append(clip)
    return clips


def fingerprint(plan):
    clips = editorial_clips(plan)
    total = sum(float(clip["duration"]) for clip in clips)
    return {
        "asset_order": [clip["asset_id"] for clip in clips],
        "duration_shares": [round(float(clip["duration"]) / total, 6) for clip in clips],
        "source_starts": [float(clip["start"]) for clip in clips],
        "durations": [float(clip["duration"]) for clip in clips],
        "roles": [str(clip.get("role") or "body") for clip in clips],
        "transitions": [str(clip.get("transition") or "cut") for clip in clips],
    }


def editorial_distance(first, second):
    """Material/interval/allocation distance within one frozen source pool.

    Labels, subtitles, model narrative and transition changes cannot manufacture
    the exploration slot. Source edits and duration allocation must differ.
    """
    a, b = fingerprint(first), fingerprint(second)
    order_distance = 1 - SequenceMatcher(None, a["asset_order"], b["asset_order"], autojunk=False).ratio()
    size = max(len(a["durations"]), len(b["durations"]))
    allocation = sum(abs((a["duration_shares"] + [0] * size)[i] -
                         (b["duration_shares"] + [0] * size)[i]) for i in range(size)) / 2
    count_distance = abs(len(a["durations"]) - len(b["durations"])) / size
    matching_intervals = [
        min(1, abs(sa - sb) / max(.2, min(da, db)))
        for aid, bid, sa, sb, da, db in zip(a["asset_order"], b["asset_order"], a["source_starts"],
                                           b["source_starts"], a["durations"], b["durations"])
        if aid == bid
    ]
    interval_distance = sum(matching_intervals) / size
    return round(max(order_distance, allocation, count_distance, interval_distance), 6)


def historical_similarity(first, second):
    """Compare cut allocation and transitions, not topic, labels or outcome.

    Asset identities only affect the score when both videos use the same media
    pool. Different footage alone is not evidence of a new editing structure.
    """
    a, b = first, second
    size = max(len(a["duration_shares"]), len(b["duration_shares"]))
    allocation = 1 - sum(abs((a["duration_shares"] + [0] * size)[i] -
                             (b["duration_shares"] + [0] * size)[i]) for i in range(size)) / 2
    count = min(len(a["duration_shares"]), len(b["duration_shares"])) / size
    transitions = SequenceMatcher(None, a["transitions"], b["transitions"], autojunk=False).ratio()
    similarity = .65 * allocation + .25 * count + .1 * transitions
    if set(a["asset_order"]) == set(b["asset_order"]):
        order = SequenceMatcher(None, a["asset_order"], b["asset_order"], autojunk=False).ratio()
        similarity = .8 * similarity + .2 * order
    return round(max(0, min(1, similarity)), 6)


def evaluate(plan, history, siblings):
    fp = fingerprint(plan)
    comparisons = [(historical_similarity(fp, item["fingerprint"]), item) for item in history]
    nearest = max(comparisons, key=lambda item: item[0]) if comparisons else None
    similarity = nearest[0] if nearest else None
    penalty = round(NOVELTY_WEIGHT * similarity, 6) if similarity is not None else 0
    distances = [editorial_distance(plan, sibling) for sibling in siblings]
    return {
        "heuristic_version": VERSION,
        "history_count": len(history),
        "history_similarity": similarity,
        "novelty": round(1 - similarity, 6) if similarity is not None else None,
        "novelty_penalty": penalty,
        "ranking_score": round(1 - penalty, 6),
        "nearest_history_plan_id": nearest[1]["plan_id"] if nearest else None,
        "minimum_batch_distance": min(distances) if distances else None,
        "exploration": bool(plan.get("candidate_strategy", {}).get("exploration")),
        "quality": {"plan_validation": "passed", "preview_validation": "pending"},
        "predicts_performance": False,
    }
