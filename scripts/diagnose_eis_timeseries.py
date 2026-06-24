"""Diagnostic: current EIS time-series grouping vs the 0hr->24hr cluster rule.

Read-only. Replays the real grouping logic on the real EIS tree and prints:
  (a) per-group time_points + whether 0hr / 24hr endpoints are present
  (b) cells (same material+replicate) split across multiple group_keys
  (c) merge candidates (fragments under the same folder/cell base)
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from battery_lab.config import BATTERY_EIS_ROOT
from battery_lab.eis_matching import (
    EIS_SUFFIXES,
    eis_cell_key,
    eis_group_key,
    folder_date,
    has_hr_token,
    relative_path,
    strip_channel_suffix,
    time_sort_key,
)
from battery_lab.file_io import guess_time_point


def collect(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EIS_SUFFIXES]


def hr_num(tp: str) -> int | None:
    m = re.search(r"\d+", tp or "")
    return int(m.group(0)) if m else None


def main() -> None:
    root = BATTERY_EIS_ROOT.resolve()
    paths = collect(root)
    ts = []
    for p in sorted(paths):
        rel = relative_path(p, root)
        tp = guess_time_point(p.stem) or guess_time_point(rel)
        is_ts = bool(tp) or has_hr_token(rel)
        if not is_ts:
            continue
        ts.append(
            {
                "rel": rel,
                "stem": p.stem,
                "date": folder_date(rel),
                "cell_key": eis_cell_key(p.stem),
                "group_key": eis_group_key(p, root, True),
                "tp": tp,
            }
        )

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in ts:
        groups[r["group_key"]].append(r)

    print(f"time-series files: {len(ts)}   groups: {len(groups)}\n")
    print("=== (a) per-group time points + endpoints ===")
    no_zero = no_24 = 0
    for gk in sorted(groups):
        rows = groups[gk]
        tps = sorted({r["tp"] for r in rows if r["tp"]}, key=time_sort_key)
        nums = {hr_num(t) for t in tps}
        has0 = 0 in nums
        has24 = 24 in nums
        no_zero += not has0
        no_24 += not has24
        flag = "" if (has0 and has24) else "  <-- MISSING " + ("0hr " if not has0 else "") + ("24hr" if not has24 else "")
        print(f"{gk:45s} n={len(rows):2d}  [{', '.join(tps)}]{flag}")
    print(f"\ngroups missing 0hr: {no_zero}   missing 24hr: {no_24}\n")

    # (b) cell base = group_key with trailing replicate index stripped too
    def cell_base(gk: str) -> str:
        # strip a trailing standalone replicate digit like "... 1" / "..._2"
        return re.sub(r"[ _]\d{1,2}$", "", gk).strip()

    base_groups: dict[str, set[str]] = defaultdict(set)
    for gk in groups:
        base_groups[cell_base(gk)].add(gk)

    print("=== (b/c) bases mapping to >1 group_key (possible wrong split) ===")
    split = 0
    for base in sorted(base_groups):
        gks = base_groups[base]
        if len(gks) < 2:
            continue
        split += 1
        print(f"\nBASE: {base}  -> {len(gks)} groups")
        for gk in sorted(gks):
            tps = sorted({r["tp"] for r in groups[gk] if r["tp"]}, key=time_sort_key)
            print(f"    {gk:45s} [{', '.join(tps)}]")
    print(f"\nbases with multiple groups: {split}")

    # (d) reclustered view
    from battery_lab.eis_matching import match_eis_files_to_conditions
    from battery_lab.conditions import read_conditions
    from battery_lab.config import BATTERY_CONDITION_WORKBOOK
    from battery_lab import scope, eis_timeseries

    conds = (scope.filter_in_scope(read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ"))
             if BATTERY_CONDITION_WORKBOOK.exists() else {})
    _, matches = match_eis_files_to_conditions(paths, conds, root)
    clusters = eis_timeseries.build_time_series_clusters(matches, conds)
    miss = sum(1 for c in clusters if not (c.has_zero and c.has_24))
    print(f"\n=== (d) reclustered: {len(clusters)} clusters (was {len(groups)} groups) ===")
    print(f"clusters missing an endpoint: {miss}\n")
    for c in clusters:
        flag = "" if (c.has_zero and c.has_24) else "  <-- INCOMPLETE"
        prov = f"  merge={c.merge_provenance}" if c.merge_provenance else ""
        print(f"{c.cluster_id} {c.cluster_signature:30s} {c.match_status:9s} [{c.time_points}]{flag}{prov}")


if __name__ == "__main__":
    main()
