"""Persistent background work: evidence → versioned plan → real rendered media."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import math
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

import media
import storage as db

EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="video-studio")
ASR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-transcription")
ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="asset-analysis")
ACTIVE = set()
ACTIVE_LOCK = threading.Lock()
PROJECT_LOCKS = {}
PROJECT_LOCKS_LOCK = threading.Lock()
ASSET_LOCKS = {}
ASSET_LOCKS_LOCK = threading.Lock()
GENERATION_SUBMISSION_LOCK = threading.Lock()
RATIOS = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (960, 960), "4:5": (864, 1080), "3:4": (810, 1080)}
UNSET = object()
UPLOAD_PURPOSES = {"library", "reference", "create", "analysis", "remix", "generate", "generation", "upload"}


def _asset_lock(asset_id, phase):
    with ASSET_LOCKS_LOCK:
        return ASSET_LOCKS.setdefault((asset_id, phase), threading.Lock())


def upload_purpose(value):
    value = str(value).strip().lower()
    if value not in UPLOAD_PURPOSES:
        raise ValueError("上传用途无效")
    return value


def providers():
    import providers as module
    return module


def get_required(table, record_id):
    result = db.get(table, record_id)
    if not result:
        raise ValueError("记录不存在或已不可用")
    return result


def job_update(job_id, **fields):
    if "progress" in fields:
        fields["progress"] = max(0, min(100, float(fields["progress"])))
    return db.update("jobs", job_id, fields)


def _error_text(error):
    # Provider errors are explicitly redacted by providers.py. Internal errors are not exposed.
    if isinstance(error, (ValueError, RuntimeError, getattr(providers(), "ProviderError", RuntimeError))):
        return str(error)[:600]
    return "任务执行失败，请查看本机服务日志并重试此操作。"


def _execute(job_id, operation):
    with ACTIVE_LOCK:
        if job_id in ACTIVE:
            return
        ACTIVE.add(job_id)
    try:
        job_update(job_id, status="running", progress=1, message="开始处理", error=None)
        result = operation(job_id)
        job_update(job_id, status="succeeded", progress=100, message="已完成", result=result, error=None)
    except Exception as error:
        module = providers()
        if isinstance(error, getattr(module, "ConfigurationError", ())):
            status = "needs_configuration"
        elif isinstance(error, getattr(module, "SubmissionUnknown", ())):
            status = "unknown"
        elif isinstance(error, getattr(module, "ProviderError", ())):
            current = get_required("jobs", job_id)
            state = current.get("provider_state", {})
            status = "unknown" if current["kind"] == "generation" and state.get("task_id") and not state.get("terminal_failure") else "failed"
            if current["kind"] == "generation" and not current.get("provider_state", {}).get("task_id"):
                job_update(job_id, provider_state={"submitting": False, "status": "rejected"})
        else:
            job = get_required("jobs", job_id)
            state = job.get("provider_state", {})
            # Accepted paid tasks remain queryable after temporary network/poll failures.
            status = "unknown" if job["kind"] == "generation" and not state.get("terminal_failure") and (state.get("task_id") or state.get("submitting")) else "failed"
        message = _error_text(error)
        job_update(job_id, status=status, message=message, error=message)
        job = db.get("jobs", job_id)
        asset_id = job.get("payload", {}).get("asset_id")
        if asset_id and job["kind"] in {"analysis", "reference"} and db.get("assets", asset_id):
            db.update("assets", asset_id, transform=lambda item:
                      {**item, "analysis_status": status, "analysis_error": message}
                      if item.get("analysis_job_id") in {None, job_id} else item)
        if asset_id and job["kind"] == "transcription" and db.get("assets", asset_id):
            db.update("assets", asset_id, transform=lambda item: {**item, "audio_status": "failed", "audio_error": message, "analysis": {**item["analysis"], "audio_status": "failed", "audio_note": "本次转录失败，保留上次的本地转录结果。" if item.get("transcript") else "语音尚未转录，不能据此推断对白内容。"} if item.get("analysis") else None})
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(job_id)


def enqueue(kind, payload, operation, job_id=None, executor=None):
    if job_id:
        job = db.update("jobs", job_id, {"status": "queued", "message": "等待查询已有任务", "error": None})
    else:
        job = db.create("jobs", {"kind": kind, "status": "queued", "progress": 0, "message": "等待处理", "result": None, "error": None, "payload": payload, "provider_state": {}})
    return _schedule_job(job, operation, executor)


def _schedule_job(job, operation, executor=None):
    try:
        (executor or EXECUTOR).submit(_execute, job["id"], operation)
    except RuntimeError:
        message = "后台执行器暂不可用，任务未执行；可稍后重试。"
        job = job_update(job["id"], status="failed", message=message, error=message)
        asset_id = job.get("payload", {}).get("asset_id")
        if asset_id and job["kind"] in {"analysis", "reference", "transcription"}:
            prefix = "audio" if job["kind"] == "transcription" else "analysis"
            db.update("assets", asset_id, {f"{prefix}_status": "failed", f"{prefix}_error": message})
    return job


def register_asset(path, name, source="upload", generation_id=None, purpose=None):
    path = Path(path).resolve()
    info = media.probe(path)
    if info["kind"] == "video" and info["duration"] > 7200:
        raise ValueError("单份视频最长支持两小时，请先分段上传")
    if purpose is not None:
        purpose = upload_purpose(purpose)
        if purpose == "reference" and info["kind"] != "video":
            raise ValueError("参考样片上传需要视频素材")
    asset_id = db.identifier()
    thumb = media.thumbnail(path, info["kind"], db.DATA / "previews" / f"{asset_id}.jpg")
    return db.create("assets", {"id": asset_id, "name": name, "path": str(path), **info, "size": path.stat().st_size, "sha256": media.file_hash(path), "url": db.media_url(path), "thumbnail_url": db.media_url(thumb) if thumb else None, "source": source, "generation_id": generation_id, **({"purpose": purpose} if purpose is not None else {}), "analysis_status": "not_analyzed", "analysis": None, "audio_status": "not_transcribed" if info.get("has_audio") else "no_audio", "transcript": None})


def queue_uploaded_asset(asset_id):
    """Only the upload endpoint calls this; old and generated media are not swept."""
    asset = get_required("assets", asset_id)
    jobs = []
    if asset.get("has_audio"):
        jobs.append(transcribe_asset(asset_id))
    if asset["kind"] in {"image", "video"}:
        jobs.append(analyze(asset_id, reference=asset.get("purpose") == "reference", force=False))
    return jobs


def transcribe_asset(asset_id, model=None):
    with _asset_lock(asset_id, "transcription_queue"):
        return _enqueue_transcription(asset_id, model)


def _enqueue_transcription(asset_id, model=None):
    asset = get_required("assets", asset_id)
    if not asset.get("has_audio"):
        raise ValueError("此素材没有音轨")
    if model not in {None, "base", "small"}:
        raise ValueError("本地转录支持 base 或 small 模型")
    for job in db.records("jobs", 5000):
        if job["kind"] == "transcription" and job["status"] in {"queued", "running"} and job.get("payload", {}).get("asset_id") == asset_id:
            return job
    db.update("assets", asset_id, {"audio_status": "queued", "audio_error": None})
    def operation(job_id):
        import transcription
        def progress(**update):
            job_update(job_id, progress=update.get("progress", 5), message=update.get("message", "本地转录中"))
            db.update("assets", asset_id, {"audio_status": update.get("audio_status", "running")})
        result = transcription.transcribe(asset["path"], model=model, progress=progress)
        result["created_at"] = db.now()
        transcript_path = db.DATA / "plans" / f"transcript-{asset_id}-{job_id}.json"
        transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["url"] = db.media_url(transcript_path)
        db.update("assets", asset_id, transform=lambda item: {**item, "transcript": result, "audio_status": "succeeded", "audio_error": None, "analysis": {**item["analysis"], "transcript": result, "audio_status": "succeeded", "audio_analyzed": True, "audio_note": "本地语音转录已完成；文字为自动识别结果，语音以原素材为准。"} if item.get("analysis") else None})
        return {"asset_id": asset_id, "transcript": result}
    job = enqueue("transcription", {"asset_id": asset_id, "model": model}, operation, executor=ASR_EXECUTOR)
    db.update("assets", asset_id, {"transcription_job_id": job["id"]})
    return job


def _analysis(asset_id, reference, job_id, force=False):
    # A plan started immediately after upload may reach the same asset before
    # its queued worker. One performs the work; the other reuses its evidence.
    initial = get_required("assets", asset_id)
    associated = db.get("jobs", initial.get("analysis_job_id")) if initial.get("analysis_job_id") else None
    shared_job_id = associated["id"] if associated and (associated["status"] in {"queued", "running"} or associated["id"] == job_id) else None
    with _asset_lock(asset_id, "analysis_work"):
        asset = get_required("assets", asset_id)
        reference = reference or asset.get("purpose") == "reference"
        ready = asset.get("analysis") and (not reference or asset["analysis"].get("reference_analysis"))
        if ready and not force:
            db.update("assets", asset_id, transform=lambda item:
                      {**item, "analysis_status": "succeeded", "analysis_error": None}
                      if item.get("analysis_job_id") in {None, job_id} else item)
            return {"asset_id": asset_id, "analysis": asset["analysis"], "reused": True}
        if (not force and shared_job_id and asset.get("_analysis_failure_job_id") == shared_job_id
                and asset.get("analysis_status") in {"failed", "needs_configuration"}):
            error_type = getattr(providers(), "ConfigurationError", ValueError) if asset["analysis_status"] == "needs_configuration" else ValueError
            raise error_type(asset.get("analysis_error") or "素材分析未完成，请重试分析")
        try:
            return _analyze_media(asset_id, reference, job_id)
        except Exception as error:
            status = "needs_configuration" if isinstance(error, getattr(providers(), "ConfigurationError", ())) else "failed"
            db.update("assets", asset_id, {"analysis_status": status, "analysis_error": _error_text(error),
                                          "_analysis_failure_job_id": shared_job_id or job_id})
            raise


def _analyze_media(asset_id, reference, job_id):
    asset = get_required("assets", asset_id)
    if reference and asset["kind"] != "video":
        raise ValueError("参考样片分析需要视频素材")
    if asset.get("has_audio") and not asset.get("transcript") and asset.get("audio_status") not in {"failed", "queued", "running", "downloading_model"}:
        transcribe_asset(asset_id)
    db.update("assets", asset_id, {"analysis_status": "running", "analysis_error": None})
    job_update(job_id, message="读取真实媒体与证据帧", progress=10)
    analysis_id = db.identifier()
    directory = db.DATA / "frames" / asset_id / analysis_id
    frames, boundaries = media.sample_frames(asset, directory, reference)
    asset = get_required("assets", asset_id)
    enriched = dict(asset, _shot_boundaries=boundaries)
    job_update(job_id, message=f"分析 {len(frames)} 张证据帧" if frames else "分析音频内容", progress=35)
    result = providers().analyze_asset(enriched, frames, reference=reference)
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
        raise ValueError("模型返回的素材分析不完整")
    result.setdefault("tags", [])
    result.setdefault("segments", [])
    if not isinstance(result["tags"], list) or not isinstance(result["segments"], list):
        raise ValueError("素材标签或片段格式不正确")
    duration = float(asset["duration"])
    for segment in result["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("模型片段格式不正确")
        start = float(segment.get("start", 0))
        end = float(segment.get("end", duration))
        if not math.isfinite(start + end) or start < 0 or end < start or (duration and end > duration + 0.2):
            raise ValueError("模型片段时间超出实际素材，未保存此分析")
        segment["start"], segment["end"] = start, min(end, duration) if duration else end
    result["evidence_frames"] = [{"time": frame["time"], "url": db.media_url(frame["path"])} for frame in frames]
    result["analysis_id"] = analysis_id
    result["analyzed_at"] = db.now()
    result["coverage"] = {"method": "sampled_frames", "sampled_frame_count": len(frames), "detected_shot_count": max(0, len(boundaries) - 1) if reference else None, "complete_frame_review": False}
    if reference:
        result["shot_boundaries"] = boundaries
        result["reference_analysis"] = True
    # Reference and ordinary analysis live under the same immutable media identity.
    latest = get_required("assets", asset_id)
    result["audio_status"] = latest.get("audio_status", "not_transcribed" if latest.get("has_audio") else "no_audio")
    result["audio_used_in_analysis"] = bool(enriched.get("transcript"))
    if latest.get("transcript"):
        result["transcript"] = latest["transcript"]
        result["audio_analyzed"] = True
        result["audio_note"] = "已完成本地语音转录，自动识别文本以原声音轨为准。"
    else:
        result["audio_analyzed"] = False
        result["audio_note"] = "语音尚未转录，不能推断对白；画面分析可独立使用。" if latest.get("has_audio") else "原素材没有音轨。"
    old = latest.get("analysis") or {}
    merged = {**old, **result}
    db.create("analyses", {"id": analysis_id, "asset_id": asset_id, "reference": reference, "source_job_id": job_id, "result": merged})
    db.update("assets", asset_id, {"analysis_status": "succeeded", "analysis": merged, "analysis_error": None})
    return {"asset_id": asset_id, "analysis": merged}


def analyze(asset_id, reference=False, force=True):
    with _asset_lock(asset_id, "analysis_queue"):
        asset = get_required("assets", asset_id)
        reference = reference or asset.get("purpose") == "reference"
        if reference and asset["kind"] != "video":
            raise ValueError("参考样片分析需要视频素材")
        for job in db.records("jobs", 5000):
            acceptable = {"reference"} if reference else {"analysis", "reference"}
            if job["kind"] in acceptable and job["status"] in {"queued", "running"} and job.get("payload", {}).get("asset_id") == asset_id:
                return job
        db.update("assets", asset_id, {"analysis_status": "queued", "analysis_error": None})
        job = enqueue("reference" if reference else "analysis", {"asset_id": asset_id, "force": bool(force)},
                      lambda jid: _analysis(asset_id, reference, jid, force=force), executor=ANALYSIS_EXECUTOR)
        db.update("assets", asset_id, {"analysis_job_id": job["id"]})
        return job


def validate_plan(plan, assets, selection=None):
    lookup = {asset["id"]: asset for asset in assets}
    result = dict(plan)
    clips = result.get("clips")
    if not isinstance(clips, list) or not 1 <= len(clips) <= 120:
        raise ValueError("剪辑方案需要 1–120 个有效片段")
    ratio = result.get("ratio", "9:16")
    if ratio not in RATIOS:
        raise ValueError("画面比例不支持")
    width, height = RATIOS[ratio]
    normalized = []
    elapsed = 0.0
    for index, raw in enumerate(clips):
        if not isinstance(raw, dict):
            raise ValueError("片段格式不正确")
        asset = lookup.get(raw.get("asset_id"))
        if asset is None or asset["kind"] == "audio":
            raise ValueError(f"第 {index + 1} 段需要有效的图片或视频素材")
        try:
            start = float(raw.get("start", 0))
            duration = float(raw.get("duration") or (float(raw.get("end", 0)) - start))
            end = float(raw.get("end", start + duration))
        except (TypeError, ValueError):
            raise ValueError(f"第 {index + 1} 段的时间无效") from None
        if not all(math.isfinite(value) for value in (start, end, duration)) or start < 0 or duration < 0.2 or duration > 600 or end <= start:
            raise ValueError(f"第 {index + 1} 段的起止时间或时长无效")
        if abs((end - start) - duration) > 0.15:
            raise ValueError(f"第 {index + 1} 段的时长与所选区间不一致")
        if asset["kind"] == "video" and end > float(asset["duration"]) + 0.02:
            raise ValueError(f"第 {index + 1} 段超出原视频实际时长")
        if selection and asset["kind"] == "video":
            allowed = [part for part in selection if part["asset_id"] == asset["id"]]
            if allowed and not any(start >= part["start"] - .001 and end <= part["end"] + .001 for part in allowed):
                raise ValueError(f"第 {index + 1} 段超出用户选定的素材区间")
        if asset["kind"] == "image" and start != 0:
            raise ValueError("图片片段应从 0 秒开始")
        duration = end - start
        transition = raw.get("transition", "cut")
        if transition not in {"cut", "fade"}:
            raise ValueError("当前转场支持 cut 和 fade")
        clip = {"id": str(raw.get("id") or db.identifier()), "asset_id": asset["id"], "start": round(start, 4), "end": round(end, 4), "duration": round(duration, 4), "label": str(raw.get("label") or f"镜头 {index + 1}")[:200], "caption": str(raw.get("caption") or "")[:1200], "role": str(raw.get("role") or "body")[:100], "transition": transition, "timeline_start": round(elapsed, 4), "timeline_end": round(elapsed + duration, 4)}
        normalized.append(clip)
        elapsed += duration
    if elapsed > 3600:
        raise ValueError("单部作品最长支持 60 分钟")
    music_id = result.get("music_asset_id")
    if music_id and (music_id not in lookup or lookup[music_id]["kind"] not in {"audio", "video"} or not lookup[music_id].get("has_audio")):
        raise ValueError("配乐必须引用带音轨的素材")
    result.update(clips=normalized, ratio=ratio, width=width, height=height, duration=round(elapsed, 4))
    return result


def _project_lock(project_id):
    with PROJECT_LOCKS_LOCK:
        return PROJECT_LOCKS.setdefault(project_id, threading.Lock())


def plan_changes(previous, current):
    """Describe changes against the actual parent, including historical branches."""
    if not previous:
        return ["建立初始方案"]
    changes = []
    before, after = previous.get("clips", []), current.get("clips", [])
    if previous.get("title") != current.get("title"):
        changes.append("修改标题")
    if previous.get("ratio") != current.get("ratio"):
        changes.append("调整画面比例")
    if len(before) != len(after):
        changes.append(f"镜头数 {len(before)} → {len(after)}")
    if [c.get("asset_id") for c in before] != [c.get("asset_id") for c in after]:
        changes.append("调整素材或镜头顺序")
    if [(c.get("start"), c.get("end")) for c in before] != [(c.get("start"), c.get("end")) for c in after]:
        changes.append("调整入出点或展示时长")
    if [c.get("caption", "") for c in before] != [c.get("caption", "") for c in after]:
        changes.append("修改字幕")
    if [c.get("transition", "cut") for c in before] != [c.get("transition", "cut") for c in after]:
        changes.append("调整转场")
    if previous.get("music_asset_id") != current.get("music_asset_id"):
        changes.append("更换配乐")
    return changes or ["调整叙事说明"]


def save_plan(project_id, proposed, asset_ids=None, expected_base=UNSET, base_plan_id=UNSET,
              *, activate=True, origin=None, selection=UNSET):
    with _project_lock(project_id):
        project = get_required("projects", project_id)
        if expected_base is not UNSET and project.get("plan_id") != expected_base:
            raise ValueError("方案已发生修改，请基于最新版本重新提出要求")
        previous = db.get("plans", project.get("plan_id")) if project.get("plan_id") else None
        if base_plan_id is UNSET:
            base_plan_id = proposed.get("base_plan_id") or proposed.get("id") or (previous["id"] if previous else None)
        source_base = get_required("plans", base_plan_id) if base_plan_id else None
        if source_base and source_base["project_id"] != project_id:
            raise ValueError("修改所依据的方案不属于当前作品")
        merged = {**(source_base or {}), **proposed, "project_id": project_id}
        referenced = [clip.get("asset_id") for clip in merged.get("clips", []) if isinstance(clip, dict)]
        if merged.get("music_asset_id"):
            referenced.append(merged["music_asset_id"])
        if project.get("reference_id") and project["reference_id"] in referenced:
            raise ValueError("参考样片仅用于分析叙事与节奏，不能作为新作品的画面或音轨")
        selected = list(dict.fromkeys([*(asset_ids or project.get("asset_ids", [])), *referenced]))
        assets = [get_required("assets", item) for item in selected]
        merged = validate_plan(merged, assets, project.get("selection") if selection is UNSET else selection)
        comparable = ("title", "brief", "template_id", "ratio", "width", "height", "duration", "clips", "music_asset_id", "summary")
        if activate and source_base and all(merged.get(key) == source_base.get(key) for key in comparable):
            return source_base
        versions = [p.get("version", 0) for p in db.records("plans", 5000) if p["project_id"] == project_id]
        inherited = (source_base or {}).get("origin") or {}
        lineage = {key: inherited[key] for key in ("batch_id", "candidate_key", "root_candidate_plan_id") if inherited.get(key)}
        if inherited.get("source") == "candidate":
            lineage["root_candidate_plan_id"] = source_base["id"]
        if (origin or {}).get("source") == "candidate":
            lineage = {}
        provenance = {**lineage, "source": "manual", "actor": "local_user", **(origin or {})}
        changes = plan_changes(source_base, merged)
        merged.update(id=db.identifier(), project_id=project_id, version=max(versions, default=0) + 1,
                      base_version_id=source_base["id"] if source_base else None,
                      origin=provenance, change_summary="；".join(changes), changes=changes)
        merged.pop("base_plan_id", None)
        if provenance["source"] != "candidate":
            merged.pop("candidate_strategy", None)
            merged.pop("difference_summary", None)
        merged.setdefault("title", project["title"])
        merged.setdefault("brief", project["brief"])
        merged.setdefault("template_id", project.get("template_id", "montage"))
        merged.pop("created_at", None)
        merged.pop("updated_at", None)
        merged.pop("url", None)
        record = db.create("plans", merged)
        plan_path = db.DATA / "plans" / (record["id"] + ".json")
        plan_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        record = db.update("plans", record["id"], {"url": db.media_url(plan_path)})
        if activate:
            db.update("projects", project_id, {"plan_id": record["id"], "title": record["title"], "asset_ids": list(dict.fromkeys(selected))})
        return record


def _plan_job(project_id, brief, job_id, previous_id=None, expected_head_id=UNSET):
    project = get_required("projects", project_id)
    asset_ids = project["asset_ids"]
    if not asset_ids:
        raise ValueError("请先上传并选择至少一份图片或视频素材")
    for asset_id in asset_ids:
        asset = get_required("assets", asset_id)
        if not asset.get("analysis"):
            try:
                _analysis(asset_id, False, job_id)
            except Exception as error:
                status = "needs_configuration" if isinstance(error, getattr(providers(), "ConfigurationError", ())) else "failed"
                db.update("assets", asset_id, {"analysis_status": status, "analysis_error": _error_text(error)})
                raise
    assets = [get_required("assets", asset_id) for asset_id in asset_ids]
    reference = None
    if project.get("reference_id"):
        reference = get_required("assets", project["reference_id"])
        if not (reference.get("analysis") or {}).get("reference_analysis"):
            try:
                _analysis(reference["id"], True, job_id)
            except Exception as error:
                status = "needs_configuration" if isinstance(error, getattr(providers(), "ConfigurationError", ())) else "failed"
                db.update("assets", reference["id"], {"analysis_status": status, "analysis_error": _error_text(error)})
                raise
            reference = get_required("assets", reference["id"])
    previous = get_required("plans", previous_id) if previous_id else None
    if previous and previous["project_id"] != project_id:
        raise ValueError("修改所依据的方案不属于当前作品")
    audio_targets = [asset for asset in assets + ([reference] if reference else []) if asset.get("has_audio") and not asset.get("transcript")]
    if audio_targets:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pending = [get_required("assets", asset["id"]) for asset in audio_targets]
            if not any(asset.get("audio_status") in {"queued", "running", "downloading_model"} for asset in pending):
                break
            job_update(job_id, progress=55, message="等待本地语音转录，优先按真实对白组织叙事")
            time.sleep(1)
        assets = [get_required("assets", asset_id) for asset_id in asset_ids]
        if reference:
            reference = get_required("assets", reference["id"])
    job_update(job_id, message="组织素材与叙事节奏，生成可执行剪辑结构", progress=65)
    audio_incomplete = any(asset.get("has_audio") and not asset.get("transcript") for asset in assets + ([reference] if reference else []))
    options = {"template_id": previous.get("template_id", project["template_id"]) if previous else project["template_id"], "duration": previous.get("duration", project["target_duration"]) if previous else project["target_duration"], "ratio": previous.get("ratio", project["ratio"]) if previous else project["ratio"], "selection": project.get("selection", []), "audio_incomplete": audio_incomplete}
    proposed = providers().create_plan(brief, assets, options, reference=reference, previous=previous)
    if not isinstance(proposed, dict):
        raise ValueError("模型没有返回有效的剪辑方案")
    proposed.update(brief=brief, ratio=options["ratio"], template_id=options["template_id"])
    proposed["audio_incomplete"] = audio_incomplete
    if audio_incomplete:
        proposed["analysis_note"] = "部分语音尚未完成转录，本轮仅使用已核实的画面、字幕和现有转录，不推断缺失对白。"
    # Reject a stale AI revision instead of silently overwriting a newer manual edit.
    current = get_required("projects", project_id)
    expected_head = previous_id if expected_head_id is UNSET else expected_head_id
    if current.get("plan_id") != expected_head:
        raise ValueError("生成期间方案已发生修改，请基于最新版本重新提出要求")
    plan = save_plan(project_id, proposed, expected_base=expected_head, base_plan_id=previous_id,
                     origin={"source": "ai_revision" if previous_id else "ai_generation", "actor": "model", "job_id": job_id})
    db.update("projects", project_id, transform=lambda item: {**item, "messages": item.get("messages", []) + [{"id": db.identifier(), "role": "assistant", "content": proposed.get("summary") or "剪辑方案已生成，可以调整片段并渲染预览。", "created_at": db.now(), "plan_id": plan["id"]}]})
    result = {"project_id": project_id, "plan": plan}
    if previous_id is None and project.get("auto_render"):
        result["render_job_id"] = render(project_id, plan["id"])["id"]
    return result


def create_project(payload):
    brief = str(payload.get("brief", "")).strip()
    if not brief:
        raise ValueError("请描述你要制作的视频")
    asset_ids = list(dict.fromkeys(payload.get("asset_ids", [])))
    if not asset_ids:
        raise ValueError("请选择至少一份图片或视频素材")
    assets = {asset_id: get_required("assets", asset_id) for asset_id in asset_ids}
    selection = []
    for part in payload.get("selection") or []:
        if not isinstance(part, dict) or part.get("asset_id") not in assets:
            raise ValueError("所选片段不属于本次素材")
        asset = assets[part["asset_id"]]
        start, end = float(part.get("start", 0)), float(part.get("end", 0))
        if not math.isfinite(start + end) or start < 0 or (asset["kind"] == "video" and (end <= start or end > asset["duration"] + .02)):
            raise ValueError("素材选择区间无效")
        selection.append({"asset_id": asset["id"], "start": start, "end": end})
    ratio = payload.get("ratio", "9:16")
    if ratio not in RATIOS:
        raise ValueError("画面比例不支持")
    duration = float(payload.get("duration") or 30)
    if not math.isfinite(duration) or not 1 <= duration <= 3600:
        raise ValueError("目标时长应在 1–3600 秒之间")
    reference_id = payload.get("reference_id")
    if reference_id:
        reference = get_required("assets", reference_id)
        if reference["kind"] != "video":
            raise ValueError("参考样片需要视频素材")
        if reference_id in asset_ids:
            raise ValueError("请用自己的素材制作新片，参考样片只能用于分析")
    from decisions import case_references
    case_ids, case_snapshot = case_references(payload.get("reference_case_ids") or [])
    platform = str(payload.get("platform") or "").strip()
    account = str(payload.get("account") or "").strip()
    if platform and platform not in {"douyin", "xiaohongshu", "shipinhao", "channels", "wechat", "抖音", "小红书", "视频号"}:
        raise ValueError("平台仅支持抖音、小红书和视频号")
    platform = {"抖音": "douyin", "小红书": "xiaohongshu", "视频号": "channels",
                "shipinhao": "channels", "wechat": "channels"}.get(platform, platform)
    if len(account) > 200:
        raise ValueError("账号名称过长")
    project = db.create("projects", {"title": brief[:50], "brief": brief, "asset_ids": asset_ids, "selection": selection, "template_id": payload.get("template_id", "free"), "ratio": ratio, "target_duration": duration, "reference_id": reference_id, "reference_case_ids": case_ids, "reference_case_snapshot": case_snapshot, "platform": platform, "account": account, "auto_render": bool(payload.get("auto_render", False)), "plan_id": None, "latest_render": None, "messages": [{"id": db.identifier(), "role": "user", "content": brief, "created_at": db.now()}]})
    return enqueue("plan", {"project_id": project["id"]}, lambda jid: _plan_job(project["id"], brief, jid))


def revise(project_id, brief, base_plan_id=None):
    with _project_lock(project_id):
        project = get_required("projects", project_id)
        brief = str(brief).strip()
        if not brief:
            raise ValueError("请输入修改要求")
        if not project.get("plan_id"):
            raise ValueError("当前作品还没有可修改的方案")
        base_id = base_plan_id or project["plan_id"]
        base = get_required("plans", base_id)
        if base["project_id"] != project_id:
            raise ValueError("修改所依据的方案不属于当前作品")
        if any(job["status"] in {"running", "queued"} and job.get("payload", {}).get("project_id") == project_id and job["kind"] in {"plan", "revision"} for job in db.records("jobs")):
            raise ValueError("此作品还有方案生成中，请等待本轮完成")
        head_id = project["plan_id"]
        db.update("projects", project_id, transform=lambda item: {**item, "messages": item.get("messages", []) + [{"id": db.identifier(), "role": "user", "content": brief, "created_at": db.now(), "base_plan_id": base_id}]})
        return enqueue("revision", {"project_id": project_id, "base_version_id": base_id, "head_version_id": head_id}, lambda jid: _plan_job(project_id, brief, jid, base_id, expected_head_id=head_id))


def retry_initial_plan(project_id):
    """Retry the saved initial brief in the same project, never fork its identity."""
    with _project_lock(project_id):
        project = get_required("projects", project_id)
        if project.get("plan_id"):
            raise ValueError("此作品已有成功方案，请使用修改方案或重新渲染")
        planning_jobs = [job for job in db.records("jobs", 5000) if job["kind"] in {"plan", "revision"} and job.get("payload", {}).get("project_id") == project_id]
        if any(job["status"] in {"queued", "running"} for job in planning_jobs):
            raise ValueError("此作品已有方案任务正在处理，请等待本轮完成")
        if not project.get("brief") or not project.get("asset_ids"):
            raise ValueError("原始制作要求或所选素材缺失，无法恢复初始规划")
        for asset_id in project["asset_ids"]:
            get_required("assets", asset_id)
        if project.get("reference_id"):
            get_required("assets", project["reference_id"])
        payload = {"project_id": project_id, "retry_of": planning_jobs[0]["id"] if planning_jobs else None}
        return enqueue("plan", payload, lambda jid: _plan_job(project_id, project["brief"], jid))


def project_view(project_id):
    project = get_required("projects", project_id)
    project["plan"] = db.get("plans", project.get("plan_id")) if project.get("plan_id") else None
    project["versions"] = sorted([p for p in db.records("plans", 5000) if p["project_id"] == project_id], key=lambda p: p["version"], reverse=True)
    project["renders"] = [r for r in db.records("renders", 5000) if r["project_id"] == project_id]
    active_plan = next((job for job in db.records("jobs", 5000) if job["kind"] in {"plan", "revision"} and job["status"] in {"queued", "running"} and job.get("payload", {}).get("project_id") == project_id), None)
    project["active_plan_job_id"] = active_plan["id"] if active_plan else None
    project["can_retry_plan"] = not bool(project.get("plan_id")) and active_plan is None
    return project


def render(project_id, plan_id=None):
    project = get_required("projects", project_id)
    plan = get_required("plans", plan_id or project.get("plan_id"))
    if plan["project_id"] != project_id:
        raise ValueError("方案不属于当前作品")
    def operation(job_id):
        from renderer import render_plan
        selected = list(dict.fromkeys([clip["asset_id"] for clip in plan["clips"]] + ([plan["music_asset_id"]] if plan.get("music_asset_id") else [])))
        if project.get("reference_id") and project["reference_id"] in selected:
            raise ValueError("参考样片不能作为新作品的画面或音轨")
        assets = [get_required("assets", asset_id) for asset_id in selected]
        checked = validate_plan(plan, assets, project.get("selection"))
        output = db.DATA / "renders" / project_id / f"v{plan['version']}-{job_id}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        def progress(value, message=None):
            if isinstance(value, dict):
                job_update(job_id, progress=float(value.get("progress", .1)) * 100, message=value.get("message", "渲染中"))
            else:
                job_update(job_id, progress=value, message=message or "按剪辑方案渲染")
        result = render_plan(checked, assets, output, progress=progress)
        actual_path = Path(result.get("path", output))
        info = media.probe(actual_path)
        if info["kind"] != "video" or abs(info["duration"] - plan["duration"]) > 0.35:
            raise ValueError("渲染结果时长与方案不一致，未标记完成")
        asset = register_asset(actual_path, f"{project['title']}_v{plan['version']}.mp4", source="render")
        record = db.create("renders", {"project_id": project_id, "plan_id": plan["id"], "version": plan["version"], "asset_id": asset["id"], "url": asset["url"], "thumbnail_url": asset["thumbnail_url"], "duration": info["duration"], "width": info["width"], "height": info["height"], "sha256": asset["sha256"], "manifest_url": db.media_url(result["manifest_path"]) if result.get("manifest_path") else None, "compiled_timeline": result.get("compiled_timeline")})
        db.update("projects", project_id, transform=lambda current:
                  {**current, "latest_render": record} if current.get("plan_id") == plan["id"] else current)
        return record
    return enqueue("render", {"project_id": project_id, "plan_id": plan["id"]}, operation)


def _validate_download_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("供应商返回了无效的下载地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("供应商下载地址不可用")
    except socket.gaierror:
        raise ValueError("供应商下载地址暂时无法解析") from None


def _download(url, target):
    for _ in range(4):
        _validate_download_url(url)
        with httpx.stream("GET", url, timeout=httpx.Timeout(60, connect=20), follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise ValueError("生成结果下载失败，可继续查询原任务")
            total = 0
            with Path(target).open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > 1024 * 1024 * 1024:
                        raise ValueError("生成文件超过 1GB，已停止下载")
                    handle.write(chunk)
            return
    raise ValueError("生成结果下载跳转过多")


def _poll_generation(job_id):
    job = get_required("jobs", job_id)
    task_id = job.get("provider_state", {}).get("task_id")
    if not task_id:
        raise ValueError("未取得供应商任务编号，无法自动重试付费提交")
    started = time.monotonic()
    while time.monotonic() - started < 2400:
        result = providers().query_generation(task_id)
        status = str(result.get("status", "")).lower()
        job_update(job_id, progress=result.get("progress") or 25, message=result.get("message") or "供应商生成中")
        if status in {"succeeded", "success", "completed", "complete"}:
            existing = next((asset for asset in db.records("assets", 5000) if asset.get("generation_id") == job_id), None)
            if existing:
                return {"asset_id": existing["id"], "url": existing["url"], "thumbnail_url": existing["thumbnail_url"], "duration": existing["duration"]}
            if not result.get("url"):
                raise ValueError("供应商已完成但没有返回下载地址，可继续查询原任务")
            job_update(job_id, progress=90, message="下载并校验生成视频")
            target = db.DATA / "generations" / f"{job_id}.mp4"
            temporary = target.with_suffix(".part.mp4")
            _download(result["url"], temporary)
            info = media.probe(temporary)
            if info["kind"] != "video":
                raise ValueError("供应商结果不是有效的视频")
            media.run([media.FFMPEG, "-nostdin", "-v", "error", "-xerror", "-i", temporary, "-f", "null", "-"], timeout=180)
            temporary.replace(target)
            asset = register_asset(target, f"AI生成_{job_id[:8]}.mp4", source="generation", generation_id=job_id)
            job_update(job_id, provider_state={"task_id": task_id, "submitting": False, "status": "succeeded"})
            return {"asset_id": asset["id"], "url": asset["url"], "thumbnail_url": asset["thumbnail_url"], "duration": asset["duration"]}
        if status in {"failed", "cancelled", "canceled", "rejected"}:
            job_update(job_id, provider_state={"task_id": task_id, "submitting": False, "status": status, "terminal_failure": True})
            raise ValueError(result.get("error") or "供应商生成失败；原任务编号已保留，不自动付费重做")
        time.sleep(8)
    raise ValueError("供应商仍在处理，点击继续查询可沿原任务接续")


def generate(payload):
    submission_id = payload.get("submission_id")
    if submission_id is not None:
        try:
            submission_id = str(uuid.UUID(str(submission_id)))
        except (ValueError, AttributeError):
            raise ValueError("submission_id 需要有效的 UUID") from None
    # Freeze exactly what was reviewed. Local deduplication metadata is not sent
    # to the provider; array order and all user-supplied options remain significant.
    request = {key: value for key, value in payload.items() if key not in {"submission_id", "request_fingerprint"}}
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    request = json.loads(canonical)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    stored_payload = {**request, **({"submission_id": submission_id, "request_fingerprint": fingerprint} if submission_id else {})}

    def operation(job_id):
        # Resolve only when executing the first submission. Replaying an existing
        # key remains possible even if its old reference asset is unavailable.
        asset_ids = list(dict.fromkeys([*request.get("reference_image_ids", []), *request.get("reference_video_ids", []), *request.get("reference_audio_ids", []), *([request["first_frame_id"]] if request.get("first_frame_id") else []), *([request["last_frame_id"]] if request.get("last_frame_id") else [])]))
        assets = [get_required("assets", asset_id) for asset_id in asset_ids]
        job_update(job_id, message="准备生成请求", provider_state={"submitting": False})
        # Persist submission intent before a paid network request. An unknown result is never reposted.
        job_update(job_id, provider_state={"submitting": True, "submitted_at": db.now(), "request": request})
        try:
            result = providers().create_generation(request, assets)
        except getattr(providers(), "SubmissionUnknown", RuntimeError):
            raise
        except Exception:
            job_update(job_id, provider_state={"submitting": False, "status": "rejected", "terminal_failure": True})
            raise
        task_id = result.get("task_id") if isinstance(result, dict) else result
        if not task_id:
            raise providers().SubmissionUnknown("未收到供应商任务编号，提交结果未知，未自动重试")
        job_update(job_id, provider_state={"task_id": str(task_id), "submitting": False, "status": "submitted"}, message="供应商已接收，等待生成", progress=10)
        return _poll_generation(job_id)
    if submission_id is None:
        return enqueue("generation", stored_payload, operation)
    with GENERATION_SUBMISSION_LOCK:
        # SQLite's write transaction also protects against two service processes.
        # Never reuse enqueue(job_id=...) here: it would restart the paid submit.
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data FROM jobs WHERE json_extract(data,'$.kind')='generation' "
                               "AND json_extract(data,'$.payload.submission_id')=? LIMIT 1", (submission_id,)).fetchone()
            if row:
                existing = json.loads(row[0])
                previous_payload = existing.get("payload", {})
                previous_fingerprint = previous_payload.get("request_fingerprint")
                if previous_fingerprint is None:
                    previous_request = {key: value for key, value in previous_payload.items()
                                        if key not in {"submission_id", "request_fingerprint"}}
                    previous_fingerprint = hashlib.sha256(json.dumps(previous_request, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                if previous_fingerprint != fingerprint:
                    raise ValueError("此 submission_id 已用于不同的生成内容；请重新审阅后使用新的提交编号")
                return existing
            stamp = db.now()
            job = {"id": db.identifier(), "kind": "generation", "status": "queued", "progress": 0,
                   "message": "等待处理", "result": None, "error": None, "payload": stored_payload,
                   "provider_state": {}, "created_at": stamp, "updated_at": stamp}
            conn.execute("INSERT INTO jobs(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                         (job["id"], json.dumps(job, ensure_ascii=False, allow_nan=False), stamp, stamp))
        return _schedule_job(job, operation)


def generation_plan(payload):
    asset_ids = list(dict.fromkeys([*payload.get("asset_ids", []), *payload.get("reference_image_ids", []), *payload.get("reference_video_ids", []), *payload.get("reference_audio_ids", []), *([payload["first_frame_id"]] if payload.get("first_frame_id") else []), *([payload["last_frame_id"]] if payload.get("last_frame_id") else [])]))
    assets = [get_required("assets", asset_id) for asset_id in asset_ids]
    def operation(job_id):
        job_update(job_id, progress=15, message="根据创作模板组织镜头与生成提示词")
        result = providers().create_generation_storyboard(payload, assets)
        if not isinstance(result, dict) or not isinstance(result.get("shots"), list):
            raise ValueError("模型未返回有效的生成分镜")
        path = db.DATA / "plans" / f"storyboard-{job_id}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"storyboard": result, "url": db.media_url(path)}
    return enqueue("generation_plan", dict(payload), operation)


def resume_generation(job_id):
    job = get_required("jobs", job_id)
    if job["kind"] != "generation" or not job.get("provider_state", {}).get("task_id"):
        raise ValueError("只有已取得供应商任务编号的生成任务可以继续查询")
    if job["status"] in {"running", "queued", "succeeded"}:
        raise ValueError("此任务正在执行或已经完成")
    if job.get("provider_state", {}).get("terminal_failure"):
        raise ValueError("供应商已明确终止此任务；若要重新生成，请创建新的生成任务")
    return enqueue("generation", job["payload"], _poll_generation, job_id=job_id)
