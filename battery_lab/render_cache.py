from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import config
from .conditions import read_conditions as _read_conditions
from .file_io import parse_file as _parse_file
from .models import FileMeta, ParsedDataset

CACHE_VERSION = "v1"


def _cache_root() -> Path:
    return config.BATTERY_OUTPUT_ROOT / ".render_cache" / CACHE_VERSION


def _disabled() -> bool:
    return config.env_truthy("BATTERY_RENDER_CACHE_DISABLE")


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(data, ensure_ascii=False, default=str)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        # Cache is best-effort (spec §6); never break the request on write failure.
        pass


def _sha1(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def file_identity(path: Path, root: Path) -> list:
    st = path.stat()
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = str(path)
    return [rel, st.st_mtime_ns, st.st_size]


def membersig(paths: list[Path], root: Path) -> str:
    return _sha1(sorted(file_identity(p, root) for p in paths))


def _stat_tuple(path: Path) -> list | None:
    try:
        st = path.stat()
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def context_hash(condition_workbook: Path, override_path: Path) -> str:
    return _sha1([_stat_tuple(condition_workbook), _stat_tuple(override_path)])


def parsed_key(path: Path, root: Path) -> str:
    return _sha1(file_identity(path, root))


def cluster_key(kind: str, mode: str, cluster_id: str, member_sig: str, ctx_hash: str, flags: dict) -> str:
    return _sha1([kind, mode, str(cluster_id), member_sig, ctx_hash, flags])


def _dataset_to_json(ds: ParsedDataset) -> dict:
    meta = asdict(ds.meta)
    meta["path"] = str(ds.meta.path)
    return {"meta": meta, "rows": ds.rows, "columns": ds.columns}


def _dataset_from_json(d: dict) -> ParsedDataset:
    meta = dict(d["meta"])
    meta["path"] = Path(meta["path"])
    return ParsedDataset(meta=FileMeta(**meta), rows=d["rows"], columns=d.get("columns", []))


def _parsed_path(key: str) -> Path:
    return _cache_root() / "parsed" / f"{key}.json"


def cached_parse_file(path: Path, root: Path) -> ParsedDataset:
    if _disabled():
        return _parse_file(path)
    key = parsed_key(path, root)
    cached = _read_json(_parsed_path(key))
    if cached is not None:
        try:
            return _dataset_from_json(cached)
        except (KeyError, TypeError):
            pass  # stale/incompatible shape -> recompute
    dataset = _parse_file(path)
    _atomic_write_json(_parsed_path(key), _dataset_to_json(dataset))
    return dataset


def _conditions_path(workbook: Path) -> Path:
    key = _sha1([str(workbook), _stat_tuple(workbook)])
    return _cache_root() / "conditions" / f"{key}.json"


def cached_read_conditions(workbook: Path) -> dict:
    if not workbook.exists():
        return {}
    if _disabled():
        return _read_conditions(workbook)
    path = _conditions_path(workbook)
    cached = _read_json(path)
    if cached is not None:
        return cached
    conditions = _read_conditions(workbook)
    _atomic_write_json(path, conditions)
    return conditions


def _safe_id(cluster_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(cluster_id))[:80] or "_"


def _flags_key(flags: dict) -> str:
    return _sha1(flags)[:8]


def _cluster_dir(kind: str, mode: str, cluster_id: str) -> Path:
    return _cache_root() / "clusters" / kind / mode / _safe_id(cluster_id)


def _cluster_path(kind: str, mode: str, cluster_id: str, member_sig: str, flags: dict) -> Path:
    return _cluster_dir(kind, mode, cluster_id) / f"{member_sig}__{_flags_key(flags)}.json"


def cluster_cache_get(kind, mode, cluster_id, member_sig, ctx_hash, flags) -> dict | None:
    if _disabled():
        return None
    # ctx_hash is part of identity but does not affect the filename; fold it into a guard field.
    cached = _read_json(_cluster_path(kind, mode, cluster_id, member_sig, flags))
    if cached is None or cached.get("_ctx") != ctx_hash:
        return None
    payload = dict(cached)
    payload.pop("_ctx", None)
    return payload


def cluster_cache_put(kind, mode, cluster_id, member_sig, ctx_hash, flags, payload: dict) -> None:
    if _disabled():
        return
    record = dict(payload)
    record["_ctx"] = ctx_hash
    path = _cluster_path(kind, mode, cluster_id, member_sig, flags)
    _atomic_write_json(path, record)
    _gc_cluster_dir(path.parent, keep_prefix=f"{member_sig}__")


def _gc_cluster_dir(cluster_dir: Path, keep_prefix: str) -> None:
    try:
        for entry in cluster_dir.glob("*.json"):
            if not entry.name.startswith(keep_prefix):
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def register_sources(kind: str, relpaths: list[str]) -> None:
    """Phase 2 upload hook. Lazy caching already covers correctness, so this is a
    best-effort warm/no-op placeholder. Intentionally cheap and exception-safe;
    eager warming will be wired to warm_overlay_cache in the Phase 2 plan."""
    try:
        _ = (kind, list(relpaths or []))
    except Exception:
        pass
