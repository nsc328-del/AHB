"""Stable request assembly and local budget metadata, not model memory or billing.

DeepSeek Responses is stateless. This layer neither invents confirmed task facts
nor summarizes history; callers choose the actual evidence supplied to a stage.
Official limits checked 2026-10-21: /guides/responses_api/, /guides/vision/ and
/quick_start/pricing/ at https://api-docs.deepseek.com.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

VERSION = "stable-request-v1"
CONTEXT_LIMIT = 1_000_000
# A deliberately smaller application budget, not the advertised model limit.
INPUT_BUDGET = 240_000
IMAGE_TOKEN_RESERVE = 1024
PROTOCOL_RESERVE = 4096
_SINK = ContextVar("task_context_request_sink", default=None)
_STABLE = ("creative_reference", "skill_context", "task_facts", "assets", "reference", "previous")
_VARIABLE = ("request", "brief", "current_instruction", "delta")


class InputBudgetExceeded(ValueError):
    pass


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def stable_json(value):
    """Canonical nested JSON with fixed references before variable user input.

    Array order is meaningful (shots, words, frames) and must never be sorted.
    No field is removed or turned from evidence into an instruction.
    """
    value = _canonical(value)
    if isinstance(value, dict):
        order = [key for key in _STABLE if key in value]
        order += [key for key in value if key not in _STABLE + _VARIABLE]
        order += [key for key in _VARIABLE if key in value]
        value = {key: value[key] for key in order}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def stable_payload(value):
    if not isinstance(value, str):
        return stable_json(value)
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    return stable_json(parsed)


def request_metadata(name, model, system, user, schema, images, *, reasoning_effort,
                     output_reserve, input_budget=INPUT_BUDGET, context_limit=CONTEXT_LIMIT):
    """Return hashes and conservative planning reserves; never actual token use.

    UTF-8 bytes are intentionally conservative for text; the image reserve follows
    the documented per-image upper bound. These are a local guard, not a tokenizer
    or a promise that the server will accept the request.
    """
    images = images or []
    text_bytes = len((system + user + stable_json(schema)).encode("utf-8"))
    text_bytes += sum(len(image["label"].encode("utf-8")) for image in images)
    image_reserve = len(images) * IMAGE_TOKEN_RESERVE
    estimated = text_bytes + image_reserve + PROTOCOL_RESERVE
    effective_budget = min(input_budget, context_limit - output_reserve)
    if effective_budget <= 0 or estimated > effective_budget:
        raise InputBudgetExceeded("本次分析内容超过单次上下文预算，请按内容段拆分后重试")
    fingerprint = {"version": VERSION, "stage": name, "model": model,
                   "system": system, "schema": schema, "user": user, "images": images,
                   "reasoning_effort": reasoning_effort, "output_reserve": output_reserve}
    try:
        payload = json.loads(user)
    except ValueError:
        payload = None
    stable_references = {key: payload[key] for key in ("creative_reference", "skill_context", "task_facts")
                         if key in payload} if isinstance(payload, dict) else {}
    prefix = system + stable_json(schema) + stable_json(stable_references)
    return {"version": VERSION, "stage": name,
            "fingerprint": hashlib.sha256(stable_json(fingerprint).encode()).hexdigest(),
            "prefix_fingerprint": hashlib.sha256(prefix.encode()).hexdigest(),
            "prefix_scope": "instructions_schema_and_stable_references",
            "budget_kind": "conservative_estimate_not_provider_usage",
            "text_utf8_bytes": text_bytes, "image_count": len(images),
            "image_token_reserve": image_reserve, "protocol_token_reserve": PROTOCOL_RESERVE,
            "estimated_input_tokens": estimated, "input_budget": effective_budget,
            "output_token_reserve": output_reserve, "context_limit": context_limit}


@contextmanager
def capture(on_request=None):
    """Capture metadata for one stage, including a request that later fails."""
    records = []
    def receive(record):
        records.append(record)
        if on_request:
            on_request(record)
    token = _SINK.set(receive)
    try:
        yield records
    finally:
        _SINK.reset(token)


def record_request(metadata):
    sink = _SINK.get()
    if sink:
        sink(metadata)
