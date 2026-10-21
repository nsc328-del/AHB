"""Versioned animation plans, resumable scene work and local film assembly."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import media
import storage as db
import workflow

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="animation-studio")
LOCK = threading.RLock()
BUSY = {"queued", "running", "unknown"}
STYLES = {"free", "type", "data", "flow"}
SCENE_KEYS = ("id", "title", "description", "duration")


def provider():
    import animation_provider
    return animation_provider


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _read(conn, table, ident):
    row = conn.execute(f"SELECT data FROM {table} WHERE id=?", (ident,)).fetchone()
    if not row:
        raise ValueError("动画或版本不存在")
    return json.loads(row[0])


def _insert(conn, table, item):
    item = copy.deepcopy(item)
    item.setdefault("id", db.identifier())
    item.setdefault("created_at", db.now())
    item["updated_at"] = db.now()
    conn.execute(f"INSERT INTO {table}(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                 (item["id"], _json(item), item["created_at"], item["updated_at"]))
    return item


def _write(conn, table, item):
    item["updated_at"] = db.now()
    conn.execute(f"UPDATE {table} SET data=?,updated_at=? WHERE id=?",
                 (_json(item), item["updated_at"], item["id"]))
    return item


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label}需要 1–{limit} 个字符")
    return value.strip()


def _duration(value, maximum=60):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 2 <= value <= maximum:
        raise ValueError(f"时长需要在 2–{maximum} 秒之间")
    return round(round(float(value) * 30) / 30, 6)


def _params(payload):
    style, ratio = payload.get("style", "free"), payload.get("ratio", "16:9")
    if not isinstance(style, str) or not isinstance(ratio, str) or style not in STYLES or ratio not in workflow.RATIOS:
        raise ValueError("动画类型或画面比例无效")
    return {"brief": _text(payload.get("brief"), "视频描述", 16000), "style": style,
            "ratio": ratio, "duration": _duration(payload.get("duration", 20))}


def _scenes(values, previous=None):
    if not isinstance(values, list) or not 1 <= len(values) <= 30:
        raise ValueError("动画需要 1–30 个片段")
    rows = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("片段格式无效")
        ident = item.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
            raise ValueError("片段 ID 无效")
        rows.append({"id": ident, "title": _text(item.get("title"), "片段标题", 160),
                     "description": _text(item.get("description"), "片段描述", 16000),
                     "duration": _duration(item.get("duration"), 30)})
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("片段 ID 不能重复")
    if previous is not None and {r["id"] for r in rows} != {r["id"] for r in previous}:
        raise ValueError("修改方案不能添加、删除或更换片段 ID")
    if sum(r["duration"] for r in rows) > 60.00001:
        raise ValueError("动画总时长不能超过 60 秒")
    return rows


def _submission_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("提交标识需要有效 UUID") from None


def _fingerprint(action, animation_id, request):
    return hashlib.sha256(_json({"action": action, "animation_id": animation_id, "request": request}).encode()).hexdigest()


def _submitted(conn, submission_id, fingerprint):
    row = conn.execute("SELECT p.data FROM animation_projects p, json_each(p.data,'$.submissions') s WHERE s.key=?", (submission_id,)).fetchone()
    if not row:
        return None
    project = json.loads(row[0])
    receipt = project["submissions"][submission_id]
    if receipt["fingerprint"] != fingerprint:
        raise ValueError("同一提交标识不能用于不同内容")
    return _read(conn, "jobs", receipt["job_id"])


def _remember(conn, project, submission_id, fingerprint, job):
    project.setdefault("submissions", {})[submission_id] = {"fingerprint": fingerprint, "job_id": job["id"]}
    project["active_job_id"] = job["id"]
    _write(conn, "animation_projects", project)


def _job(conn, action, project, request, state=None):
    return _insert(conn, "jobs", {"kind": "animation_" + action, "status": "queued", "progress": 0,
        "message": "等待处理", "result": None, "error": None,
        "payload": {"animation_id": project["id"], "action": action, **copy.deepcopy(request)},
        "provider_state": copy.deepcopy(state or {})})


def _jobs(conn, animation_id):
    return [json.loads(row[0]) for row in conn.execute(
        "SELECT data FROM jobs WHERE json_extract(data,'$.payload.animation_id')=? ORDER BY created_at DESC", (animation_id,))]


def _revision(conn, animation_id, ident):
    row = _read(conn, "animation_revisions", ident)
    if row["animation_id"] != animation_id:
        raise ValueError("版本不属于当前动画")
    return row


def _new_revision(conn, project, scenes, *, title, params, parent=None, job_id=None):
    versions = conn.execute("SELECT MAX(CAST(json_extract(data,'$.version') AS INTEGER)) FROM animation_revisions WHERE json_extract(data,'$.animation_id')=?", (project["id"],)).fetchone()[0]
    old = {s["id"]: s for s in (parent or {}).get("scenes", [])}
    old_renders = {r["scene_id"]: r for r in (parent or {}).get("renders", [])}
    inherited, renders = [], []
    for scene in scenes:
        previous = old.get(scene["id"])
        if previous and all(previous.get(key) == scene.get(key) for key in SCENE_KEYS):
            inherited.append(copy.deepcopy(previous))
            if scene["id"] in old_renders:
                renders.append(copy.deepcopy(old_renders[scene["id"]]))
        else:
            changed = copy.deepcopy(scene)
            if previous and previous.get("source_path"):
                changes = {key: {"from": previous.get(key), "to": scene[key]}
                           for key in ("title", "description", "duration") if previous.get(key) != scene[key]}
                changed["prior_source_path"] = previous["source_path"]
                changed["revision_instruction"] = (
                    "用户已确认以下片段修改。沿用旧源码的视觉风格、构图和未涉及的动画，仅落实这些变化："
                    + _json(changes) + "。时长变化时同步调整时间轴和末帧停留。")
            inherited.append(changed)
    revision = _insert(conn, "animation_revisions", {"animation_id": project["id"], "version": int(versions or 0) + 1,
        "parent_revision_id": (parent or {}).get("id"), "title": title, **params,
        "duration": round(sum(s["duration"] for s in scenes), 6), "scenes": inherited,
        "renders": renders, "output": None, "job_id": job_id})
    # A late AI response creates a historical branch, never replacing a newer edit.
    if project.get("current_revision_id") == (parent or {}).get("id"):
        project.update(current_revision_id=revision["id"], title=title)
        _write(conn, "animation_projects", project)
    return revision


def _reply(job):
    return {"job_id": job["id"], "animation_id": job["payload"]["animation_id"],
            "revision_id": job.get("provider_state", {}).get("revision_id") or job["payload"].get("revision_id")}


def _dispatch(job):
    workflow._schedule_job(job, _run, executor=EXECUTOR)
    return _reply(job)


def create(payload):
    params = _params(payload)
    submission = _submission_id(payload.get("submission_id"))
    fingerprint = _fingerprint("plan", None, params)
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = _submitted(conn, submission, fingerprint)
        if existing:
            return _reply(existing)
        project = _insert(conn, "animation_projects", {"title": params["brief"][:40], "current_revision_id": None,
            "workspace_project_id": None, "submissions": {}})
        job = _job(conn, "plan", project, {"params": params})
        _remember(conn, project, submission, fingerprint, job)
    return _dispatch(job)


def edit(animation_id, revision_id, payload):
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = _read(conn, "animation_projects", animation_id)
        if payload.get("expected_revision_id") != project.get("current_revision_id") or revision_id != project.get("current_revision_id"):
            raise ValueError("方案版本已更新，请刷新后再修改")
        previous = _revision(conn, animation_id, revision_id)
        scenes = _scenes(payload.get("scenes"), previous["scenes"])
        revision = _new_revision(conn, project, scenes, title=previous["title"],
            params={k: previous[k] for k in ("brief", "style", "ratio")}, parent=previous)
    return get_animation(animation_id, revision["id"])


def _submit(animation_id, action, payload):
    submission = _submission_id(payload.get("submission_id"))
    if action == "retry":
        request = {"job_id": _text(payload.get("job_id"), "任务 ID", 80)}
    else:
        request = {"revision_id": _text(payload.get("revision_id"), "版本 ID", 80)}
        if action == "revise":
            request.update(scene_id=_text(payload.get("scene_id"), "片段 ID", 80),
                           instruction=_text(payload.get("instruction"), "修改要求", 16000))
    fingerprint = _fingerprint(action, animation_id, request)
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = _read(conn, "animation_projects", animation_id)
        existing = _submitted(conn, submission, fingerprint)
        if existing:
            return _reply(existing)
        jobs = _jobs(conn, animation_id)
        state = None
        if action == "retry":
            prior = _read(conn, "jobs", request["job_id"])
            if prior.get("payload", {}).get("animation_id") != animation_id or not prior.get("kind", "").startswith("animation_"):
                raise ValueError("任务不属于当前动画")
            root_id = prior["payload"].get("retry_root_job_id", prior["id"])
            family = [j for j in jobs if j["id"] == root_id or j["payload"].get("retry_root_job_id") == root_id]
            active = next((j for j in family if j["status"] in BUSY), None)
            completed = next((j for j in family if j["status"] == "succeeded"), None)
            if active or completed:
                existing = active or completed
            else:
                latest = family[0] if family else prior
                target = latest.get("provider_state", {}).get("revision_id") or latest["payload"].get("revision_id")
                if target and any(j["status"] in BUSY and
                    (j.get("provider_state", {}).get("revision_id") or j["payload"].get("revision_id")) == target
                    for j in jobs):
                    raise ValueError("此版本还有任务正在处理，请完成后再重试")
                request = {**latest["payload"], "retry_of": latest["id"], "retry_root_job_id": root_id}
                request.pop("animation_id", None)
                action = request.pop("action")
                state = latest.get("provider_state", {})
        else:
            revision = _revision(conn, animation_id, request["revision_id"])
            if action == "revise":
                if project.get("current_revision_id") != revision["id"]:
                    raise ValueError("方案版本已更新，请基于当前版本修改")
                if request["scene_id"] not in {s["id"] for s in revision["scenes"]}:
                    raise ValueError("片段不属于当前版本")
            # A render and its recovery may not concurrently write one revision.
            existing = next((j for j in jobs if j["status"] in BUSY and
                (j.get("provider_state", {}).get("revision_id") or j["payload"].get("revision_id")) == revision["id"]), None)
            if existing and (existing["payload"].get("action") != action or
                (action == "revise" and any(existing["payload"].get(k) != request.get(k) for k in ("scene_id", "instruction")))):
                raise ValueError("此版本还有任务正在处理，请完成后再提交")
            if not existing and action == "render" and revision.get("output"):
                existing = next((j for j in jobs if j["status"] == "succeeded" and
                    (j.get("result") or {}).get("revision_id") == revision["id"] and (j.get("result") or {}).get("output")), None)
        if existing:
            _remember(conn, project, submission, fingerprint, existing)
            return _reply(existing)
        job = _job(conn, action, project, request, state)
        _remember(conn, project, submission, fingerprint, job)
    return _dispatch(job)


def render(animation_id, payload):
    return _submit(animation_id, "render", payload)


def revise(animation_id, payload):
    return _submit(animation_id, "revise", payload)


def retry(animation_id, payload):
    return _submit(animation_id, "retry", payload)


def _checkpoint(job_id, **changes):
    db.update("jobs", job_id, transform=lambda job: {**job, "provider_state": {**job.get("provider_state", {}), **changes}})


def _context(revision):
    width, height = (800, 1000) if revision["ratio"] == "4:5" else workflow.RATIOS[revision["ratio"]]
    return {**{k: revision[k] for k in ("title", "brief", "style", "ratio")}, "width": width, "height": height,
            "animation_id": revision["animation_id"], "revision_id": revision["id"],
            "scenes": [{k: s[k] for k in SCENE_KEYS} for s in revision["scenes"]]}


def _progress_fraction(update):
    value = float(update.get("progress", 0))
    # Source generation reports percent; the isolated renderer reports 0..1.
    return max(0, min(1, value / 100 if update.get("stage") == "source" else value))


def _plan(job):
    params = job["payload"]["params"]
    state = job.get("provider_state", {})
    proposed = state.get("plan")
    if not proposed:
        workflow.job_update(job["id"], progress=8, message="生成动画方案")
        proposed = provider().plan(copy.deepcopy(params))
        _checkpoint(job["id"], plan=proposed)
    scenes = _scenes(proposed.get("scenes"))
    title = _text(proposed.get("title"), "动画标题", 160)
    if abs(sum(s["duration"] for s in scenes) - params["duration"]) > .1:
        raise ValueError("方案总时长与目标不符")
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _read(conn, "jobs", job["id"])
        if current.get("provider_state", {}).get("revision_id"):
            return _revision(conn, job["payload"]["animation_id"], current["provider_state"]["revision_id"])
        project = _read(conn, "animation_projects", job["payload"]["animation_id"])
        revision = _new_revision(conn, project, scenes, title=title, params=params, job_id=job["id"])
        current.setdefault("provider_state", {})["revision_id"] = revision["id"]
        _write(conn, "jobs", current)
    return revision


def _revise(job):
    payload, state = job["payload"], job.get("provider_state", {})
    if state.get("revision_id"):
        return workflow.get_required("animation_revisions", state["revision_id"])
    previous = workflow.get_required("animation_revisions", payload["revision_id"])
    original = next(s for s in previous["scenes"] if s["id"] == payload["scene_id"])
    proposed = state.get("revised_scene")
    if not proposed:
        workflow.job_update(job["id"], progress=5, message="修改片段方案")
        proposed = provider().revise_scene({k: original[k] for k in SCENE_KEYS}, _context(previous), payload["instruction"])
        _checkpoint(job["id"], revised_scene=proposed)
    if proposed.get("id") != original["id"]:
        raise ValueError("局部修改不能更换片段 ID")
    scenes = _scenes([proposed if s["id"] == original["id"] else s for s in previous["scenes"]], previous["scenes"])
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = _read(conn, "animation_projects", payload["animation_id"])
        revision = _new_revision(conn, project, scenes, title=previous["title"],
            params={k: previous[k] for k in ("brief", "style", "ratio")}, parent=previous, job_id=job["id"])
        # An instruction can change animation/code even if its prose stayed equal.
        target = next(s for s in revision["scenes"] if s["id"] == original["id"])
        for key in list(target):
            if key not in SCENE_KEYS:
                target.pop(key)
        target["revision_instruction"] = payload["instruction"]
        target["prior_source_path"] = original.get("source_path")
        revision["renders"] = [r for r in revision["renders"] if r["scene_id"] != original["id"]]
        _write(conn, "animation_revisions", revision)
        current = _read(conn, "jobs", job["id"])
        current.setdefault("provider_state", {})["revision_id"] = revision["id"]
        _write(conn, "jobs", current)
    return revision


def _source(path):
    if not path:
        return None
    path = Path(path).resolve()
    root = (db.DATA / "animation_sources").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("动画源文件不可用")
    return path.read_text(encoding="utf-8")


def _asset_output(asset, scene_id=None):
    result = {"asset_id": asset["id"], **{k: asset.get(k) for k in ("url", "thumbnail_url", "duration", "width", "height")}}
    if scene_id:
        result["scene_id"] = scene_id
    return result


def _render_scenes(revision, job_id):
    directory = db.DATA / "generations" / "animations" / revision["animation_id"] / revision["id"]
    directory.mkdir(parents=True, exist_ok=True)
    private = db.DATA / "animation_sources" / revision["animation_id"] / revision["id"]
    private.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(revision["scenes"]):
        current = workflow.get_required("animation_revisions", revision["id"])
        if any(r["scene_id"] == scene["id"] for r in current["renders"]):
            continue
        output = directory / "scenes" / f"{scene['id']}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        source_path = private / f"{scene['id']}.html"
        context = {**_context(revision), "scene_id": scene["id"], "source_path": str(source_path),
                   "cache_key": f"{revision['id']}/{scene['id']}"}
        def progress(update):
            workflow.job_update(job_id, progress=10 + 70 * (index + _progress_fraction(update)) / len(revision["scenes"]),
                                message=f"片段 {index + 1}/{len(revision['scenes'])} · {update.get('message', '生成动画')}")
        result = provider().generate_and_render({k: scene[k] for k in SCENE_KEYS}, context, output, progress=progress,
            prior_source=_source(scene.get("prior_source_path")), instruction=scene.get("revision_instruction"))
        html = result.get("source_html")
        if not isinstance(html, str) or not html.strip() or not output.is_file():
            raise ValueError("动画输出缺少源文件或视频")
        digest = hashlib.sha256(html.encode()).hexdigest()
        if result.get("source_hash") != digest:
            raise ValueError("动画源码校验失败")
        info = media.probe(output)
        if info["kind"] != "video" or abs(info["duration"] - scene["duration"]) > .12:
            raise ValueError("动画片段时长与方案不一致")
        source_path.write_text(html, encoding="utf-8")
        asset = workflow.register_asset(output, f"{scene['title']}.mp4", source="animation", generation_id=job_id, purpose="generate")
        db.update("assets", asset["id"], {"animation_project_id": revision["animation_id"], "animation_revision_id": revision["id"], "animation_scene_id": scene["id"]})
        rendered = _asset_output(asset, scene["id"])
        with LOCK, db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _revision(conn, revision["animation_id"], revision["id"])
            stored = next(s for s in current["scenes"] if s["id"] == scene["id"])
            stored.update(source_path=str(source_path), source_hash=digest, source_revision_id=revision["id"],
                          audit=result.get("audit", {}))
            current["renders"] = [r for r in current["renders"] if r["scene_id"] != scene["id"]] + [rendered]
            current["job_id"] = job_id
            _write(conn, "animation_revisions", current)
    return workflow.get_required("animation_revisions", revision["id"])


def _film_plan(revision):
    context = _context(revision)
    renders = {r["scene_id"]: r for r in revision["renders"]}
    return {"title": revision["title"], "brief": revision["brief"], "template_id": "animation",
        "ratio": revision["ratio"], "width": context["width"], "height": context["height"], "duration": revision["duration"],
        "summary": "\n".join(s["description"] for s in revision["scenes"]),
        "clips": [{"id": s["id"], "asset_id": renders[s["id"]]["asset_id"], "start": 0, "end": s["duration"],
                   "duration": s["duration"], "label": s["title"], "role": s["title"], "caption": "", "transition": "cut"}
                  for s in revision["scenes"]]}


def _assemble(revision, job_id):
    if revision.get("output"):
        return revision
    from renderer import render_plan
    plan = _film_plan(revision)
    assets = [workflow.get_required("assets", r["asset_id"]) for r in revision["renders"]]
    output = db.DATA / "generations" / "animations" / revision["animation_id"] / revision["id"] / "complete.mp4"
    if not output.is_file():
        render_plan(plan, assets, output, progress=lambda event: workflow.job_update(job_id,
            progress=82 + float(event.get("progress", 0)) * 15, message="拼接完整动画"))
    info = media.probe(output)
    if info["kind"] != "video" or abs(info["duration"] - revision["duration"]) > .15:
        raise ValueError("完整动画时长校验失败，成功片段已保留")
    asset = workflow.register_asset(output, f"{revision['title']}_v{revision['version']}.mp4", source="animation", generation_id=job_id, purpose="generate")
    db.update("assets", asset["id"], {"animation_project_id": revision["animation_id"], "animation_revision_id": revision["id"], "animation_output": True})
    # The film, conventional plan and workspace render are committed together.
    with LOCK, db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = _read(conn, "animation_projects", revision["animation_id"])
        current = _revision(conn, project["id"], revision["id"])
        if current.get("output"):
            return current
        if project.get("workspace_project_id"):
            workspace = _read(conn, "projects", project["workspace_project_id"])
        else:
            workspace = _insert(conn, "projects", {"title": revision["title"], "brief": revision["brief"],
                "asset_ids": [], "selection": [], "template_id": "animation", "ratio": revision["ratio"],
                "target_duration": revision["duration"], "reference_id": None, "plan_id": None, "latest_render": None,
                "messages": [], "creation_source": "animation", "animation_project_id": project["id"]})
            project["workspace_project_id"] = workspace["id"]
        stored_plan = _insert(conn, "plans", {**plan, "project_id": workspace["id"], "version": revision["version"],
            "base_version_id": None, "origin": {"source": "animation", "animation_id": project["id"], "revision_id": revision["id"]}})
        record = _insert(conn, "renders", {"project_id": workspace["id"], "plan_id": stored_plan["id"], "version": revision["version"],
            **_asset_output(asset), "sha256": asset["sha256"], "candidate_preview": False, "animation_revision_id": revision["id"]})
        # Historical completion remains viewable but must not change a newer workspace head.
        head = _read(conn, "plans", workspace["plan_id"]) if workspace.get("plan_id") else None
        if head is None or project.get("current_revision_id") == revision["id"]:
            workspace.update(title=revision["title"], brief=revision["brief"], plan_id=stored_plan["id"], latest_render=record,
                             asset_ids=[a["id"] for a in assets], ratio=revision["ratio"], target_duration=revision["duration"])
            _write(conn, "projects", workspace)
        current.update(output=_asset_output(asset), workspace_project_id=workspace["id"], workspace_plan_id=stored_plan["id"],
                       workspace_render_id=record["id"], job_id=job_id)
        _write(conn, "animation_revisions", current)
        _write(conn, "animation_projects", project)
    return current


def _run(job_id):
    job = workflow.get_required("jobs", job_id)
    action = job["payload"]["action"]
    if action == "plan":
        revision = _plan(job)
    else:
        revision = _revise(job) if action == "revise" else workflow.get_required("animation_revisions", job["payload"]["revision_id"])
        _checkpoint(job_id, revision_id=revision["id"])
        revision = _assemble(_render_scenes(revision, job_id), job_id)
    return {"animation_id": revision["animation_id"], "revision_id": revision["id"],
            "output": revision.get("output"), "workspace_project_id": revision.get("workspace_project_id")}


def public_revision(revision):
    if not revision:
        return None
    keys = ("id", "animation_id", "parent_revision_id", "version", "title", "brief", "style", "ratio", "duration",
            "renders", "output", "job_id", "created_at", "updated_at", "workspace_project_id")
    result = {k: copy.deepcopy(revision.get(k)) for k in keys}
    ordered = {r["scene_id"]: r for r in result["renders"]}
    result["renders"] = [ordered[s["id"]] for s in revision["scenes"] if s["id"] in ordered]
    result["scenes"] = [{k: s[k] for k in (*SCENE_KEYS, "source_revision_id") if k in s} for s in revision["scenes"]]
    return result


def _public_project(project, jobs):
    result = {k: project.get(k) for k in ("id", "title", "current_revision_id", "workspace_project_id", "active_job_id", "created_at", "updated_at")}
    active = next((j for j in jobs if j["id"] == project.get("active_job_id")), None)
    result["status"] = active["status"] if active else "ready"
    return result


def get_animation(animation_id, revision_id=None):
    with db.connection() as conn:
        project = _read(conn, "animation_projects", animation_id)
        jobs = _jobs(conn, animation_id)
        ident = revision_id or project.get("current_revision_id")
        revision = _revision(conn, animation_id, ident) if ident else None
        versions = [json.loads(row[0]) for row in conn.execute(
            "SELECT data FROM animation_revisions WHERE json_extract(data,'$.animation_id')=? ORDER BY CAST(json_extract(data,'$.version') AS INTEGER) DESC", (animation_id,))]
    return {**_public_project(project, jobs), "revision": public_revision(revision),
            "revisions": [{k: row.get(k) for k in ("id", "parent_revision_id", "version", "title", "duration", "job_id", "created_at")} for row in versions],
            "jobs": [db.public_job(j) for j in jobs]}


def list_animations():
    with db.connection() as conn:
        projects = [json.loads(row[0]) for row in conn.execute("SELECT data FROM animation_projects ORDER BY updated_at DESC")]
        return [_public_project(project, _jobs(conn, project["id"])) for project in projects]
