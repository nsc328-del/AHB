"""Local candidate branches and append-only editorial decisions.

Case records stay local. Model inputs contain only the project's selected media,
its analysis and the user's brief, never imported publication text or metrics.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time

import storage as db
import workflow as work

STRATEGY_VERSION = "three-structures-v1"
STRATEGIES = [
    {"key": "evidence", "label": "证据先行", "instruction": "先给素材中最有说服力的结果、事实或视觉证据，再补原因或上下文，最后收束。没有结果证据时，用最明确的主体展示开场，不虚构结论。"},
    {"key": "question", "label": "问题切入", "instruction": "从用户需求中真实存在的问题、对照或悬念切入，再用素材逐步回应。与证据先行方案改变开场取材、镜头顺序或时间分配；不能编造冲突或对白。"},
    {"key": "process", "label": "过程展开", "instruction": "顺着动作、时间、空间或观察过程展开，保留动作与信息的连续性，再落到结果。与前两案采用不同镜头组织或节奏；素材无真实过程时按细节到整体组织，不能虚构时间因果。"},
]


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def case_references(ids):
    if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(item, str) or not item or len(item) > 150 for item in ids):
        raise ValueError("参考案例需要有效的案例 ID 列表，最多 20 个")
    ids = list(dict.fromkeys(ids))
    snapshots = []
    for case_id in ids:
        case = work.get_required("publication_cases", case_id)
        # Only identifiers, dates and metric values are retained, not imported copy.
        fields = ("id", "platform", "account", "publication_id", "work_id", "source_id",
                  "snapshot_id", "observation_id", "observation_ids", "observed_at",
                  "collected_at", "published_at", "metrics", "metric_status", "updated_at",
                  "source_snapshot", "source_snapshot_id", "source_observation_id",
                  "captured_at", "latest_observation_id", "source")
        snapshots.append({**{key: copy.deepcopy(case[key]) for key in fields if key in case},
                          "case_hash": _hash(case)})
    return ids, snapshots


def _asset_snapshot(asset):
    fields = ("id", "name", "kind", "duration", "width", "height", "has_audio", "sha256",
              "analysis", "audio_status", "transcript")
    result = {key: copy.deepcopy(asset[key]) for key in fields if key in asset}
    result["analysis_id"] = (asset.get("analysis") or {}).get("analysis_id")
    result["analysis_hash"] = _hash(asset.get("analysis"))
    result["transcript_hash"] = _hash(asset.get("transcript"))
    return result


def create_candidates(project_id, payload):
    if payload.get("count", 3) != 3:
        raise ValueError("每批生成 3 个候选方案")
    with work._project_lock(project_id):
        project = work.get_required("projects", project_id)
        if any(job["status"] in {"queued", "running"} and job.get("kind") in {"plan", "revision", "candidates"}
               and job.get("payload", {}).get("project_id") == project_id for job in db.records("jobs", 5000)):
            raise ValueError("此作品还有方案生成中，请等待本轮完成")
        brief = str(payload.get("brief") or project.get("brief") or "").strip()
        if not brief or len(brief) > 12000:
            raise ValueError("制作要求需要 1–12000 个字符")
        assets = [work.get_required("assets", asset_id) for asset_id in project.get("asset_ids", [])]
        if not any(asset["kind"] in {"video", "image"} for asset in assets):
            raise ValueError("请选择至少一份图片或视频素材")
        cases, case_snapshot = case_references(payload.get("reference_case_ids", project.get("reference_case_ids", [])))
        snapshot = {key: copy.deepcopy(project.get(key)) for key in
                    ("asset_ids", "selection", "template_id", "ratio", "target_duration", "reference_id", "platform", "account")}
        snapshot.update(brief=brief, base_plan_id=project.get("plan_id"), assets=[_asset_snapshot(a) for a in assets],
                        reference_case_snapshot=case_snapshot)
        batch = db.create("candidate_batches", {"project_id": project_id, "status": "queued", "job_id": None,
                          "plan_ids": [], "selected_plan_id": None, "decision_status": "pending",
                          "input_snapshot": snapshot, "reference_case_ids": cases,
                          "strategy_version": STRATEGY_VERSION, "strategies": copy.deepcopy(STRATEGIES), "error": None})
        job = work.enqueue("candidates", {"project_id": project_id, "batch_id": batch["id"]},
                           lambda jid: _candidate_job(batch["id"], jid))
        db.update("candidate_batches", batch["id"], {"job_id": job["id"]})
    return {"job_id": job["id"], "batch_id": batch["id"]}


def _structure(plan):
    return [(c["asset_id"], round(c["start"], 1), round(c["end"], 1)) for c in plan["clips"]]


def _difference(previous, current):
    before, after = previous["clips"], current["clips"]
    details = []
    if len(before) != len(after):
        details.append(f"镜头数 {len(before)} → {len(after)}")
    if [c["asset_id"] for c in before] != [c["asset_id"] for c in after]:
        details.append("素材次序不同")
    if [(round(c["start"], 1), round(c["end"], 1)) for c in before] != [(round(c["start"], 1), round(c["end"], 1)) for c in after]:
        details.append("取材区间或节奏不同")
    return "；".join(details)


def _distinct(previous, current):
    before, after = previous["clips"], current["clips"]
    if [c["asset_id"] for c in before] != [c["asset_id"] for c in after]:
        return True
    # Tiny time jitter is not another editorial approach. Require a material
    # source/time allocation difference when the material sequence is identical.
    threshold = max(.25, min(previous["duration"], current["duration"]) * .05)
    return any(abs(a["start"] - b["start"]) >= threshold or abs(a["end"] - b["end"]) >= threshold
               for a, b in zip(before, after))


def _candidate_job(batch_id, job_id):
    batch = work.get_required("candidate_batches", batch_id)
    snapshot = copy.deepcopy(batch["input_snapshot"])
    db.update("candidate_batches", batch_id, {"status": "running", "job_id": job_id})
    try:
        asset_ids = snapshot["asset_ids"]
        for asset_id in asset_ids:
            if not work.get_required("assets", asset_id).get("analysis"):
                work._analysis(asset_id, False, job_id)
        reference = work.get_required("assets", snapshot["reference_id"]) if snapshot.get("reference_id") else None
        if reference and not (reference.get("analysis") or {}).get("reference_analysis"):
            work._analysis(reference["id"], True, job_id)
        # Freeze one consistent analysis/ASR input before the three calls.
        targets = asset_ids + ([reference["id"]] if reference else [])
        deadline = time.monotonic() + 90
        while any(work.get_required("assets", aid).get("audio_status") in {"queued", "running", "downloading_model"} for aid in targets):
            if time.monotonic() >= deadline:
                break
            work.job_update(job_id, progress=15, message="等待本地语音转录")
            time.sleep(1)
        assets = [copy.deepcopy(work.get_required("assets", aid)) for aid in asset_ids]
        reference = copy.deepcopy(work.get_required("assets", reference["id"])) if reference else None
        snapshot["assets"] = [_asset_snapshot(asset) for asset in assets]
        snapshot["reference"] = _asset_snapshot(reference) if reference else None
        snapshot["frozen_at"] = db.now()
        db.update("candidate_batches", batch_id, {"input_snapshot": snapshot})
        options = {"template_id": snapshot.get("template_id") or "free", "ratio": snapshot.get("ratio") or "9:16",
                   "duration": snapshot.get("target_duration") or 30, "selection": snapshot.get("selection") or [],
                   "audio_incomplete": any(a.get("has_audio") and not a.get("transcript") for a in assets + ([reference] if reference else []))}
        plans, attempts = [], []
        for index, strategy in enumerate(batch["strategies"]):
            checked = None
            for attempt in range(2):
                work.job_update(job_id, progress=20 + index * 24 + attempt * 8, message=f"生成候选 {index + 1}/3：{strategy['label']}")
                candidate_options = {**options, "candidate_strategy": strategy,
                                     "other_candidate_structures": [_structure(plan) for plan in plans]}
                instruction = (f"{snapshot['brief']}\n\n本候选的叙事要求：{strategy['instruction']}\n"
                               "保持同一制作目标与素材范围。实际镜头数量、素材顺序、取材区间或时间分配必须与已有候选不同，"
                               "只改标题、镜头名称、字幕或转场不算不同方案。不要用微小时间抖动伪造差异。")
                if attempt:
                    instruction += "\n上一版结构与已有候选重复；请重新组织镜头，而不是改名。"
                proposed = work.providers().create_plan(instruction, copy.deepcopy(assets), candidate_options,
                                                        reference=copy.deepcopy(reference), previous=None)
                if not isinstance(proposed, dict):
                    raise ValueError("模型没有返回有效的候选方案")
                proposed.update(brief=snapshot["brief"], ratio=options["ratio"], template_id=options["template_id"],
                                audio_incomplete=options["audio_incomplete"])
                if snapshot.get("reference_id") in [c.get("asset_id") for c in proposed.get("clips", [])] and snapshot.get("reference_id"):
                    raise ValueError("参考样片不能作为候选的成片素材")
                checked = work.validate_plan(proposed, assets, snapshot.get("selection"))
                duplicate = any(not _distinct(plan, checked) for plan in plans)
                attempts.append({"strategy": strategy["key"], "attempt": attempt + 1, "model": proposed.get("model"),
                                 "structure_hash": _hash(_structure(checked)), "accepted": not duplicate,
                                 "brief": instruction, "options": candidate_options})
                db.update("candidate_batches", batch_id, {"attempts": attempts})
                if not duplicate:
                    break
            else:
                raise ValueError(f"“{strategy['label']}”未形成不同的镜头结构；已保留本批记录，请调整素材或要求后重试")
            checked["candidate_strategy"] = strategy
            checked["difference_summary"] = _difference(plans[0], checked) if plans else "本批结构对照"
            saved = work.save_plan(batch["project_id"], checked, asset_ids=asset_ids,
                                   base_plan_id=snapshot.get("base_plan_id"), activate=False,
                                   selection=snapshot.get("selection") or [],
                                   origin={"source": "candidate", "actor": "model", "job_id": job_id,
                                           "batch_id": batch_id, "candidate_key": strategy["key"]})
            plans.append(saved)
            db.update("candidate_batches", batch_id, {"plan_ids": [plan["id"] for plan in plans]})
        db.update("candidate_batches", batch_id, {"status": "succeeded", "completed_at": db.now()})
        return {"batch_id": batch_id, "project_id": batch["project_id"], "plan_ids": [plan["id"] for plan in plans]}
    except Exception as error:
        status = "needs_configuration" if isinstance(error, getattr(work.providers(), "ConfigurationError", ())) else "failed"
        db.update("candidate_batches", batch_id, {"status": status, "error": work._error_text(error)})
        raise


def reconcile_batches(startup=False):
    for batch in db.records("candidate_batches", 5000):
        if batch["status"] not in {"queued", "running"}:
            continue
        job = db.get("jobs", batch.get("job_id")) if batch.get("job_id") else None
        if job and job["status"] not in {"queued", "running"}:
            db.update("candidate_batches", batch["id"], {"status": job["status"], "error": job.get("error")})
        elif startup and not job:
            db.update("candidate_batches", batch["id"], {"status": "failed", "error": "服务在候选任务入队前中断，请重新发起此批候选。"})


def batch_view(batch_id):
    reconcile_batches()
    batch = work.get_required("candidate_batches", batch_id)
    plans = [work.get_required("plans", pid) for pid in batch["plan_ids"]]
    events = sorted([event for event in db.records("decision_events", 5000) if event["batch_id"] == batch_id], key=lambda event: event["created_at"])
    return {"batch": batch, "plans": plans, "events": events}


def _write_record(conn, table, item, insert=False):
    encoded = json.dumps(item, ensure_ascii=False, allow_nan=False)
    if insert:
        conn.execute(f"INSERT INTO {db._table(table)}(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                     (item["id"], encoded, item["created_at"], item["updated_at"]))
    else:
        conn.execute(f"UPDATE {db._table(table)} SET data=?,updated_at=? WHERE id=?", (encoded, item["updated_at"], item["id"]))


def record_event(batch_id, payload):
    batch = work.get_required("candidate_batches", batch_id)
    with work._project_lock(batch["project_id"]):
        view = batch_view(batch_id)
        batch, events = view["batch"], view["events"]
        if batch["status"] != "succeeded":
            raise ValueError("请等待本批候选全部生成成功后再选择")
        event_type = payload.get("event_type")
        if event_type not in {"viewed", "selected", "rejected_all"}:
            raise ValueError("不支持的选择事件")
        valid = set(batch["plan_ids"])
        displayed = payload.get("displayed_plan_ids", batch["plan_ids"])
        declared_viewed = payload.get("viewed_plan_ids", [])
        for values in (displayed, declared_viewed):
            if not isinstance(values, list) or any(not isinstance(pid, str) or pid not in valid for pid in values) or len(values) != len(set(values)):
                raise ValueError("展示或查看记录包含不属于本批的候选")
        viewed = {event["plan_id"] for event in events if event["event_type"] == "viewed"}
        plan_id = payload.get("plan_id")
        if event_type in {"viewed", "selected"} and (plan_id not in valid or plan_id not in displayed):
            raise ValueError("请选择本批已展示的候选")
        if event_type == "viewed":
            viewed.add(plan_id)
        if any(pid not in viewed for pid in declared_viewed):
            raise ValueError("查看记录尚未保存，请先打开对应候选")
        if event_type == "selected" and plan_id not in viewed:
            raise ValueError("请先查看该候选的镜头结构再采用")
        if event_type == "rejected_all" and plan_id:
            raise ValueError("全部不选时不能指定某个候选")
        reason = str(payload.get("reason") or "").strip()
        if len(reason) > 2000:
            raise ValueError("选择说明不能超过 2000 字")
        project = work.get_required("projects", batch["project_id"])
        decisions = [event for event in events if event["event_type"] in {"selected", "rejected_all"}]
        if event_type == "rejected_all" and any(event["event_type"] == "selected" for event in decisions):
            raise ValueError("本批已有采用记录；可以换选其他候选，不能将已采用的批次记为全部未选")
        if (event_type == "selected" and project.get("plan_id") == plan_id and decisions
                and decisions[-1]["event_type"] == "selected" and decisions[-1]["plan_id"] == plan_id):
            return decisions[-1]
        stamp = db.now()
        event = {"id": db.identifier(), "project_id": project["id"], "batch_id": batch_id, "event_type": event_type,
                 "plan_id": plan_id if event_type != "rejected_all" else None,
                 "displayed_plan_ids": displayed, "viewed_plan_ids": [pid for pid in batch["plan_ids"] if pid in viewed],
                 "reason": reason, "actor": "local_user", "current_plan_id": plan_id if event_type == "selected" else project.get("plan_id"),
                 "previous_plan_id": project.get("plan_id"), "strategy_version": batch["strategy_version"],
                 "created_at": stamp, "updated_at": stamp}
        if event_type == "selected":
            plan = work.get_required("plans", plan_id)
            if plan["project_id"] != project["id"] or (plan.get("origin") or {}).get("batch_id") != batch_id:
                raise ValueError("候选分支与本作品不匹配")
            project.update(plan_id=plan_id, title=plan["title"], selected_batch_id=batch_id,
                           latest_render=next((r for r in db.records("renders", 5000) if r["plan_id"] == plan_id), None), updated_at=stamp)
            batch.update(selected_plan_id=plan_id, decision_status="selected", updated_at=stamp)
        elif event_type == "rejected_all":
            batch.update(selected_plan_id=None, decision_status="rejected_all", updated_at=stamp)
        # Current pointer and its decision are committed together, never inferred later.
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _write_record(conn, "decision_events", event, insert=True)
            if event_type in {"selected", "rejected_all"}:
                _write_record(conn, "candidate_batches", batch)
            if event_type == "selected":
                # Preserve concurrent progress/message writes outside the project lock.
                row = conn.execute("SELECT data FROM projects WHERE id=?", (project["id"],)).fetchone()
                current = json.loads(row[0])
                current.update({key: project[key] for key in ("plan_id", "title", "selected_batch_id", "latest_render", "updated_at")})
                _write_record(conn, "projects", current)
        return event


def project_decisions(project_id):
    work.get_required("projects", project_id)
    batches = [batch_view(batch["id"]) for batch in db.records("candidate_batches", 5000) if batch["project_id"] == project_id]
    events = [event for event in db.records("decision_events", 5000) if event["project_id"] == project_id]
    revisions = []
    for plan in db.records("plans", 5000):
        if plan["project_id"] != project_id or (plan.get("origin") or {}).get("source") == "candidate":
            continue
        base = db.get("plans", plan.get("base_version_id")) if plan.get("base_version_id") else None
        changes = plan.get("changes") or work.plan_changes(base, plan)
        revisions.append({"plan_id": plan["id"], "base_plan_id": plan.get("base_version_id"), "version": plan["version"],
                          "origin": plan.get("origin") or {"source": "legacy"}, "summary": "；".join(changes),
                          "changes": changes, "created_at": plan.get("created_at")})
    return {"batches": [{**view["batch"], "plans": view["plans"], "events": view["events"]} for view in batches],
            "events": events, "revisions": revisions}
