"""Save a human-arranged home branch and render it without a model request."""
from __future__ import annotations

import hashlib
import json
import re
import threading

from fastapi import APIRouter

import storage as db
import workflow

router = APIRouter()
_SUBMISSION_LOCK = threading.Lock()


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


@router.post("/api/home/previews")
def create_home_preview(payload: dict):
    submission_id = str(payload.get("submission_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", submission_id):
        raise ValueError("本次预览缺少有效提交编号")
    raw = payload.get("plan")
    if not isinstance(raw, dict):
        raise ValueError("请选择一个有效剪辑分支")
    branch_id = str(payload.get("branch_id") or "")[:100]
    branch_name = str(payload.get("branch_name") or "分支")[:80]
    title = str(raw.get("title") or "新的剪辑").strip()[:100]
    proposed = {
        "title": title or "新的剪辑", "brief": f"手动编排 · {branch_name}",
        "summary": str(raw.get("summary") or "")[:1000], "template_id": "manual",
        "ratio": raw.get("ratio", "9:16"), "clips": raw.get("clips"),
    }
    # Validate all references and ranges before creating any project or version.
    if not isinstance(proposed["clips"], list):
        raise ValueError("剪辑分支缺少镜头")
    ids = list(dict.fromkeys(clip.get("asset_id") for clip in proposed["clips"] if isinstance(clip, dict)))
    if any(not isinstance(asset_id, str) for asset_id in ids):
        raise ValueError("镜头缺少有效素材")
    assets = [workflow.get_required("assets", asset_id) for asset_id in ids]
    checked = workflow.validate_plan(proposed, assets)
    fingerprint = _fingerprint({"plan": proposed, "branch_id": branch_id, "branch_name": branch_name,
                                "project_id": payload.get("project_id"),
                                "expected_plan_id": payload.get("expected_plan_id")})
    with _SUBMISSION_LOCK:
        existing = next((project for project in db.records("projects", 5000)
                         if submission_id in project.get("home_submissions", {})), None)
        if existing:
            entry = existing["home_submissions"][submission_id]
            if entry["fingerprint"] != fingerprint:
                raise ValueError("相同提交编号不能用于不同剪辑，请重新确认")
            project = existing
        else:
            project_id = payload.get("project_id")
            if project_id:
                project = workflow.get_required("projects", project_id)
                if project.get("creation_source") != "home_canvas":
                    raise ValueError("请从这个作品的编辑页保存修改")
                if project.get("plan_id") != payload.get("expected_plan_id"):
                    raise ValueError("作品已有新版本，请刷新后再确认预览")
            else:
                if payload.get("expected_plan_id"):
                    raise ValueError("新作品不能引用已有方案版本")
                project = db.create("projects", {
                    "title": checked["title"], "brief": checked["brief"], "asset_ids": ids,
                    "selection": [], "template_id": "manual", "ratio": checked["ratio"],
                    "target_duration": checked["duration"], "reference_id": None,
                    "reference_case_ids": [], "reference_case_snapshot": [],
                    "plan_id": None, "latest_render": None, "messages": [],
                    "creation_source": "home_canvas", "home_submissions": {},
                })
            entry = {"fingerprint": fingerprint, "branch_id": branch_id, "branch_name": branch_name}
            project = db.update("projects", project["id"], transform=lambda current: {
                **current, "home_submissions": {**current.get("home_submissions", {}), submission_id: entry},
            })
        if entry.get("plan_id"):
            plan = workflow.get_required("plans", entry["plan_id"])
        else:
            plan = workflow.save_plan(
                project["id"], checked, asset_ids=ids, expected_base=project.get("plan_id"),
                origin={"source": "home_canvas", "branch_id": branch_id,
                        "branch_name": branch_name, "submission_id": submission_id},
            )
            entry = {**entry, "plan_id": plan["id"]}
            db.update("projects", project["id"], transform=lambda current: {
                **current, "home_submissions": {**current.get("home_submissions", {}), submission_id: entry},
            })
        job = db.get("jobs", entry["job_id"]) if entry.get("job_id") else next(
            (item for item in db.records("jobs", 5000)
             if item["kind"] == "render" and item.get("payload", {}).get("project_id") == project["id"]
             and item.get("payload", {}).get("plan_id") == plan["id"]
             and item.get("status") in {"queued", "running", "succeeded"}), None)
        if not job:
            job = workflow.render(project["id"], plan["id"])
        entry = {**entry, "job_id": job["id"]}
        db.update("projects", project["id"], transform=lambda current: {
            **current, "home_submissions": {**current.get("home_submissions", {}), submission_id: entry},
        })
        return {"project_id": project["id"], "plan_id": plan["id"], "job_id": job["id"],
                "branch_id": branch_id, "branch_name": branch_name}
