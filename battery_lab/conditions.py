from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from .file_io import canonical_column, read_delimited, read_xlsx_optional


CONDITION_FIELDS = [
    "sample",
    "batch",
    "cell_no",
    "date",
    "areal_mass_density",
    "electrode_density",
    "electrolyte",
    "binder",
    "voltage_range",
    "ratio",
    "note",
]


def read_conditions(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_xlsx_optional(path) if path.suffix.lower() in {".xlsx", ".xls"} else read_delimited(path)
    conditions = {}
    for row in rows:
        normalized = {condition_column(key): value for key, value in row.items()}
        cell_id = str(normalized.get("cell_id") or normalized.get("cell") or normalized.get("sample") or "").strip()
        if cell_id:
            normalized["cell_id"] = cell_id
            conditions[cell_id] = normalized
    return conditions


def condition_column(name: str) -> str:
    canonical = canonical_column(name)
    aliases = {
        "참고": "reference",
        "전해질": "electrolyte",
        "날짜": "date",
        "일자": "date",
        "측정일": "date",
        "종류": "cell_type",
        "cell": "cell_id",
        "cell_name": "cell_id",
        "cell_자리": "cell_position",
        "sample": "sample",
        "sample_name": "sample",
        "conductive_agent": "conductive_agent",
        "mass_loading": "areal_mass_density",
        "areal_loading": "areal_mass_density",
        "areal_mass_density_mg_c_2": "areal_mass_density",
        "areal_mass_density_mg_cm2": "areal_mass_density",
        "합제밀도_g_cm3": "electrode_density",
        "합제밀도": "electrode_density",
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
    notes = []
    matched = {cell_id: find_condition(cell_id, conditions) for cell_id in sorted(set(cell_ids))}
    available = [cell_id for cell_id, condition in matched.items() if condition]
    for left, right in itertools.combinations(available, 2):
        notes.append(compare_pair(left, right, matched[left] or {}, matched[right] or {}))
    missing = [cell_id for cell_id, condition in matched.items() if not condition]
    if missing and conditions:
        notes.append(f"조건표 매칭 실패: {len(missing)}개 셀 ({', '.join(missing[:5])}).")
    return notes


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
    problems = []
    diff = numeric_diff(left.get("areal_mass_density"), right.get("areal_mass_density"))
    if diff is not None and diff > 1.0:
        problems.append(f"Areal mass density 차이 {diff:.2f} mg/cm2")
    for field in ("electrolyte", "binder", "voltage_range", "ratio"):
        l_value = clean(left.get(field))
        r_value = clean(right.get(field))
        if l_value and r_value and l_value != r_value:
            problems.append(f"{field} 불일치")
    if problems:
        return f"{left_id} vs {right_id}: 비교 주의 - {', '.join(problems)}."
    if diff is not None:
        return f"{left_id} vs {right_id}: 비교 가능 - Areal mass density 차이 {diff:.2f} mg/cm2."
    return f"{left_id} vs {right_id}: 주요 조건은 확인됐지만 Areal mass density 값이 없습니다."


def numeric_diff(left: Any, right: Any) -> float | None:
    from .metrics import to_float

    l_value = to_float(left)
    r_value = to_float(right)
    if l_value is None or r_value is None:
        return None
    return abs(l_value - r_value)


def clean(value: Any) -> str:
    return str(value).strip().lower() if value not in (None, "") else ""
