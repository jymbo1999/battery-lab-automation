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
