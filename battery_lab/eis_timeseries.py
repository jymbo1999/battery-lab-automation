from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .eis_matching import (
    EISConditionMatch,
    compact_date,
    compact_text,
    folder_date,
    time_sort_key,
)

REPLICATE_VOTE_RATIO = 0.6  # second-place journal row this close -> competing/ambiguous


@dataclass(frozen=True)
class EISTimeSeriesCluster:
    cluster_id: str
    folder_date: str
    cluster_signature: str
    member_paths: str
    time_points: str
    has_zero: bool
    has_24: bool
    file_count: int
    merge_provenance: str
    condition_key: str
    condition_sample: str
    condition_date: str
    date_delta_days: int | None
    match_status: str
    candidate_options: str
    reason: str


def hr_num(time_point: str) -> int | None:
    match = re.search(r"\d+", time_point or "")
    return int(match.group(0)) if match else None


def _fmt_hrs(hours: set[int | None]) -> str:
    nums = sorted(n for n in hours if n is not None)
    return "[" + ",".join(str(n) for n in nums) + "]"


def _base_signature(signature: str) -> str:
    # Strip a trailing replicate index (cell number) so cell-1/cell-2 fragments
    # of the same material share a base for endpoint-rule merging. Thickness
    # tokens end in a letter ("...3t") so they survive.
    return re.sub(r"\d{1,2}$", "", signature)


def _stage1_groups(matches: list[EISConditionMatch]) -> dict[str, list[EISConditionMatch]]:
    # Collapse splits caused only by spacing/punctuation: compact_text removes
    # spaces/symbols, so "260521 dl 2t2t" and "260521 dl2t2t" key together while
    # thickness/replicate digits keep genuinely different cells apart.
    groups: dict[str, list[EISConditionMatch]] = defaultdict(list)
    for match in matches:
        groups[compact_text(match.file_group_key)].append(match)
    return groups


def _hours(group: list[EISConditionMatch]) -> set[int | None]:
    return {hr_num(m.time_point) for m in group if m.time_point}


def _merge_fragments(sig_groups: list[tuple[str, list[EISConditionMatch]]]) -> list[dict[str, Any]]:
    """Endpoint-rule merge within one base signature.

    A 0-side fragment (has 0hr, no 24hr) merges with a 24-side fragment (has
    24hr, no 0hr) when their hour sets are disjoint. Complete groups (0 and 24)
    and groups with neither endpoint are passed through unchanged.
    """
    complete: list[list[EISConditionMatch]] = []
    left: list[tuple[str, list[EISConditionMatch], set[int | None]]] = []
    right: list[tuple[str, list[EISConditionMatch], set[int | None]]] = []
    neither: list[list[EISConditionMatch]] = []
    for sig, group in sig_groups:
        hours = _hours(group)
        has0, has24 = 0 in hours, 24 in hours
        if has0 and has24:
            complete.append(group)
        elif has0:
            left.append((sig, group, hours))
        elif has24:
            right.append((sig, group, hours))
        else:
            neither.append(group)

    results: list[dict[str, Any]] = [{"members": list(g), "provenance": ""} for g in complete]

    right_sorted = sorted(right, key=lambda x: x[0])
    used = set()
    for lsig, lgroup, lh in sorted(left, key=lambda x: x[0]):
        paired = None
        for j, (rsig, rgroup, rh) in enumerate(right_sorted):
            if j in used or (lh & rh):  # already taken, or overlapping hours
                continue
            paired = (j, rsig, rgroup, rh)
            break
        if paired is None:
            results.append({"members": list(lgroup), "provenance": ""})
            continue
        j, rsig, rgroup, rh = paired
        used.add(j)
        prov = f"{lsig}{_fmt_hrs(lh)}+{rsig}{_fmt_hrs(rh)}"
        results.append({"members": list(lgroup) + list(rgroup), "provenance": prov})

    for j, (rsig, rgroup, rh) in enumerate(right_sorted):
        if j not in used:
            results.append({"members": list(rgroup), "provenance": ""})
    for group in neither:
        results.append({"members": list(group), "provenance": ""})
    return results


def _candidate_options(ranked: list[tuple[str, int]], meta: dict[str, EISConditionMatch],
                       conditions: dict[str, dict[str, Any]], *, max_rows: int = 8) -> str:
    options = []
    for key, weight in ranked[:max_rows]:
        cond = conditions.get(key, {})
        match = meta[key]
        options.append({
            "condition_key": key,
            "journal_row": cond.get("_source_row_number") or "",
            "sample": str(cond.get("sample") or match.condition_sample or key),
            "date": compact_date(cond.get("date")) or match.condition_date or "",
            "date_delta_days": match.date_delta_days,
            "score": int(weight),
        })
    return json.dumps(options, ensure_ascii=False)


def _cluster_dict(members: list[EISConditionMatch], provenance: str,
                  conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paths = [m.relative_path for m in members]
    fdate = folder_date(members[0].relative_path)
    time_points = sorted({m.time_point for m in members if m.time_point}, key=time_sort_key)
    hours = {hr_num(t) for t in time_points}
    has_zero, has_24 = 0 in hours, 24 in hours

    votes: dict[str, int] = defaultdict(int)
    meta: dict[str, EISConditionMatch] = {}
    for m in members:
        if m.condition_key:
            votes[m.condition_key] += max(int(m.score), 1)
            meta.setdefault(m.condition_key, m)
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    best_key = ranked[0][0] if ranked else ""
    best = meta.get(best_key)
    competing = len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * REPLICATE_VOTE_RATIO

    if not (has_zero and has_24):
        status = "ambiguous"
        reason = "0hr/24hr 끝점이 불완전합니다(병합 후에도 한쪽 결손)."
    elif not best_key:
        status = "ambiguous"
        reason = "일지 행 후보를 찾지 못했습니다."
    elif competing:
        status = "ambiguous"
        reason = "멤버 파일들이 서로 다른 일지 행을 가리킵니다."
    else:
        row = conditions.get(best_key, {}).get("_source_row_number") or "?"
        status = "verified"
        reason = f"0hr→24hr 완비 + 단일 일지 행 {row} (파일 {len(members)}개)."
        if provenance:
            reason += f" 병합: {provenance}."

    return {
        "folder_date": fdate,
        "cluster_signature": compact_text(members[0].file_group_key),
        "member_paths": ";".join(sorted(paths)),
        "has_zero": has_zero,
        "has_24": has_24,
        "file_count": len(members),
        "merge_provenance": provenance,
        "time_points": ";".join(time_points),
        "condition_key": best_key,
        "condition_sample": str(best.condition_sample if best else ""),
        "condition_date": str(best.condition_date if best else ""),
        "date_delta_days": best.date_delta_days if best else None,
        "match_status": status,
        "candidate_options": _candidate_options(ranked, meta, conditions),
        "reason": reason,
    }


def build_time_series_clusters(
    matches: list[EISConditionMatch],
    conditions: dict[str, dict[str, Any]],
) -> list[EISTimeSeriesCluster]:
    """Re-cluster EIS _hr files into one-cell (0hr->24hr) groups and map each to
    a journal row. Stage 1 collapses spacing splits; stage 2 merges 0-side and
    24-side fragments; stage 3 classifies and votes the journal row; finally a
    journal row claimed by >1 cluster marks those clusters as conflicts."""
    ts_matches = [m for m in matches if m.is_time_series]
    stage1 = _stage1_groups(ts_matches)

    by_base: dict[str, list[tuple[str, list[EISConditionMatch]]]] = defaultdict(list)
    for sig, group in stage1.items():
        by_base[_base_signature(sig)].append((sig, group))

    cluster_dicts: list[dict[str, Any]] = []
    for sig_groups in by_base.values():
        for item in _merge_fragments(sig_groups):
            cluster_dicts.append(_cluster_dict(item["members"], item["provenance"], conditions))

    cluster_dicts.sort(key=lambda c: (c["folder_date"], c["cluster_signature"], c["member_paths"]))

    row_counts = Counter(c["condition_key"] for c in cluster_dicts if c["condition_key"])
    clusters: list[EISTimeSeriesCluster] = []
    for idx, c in enumerate(cluster_dicts, start=1):
        status, reason = c["match_status"], c["reason"]
        if c["condition_key"] and row_counts[c["condition_key"]] > 1:
            status = "conflict"
            reason = f"같은 일지 행을 {row_counts[c['condition_key']]}개 클러스터가 차지(충돌). " + reason
        clusters.append(EISTimeSeriesCluster(
            cluster_id=f"TS{idx:03d}",
            folder_date=c["folder_date"],
            cluster_signature=c["cluster_signature"],
            member_paths=c["member_paths"],
            time_points=c["time_points"],
            has_zero=c["has_zero"],
            has_24=c["has_24"],
            file_count=c["file_count"],
            merge_provenance=c["merge_provenance"],
            condition_key=c["condition_key"],
            condition_sample=c["condition_sample"],
            condition_date=c["condition_date"],
            date_delta_days=c["date_delta_days"],
            match_status=status,
            candidate_options=c["candidate_options"],
            reason=reason,
        ))
    return clusters
