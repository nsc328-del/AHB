"""A scoped, reviewable AI edit for one node of an unsaved home branch."""
from __future__ import annotations

import hashlib
import json
import re

from fastapi import APIRouter

import storage as db
import workflow

router = APIRouter()
JOB_KIND = "home_node_edit"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _visual_assets(ids):
    assets = [workflow.get_required("assets", asset_id) for asset_id in ids]
    if any(workflow.is_reference_asset(asset) for asset in assets):
        raise ValueError("样片不能作为成片素材，请改用素材库中的素材")
    if any(asset.get("kind") not in {"image", "video"} for asset in assets):
        raise ValueError("节点只能使用图片或视频素材")
    return assets


def _request(payload):
    submission_id = payload.get("submission_id")
    if not isinstance(submission_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", submission_id):
        raise ValueError("本次修改缺少有效提交编号")
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
        raise ValueError("请输入 1–4000 个字符的剪辑要求")
    raw = payload.get("plan")
    if not isinstance(raw, dict) or not isinstance(raw.get("clips"), list):
        raise ValueError("请选择一个有效剪辑分支")
    clips = raw["clips"]
    if any(not isinstance(clip, dict) for clip in clips):
        raise ValueError("镜头格式不正确")
    clip_ids = [clip.get("id") for clip in clips]
    if any(not isinstance(value, str) or not value or len(value) > 100 for value in clip_ids):
        raise ValueError("每个镜头都需要有效节点编号")
    if len(set(clip_ids)) != len(clip_ids):
        raise ValueError("节点编号不能重复")
    clip_id = payload.get("clip_id")
    if clip_id not in clip_ids:
        raise ValueError("选中的镜头已不存在，请重新选择")
    source_ids = [clip.get("asset_id") for clip in clips]
    if raw.get("music_asset_id"):
        source_ids.append(raw["music_asset_id"])
    if any(not isinstance(value, str) or not value for value in source_ids):
        raise ValueError("镜头缺少有效素材")
    source_assets = [workflow.get_required("assets", value) for value in dict.fromkeys(source_ids)]
    if any(workflow.is_reference_asset(asset) for asset in source_assets):
        raise ValueError("样片不能作为成片素材，请改用素材库中的素材")
    # Validate the whole incoming branch, not only the edited node.
    checked = workflow.validate_plan(raw, source_assets)
    selected = next(clip for clip in checked["clips"] if clip["id"] == clip_id)
    allowed = payload.get("asset_ids")
    if allowed is None or allowed == []:
        allowed = [selected["asset_id"]]
    if not isinstance(allowed, list) or not 1 <= len(allowed) <= 20 or any(
            not isinstance(value, str) or not value for value in allowed):
        raise ValueError("请选择 1–20 份可用于这个节点的素材")
    allowed = list(dict.fromkeys(allowed))
    _visual_assets(allowed)
    # Keep only edit data in the provider snapshot, excluding project lineage.
    plan = {key: checked[key] for key in ("title", "summary", "ratio", "clips", "music_asset_id")
            if key in checked}
    request = {"instruction": instruction.strip(), "plan": plan,
               "clip_id": clip_id, "asset_ids": allowed}
    signature = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
    return submission_id, request, signature


def _changes(before, after, assets):
    changes = []
    indexed = {asset["id"]: asset for asset in assets}
    if before["asset_id"] != after["asset_id"]:
        changes.append("素材改为「" + indexed[after["asset_id"]]["name"] + "」")
    if (before["start"], before["end"]) != (after["start"], after["end"]):
        if indexed[after["asset_id"]]["kind"] == "image":
            changes.append(f"展示 {after['duration']:g} 秒")
        else:
            changes.append(f"取用 {after['start']:g}–{after['end']:g} 秒，共 {after['duration']:g} 秒")
    if before["caption"] != after["caption"]:
        changes.append("字幕改为「" + after["caption"] + "」" if after["caption"] else "移除字幕")
    if before["transition"] != after["transition"]:
        changes.append("开头改为淡入" if after["transition"] == "fade" else "开头改为直接切入")
    if before["label"] != after["label"]:
        changes.append("镜头名称改为「" + after["label"] + "」")
    if before["role"] != after["role"]:
        changes.append("叙事作用改为「" + after["role"] + "」")
    return changes


def _operation(request, signature):
    def execute(job_id):
        assets = _visual_assets(request["asset_ids"])
        plan = request["plan"]
        index = next(i for i, clip in enumerate(plan["clips"]) if clip["id"] == request["clip_id"])
        current = plan["clips"][index]
        neighbors = {"before": plan["clips"][index - 1] if index else None,
                     "after": plan["clips"][index + 1] if index + 1 < len(plan["clips"]) else None}
        workflow.job_update(job_id, progress=15, message="根据要求调整这个镜头")
        brief = (
            "只修改当前选中的一个镜头。必须恰好返回 1 个 clip；其他镜头只是上下文，不能返回或修改。"
            "只可从本次提供的 assets 选择画面。可执行修改只有选素材、源区间或展示时长、字幕、"
            "镜头名称、叙事作用以及 cut/fade 切入。不能变速、生成新画面、改变画面内容、"
            "替换声音或添加模型不支持的特效；不要声称执行了这些操作。"
            "未要求的字段保持原样。music_asset_id 必须为空字符串。"
            "用户要求如下：\n" + request["instruction"]
        )
        proposed = workflow.providers().create_plan(
            brief, assets,
            {"ratio": plan["ratio"], "target_duration": current["duration"],
             "node_context": {"current": current, "neighbors": neighbors},
             "audio_incomplete": any(asset.get("has_audio") and not asset.get("transcript") for asset in assets)},
            previous={"title": plan.get("title", "节点修改"), "ratio": plan["ratio"],
                      "clips": [current], "music_asset_id": ""},
        )
        if not isinstance(proposed, dict) or not isinstance(proposed.get("clips"), list) or len(proposed["clips"]) != 1:
            raise ValueError("模型需要只返回当前的一个镜头；本次建议未应用")
        if proposed.get("music_asset_id"):
            raise ValueError("单节点修改不能更换整片配乐；本次建议未应用")
        raw_clip = proposed["clips"][0]
        if not isinstance(raw_clip, dict):
            raise ValueError("模型返回的镜头无效；本次建议未应用")
        checked = workflow.validate_plan(
            {"ratio": plan["ratio"], "clips": [{**raw_clip, "id": request["clip_id"]}]}, assets)
        result_clip = checked["clips"][0]
        if sum(clip["duration"] for clip in plan["clips"]) - current["duration"] + result_clip["duration"] > 3600:
            raise ValueError("修改后作品超过 60 分钟；本次建议未应用")
        # Keep the branch placement; the UI recalculates later nodes on application.
        result_clip["timeline_start"] = current["timeline_start"]
        result_clip["timeline_end"] = round(current["timeline_start"] + result_clip["duration"], 4)
        changes = _changes(current, result_clip, assets)
        return {"clip_id": request["clip_id"], "signature": signature, "clip": result_clip,
                "explanation": "；".join(changes) if changes else "这条要求没有产生可应用的镜头修改。",
                "changes": changes}
    return execute


@router.post("/api/home/node-edits")
def create_node_edit(payload: dict):
    submission_id, request, signature = _request(payload)
    stored_payload = {**request, "submission_id": submission_id, "signature": signature}
    # Persist the key before scheduling. This covers concurrent requests and a
    # service restart; failed/interrupted submissions never silently call AI again.
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM jobs WHERE json_extract(data,'$.kind')=? "
                           "AND json_extract(data,'$.payload.submission_id')=? LIMIT 1",
                           (JOB_KIND, submission_id)).fetchone()
        if row:
            job = json.loads(row[0])
            if job["payload"]["signature"] != signature:
                raise ValueError("相同提交编号不能用于不同修改，请重新确认")
            return {"job_id": job["id"], "clip_id": request["clip_id"], "signature": signature}
        stamp = db.now()
        job = {"id": db.identifier(), "kind": JOB_KIND, "status": "queued", "progress": 0,
               "message": "等待处理", "result": None, "error": None, "payload": stored_payload,
               "provider_state": {}, "created_at": stamp, "updated_at": stamp}
        conn.execute("INSERT INTO jobs(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                     (job["id"], _canonical(job), stamp, stamp))
    workflow.enqueue(JOB_KIND, stored_payload, _operation(request, signature), job_id=job["id"])
    return {"job_id": job["id"], "clip_id": request["clip_id"], "signature": signature}
