"""Read-only publication imports and explicit, auditable render bindings."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import zlib
from datetime import date, datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

import storage as db

TABLES = {"business_projects", "publication_cases", "publication_observations", "publication_bindings",
          "publication_events", "publication_syncs"}
SOURCE_LOCATION = "mini-alpha:publication-inventory/publications.sqlite"
SYNC_LOCK = threading.Lock()
METRICS = {
    "views": ("播放", "观看量"), "likes": ("点赞",), "comments": ("评论",),
    "saves": ("收藏",), "shares": ("分享", "转发"), "recommends": ("推荐",),
    "completion_rate": ("完播率",), "bounce_2s": ("2秒跳出率",),
    "avg_watch_seconds": ("人均观看时长", "平均观看时长"),
}

# This fixed program only reads the known source. No credentials, collector calls,
# source schema changes, checkpoints, or remote temporary files are involved.
REMOTE_EXPORT = r'''
import base64, json, sqlite3, zlib
from pathlib import Path
p = Path.home() / "Library/Application Support/星尚局域网/publication-inventory/publications.sqlite"
def signature():
    s = p.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns]
def has_wal():
    w = Path(str(p) + "-wal")
    return w.exists() and w.stat().st_size > 0
try:
    before = signature()
    mode = "read_only"
    try:
        c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
        c.execute("PRAGMA query_only=ON")
        c.execute("SELECT count(*) FROM publications").fetchone()
    except sqlite3.Error:
        try: c.close()
        except Exception: pass
        if has_wal(): raise RuntimeError("source_wal_requires_readonly_connection")
        mode = "stable_checkpoint"
        c = sqlite3.connect(p.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=10)
        c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    c.execute("BEGIN")
    publications = [dict(r) for r in c.execute("SELECT id,platform,account,platform_id,published_at,title,status,duration_seconds,first_seen,last_seen,archive_id FROM publications")]
    observations = [dict(r) for r in c.execute("SELECT publication_id,captured_at,source,metrics_json,raw_json FROM observations ORDER BY captured_at")]
    c.close()
    if mode == "stable_checkpoint" and (has_wal() or signature() != before):
        raise RuntimeError("source_changed_during_read")
    payload = json.dumps({"publications":publications,"observations":observations,"read_mode":mode},ensure_ascii=False,separators=(",",":")).encode()
    print(json.dumps({"encoding":"zlib+base64","payload":base64.b64encode(zlib.compress(payload)).decode()}))
except Exception as e:
    print(json.dumps({"error":str(e) if isinstance(e,RuntimeError) else "source_read_failed"}))
'''


class SourceError(ValueError):
    pass


class BindingConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except ValueError:
        return None


def _age(published, captured):
    a, b = _time(published), _time(captured)
    return round((b - a) / 3600, 3) if a is not None and b is not None and b >= a else None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _records(table, where="", args=()):
    if table not in TABLES:
        raise ValueError("Unknown publication record")
    with db.connection() as conn:
        rows = conn.execute(f"SELECT data FROM {table} {where}", args).fetchall()
    return [json.loads(row[0]) for row in rows]


def _put(conn, table, item, replace=False):
    if table not in TABLES:
        raise ValueError("Unknown publication record")
    item = dict(item)
    item.setdefault("created_at", db.now())
    item["updated_at"] = db.now()
    sql = f"INSERT {'OR REPLACE' if replace else 'OR IGNORE'} INTO {table}(id,data,created_at,updated_at) VALUES(?,?,?,?)"
    inserted = conn.execute(sql, (item["id"], _json(item), item["created_at"], item["updated_at"])).rowcount
    return item, inserted


def _metric_set(metrics, raw_record):
    result = {}
    for key, names in METRICS.items():
        name = next((name for name in names if name in metrics), None)
        original = metrics.get(name, {}) if name else {}
        item = original if isinstance(original, dict) else {"value": original, "raw": original}
        raw = item.get("raw")
        for raw_source in ("metrics_raw", "export_raw"):
            container = raw_record.get(raw_source)
            if raw is None and isinstance(container, dict) and name in container:
                raw = container[name]
        source_value = item.get("value")
        value = source_value
        if raw is None and source_value is not None:
            raw = source_value
        ambiguous = bool(item.get("zero_may_mean_unavailable")) and source_value == 0
        unit = "count"
        reason = None
        if key in {"completion_rate", "bounce_2s"}:
            percentage = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", raw) if isinstance(raw, str) else None
            unit = "ratio" if percentage or item.get("unit") in {"ratio", "fraction"} else None
            if percentage:
                value = float(percentage[1]) / 100
        elif key == "avg_watch_seconds":
            unit = "seconds" if item.get("unit") in {"seconds", "second", "s", "秒"} else None
        if not name or not _number(value):
            reason = "not_observed"
        elif ambiguous:
            reason = "zero_may_mean_unavailable"
        elif unit is None:
            reason = "unit_unknown"
        elif unit == "ratio" and not 0 <= value <= 1:
            reason = "out_of_range"
        result[key] = {"value": None if reason else value, "raw": raw, "unit": unit,
                       "approximate": bool(item.get("approximate")), "unknown": bool(reason),
                       "unknown_reason": reason, "zero_may_mean_unavailable": ambiguous,
                       "source_name": name, "source_value": source_value,
                       "definition": "平台累计指标" if key not in {"completion_rate", "bounce_2s", "avg_watch_seconds"} else "平台原始口径"}
    return result


def _read_sqlite(path):
    # Local override is explicit, never discovered by scanning private directories.
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SourceError("指定的本地采集数据库不存在；保留已有历史")
    conn = None
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        result = {"publications": [dict(r) for r in conn.execute("SELECT * FROM publications")],
                  "observations": [dict(r) for r in conn.execute("SELECT * FROM observations ORDER BY captured_at")],
                  "read_mode": "read_only"}
        return result
    except sqlite3.Error:
        raise SourceError("本地采集库无法只读打开；保留已有历史") from None
    finally:
        if conn is not None:
            conn.close()


def _fetch_source():
    override = os.environ.get("VIDEO_STUDIO_PUBLICATION_DB")
    if override:
        return _read_sqlite(override), {"kind": "local_sqlite", "label": "指定本地采集库", "location": Path(override).name}
    try:
        response = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "mini-alpha", "python3 -"],
            input=REMOTE_EXPORT, text=True, capture_output=True, timeout=55, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise SourceError("Alpha 连接超时或不可用；未触发采集，保留已有历史") from None
    if response.returncode != 0:
        raise SourceError("Alpha 只读连接失败；未触发采集，保留已有历史")
    try:
        payload = json.loads(response.stdout)
        if isinstance(payload, dict) and payload.get("encoding") == "zlib+base64":
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(base64.b64decode(payload["payload"], validate=True), 100 * 1024 * 1024)
            if not decoder.eof:
                raise ValueError("source_export_too_large")
            payload = json.loads(decoded)
    except (ValueError, TypeError, KeyError, zlib.error):
        raise SourceError("Alpha 返回的采集快照无效；保留已有历史") from None
    if not isinstance(payload, dict):
        raise SourceError("Alpha 返回的采集快照无效；保留已有历史")
    if payload.get("error"):
        messages = {"source_changed_during_read": "读取期间采集库发生变化，请稍后刷新",
                    "source_wal_requires_readonly_connection": "源库存在未合并观测，当前无法安全只读导出"}
        raise SourceError(messages.get(payload["error"], "Alpha 采集库无法只读打开") + "；保留已有历史")
    return payload, {"kind": "alpha_sqlite", "label": "星尚 Alpha 采集库", "location": SOURCE_LOCATION}


def _ingest(payload, source):
    if not isinstance(payload, dict) or not isinstance(payload.get("publications"), list) or not isinstance(payload.get("observations"), list):
        raise SourceError("采集快照格式无效；本次未导入")
    publications = {}
    for row in payload.get("publications", []):
        if not all(row.get(k) for k in ("id", "platform", "account", "published_at")):
            raise SourceError("采集快照缺少发布身份；本次未导入")
        old = publications.get(row["id"])
        if not old or (_time(row.get("last_seen")) or 0) >= (_time(old.get("last_seen")) or 0):
            publications[row["id"]] = row
    observations = []
    for row in payload.get("observations", []):
        if row.get("publication_id") not in publications:
            raise SourceError("采集观测无法关联发布身份；本次未导入")
        try:
            raw = json.loads(row["raw_json"])
            metrics = json.loads(row["metrics_json"])
        except (ValueError, TypeError, KeyError):
            raise SourceError("采集观测格式无效；本次未导入") from None
        if not isinstance(raw, dict) or not isinstance(metrics, dict):
            raise SourceError("采集观测字段无效；本次未导入")
        pub = publications[row["publication_id"]]
        observed_source = {**source, "label": row.get("source") or source["label"], "captured_at": row.get("captured_at")}
        content = {"publication_id": row["publication_id"], "captured_at": row.get("captured_at"),
                   "source_label": row.get("source"), "metrics": metrics, "raw_record": raw}
        observations.append({"id": _hash(content), "publication_id": row["publication_id"],
            "captured_at": row.get("captured_at"), "captured_epoch": _time(row.get("captured_at")),
            "age_hours": _age(pub["published_at"], row.get("captured_at")), "metrics": _metric_set(metrics, raw),
            "source": observed_source, "raw_metrics": metrics, "raw_record": raw})
    latest = {}
    for obs in observations:
        previous = latest.get(obs["publication_id"])
        if not previous or (obs["captured_epoch"] or 0) >= (previous["captured_epoch"] or 0):
            latest[obs["publication_id"]] = obs
    inserted_cases = inserted_obs = 0
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for obs in observations:
            _, inserted = _put(conn, "publication_observations", obs)
            inserted_obs += inserted
        for publication_id, pub in publications.items():
            row = conn.execute("SELECT data FROM publication_cases WHERE id=?", (publication_id,)).fetchone()
            old = json.loads(row[0]) if row else None
            obs = latest.get(publication_id)
            if old and (not obs or (old.get("captured_epoch") or 0) > (obs["captured_epoch"] or 0)):
                continue
            case = {"id": publication_id, "platform": pub["platform"], "account": pub["account"],
                    "title": pub.get("title") or "", "published_at": pub["published_at"],
                    "duration": pub.get("duration_seconds"), "status": pub.get("status", "未读取"),
                    "platform_id": pub.get("platform_id"), "archive_id": pub.get("archive_id"),
                    "source_url": None, "thumbnail_url": None,
                    "metrics": obs["metrics"] if obs else _metric_set({}, {}),
                    "captured_at": obs["captured_at"] if obs else None,
                    "captured_epoch": obs["captured_epoch"] if obs else None,
                    "age_hours": obs["age_hours"] if obs else None,
                    "source": obs["source"] if obs else source,
                    "latest_observation_id": obs["id"] if obs else None,
                    "identity_basis": "source_publication_id", "version_confirmed": False}
            if old:
                case["created_at"] = old["created_at"]
            _put(conn, "publication_cases", case, replace=True)
            inserted_cases += int(old is None)
    return inserted_cases, inserted_obs


def _active_bindings():
    return _records("publication_bindings", "WHERE json_extract(data,'$.status')='active'")


def _public_case(case, binding=None):
    result = {k: v for k, v in case.items() if k not in {"captured_epoch", "created_at", "updated_at"}}
    stamp = _time(result.get("captured_at"))
    result["freshness_hours"] = round((datetime.now(timezone.utc).timestamp() - stamp) / 3600, 3) if stamp else None
    result["binding"] = binding
    result["version_confirmed"] = binding is not None
    return result


def history_status():
    with db.connection() as conn:
        case_count = conn.execute("SELECT count(*) FROM publication_cases").fetchone()[0]
        observation_count = conn.execute("SELECT count(*) FROM publication_observations").fetchone()[0]
        latest = conn.execute("SELECT data FROM publication_cases ORDER BY json_extract(data,'$.captured_epoch') DESC LIMIT 1").fetchone()
        sync = conn.execute("SELECT data FROM publication_syncs ORDER BY created_at DESC LIMIT 1").fetchone()
        platforms = [r[0] for r in conn.execute("SELECT DISTINCT json_extract(data,'$.platform') FROM publication_cases ORDER BY 1")]
    last = json.loads(sync[0]) if sync else {}
    return {"status": last.get("status", "not_synced"), "source": last.get("source", {"kind": "alpha_sqlite", "label": "星尚 Alpha 采集库", "location": SOURCE_LOCATION}),
            "cases_count": case_count, "observations_count": observation_count,
            "last_sync_at": last.get("finished_at"), "latest_captured_at": json.loads(latest[0]).get("captured_at") if latest else None,
            "platforms": platforms, "error": last.get("error"), "read_mode": last.get("read_mode"),
            "cumulative": True, "refresh_action": "读取已有采集库"}


def sync_history():
    if not SYNC_LOCK.acquire(blocking=False):
        raise SourceError("已有历史刷新正在进行")
    try:
        error = None
        source = {"kind": "local_sqlite", "label": "指定本地采集库", "location": Path(os.environ["VIDEO_STUDIO_PUBLICATION_DB"]).name} if os.environ.get("VIDEO_STUDIO_PUBLICATION_DB") else {
            "kind": "alpha_sqlite", "label": "星尚 Alpha 采集库", "location": SOURCE_LOCATION}
        payload = {}
        try:
            payload, source = _fetch_source()
            status = "ok"
        except SourceError as exc:
            error = str(exc)
            status = "error"
        try:
            cases, observations = _ingest(payload, source) if status == "ok" else (0, 0)
        except (SourceError, ValueError, KeyError) as exc:
            error = str(exc) if isinstance(exc, SourceError) else "来源快照格式异常；保留已有历史"
            status, cases, observations = "error", 0, 0
        db.create("publication_syncs", {"status": status, "source": source, "error": error,
                  "read_mode": payload.get("read_mode"), "finished_at": db.now(),
                  "imported_cases": cases, "imported_observations": observations})
        return {**history_status(), "imported_cases": cases, "imported_observations": observations}
    finally:
        SYNC_LOCK.release()


def list_history(platform="", account="", query="", limit=30, offset=0, workspace_id=None):
    cases = _records("publication_cases")
    if workspace_id is not None:
        cases = workspace_cases(workspace_id)
    facets = {"platforms": sorted({c["platform"] for c in cases}), "accounts": sorted({c["account"] for c in cases})}
    selected = [c for c in cases if (not platform or c["platform"] == platform) and
                (not account or c["account"] == account) and (not query or query.casefold() in c["title"].casefold())]
    selected.sort(key=lambda c: c["published_at"], reverse=True)
    bindings = {b["publication_id"]: _public_binding(b, include_publication=False) for b in _active_bindings()}
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    return {"cases": [_public_case(c, bindings.get(c["id"])) for c in selected[offset:offset + limit]],
            "facets": facets, "total": len(selected), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(selected) else None}


def workspace_info(workspace_id):
    if workspace_id == "xingshang":
        return {"id": "xingshang", "name": "星尚"}
    item = db.get("business_projects", workspace_id)
    if not item:
        raise KeyError(workspace_id)
    return item


def workspace_cases(workspace_id):
    workspace_info(workspace_id)
    cases = _records("publication_cases")
    if workspace_id == "xingshang":
        return [c for c in cases if c.get("source", {}).get("kind") == "alpha_sqlite"
                and c.get("source", {}).get("location") == SOURCE_LOCATION]
    return [c for c in cases if c.get("workspace_id") == workspace_id]


def create_workspace(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("项目名称需要 1–60 个字符")
    name = name.strip()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = [json.loads(row[0])["name"] for row in conn.execute("SELECT data FROM business_projects")]
        if name.casefold() in {n.casefold() for n in ["星尚", *existing]}:
            raise ValueError("已有同名项目")
        item = {"id": db.identifier(), "name": name, "created_at": db.now(), "updated_at": db.now()}
        _put(conn, "business_projects", item)
    return item


def business_workspaces():
    rows = []
    for item in [{"id": "xingshang", "name": "星尚"}, *db.records("business_projects", 5000)]:
        cases = workspace_cases(item["id"])
        latest = max(cases, key=lambda c: c.get("captured_epoch") or 0, default={})
        rows.append({**item, "cases_count": len(cases), "platforms": sorted({c["platform"] for c in cases}),
            "accounts_count": len({c["account"] for c in cases}), "latest_captured_at": latest.get("captured_at"), "view_only": True})
    return {"workspaces": rows}


def history_dashboard(workspace_id="xingshang", platform="douyin", account="", date_from="", date_to=""):
    """Aggregate one platform's latest per-video observations, never observation sums."""
    workspace = workspace_info(workspace_id)
    if platform not in {"douyin", "xiaohongshu", "channels"}:
        raise ValueError("请选择一个平台")

    def parse_day(value):
        if not value:
            return None
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError()
            return parsed
        except (ValueError, TypeError):
            raise ValueError("日期格式应为 YYYY-MM-DD") from None

    start, end = parse_day(date_from), parse_day(date_to)
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    zone = ZoneInfo("Asia/Shanghai")

    def day_of(value):
        epoch = _time(value)
        return datetime.fromtimestamp(epoch, zone).date() if epoch is not None else None

    source_cases = workspace_cases(workspace_id)
    platform_cases = [c for c in source_cases if c.get("platform") == platform]
    accounts = sorted({c["account"] for c in platform_cases})
    scoped = [c for c in platform_cases if not account or c.get("account") == account]
    known_dates = sorted(day_of(c.get("published_at")) for c in scoped if day_of(c.get("published_at")))
    cases = []
    for case in scoped:
        day = day_of(case.get("published_at"))
        if day is None or (start and day < start) or (end and day > end):
            continue
        cases.append({**case, "published_date": day.isoformat()})
    selected_dates = sorted(c["published_date"] for c in cases)

    def metric_summary(rows, key):
        entries = [c.get("metrics", {}).get(key, {}) for c in rows]
        observed = [m for m in entries if not m.get("unknown", True) and _number(m.get("value"))
                    and m["value"] >= 0]
        values = [m["value"] for m in observed]
        return {"count": len(values), "missing_count": len(rows) - len(values),
                "ambiguous_count": sum(bool(m.get("zero_may_mean_unavailable")) for m in entries),
                "approximate_count": sum(bool(m.get("approximate")) for m in observed),
                "total": sum(values) if values else None, "median": median(values) if values else None,
                "min": min(values) if values else None, "max": max(values) if values else None}

    metrics = {key: metric_summary(cases, key) for key in METRICS}
    by_day = {}
    for case in cases:
        by_day.setdefault(case["published_date"], []).append(case)
    daily = [{"date": day, "videos": len(rows), "views": metric_summary(rows, "views")}
             for day, rows in sorted(by_day.items())]
    thresholds = [(0, 1000, "1 千以下"), (1000, 10000, "1 千–1 万"),
                  (10000, 100000, "1 万–10 万"), (100000, 1000000, "10 万–100 万"),
                  (1000000, None, "100 万及以上")]
    distribution = []
    for low, high, label in thresholds:
        rows = [c for c in cases if not c.get("metrics", {}).get("views", {}).get("unknown", True)
                and _number(c["metrics"]["views"].get("value"))
                and c["metrics"]["views"]["value"] >= low
                and (high is None or c["metrics"]["views"]["value"] < high)]
        distribution.append({"label": label, "min": low, "max": high, "count": len(rows),
                             "approximate_count": sum(bool(c["metrics"]["views"].get("approximate")) for c in rows)})
    captured = [c for c in cases if _time(c.get("captured_at")) is not None]
    oldest_capture = min(captured, key=lambda c: _time(c["captured_at"]), default={})
    latest_capture = max(captured, key=lambda c: _time(c["captured_at"]), default={})
    view_cases = [c for c in cases if not c.get("metrics", {}).get("views", {}).get("unknown", True)
                  and _number(c["metrics"]["views"].get("value")) and c["metrics"]["views"]["value"] >= 0]
    compact = lambda c: {k: c.get(k) for k in
                        ("id", "title", "account", "platform", "published_at", "published_date",
                         "captured_at", "age_hours", "metrics")}
    sorted_cases = sorted(cases, key=lambda c: _time(c.get("published_at")) or 0, reverse=True)
    return {
        "workspace": {"id": workspace_id, "name": workspace["name"]},
        "filters": {"platform": platform, "account": account, "date_from": date_from, "date_to": date_to},
        "facets": {"platforms": sorted({c["platform"] for c in source_cases}), "accounts": accounts,
                   "date_min": known_dates[0].isoformat() if known_dates else None,
                   "date_max": known_dates[-1].isoformat() if known_dates else None},
        "scope": {"date_from": date_from or (selected_dates[0] if selected_dates else None),
                  "date_to": date_to or (selected_dates[-1] if selected_dates else None),
                  "timezone": "Asia/Shanghai", "videos": len(cases), "project_videos": len(source_cases),
                  "undated_videos": sum(day_of(c.get("published_at")) is None for c in scoped),
                  "capture_from": oldest_capture.get("captured_at"), "capture_to": latest_capture.get("captured_at"),
                  "observation_basis": "latest_per_video", "cumulative": True},
        "metrics": metrics, "daily": daily, "distribution": distribution,
        "cases": [compact(c) for c in sorted_cases],
        "top_cases": [compact(c) for c in sorted(view_cases, key=lambda c: c["metrics"]["views"]["value"],
                                               reverse=True)[:10]],
    }


def history_detail(publication_id):
    case = db.get("publication_cases", publication_id)
    if not case:
        raise KeyError(publication_id)
    observations = _records("publication_observations",
        "WHERE json_extract(data,'$.publication_id')=? ORDER BY json_extract(data,'$.captured_epoch') DESC", (publication_id,))
    binding = next((b for b in _active_bindings() if b["publication_id"] == publication_id), None)
    return {"case": _public_case(case, _public_binding(binding, False) if binding else None),
            "observations": [{k: v for k, v in o.items() if k not in {"raw_record", "captured_epoch", "created_at", "updated_at"}} for o in observations],
            "observations_count": len(observations)}


def _public_binding(binding, include_publication=True):
    result = {k: v for k, v in binding.items() if k not in {"plan_snapshot", "render_snapshot", "updated_at"}}
    if include_publication:
        case = db.get("publication_cases", binding["publication_id"])
        active = _public_binding(binding, include_publication=False) if binding["status"] == "active" else None
        result["publication"] = _public_case(case, active) if case else None
    return result


def project_publications(project_id):
    if not db.get("projects", project_id):
        raise KeyError(project_id)
    return {"bindings": [_public_binding(b) for b in _active_bindings() if b["project_id"] == project_id]}


def bind_publication(project_id, publication_id, render_id, confirmed):
    if confirmed is not True:
        raise ValueError("请明确确认该发布记录对应所选成片版本")
    if not all(isinstance(value, str) and value for value in (project_id, publication_id, render_id)):
        raise ValueError("请选择发布记录和成片版本")
    project = db.get("projects", project_id)
    publication = db.get("publication_cases", publication_id)
    render = db.get("renders", render_id)
    if not project or not publication or not render:
        raise KeyError("binding_target")
    if render.get("project_id") != project_id:
        raise ValueError("所选成片不属于当前项目")
    plan = db.get("plans", render.get("plan_id"))
    asset = db.get("assets", render.get("asset_id"))
    if not plan or plan.get("project_id") != project_id or plan.get("version") != render.get("version") or not asset or asset.get("kind") != "video":
        raise ValueError("成片缺少对应方案或素材记录，无法绑定")
    path = Path(asset.get("path", "")).resolve()
    if not path.is_file() or not path.is_relative_to(db.DATA.resolve()):
        raise ValueError("本地成片文件不可用，无法确认版本")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    if checksum != render.get("sha256") or checksum != asset.get("sha256"):
        raise ValueError("成片文件指纹与渲染记录不一致，无法绑定")
    stamp = db.now()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM publication_bindings WHERE json_extract(data,'$.publication_id')=? AND json_extract(data,'$.status')='active'", (publication_id,)).fetchone()
        if row:
            current = json.loads(row[0])
            if current["project_id"] == project_id and current["render_id"] == render_id:
                return _public_binding(current)
            raise BindingConflict("该发布记录已绑定其他成片，请先撤销原关联")
        binding = {"id": db.identifier(), "project_id": project_id, "publication_id": publication_id,
            "render_id": render_id, "plan_id": plan["id"], "version": render.get("version"), "asset_id": asset["id"],
            "sha256": checksum, "plan_sha256": _hash(plan), "confirmed": True,
            "confirmed_at": stamp, "status": "active", "confirmation_basis": "user_asserted_exact_render",
            "plan_snapshot": plan, "render_snapshot": render, "render_url": render.get("url")}
        binding, _ = _put(conn, "publication_bindings", binding)
        _put(conn, "publication_events", {"id": db.identifier(), "kind": "binding_confirmed",
            "binding_id": binding["id"], "project_id": project_id, "publication_id": publication_id,
            "render_id": render_id, "plan_id": plan["id"], "sha256": checksum, "occurred_at": stamp})
    return _public_binding(binding)


def unbind_publication(project_id, binding_id):
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM publication_bindings WHERE id=?", (binding_id,)).fetchone()
        binding = json.loads(row[0]) if row else None
        if not binding or binding["project_id"] != project_id:
            raise KeyError(binding_id)
        if binding["status"] == "active":
            stamp = db.now()
            binding.update(status="revoked", revoked_at=stamp)
            _put(conn, "publication_bindings", binding, replace=True)
            _put(conn, "publication_events", {"id": db.identifier(), "kind": "binding_revoked",
                "binding_id": binding_id, "project_id": project_id, "publication_id": binding["publication_id"],
                "render_id": binding["render_id"], "occurred_at": stamp})
    return _public_binding(binding)
