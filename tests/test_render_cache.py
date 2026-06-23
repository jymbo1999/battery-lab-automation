from pathlib import Path

from battery_lab import config, file_io, render_cache
from battery_lab import ui as battery_ui
from battery_lab import viewer_service

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_atomic_write_then_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    target = render_cache._cache_root() / "sub" / "x.json"
    render_cache._atomic_write_json(target, {"a": 1, "b": "두 번째"})
    assert render_cache._read_json(target) == {"a": 1, "b": "두 번째"}


def test_read_json_missing_or_corrupt_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    missing = render_cache._cache_root() / "nope.json"
    assert render_cache._read_json(missing) is None
    corrupt = render_cache._cache_root() / "bad.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{not json", encoding="utf-8")
    assert render_cache._read_json(corrupt) is None


def test_disabled_flag(monkeypatch):
    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "1")
    assert render_cache._disabled() is True
    monkeypatch.setenv("BATTERY_RENDER_CACHE_DISABLE", "0")
    assert render_cache._disabled() is False


def test_file_identity_and_membersig(tmp_path):
    root = tmp_path
    a = root / "d" / "a.csv"
    a.parent.mkdir(parents=True)
    a.write_text("x", encoding="utf-8")
    b = root / "d" / "b.csv"
    b.write_text("y", encoding="utf-8")

    ident = render_cache.file_identity(a, root)
    assert ident[0] == "d/a.csv" and isinstance(ident[1], int) and isinstance(ident[2], int)

    # order-independent
    assert render_cache.membersig([a, b], root) == render_cache.membersig([b, a], root)

    # size change -> identity and membersig change
    sig_before = render_cache.membersig([a, b], root)
    a.write_text("xxxxx", encoding="utf-8")
    assert render_cache.membersig([a, b], root) != sig_before


def test_cluster_key_changes_with_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    ctx = render_cache.context_hash(tmp_path / "wb.xlsx", tmp_path / "ov.json")
    k1 = render_cache.cluster_key("eis", "comparison", "C001", "sig", ctx, {"show_fit": True})
    k2 = render_cache.cluster_key("eis", "comparison", "C001", "sig", ctx, {"show_fit": False})
    assert k1 != k2


def test_cached_parse_file_hits_disk_second_time(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    sample = data / "cell__capacity__cycle1__20260101.csv"
    sample.write_text("cycle,capacity\n1,10\n2,11\n", encoding="utf-8")

    calls = {"n": 0}
    real_parse = file_io.parse_file

    def counting_parse(path):
        calls["n"] += 1
        return real_parse(path)

    monkeypatch.setattr(render_cache, "_parse_file", counting_parse)

    ds1 = render_cache.cached_parse_file(sample, data)
    ds2 = render_cache.cached_parse_file(sample, data)
    assert calls["n"] == 1                      # second call served from disk
    assert ds1.rows == ds2.rows
    assert ds1.meta.cell_id == ds2.meta.cell_id

    # mtime/size change -> re-parse
    sample.write_text("cycle,capacity\n1,10\n2,11\n3,12\n", encoding="utf-8")
    render_cache.cached_parse_file(sample, data)
    assert calls["n"] == 2


def test_ui_parse_file_cached_uses_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    from battery_lab import ui
    ui.parse_file_cached_by_mtime.cache_clear()

    sample = tmp_path / "cell__capacity__cycle1__20260101.csv"
    sample.write_text("cycle,capacity\n1,10\n", encoding="utf-8")

    calls = {"n": 0}
    real = render_cache._parse_file

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(render_cache, "_parse_file", counting)

    ui.parse_file_cached(sample)
    ui.parse_file_cached_by_mtime.cache_clear()   # drop in-memory layer
    ui.parse_file_cached(sample)                  # must hit DISK, not re-parse
    assert calls["n"] == 1


def test_cached_read_conditions_reads_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    wb = tmp_path / "cell_conditions.csv"
    wb.write_text("cell_id,binder\nA,CMC\n", encoding="utf-8")

    calls = {"n": 0}

    def fake_reader(path):
        calls["n"] += 1
        return {"A": {"binder": "CMC"}}

    monkeypatch.setattr(render_cache, "_read_conditions", fake_reader)

    c1 = render_cache.cached_read_conditions(wb)
    c2 = render_cache.cached_read_conditions(wb)
    assert calls["n"] == 1
    assert c1 == c2 == {"A": {"binder": "CMC"}}

    # workbook change -> re-read
    wb.write_text("cell_id,binder\nA,CMC\nB,PVdF\n", encoding="utf-8")
    render_cache.cached_read_conditions(wb)
    assert calls["n"] == 2


def test_cached_read_conditions_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    assert render_cache.cached_read_conditions(tmp_path / "absent.xlsx") == {}


def test_cluster_cache_put_get_and_gc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    flags = {"show_fit": False}
    payload_v1 = {"available": True, "html": "<svg>v1</svg>", "errors": [], "title": "C001"}

    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigAAA", "ctx", flags) is None
    render_cache.cluster_cache_put("eis", "comparison", "C001", "sigAAA", "ctx", flags, payload_v1)
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigAAA", "ctx", flags) == payload_v1

    # new membersig (a file changed) -> miss, and GC drops the stale sig file
    payload_v2 = {"available": True, "html": "<svg>v2</svg>", "errors": [], "title": "C001"}
    assert render_cache.cluster_cache_get("eis", "comparison", "C001", "sigBBB", "ctx", flags) is None
    render_cache.cluster_cache_put("eis", "comparison", "C001", "sigBBB", "ctx", flags, payload_v2)

    cluster_dir = render_cache._cluster_dir("eis", "comparison", "C001")
    remaining = sorted(p.name for p in cluster_dir.glob("*.json"))
    assert all(name.startswith("sigBBB__") for name in remaining)
    assert len(remaining) == 1


def test_eis_overlay_payload_renders_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    calls = {"n": 0}
    real_html = battery_ui.eis_overlay_html

    def counting_html(*args, **kwargs):
        calls["n"] += 1
        return real_html(*args, **kwargs)

    monkeypatch.setattr(battery_ui, "eis_overlay_html", counting_html)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", tmp_path / "ov.json")
    kwargs = dict(mode="comparison", key="", show_fit=False)

    p1 = viewer_service.eis_overlay_payload(*args, **kwargs)
    p2 = viewer_service.eis_overlay_payload(*args, **kwargs)
    assert calls["n"] == 1                 # second call served from cluster cache
    assert p1 == p2


def test_capacity_overlay_payload_renders_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BATTERY_OUTPUT_ROOT", tmp_path)
    battery_ui.parse_file_cached_by_mtime.cache_clear()

    calls = {"n": 0}
    real_html = battery_ui.capacity_overlay_html

    def counting_html(*args, **kwargs):
        calls["n"] += 1
        return real_html(*args, **kwargs)

    monkeypatch.setattr(battery_ui, "capacity_overlay_html", counting_html)

    args = (SAMPLE_DATA, SAMPLE_DATA, SAMPLE_DATA / "cell_conditions.csv", tmp_path / "ov.json")
    kwargs = dict(mode="cluster", key="")

    p1 = viewer_service.capacity_overlay_payload(*args, **kwargs)
    p2 = viewer_service.capacity_overlay_payload(*args, **kwargs)
    assert calls["n"] == 1
    assert p1 == p2
