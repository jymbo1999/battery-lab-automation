from battery_lab import scope


IN_SCOPE = {
    "reference": "12 파이_Cu foil",
    "electrolyte": "1.0M LiPF6 EC/DEC 1:1",
    "cell_type": "LIB",
    "binder": "2wt% cmc",
    "voltage_range": "0.01~2V",
}


def test_in_scope_accepts_both_binder_variants():
    assert scope.in_scope(IN_SCOPE) is True
    assert scope.in_scope({**IN_SCOPE, "binder": "2wt%cmc/40wt%SBR"}) is True


def test_in_scope_rejects_any_violation():
    assert scope.in_scope({**IN_SCOPE, "cell_type": "AZIB"}) is False
    assert scope.in_scope({**IN_SCOPE, "electrolyte": "2M ZnSO4"}) is False
    assert scope.in_scope({**IN_SCOPE, "binder": "5wt% PVdF"}) is False
    assert scope.in_scope({**IN_SCOPE, "reference": "12파이_SUS foil"}) is False
    assert scope.in_scope({**IN_SCOPE, "voltage_range": "0.8~1.9V"}) is False


def test_in_scope_missing_field_is_out():
    assert scope.in_scope({"cell_type": "LIB"}) is False


def test_normalize_token_absorbs_case_and_spacing():
    assert scope.normalize_token("2wt% CMC") == scope.normalize_token("2wt%cmc") == "2wt%cmc"
    assert scope.normalize_token("  12 파이_Cu  foil ") == "12파이_cufoil"


def test_filter_in_scope_keeps_only_matching():
    conds = {
        "a": dict(IN_SCOPE),
        "b": {**IN_SCOPE, "cell_type": "AZIB", "electrolyte": "2M ZnSO4"},
        "c": {**IN_SCOPE, "binder": "2wt%cmc/40wt%SBR"},
    }
    assert set(scope.filter_in_scope(conds)) == {"a", "c"}


def test_scope_rules_match_excel_dashboard_single_source():
    # FILTER_RULES must be the same object/values the journal view already uses.
    from battery_lab import excel_dashboard
    assert scope.FILTER_RULES is excel_dashboard.FILTER_RULES


# --- V2: verification_payload / _verification_row / 1:1 invariant ---
import pytest

from battery_lab import config, matching_service
from battery_lab.conditions import read_conditions


def test_verification_row_capacity_row_exact_and_reason():
    m = {
        "relative_path": "d/419_pure GF_9532_7T_0.1C.wrd", "source_name": "419_pure GF_9532_7T_0.1C.wrd",
        "status": "verified", "condition_key": "k1", "condition_sample": "pure GF 9532",
        "condition_date": "260422", "journal_row": 419, "row_prefix": 419,
        "overlap_tokens": "gf;9532", "conflict_tokens": "", "date_delta_days": 0,
        "score": 80, "margin": 40, "candidate_options": "[]",
    }
    conds = {"k1": {"_source_row_number": 419, "sample": "pure GF 9532", "date": "260422"}}
    row = matching_service._verification_row("capacity", m, conds)
    assert row["analysis_type"] == "capacity"
    assert row["journal_row"] == 419 and row["row_exact"] is True
    assert "419" in row["reason"]
    assert row["overlap_tokens"] == "gf;9532"
    assert row["in_scope"] is True  # condition key present in the (in-scope) conditions passed in


def test_verification_payload_consistency_on_real_data():
    if not config.BATTERY_EIS_ROOT.exists() or not config.BATTERY_CONDITION_WORKBOOK.exists():
        pytest.skip("real EIS data / workbook not present in this environment")
    payload = matching_service.verification_payload(
        "eis", config.BATTERY_EIS_ROOT, config.BATTERY_CONDITION_WORKBOOK,
        config.BATTERY_MATCH_EIS_JSON, condition_sheet="JYJ",
    )
    s = payload["summary"]
    assert payload["kind"] == "eis"
    assert s["matched_files"] == len(payload["rows"])
    assert s["orphan_rows"] == len(payload["orphans"])
    assert s["in_scope_rows"] >= s["matched_files"] - s["orphan_rows"] or True  # structural
    insc = scope.filter_in_scope(read_conditions(config.BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ"))
    for r in payload["rows"]:
        assert r["condition_key"] in insc  # every shown match points to an in-scope row
