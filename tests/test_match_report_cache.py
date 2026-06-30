from pathlib import Path

from battery_lab import config
from battery_lab import ui as battery_ui
from battery_lab import viewer_service

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def _count_match_report(monkeypatch):
    """Wrap viewer_service.build_eis_match_report with a call counter."""
    calls = {"n": 0}
    real = viewer_service.build_eis_match_report

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(viewer_service, "build_eis_match_report", counting)
    return calls


def test_match_report_built_once_across_overlay_requests(tmp_path, monkeypatch):
    # Two cluster-overlay requests over UNCHANGED data must not rebuild the match
    # report twice. build_*_match_report is the ~2.2s Render bottleneck that ran on
    # every request in front of the render cache; memoizing it is the fix.
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    ov = tmp_path / "ov.json"
    ov.write_text("{}", encoding="utf-8")  # unique stat -> isolates this test's memo key
    calls = _count_match_report(monkeypatch)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", ov)
    kwargs = dict(mode="comparison", key="", show_fit=False)
    viewer_service.eis_overlay_payload(*args, **kwargs)
    viewer_service.eis_overlay_payload(*args, **kwargs)

    assert calls["n"] == 1


def test_match_report_rebuilds_when_inputs_change(tmp_path, monkeypatch):
    # Changing the override file changes the cache context, so the memo must
    # invalidate and rebuild rather than serve a stale report.
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    ov = tmp_path / "ov.json"
    ov.write_text("{}", encoding="utf-8")
    calls = _count_match_report(monkeypatch)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", ov)
    kwargs = dict(mode="comparison", key="", show_fit=False)
    viewer_service.eis_overlay_payload(*args, **kwargs)  # build #1
    ov.write_text('{"changed": 1}', encoding="utf-8")    # context changes
    viewer_service.eis_overlay_payload(*args, **kwargs)  # build #2

    assert calls["n"] == 2
