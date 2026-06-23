from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from .file_io import (
    ANALYSIS_CAPACITY,
    ANALYSIS_EIS,
    ANALYSIS_RAMAN,
    ANALYSIS_SHEET,
    ANALYSIS_TGA,
    ANALYSIS_VOLTAGE,
    canonical_column,
    read_delimited,
    read_xlsx_optional,
)
from .models import AnalysisAvailability, AnalysisComparisonValidation, AnalysisFileRecord, ComparisonCandidate, MetricRecord, ParsedDataset


CONDITION_FIELDS = [
    "raw_sample_name",
    "sample",
    "canonical_cell_id",
    "display_label",
    "sample_batch_id",
    "batch",
    "cell_no",
    "date",
    "sample_group",
    "material_family",
    "treatment",
    "areal_mass_density",
    "electrode_density",
    "electrolyte",
    "binder",
    "voltage_range",
    "ratio",
    "note",
    "additional_note",
]

REQUIRED_COMPARISON_FIELDS = ("electrolyte", "binder", "voltage_range", "ratio")
CELL_LEVEL_ANALYSES = {ANALYSIS_CAPACITY, ANALYSIS_VOLTAGE, ANALYSIS_EIS}
ELECTRODE_LEVEL_ANALYSES = {ANALYSIS_SHEET}
MATERIAL_LEVEL_ANALYSES = {ANALYSIS_RAMAN, ANALYSIS_TGA}


def read_conditions(path: Path, sheet_name: str | None = None) -> dict[str, dict[str, Any]]:
    rows = read_xlsx_optional(path, sheet_name=sheet_name) if path.suffix.lower() in {".xlsx", ".xls"} else read_delimited(path)
    conditions = {}
    for row_idx, row in enumerate(rows, start=2):
        normalized = {condition_column(key): value for key, value in row.items()}
        cell_id = str(normalized.get("cell_id") or normalized.get("cell") or normalized.get("sample") or "").strip()
        if cell_id:
            normalized = normalize_condition_record(cell_id, normalized)
            normalized["_source_row_number"] = row_idx
            # Keep every journal row distinct. Replicate cells share a Sample name
            # (e.g. JYJ rows 447/448 both '1.5act 4T'); keying by Sample alone would
            # collapse them and break 1:1 file<->row matching. Suffix the key with the
            # row number on collision so capacity's row_exact (+120) bonus can resolve
            # each file to its own row. The display `sample`/`cell_id` stay unchanged.
            key = cell_id if cell_id not in conditions else f"{cell_id} #row{row_idx}"
            conditions[key] = normalized
    return conditions


def normalize_condition_record(cell_id: str, row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["cell_id"] = cell_id
    normalized.setdefault("raw_sample_name", cell_id)
    normalized.setdefault("sample", normalized.get("raw_sample_name") or cell_id)
    normalized.setdefault("canonical_cell_id", canonical_cell_id(cell_id))
    normalized.setdefault("display_label", normalized.get("sample") or cell_id)
    normalized.setdefault("sample_batch_id", sample_batch_id(normalized))
    return normalized


def condition_column(name: str) -> str:
    aliases = {
        "참고": "reference",
        "전해질": "electrolyte",
        "날짜": "date",
        "일자": "date",
        "측정일": "date",
        "종류": "cell_type",
        "cell": "cell_id",
        "cell_id": "cell_id",
        "cell_name": "cell_id",
        "cell_자리": "cell_position",
        "sample": "sample",
        "sample_name": "sample",
        "raw_sample_name": "raw_sample_name",
        "canonical_cell_id": "canonical_cell_id",
        "display_label": "display_label",
        "sample_batch_id": "sample_batch_id",
        "sample_group": "sample_group",
        "material_family": "material_family",
        "treatment": "treatment",
        "conductive_agent": "conductive_agent",
        "mass_loading": "areal_mass_density",
        "areal_loading": "areal_mass_density",
        "areal_mass_density_mg_c_2": "areal_mass_density",
        "areal_mass_density_mg_cm2": "areal_mass_density",
        "합제밀도_g_cm3": "electrode_density",
        "합제밀도": "electrode_density",
        "g_cm3": "electrode_density",
        "electrode_density_g_cm3": "electrode_density",
        "전극_g": "electrode_mass",
        "active_material_g": "active_material_g",
        "current_a": "current_a",
        "c_rate_1_h": "c_rate",
        "voltage": "voltage_range",
        "composition": "ratio",
        "memo": "note",
        "additional_memo": "additional_note",
        "additionnal_memo": "additional_note",
    }
    raw = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if raw in aliases:
        return aliases[raw]
    canonical = canonical_column(name)
    if canonical.startswith("areal_mass_density"):
        return "areal_mass_density"
    if canonical.startswith("voltage_range"):
        return "voltage_range"
    if canonical.startswith("binder"):
        return "binder"
    if canonical.startswith("ratio"):
        return "ratio"
    return aliases.get(canonical, canonical)


def compatibility_notes(cell_ids: list[str], conditions: dict[str, dict[str, Any]]) -> list[str]:
    notes = [candidate_note(candidate) for candidate in build_comparison_candidates(cell_ids, conditions)]
    matched = {cell_id: find_condition(cell_id, conditions) for cell_id in sorted(set(cell_ids))}
    missing = [cell_id for cell_id, condition in matched.items() if not condition]
    if missing and conditions:
        notes.append(f"조건표 매칭 실패: {len(missing)}개 셀 ({', '.join(missing[:5])}).")
    return notes


def build_analysis_file_records(
    datasets: list[ParsedDataset],
    conditions: dict[str, dict[str, Any]],
) -> list[AnalysisFileRecord]:
    records = []
    for idx, dataset in enumerate(datasets, start=1):
        meta = dataset.meta
        condition = find_condition(meta.cell_id, conditions)
        batch_id = condition.get("sample_batch_id") or sample_batch_id_from_text(meta.cell_id)
        records.append(
            AnalysisFileRecord(
                file_id=f"F{idx:04d}",
                cell_id=condition.get("cell_id", meta.cell_id) if condition else meta.cell_id,
                sample_batch_id=str(batch_id),
                analysis_type=meta.analysis_type,
                file_name=meta.original_filename,
                file_path=str(meta.path),
                time_point=meta.time_point,
                cycle_protocol=guess_cycle_protocol(meta.original_filename),
                upload_date=meta.date,
                parse_status=meta.parser_status,
                warning=meta.warning,
            )
        )
    return records


def build_analysis_availability(
    datasets: list[ParsedDataset],
    conditions: dict[str, dict[str, Any]],
) -> list[AnalysisAvailability]:
    analysis_files = build_analysis_file_records(datasets, conditions)
    cells: dict[str, dict[str, Any]] = {}
    for cell_id, condition in conditions.items():
        cells[cell_id] = {
            "cell_id": cell_id,
            "canonical_cell_id": condition.get("canonical_cell_id") or canonical_cell_id(cell_id),
            "display_label": condition.get("display_label") or condition.get("sample") or cell_id,
            "sample_batch_id": condition.get("sample_batch_id") or sample_batch_id(condition),
        }
    for file_record in analysis_files:
        cells.setdefault(
            file_record.cell_id,
            {
                "cell_id": file_record.cell_id,
                "canonical_cell_id": canonical_cell_id(file_record.cell_id),
                "display_label": file_record.cell_id,
                "sample_batch_id": file_record.sample_batch_id,
            },
        )
    by_cell: dict[str, list[AnalysisFileRecord]] = {cell_id: [] for cell_id in cells}
    by_batch: dict[str, list[AnalysisFileRecord]] = {}
    for file_record in analysis_files:
        by_cell.setdefault(file_record.cell_id, []).append(file_record)
        by_batch.setdefault(file_record.sample_batch_id, []).append(file_record)
    availability = []
    for cell_id, base in sorted(cells.items()):
        cell_files = by_cell.get(cell_id, [])
        batch_files = by_batch.get(str(base["sample_batch_id"]), [])
        cell_types = {record.analysis_type for record in cell_files}
        batch_types = {record.analysis_type for record in batch_files}
        eis_time_points = {record.time_point for record in cell_files if record.analysis_type == ANALYSIS_EIS and record.time_point}
        missing = missing_cell_level_note(cell_types)
        availability.append(
            AnalysisAvailability(
                cell_id=cell_id,
                canonical_cell_id=str(base["canonical_cell_id"]),
                display_label=str(base["display_label"]),
                sample_batch_id=str(base["sample_batch_id"]),
                has_capacity=ANALYSIS_CAPACITY in cell_types,
                has_voltage_profile=ANALYSIS_VOLTAGE in cell_types,
                has_eis=ANALYSIS_EIS in cell_types,
                has_eis_time_series=len(eis_time_points) >= 2,
                has_sheet_resistance=ANALYSIS_SHEET in cell_types,
                has_raman=ANALYSIS_RAMAN in batch_types,
                has_tga=ANALYSIS_TGA in batch_types,
                file_count=len(cell_files),
                missing_note=missing,
            )
        )
    return availability


def build_comparison_candidates(
    cell_ids: list[str],
    conditions: dict[str, dict[str, Any]],
) -> list[ComparisonCandidate]:
    matched = {cell_id: find_condition(cell_id, conditions) for cell_id in sorted(set(cell_ids))}
    available = [cell_id for cell_id, condition in matched.items() if condition]
    candidates = []
    for left_id, right_id in itertools.combinations(available, 2):
        candidates.append(compare_candidate(left_id, right_id, matched[left_id] or {}, matched[right_id] or {}))
    return candidates


def build_analysis_comparison_validations(
    records: list[MetricRecord],
    conditions: dict[str, dict[str, Any]],
) -> list[AnalysisComparisonValidation]:
    by_type: dict[str, list[MetricRecord]] = {}
    for record in records:
        if record.analysis_type in {ANALYSIS_CAPACITY, ANALYSIS_VOLTAGE}:
            by_type.setdefault(record.analysis_type, []).append(record)
    validations: list[AnalysisComparisonValidation] = []
    for analysis_type, typed_records in sorted(by_type.items()):
        latest_by_cell = latest_records_by_cell(typed_records)
        condition_candidates = {
            frozenset((candidate.cell_id_a, candidate.cell_id_b)): candidate
            for candidate in build_comparison_candidates(list(latest_by_cell), conditions)
        }
        for left_id, right_id in itertools.combinations(sorted(latest_by_cell), 2):
            base = condition_candidates.get(frozenset((left_id, right_id)))
            left = latest_by_cell[left_id]
            right = latest_by_cell[right_id]
            validations.append(validate_analysis_pair(analysis_type, left, right, base))
    return validations


def latest_records_by_cell(records: list[MetricRecord]) -> dict[str, MetricRecord]:
    latest: dict[str, MetricRecord] = {}
    for record in records:
        latest[record.cell_id] = record
    return latest


def validate_analysis_pair(
    analysis_type: str,
    left: MetricRecord,
    right: MetricRecord,
    base: ComparisonCandidate | None,
) -> AnalysisComparisonValidation:
    if base and base.comparison_grade == "X":
        return AnalysisComparisonValidation(
            analysis_type=analysis_type,
            cell_id_a=left.cell_id,
            cell_id_b=right.cell_id,
            status="BLOCK",
            reason=base.reason,
        )
    status = "GOOD" if base and base.comparison_grade == "A" else "WARNING"
    reason = "조건 동일, loading 차이 비교 가능 범위입니다." if status == "GOOD" else "공통 조건은 통과했지만 loading 차이 또는 조건표 정보 확인이 필요합니다."
    protocol_a = str(left.metrics.get("protocol") or "")
    protocol_b = str(right.metrics.get("protocol") or "")
    common_cycles = ""
    if analysis_type == ANALYSIS_CAPACITY:
        if protocol_a and protocol_b and protocol_a != protocol_b:
            status = "BLOCK"
            reason = f"protocol 다름: {protocol_a} vs {protocol_b}. 직접 capacity curve overlay는 해석 위험이 있습니다."
    elif analysis_type == ANALYSIS_VOLTAGE:
        left_cycles = metric_cycle_set(left.metrics.get("profile_available_cycles"))
        right_cycles = metric_cycle_set(right.metrics.get("profile_available_cycles"))
        common = sorted(left_cycles & right_cycles, key=cycle_sort_key)
        common_cycles = ",".join(common)
        if not common:
            status = "BLOCK"
            reason = "공통 cycle이 없어 voltage profile overlay를 막습니다."
        elif left_cycles != right_cycles and status != "BLOCK":
            status = "WARNING"
            reason = f"공통 cycle만 비교합니다: {common_cycles}."
    return AnalysisComparisonValidation(
        analysis_type=analysis_type,
        cell_id_a=left.cell_id,
        cell_id_b=right.cell_id,
        status=status,
        reason=reason,
        protocol_a=protocol_a,
        protocol_b=protocol_b,
        common_cycles=common_cycles,
    )


def metric_cycle_set(value: Any) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def cycle_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"\d+", value)
    return (int(match.group(0)) if match else 999999, value)


def compare_candidate(left_id: str, right_id: str, left: dict[str, Any], right: dict[str, Any]) -> ComparisonCandidate:
    matches = {field: clean(left.get(field)) == clean(right.get(field)) and bool(clean(left.get(field))) for field in REQUIRED_COMPARISON_FIELDS}
    missing_fields = [
        field
        for field in REQUIRED_COMPARISON_FIELDS
        if not clean(left.get(field)) or not clean(right.get(field))
    ]
    mismatches = [field for field, matched in matches.items() if not matched and field not in missing_fields]
    diff = numeric_diff(left.get("areal_mass_density"), right.get("areal_mass_density"))
    if missing_fields:
        grade = "X"
        reason = f"비교 금지: 필수 조건 누락 ({', '.join(missing_fields)})."
    elif mismatches:
        grade = "X"
        reason = f"비교 금지: {', '.join(mismatches)} 불일치."
    elif diff is None:
        grade = "X"
        reason = "비교 금지: Areal mass density 값이 없습니다."
    elif diff <= 0.5:
        grade = "A"
        reason = f"완전 비교 가능: loading diff {diff:.2f} mg/cm2."
    elif diff <= 1.0:
        grade = "B"
        reason = f"주의 비교 가능: loading diff {diff:.2f} mg/cm2."
    else:
        grade = "C"
        reason = f"비교 비추천: loading diff {diff:.2f} mg/cm2."
    return ComparisonCandidate(
        cell_id_a=left_id,
        cell_id_b=right_id,
        same_electrolyte=matches["electrolyte"],
        same_binder=matches["binder"],
        same_voltage_range=matches["voltage_range"],
        same_ratio=matches["ratio"],
        areal_mass_density_diff=diff,
        comparison_grade=grade,
        reason=reason,
    )


def candidate_note(candidate: ComparisonCandidate) -> str:
    diff = (
        f"{candidate.areal_mass_density_diff:.2f} mg/cm2"
        if candidate.areal_mass_density_diff is not None
        else "값 없음"
    )
    return (
        f"{candidate.cell_id_a} vs {candidate.cell_id_b}: "
        f"grade {candidate.comparison_grade} - {candidate.reason} "
        f"(Areal mass density 차이: {diff})"
    )


def find_condition(cell_id: str, conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if cell_id in conditions:
        return conditions[cell_id]
    target = normalize_match_key(cell_id)
    best: tuple[int, dict[str, Any]] | None = None
    for key, condition in conditions.items():
        candidates = [key, str(condition.get("sample") or ""), str(condition.get("cell_id") or "")]
        for candidate in candidates:
            normalized = normalize_match_key(candidate)
            if not normalized:
                continue
            score = 0
            if target == normalized:
                score = 100
            elif len(normalized) >= 5 and normalized in target:
                score = 80 + min(len(normalized), 20)
            elif len(target) >= 5 and target in normalized:
                score = 70 + min(len(target), 20)
            else:
                score = token_match_score(cell_id, candidate)
            if score and (best is None or score > best[0]):
                best = (score, condition)
    return best[1] if best and best[0] >= 75 else {}


def normalize_match_key(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"^\d+[_\-\s]*", "", text)
    text = text.replace("activated", "act")
    text = text.replace(" ", "")
    return re.sub(r"[^a-z0-9.]+", "", text)


def token_match_score(target: Any, candidate: Any) -> int:
    target_tokens = set(match_tokens(str(target).lower()))
    candidate_tokens = set(match_tokens(str(candidate).lower()))
    candidate_tokens = {token for token in candidate_tokens if not token.isdigit()}
    if not target_tokens or not candidate_tokens:
        return 0
    common = candidate_tokens & target_tokens
    required = {token for token in candidate_tokens if len(token) >= 3 or re.search(r"\d+t$", token)}
    if required and required <= target_tokens:
        return 75 + min(len(common), 4)
    if len(common) >= max(2, len(candidate_tokens) - 1):
        return 75
    return 0


def match_tokens(value: str) -> list[str]:
    text = value.replace("activated", "act")
    text = re.sub(r"(?i)(capacity|cycle|eis|profile|rate|per)", " ", text)
    raw_tokens = re.findall(r"[a-z]*\d+t|\d+(?:\.\d+)?c?|[a-z]+", text)
    tokens: list[str] = []
    for token in raw_tokens:
        tokens.append(token)
        split = re.match(r"([a-z]+)(\d+t)$", token)
        if split:
            tokens.extend([split.group(1), split.group(2)])
    return tokens


def compare_pair(left_id: str, right_id: str, left: dict[str, Any], right: dict[str, Any]) -> str:
    return candidate_note(compare_candidate(left_id, right_id, left, right))


def numeric_diff(left: Any, right: Any) -> float | None:
    from .metrics import to_float

    l_value = to_float(left)
    r_value = to_float(right)
    if l_value is None or r_value is None:
        return None
    return abs(l_value - r_value)


def clean(value: Any) -> str:
    return str(value).strip().lower() if value not in (None, "") else ""


def canonical_cell_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("activated", "act")
    text = re.sub(r"[^a-z0-9가-힣]+", "_", text).strip("_")
    return text or "unknown_cell"


def sample_batch_id(condition: dict[str, Any]) -> str:
    for key in ("sample_batch_id", "sample_group", "material_family", "sample", "raw_sample_name", "cell_id"):
        value = condition.get(key)
        if value not in (None, ""):
            return sample_batch_id_from_text(value)
    return "unknown_batch"


def sample_batch_id_from_text(value: Any) -> str:
    text = canonical_cell_id(value)
    text = re.sub(r"(^|_)\d+(?:\.\d+)?c($|_)", "_", text)
    text = re.sub(r"(_)?\d+hr(_)?", "_", text)
    text = re.sub(r"(_)?\d{6,8}(_)?", "_", text)
    text = re.sub(r"(_)?\d+t(?:_\d+)?$", "", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown_batch"


def guess_cycle_protocol(filename: str) -> str:
    match = re.search(r"(?i)(\d+(?:\.\d+)?\s*c|rate\s*per|low\s*temp|cv)", filename)
    return re.sub(r"\s+", "", match.group(1)).lower() if match else ""


def missing_cell_level_note(analysis_types: set[str]) -> str:
    missing = []
    if ANALYSIS_CAPACITY not in analysis_types:
        missing.append("Capacity file missing")
    if ANALYSIS_VOLTAGE not in analysis_types:
        missing.append("Voltage profile file missing")
    if ANALYSIS_EIS not in analysis_types:
        missing.append("EIS file missing")
    return "; ".join(missing)
