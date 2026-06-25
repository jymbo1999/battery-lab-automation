from __future__ import annotations

import csv
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .conditions import REQUIRED_COMPARISON_FIELDS, clean, numeric_diff
from .file_io import ANALYSIS_EIS, guess_cell_id, guess_time_point

if TYPE_CHECKING:
    from .eis_timeseries import EISTimeSeriesCluster


EIS_SUFFIXES = {".seo", ".sde", ".csv", ".xlsx", ".xls"}
SCHEMA_VERSION = "eis_matching_v1"


@dataclass(frozen=True)
class EISFileInventory:
    source_path: str
    relative_path: str
    source_name: str
    extension: str
    folder_date: str
    time_point: str
    is_time_series: bool
    cell_key: str
    group_key: str
    parser_type: str
    material_signature: str


@dataclass(frozen=True)
class EISConditionMatch:
    source_path: str
    relative_path: str
    is_time_series: bool
    file_group_key: str
    time_point: str
    status: str
    score: int
    margin: int
    condition_key: str = ""
    condition_sample: str = ""
    condition_date: str = ""
    date_delta_days: int | None = None
    overlap_tokens: str = ""
    conflict_tokens: str = ""
    candidate_summary: str = ""
    candidate_date_deltas: str = ""
    candidate_options: str = ""
    reason: str = ""


@dataclass(frozen=True)
class EISComparisonCluster:
    cluster_id: str
    electrolyte: str
    binder: str
    voltage_range: str
    ratio: str
    loading_min: float | None
    loading_max: float | None
    file_count: int
    condition_count: int
    source_paths: str
    condition_keys: str
    optional_source_paths: str = ""  # matching time-series cells (24hr reps), toggle-able in the viewer


@dataclass(frozen=True)
class EISComparisonPair:
    cluster_id: str
    left_source_path: str
    right_source_path: str
    left_condition_key: str
    right_condition_key: str
    areal_mass_density_diff: float | None
    comparison_grade: str
    reason: str


@dataclass(frozen=True)
class EISMatchReport:
    schema_version: str
    created_at: str
    source_root: str
    condition_count: int
    source_count: int
    status_counts: dict[str, int]
    class_counts: dict[str, int]
    inventory: list[EISFileInventory]
    matches: list[EISConditionMatch]
    time_series_groups: list["EISTimeSeriesCluster"]
    comparison_clusters: list[EISComparisonCluster]
    comparison_pairs: list[EISComparisonPair]


def collect_eis_inventory(source_paths: list[Path], source_root: Path | None = None) -> list[EISFileInventory]:
    root = source_root.resolve() if source_root else common_root(source_paths)
    return [inventory_for_path(path, root) for path in sorted(source_paths)]


def inventory_for_path(path: Path, source_root: Path) -> EISFileInventory:
    relative = relative_path(path, source_root)
    time_point = guess_time_point(path.stem) or guess_time_point(relative)
    is_time_series = bool(time_point) or has_hr_token(relative)
    cell_key = eis_cell_key(path.stem)
    group_key = eis_group_key(path, source_root, is_time_series)
    signature = sorted(material_signature(" ".join([relative, path.stem, str(path.parent.name)])))
    return EISFileInventory(
        source_path=str(path),
        relative_path=relative,
        source_name=path.name,
        extension=path.suffix.lower(),
        folder_date=folder_date(relative),
        time_point=time_point,
        is_time_series=is_time_series,
        cell_key=cell_key,
        group_key=group_key,
        parser_type=path.suffix.lower().lstrip(".") or "unknown",
        material_signature=";".join(signature),
    )


def match_eis_files_to_conditions(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[EISFileInventory], list[EISConditionMatch]]:
    inventory = collect_eis_inventory(source_paths, source_root)
    condition_index = build_condition_index(conditions)
    matches = [match_inventory_item(item, condition_index, overrides or {}) for item in inventory]
    return inventory, matches


def build_eis_match_report(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> EISMatchReport:
    root = source_root.resolve() if source_root else common_root(source_paths)
    inventory, matches = match_eis_files_to_conditions(source_paths, conditions, root, overrides)
    from .eis_timeseries import build_time_series_clusters
    time_groups = build_time_series_clusters(matches, conditions)
    clusters, pairs = build_comparison_clusters(matches, time_groups, conditions)
    return EISMatchReport(
        schema_version=SCHEMA_VERSION,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        source_root=str(root),
        condition_count=len(conditions),
        source_count=len(source_paths),
        status_counts=dict(Counter(match.status for match in matches)),
        class_counts=dict(Counter("time_series" if item.is_time_series else "comparison" for item in inventory)),
        inventory=inventory,
        matches=matches,
        time_series_groups=time_groups,
        comparison_clusters=clusters,
        comparison_pairs=pairs,
    )


def build_condition_index(conditions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    index = []
    for key, condition in conditions.items():
        texts = condition_texts(key, condition)
        signature = material_signature(" ".join(texts))
        index.append(
            {
                "key": key,
                "condition": condition,
                "date": compact_date(condition.get("date")),
                "texts": texts,
                "normalized": [normalize_text(text) for text in texts],
                "compact": [compact_text(text) for text in texts],
                "tokens": set().union(*(match_tokens(text) for text in texts)),
                "signature": signature,
            }
        )
    return index


def match_inventory_item(
    item: EISFileInventory,
    condition_index: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> EISConditionMatch:
    scored = []
    file_text = " ".join([item.relative_path, item.cell_key, item.group_key])
    file_tokens = match_tokens(file_text)
    file_signature = set(item.material_signature.split(";")) if item.material_signature else set()
    file_compact = compact_text(file_text)
    for entry in condition_index:
        candidate = score_condition_candidate(item, file_compact, file_tokens, file_signature, entry)
        if candidate:
            scored.append(candidate)
    scored.sort(key=lambda row: row["score"], reverse=True)
    manual = manual_override_match(item, scored, condition_index, overrides or {})
    if manual:
        return manual
    if not scored:
        return EISConditionMatch(
            source_path=item.source_path,
            relative_path=item.relative_path,
            is_time_series=item.is_time_series,
            file_group_key=item.group_key,
            time_point=item.time_point,
            status="unmatched",
            score=0,
            margin=0,
            reason="No condition row passed material/date guards.",
        )

    best = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0
    margin = best["score"] - second_score
    status = match_status(best["score"], margin, best["conflicts"])
    condition = best["condition"]
    return EISConditionMatch(
        source_path=item.source_path,
        relative_path=item.relative_path,
        is_time_series=item.is_time_series,
        file_group_key=item.group_key,
        time_point=item.time_point,
        status=status,
        score=best["score"],
        margin=margin,
        condition_key=str(best["key"]),
        condition_sample=str(condition.get("sample") or best["key"]),
        condition_date=compact_date(condition.get("date")),
        date_delta_days=best["date_delta"] if best["date_delta"] < 9999 else None,
        overlap_tokens=";".join(sorted(best["overlap"])),
        conflict_tokens=";".join(best["conflicts"]),
        candidate_summary=candidate_summary(scored),
        candidate_date_deltas=candidate_date_deltas(scored),
        candidate_options=candidate_options_json(scored),
        reason=best["reason"],
    )


def manual_override_match(
    item: EISFileInventory,
    scored: list[dict[str, Any]],
    condition_index: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> EISConditionMatch | None:
    override = overrides.get(item.relative_path) or overrides.get(item.source_path)
    if not override:
        return None
    condition_key = str(override.get("condition_key") or "")
    entry = next((row for row in condition_index if str(row["key"]) == condition_key), None)
    if entry is None:
        return None
    condition = entry["condition"]
    candidate = next((row for row in scored if str(row["key"]) == condition_key), None)
    score = int(candidate["score"]) if candidate else 0
    date_delta = candidate["date_delta"] if candidate else date_delta_days(item.folder_date, entry["date"])
    overlap = sorted(candidate["overlap"]) if candidate else []
    conflicts = candidate["conflicts"] if candidate else []
    return EISConditionMatch(
        source_path=item.source_path,
        relative_path=item.relative_path,
        is_time_series=item.is_time_series,
        file_group_key=item.group_key,
        time_point=item.time_point,
        status="manual",
        score=score,
        margin=999,
        condition_key=condition_key,
        condition_sample=str(condition.get("sample") or condition_key),
        condition_date=compact_date(condition.get("date")),
        date_delta_days=date_delta if date_delta < 9999 else None,
        overlap_tokens=";".join(overlap),
        conflict_tokens=";".join(conflicts),
        candidate_summary=candidate_summary(scored),
        candidate_date_deltas=candidate_date_deltas(scored),
        candidate_options=candidate_options_json(scored),
        reason=f"사용자가 실험일지 행 {condition.get('_source_row_number') or '?'}로 수동 확정했습니다.",
    )


def score_condition_candidate(
    item: EISFileInventory,
    file_compact: str,
    file_tokens: set[str],
    file_signature: set[str],
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    condition_signature: set[str] = entry["signature"]
    conflicts = material_conflicts(file_signature, condition_signature)
    if conflicts:
        return None

    overlap = file_signature & condition_signature
    token_overlap = file_tokens & entry["tokens"]
    date_delta = date_delta_days(item.folder_date, entry["date"])
    exact_score = 0
    for compact in entry["compact"]:
        if not compact:
            continue
        if compact == file_compact:
            exact_score = max(exact_score, 60)
        elif len(compact) >= 5 and compact in file_compact:
            exact_score = max(exact_score, 42 + min(len(compact) // 4, 15))
        elif len(file_compact) >= 5 and file_compact in compact:
            exact_score = max(exact_score, 36 + min(len(file_compact) // 4, 15))

    weighted_overlap = sum(token_weight(token) for token in overlap)
    token_score = min(28, len(token_overlap) * 3)
    date_score = score_date_delta(date_delta)
    score = exact_score + weighted_overlap + token_score + date_score
    if not overlap and exact_score < 42:
        return None
    if score < 32:
        return None
    reason = f"overlap={','.join(sorted(overlap)) or '-'}; date_delta={date_delta if date_delta < 9999 else 'unknown'}"
    return {
        "key": entry["key"],
        "condition": entry["condition"],
        "score": int(score),
        "date_delta": date_delta,
        "overlap": overlap,
        "conflicts": conflicts,
        "reason": reason,
    }


def match_status(score: int, margin: int, conflicts: list[str]) -> str:
    if conflicts:
        return "blocked"
    if score >= 74 and margin >= 10:
        return "verified"
    if score >= 58 and margin < 10:
        return "ambiguous"
    if score >= 52:
        return "review"
    return "unmatched"


def candidate_summary(scored: list[dict[str, Any]], *, max_rows: int = 8) -> str:
    return "\n".join(candidate_summary_parts(scored, max_rows=max_rows))


def candidate_date_deltas(scored: list[dict[str, Any]], *, max_rows: int = 8) -> str:
    parts = []
    for row in close_candidates(scored, max_rows=max_rows):
        condition = row["condition"]
        row_number = condition.get("_source_row_number") or "?"
        date_delta = row["date_delta"]
        delta_label = "-" if date_delta >= 9999 else f"{date_delta}일"
        parts.append(f"행 {row_number}: {delta_label}")
    return "\n".join(parts)


def candidate_options_json(scored: list[dict[str, Any]], *, max_rows: int = 8) -> str:
    options = []
    for row in close_candidates(scored, max_rows=max_rows):
        condition = row["condition"]
        date_delta = row["date_delta"]
        options.append(
            {
                "condition_key": str(row["key"]),
                "journal_row": condition.get("_source_row_number") or "",
                "sample": str(condition.get("sample") or condition.get("cell_id") or row["key"]),
                "date": compact_date(condition.get("date")) or "",
                "date_delta_days": None if date_delta >= 9999 else date_delta,
                "score": int(row["score"]),
                "overlap_tokens": ";".join(sorted(row["overlap"])),
            }
        )
    return json.dumps(options, ensure_ascii=False)


def candidate_summary_parts(scored: list[dict[str, Any]], *, max_rows: int = 8) -> list[str]:
    parts = []
    for row in close_candidates(scored, max_rows=max_rows):
        condition = row["condition"]
        row_number = condition.get("_source_row_number") or "?"
        sample = condition.get("sample") or condition.get("cell_id") or row["key"]
        date = compact_date(condition.get("date")) or "-"
        parts.append(f"행 {row_number}, {sample}, {date}")
    return parts


def close_candidates(scored: list[dict[str, Any]], *, max_rows: int = 8) -> list[dict[str, Any]]:
    if not scored:
        return []
    best_score = int(scored[0]["score"])
    return [row for row in scored if int(row["score"]) >= best_score - 10][:max_rows]


@dataclass(frozen=True)
class _ComparisonCell:
    condition_key: str
    relative_path: str


def _pick_endpoint_member(member_paths: str, match_by_path: dict[str, "EISConditionMatch"], target_hr: int = 24) -> str:
    """Pick the time-series member file closest to `target_hr` (default 24hr endpoint)."""
    from .eis_timeseries import hr_num

    members = [path for path in member_paths.split(";") if path]
    best, best_dist = "", None
    for path in members:
        match = match_by_path.get(path)
        hours = hr_num(match.time_point) if match else None
        if hours is None:
            continue
        dist = abs(target_hr - hours)
        if best_dist is None or dist < best_dist:
            best, best_dist = path, dist
    return best or (members[0] if members else "")


def _primary_cells(
    matches: list[EISConditionMatch],
    conditions: dict[str, dict[str, Any]],
) -> list[_ComparisonCell]:
    """Non-time-series comparison cells — one (verified-preferred) file per journal row.
    These DEFINE the comparison clusters; time-series data is attached separately."""
    comparison: dict[str, list[EISConditionMatch]] = defaultdict(list)
    for match in matches:
        if match.is_time_series:
            continue
        if match.status in {"verified", "review", "ambiguous", "manual"} and match.condition_key in conditions:
            comparison[match.condition_key].append(match)
    cells: list[_ComparisonCell] = []
    for key, group in comparison.items():
        group.sort(key=lambda m: (0 if m.status in ("verified", "manual") else 1, m.relative_path))
        cells.append(_ComparisonCell(condition_key=key, relative_path=group[0].relative_path))
    return cells


def _time_series_cells(
    ts_clusters: list,
    matches: list[EISConditionMatch],
    conditions: dict[str, dict[str, Any]],
) -> list[_ComparisonCell]:
    """Time-series cells represented by their 24hr endpoint file, keyed by journal row."""
    match_by_path = {match.relative_path: match for match in matches}
    reps: dict[str, str] = {}
    for ts in sorted(ts_clusters, key=lambda t: (0 if t.match_status == "verified" else 1, t.cluster_id)):
        if ts.condition_key and ts.condition_key in conditions:
            rep = _pick_endpoint_member(ts.member_paths, match_by_path, 24)
            if rep:
                reps.setdefault(ts.condition_key, rep)
    return [_ComparisonCell(condition_key=key, relative_path=path) for key, path in reps.items()]


def _attach_time_series(
    component: list[_ComparisonCell],
    required_key: tuple,
    ts_cells: list[_ComparisonCell],
    conditions: dict[str, dict[str, Any]],
) -> list[str]:
    """Time-series cells whose conditions match this cluster (same backbone + loading
    within 1.0 mg/cm2 of a member) — offered as optional, toggle-able overlay members."""
    member_loads = [to_float(conditions[cell.condition_key].get("areal_mass_density")) for cell in component]
    member_loads = [value for value in member_loads if value is not None]
    primary_keys = {cell.condition_key for cell in component}
    attached: list[str] = []
    for cell in ts_cells:
        if cell.condition_key in primary_keys:
            continue
        condition = conditions.get(cell.condition_key, {})
        if tuple(clean(condition.get(field)) for field in REQUIRED_COMPARISON_FIELDS) != required_key:
            continue
        load = to_float(condition.get("areal_mass_density"))
        if load is None or not member_loads:
            continue
        if min(abs(load - other) for other in member_loads) <= 1.0:
            attached.append(cell.relative_path)
    return attached


def build_comparison_clusters(
    matches: list[EISConditionMatch],
    ts_clusters: list,
    conditions: dict[str, dict[str, Any]],
) -> tuple[list[EISComparisonCluster], list[EISComparisonPair]]:
    usable = _primary_cells(matches, conditions)
    ts_cells = _time_series_cells(ts_clusters, matches, conditions)
    buckets: dict[tuple[str, str, str, str], list[EISConditionMatch]] = defaultdict(list)
    for match in usable:
        condition = conditions[match.condition_key]
        if any(not clean(condition.get(field)) for field in REQUIRED_COMPARISON_FIELDS):
            continue
        key = tuple(clean(condition.get(field)) for field in REQUIRED_COMPARISON_FIELDS)
        buckets[key].append(match)

    clusters: list[EISComparisonCluster] = []
    pairs: list[EISComparisonPair] = []
    cluster_idx = 1
    for required_key, group in sorted(buckets.items(), key=lambda row: (-len(row[1]), row[0])):
        for component in loading_components(group, conditions):
            cluster_id = f"C{cluster_idx:03d}"
            cluster_idx += 1
            loads = [to_float(conditions[match.condition_key].get("areal_mass_density")) for match in component]
            valid_loads = [value for value in loads if value is not None]
            optional = _attach_time_series(component, required_key, ts_cells, conditions)
            clusters.append(
                EISComparisonCluster(
                    cluster_id=cluster_id,
                    electrolyte=required_key[0],
                    binder=required_key[1],
                    voltage_range=required_key[2],
                    ratio=required_key[3],
                    loading_min=min(valid_loads) if valid_loads else None,
                    loading_max=max(valid_loads) if valid_loads else None,
                    file_count=len(component),
                    condition_count=len({match.condition_key for match in component}),
                    source_paths=";".join(match.relative_path for match in component),
                    condition_keys=";".join(sorted({match.condition_key for match in component})),
                    optional_source_paths=";".join(sorted(set(optional))),
                )
            )
            for left, right in combinations(component, 2):
                pair = comparison_pair(cluster_id, left, right, conditions)
                if pair:
                    pairs.append(pair)
    return clusters, pairs


def loading_components(group: list[EISConditionMatch], conditions: dict[str, dict[str, Any]]) -> list[list[EISConditionMatch]]:
    graph: dict[int, set[int]] = {idx: set() for idx in range(len(group))}
    for i, j in combinations(range(len(group)), 2):
        diff = numeric_diff(
            conditions[group[i].condition_key].get("areal_mass_density"),
            conditions[group[j].condition_key].get("areal_mass_density"),
        )
        if diff is not None and diff <= 1.0:
            graph[i].add(j)
            graph[j].add(i)
    seen: set[int] = set()
    components = []
    for idx in range(len(group)):
        if idx in seen:
            continue
        stack = [idx]
        seen.add(idx)
        component = []
        while stack:
            current = stack.pop()
            component.append(group[current])
            for nxt in graph[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if len(component) >= 2:
            components.append(sorted(component, key=lambda match: match.relative_path))
    return components


def comparison_pair(
    cluster_id: str,
    left: EISConditionMatch,
    right: EISConditionMatch,
    conditions: dict[str, dict[str, Any]],
) -> EISComparisonPair | None:
    diff = numeric_diff(
        conditions[left.condition_key].get("areal_mass_density"),
        conditions[right.condition_key].get("areal_mass_density"),
    )
    if diff is None:
        return None
    if diff <= 0.5:
        grade = "A"
        reason = f"loading diff {diff:.2f} mg/cm2"
    elif diff <= 1.0:
        grade = "B"
        reason = f"loading diff {diff:.2f} mg/cm2"
    else:
        return None
    return EISComparisonPair(
        cluster_id=cluster_id,
        left_source_path=left.relative_path,
        right_source_path=right.relative_path,
        left_condition_key=left.condition_key,
        right_condition_key=right.condition_key,
        areal_mass_density_diff=diff,
        comparison_grade=grade,
        reason=reason,
    )


def write_eis_match_outputs(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    output_dir: Path,
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> EISMatchReport:
    report = build_eis_match_report(source_paths, conditions, source_root, overrides)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_dataclass_rows(report.inventory, output_dir / "eis_file_inventory.csv")
    write_dataclass_rows(report.matches, output_dir / "eis_condition_matches.csv")
    write_dataclass_rows(report.time_series_groups, output_dir / "eis_time_series_groups.csv")
    write_dataclass_rows(report.comparison_clusters, output_dir / "eis_comparison_clusters.csv")
    write_dataclass_rows(report.comparison_pairs, output_dir / "eis_comparison_pairs.csv")
    payload = asdict(report)
    (output_dir / "eis_match_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_dataclass_rows(rows: list[Any], path: Path) -> None:
    data = [asdict(row) for row in rows]
    headers = list(data[0].keys()) if data else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if headers:
            writer.writeheader()
            writer.writerows(data)


def common_root(paths: list[Path]) -> Path:
    if not paths:
        return Path(".").resolve()
    try:
        if len(paths) == 1:
            return paths[0].resolve().parent
        return Path(os.path.commonpath([str(path.resolve().parent) for path in paths]))
    except Exception:
        return paths[0].resolve().parent


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def has_hr_token(value: str) -> bool:
    return bool(re.search(r"(?i)(?:^|[^a-z0-9])\d+\s*hr(?:$|[^a-z0-9])", value))


def folder_date(relative: str) -> str:
    match = re.search(r"(?:^|/)(\d{6})(?:/|$)", relative)
    return match.group(1) if match else ""


def eis_cell_key(stem: str) -> str:
    cleaned = guess_cell_id(stem, ANALYSIS_EIS)
    cleaned = re.sub(r"(?i)_?nxt_day_\d+", "", cleaned)
    cleaned = re.sub(r"(?i)_?(?:next|nxt)_day_\d+", "", cleaned)
    cleaned = re.sub(r"(?i)_?again(?:_\d+)?$", "", cleaned)
    cleaned = re.sub(r"(?i)_?(again|nxt_day|next_day|data|2nd|measurement|after|rest|cycle)$", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown_cell"


def eis_group_key(path: Path, source_root: Path, is_time_series: bool) -> str:
    key = eis_cell_key(path.stem)
    relative = relative_path(path, source_root)
    if is_time_series:
        key = strip_channel_suffix(key)
        return normalize_group_key(" ".join([folder_date(relative), key]))
    return normalize_group_key(" ".join([folder_date(relative), key]))


def strip_channel_suffix(value: str) -> str:
    return re.sub(r"_(?:0?\d)$", "", value).strip("_")


def normalize_group_key(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"\b\d+\s*hr\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "unknown"


def condition_texts(key: str, condition: dict[str, Any]) -> list[str]:
    values = [key]
    for field in (
        "cell_id",
        "sample",
        "raw_sample_name",
        "display_label",
        "sample_batch_id",
        "sample_group",
        "material_family",
        "treatment",
        "note",
        "additional_note",
    ):
        value = condition.get(field)
        if value not in (None, ""):
            values.append(str(value))
    return values


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("activated", "act")
    text = text.replace("1.5_act", "1.5act")
    text = re.sub(r"\b1\.5[\s_]+act\b", "1.5act", text)
    text = re.sub(r"(?i)(capacity|cycle|eis|profile|rate|per)", " ", text)
    text = re.sub(r"[^0-9a-z가-힣.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣.]+", "", normalize_text(value))


def match_tokens(value: Any) -> set[str]:
    text = normalize_text(value)
    tokens = set(re.findall(r"1\.5|\d+t\d*t?|\d+(?:\.\d+)?|[a-z]+|[가-힣]+", text))
    return {token for token in tokens if token not in {"seo", "sde", "xlsx", "xls", "csv", "hr", "data"}}


def material_signature(value: Any) -> set[str]:
    text = normalize_text(value)
    tokens = match_tokens(text)
    signature: set[str] = set()
    if ("1.5" in tokens and "act" in tokens) or "1.5act" in compact_text(text):
        signature.add("1.5act")
    for token in ("act", "pure", "pc", "dl", "gf"):
        if token in tokens:
            signature.add(token)
    if "sbr" in tokens:
        signature.add("sbr")
    if "no" in tokens and "sbr" in tokens:
        signature.add("no_sbr")
    signature.update(re.findall(r"\d+t(?:\d+t)?", compact_text(text)))
    for token in ("19", "37", "55", "73", "91", "900", "964", "811", "9532", "9055"):
        if token in tokens:
            signature.add(token)
    return signature


def material_conflicts(file_signature: set[str], condition_signature: set[str]) -> list[str]:
    conflicts = []
    for token in ("1.5act", "pure", "pc", "dl"):
        if token in file_signature and token not in condition_signature:
            conflicts.append(f"{token}_missing")
    file_nums = file_signature & {"19", "37", "55", "73", "91", "900", "964", "811", "9532", "9055"}
    condition_nums = condition_signature & {"19", "37", "55", "73", "91", "900", "964", "811", "9532", "9055"}
    if file_nums and condition_nums and not file_nums & condition_nums:
        conflicts.append("number_conflict")
    file_t = {token for token in file_signature if re.fullmatch(r"\d+t(?:\d+t)?", token)}
    condition_t = {token for token in condition_signature if re.fullmatch(r"\d+t(?:\d+t)?", token)}
    if file_t and condition_t and not file_t & condition_t:
        conflicts.append("thickness_conflict")
    if "no_sbr" in file_signature and "no_sbr" not in condition_signature:
        conflicts.append("no_sbr_missing")
    return conflicts


def token_weight(token: str) -> int:
    if token in {"1.5act", "pure", "pc", "dl", "no_sbr"}:
        return 18
    if re.fullmatch(r"\d+t(?:\d+t)?", token):
        return 16
    if token in {"19", "37", "55", "73", "91", "900", "964", "811", "9532", "9055"}:
        return 14
    if token in {"gf", "act"}:
        return 8
    return 5


def compact_date(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{6}", text):
        return text
    if re.fullmatch(r"\d{8}", text):
        return text[2:]
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        return ""
    return str(number)[-6:] if number else ""


def date_delta_days(left: str, right: str) -> int:
    if not left or not right:
        return 9999
    try:
        l_date = datetime.strptime(left, "%y%m%d").date()
        r_date = datetime.strptime(right, "%y%m%d").date()
    except ValueError:
        return 9999
    return abs((l_date - r_date).days)


def score_date_delta(delta: int) -> int:
    if delta == 0:
        return 24
    if delta == 1:
        return 18
    if delta <= 7:
        return 8
    if delta <= 14:
        return 2
    if delta == 9999:
        return 0
    return -14


def natural_time(value: str) -> str:
    return re.sub(r"\s+", "", value.lower())


def time_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"\d+", value)
    return (int(match.group(0)) if match else 999999, value)


def first_value(values: Any) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
