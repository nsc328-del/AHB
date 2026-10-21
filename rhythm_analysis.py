"""DeepSeek interprets measured sound events and edits; no raw-audio claim."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import providers
import rhythm_evidence
import storage as db
import usage
import workflow

SYSTEM = """你分析视频剪辑与声音节奏的时间关系。输入是本机计算的声音起音、周期性脉冲候选和切镜时间，
不是音频。你没有听到原音，不能编造音乐曲风、歌词、音效、鼓点乐器或说话内容。
onset是声音频谱能量的起音候选，可能来自语音、噪声或音乐；beat_candidate是有周期支持的候选，
仍不能直接认定为真实音乐拍点。用中文给出谨慎但具体的结论。
对每个cut_id逐一分类：beat_aligned只允许在容差内确有beat_candidate；
onset_aligned只允许在容差内确有onset；不靠近任何候选则unaligned，不确定可以uncertain。
偏差offset_ms=cut_time-event_time，正数表示切镜晚于声音事件，负数表示早于声音事件。
重点看多次切镜是否有稳定重复的相位对应；单个邻近点不等于有意卡点。
比较alignment_summary的偏移切点对照与均匀时间覆盖；声音事件密集、偶合率相近时，不能称已证明卡点。
editing_pattern只能是beat_driven、accent_driven、mixed、not_established；前两者是时序证据的推断，
summary必须注明是候选时序关系，不能写成已听审结论。confidence只描述这次证据的充分程度，不是准确率。
不将旁白ASR时间当作节拍，不将切镜密集自动解释为节奏强。输出JSON，保留全部cut_id，不创造切点。"""


def _schema():
    return providers._object({
        "summary": providers._string(),
        "editing_pattern": {"type": "string", "enum": ["beat_driven", "accent_driven", "mixed", "not_established"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "matches": providers._array(providers._object({
            "cut_id": {"type": "integer"},
            "classification": {"type": "string", "enum": ["beat_aligned", "onset_aligned", "unaligned", "uncertain"]},
            "reason": providers._string(),
        })),
    })


def validate(result, matches):
    if len(result["matches"]) != len(matches) or {m["cut_id"] for m in result["matches"]} != set(range(len(matches))):
        raise providers.ProviderError("节奏判断未对应实际切点")
    for row in result["matches"]:
        source = matches[row["cut_id"]]
        key = {"beat_aligned": "beat_candidate", "onset_aligned": "onset"}.get(row["classification"])
        if key:
            event = source.get(key) or {}
            try:
                offset = float(source["cut_time"]) - float(event["time"])
            except (KeyError, ValueError, TypeError):
                raise providers.ProviderError("节奏判断引用的声音事件不存在") from None
            if not math.isfinite(offset) or abs(offset) > rhythm_evidence.MATCH_TOLERANCE + rhythm_evidence.MATCH_EPSILON:
                raise providers.ProviderError("节奏判断引用的声音事件不在容差内")


def _compute(asset, analysis, job_id):
    path = Path(asset["path"])
    signature = hashlib.sha256(json.dumps({
        "size": path.stat().st_size, "mtime": path.stat().st_mtime_ns,
        "cuts": analysis.get("shot_boundaries", []), "version": 1,
        "matching_tolerance": rhythm_evidence.MATCH_TOLERANCE,
        "matching_epsilon": rhythm_evidence.MATCH_EPSILON,
    }, sort_keys=True).encode()).hexdigest()
    directory = db.DATA / "rhythm-cache" / asset["id"] / signature
    cached = directory / "evidence.json"
    if cached.exists():
        evidence = json.loads(cached.read_text())
    else:
        evidence = rhythm_evidence.analyze_audio_rhythm(path, directory, cut_times=analysis.get("shot_boundaries", []))
        directory.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(evidence, ensure_ascii=False, allow_nan=False))
    if evidence["status"] not in {"analyzed", "completed", "available"}:
        return {"status": "unavailable", "summary": "暂未取得足够的节奏证据", "evidence": evidence,
                "source_job_id": job_id, "new_provider_calls": 0}
    matches = evidence["cut_matches"]
    context = {
        "audio_input": "measured_time_series_only", "duration": evidence["duration"],
        "alignment_summary": evidence["alignment_summary"],
        "local_windows": [{k: w.get(k) for k in ("start", "end", "bpm", "confidence", "score", "reason")}
                          for w in evidence["local_windows"]],
        "cut_matches": [dict(m, cut_id=i) for i, m in enumerate(matches)],
        "limitations": evidence.get("limits", evidence.get("limitations")),
    }
    enriched = {"_analysis_cache_dir": str(db.DATA / "analysis-cache" / asset["id"] / "rhythm")}
    result, receipt = providers._analysis_response(enriched, "reference_rhythm", _schema(), SYSTEM, context, None,
                                                   lambda value: validate(value, matches))
    mapped = []
    for row in sorted(result["matches"], key=lambda r: r["cut_id"]):
        source = matches[row["cut_id"]]
        onset, beat = source.get("onset") or {}, source.get("beat_candidate") or {}
        def offset_ms(event):
            return round((float(source["cut_time"])-float(event["time"]))*1000, 1) if event.get("time") is not None else None
        mapped.append({**row, "cut_time": source["cut_time"], "onset_time": onset.get("time"),
                       "beat_time": beat.get("time"), "onset_offset_ms": offset_ms(onset),
                       "beat_offset_ms": offset_ms(beat)})
    return {**result, "matches": mapped, "status": "completed", "evidence": evidence,
            "source_job_id": receipt["source_job_id"], "new_provider_calls": int(not receipt["cache_hit"]),
            "cache_hit": receipt["cache_hit"], "raw_audio_sent": False, "created_at": db.now()}


def _operation(asset_id, base_analysis_id, job_id):
    asset = workflow.get_required("assets", asset_id)
    before = asset["analysis"]
    if before["analysis_id"] != base_analysis_id:
        raise ValueError("样片分析版本已变化")
    workflow.job_update(job_id, message="提取声音起音与周期候选", progress=10)
    with usage.context(asset_id=asset_id):
        rhythm = _compute(asset, before, job_id)
    result = copy.deepcopy(before)
    if result.pop("presentation", None):
        result["presentation_invalidated"] = {"reason": "rhythm_evidence_changed", "source_analysis_id": base_analysis_id}
    result.update(rhythm_analysis=rhythm, rhythm_source_analysis_id=base_analysis_id, analysis_id=db.identifier())
    with workflow._asset_lock(asset_id, "analysis_work"):
        latest = workflow.get_required("assets", asset_id)
        if latest["analysis"]["analysis_id"] != base_analysis_id:
            raise ValueError("节奏分析期间版本已变化，结果已缓存但未覆盖新版本")
        db.create("analyses", {"id": result["analysis_id"], "asset_id": asset_id, "reference": True,
                               "source_job_id": job_id, "result": result})
        db.update("assets", asset_id, {"analysis": result})
    return {"asset_id": asset_id, "analysis_id": result["analysis_id"], "rhythm_analysis": rhythm}


def analyze_rhythm(asset_id):
    with workflow._asset_lock(asset_id, "analysis_queue"):
        asset = workflow.get_required("assets", asset_id)
        if not workflow.is_reference_asset(asset) or not asset.get("analysis"):
            raise ValueError("请先完成样片画面分析")
        payload = {"asset_id": asset_id, "base_analysis_id": asset["analysis"]["analysis_id"]}
        for job in db.records("jobs", 5000):
            if job.get("payload", {}).get("asset_id") == asset_id and job["status"] in {"queued", "running"} and job["kind"] in {"reference", "reference_repair", "reference_rhythm", "reference_presentation", "reference_semantics"}:
                if job["kind"] == "reference_rhythm":
                    return job
                raise ValueError("请等待该样片的当前分析完成")
        return workflow.enqueue("reference_rhythm", payload,
                                lambda jid: _operation(asset_id, payload["base_analysis_id"], jid),
                                executor=workflow.ANALYSIS_EXECUTOR)
