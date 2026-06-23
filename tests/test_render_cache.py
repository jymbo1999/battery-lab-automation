from pathlib import Path

from battery_lab import config, render_cache


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
