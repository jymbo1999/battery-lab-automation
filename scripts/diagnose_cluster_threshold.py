"""Read-only: diagnose EIS comparison clusters after 24hr TS promotion.

Prints the real-data funnel from EIS files and JYJ rows to comparison clusters,
including how many time-series 24hr endpoints were promoted as formal members.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from battery_lab import eis_timeseries, scope
from battery_lab.config import BATTERY_EIS_ROOT, BATTERY_CONDITION_WORKBOOK, BATTERY_MATCH_EIS_JSON
from battery_lab.conditions import REQUIRED_COMPARISON_FIELDS, clean, read_conditions
from battery_lab.eis_matching import (
    EIS_SUFFIXES,
    _merge_primary_and_ts,
    _primary_cells,
    _time_series_cells,
    backbone_components,
    build_comparison_clusters,
    match_eis_files_to_conditions,
)
from battery_lab.matching_service import collect_source_files, load_match_overrides


def main() -> None:
    root = BATTERY_EIS_ROOT.resolve()
    source_paths = collect_source_files(root, EIS_SUFFIXES)
    conditions = scope.filter_in_scope(read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ"))
    overrides = load_match_overrides(BATTERY_MATCH_EIS_JSON)
    print(f"source files          : {len(source_paths)}")
    print(f"condition rows (scope): {len(conditions)}")
    print(f"manual overrides      : {len(overrides)}")

    inventory, matches = match_eis_files_to_conditions(source_paths, conditions, root, overrides)
    ts_clusters = eis_timeseries.build_time_series_clusters(matches, conditions)

    ts_count = sum(1 for m in matches if m.is_time_series)
    print(f"time-series files     : {ts_count}")
    print(f"time-series clusters  : {len(ts_clusters)}")
    print("TS status counts      :", dict(Counter(c.match_status for c in ts_clusters)))
    print(f"non-time-series files : {len(matches) - ts_count}")
    print("status counts         :", dict(Counter(m.status for m in matches)))

    # Stage: primary cells + promoted 24hr TS endpoints.
    primary = _primary_cells(matches, conditions)
    ts24 = _time_series_cells(ts_clusters, matches, conditions)
    usable = _merge_primary_and_ts(primary, ts24)
    print(f"primary cells (dedup) : {len(usable)}  (one per journal row)")
    print(f"steady primary cells  : {len(primary)}")
    print(f"TS 24hr candidates    : {len(ts24)}")
    print(f"promoted TS 24hr      : {sum(1 for c in usable if c.origin == 'ts_24hr')}")

    # Stage: keep only rows with all 4 backbone fields filled, bucket by backbone
    buckets: dict[tuple, list] = defaultdict(list)
    dropped_missing = 0
    for cell in usable:
        cond = conditions[cell.condition_key]
        if any(not clean(cond.get(f)) for f in REQUIRED_COMPARISON_FIELDS):
            dropped_missing += 1
            continue
        key = tuple(clean(cond.get(f)) for f in REQUIRED_COMPARISON_FIELDS)
        buckets[key].append(cell)
    print(f"dropped (missing field): {dropped_missing}")
    print(f"backbone buckets        : {len(buckets)}")

    # ---- Current implementation: backbone bucket = 1 cluster, keep >=2 ----
    cur_clusters = []
    for key, group in buckets.items():
        for comp in backbone_components(group):
            cur_clusters.append((key, len(comp)))
    cur_sizes = sorted((n for _, n in cur_clusters), reverse=True)
    cur_members = sum(cur_sizes)
    print("\n=== COMPARISON CLUSTERS (backbone-only, keep >=2) ===")
    print(f"clusters : {len(cur_clusters)}   sizes: {cur_sizes}")
    print(f"cells in clusters : {cur_members}")
    impl_clusters, _ = build_comparison_clusters(matches, ts_clusters, conditions)
    impl_sizes = sorted((c.condition_count for c in impl_clusters), reverse=True)
    impl_promoted = sum(1 for c in impl_clusters for origin in c.member_origins.split(";") if origin == "ts_24hr")
    print(f"implementation check : {len(impl_clusters)} clusters   sizes: {impl_sizes}")
    print(f"implementation TS 24hr promoted: {impl_promoted}")

    print("\n=== per-backbone breakdown (no threshold) ===")
    for key, group in sorted(buckets.items(), key=lambda r: -len(r[1])):
        tag = "" if len(group) >= 2 else "  (singleton -> dropped)"
        print(f"  n={len(group):2d}  {key}{tag}")


if __name__ == "__main__":
    main()
