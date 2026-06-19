from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .eis_matching import (
    candidate_date_deltas,
    candidate_options_json,
    candidate_summary,
    compact_date,
    compact_text,
    date_delta_days,
    folder_date,
    match_tokens,
    material_conflicts,
    material_signature,
    normalize_text,
    score_date_delta,
    token_weight,
)
from .file_io import ANALYSIS_CAPACITY, guess_cell_id


CAPACITY_SUFFIXES = {".csv", ".wrd", ".xlsx", ".xls"}
SCHEMA_VERSION = "capacity_matching_v1"
CAPACITY_PROTOCOL_TYPE_1 = "type_1_0p1c_continuous"
CAPACITY_PROTOCOL_TYPE_2 = "type_2_0p5c_after_stabilization"
CAPACITY_PROTOCOL_TYPE_3 = "type_3_rate_performance"
CAPACITY_PROTOCOL_ORDER = [CAPACITY_PROTOCOL_TYPE_1, CAPACITY_PROTOCOL_TYPE_2, CAPACITY_PROTOCOL_TYPE_3]
CAPACITY_PROTOCOL_CLUSTER_IDS = {
    CAPACITY_PROTOCOL_TYPE_1: "P001",
    CAPACITY_PROTOCOL_TYPE_2: "P002",
    CAPACITY_PROTOCOL_TYPE_3: "P003",
}
CAPACITY_PROTOCOL_LABELS = {
    CAPACITY_PROTOCOL_TYPE_1: "1번 · 0.1C continuous",
    CAPACITY_PROTOCOL_TYPE_2: "2번 · 0.1C 안정화 후 0.5C",
    CAPACITY_PROTOCOL_TYPE_3: "3번 · rate performance",
}


@dataclass(frozen=True)
class CapacityFileInventory:
    source_path: str
    relative_path: str
    source_name: str
    extension: str
    folder_date: str
    row_prefix: int | None
    cell_key: str
    parser_type: str
    material_signature: str


@dataclass(frozen=True)
class CapacityConditionMatch:
    source_path: str
    relative_path: str
    row_prefix: int | None
    file_group_key: str
    status: str
    score: int
    margin: int
    condition_key: str = ""
    condition_sample: str = ""
    condition_date: str = ""
    journal_row: int | None = None
    date_delta_days: int | None = None
    overlap_tokens: str = ""
    conflict_tokens: str = ""
    candidate_summary: str = ""
    candidate_date_deltas: str = ""
    candidate_options: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CapacityMatchReport:
    schema_version: str
    created_at: str
    source_root: str
    condition_count: int
    source_count: int
    status_counts: dict[str, int]
    inventory: list[CapacityFileInventory]
    matches: list[CapacityConditionMatch]


@dataclass(frozen=True)
class CapacityProtocolClassification:
    protocol_type: str
    protocol_label: str
    cluster_id: str
    rule_source: str
    bend_count: int
    reason: str


def classify_capacity_protocol(
    source_name: str,
    points: list[tuple[float, float]] | None = None,
) -> CapacityProtocolClassification:
    filename_type = capacity_protocol_from_filename(source_name)
    if filename_type:
        return capacity_protocol_classification(
            filename_type,
            "filename",
            count_capacity_bends(points or []),
            filename_protocol_reason(source_name, filename_type),
        )
    bend_count = count_capacity_bends(points or [])
    if bend_count >= 3:
        protocol_type = CAPACITY_PROTOCOL_TYPE_3
    elif bend_count >= 1:
        protocol_type = CAPACITY_PROTOCOL_TYPE_2
    else:
        protocol_type = CAPACITY_PROTOCOL_TYPE_1
    return capacity_protocol_classification(
        protocol_type,
        "shape",
        bend_count,
        f"파일명 키워드가 없어 capacity 곡선의 큰 꺾임 {bend_count}개로 판정했습니다.",
    )


def capacity_protocol_classification(
    protocol_type: str,
    rule_source: str,
    bend_count: int,
    reason: str,
) -> CapacityProtocolClassification:
    return CapacityProtocolClassification(
        protocol_type=protocol_type,
        protocol_label=CAPACITY_PROTOCOL_LABELS[protocol_type],
        cluster_id=CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type],
        rule_source=rule_source,
        bend_count=bend_count,
        reason=reason,
    )


def capacity_protocol_from_filename(source_name: str) -> str | None:
    text = normalize_capacity_protocol_text(source_name)
    if re.search(r"\brate\s*per\b", text):
        return CAPACITY_PROTOCOL_TYPE_3
    if re.search(r"(?<!\d)0[\s._-]*5\s*c\b", text) or re.search(r"(?<!\d)0p5\s*c\b", text):
        return CAPACITY_PROTOCOL_TYPE_2
    if re.search(r"(?<!\d)0[\s._-]*1\s*c\b", text) or re.search(r"(?<!\d)0p1\s*c\b", text):
        return CAPACITY_PROTOCOL_TYPE_1
    return None


def normalize_capacity_protocol_text(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", str(value or "").lower()).strip()


def filename_protocol_reason(source_name: str, protocol_type: str) -> str:
    if protocol_type == CAPACITY_PROTOCOL_TYPE_3:
        return f"파일명 '{source_name}'에 rate per 키워드가 있어 3번 유형으로 분류했습니다."
    if protocol_type == CAPACITY_PROTOCOL_TYPE_2:
        return f"파일명 '{source_name}'에 0.5C 키워드가 있어 2번 유형으로 분류했습니다."
    return f"파일명 '{source_name}'에 0.1C 키워드가 있어 1번 유형으로 분류했습니다."


def count_capacity_bends(points: list[tuple[float, float]]) -> int:
    clean_points = sorted(
        [(float(x), float(y)) for x, y in points if math.isfinite(float(x)) and math.isfinite(float(y))],
        key=lambda point: point[0],
    )
    if len(clean_points) < 4:
        return 0
    values = [point[1] for point in clean_points]
    positive = [value for value in values if value > 0]
    if not positive:
        return 0
    baseline_values = positive[: min(5, len(positive))]
    baseline = sorted(baseline_values)[len(baseline_values) // 2]
    threshold = max(20.0, baseline * 0.12)
    bend_count = 0
    previous_large = False
    for left, right in zip(values, values[1:]):
        delta = right - left
        is_large = abs(delta) > threshold
        if is_large and not previous_large:
            bend_count += 1
        previous_large = is_large
    return bend_count


def collect_capacity_inventory(source_paths: list[Path], source_root: Path | None = None) -> list[CapacityFileInventory]:
    root = source_root.resolve() if source_root else common_root(source_paths)
    return [inventory_for_path(path, root) for path in sorted(source_paths)]


def inventory_for_path(path: Path, source_root: Path) -> CapacityFileInventory:
    relative = relative_path(path, source_root)
    stem = path.stem
    signature = sorted(material_signature(" ".join([relative, stem, str(path.parent.name)])))
    return CapacityFileInventory(
        source_path=str(path),
        relative_path=relative,
        source_name=path.name,
        extension=path.suffix.lower(),
        folder_date=folder_date(relative),
        row_prefix=row_prefix(stem),
        cell_key=capacity_cell_key(stem),
        parser_type=path.suffix.lower().lstrip(".") or "unknown",
        material_signature=";".join(signature),
    )


def build_capacity_match_report(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> CapacityMatchReport:
    root = source_root.resolve() if source_root else common_root(source_paths)
    inventory, matches = match_capacity_files_to_conditions(source_paths, conditions, root, overrides)
    return CapacityMatchReport(
        schema_version=SCHEMA_VERSION,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        source_root=str(root),
        condition_count=len(conditions),
        source_count=len(source_paths),
        status_counts=dict(Counter(match.status for match in matches)),
        inventory=inventory,
        matches=matches,
    )


def match_capacity_files_to_conditions(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[CapacityFileInventory], list[CapacityConditionMatch]]:
    inventory = collect_capacity_inventory(source_paths, source_root)
    condition_index = build_condition_index(conditions)
    matches = [match_inventory_item(item, condition_index, overrides or {}) for item in inventory]
    return inventory, matches


def build_condition_index(conditions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    index = []
    for key, condition in conditions.items():
        texts = condition_texts(key, condition)
        index.append(
            {
                "key": key,
                "condition": condition,
                "journal_row": int(condition.get("_source_row_number") or 0) or None,
                "date": compact_date(condition.get("date")),
                "texts": texts,
                "compact": [compact_text(text) for text in texts],
                "tokens": set().union(*(match_tokens(text) for text in texts)),
                "signature": material_signature(" ".join(texts)),
            }
        )
    return index


def match_inventory_item(
    item: CapacityFileInventory,
    condition_index: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> CapacityConditionMatch:
    scored = []
    file_text = " ".join([item.relative_path, item.cell_key])
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
        return CapacityConditionMatch(
            source_path=item.source_path,
            relative_path=item.relative_path,
            row_prefix=item.row_prefix,
            file_group_key=item.cell_key,
            status="unmatched",
            score=0,
            margin=0,
            reason="No condition row matched row prefix or filename tokens.",
        )
    best = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0
    margin = best["score"] - second_score
    status = capacity_match_status(best["score"], margin, best["conflicts"], best["row_exact"])
    condition = best["condition"]
    return CapacityConditionMatch(
        source_path=item.source_path,
        relative_path=item.relative_path,
        row_prefix=item.row_prefix,
        file_group_key=item.cell_key,
        status=status,
        score=best["score"],
        margin=margin,
        condition_key=str(best["key"]),
        condition_sample=str(condition.get("sample") or best["key"]),
        condition_date=compact_date(condition.get("date")),
        journal_row=condition.get("_source_row_number"),
        date_delta_days=best["date_delta"] if best["date_delta"] < 9999 else None,
        overlap_tokens=";".join(sorted(best["overlap"])),
        conflict_tokens=";".join(best["conflicts"]),
        candidate_summary=candidate_summary(scored),
        candidate_date_deltas=candidate_date_deltas(scored),
        candidate_options=candidate_options_json(scored),
        reason=best["reason"],
    )


def manual_override_match(
    item: CapacityFileInventory,
    scored: list[dict[str, Any]],
    condition_index: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> CapacityConditionMatch | None:
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
    return CapacityConditionMatch(
        source_path=item.source_path,
        relative_path=item.relative_path,
        row_prefix=item.row_prefix,
        file_group_key=item.cell_key,
        status="manual",
        score=score,
        margin=999,
        condition_key=condition_key,
        condition_sample=str(condition.get("sample") or condition_key),
        condition_date=compact_date(condition.get("date")),
        journal_row=condition.get("_source_row_number"),
        date_delta_days=date_delta if date_delta < 9999 else None,
        overlap_tokens=";".join(overlap),
        conflict_tokens=";".join(conflicts),
        candidate_summary=candidate_summary(scored),
        candidate_date_deltas=candidate_date_deltas(scored),
        candidate_options=candidate_options_json(scored),
        reason=f"사용자가 실험일지 행 {condition.get('_source_row_number') or '?'}로 수동 확정했습니다.",
    )


def score_condition_candidate(
    item: CapacityFileInventory,
    file_compact: str,
    file_tokens: set[str],
    file_signature: set[str],
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    condition_signature: set[str] = entry["signature"]
    conflicts = material_conflicts(file_signature, condition_signature)
    row_exact = item.row_prefix is not None and item.row_prefix == entry["journal_row"]
    if conflicts and not row_exact:
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
    row_score = 120 if row_exact else 0
    weighted_overlap = sum(token_weight(token) for token in overlap)
    token_score = min(28, len(token_overlap) * 3)
    date_score = score_date_delta(date_delta)
    score = row_score + exact_score + weighted_overlap + token_score + date_score
    if not row_exact and not overlap and exact_score < 42:
        return None
    if score < 32:
        return None
    reason = (
        f"row_prefix={item.row_prefix or '-'}; journal_row={entry['journal_row'] or '-'}; "
        f"overlap={','.join(sorted(overlap)) or '-'}; date_delta={date_delta if date_delta < 9999 else 'unknown'}"
    )
    return {
        "key": entry["key"],
        "condition": entry["condition"],
        "score": int(score),
        "date_delta": date_delta,
        "overlap": overlap,
        "conflicts": conflicts,
        "reason": reason,
        "row_exact": row_exact,
    }


def capacity_match_status(score: int, margin: int, conflicts: list[str], row_exact: bool) -> str:
    if row_exact:
        return "verified"
    if conflicts:
        return "blocked"
    if score >= 74 and margin >= 10:
        return "verified"
    if score >= 58 and margin < 10:
        return "ambiguous"
    if score >= 52:
        return "review"
    return "unmatched"


def write_capacity_match_outputs(
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    output_dir: Path,
    source_root: Path | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> CapacityMatchReport:
    report = build_capacity_match_report(source_paths, conditions, source_root, overrides)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_dataclass_rows(report.inventory, output_dir / "capacity_file_inventory.csv")
    write_dataclass_rows(report.matches, output_dir / "capacity_condition_matches.csv")
    payload = asdict(report)
    (output_dir / "capacity_match_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_dataclass_rows(rows: list[Any], path: Path) -> None:
    data = [asdict(row) for row in rows]
    headers = list(data[0].keys()) if data else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if headers:
            writer.writeheader()
            writer.writerows(data)


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


def row_prefix(stem: str) -> int | None:
    match = re.match(r"\s*(\d+)(?:[_\-\s]|$)", stem)
    if not match:
        return None
    return int(match.group(1))


def capacity_cell_key(stem: str) -> str:
    cleaned = guess_cell_id(stem, ANALYSIS_CAPACITY)
    cleaned = re.sub(r"(?i)^\d+[_\-\s]+", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return normalize_text(cleaned) or "unknown_cell"


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
