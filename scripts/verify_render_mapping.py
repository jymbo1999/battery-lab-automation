"""Verify that the EIS mapping deployed on Render matches our local mapping.

Past-data correction job: the manual overrides + checklist answers were applied
to the *local* persistent disk only. Render keeps its own copy of
``eis_match_overrides.json`` on its persistent disk, so it can lag behind. This
script proves whether Render's mapping is identical to local, file by file.

Source of truth = the **local effective mapping** (local overrides + local
workbook + current code, reduced to per-file: status / journal row / delete).

Two modes
---------
1. LIVE API  (recommended) — read Render's *real* output over HTTP. No file
   download, no token needed (the read endpoint is unauthenticated). Because we
   read what Render actually computes, this covers ALL drift vectors at once:
   Render's overrides, its condition workbook, AND its deployed code version.

       .venv/bin/python scripts/verify_render_mapping.py \
           --render-url https://YOUR-APP.onrender.com

2. FILE      (offline) — re-simulate using a Render overrides file you exported.
   This only checks overrides drift; it assumes Render's code + workbook match
   local (so it cannot catch a stale Render code pin or workbook).

       .venv/bin/python scripts/verify_render_mapping.py \
           --render-overrides /path/to/render_eis_match_overrides.json

Exit code 0 = Render matches local. Exit code 1 = divergence found.
Read-only: never writes to any overrides file.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Allow running as `python scripts/verify_render_mapping.py` from the repo root
# without the package being pip-installed: put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from battery_lab.config import (
    BATTERY_CONDITION_WORKBOOK,
    BATTERY_EIS_ROOT,
    BATTERY_MATCH_EIS_JSON,
)
from battery_lab.matching_service import build_match_payload

# The Battery blueprint is mounted at /battery; the matches API is unauthenticated GET.
MATCHES_PATH = "/battery/api/eis/matches"


def normalize_rows(final_rows: list[dict[str, Any]]) -> dict[str, tuple]:
    """Reduce a payload's ``final_rows`` to a comparable per-file mapping.

    Key = file's relative_path. Value = (status, condition_key, journal_row).
    ``status`` is already "delete_candidate" for delete decisions, so deletes
    are first-class here without special handling.
    """
    out: dict[str, tuple] = {}
    for row in final_rows or []:
        key = str(row.get("relative_path") or "")
        if not key:
            continue
        out[key] = (
            str(row.get("status") or ""),
            str(row.get("condition_key") or ""),
            str(row.get("journal_row") or ""),
        )
    return out


def local_payload() -> dict[str, Any]:
    """Exactly what Render's own endpoint computes, but against local data."""
    return build_match_payload("eis", BATTERY_EIS_ROOT, BATTERY_CONDITION_WORKBOOK, BATTERY_MATCH_EIS_JSON)


def render_payload_via_api(base_url: str, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + MATCHES_PATH
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted operator URL)
        if resp.status != 200:
            raise SystemExit(f"Render API returned HTTP {resp.status} for {url}")
        return json.loads(resp.read().decode("utf-8"))


def render_payload_via_file(override_file: Path) -> dict[str, Any]:
    if not override_file.exists():
        raise SystemExit(f"overrides file not found: {override_file}")
    # Re-simulate Render using its overrides but local code + local workbook.
    return build_match_payload("eis", BATTERY_EIS_ROOT, BATTERY_CONDITION_WORKBOOK, override_file)


def diff_rows(local: dict[str, tuple], render: dict[str, tuple]) -> dict[str, list]:
    only_local = sorted(k for k in local if k not in render)
    only_render = sorted(k for k in render if k not in local)
    changed = sorted((k, local[k], render[k]) for k in local if k in render and local[k] != render[k])
    return {"only_local": only_local, "only_render": only_render, "changed": changed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Render EIS mapping == local mapping.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--render-url", help="Base URL of the deployed app, e.g. https://app.onrender.com")
    src.add_argument("--render-overrides", type=Path, help="Exported Render eis_match_overrides.json (offline mode).")
    ap.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds (live mode).")
    args = ap.parse_args()

    local = local_payload()
    local_rows = normalize_rows(local.get("final_rows"))

    if args.render_url:
        mode = f"LIVE API  ({args.render_url.rstrip('/') + MATCHES_PATH})"
        render = render_payload_via_api(args.render_url, args.timeout)
    else:
        mode = f"FILE      ({args.render_overrides})"
        render = render_payload_via_file(args.render_overrides)
    render_rows = normalize_rows(render.get("final_rows"))

    print("Mode             :", mode)
    print("Local files      :", len(local_rows), "| override_count:", local.get("override_count"))
    print("Render files     :", len(render_rows), "| override_count:", render.get("override_count"))
    print("Local status     :", local.get("status_counts"))
    print("Render status    :", render.get("status_counts"))
    if args.render_url:
        print("NOTE: live mode reflects Render's real code + workbook + overrides.")
    else:
        print("NOTE: file mode assumes Render's code + workbook == local (only overrides checked).")

    d = diff_rows(local_rows, render_rows)
    n = len(d["only_local"]) + len(d["only_render"]) + len(d["changed"])

    print(f"\n=== Per-file mapping diff [{'OK' if n == 0 else f'{n} DIFF'}] ===")
    if n == 0:
        print("  (identical)")
    else:
        for k in d["only_local"]:
            print(f"  ONLY IN LOCAL  (Render is missing this file): {k}")
        for k in d["only_render"]:
            print(f"  ONLY IN RENDER (not present locally): {k}")
        for k, lv, rv in d["changed"]:
            print(f"  DIFFERS: {k}")
            print(f"      LOCAL  (status, condition_key, journal_row): {lv}")
            print(f"      RENDER (status, condition_key, journal_row): {rv}")

    print("\n" + "=" * 64)
    if n == 0:
        print("RESULT: PASS — Render mapping is identical to local. ✅")
        return 0
    print(f"RESULT: FAIL — {n} file(s) differ. ❌")
    print("Each DIFFERS line shows what Render needs to match. Apply the missing")
    print("override decisions on Render (re-run the checklist apply there, or")
    print("upload the local overrides), then re-run until this reports PASS.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
