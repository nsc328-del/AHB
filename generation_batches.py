"""Frozen, versioned H3 batches. Paid submissions are never replayed implicitly."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter

import media
import storage as db
import workflow

router = APIRouter()
LOCK = threading.RLock()
BUSY = {"queued", "running"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _read(conn, table, ident):
    row = conn.execute(f"SELECT data FROM {table} WHERE id=?", (ident,)).fetchone()
    if not row:
        raise ValueError("记录不存在或已不可用")
    return json.loads(row[0])


def _insert(conn, table, item):
    conn.execute(f"INSERT INTO {table}(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                 (item["id"], _json(item), item["created_at"], item["updated_at"]))


def _write(conn, table, item):
    item["updated_at"] = db.now()
    conn.execute(f"UPDATE {table} SET data=?,updated_at=? WHERE id=?", (_json(item), item["updated_at"], item["id"]))


def _new_job(kind, payload):
    stamp = db.now()
    return {"id": db.identifier(), "kind": kind, "payload": payload, "status": "queued", "progress": 0,
            "message": "等待处理", "result": None, "error": None, "provider_state": {}, "created_at": stamp, "updated_at": stamp}


def _key(action, plan_id, payload, parent_id=None):
    try:
        submission_id = str(uuid.UUID(str(payload.get("submission_id", ""))))
    except (ValueError, AttributeError):
        raise ValueError("请提供有效的生成提交编号") from None
    fingerprint = hashlib.sha256(_json({"action": action, "plan_job_id": plan_id,
        "parent_batch_id": parent_id, "payload": {k: v for k, v in payload.items() if k != "submission_id"}}).encode()).hexdigest()
    return submission_id, fingerprint


def _existing(conn, key, fingerprint):
    row = conn.execute("SELECT data FROM generation_batches WHERE json_extract(data,'$.submission_id')=? LIMIT 1", (key,)).fetchone()
    if not row:
        return None
    item = json.loads(row[0])
    if item.get("request_fingerprint") != fingerprint:
        raise ValueError("此提交编号已用于不同内容，请重新确认后再生成")
    return item


def _revision(view, expected):
    if not isinstance(expected, int) or isinstance(expected, bool) or expected != view["revision"]:
        raise ValueError("方案已修改，请刷新并保存当前版本后生成")
    if view["storyboard"].get("missing_inputs"):
        raise ValueError("请先补齐方案所需素材并重新生成方案")


def _prepare(view, ratio=None, resolution=None, shot_id=None):
    shots = view["storyboard"]["shots"]
    if not shots or len(shots) > 30:
        raise ValueError("方案片段数量须为 1 至 30")
    if shot_id is not None:
        shots = [shot for shot in shots if shot["id"] == shot_id]
        if not shots:
            raise ValueError("片段不存在")
    prepared = []
    for shot in shots:
        if shot.get("prompt_stale"):
            raise ValueError(f"{shot.get('label', '片段')}的提示词尚未同步，请先更新提示词")
        ids = list(dict.fromkeys([*shot.get("reference_asset_ids", []),
            *[shot[key] for key in ("first_asset_id", "last_asset_id") if shot.get(key)]]))
        assets = [workflow.get_required("assets", ident) for ident in ids]
        try:
            checked = workflow.providers().validate_generation_review_shot(shot, assets)
            request = {"prompt": checked["prompt"], "mode": checked["mode"], "duration": int(checked["duration"]),
                "ratio": ratio or view["request"].get("ratio") or "9:16",
                "resolution": resolution or view["request"].get("resolution") or "768P",
                "first_frame_id": checked.get("first_asset_id", ""), "last_frame_id": checked.get("last_asset_id", ""),
                "require_h3_prompt_contract": True, "source": "storyboard"}
            for kind in ("image", "video", "audio"):
                request[f"reference_{kind}_ids"] = [asset["id"] for asset in assets
                    if asset["id"] in checked.get("reference_asset_ids", []) and asset["kind"] == kind]
            _, metadata = workflow.providers()._generation_body(request, assets)
        except getattr(workflow.providers(), "ProviderError", RuntimeError) as error:
            raise ValueError(str(error)) from None
        prepared.append({"shot": copy.deepcopy(checked), "request": request,
                         "amount": metadata["estimated_cost"], "currency": metadata["currency"]})
    return prepared


def _quote(rows):
    currencies = {row["currency"] for row in rows}
    if len(currencies) != 1:
        raise ValueError("此批次费用币种不一致")
    return {"amount": round(sum(row["amount"] for row in rows if not row.get("reused")), 3),
            "currency": next(iter(currencies)), "new_shot_count": sum(not row.get("reused") for row in rows),
            "per_shot": [{"shot_id": row.get("shot_id") or row["shot"]["id"],
                          "amount": 0 if row.get("reused") else row["amount"], "reused": bool(row.get("reused"))} for row in rows]}


def quote(plan_id, ratio=None, resolution=None, shot_id=None):
    view = workflow.get_generation_plan(plan_id)
    if view["storyboard"].get("missing_inputs"):
        raise ValueError("请先补齐方案所需素材")
    return _quote(_prepare(view, ratio, resolution, shot_id))


def _confirm(conn, view, shot_ids):
    job = _read(conn, "jobs", view["job_id"])
    current = workflow._generation_plan_view(job)
    _revision(current, view["revision"])
    review = copy.deepcopy(job.get("generation_review") or {"revision": 0, "shots": {}})
    stamp = db.now()
    changed = False
    for shot in current["storyboard"]["shots"]:
        if shot["id"] in shot_ids and not shot.get("confirmed"):
            review["shots"][shot["id"]] = {**review["shots"].get(shot["id"], {}), "confirmed": True, "confirmed_at": stamp}
            changed = True
    if changed:
        review.update(revision=current["revision"] + 1, updated_at=stamp)
        job["generation_review"] = review
        _write(conn, "jobs", job)
    return review["revision"]


def _safe_retry(job):
    state = job.get("provider_state", {})
    return job.get("status") in {"failed", "needs_configuration"} and not (
        state.get("submitting") or (state.get("task_id") and not state.get("terminal_failure")))


def _resumable(job):
    state = job.get("provider_state", {})
    return job.get("status") in {"unknown", "failed"} and bool(state.get("task_id")) and not state.get("terminal_failure")


def _settled_parent(conn, plan_id):
    # Older tabs could have submitted an independently reviewed shot before
    # batch generation existed. Its paid intent belongs to the same plan.
    standalone = conn.execute("SELECT data FROM jobs WHERE json_extract(data,'$.kind')='generation' "
                              "AND json_extract(data,'$.payload.storyboard_job_id')=?", (plan_id,)).fetchall()
    for raw in standalone:
        job = json.loads(raw[0])
        state = job.get("provider_state", {})
        if (job.get("status") in {"queued", "running", "unknown"} or _resumable(job)
                or (state.get("submitting") and not state.get("terminal_failure"))):
            raise ValueError("此方案仍有未完成或结果未知的单段生成任务，请先完成或查询原任务")
    rows = conn.execute("SELECT data FROM generation_batches WHERE json_extract(data,'$.plan_job_id')=? ORDER BY created_at DESC", (plan_id,)).fetchall()
    latest = None
    for raw in rows:
        batch = json.loads(raw[0])
        if latest is None:
            latest = batch
        jobs = [_read(conn, "jobs", segment["job_id"]) for segment in batch["segments"]]
        assembly = _read(conn, "jobs", batch["assembly_job_id"]) if batch.get("assembly_job_id") else None
        status, _ = _derive(batch, jobs, assembly)
        if status in {"queued", "running", "assembling", "unknown"}:
            raise ValueError("此方案仍有生成中或结果未知的版本，请先完成或查询已有任务")
    return latest


def _new_batch(conn, plan_id, rows, key, fingerprint, *, view=None, parent=None, revision=None):
    ident, stamp = db.identifier(), db.now()
    family_id = parent["family_id"] if parent else ident
    version = 1
    if parent:
        versions = conn.execute("SELECT json_extract(data,'$.version') FROM generation_batches WHERE json_extract(data,'$.family_id')=?", (family_id,)).fetchall()
        version = max(int(row[0]) for row in versions) + 1
    batch = {"id": ident, "plan_job_id": plan_id, "family_id": family_id,
             "parent_batch_id": parent["id"] if parent else None, "version": version, "status": "queued",
             "title": (view or {}).get("storyboard", {}).get("title") or (parent or {}).get("title") or "生成视频",
             "review_revision": revision, "submission_id": key, "request_fingerprint": fingerprint,
             "segments": [], "quote": _quote(rows), "assembly_job_id": None, "output": None, "error": None,
             "created_at": stamp, "updated_at": stamp}
    jobs = []
    for index, row in enumerate(rows):
        if row.get("reused"):
            segment = copy.deepcopy(row["segment"])
            segment["source_batch_id"] = segment.get("source_batch_id") or parent["id"]
        else:
            shot, request = row["shot"], copy.deepcopy(row["request"])
            request.update(batch_id=ident, batch_shot_id=shot["id"])
            job = _new_job("generation", {**request,
                "submission_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"cutroom:batch:{ident}:{shot['id']}")),
                "request_fingerprint": hashlib.sha256(_json(request).encode()).hexdigest()})
            _insert(conn, "jobs", job)
            jobs.append((job, request))
            segment = {"shot_id": shot["id"], "label": shot.get("label") or shot.get("title") or f"片段 {index + 1}",
                       "snapshot": copy.deepcopy(shot), "request": request, "job_id": job["id"], "source_batch_id": ident,
                       "amount": row["amount"], "currency": row["currency"]}
        segment["index"] = index
        batch["segments"].append(segment)
    _insert(conn, "generation_batches", batch)
    return batch, jobs


def _dispatch(batch, jobs):
    for job, request in jobs:
        workflow._schedule_job(job, workflow._generation_operation(request))
    reconcile(batch["id"])
    return get_batch(batch["id"])


def create(plan_id, payload):
    key, fingerprint = _key("create", plan_id, payload)
    with LOCK:
        with db.connection() as conn:
            prior = _existing(conn, key, fingerprint)
        if prior:
            return get_batch(prior["id"])
        view = workflow.get_generation_plan(plan_id)
        _revision(view, payload.get("expected_revision"))
        rows = _prepare(view, payload.get("ratio"), payload.get("resolution"))
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = _existing(conn, key, fingerprint)
            if prior:
                return public_batch(prior)
            parent = _settled_parent(conn, plan_id)
            revision = _confirm(conn, view, {row["shot"]["id"] for row in rows})
            batch, jobs = _new_batch(conn, plan_id, rows, key, fingerprint, view=view, parent=parent, revision=revision)
        return _dispatch(batch, jobs)


def _reused_row(segment):
    return {"shot_id": segment["shot_id"], "reused": True, "segment": segment,
            "amount": segment["amount"], "currency": segment["currency"]}


def regenerate(batch_id, payload):
    parent = workflow.get_required("generation_batches", batch_id)
    key, fingerprint = _key("regenerate", parent["plan_job_id"], payload, batch_id)
    with LOCK:
        with db.connection() as conn:
            prior = _existing(conn, key, fingerprint)
        if prior:
            return get_batch(prior["id"])
        shot_id = payload.get("shot_id")
        target = next((row for row in parent["segments"] if row["shot_id"] == shot_id), None)
        if target is None:
            raise ValueError("片段不属于此批次")
        for segment in parent["segments"]:
            job = workflow.get_required("jobs", segment["job_id"])
            if segment["shot_id"] == shot_id:
                if job["status"] != "succeeded" and not _safe_retry(job):
                    raise ValueError("此片段仍在生成或提交结果未知，请先查询已有任务")
            elif job["status"] != "succeeded":
                raise ValueError("请先完成或重试其他失败片段，再单独重生成此片段")
        view = workflow.get_generation_plan(parent["plan_job_id"])
        _revision(view, payload.get("expected_revision"))
        changed = _prepare(view, target["request"]["ratio"], target["request"]["resolution"], shot_id)[0]
        rows = [changed if row["shot_id"] == shot_id else _reused_row(row) for row in parent["segments"]]
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = _existing(conn, key, fingerprint)
            if prior:
                return public_batch(prior)
            _settled_parent(conn, parent["plan_job_id"])
            revision = _confirm(conn, view, {shot_id})
            batch, jobs = _new_batch(conn, parent["plan_job_id"], rows, key, fingerprint,
                                    view=view, parent=parent, revision=revision)
        return _dispatch(batch, jobs)


def retry(batch_id, payload):
    parent = workflow.get_required("generation_batches", batch_id)
    key, fingerprint = _key("retry", parent["plan_job_id"], payload, batch_id)
    with LOCK:
        with db.connection() as conn:
            prior = _existing(conn, key, fingerprint)
        if prior:
            return get_batch(prior["id"])
        rows = []
        for segment in parent["segments"]:
            job = workflow.get_required("jobs", segment["job_id"])
            if job["status"] == "succeeded":
                rows.append(_reused_row(segment))
            elif _safe_retry(job):
                request = {k: v for k, v in segment["request"].items() if k not in {"batch_id", "batch_shot_id"}}
                rows.append({"shot": segment["snapshot"], "request": request, "amount": segment["amount"], "currency": segment["currency"]})
            else:
                raise ValueError("存在尚未完成或结果未知的片段，请先查询原任务，不能重复付费提交")
        if all(row.get("reused") for row in rows) and parent.get("output"):
            raise ValueError("完整视频已生成；如需修改，请使用单段重新生成")
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = _existing(conn, key, fingerprint)
            if prior:
                return public_batch(prior)
            _settled_parent(conn, parent["plan_job_id"])
            batch, jobs = _new_batch(conn, parent["plan_job_id"], rows, key, fingerprint,
                                    parent=parent, revision=parent["review_revision"])
        return _dispatch(batch, jobs)


def resume(batch_id):
    batch = workflow.get_required("generation_batches", batch_id)
    with LOCK:
        jobs = [workflow.get_required("jobs", segment["job_id"]) for segment in batch["segments"]]
        resumed = [workflow.resume_generation(job["id"])["id"] for job in jobs if _resumable(job)]
        if not resumed and not any(job["status"] in BUSY for job in jobs):
            raise ValueError("没有可继续查询的供应商任务")
    return get_batch(batch_id)


def _derive(batch, jobs, assembly):
    if batch.get("output"):
        return "succeeded", None
    if any(job["status"] in BUSY for job in jobs):
        return "running", None
    if any(job["status"] == "unknown" or _resumable(job) for job in jobs):
        return "unknown", "部分片段尚未取得确定结果"
    failed = [job for job in jobs if job["status"] != "succeeded"]
    if failed:
        return "failed", failed[0].get("error") or "部分片段未生成成功"
    if assembly:
        if assembly["status"] in BUSY:
            return "assembling", None
        if assembly["status"] == "succeeded":
            return "succeeded", None
        return "failed", assembly.get("error") or "完整视频拼接失败"
    return "assembling", None


def public_batch(batch):
    jobs = [workflow.get_required("jobs", row["job_id"]) for row in batch["segments"]]
    assembly = db.get("jobs", batch["assembly_job_id"]) if batch.get("assembly_job_id") else None
    status, error = _derive(batch, jobs, assembly)
    shots = []
    for segment, job in zip(batch["segments"], jobs):
        result = job.get("result") or {}
        shots.append({"shot_id": segment["shot_id"], "index": segment["index"], "label": segment["label"],
            "job_id": job["id"], "status": job["status"], "error": job.get("error"), "progress": job.get("progress", 0),
            "asset_id": result.get("asset_id"), "url": result.get("url"), "thumbnail_url": result.get("thumbnail_url"),
            "duration": result.get("duration") or segment["snapshot"]["duration"], "source_batch_id": segment["source_batch_id"],
            "resumable": _resumable(job), "snapshot": {k: segment["snapshot"].get(k) for k in ("script", "prompt", "duration", "mode")}})
    can_retry = status == "failed" and all(job["status"] == "succeeded" or _safe_retry(job) for job in jobs)
    return {**{key: batch.get(key) for key in ("id", "plan_job_id", "family_id", "parent_batch_id", "version", "title", "review_revision",
            "quote", "created_at", "updated_at")}, "status": status, "error": error, "shots": shots,
            "output": batch.get("output") or ((assembly or {}).get("result") if status == "succeeded" else None),
            "can_resume": any(_resumable(job) for job in jobs), "can_retry": can_retry,
            "assembly_job_id": batch.get("assembly_job_id")}


def get_batch(batch_id):
    reconcile(batch_id)
    return public_batch(workflow.get_required("generation_batches", batch_id))


def list_batches(plan_id):
    return [get_batch(row["id"]) for row in db.records("generation_batches", 5000) if row["plan_job_id"] == plan_id]


def reconcile(batch_id):
    schedule = None
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = _read(conn, "generation_batches", batch_id)
        previous = _json(batch)
        jobs = [_read(conn, "jobs", row["job_id"]) for row in batch["segments"]]
        assembly = _read(conn, "jobs", batch["assembly_job_id"]) if batch.get("assembly_job_id") else None
        if assembly and assembly["status"] == "succeeded":
            batch["output"] = assembly["result"]
        if not batch.get("output") and not assembly and all(job["status"] == "succeeded" for job in jobs):
            assembly = _new_job("generation_assembly", {"batch_id": batch_id})
            _insert(conn, "jobs", assembly)
            batch["assembly_job_id"] = assembly["id"]
            schedule = assembly
        batch["status"], batch["error"] = _derive(batch, jobs, assembly)
        if _json(batch) != previous:
            _write(conn, "generation_batches", batch)
    if schedule:
        workflow._schedule_job(schedule, lambda job_id: assemble(batch_id, job_id), executor=workflow.RENDER_EXECUTOR)


def on_job_finished(job_id):
    try:
        job = db.get("jobs", job_id)
        if not job or job.get("kind") not in {"generation", "generation_assembly"}:
            return
        for batch in db.records("generation_batches", 5000):
            if batch.get("assembly_job_id") == job_id or any(row["job_id"] == job_id for row in batch["segments"]):
                reconcile(batch["id"])
    except Exception:
        # A notification failure cannot relabel a completed paid task as failed.
        logging.getLogger(__name__).exception("Could not refresh generation batch state")


def reconcile_startup():
    # mark_interrupted() has already marked all interrupted paid requests.
    # Only completed segment sets may start a purely local assembly here.
    for batch in db.records("generation_batches", 5000):
        reconcile(batch["id"])


def assemble(batch_id, job_id):
    batch = workflow.get_required("generation_batches", batch_id)
    assets = []
    for row in batch["segments"]:
        job = workflow.get_required("jobs", row["job_id"])
        if job["status"] != "succeeded" or not job.get("result", {}).get("asset_id"):
            raise ValueError("所有片段成功后才能拼接")
        assets.append(workflow.get_required("assets", job["result"]["asset_id"]))
    directory = db.DATA / "generations" / "batches" / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    scratch = db.DATA / "tmp" / f"assembly-{job_id}"
    scratch.mkdir(parents=True, exist_ok=True)
    target = directory / f"v{batch['version']}-{job_id}.mp4"
    # Preserve the first generated frame's actual size; normalize all later
    # clips to it without stretching. Each normalized stream has identical caps.
    first = media.probe(assets[0]["path"])
    width, height = max(2, first["width"] // 2 * 2), max(2, first["height"] // 2 * 2)
    parts, total = [], 0.0
    for index, asset in enumerate(assets):
        info = media.probe(asset["path"])
        if info["kind"] != "video":
            raise ValueError("片段输出不是有效视频")
        duration = float(info["duration"])
        total += duration
        part = scratch / f"part-{index:03}.mp4"
        args = [media.FFMPEG, "-nostdin", "-v", "error", "-y", "-i", asset["path"]]
        if not info["has_audio"]:
            args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        args += ["-map", "0:v:0", "-map", "0:a:0" if info["has_audio"] else "1:a:0",
                 "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS",
                 "-af", f"aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
                 "-t", str(duration), "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-threads", "2",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "15360", part]
        media.run(args, timeout=max(120, int(duration * 12)))
        parts.append(part)
        workflow.job_update(job_id, progress=10 + (index + 1) / len(assets) * 75, message=f"拼接片段 {index + 1}/{len(assets)}")
    # Generated paths contain only server-generated IDs; the concat list never
    # incorporates user filenames or external URLs.
    listing = scratch / "concat.txt"
    listing.write_text("".join(f"file '{part.name}'\n" for part in parts))
    temporary = target.with_suffix(".part.mp4")
    media.run([media.FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
               "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", temporary], timeout=max(120, int(total * 4)))
    info = media.probe(temporary)
    if info["kind"] != "video" or not info["has_audio"] or abs(info["duration"] - total) > max(.35, len(parts) * .06):
        raise ValueError("拼接结果时长或音轨校验失败，原片段已保留")
    media.run([media.FFMPEG, "-nostdin", "-v", "error", "-xerror", "-i", temporary, "-f", "null", "-"], timeout=max(120, int(total * 3)))
    temporary.replace(target)
    asset = workflow.register_asset(target, f"{batch['title']}_v{batch['version']}.mp4", source="generation_batch", generation_id=job_id)
    db.update("assets", asset["id"], {"generation_batch_id": batch_id, "generation_plan_id": batch["plan_job_id"]})
    # Intermediates are rebuildable and can otherwise dwarf a finished video.
    for part in parts:
        part.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    try:
        scratch.rmdir()
    except OSError:
        pass
    return {"asset_id": asset["id"], "url": asset["url"], "thumbnail_url": asset["thumbnail_url"], "duration": asset["duration"]}


@router.get("/api/generation-plans/{plan_id}/batch-quote")
def quote_endpoint(plan_id: str, ratio: str | None = None, resolution: str | None = None, shot_id: str | None = None):
    return {"quote": quote(plan_id, ratio, resolution, shot_id)}


@router.get("/api/generation-plans/{plan_id}/batches")
def list_endpoint(plan_id: str):
    return {"batches": list_batches(plan_id)}


@router.post("/api/generation-plans/{plan_id}/batches")
def create_endpoint(plan_id: str, payload: dict):
    return {"batch": create(plan_id, payload)}


@router.get("/api/generation-batches/{batch_id}")
def detail_endpoint(batch_id: str):
    return {"batch": get_batch(batch_id)}


@router.post("/api/generation-batches/{batch_id}/regenerate")
def regenerate_endpoint(batch_id: str, payload: dict):
    return {"batch": regenerate(batch_id, payload)}


@router.post("/api/generation-batches/{batch_id}/retry")
def retry_endpoint(batch_id: str, payload: dict):
    return {"batch": retry(batch_id, payload)}


@router.post("/api/generation-batches/{batch_id}/resume")
def resume_endpoint(batch_id: str):
    return {"batch": resume(batch_id)}
