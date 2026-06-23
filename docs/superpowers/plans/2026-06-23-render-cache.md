# Phase 1 Render Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the EIS/capacity graph viewer load instantly after the first render by adding a persistent, worker-shared, content-addressed cache; new files only recompute their own cluster.

**Architecture:** A new `battery_lab/render_cache.py` module provides content-addressed disk caching under `battery_visual_outputs/.render_cache/v1/`. Three caches: parsed file (Dataset), conditions workbook read, and cluster render payload. Keys embed file `(relpath, mtime, size)` so changed/added files automatically miss and recompute while everything else is served from disk. Existing `viewer_service.py` and `ui.py` functions are wrapped — no route or output changes.

**Tech Stack:** Python 3, Flask, stdlib only (`hashlib`, `json`, `os`, `pathlib`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-23-render-cache-design.md`

**Conventions for every commit step below:** run from the repo root `battery-lab-automation/`; every commit message must end with a trailing line `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Run tests with `python -m pytest` from the repo root.

> **Refinement note (realizes spec §4.5):** The spec's "classification report cache" is realized here as a **conditions-workbook cache** (Task 5) rather than full `EISMatchReport` serialization. The workbook xlsx read is the costly part of report building; the filename-based match is fast and left in place. This avoids fragile nested-dataclass serialization while removing the real cost from the hit path.

---

## File Structure

- **Create** `battery_lab/render_cache.py` — all cache logic: keys, atomic JSON store, parsed cache, conditions cache, cluster cache, GC, `register_sources`.
- **Create** `tests/test_render_cache.py` — unit + integration tests for the cache.
- **Modify** `battery_lab/ui.py` — `parse_file_cached_by_mtime` delegates to the disk parsed cache.
- **Modify** `battery_lab/viewer_service.py` — `build_*_viewer_report` use the conditions cache; the four overlay/source payload functions wrap their compute in the cluster cache.

---

## Task 1: Module scaffold + atomic JSON store

**Files:**
- Create: `battery_lab/render_cache.py`
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_cache.py
from pathlib import Path

from battery_lab import config, render_cache


def test_atomic_write_then_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    target = render_cache._cache_root() / "sub" / "x.json"
    render_cache._atomic_write_json(target, {"a": 1, "b": "두 번째"})
    assert render_cache._read_json(target) == {"a": 1, "b": "두 번째"}


def test_read_json_missing_or_corrupt_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    missing = render_cache._cache_root() / "nope.json"
    assert render_cache._read_json(missing) is None
    corrupt = render_cache._cache_root() / "bad.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{not json", encoding="utf-8")
    assert render_cache._read_json(corrupt) is None


def test_disabled_flag(monkeypatch):
    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "1")
    assert render_cache._disabled() is True
    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "0")
    assert render_cache._disabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'battery_lab.render_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# battery_lab/render_cache.py
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import config

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add battery_lab/render_cache.py tests/test_render_cache.py
git commit -m "feat(render-cache): atomic JSON store scaffold"
```

---

## Task 2: Key functions

**Files:**
- Modify: `battery_lab/render_cache.py`
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_file_identity_and_membersig(tmp_path):
    root = tmp_path
    a = root / "d" / "a.csv"
    a.parent.mkdir(parents=True)
    a.write_text("x", encoding="utf-8")
    b = root / "d" / "b.csv"
    b.write_text("y", encoding="utf-8")

    ident = render_cache.file_identity(a, root)
    assert ident[0] == "d/a.csv" and isinstance(ident[1], int) and isinstance(ident[2], int)

    # order-independent
    assert render_cache.membersig([a, b], root) == render_cache.membersig([b, a], root)

    # size change -> identity and membersig change
    sig_before = render_cache.membersig([a, b], root)
    a.write_text("xxxxx", encoding="utf-8")
    assert render_cache.membersig([a, b], root) != sig_before


def test_cluster_key_changes_with_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    ctx = render_cache.context_hash(tmp_path / "wb.xlsx", tmp_path / "ov.json")
    k1 = render_cache.cluster_key("eis", "comparison", "C001", "sig", ctx, {"show_fit": True})
    k2 = render_cache.cluster_key("eis", "comparison", "C001", "sig", ctx, {"show_fit": False})
    assert k1 != k2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py -q`
Expected: FAIL — `AttributeError: module 'battery_lab.render_cache' has no attribute 'file_identity'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to battery_lab/render_cache.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add battery_lab/render_cache.py tests/test_render_cache.py
git commit -m "feat(render-cache): content-addressed key functions"
```

---

## Task 3: Parsed-file disk cache

**Files:**
- Modify: `battery_lab/render_cache.py`
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
from battery_lab import file_io


def test_cached_parse_file_hits_disk_second_time(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    sample = data / "cell__capacity__cycle1__20260101.csv"
    sample.write_text("cycle,capacity\n1,10\n2,11\n", encoding="utf-8")

    calls = {"n": 0}
    real_parse = file_io.parse_file

    def counting_parse(path):
        calls["n"] += 1
        return real_parse(path)

    monkeypatch.setattr(render_cache, "_parse_file", counting_parse)

    ds1 = render_cache.cached_parse_file(sample, data)
    ds2 = render_cache.cached_parse_file(sample, data)
    assert calls["n"] == 1                      # second call served from disk
    assert ds1.rows == ds2.rows
    assert ds1.meta.cell_id == ds2.meta.cell_id

    # mtime/size change -> re-parse
    sample.write_text("cycle,capacity\n1,10\n2,11\n3,12\n", encoding="utf-8")
    render_cache.cached_parse_file(sample, data)
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_cached_parse_file_hits_disk_second_time -q`
Expected: FAIL — `AttributeError: ... has no attribute '_parse_file'` / `cached_parse_file`

- [ ] **Step 3: Write minimal implementation**

```python
# append to battery_lab/render_cache.py
from dataclasses import asdict

from .file_io import parse_file as _parse_file
from .models import FileMeta, ParsedDataset


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py::test_cached_parse_file_hits_disk_second_time -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add battery_lab/render_cache.py tests/test_render_cache.py
git commit -m "feat(render-cache): persistent parsed-file cache"
```

---

## Task 4: Wire parsed cache into `ui.parse_file_cached`

**Files:**
- Modify: `battery_lab/ui.py:1570-1577` (`parse_file_cached`, `parse_file_cached_by_mtime`)
- Test: `tests/test_render_cache.py`

Current code (for reference):

```python
def parse_file_cached(path: Path) -> Any:
    stat = path.stat()
    return parse_file_cached_by_mtime(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=512)
def parse_file_cached_by_mtime(path_text: str, mtime_ns: int, size: int) -> Any:
    return parse_file(Path(path_text))
```

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_ui_parse_file_cached_uses_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    from battery_lab import ui
    ui.parse_file_cached_by_mtime.cache_clear()

    sample = tmp_path / "cell__capacity__cycle1__20260101.csv"
    sample.write_text("cycle,capacity\n1,10\n", encoding="utf-8")

    calls = {"n": 0}
    real = render_cache._parse_file

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(render_cache, "_parse_file", counting)

    ui.parse_file_cached(sample)
    ui.parse_file_cached_by_mtime.cache_clear()   # drop in-memory layer
    ui.parse_file_cached(sample)                  # must hit DISK, not re-parse
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_ui_parse_file_cached_uses_disk -q`
Expected: FAIL — `calls["n"] == 2` (no disk layer yet)

- [ ] **Step 3: Write minimal implementation**

Add import near the top of `battery_lab/ui.py` (with the other `from .` imports):

```python
from . import render_cache
```

Replace `parse_file_cached_by_mtime` body so the in-memory lru sits on top of the disk cache. The disk cache keys on `(relpath, mtime, size)` relative to the file's own parent, which is stable per file:

```python
@lru_cache(maxsize=512)
def parse_file_cached_by_mtime(path_text: str, mtime_ns: int, size: int) -> Any:
    path = Path(path_text)
    return render_cache.cached_parse_file(path, path.parent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py::test_ui_parse_file_cached_uses_disk -q`
Expected: PASS

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (all existing tests still green)

- [ ] **Step 6: Commit**

```bash
git add battery_lab/ui.py tests/test_render_cache.py
git commit -m "feat(render-cache): back ui parse cache with disk layer"
```

---

## Task 5: Conditions-workbook cache (realizes spec §4.5)

**Files:**
- Modify: `battery_lab/render_cache.py`
- Modify: `battery_lab/viewer_service.py:299-317` (`build_eis_viewer_report`, `build_capacity_viewer_report`)
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_cached_read_conditions_reads_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    wb = tmp_path / "cell_conditions.csv"
    wb.write_text("cell_id,binder\nA,CMC\n", encoding="utf-8")

    calls = {"n": 0}

    def fake_reader(path):
        calls["n"] += 1
        return {"A": {"binder": "CMC"}}

    monkeypatch.setattr(render_cache, "_read_conditions", fake_reader)

    c1 = render_cache.cached_read_conditions(wb)
    c2 = render_cache.cached_read_conditions(wb)
    assert calls["n"] == 1
    assert c1 == c2 == {"A": {"binder": "CMC"}}

    # workbook change -> re-read
    wb.write_text("cell_id,binder\nA,CMC\nB,PVdF\n", encoding="utf-8")
    render_cache.cached_read_conditions(wb)
    assert calls["n"] == 2


def test_cached_read_conditions_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    assert render_cache.cached_read_conditions(tmp_path / "absent.xlsx") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py -k conditions -q`
Expected: FAIL — `has no attribute 'cached_read_conditions'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to battery_lab/render_cache.py
from .conditions import read_conditions as _read_conditions


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py -k conditions -q`
Expected: PASS

- [ ] **Step 5: Use it in `viewer_service.py`**

In `battery_lab/viewer_service.py`, add `from . import render_cache` to the imports, then replace the two identical lines (in `build_eis_viewer_report` and `build_capacity_viewer_report`):

```python
    conditions = read_conditions(condition_workbook) if condition_workbook.exists() else {}
```

with:

```python
    conditions = render_cache.cached_read_conditions(condition_workbook)
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add battery_lab/render_cache.py battery_lab/viewer_service.py tests/test_render_cache.py
git commit -m "feat(render-cache): cache condition-workbook reads"
```

---

## Task 6: Cluster render cache helpers + GC

**Files:**
- Modify: `battery_lab/render_cache.py`
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_cluster_cache_put_get_and_gc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    flags = {"show_fit": False}
    payload_v1 = {"available": True, "html": "<svg>v1</svg>", "errors": [], "title": "C001"}

    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigAAA", "ctx", flags) is None
    render_cache.cluster_cache_put("eis", "comparison", "C001", "sigAAA", "ctx", flags, payload_v1)
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigAAA", "ctx", flags) == payload_v1

    # new membersig (a file changed) -> miss, and GC drops the stale sig file
    payload_v2 = {"available": True, "html": "<svg>v2</svg>", "errors": [], "title": "C001"}
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigBBB", "ctx", flags) is None
    render_cache.cluster_cache_put("eis", "comparison", "C001", "sigBBB", "ctx", flags, payload_v2)

    cluster_dir = render_cache._cluster_dir("eis", "comparison", "C001")
    remaining = sorted(p.name for p in cluster_dir.glob("*.json"))
    assert all(name.startswith("sigBBB__") for name in remaining)
    assert len(remaining) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_cluster_cache_put_get_and_gc -q`
Expected: FAIL — `has no attribute 'cluster_cache_get'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to battery_lab/render_cache.py
import re


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
```

> Note: `ctx_hash` (conditions/override version) is stored inside the record as `_ctx` and checked on read, so a workbook change invalidates the cluster payload even though the filename only carries `member_sig` + flags.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py::test_cluster_cache_put_get_and_gc -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add battery_lab/render_cache.py tests/test_render_cache.py
git commit -m "feat(render-cache): cluster payload cache with GC"
```

---

## Task 7: Wrap `eis_overlay_payload` with the cluster cache

**Files:**
- Modify: `battery_lab/viewer_service.py:105-166` (`eis_overlay_payload`)
- Test: `tests/test_render_cache.py`

The function already resolves `rel_paths` (the exact member set) and `title` before loading series. Insert the cache lookup right after `rel_paths` is known and non-empty, and store right before returning.

- [ ] **Step 1: Write the failing test** (uses the bundled `sample_data`)

```python
# append to tests/test_render_cache.py
from battery_lab import ui as battery_ui
from battery_lab import viewer_service

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_eis_overlay_payload_renders_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    calls = {"n": 0}
    real_html = battery_ui.eis_overlay_html

    def counting_html(*args, **kwargs):
        calls["n"] += 1
        return real_html(*args, **kwargs)

    monkeypatch.setattr(battery_ui, "eis_overlay_html", counting_html)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", tmp_path / "ov.json")
    kwargs = dict(mode="comparison", key="", show_fit=False)

    p1 = viewer_service.eis_overlay_payload(*args, **kwargs)
    p2 = viewer_service.eis_overlay_payload(*args, **kwargs)
    assert calls["n"] == 1                 # second call served from cluster cache
    assert p1 == p2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_eis_overlay_payload_renders_once -q`
Expected: FAIL — `calls["n"] == 2`

- [ ] **Step 3: Write minimal implementation**

Add `from . import render_cache` to `battery_lab/viewer_service.py` imports (if not already added in Task 5). Then edit `eis_overlay_payload`. After the existing block that ends with:

```python
        if not rel_paths:
            return {"available": False, "html": "", "errors": ["표시할 EIS source가 없습니다."], "title": title}
```

insert the cache lookup:

```python
        member_paths = [eis_root / rel for rel in rel_paths]
        flags = {"show_fit": bool(show_fit)}
        ctx = render_cache.context_hash(condition_workbook, override_path)
        msig = render_cache.membersig(member_paths, eis_root)
        cache_id = key or f"{mode}:all"
        cached = render_cache.cluster_cache_get("eis", mode, cache_id, msig, ctx, flags)
        if cached is not None:
            return cached
```

Then, replace the existing `return {...}` at the end of the function:

```python
        return {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
```

with a store-then-return:

```python
        payload = {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
        render_cache.cluster_cache_put("eis", mode, cache_id, msig, ctx, flags, payload)
        return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py::test_eis_overlay_payload_renders_once -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add battery_lab/viewer_service.py tests/test_render_cache.py
git commit -m "feat(render-cache): cache EIS overlay renders"
```

---

## Task 8: Wrap `capacity_overlay_payload` with the cluster cache

**Files:**
- Modify: `battery_lab/viewer_service.py:245-278` (`capacity_overlay_payload`)
- Test: `tests/test_render_cache.py`

This function resolves `selected_paths` (member set) and `title`. Capacity has no `show_fit`, so `flags = {}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_capacity_overlay_payload_renders_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    calls = {"n": 0}
    real_html = battery_ui.capacity_overlay_html

    def counting_html(*args, **kwargs):
        calls["n"] += 1
        return real_html(*args, **kwargs)

    monkeypatch.setattr(battery_ui, "capacity_overlay_html", counting_html)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", tmp_path / "ov.json")
    kwargs = dict(mode="cluster", key="")

    p1 = viewer_service.capacity_overlay_payload(*args, **kwargs)
    p2 = viewer_service.capacity_overlay_payload(*args, **kwargs)
    assert calls["n"] == 1
    assert p1 == p2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_capacity_overlay_payload_renders_once -q`
Expected: FAIL — `calls["n"] == 2`

- [ ] **Step 3: Write minimal implementation**

In `capacity_overlay_payload`, after the existing guard:

```python
        if not selected_paths:
            return {"available": False, "html": "", "errors": ["표시할 Capacity summary source가 없습니다."], "title": title}
```

insert:

```python
        member_paths = [capacity_root / rel for rel in selected_paths]
        flags: dict = {}
        ctx = render_cache.context_hash(condition_workbook, override_path)
        msig = render_cache.membersig(member_paths, capacity_root)
        cache_id = key or f"{mode}:all"
        cached = render_cache.cluster_cache_get("capacity", mode, cache_id, msig, ctx, flags)
        if cached is not None:
            return cached
```

Then replace the final return:

```python
        return {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
```

with:

```python
        payload = {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
        render_cache.cluster_cache_put("capacity", mode, cache_id, msig, ctx, flags, payload)
        return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_cache.py::test_capacity_overlay_payload_renders_once -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add battery_lab/viewer_service.py tests/test_render_cache.py
git commit -m "feat(render-cache): cache capacity overlay renders"
```

---

## Task 9: Incremental behavior + output-identity regression

**Files:**
- Test: `tests/test_render_cache.py`

These tests assert the two core spec guarantees: (a) cache output is byte-identical to uncached output, and (b) changing one member invalidates only that cluster's payload.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_cache_output_matches_uncached(tmp_path, monkeypatch):
    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", tmp_path / "ov.json")
    kwargs = dict(mode="comparison", key="", show_fit=False)

    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "1")
    battery_ui.parse_file_cached_by_mtime.cache_clear()
    uncached = viewer_service.eis_overlay_payload(*args, **kwargs)

    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "0")
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()
    cached = viewer_service.eis_overlay_payload(*args, **kwargs)

    assert cached["html"] == uncached["html"]


def test_context_change_invalidates_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    flags = {"show_fit": False}
    payload = {"available": True, "html": "<svg/>", "errors": [], "title": "C001"}
    render_cache.cluster_cache_put("eis", "comparison", "C001", "sig", "ctxOLD", flags, payload)
    # same key/sig but a different context (workbook changed) must miss:
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sig", "ctxNEW", flags) is None
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sig", "ctxOLD", flags) == payload
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_render_cache.py -k "matches_uncached or context_change" -q`
Expected: PASS (behavior already implemented in Tasks 6–8; these lock it in)

> If `test_cache_output_matches_uncached` fails, the cache is altering output — investigate serialization in Task 3 / payload construction in Task 7 before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_render_cache.py
git commit -m "test(render-cache): output-identity and invalidation guarantees"
```

---

## Task 10: Source-payload caching + `register_sources` warm hook

**Files:**
- Modify: `battery_lab/render_cache.py` (add `register_sources`)
- Modify: `battery_lab/viewer_service.py` (`eis_source_payload`, `capacity_source_payload` — single-file caching)
- Test: `tests/test_render_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_render_cache.py
def test_register_sources_is_callable_and_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    # No-op-safe hook for Phase 2 upload to call; must never raise.
    render_cache.register_sources("eis", ["does/not/exist.seo"])
    render_cache.register_sources("capacity", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_cache.py::test_register_sources_is_callable_and_safe -q`
Expected: FAIL — `has no attribute 'register_sources'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to battery_lab/render_cache.py
def register_sources(kind: str, relpaths: list[str]) -> None:
    """Phase 2 upload hook. Lazy caching already covers correctness, so this is a
    best-effort warm/no-op placeholder. Intentionally cheap and exception-safe;
    eager warming will be wired to warm_overlay_cache in the Phase 2 plan."""
    try:
        _ = (kind, list(relpaths or []))
    except Exception:
        pass
```

- [ ] **Step 4: Add single-file caching to source payloads**

In `eis_source_payload` (wraps one file) — after `path = safe_child(eis_root, rel_path)`:

```python
        flags = {"show_fit": bool(show_fit)}
        ctx = render_cache.context_hash(eis_root / "__none__", eis_root / "__none__")
        msig = render_cache.membersig([path], eis_root)
        cached = render_cache.cluster_cache_get("eis", "source", rel_path, msig, ctx, flags)
        if cached is not None:
            return cached
```

and change the final `return {...}` to:

```python
        payload = {"available": bool(html_doc), "html": html_doc, "errors": [], "title": path.name, "point_count": len(points)}
        render_cache.cluster_cache_put("eis", "source", rel_path, msig, ctx, flags, payload)
        return payload
```

Apply the same pattern to `capacity_source_payload` (use `kind="capacity"`, `flags={}`, `cluster_cache_get("capacity", "source", rel_path, msig, ctx, {})`), storing the existing payload dict before each `return` in its success paths. For the `.wrd` and dataset branches, build `payload = {...}` (the dict currently returned), call `cluster_cache_put(...)`, then `return payload`. Leave the `except` error branch uncached.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add battery_lab/render_cache.py battery_lab/viewer_service.py tests/test_render_cache.py
git commit -m "feat(render-cache): cache source previews + register_sources hook"
```

---

## Task 11: Manual smoke test + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-06-23-render-cache-design.md` (mark implemented)

- [ ] **Step 1: Smoke-test the live endpoints**

Run the app locally and hit an overlay twice; the second response should be visibly faster and a cache tree should appear:

```bash
python -m pytest -q                      # full suite green
python - <<'PY'
from pathlib import Path
from battery_lab import config, viewer_service
root = config.BATTERY_EIS_ROOT
args = (config.BATTERY_EIS_ROOT, config.BATTERY_CAPACITY_ROOT, config.BATTERY_CONDITION_WORKBOOK, config.BATTERY_MATCH_EIS_JSON)
import time
for i in range(2):
    t = time.perf_counter()
    p = viewer_service.eis_overlay_payload(*args, mode="comparison", key="", show_fit=False)
    print(f"call {i}: {time.perf_counter()-t:.3f}s available={p['available']}")
print("cache dir:", (config.BATTERY_OUTPUT_ROOT / ".render_cache").exists())
PY
```

Expected: `call 1` slower than `call 0` is wrong — expect `call 1` (second) much faster; `cache dir: True`.

- [ ] **Step 2: Verify cache survives a fresh process**

Re-run the snippet in a new `python` process (cold in-memory caches). First call should still be fast because the disk cluster cache is hit.

- [ ] **Step 3: Mark spec implemented + commit**

Add a line at the top of the spec: `> Implemented on branch phase1-render-cache (2026-06-23).` Then:

```bash
git add docs/superpowers/specs/2026-06-23-render-cache-design.md
git commit -m "docs(render-cache): mark Phase 1 spec implemented"
```

---

## Self-Review Checklist (completed during planning)

- **Spec coverage:** §3 three layers → parsed cache (Task 3/4), conditions cache (Task 5, realizes §4.5), cluster cache (Tasks 6–8, 10). §4.2 keys → Task 2. §4.3 read path → Tasks 7–8. §4.6 incremental → Task 9. §4.7 atomicity/GC → Tasks 1, 6. §4.8 register_sources → Task 10. §6 best-effort/disable flag → Tasks 1, 9. §7 tests → all tasks.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code.
- **Type consistency:** `cached_parse_file(path, root)`, `membersig(paths, root)`, `cluster_cache_get/put(kind, mode, cluster_id, member_sig, ctx_hash, flags[, payload])`, `context_hash(workbook, override)` used identically across Tasks 2–10.
- **Known follow-ups (out of scope):** `parsed/` directory size GC; eager warming inside `register_sources` (Phase 2).
