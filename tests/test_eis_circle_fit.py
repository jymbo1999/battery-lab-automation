import builtins

import numpy as np

from eis_fit_handoff.eis_circle_fit import fit_eis_first_arc


def semicircle_points():
    rs = 2.0
    rct = 24.0
    radius = rct / 2.0
    xc = rs + radius
    theta = np.linspace(np.pi, 0, 30)
    x = xc + radius * np.cos(theta)
    y = radius * np.sin(theta)
    return rs, rct, x, -y


def test_perfect_semicircle():
    rs, rct, x, z_imag = semicircle_points()

    result = fit_eis_first_arc(x, z_imag)

    assert result.status in {"ok", "warn"}
    assert result.rs_ohm is not None
    assert result.rct_ohm is not None
    assert abs(result.rs_ohm - rs) < 0.1
    assert abs(result.rct_ohm - rct) < 0.2


def test_perfect_semicircle_uses_algebraic_fallback_without_scipy(monkeypatch):
    original_import = builtins.__import__

    def import_without_scipy(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "scipy.optimize":
            raise ModuleNotFoundError("No module named 'scipy'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_scipy)
    rs, rct, x, z_imag = semicircle_points()

    result = fit_eis_first_arc(x, z_imag)

    assert result.status in {"ok", "warn"}
    assert result.rs_ohm is not None
    assert result.rct_ohm is not None
    assert abs(result.rs_ohm - rs) < 0.1
    assert abs(result.rct_ohm - rct) < 0.2
    assert "algebraic circle fit fallback" in " ".join(result.warnings)
