#!/usr/bin/env python3
"""Export the approved local demonstration, or restore it into an empty directory.

This utility never imports the application or calls a model. Publication history
is omitted at the record boundary, not deleted from a copy of the source SQLite.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
MARKER = "${AHB_DATA}"
HISTORY_MARKER = "${AHB_HISTORY}"
VERSION = 1
SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token|cookie|credential)", re.I)
KEY_VALUE = re.compile(r"\bsk-(?:api-)?[A-Za-z0-9_-]{16,}")
SIGNED_KEYS = {"token", "access_token", "signature", "sig", "key", "api_key", "auth", "authorization", "ossaccesskeyid", "x-amz-signature", "x-amz-credential", "x-amz-security-token"}
TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
HISTORY_FIELDS = {"reference_case_ids", "reference_case_snapshot", "reference_case_id", "publication_id", "publication_case_id", "publication_snapshot", "publication_bindings", "publication_observations"}
TEXT_SUFFIXES = {".html", ".txt", ".md", ".csv", ".srt", ".vtt", ".ass", ".log", ".ffconcat", ".py"}
HTML_LINK = re.compile(r"(\b(?:src|href)\s*=\s*)([\"'])(.*?)(\2)", re.I)


class DemoError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise DemoError("Unsafe relative file path in demonstration")
    return path.as_posix()


def _walk(value, location=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{location}[{index}]")
    elif isinstance(value, str):
        yield location, value


def _relative_url(target, document, *, query="", fragment=""):
    path = quote(Path(os.path.relpath(target, document.parent)).as_posix(), safe="/")
    return urlunsplit(("", "", path, query, fragment))


def _rewrite_html_links(text, resolver):
    def replace(match):
        replacement = resolver(html.unescape(match[3]))
        if replacement is None:
            return match[0]
        return match[1] + match[2] + html.escape(replacement, quote=True) + match[4]
    return HTML_LINK.sub(replace, text)


def _installed_text(source, entry, data_dir):
    text = source.read_text(encoding="utf-8")
    mappings = {link["export_url"]: link for link in entry.get("html_links", [])}
    if mappings:
        document = data_dir / entry["data_path"]
        def resolve(value):
            link = mappings.get(value)
            if link is None:
                return None
            url = urlsplit(value)
            return _relative_url(data_dir / _relative(link["data_path"]), document, query=url.query, fragment=url.fragment)
        text = _rewrite_html_links(text, resolve)
    return _relocate(text, data_dir)


def _check_html_links(index, allowed_root):
    count = 0
    for path in index.values():
        if path.suffix.lower() != ".html":
            continue
        for match in HTML_LINK.finditer(path.read_text(encoding="utf-8")):
            value = html.unescape(match[3])
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(allowed_root) or not target.is_file():
                raise DemoError("Static HTML contains a missing or external local media link")
            count += 1
    return count


def _records(source_data):
    database = source_data / "studio.sqlite"
    if not database.is_file():
        raise DemoError("Source studio.sqlite is missing")
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result, excluded = {}, {}
        for table in tables:
            if not TABLE_NAME.fullmatch(table):
                raise DemoError("Unexpected database table name")
            if table.startswith("publication_"):
                excluded[table] = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                result[table] = []
                continue
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if columns != {"id", "data", "created_at", "updated_at"}:
                raise DemoError(f"Unexpected record schema: {table}")
            result[table] = [dict(id=row[0], data=json.loads(row[1]), created_at=row[2], updated_at=row[3])
                             for row in conn.execute(f'SELECT id,data,created_at,updated_at FROM "{table}" ORDER BY id')]
        return result, excluded
    finally:
        conn.close()


def export_demo(destination, *, source_data, project_root=ROOT, include_output=(), exclude_output=()):
    destination, source_data, project_root = map(lambda p: Path(p).expanduser().resolve(), (destination, source_data, project_root))
    if destination.exists():
        raise DemoError("Export destination already exists; use a new directory")
    if destination.is_relative_to(source_data):
        raise DemoError("Export destination cannot be inside the source data directory")
    records, excluded = _records(source_data)
    files, redactions, path_transforms = {}, [], []
    excluded_output = {_relative(str(path)) for path in exclude_output}
    if any(not path.startswith("output/") for path in excluded_output):
        raise DemoError("Excluded output paths must be project-relative output/... paths")

    def external(path):
        path = path.resolve()
        if not path.is_relative_to(project_root / "output") or not path.is_file():
            raise DemoError("Referenced external file is not an existing project output file")
        relative = path.relative_to(project_root).as_posix()
        if relative in excluded_output:
            return HISTORY_MARKER + "/excluded/" + relative
        target = "frames/imported-evidence/" + relative
        files[target] = (path, "external/" + relative)
        return MARKER + "/" + target

    def portable(value, location=""):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                at = f"{location}.{key}"
                if location.startswith("records") and key in HISTORY_FIELDS and item:
                    raise DemoError(f"Nonempty publication history reference at {at}")
                if SECRET_KEY.search(key) and item:
                    result[key] = None
                    redactions.append({"location": at, "reason": "credential_field"})
                else:
                    result[key] = portable(item, at)
            return result
        if isinstance(value, list):
            return [portable(item, f"{location}[{index}]") for index, item in enumerate(value)]
        if not isinstance(value, str):
            return value
        original = value
        path_kind = None
        if value.startswith(str(source_data) + "/"):
            value = MARKER + "/" + Path(value).relative_to(source_data).as_posix()
            path_kind = "data_dependency"
        elif value.startswith(str(project_root) + "/"):
            path = Path(value)
            if path.is_relative_to(project_root / "output") and path.is_file():
                value = external(path)
                path_kind = "output_dependency" if value.startswith(MARKER) else "excluded_output_reference"
            else:
                value = HISTORY_MARKER + "/project/" + path.relative_to(project_root).as_posix()
                path_kind = "historical_project_path"
        # Reports may embed links in Markdown, HTML, or saved error text. Only
        # already-approved data/output roots are portable, never arbitrary home
        # directories. Exact external file fields above also add that dependency.
        before = value
        for prefix, replacement in ((str(source_data), MARKER), (str(project_root / "output"), MARKER + "/frames/imported-evidence/output"), (str(project_root), HISTORY_MARKER + "/project")):
            value = value.replace(prefix + "/", replacement + "/")
            value = value.replace(quote(prefix, safe="/") + "/", replacement + "/")
        if value != before:
            path_kind = path_kind or "embedded_project_paths"
        # Historical reports can name old worktrees or machines. Keep the path
        # semantics as evidence, but never discover/copy those other directories.
        before = value
        value = re.sub(r"/(?:Users|home)/[^/\s\"'<>]+(?=/|$)", HISTORY_MARKER + "/home", value)
        value = re.sub(r"(?:/private)?/var/folders/[^/\s]+/[^/\s]+", HISTORY_MARKER + "/temp", value)
        value = re.sub(r"(?<![\w:])/(?:private/)?tmp/", HISTORY_MARKER + "/tmp/", value)
        if value != before:
            path_kind = "historical_environment_path"
        if path_kind:
            path_transforms.append({"location": location, "kind": path_kind})
        if value.startswith(("https://", "http://")):
            url = urlsplit(value)
            if url.username or url.password or any(key.lower() in SIGNED_KEYS for key, _ in parse_qsl(url.query)):
                value = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
                redactions.append({"location": location, "reason": "signed_or_authenticated_url"})
        if KEY_VALUE.search(value):
            value = KEY_VALUE.sub("[REDACTED]", value)
            redactions.append({"location": location, "reason": "credential_value"})
        return value if value != original else original

    portable_records = portable(records, "records")
    for path in sorted(source_data.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_data)
        if relative.parts[0] == "tmp" or "__pycache__" in relative.parts or relative.name == ".DS_Store" or ".sqlite" in relative.name or relative.name.endswith((".pyc", ".db", ".db-wal", ".db-shm")):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source_data):
            raise DemoError("Source data contains a symlink outside its directory")
        if path.suffix.lower() in {".env", ".log"} or path.name in {"providers.json", "server.env"}:
            raise DemoError("Unexpected private configuration or log in source data")
        files[relative.as_posix()] = (path, "files/" + relative.as_posix())
    for requested in include_output:
        path = Path(requested).expanduser()
        path = (project_root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(project_root / "output") or not path.exists():
            raise DemoError("--include-output must name a project output file or directory")
        for entry in ([path] if path.is_file() else sorted(path.rglob("*"))):
            if entry.is_file() and "__pycache__" not in entry.parts and entry.suffix != ".pyc" and entry.name != ".DS_Store" and entry.relative_to(project_root).as_posix() not in excluded_output:
                external(entry)

    destination.mkdir(parents=True)
    try:
        (destination / "records.json").write_text(_json(portable_records), encoding="utf-8")
        entries, processed = [], set()
        while len(processed) < len(files):
            for relative in sorted(set(files) - processed):
                source, bundle_path = files[relative]
                target = destination / bundle_path
                target.parent.mkdir(parents=True, exist_ok=True)
                original_hash = _digest(source)
                html_links = []
                if source.suffix.lower() == ".json":
                    raw = source.read_text(encoding="utf-8")
                    original = json.loads(raw)
                    converted = portable(original, bundle_path)
                    target.write_text(_json(converted) if converted != original else raw, encoding="utf-8")
                elif source.suffix.lower() in TEXT_SUFFIXES:
                    raw = source.read_text(encoding="utf-8")
                    if source.suffix.lower() == ".html":
                        def resolve_link(value):
                            url = urlsplit(value)
                            if url.scheme or url.netloc or not url.path:
                                return None
                            referenced = (source.parent / unquote(url.path)).resolve()
                            if referenced.is_relative_to(source_data):
                                referenced_relative = referenced.relative_to(source_data).as_posix()
                            elif referenced.is_relative_to(project_root / "output"):
                                mapped = external(referenced)
                                if not mapped.startswith(MARKER + "/"):
                                    raise DemoError("Static HTML references an excluded output file")
                                referenced_relative = mapped[len(MARKER) + 1:]
                            else:
                                raise DemoError("Static HTML references a file outside the approved data/output scope")
                            if referenced_relative not in files:
                                raise DemoError("Static HTML references an unavailable data file")
                            url_new = _relative_url(destination / files[referenced_relative][1], target,
                                                    query=url.query, fragment=url.fragment)
                            html_links.append({"export_url": url_new, "data_path": referenced_relative})
                            if url_new != value:
                                path_transforms.append({"location": f"{bundle_path}.links[{len(html_links)-1}]", "kind": "html_relative_link"})
                            return url_new
                        raw = _rewrite_html_links(raw, resolve_link)
                    converted = portable(raw, bundle_path)
                    target.write_text(converted, encoding="utf-8")
                else:
                    shutil.copyfile(source, target)
                if _digest(source) != original_hash:
                    raise DemoError("A source file changed during export; retry after active work completes")
                entries.append({"path": bundle_path, "data_path": relative, "size": target.stat().st_size,
                                "sha256": _digest(target), "source_sha256": original_hash,
                                "transformed": _digest(target) != original_hash,
                                **({"html_links": html_links} if html_links else {})})
                processed.add(relative)
        metadata = {"schema_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                    "records_sha256": _digest(destination / "records.json"),
                    "counts": {table: len(rows) for table, rows in portable_records.items()},
                    "excluded_publication_tables": sorted(excluded),
                    "publication_exclusion_reason": "Historical publication data is outside the approved public demonstration.", "redactions": redactions,
                    "excluded_output_paths": sorted(excluded_output), "path_transforms": path_transforms,
                    "files": sorted(entries, key=lambda item: item["data_path"])}
        (destination / "manifest.json").write_text(_json(metadata), encoding="utf-8")
        return check_demo(destination)
    except Exception:
        # The new export belongs to this invocation. Never touch the source.
        shutil.rmtree(destination)
        raise


def _load_snapshot(snapshot):
    snapshot = Path(snapshot).expanduser().resolve()
    metadata = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if metadata.get("schema_version") != VERSION:
        raise DemoError("Unsupported demonstration schema")
    if _digest(snapshot / "records.json") != metadata["records_sha256"]:
        raise DemoError("Demonstration records checksum mismatch")
    records = json.loads((snapshot / "records.json").read_text(encoding="utf-8"))
    index = {}
    for entry in metadata["files"]:
        relative, bundle = _relative(entry["data_path"]), _relative(entry["path"])
        if relative in index:
            raise DemoError("Duplicate file destination")
        source = (snapshot / bundle).resolve()
        if not source.is_relative_to(snapshot) or not source.is_file() or _digest(source) != entry["sha256"]:
            raise DemoError(f"Demonstration file missing or changed: {bundle}")
        index[relative] = source
    for table, rows in records.items():
        if not TABLE_NAME.fullmatch(table) or not isinstance(rows, list):
            raise DemoError("Invalid demonstration table")
        if table.startswith("publication_") and rows:
            raise DemoError("Publication history must not be included")
        if len(rows) != metadata["counts"].get(table):
            raise DemoError("Demonstration record count mismatch")
    return snapshot, metadata, records, index


def _resolve_reference(value, index, data_dir=None):
    if value.startswith("/api/media/"):
        relative = unquote(urlsplit(value).path.removeprefix("/api/media/"))
    elif value.startswith(MARKER + "/"):
        relative = value[len(MARKER) + 1:]
    elif data_dir and value.startswith(str(data_dir) + "/"):
        relative = Path(value).relative_to(data_dir).as_posix()
    else:
        return None
    relative = _relative(relative)
    return index.get(relative)


def _relocate(value, data_dir):
    if isinstance(value, dict):
        return {key: _relocate(item, data_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_relocate(item, data_dir) for item in value]
    if isinstance(value, str):
        return value.replace(MARKER + "/", str(data_dir) + "/")
    return value


def _validate_records(records, index, data_dir=None):
    by_table = {table: {row["id"]: row["data"] for row in rows} for table, rows in records.items()}
    media_refs, manifests, legacy, missing_audit_outputs = 0, 0, 0, 0
    for table, rows in records.items():
        if table.startswith("publication_") and rows:
            raise DemoError("Publication history is not empty")
        for location, value in _walk(rows, table):
            if value.startswith("/api/media/"):
                media_refs += 1
                if not _resolve_reference(value, index, data_dir):
                    raise DemoError(f"Missing media reference at {location}")
            if value.startswith(MARKER + "/") or (data_dir and value.startswith(str(data_dir) + "/")):
                if not _resolve_reference(value, index, data_dir):
                    if location.endswith(".output_path") and ".audit." in location:
                        missing_audit_outputs += 1
                    else:
                        raise DemoError(f"Missing local evidence reference at {location}")
    assets = by_table.get("assets", {})
    for asset in assets.values():
        path = _resolve_reference(asset["path"], index, data_dir)
        if not path or _digest(path) != asset["sha256"]:
            raise DemoError("Asset source file is missing or its hash changed")
    for revision in by_table.get("animation_revisions", {}).values():
        for scene in revision.get("scenes", []):
            for key in ("source_path", "prior_source_path"):
                if scene.get(key) and not _resolve_reference(scene[key], index, data_dir):
                    raise DemoError("Animation source is missing")
    for render in by_table.get("renders", {}).values():
        path = _resolve_reference(render["url"], index, data_dir)
        if not path or _digest(path) != render["sha256"]:
            raise DemoError("Rendered video is missing or its hash changed")
        if not render.get("manifest_url"):
            legacy += 1
            continue
        manifests += 1
        manifest_path = _resolve_reference(render["manifest_url"], index, data_dir)
        frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
        if frozen.get("plan_id") != render.get("plan_id") or frozen.get("output_sha256") != render["sha256"] or frozen.get("compiled_timeline") != render.get("compiled_timeline"):
            raise DemoError("Frozen render evidence does not match its render")
        for item in frozen.get("assets", []):
            if item["asset_id"] not in assets or assets[item["asset_id"]]["sha256"] != item["sha256"]:
                raise DemoError("Frozen render source fingerprint does not match")
    return {"counts": {table: len(rows) for table, rows in records.items()}, "files": len(index),
            "media_references": media_refs, "render_manifests": manifests, "legacy_renders_without_manifest": legacy,
            "historical_missing_audit_outputs": missing_audit_outputs}


def check_demo(snapshot, *, data_dir=None):
    snapshot, metadata, records, index = _load_snapshot(snapshot)
    if data_dir is not None:
        data_dir = Path(data_dir).expanduser().resolve()
        expected_records = _relocate(records, data_dir)
        records, _ = _records(data_dir)
        # _records omits history for export, so explicitly verify the installed DB.
        conn = sqlite3.connect((data_dir / "studio.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            for table in metadata["counts"]:
                if table.startswith("publication_") and conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]:
                    raise DemoError("Installed data contains publication history")
        finally:
            conn.close()
        for entry in metadata["files"]:
            path = data_dir / _relative(entry["data_path"])
            if not path.is_file():
                raise DemoError("Installed demonstration file is missing")
            if not entry["transformed"] and _digest(path) != entry["sha256"]:
                raise DemoError("Installed media bytes changed")
            if entry["transformed"]:
                source = index[entry["data_path"]]
                if path.suffix.lower() == ".json":
                    expected = _relocate(json.loads(source.read_text(encoding="utf-8")), data_dir)
                    if json.loads(path.read_text(encoding="utf-8")) != expected:
                        raise DemoError("Installed JSON evidence changed")
                elif path.read_text(encoding="utf-8") != _installed_text(source, entry, data_dir):
                    raise DemoError("Installed text evidence changed")
        index = {relative: data_dir / relative for relative in index}
        if records != expected_records:
            raise DemoError("Installed records changed")
    report = _validate_records(records, index, data_dir)
    report["html_local_links"] = _check_html_links(index, data_dir or snapshot)
    report["redacted_fields"] = len(metadata["redactions"])
    report["relocated_path_fields"] = len(metadata.get("path_transforms", []))
    return report


def import_demo(snapshot, *, data_dir):
    snapshot, metadata, records, index = _load_snapshot(snapshot)
    _validate_records(records, index)
    data_dir = Path(data_dir).expanduser().resolve()
    if data_dir == snapshot or data_dir.is_relative_to(snapshot):
        raise DemoError("Import directory cannot be inside the snapshot")
    if data_dir.exists() and (not data_dir.is_dir() or any(data_dir.iterdir())):
        raise DemoError("Import refuses to overwrite existing data; choose an empty --data-dir")

    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        for entry in metadata["files"]:
            relative = entry["data_path"]
            source, target = index[relative], data_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() == ".json" and entry["transformed"]:
                target.write_text(_json(_relocate(json.loads(source.read_text(encoding="utf-8")), data_dir)), encoding="utf-8")
            elif source.suffix.lower() in TEXT_SUFFIXES and entry["transformed"]:
                target.write_text(_installed_text(source, entry, data_dir), encoding="utf-8")
            else:
                shutil.copyfile(source, target)
        conn = sqlite3.connect(data_dir / "studio.sqlite")
        try:
            with conn:
                for table, rows in records.items():
                    conn.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
                    conn.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?)',
                                     [(r["id"], json.dumps(_relocate(r["data"], data_dir), ensure_ascii=False, allow_nan=False), r["created_at"], r["updated_at"]) for r in rows])
        finally:
            conn.close()
        return check_demo(snapshot, data_dir=data_dir)
    except Exception:
        shutil.rmtree(data_dir)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Create a new portable snapshot, excluding publication history")
    export.add_argument("--destination", type=Path, default=ROOT / "demo")
    export.add_argument("--source-data", type=Path, default=ROOT / "data")
    export.add_argument("--include-output", type=Path, action="append", default=[], help="Approved output file/directory, absolute or relative to the project root; repeat to include several")
    export.add_argument("--exclude-output", action="append", default=[], help="Exact project-relative output/... file path; repeat for several files")
    restore = sub.add_parser("import", help="Restore only into an empty data directory")
    restore.add_argument("--snapshot", type=Path, default=ROOT / "demo")
    restore.add_argument("--data-dir", type=Path, default=ROOT / "data")
    check = sub.add_parser("check", help="Verify a snapshot, optionally also its installed data")
    check.add_argument("--snapshot", type=Path, default=ROOT / "demo")
    check.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            report = export_demo(args.destination, source_data=args.source_data, include_output=args.include_output, exclude_output=args.exclude_output)
        elif args.command == "import":
            report = import_demo(args.snapshot, data_dir=args.data_dir)
        else:
            report = check_demo(args.snapshot, data_dir=args.data_dir)
        print(_json(report), end="")
    except (DemoError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        # Do not echo supplier payloads or credential values in errors.
        print(f"Demo data operation failed: {type(error).__name__}: {error if isinstance(error, DemoError) else 'input or filesystem validation failed'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
