"""In-scope row filter for matching verification.

Single source of truth: reuses the journal view's `FILTER_RULES` and value
normalization from `excel_dashboard` (the same rules behind the "무시행 표시안함/회색"
toggle), but exposes them over the `conditions` dict produced by `read_conditions`
instead of a live worksheet.

In-scope = a journal row satisfying ALL filter rules:
  참고=12파이_Cu foil · 전해질=1.0M LiPF6 EC/DEC 1:1 · 종류=LIB ·
  Binder∈{2wt%cmc, 2wt%cmc/40wt%SBR} · Voltage range=0.01~2V
"""
from __future__ import annotations

from typing import Any

from .excel_dashboard import FILTER_RULES, normalize_filter_value

# FILTER_RULES is keyed by raw journal header; map each to the key that
# `conditions.condition_column` produces in the conditions dict.
_HEADER_TO_CONDITION_KEY = {
    "참고": "reference",
    "전해질": "electrolyte",
    "종류": "cell_type",
    "binder": "binder",
    "voltagerange": "voltage_range",
}


def normalize_token(value: Any) -> str:
    """Lowercase + strip all whitespace (absorbs `2wt% CMC` vs `2wt%cmc`)."""
    return normalize_filter_value(value)


def in_scope(condition: dict[str, Any]) -> bool:
    """True iff a condition (journal row) satisfies every FILTER_RULES rule."""
    for header, allowed_values in FILTER_RULES.items():
        condition_key = _HEADER_TO_CONDITION_KEY.get(header)
        if condition_key is None:
            continue
        if normalize_token(condition.get(condition_key, "")) not in allowed_values:
            return False
    return True


def filter_in_scope(conditions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep only in-scope rows from a conditions dict."""
    return {key: cond for key, cond in conditions.items() if in_scope(cond)}
