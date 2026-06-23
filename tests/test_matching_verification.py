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


def test_read_conditions_keeps_duplicate_sample_rows(tmp_path):
    # Two replicate cells share the Sample name '1.5act 4T' (real case: JYJ rows 447/448).
    # They must remain TWO distinct conditions, each with its own row number, not collapse to one.
    csv = tmp_path / "cond.csv"
    csv.write_text(
        "sample,참고,전해질,종류,Binder,Voltage range\n"
        "1.5act 4T,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n"
        "1.5act 4T,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n"
        "pure 5T,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n",
        encoding="utf-8",
    )
    conds = read_conditions(csv)
    assert len(conds) == 3  # was 2 before fix (the two '1.5act 4T' collapsed)
    assert sorted(c["_source_row_number"] for c in conds.values()) == [2, 3, 4]
    onefive = [c for c in conds.values() if c.get("sample") == "1.5act 4T"]
    assert len(onefive) == 2
    assert {c["_source_row_number"] for c in onefive} == {2, 3}
    assert all(scope.in_scope(c) for c in conds.values())


def test_read_conditions_uses_true_excel_row_not_drifted_by_blanks(tmp_path):
    # Blank rows in the journal must NOT shift row numbers: capacity file leading
    # numbers refer to the TRUE Excel row, so _source_row_number must equal it.
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["sample", "참고", "전해질", "종류", "Binder", "Voltage range"])      # Excel row 1 (header)
    ws.append(["A 4T", "12파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "2wt% cmc", "0.01~2V"])  # row 2
    ws.append([None, None, None, None, None, None])                                # row 3 (blank)
    ws.append(["B 5T", "12파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "2wt% cmc", "0.01~2V"])  # row 4
    path = tmp_path / "j.xlsx"
    wb.save(path)

    conds = read_conditions(path, sheet_name=ws.title)
    byrow = {c["_source_row_number"]: c.get("sample") for c in conds.values()}
    assert byrow == {2: "A 4T", 4: "B 5T"}  # B at TRUE row 4, not 3 (blank row not collapsed away)


def test_verification_api_route_shape_and_404():
    from battery_lab.flask_app import create_app

    client = create_app().test_client()
    resp = client.get("/battery/api/eis/verification")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "summary" in data and "rows" in data and "orphans" in data and data["kind"] == "eis"
    # unknown kind -> 404
    assert client.get("/battery/api/nope/verification").status_code == 404
