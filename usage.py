"""Provider-reported usage ledger; no prompt text, secrets, estimates, or historic backfill.

Wire contracts checked 2026-10-20:
https://api-docs.deepseek.com/api/create-response/
https://platform.minimax.io/docs/api-reference/video-generation-v2-query
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar

import storage as db

CONTEXT = ContextVar("provider_usage_context", default=None)
TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens")
SECONDS_FIELDS = ("video_seconds", "input_seconds", "output_seconds", "input_audio_seconds")
METRICS = TOKEN_FIELDS + SECONDS_FIELDS + ("input_image_count",)
AI_KINDS = {"analysis", "reference", "reference_repair", "reference_rhythm", "reference_presentation", "reference_semantics", "plan", "revision", "candidates", "generation_plan", "generation_prompt", "generation", "home_node_edit", "animation_plan", "animation_render", "animation_revise"}


@contextmanager
def context(**fields):
    value = {**(CONTEXT.get() or {}), **fields}
    token = CONTEXT.set(value)
    try:
        yield
    finally:
        CONTEXT.reset(token)


def job_context(job):
    payload = job.get("payload") or {}
    return context(job_id=job["id"], operation=job.get("kind"),
                   **{key: payload.get(key) for key in ("project_id", "asset_id", "batch_id")})


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    if integer:
        return int(value) if int(value) == value else None
    return value


def _first_number(*values, integer=False):
    return next((number for value in values if (number := _number(value, integer=integer)) is not None), None)


def normalize(value):
    """Normalize only documented numeric fields; missing values stay unknown."""
    data = value if isinstance(value, dict) else {}
    details = data.get("input_tokens_details") or data.get("prompt_tokens_details") or {}
    details = details if isinstance(details, dict) else {}
    result = {"input_tokens": _first_number(data.get("input_tokens"), data.get("prompt_tokens"), integer=True),
              "output_tokens": _first_number(data.get("output_tokens"), data.get("completion_tokens"), integer=True),
              "total_tokens": _number(data.get("total_tokens"), integer=True),
              "cache_hit_tokens": _first_number(details.get("cached_tokens"), data.get("prompt_cache_hit_tokens"), integer=True),
              "cache_miss_tokens": _number(data.get("prompt_cache_miss_tokens"), integer=True),
              "video_seconds": _number(data.get("total_seconds")),
              **{name: _number(data.get(name), integer=name == "input_image_count")
                 for name in ("input_seconds", "output_seconds", "input_audio_seconds", "input_image_count")}}
    result["derived_fields"] = []
    # This is an exact decomposition of two reported counters, never token estimation.
    incoming, cached = result["input_tokens"], result["cache_hit_tokens"]
    if incoming is not None and cached is not None:
        if cached > incoming:
            result["cache_hit_tokens"] = None
        elif result["cache_miss_tokens"] is None:
            result["cache_miss_tokens"] = incoming - cached
            result["derived_fields"].append("cache_miss_tokens")
    return result


def record_response(provider, endpoint, body=None, response=None, *, http_status=None, transport_error=False):
    """Called at the HTTP boundary, before response validation can raise.

    No active workflow context means no application call is being tracked (e.g.
    a pure adapter contract test). The request body and raw response are not saved.
    """
    ctx = CONTEXT.get()
    if ctx is None:
        return None
    response = response if isinstance(response, dict) else {}
    body = body if isinstance(body, dict) else {}
    task = response.get("task") if isinstance(response.get("task"), dict) else {}
    raw = task.get("usage") if provider == "minimax" and task else response.get("usage")
    parsed = normalize(raw)
    task_id = task.get("id") or response.get("task_id")
    if provider == "minimax" and endpoint.startswith("/v2/query/video_generation/"):
        task_id = endpoint.rsplit("/", 1)[-1]
    task_id = str(task_id)[:128] if task_id else None
    request_id = response.get("id") if provider == "deepseek" else None
    request_id = str(request_id)[:160] if request_id else None
    fmt = (body.get("text") or {}).get("format") or {}
    operation = fmt.get("name") or ctx.get("operation") or ("generation" if provider == "minimax" else "response")
    operation = str(operation)[:80]
    wire_status = task.get("status") or response.get("status")
    if not isinstance(wire_status, str):
        wire_status = None
    valid_statuses = {"queued", "running", "in_progress", "completed", "succeeded", "failed", "cancelled", "incomplete"}
    status = wire_status if wire_status in valid_statuses else ("unknown" if transport_error else "failed" if http_status and http_status >= 400 else "received")
    if http_status is not None and not 200 <= http_status < 300:
        status = "failed"
    key = f"{provider}:task:{task_id}" if task_id else f"{provider}:response:{request_id}" if request_id else f"{provider}:job:{ctx['job_id']}" if provider == "minimax" and ctx.get("job_id") else db.identifier()
    identifier = hashlib.sha256(key.encode()).hexdigest()[:32]
    stamp = db.now()
    record = {"id": identifier, "created_at": stamp, "updated_at": stamp,
              "provider": provider, "model": str(task.get("model") or response.get("model") or body.get("model") or ("MiniMax-H3" if provider == "minimax" else "deepseek-flash"))[:100],
              "operation": operation, "status": status, "http_status": http_status,
              "provider_task_id": task_id, "provider_response_id": request_id,
              **{key: ctx.get(key) for key in ("job_id", "project_id", "asset_id", "batch_id")}, **parsed}
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if provider == "minimax":
            # Submission, polling, reconnection and a repeated query are one task.
            old = conn.execute("SELECT data FROM usage_records WHERE id=? OR "
                "(json_extract(data,'$.provider')='minimax' AND "
                "((? IS NOT NULL AND json_extract(data,'$.provider_task_id')=?) OR "
                "(? IS NOT NULL AND json_extract(data,'$.job_id')=?))) LIMIT 1",
                (identifier, task_id, task_id, ctx.get("job_id"), ctx.get("job_id"))).fetchone()
        else:
            old = conn.execute("SELECT data FROM usage_records WHERE id=?", (identifier,)).fetchone()
        if old:
            previous = json.loads(old[0])
            record["id"] = previous["id"]
            record["created_at"] = previous["created_at"]
            # Missing counters on a later poll never erase an earlier receipt.
            for field in METRICS + ("job_id", "project_id", "asset_id", "batch_id", "provider_task_id", "provider_response_id"):
                if record.get(field) is None:
                    record[field] = previous.get(field)
            for field in ("job_id", "project_id", "asset_id", "batch_id"):
                if previous.get(field) is not None:
                    record[field] = previous[field]
            if previous.get("status") in {"succeeded", "completed"} and status not in {"succeeded", "completed"}:
                record["status"] = previous["status"]
            record["derived_fields"] = sorted(set(previous.get("derived_fields", [])) | set(record["derived_fields"]))
        known = any(record.get(field) is not None for field in METRICS)
        record["usage_status"] = "reported" if known else "pending" if record["status"] in {"queued", "running", "in_progress"} else "unreported"
        conn.execute("INSERT INTO usage_records(id,data,created_at,updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                     (record["id"], json.dumps(record, ensure_ascii=False, allow_nan=False), record["created_at"], stamp))
    return record


def _scope(scope, ids):
    if scope not in {"global", "project", "asset", "job", "batch", "animation"}:
        raise ValueError("用量范围无效")
    selected = {key: str(value) for key, value in ids.items() if value}
    if scope != "global" and not selected.get(scope + "_id"):
        raise ValueError("请指定用量记录范围")
    if any(len(value) > 128 for value in selected.values()):
        raise ValueError("用量记录编号无效")
    return selected


def summary(scope="global", *, project_id=None, asset_id=None, job_id=None, batch_id=None, animation_id=None, limit=50):
    filters = _scope(scope, {"project_id": project_id, "asset_id": asset_id, "job_id": job_id,
                             "batch_id": batch_id, "animation_id": animation_id})
    limit = min(100, max(1, int(limit)))
    with db.connection() as conn:
        tracking = conn.execute("SELECT created_at FROM usage_tracking WHERE id='provider-usage-v1'").fetchone()
        started = tracking[0] if tracking else None
        clauses, parameters = [], []
        for key, value in filters.items():
            if key == "animation_id":
                # The job relationship includes every revision, failed attempt
                # and retry without copying or inventing provider receipts.
                clauses.append("json_extract(data,'$.job_id') IN "
                               "(SELECT id FROM jobs WHERE json_extract(data,'$.payload.animation_id')=?)")
                parameters.append(value)
            elif key == "asset_id":
                # Generated media inherit the generation job's actual receipt.
                clauses.append("(json_extract(data,'$.asset_id')=? OR json_extract(data,'$.job_id') IN "
                               "(SELECT json_extract(data,'$.generation_id') FROM assets WHERE id=?))")
                parameters.extend([value, value])
            else:
                clauses.append(f"json_extract(data,'$.{key}')=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute("SELECT data FROM usage_records" + where + " ORDER BY created_at DESC", parameters).fetchall()
        items = [json.loads(row[0]) for row in rows]
        jobs = [json.loads(row[0]) for row in conn.execute("SELECT data FROM jobs WHERE created_at<?", (started or "",)).fetchall()]
    def matches_job(job):
        payload = job.get("payload") or {}
        result = job.get("result") or {}
        if job.get("kind") not in AI_KINDS:
            return False
        return all((job["id"] == value if key == "job_id" else
                    (payload.get(key) == value or result.get(key) == value) if key == "asset_id" else
                    payload.get(key) == value) for key, value in filters.items())
    old_jobs = [job for job in jobs if matches_job(job)]
    aggregates = {key: (sum(values) if (values := [item[key] for item in items if item.get(key) is not None]) else None)
                  for key in METRICS}
    comparable_cache = [item for item in items if item.get("input_tokens") is not None and item.get("cache_hit_tokens") is not None]
    cache_input = sum(item["input_tokens"] for item in comparable_cache)
    aggregates["cache_hit_rate"] = sum(item["cache_hit_tokens"] for item in comparable_cache) / cache_input if cache_input else None
    token_known = sum(any(item.get(key) is not None for key in TOKEN_FIELDS) for item in items)
    seconds_known = sum(any(item.get(key) is not None for key in SECONDS_FIELDS) for item in items)
    unknown = sum(not any(item.get(key) is not None for key in TOKEN_FIELDS + SECONDS_FIELDS) for item in items)
    total_unknown = sum(item.get("total_tokens") is None for item in items)
    partial_tokens = sum(any(item.get(key) is not None for key in TOKEN_FIELDS) and
                         any(item.get(key) is None for key in ("input_tokens", "output_tokens", "total_tokens")) for item in items)
    seconds_only = sum(not any(item.get(key) is not None for key in TOKEN_FIELDS) and
                       any(item.get(key) is not None for key in SECONDS_FIELDS) for item in items)
    coverage = {"recorded_calls": len(items), "token_reported_calls": token_known,
                "token_total_unknown_calls": total_unknown, "token_partial_calls": partial_tokens,
                "seconds_only_calls": seconds_only,
                "token_unknown_calls": unknown, "seconds_reported_calls": seconds_known,
                "cache_reported_calls": len(comparable_cache),
                "metric_reported_calls": {key: sum(item.get(key) is not None for item in items) for key in METRICS},
                "legacy_jobs_count": len(old_jobs),
                "scope_has_prior_activity": bool(old_jobs),
                "scope_status": "recorded" if items else "unrecorded" if old_jobs else "empty",
                "first_recorded_at": items[-1]["created_at"] if items else None,
                "tracking_started_at": started, "history_available": not bool(old_jobs)}
    return {"scope": scope, "filters": filters, "aggregates": aggregates, "coverage": coverage,
            "records": items[:limit], "records_total": len(items), "has_more": len(items) > limit}
