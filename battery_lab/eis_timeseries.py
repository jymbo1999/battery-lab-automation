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
