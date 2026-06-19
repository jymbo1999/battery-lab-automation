import numpy as np

from eis_fit_handoff.eis_circle_fit import fit_eis_first_arc


def test_perfect_semicircle():
    rs = 2.0
    rct = 24.0
    radius = rct / 2.0
    xc = rs + radius
    theta = np.linspace(np.pi, 0, 30)
    x = xc + radius * np.cos(theta)
    y = radius * np.sin(theta)
    z_imag = -y

    result = fit_eis_first_arc(x, z_imag)

    assert result.status in {"ok", "warn"}
    assert result.rs_ohm is not None
    assert result.rct_ohm is not None
    assert abs(result.rs_ohm - rs) < 0.1
    assert abs(result.rct_ohm - rct) < 0.2
