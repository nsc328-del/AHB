"""Local immutable media records and transactional job/project persistence."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VIDEO_STUDIO_DATA", str(ROOT / "data"))).expanduser().resolve()
TABLES = {
    "assets", "analyses", "projects", "plans", "jobs", "renders", "business_projects", "daily_summaries",
    "candidate_batches", "decision_events", "publication_cases",
    "publication_observations", "publication_bindings", "publication_events",
    "publication_syncs", "usage_records", "usage_tracking", "generation_batches", "animation_projects", "animation_revisions",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid.uuid4().hex


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "studio.sqlite", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    for name in ("assets", "previews", "frames", "renders", "plans", "tmp", "generations", "animation_sources"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        stamp = now()
        conn.execute("INSERT OR IGNORE INTO usage_tracking(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                     ("provider-usage-v1", '{"version": 1}', stamp, stamp))


def _table(table):
    if table not in TABLES:
        raise ValueError("Unknown record type")
    return table


def create(table, record):
    table = _table(table)
    item = dict(record)
    item.setdefault("id", identifier())
    item.setdefault("created_at", now())
    item["updated_at"] = now()
    with connection() as conn:
        conn.execute(f"INSERT INTO {table}(id,data,created_at,updated_at) VALUES(?,?,?,?)", (item["id"], json.dumps(item, ensure_ascii=False, allow_nan=False), item["created_at"], item["updated_at"]))
    return item


def get(table, record_id):
    table = _table(table)
    with connection() as conn:
        row = conn.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
    return json.loads(row[0]) if row else None


def records(table, limit=500):
    table = _table(table)
    with connection() as conn:
        rows = conn.execute(f"SELECT data FROM {table} ORDER BY created_at DESC LIMIT ?", (min(5000, max(1, int(limit))),)).fetchall()
    return [json.loads(row[0]) for row in rows]


def update(table, record_id, changes=None, transform=None):
    table = _table(table)
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        item = json.loads(row[0])
        if changes:
            item.update(changes)
        if transform:
            item = transform(item)
        item["updated_at"] = now()
        conn.execute(f"UPDATE {table} SET data=?,updated_at=? WHERE id=?", (json.dumps(item, ensure_ascii=False, allow_nan=False), item["updated_at"], record_id))
    return item


def media_url(path):
    from urllib.parse import quote
    relative = Path(path).resolve().relative_to(DATA.resolve()).as_posix()
    return "/api/media/" + quote(relative, safe="/")


def safe_media_path(relative):
    path = (DATA / relative).resolve()
    root = DATA.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError(relative)
    if path.parts[len(root.parts)] not in {"assets", "previews", "renders", "plans", "generations", "frames"}:
        raise FileNotFoundError(relative)
    return path


def public_asset(asset):
    if not asset:
        return None
    return {k: v for k, v in asset.items() if k not in {"path", "_shot_boundaries", "_frames", "provider_state"} and not k.startswith("_")}


def public_job(job):
    if not job:
        return None
    item = {k: v for k, v in job.items() if k not in {"payload", "provider_state", "traceback"} and not k.startswith("_")}
    if job.get("kind") in {"generation_plan", "generation_prompt"}:
        item["skill_id"] = job.get("payload", {}).get("skill_id") or "h3-prompt-writing"
    if job.get("kind") == "generation_prompt":
        item.update({key: job.get("payload", {}).get(key) for key in ("plan_job_id", "shot_id")})
    state = job.get("provider_state", {})
    item["resumable"] = job.get("kind") == "generation" and bool(state.get("task_id")) and not state.get("terminal_failure") and job.get("status") in {"failed", "unknown"}
    return item


def mark_interrupted():
    """A process restart is evidence of interruption, never evidence of success."""
    for job in records("jobs", 5000):
        if job["status"] in {"queued", "running"}:
            state = job.get("provider_state", {})
            status = "unknown" if job["kind"] == "generation" and (state.get("task_id") or state.get("submitting")) else "failed"
            message = "服务已重启；保留已提交的供应商任务，请查询继续。" if status == "unknown" else "服务重启导致任务中断，可重新发起此操作。"
            update("jobs", job["id"], {"status": status, "message": message, "error": message})
            asset_id = job.get("payload", {}).get("asset_id")
            if asset_id and job["kind"] in {"analysis", "reference"} and get("assets", asset_id):
                update("assets", asset_id, {"analysis_status": "failed", "analysis_error": message})
            if asset_id and job["kind"] == "transcription" and get("assets", asset_id):
                update("assets", asset_id, {"audio_status": "failed", "audio_error": "服务重启，语音转录中断；画面分析仍可使用，可重新发起本地转录。"})
