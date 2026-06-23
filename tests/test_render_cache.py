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
