"""Lightweight, opt-in request timing for the graph viewer.

Disabled unless ``BATTERY_PERF_LOG`` is truthy, so it is safe to leave in
place. Emits single greppable ``[PERF]`` lines to stdout (captured by Render's
log stream) so a slow cluster-overlay request can be broken down into segments
without a profiler. Remove once the graph-loading bottleneck is resolved.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import config

_ENV_LINE_DONE = False


def enabled() -> bool:
    return config.env_truthy("BATTERY_PERF_LOG")


def now() -> float:
    return time.perf_counter()


def ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000.0, 1)


def _emit(line: str) -> None:
    print(line, flush=True)


def emit_overlay(kind: str, mode: str, key: str, marks: dict[str, Any], total_since: float, roots: tuple[Path, Path, Path]) -> None:
    """Print one per-request breakdown line, then the one-time env line."""
    if not enabled():
        return
    marks["total_ms"] = ms(total_since)
    fields = " ".join(f"{name}={marks[name]}" for name in marks)
    eis_root, cap_root, out_root = roots
    _emit(
        f"[PERF] overlay kind={kind} mode={mode} key={key!r} {fields} "
        f"cpu={os.cpu_count()} eis_root={eis_root} cap_root={cap_root} out_root={out_root}"
    )
    _emit_env_once(roots)


def _emit_env_once(roots: tuple[Path, Path, Path]) -> None:
    """Once per process: raw disk-stat latency + a CPU micro-benchmark.

    Separates "disk is slow" (high per_stat_us) from "CPU is throttled"
    (high cpubench_ms) from "the tree is just huge" (statstorm_n)."""
    global _ENV_LINE_DONE
    if _ENV_LINE_DONE:
        return
    _ENV_LINE_DONE = True
    eis_root, cap_root, out_root = roots

    files: list[Path] = []
    statstorm_ms = -1.0
    per_stat_us = -1.0
    try:
        for path in Path(eis_root).rglob("*"):
            if path.is_file():
                files.append(path)
                if len(files) >= 200:
                    break
        start = time.perf_counter()
        for path in files:
            try:
                path.stat()
            except OSError:
                pass
        statstorm_ms = round((time.perf_counter() - start) * 1000.0, 1)
        per_stat_us = round((statstorm_ms * 1000.0) / max(len(files), 1), 1)
    except Exception:
        pass

    start = time.perf_counter()
    total = 0
    for i in range(2_000_000):
        total += i
    cpubench_ms = round((time.perf_counter() - start) * 1000.0, 1)

    _emit(
        f"[PERF] env cpu={os.cpu_count()} statstorm_n={len(files)} statstorm_ms={statstorm_ms} "
        f"per_stat_us={per_stat_us} cpubench_2M_ms={cpubench_ms} "
        f"eis_root={eis_root} cap_root={cap_root} out_root={out_root}"
    )
