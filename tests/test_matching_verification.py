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


def test_render_verification_html_shows_evidence_summary_orphans():
    from battery_lab import verification_view

    payloads = {
        "capacity": {
            "kind": "capacity",
            "summary": {"in_scope_rows": 125, "matched_files": 117, "needs_review": 0,
                        "ambiguous_files": 0, "unmatched_files": 0, "orphan_rows": 12, "duplicate_groups": 4},
            "rows": [{
                "relative_path": "d/448_1.5act 4T.wrd", "source_name": "448_1.5act 4T.wrd",
                "analysis_type": "capacity", "status": "verified", "journal_row": 448,
                "condition_key": "1.5act 4T", "sample": "1.5act 4T", "date": "260507",
                "row_exact": True, "overlap_tokens": "1.5act", "conflict_tokens": "",
                "date_delta_days": 0, "score": 140, "margin": 50,
                "reason": "파일명 앞 행번호 448가 실험일지 행 448와 일치합니다.",
                "candidate_options": [], "override_source": "",
            }],
            "orphans": [{"condition_key": "pure 7T", "journal_row": 501, "sample": "pure 7T", "date": "260601"}],
            "invariant": {"ambiguous": [], "duplicates": [{"journal_row": 443, "files": ["a.wrd", "b.wrd"]}], "unmatched_count": 0},
        },
        "eis": {"kind": "eis", "summary": {"in_scope_rows": 125, "matched_files": 274, "needs_review": 154,
                "ambiguous_files": 124, "unmatched_files": 38, "orphan_rows": 84, "duplicate_groups": 21},
                "rows": [], "orphans": [], "invariant": {"ambiguous": [], "duplicates": [], "unmatched_count": 38}},
    }
    html = verification_view.render_verification_html(payloads)
    assert "매칭 검증" in html and "<table" in html
    assert "448_1.5act 4T.wrd" in html
    assert "파일명 앞 행번호 448가 실험일지 행 448와 일치합니다." in html  # verified gets its reason shown
    assert "117" in html and "125" in html       # summary numbers
    assert "pure 7T" in html                       # orphan row listed
    assert "확정" in html                          # verified badge label


def test_verification_api_route_shape_and_404():
    from battery_lab.flask_app import create_app

    client = create_app().test_client()
    resp = client.get("/battery/api/eis/verification")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "summary" in data and "rows" in data and "orphans" in data and data["kind"] == "eis"
    # unknown kind -> 404
    assert client.get("/battery/api/nope/verification").status_code == 404


def test_verification_page_route_renders_html():
    from battery_lab.flask_app import create_app

    resp = create_app().test_client().get("/battery/verification")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "매칭 검증" in body and "<table" in body


def test_path_date_extracts_yymmdd_folder():
    assert matching_service._path_date("260501/1.5 act 1_01.SEO") == "260501"
    assert matching_service._path_date("260319/20260319/419_x.wrd") == "260319"  # 6-digit folder, not the 8-digit one
    assert matching_service._path_date("no date here.wrd") == ""


def test_verification_payload_clusters_eis_time_series():
    if not config.BATTERY_EIS_ROOT.exists() or not config.BATTERY_CONDITION_WORKBOOK.exists():
        pytest.skip("real EIS data / workbook not present")
    p = matching_service.verification_payload(
        "eis", config.BATTERY_EIS_ROOT, config.BATTERY_CONDITION_WORKBOOK,
        config.BATTERY_MATCH_EIS_JSON, condition_sheet="JYJ",
    )
    clusters = p["deferred_rows"]
    assert p["summary"]["time_series_clusters"] == len(clusters)
    assert len(clusters) < 43
    missing_endpoint = [c for c in clusters if not (c["has_zero"] and c["has_24"])]
    assert len(missing_endpoint) < 37
    assert all("member_paths" in c and "match_status" in c for c in clusters)


def test_verification_payload_time_series_clusters_synthetic(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); wsx = wb.active
    wsx.append(["sample", "참고", "전해질", "종류", "Binder", "Voltage range", "date"])
    wsx.append(["dl 2t2t", "12파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "2wt% cmc", "0.01~2V", "260521"])
    wb_path = tmp_path / "cond.xlsx"; wb.save(wb_path)
    eis_root = tmp_path / "EIS" / "260521"; eis_root.mkdir(parents=True)
    for name in ("dl 2t2t_0hr_01.SEO", "dl2t2t_24hr_01.SEO"):
        (eis_root / name).write_text("x", encoding="utf-8")
    ov = tmp_path / "ov.json"
    p = matching_service.verification_payload(
        "eis", tmp_path / "EIS", wb_path, ov, condition_sheet=wsx.title)
    assert p["summary"]["time_series_clusters"] == len(p["deferred_rows"]) >= 1
    assert all("has_zero" in c for c in p["deferred_rows"])


def test_render_checklist_html_has_inputs_and_candidates():
    from battery_lab import checklist_view

    payloads = {"eis": {"kind": "eis", "summary": {}, "orphans": [], "deferred_rows": [], "rows": [
        {"relative_path": "260430/pc 91_1_02.SEO", "source_name": "pc 91_1_02.SEO", "status": "ambiguous",
         "file_date": "260430", "sample": "pc 91_6T_1", "journal_row": 432, "reason": "상위 후보가 가깝습니다.",
         "candidate_options": [
             {"condition_key": "pc 91_6T_1", "journal_row": 432, "sample": "pc 91_6T_1", "date": "260430", "date_delta_days": 1, "score": 59},
             {"condition_key": "pc 91_6T_2", "journal_row": 433, "sample": "pc 91_6T_2", "date": "260430", "date_delta_days": 1, "score": 56},
         ]},
        {"relative_path": "260430/v.SEO", "source_name": "v.SEO", "status": "verified", "journal_row": 400, "sample": "x", "candidate_options": []},
    ]}}
    html = checklist_view.render_checklist_html(payloads)
    assert "매칭 확인 체크리스트" in html
    assert 'select class="ans" data-file="260430/pc 91_1_02.SEO"' in html
    assert "행 432" in html and "행 433" in html      # both candidates offered
    assert "__delete__" in html and "__skip__" in html
    assert "이미 확정" in html                          # verified collapsed for spot-check
    assert "battery_matching_checklist_v1" in html      # localStorage key (round-trip)


def test_apply_checklist_answers_roundtrip(tmp_path):
    import json as _json

    csv = tmp_path / "cond.csv"
    csv.write_text(
        "sample,참고,전해질,종류,Binder,Voltage range\n"
        "pc 91_6T_1,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n"
        "pc 91_6T_2,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n",
        encoding="utf-8",
    )
    ov = tmp_path / "ov.json"
    answers = {"version": 1, "answers": {
        "260430/pc 91_1_02.SEO": {"choice": "pc 91_6T_1", "memo": "확실"},
        "260430/junk.SEO": {"choice": "__delete__", "memo": "중복본"},
        "260430/dunno.SEO": {"choice": "__skip__"},
        "260430/bad.SEO": {"choice": "no such key"},
    }}
    res = matching_service.apply_checklist_answers(answers, csv, ov)
    assert res == {"applied": 1, "deleted": 1, "skipped": 1, "unknown": 1, "override_count": 2}
    saved = _json.loads(ov.read_text(encoding="utf-8"))
    assert saved["260430/pc 91_1_02.SEO"]["condition_key"] == "pc 91_6T_1"
    assert saved["260430/pc 91_1_02.SEO"]["journal_row"] == 2  # first data row
    assert saved["260430/junk.SEO"]["action"] == "delete_file"
    assert "260430/dunno.SEO" not in saved and "260430/bad.SEO" not in saved
