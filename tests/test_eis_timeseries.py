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
