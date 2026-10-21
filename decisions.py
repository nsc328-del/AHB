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
import diversity

STRATEGY_VERSION = "three-to-five-previews-v2"
STRATEGIES = [
    {"key": "evidence", "label": "证据先行", "instruction": "先给素材中最有说服力的结果、事实或视觉证据，再补原因或上下文，最后收束。没有结果证据时，用最明确的主体展示开场，不虚构结论。"},
    {"key": "question", "label": "问题切入", "instruction": "从用户需求中真实存在的问题、对照或悬念切入，再用素材逐步回应。与证据先行方案改变开场取材、镜头顺序或时间分配；不能编造冲突或对白。"},
    {"key": "process", "label": "过程展开", "instruction": "顺着动作、时间、空间或观察过程展开，保留动作与信息的连续性，再落到结果。与前两案采用不同镜头组织或节奏；素材无真实过程时按细节到整体组织，不能虚构时间因果。"},
    {"key": "contrast", "label": "对照组织", "instruction": "以素材中确实可见的细节与整体、状态或观察角度形成对照，交替展开。改变主要镜头次序或时长分配，不编造前后因果，也不把同一片段拆分或微调几帧伪装成差异。"},
]
EXPLORATION = {"key": "exploration", "label": "探索剪法", "exploration": True,
               "instruction": "在事实与素材范围不变的前提下，探索与其他候选结构明显不同的剪法：更换开场素材、重组主要片段顺序，或大幅改变节奏分配。至少四分之一的素材组织或时间分配应不同；不能只换标题、角色名称、字幕或转场，不为差异牺牲可理解性。"}


def candidate_count(value):
    if type(value) is not int or not 3 <= value <= 5:
        raise ValueError("每批候选数量应为 3、4 或 5")
    return value


def _history_snapshot(project):
    projects = {item["id"]: item for item in db.records("projects", 5000)}
    def comparable(other):
        if other["id"] == project["id"]:
            return True
        # An unnamed account is not a comparable cohort. Never fall back to
        # global history or imported platform publications.
        return bool(project.get("platform") and project.get("account") and
                    other.get("platform") == project["platform"] and
                    other.get("account") == project["account"])
    scoped = {pid for pid, item in projects.items() if comparable(item)}
    adopted = {item.get("plan_id") for item in projects.values() if item["id"] in scoped}
    adopted.update(event.get("plan_id") for event in db.records("decision_events", 5000)
                   if event.get("project_id") in scoped and event.get("event_type") == "selected")
    renders = {}
    for item in db.records("renders", 5000):
        if item["project_id"] in scoped and item["plan_id"] in adopted:
            renders.setdefault(item["plan_id"], item)
    history = []
    for plan in db.records("plans", 5000):
        if plan["id"] not in renders:
            continue
        history.append({"plan_id": plan["id"], "project_id": plan["project_id"],
                        "render_id": renders[plan["id"]]["id"], "render_sha256": renders[plan["id"]].get("sha256"),
                        "fingerprint": diversity.fingerprint(plan)})
        if len(history) >= 100:
            break
    return history


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
    count = candidate_count(payload.get("count", 3))
    with work._project_lock(project_id):
        project = work.get_required("projects", project_id)
        if any(job["status"] in {"queued", "running"} and job.get("kind") in {"plan", "revision", "candidates", "candidate_previews"}
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
                        reference_case_snapshot=case_snapshot, comparable_history=_history_snapshot(project))
        strategies = [{**item, "exploration": False} for item in STRATEGIES[:count - 1]] + [copy.deepcopy(EXPLORATION)]
        batch = db.create("candidate_batches", {"project_id": project_id, "status": "queued", "job_id": None,
                          "plan_ids": [], "selected_plan_id": None, "decision_status": "pending",
                          "count": count, "phase": "planning", "previews": {}, "display_order": [],
                          "exploration_plan_id": None, "diversity_policy": copy.deepcopy(diversity.POLICY),
                          "input_snapshot": snapshot, "reference_case_ids": cases,
                          "strategy_version": STRATEGY_VERSION, "strategies": strategies, "error": None})
        job = work.enqueue("candidates", {"project_id": project_id, "batch_id": batch["id"]},
                           lambda jid: _candidate_job(batch["id"], jid))
        db.update("candidate_batches", batch["id"], {"job_id": job["id"]})
        db.update("projects", project_id, {"latest_candidate_batch_id": batch["id"]})
    return {"job_id": job["id"], "batch_id": batch["id"]}


def _structure(plan):
    return [(c["asset_id"], round(c["start"], 1), round(c["end"], 1)) for c in diversity.editorial_clips(plan)]


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
    before, after = diversity.editorial_clips(previous), diversity.editorial_clips(current)
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
        # Freeze one consistent analysis/ASR input before all candidate calls.
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
                   "audio_incomplete": any(a.get("has_audio") and not work._audio_input(a)[0] for a in assets + ([reference] if reference else []))}
        plans, attempts = [], []
        for index, strategy in enumerate(batch["strategies"]):
            checked = None
            for attempt in range(2):
                work.job_update(job_id, progress=20 + (index + attempt * .3) / len(batch["strategies"]) * 35,
                                message=f"生成候选 {index + 1}/{batch['count']}：{strategy['label']}")
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
                                audio_incomplete=options["audio_incomplete"],
                                source_kinds={asset["id"]: asset["kind"] for asset in assets})
                if snapshot.get("reference_id") in [c.get("asset_id") for c in proposed.get("clips", [])] and snapshot.get("reference_id"):
                    raise ValueError("参考样片不能作为候选的成片素材")
                checked = work.validate_plan(proposed, assets, snapshot.get("selection"))
                duplicate = any(not _distinct(plan, checked) for plan in plans)
                distances = [diversity.editorial_distance(plan, checked) for plan in plans]
                weak_exploration = bool(strategy.get("exploration") and
                                        any(distance < diversity.EXPLORATION_DISTANCE for distance in distances))
                attempts.append({"strategy": strategy["key"], "attempt": attempt + 1, "model": proposed.get("model"),
                                 "structure_hash": _hash(_structure(checked)), "accepted": not duplicate and not weak_exploration,
                                 "batch_distances": distances, "exploration_distance_passed": not weak_exploration,
                                 "brief": instruction, "options": candidate_options})
                db.update("candidate_batches", batch_id, {"attempts": attempts})
                if not duplicate and not weak_exploration:
                    break
            else:
                raise ValueError(f"“{strategy['label']}”未形成不同的镜头结构；已保留本批记录，请调整素材或要求后重试")
            checked["candidate_strategy"] = strategy
            checked["difference_summary"] = _difference(plans[0], checked) if plans else "本批结构对照"
            checked["diversity"] = diversity.evaluate(checked, snapshot["comparable_history"], plans)
            saved = work.save_plan(batch["project_id"], checked, asset_ids=asset_ids,
                                   base_plan_id=snapshot.get("base_plan_id"), activate=False,
                                   selection=snapshot.get("selection") or [],
                                   origin={"source": "candidate", "actor": "model", "job_id": job_id,
                                           "batch_id": batch_id, "candidate_key": strategy["key"]})
            plans.append(saved)
            update = {"plan_ids": [plan["id"] for plan in plans]}
            if strategy.get("exploration"):
                update["exploration_plan_id"] = saved["id"]
            db.update("candidate_batches", batch_id, update)
        # The local ranking is frozen for audit/replay. It never activates a plan.
        for plan in plans:
            plan["diversity"] = diversity.evaluate(plan, snapshot["comparable_history"],
                                                   [other for other in plans if other["id"] != plan["id"]])
            db.update("plans", plan["id"], {"diversity": plan["diversity"]})
        display_order = [plan["id"] for plan in sorted(plans, key=lambda plan: -plan["diversity"]["ranking_score"])]
        db.update("candidate_batches", batch_id, {"display_order": display_order, "phase": "rendering"})
        return _render_previews(batch_id, job_id, [plan["id"] for plan in plans])
    except Exception as error:
        status = "needs_configuration" if isinstance(error, getattr(work.providers(), "ConfigurationError", ())) else "failed"
        db.update("candidate_batches", batch_id, {"status": status, "error": work._error_text(error)})
        raise


def _preview_view(batch):
    previews = copy.deepcopy(batch.get("previews") or {})
    for plan_id in batch.get("plan_ids", []):
        preview = previews.setdefault(plan_id, {"plan_id": plan_id, "status": "pending", "job_id": None,
                                               "render": None, "error": None})
        job = db.get("jobs", preview.get("job_id")) if preview.get("job_id") else None
        if job:
            preview.update(status=job["status"], error=job.get("error"))
            if job["status"] == "succeeded":
                preview["render"] = job.get("result")
        preview["retryable"] = preview["status"] in {"failed", "unknown", "needs_configuration"}
        if preview["status"] == "not_rendered":
            preview["retryable"] = True
    return previews


def _prepare_legacy_batch(batch):
    """Old batches generated plans only; existing human choices are untouched."""
    if "count" in batch or not batch.get("plan_ids"):
        return batch
    renders = {}
    for render in db.records("renders", 5000):
        if render["project_id"] == batch["project_id"] and render["plan_id"] in batch["plan_ids"]:
            renders.setdefault(render["plan_id"], render)
    previews = {pid: {"plan_id": pid, "status": "succeeded" if pid in renders else "not_rendered",
                       "job_id": None, "render": renders.get(pid), "error": None}
                for pid in batch["plan_ids"]}
    ready = all(item["status"] == "succeeded" for item in previews.values())
    expected = len(batch.get("strategies") or batch["plan_ids"])
    complete_plans = len(batch["plan_ids"]) == expected
    fields = {"count": expected, "previews": previews, "display_order": list(batch["plan_ids"]),
              "phase": "complete" if ready and complete_plans else "rendering" if complete_plans else "planning",
              "legacy_plans_only": True}
    if complete_plans and batch["status"] not in {"queued", "running"}:
        fields.update(status="succeeded" if ready else "failed",
                      error=None if ready else "这批历史候选尚未全部生成预览，可补充渲染。")
    return db.update("candidate_batches", batch["id"], fields)


def _render_previews(batch_id, job_id, plan_ids):
    batch = work.get_required("candidate_batches", batch_id)
    db.update("candidate_batches", batch_id, {"status": "running", "phase": "rendering", "error": None})
    for plan_id in plan_ids:
        try:
            job = work.render(batch["project_id"], plan_id, preview_batch_id=batch_id)
            preview = {"plan_id": plan_id, "status": job["status"], "job_id": job["id"], "render": None, "error": None}
        except Exception as error:
            preview = {"plan_id": plan_id, "status": "failed", "job_id": None, "render": None,
                       "error": work._error_text(error)}
        db.update("candidate_batches", batch_id, transform=lambda item:
                  {**item, "previews": {**item.get("previews", {}), plan_id: preview}})
    last = None
    while True:
        batch = work.get_required("candidate_batches", batch_id)
        previews = _preview_view(batch)
        encoded = _hash(previews)
        if last != encoded:
            db.update("candidate_batches", batch_id, {"previews": previews})
            last = encoded
        completed = sum(item["status"] == "succeeded" for item in previews.values())
        work.job_update(job_id, progress=55 + 42 * completed / batch["count"],
                        message=f"渲染候选预览 {completed}/{batch['count']}")
        if not any(item["status"] in {"queued", "running"} for item in previews.values()):
            break
        time.sleep(.2)
    for plan_id, preview in previews.items():
        if preview["status"] == "succeeded":
            db.update("plans", plan_id, transform=lambda item: {**item, "diversity": {
                **item.get("diversity", {}),
                "quality": {"plan_validation": "passed", "preview_validation": "passed"}}})
    if len(previews) != batch["count"] or any(item["status"] != "succeeded" for item in previews.values()):
        message = "部分候选预览未完成；已保留成功结果，可重试失败预览。"
        db.update("candidate_batches", batch_id, {"status": "failed", "error": message})
        raise ValueError(message)
    db.update("candidate_batches", batch_id, {"status": "succeeded", "phase": "complete", "error": None,
                                             "completed_at": db.now()})
    return {"batch_id": batch_id, "project_id": batch["project_id"], "plan_ids": batch["plan_ids"]}


def retry_previews(batch_id, payload):
    batch = work.get_required("candidate_batches", batch_id)
    with work._project_lock(batch["project_id"]):
        batch = _prepare_legacy_batch(work.get_required("candidate_batches", batch_id))
        if batch["status"] in {"queued", "running"}:
            raise ValueError("此批候选仍在处理，请等待本轮完成")
        if batch.get("phase") not in {"rendering", "complete"} or len(batch["plan_ids"]) != batch.get("count"):
            raise ValueError("本批方案尚未全部生成，不能仅重试预览；请重新生成候选")
        previews = _preview_view(batch)
        failed = [pid for pid, preview in previews.items() if preview["status"] != "succeeded"]
        selected = payload.get("plan_ids", failed)
        if not isinstance(selected, list) or not selected or any(not isinstance(pid, str) or pid not in failed for pid in selected):
            raise ValueError("请选择本批未成功的预览重试")
        selected = list(dict.fromkeys(selected))
        db.update("candidate_batches", batch_id, {"status": "queued", "error": None})
        job = work.enqueue("candidates", {"project_id": batch["project_id"], "batch_id": batch_id,
                                         "operation": "retry_previews", "plan_ids": selected},
                           lambda jid: _render_previews(batch_id, jid, selected))
        db.update("candidate_batches", batch_id, {"job_id": job["id"]})
    return {"job_id": job["id"], "batch_id": batch_id, "project_id": batch["project_id"]}


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
    batch = _prepare_legacy_batch(work.get_required("candidate_batches", batch_id))
    plans = [work.get_required("plans", pid) for pid in batch["plan_ids"]]
    events = sorted([event for event in db.records("decision_events", 5000) if event["batch_id"] == batch_id], key=lambda event: event["created_at"])
    return {"batch": batch, "plans": plans, "previews": _preview_view(batch), "events": events}


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
        event_type = payload.get("event_type")
        if event_type not in {"viewed", "selected", "rejected_all"}:
            raise ValueError("不支持的选择事件")
        if event_type != "viewed" and batch["status"] != "succeeded":
            raise ValueError("请等待本批候选全部预览生成成功后再选择")
        valid = set(batch["plan_ids"])
        displayed = payload.get("displayed_plan_ids", batch.get("display_order") or batch["plan_ids"])
        declared_viewed = payload.get("viewed_plan_ids", [])
        for values in (displayed, declared_viewed):
            if not isinstance(values, list) or any(not isinstance(pid, str) or pid not in valid for pid in values) or len(values) != len(set(values)):
                raise ValueError("展示或查看记录包含不属于本批的候选")
        viewed = {event["plan_id"] for event in events if event["event_type"] == "viewed"}
        plan_id = payload.get("plan_id")
        if event_type in {"viewed", "selected"} and (plan_id not in valid or plan_id not in displayed):
            raise ValueError("请选择本批已展示的候选")
        if event_type in {"viewed", "selected"}:
            preview = view["previews"].get(plan_id, {})
            if preview.get("status") != "succeeded" or not preview.get("render"):
                raise ValueError("该候选还没有可播放的成功预览")
        if event_type == "viewed":
            viewed.add(plan_id)
        if any(pid not in viewed for pid in declared_viewed):
            raise ValueError("查看记录尚未保存，请先打开对应候选")
        if event_type == "selected" and plan_id not in viewed:
            raise ValueError("请先查看该候选的成片预览再采用")
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
            chosen_render = dict(view["previews"][plan_id]["render"], adopted=True, adopted_at=stamp, updated_at=stamp)
            project.update(plan_id=plan_id, title=plan["title"], selected_batch_id=batch_id,
                           latest_render=chosen_render, updated_at=stamp)
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
                _write_record(conn, "renders", chosen_render)
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
    return {"batches": [{**view["batch"], "plans": view["plans"], "previews": view["previews"],
                         "events": view["events"]} for view in batches],
            "events": events, "revisions": revisions}
