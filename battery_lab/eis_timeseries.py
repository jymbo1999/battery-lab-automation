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
