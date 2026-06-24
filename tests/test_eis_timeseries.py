from battery_lab.eis_matching import EISConditionMatch
from battery_lab import eis_timeseries as ts


def _m(rel, group_key, tp, *, key="", sample="", date="", delta=None, score=70):
    """Build a minimal time-series EISConditionMatch for tests."""
    return EISConditionMatch(
        source_path=rel, relative_path=rel, is_time_series=True,
        file_group_key=group_key, time_point=tp, status="review", score=score, margin=0,
        condition_key=key, condition_sample=sample, condition_date=date, date_delta_days=delta,
    )


def test_hr_num_and_fmt():
    assert ts.hr_num("24hr") == 24
    assert ts.hr_num("0hr") == 0
    assert ts.hr_num("") is None
    assert ts._fmt_hrs({0, 1, 2, 3}) == "[0,1,2,3]"


def test_base_signature_strips_trailing_replicate_only():
    assert ts._base_signature("260610pure4t1") == "260610pure4t"
    assert ts._base_signature("260610pure4t") == "260610pure4t"
    assert ts._base_signature("260521dl2t2t2") == "260521dl2t2t"
    assert ts._base_signature("260521dl2t2t") == "260521dl2t2t"


def test_stage1_collapses_spacing_only_split():
    # "dl 2t2t" and "dl2t2t" differ only by a space -> same compact signature.
    ms = [
        _m("260521/dl 2t2t_0hr_01.SEO", "260521 dl 2t2t", "0hr"),
        _m("260521/dl2t2t_24hr_01.SEO", "260521 dl2t2t", "24hr"),
    ]
    groups = ts._stage1_groups(ms)
    assert len(groups) == 1
    (sig, members), = groups.items()
    assert len(members) == 2


def _frags(*pairs):
    """pairs: (compact_sig, [hr ints]) -> list[(sig, [match])] for _merge_fragments."""
    out = []
    for sig, hrs in pairs:
        out.append((sig, [_m(f"{sig}_{h}hr.SEO", sig, f"{h}hr") for h in hrs]))
    return out


def _hrs_of(members):
    return sorted(ts.hr_num(m.time_point) for m in members)


def test_merge_left_and_right_fragment():
    # dl 2t2t: [0,1,2,3] + [4,5,8,24] -> one complete cell.
    res = ts._merge_fragments(_frags(("260521dl2t2t", [0, 1, 2, 3]),
                                     ("260521dl2t2t", [4, 5, 8, 24])))
    assert len(res) == 1
    assert _hrs_of(res[0]["members"]) == [0, 1, 2, 3, 4, 5, 8, 24]
    assert res[0]["provenance"]  # records what was merged


def test_keep_two_real_cells_with_two_zeros():
    # Both fragments start at 0hr -> two separate cells, never merged.
    res = ts._merge_fragments(_frags(("260603pure2t1", [0, 1, 2, 9]),
                                     ("260603pure2t2", [0, 1, 24])))
    assert len(res) == 2


def test_no_merge_on_overlapping_hours():
    # Disjoint requirement fails (both contain 3hr) -> stay separate, flagged later.
    res = ts._merge_fragments(_frags(("x", [0, 3]), ("x2", [3, 24])))
    assert len(res) == 2


def test_complete_group_passes_through_untouched():
    res = ts._merge_fragments(_frags(("c", [0, 6, 24])))
    assert len(res) == 1 and res[0]["provenance"] == ""


def test_cluster_dict_verified_complete_single_row():
    members = [
        _m("260610/pure 4t_2_0hr.SEO", "260610 pure 4t 2", "0hr", key="k1", sample="pure 4T", date="260610", delta=0, score=80),
        _m("260610/pure 4t_2_24hr.SEO", "260610 pure 4t 2", "24hr", key="k1", sample="pure 4T", date="260610", delta=0, score=80),
    ]
    conds = {"k1": {"_source_row_number": 510, "sample": "pure 4T", "date": "260610"}}
    c = ts._cluster_dict(members, "", conds)
    assert c["has_zero"] and c["has_24"]
    assert c["match_status"] == "verified"
    assert c["condition_key"] == "k1" and c["date_delta_days"] == 0
    assert c["time_points"] == "0hr;24hr"


def test_cluster_dict_ambiguous_when_endpoint_missing():
    members = [_m("260603/pure 5t_1_0hr.SEO", "260603 pure 5t 1", "0hr", key="k1", score=70),
               _m("260603/pure 5t_1_9hr.SEO", "260603 pure 5t 1", "9hr", key="k1", score=70)]
    conds = {"k1": {"_source_row_number": 300, "sample": "pure 5T", "date": "260603"}}
    c = ts._cluster_dict(members, "", conds)
    assert c["match_status"] == "ambiguous"
    assert "끝점" in c["reason"]


def test_cluster_dict_ambiguous_when_rows_compete():
    members = [_m("a/x_0hr.SEO", "g", "0hr", key="k1", score=70),
               _m("a/x_24hr.SEO", "g", "24hr", key="k2", score=68)]
    conds = {"k1": {"_source_row_number": 1}, "k2": {"_source_row_number": 2}}
    c = ts._cluster_dict(members, "", conds)
    assert c["match_status"] == "ambiguous"
    import json
    opts = json.loads(c["candidate_options"])
    assert {o["condition_key"] for o in opts} == {"k1", "k2"}


def test_build_clusters_end_to_end_merges_and_ids():
    ms = [
        _m("260521/dl 2t2t_0hr.SEO", "260521 dl 2t2t", "0hr", key="k1", score=70),
        _m("260521/dl2t2t_24hr.SEO", "260521 dl2t2t", "24hr", key="k1", score=70),
    ]
    conds = {"k1": {"_source_row_number": 11, "sample": "dl 2t2t", "date": "260521"}}
    clusters = ts.build_time_series_clusters(ms, conds)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.cluster_id == "TS001"
    assert c.time_points == "0hr;24hr" and c.has_zero and c.has_24
    assert c.match_status == "verified"


def test_build_clusters_flags_journal_row_conflict():
    # Two distinct complete cells both map to the same journal row -> both conflict.
    ms = [
        _m("a/c1_0hr.SEO", "260521 a 1", "0hr", key="k1", score=70),
        _m("a/c1_24hr.SEO", "260521 a 1", "24hr", key="k1", score=70),
        _m("a/c2_0hr.SEO", "260521 a 2", "0hr", key="k1", score=70),
        _m("a/c2_24hr.SEO", "260521 a 2", "24hr", key="k1", score=70),
    ]
    conds = {"k1": {"_source_row_number": 5, "sample": "a", "date": "260521"}}
    clusters = ts.build_time_series_clusters(ms, conds)
    assert len(clusters) == 2
    assert all(c.match_status == "conflict" for c in clusters)
    assert all("충돌" in c.reason for c in clusters)


def test_report_uses_clusters(tmp_path):
    from pathlib import Path
    from battery_lab import eis_matching

    root = tmp_path
    (root / "260521").mkdir()
    for name in ("dl 2t2t_0hr_01.SEO", "dl2t2t_24hr_01.SEO"):
        (root / "260521" / name).write_text("x", encoding="utf-8")
    conditions = {"k1": {"_source_row_number": 11, "sample": "dl 2t2t",
                         "date": "260521", "cell_id": "dl 2t2t"}}
    paths = list((root / "260521").glob("*.SEO"))
    report = eis_matching.build_eis_match_report(paths, conditions, root)
    assert all(isinstance(g, ts.EISTimeSeriesCluster) for g in report.time_series_groups)
    assert len(report.time_series_groups) == 1


def test_real_data_recluster_reduces_fragmentation():
    import pytest
    from battery_lab import config
    from battery_lab.conditions import read_conditions
    from battery_lab.eis_matching import match_eis_files_to_conditions
    from battery_lab.matching_service import collect_source_files, EIS_SUFFIXES
    from battery_lab import scope

    if not config.BATTERY_EIS_ROOT.exists() or not config.BATTERY_CONDITION_WORKBOOK.exists():
        pytest.skip("real EIS data / workbook not present")
    conds = scope.filter_in_scope(read_conditions(config.BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ"))
    paths = collect_source_files(config.BATTERY_EIS_ROOT, EIS_SUFFIXES)
    _, matches = match_eis_files_to_conditions(paths, conds, config.BATTERY_EIS_ROOT)
    clusters = ts.build_time_series_clusters(matches, conds)
    # Baseline before reclustering: 43 groups, 16 missing 0hr + 21 missing 24hr.
    assert len(clusters) < 43
    missing = [c for c in clusters if not (c.has_zero and c.has_24)]
    assert len(missing) < 30
    # The dl/pc73 spacing splits must now be single complete clusters.
    by_sig = {c.cluster_signature: c for c in clusters}
    assert all(by_sig[s].has_zero and by_sig[s].has_24
               for s in by_sig if s.endswith("dl2t2t") or s.endswith("dl3t3t"))
