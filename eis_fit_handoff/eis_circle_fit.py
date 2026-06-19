"""
EIS Nyquist first-arc circle fitting utilities.

Purpose
-------
Given EIS columns Z' and Z'', this module calculates Rs/Rct-like metadata
from the first Nyquist semicircle.

Important convention
--------------------
Nyquist plot uses:
    x = Z'      [Ohm]
    y = -Z''    [Ohm]
Do NOT rescale x/y for fitting. The display may use any aspect ratio, but
fitting must use the physical Ohm-vs-Ohm coordinates.

Recommended usage inside the app
--------------------------------
1. Parser returns numeric arrays: z_real, z_imag.
2. Call fit_eis_first_arc(z_real, z_imag).
3. Save the returned dict as a sidecar metadata JSON.
4. Viewer reads that JSON immediately instead of refitting every time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np


@dataclass
class EISCircleFitResult:
    schema_version: str
    status: str
    warnings: list[str]

    rs_ohm: Optional[float]
    rct_ohm: Optional[float]
    x_left_intercept_ohm: Optional[float]
    x_right_intercept_ohm: Optional[float]

    center_x_ohm: Optional[float]
    center_y_ohm: Optional[float]
    radius_ohm: Optional[float]
    depression_angle_deg: Optional[float]

    observed_rs_ohm: Optional[float]
    segment_start_index: Optional[int]
    segment_end_index: Optional[int]
    segment_point_count: int

    rmse_ohm: Optional[float]
    normalized_rmse: Optional[float]
    computed_at_utc: str

    def to_dict(self) -> dict:
        return asdict(self)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail_result(message: str) -> EISCircleFitResult:
    return EISCircleFitResult(
        schema_version="eis_circle_fit_v1",
        status="fail",
        warnings=[message],
        rs_ohm=None,
        rct_ohm=None,
        x_left_intercept_ohm=None,
        x_right_intercept_ohm=None,
        center_x_ohm=None,
        center_y_ohm=None,
        radius_ohm=None,
        depression_angle_deg=None,
        observed_rs_ohm=None,
        segment_start_index=None,
        segment_end_index=None,
        segment_point_count=0,
        rmse_ohm=None,
        normalized_rmse=None,
        computed_at_utc=_now_utc_iso(),
    )


def _as_clean_arrays(
    z_real: Sequence[float] | np.ndarray,
    z_imag: Sequence[float] | np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    x = np.asarray(z_real, dtype=float)
    y = -np.asarray(z_imag, dtype=float)

    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    return x, y


def _moving_average(values: np.ndarray, window: int = 3) -> np.ndarray:
    if window <= 1 or len(values) < window:
        return values.copy()
    kernel = np.ones(window) / window
    pad_left = window // 2
    pad_right = window - 1 - pad_left
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _observed_x_axis_intercept(x: np.ndarray, y: np.ndarray, start: int = 0) -> Optional[float]:
    """
    Returns observed/interpolated high-frequency x-axis intercept near start.
    This is not always as stable as the fitted intercept, but it is useful as
    a QC/reference value.
    """
    if len(x) < 2:
        return None

    # Exact or near-exact zero first.
    scale = max(1.0, float(np.nanmax(np.abs(y))))
    tol = 1e-6 * scale
    for i in range(start, len(x)):
        if abs(y[i]) <= tol:
            return float(x[i])

    # First sign crossing after start.
    for i in range(start, len(x) - 1):
        y0, y1 = y[i], y[i + 1]
        if y0 == y1:
            continue
        if (y0 <= 0 <= y1) or (y0 >= 0 >= y1):
            t = -y0 / (y1 - y0)
            return float(x[i] + t * (x[i + 1] - x[i]))

    # Fallback: point closest to x-axis among early points.
    early_end = min(len(x), max(start + 5, int(len(x) * 0.2)))
    idx = start + int(np.argmin(np.abs(y[start:early_end])))
    return float(x[idx])


def find_first_arc_segment(x: np.ndarray, y: np.ndarray, min_points: int = 8) -> Tuple[int, int, list[str]]:
    """
    Automatically selects the first semicircle-like arc in sequential EIS order.

    Rules:
    - Remove initial inductive loop: start at first y >= 0 when possible.
    - Find first local maximum after start as the arc peak.
    - End at the first local minimum after the peak, which usually marks the
      transition from charge-transfer arc to diffusion/Warburg tail.
    - If no clean local minimum exists, use the lowest point after the peak.
    """
    warnings: list[str] = []
    n = len(x)
    if n < min_points:
        raise ValueError(f"not enough points: {n} < {min_points}")

    y_smooth = _moving_average(y, window=3)
    positive_scale = float(np.nanpercentile(np.abs(y_smooth), 80)) if n else 1.0
    y_tol = max(1e-9, positive_scale * 0.005)

    nonnegative = np.where(y_smooth >= -y_tol)[0]
    if len(nonnegative) == 0:
        start = int(np.argmin(np.abs(y_smooth)))
        warnings.append("No y>=0 point found; started at point closest to x-axis.")
    else:
        start = int(nonnegative[0])

    if start > 0:
        warnings.append("Initial negative-y inductive loop was excluded from fitting.")

    # First local maximum after start.
    peak = None
    for i in range(start + 1, n - 1):
        if y_smooth[i] >= y_smooth[i - 1] and y_smooth[i] > y_smooth[i + 1]:
            peak = i
            break

    if peak is None:
        search_end = max(start + min_points, int(n * 0.65))
        peak = start + int(np.argmax(y_smooth[start:search_end]))
        warnings.append("No clean local maximum found; used early global maximum.")

    # First local minimum after peak. This catches the beginning of Warburg tail.
    end = None
    for i in range(peak + 1, n - 1):
        if y_smooth[i] <= y_smooth[i - 1] and y_smooth[i] < y_smooth[i + 1]:
            end = i
            break

    if end is None:
        # Fallback: choose minimum after peak. If the curve just descends without
        # a visible tail, this is usually the right endpoint of the arc.
        tail_start = peak + 1
        if tail_start >= n:
            raise ValueError("peak occurs too late; cannot select right arc endpoint")
        end = tail_start + int(np.argmin(y_smooth[tail_start:]))
        warnings.append("No clean post-peak local minimum found; used minimum after peak.")

    # Guarantee enough points. If too narrow, expand end first, then start.
    if end - start + 1 < min_points:
        needed = min_points - (end - start + 1)
        end = min(n - 1, end + needed)
        if end - start + 1 < min_points:
            start = max(0, end - min_points + 1)
        warnings.append("Segment was expanded to satisfy minimum point count.")

    if end <= start:
        raise ValueError("invalid segment: end <= start")

    return start, end, warnings


def algebraic_circle_fit(x: np.ndarray, y: np.ndarray) -> Tuple[float, float, float]:
    """
    Fast Kasa-style algebraic circle fit for initial parameters.

    Circle equation:
        x^2 + y^2 + D*x + E*y + F = 0
    center = (-D/2, -E/2)
    radius = sqrt((D^2 + E^2)/4 - F)
    """
    A = np.column_stack([x, y, np.ones_like(x)])
    b = -(x**2 + y**2)
    D, E, F = np.linalg.lstsq(A, b, rcond=None)[0]
    xc = -D / 2.0
    yc = -E / 2.0
    r2 = (D * D + E * E) / 4.0 - F
    r = math.sqrt(max(float(r2), 0.0))
    return float(xc), float(yc), float(r)


def refine_circle_fit(x: np.ndarray, y: np.ndarray, *, warnings: list[str] | None = None) -> Tuple[float, float, float, float, float]:
    """
    Robust geometric circle fit.

    It minimizes radial distance error:
        sqrt((x-xc)^2 + (y-yc)^2) - r

    soft_l1 reduces influence of scattered outliers.
    """
    xc0, yc0, r0 = algebraic_circle_fit(x, y)
    if r0 <= 0:
        raise ValueError("initial radius is zero or invalid")

    def residual(params: np.ndarray) -> np.ndarray:
        xc, yc, r = params
        return np.sqrt((x - xc) ** 2 + (y - yc) ** 2) - r

    try:
        from scipy.optimize import least_squares
    except ModuleNotFoundError:
        if warnings is not None:
            warnings.append("SciPy is not installed; used algebraic circle fit fallback.")
        radial_error = residual(np.array([xc0, yc0, r0], dtype=float))
        rmse = float(math.sqrt(np.mean(radial_error**2)))
        nrmse = float(rmse / r0) if r0 > 0 else float("inf")
        return xc0, yc0, r0, rmse, nrmse

    result = least_squares(
        residual,
        x0=np.array([xc0, yc0, r0], dtype=float),
        bounds=([-np.inf, -np.inf, 1e-12], [np.inf, np.inf, np.inf]),
        loss="soft_l1",
        f_scale=max(0.05, 0.02 * r0),
        max_nfev=300,
    )

    xc, yc, r = [float(v) for v in result.x]
    radial_error = residual(result.x)
    rmse = float(math.sqrt(np.mean(radial_error**2)))
    nrmse = float(rmse / r) if r > 0 else float("inf")
    return xc, yc, r, rmse, nrmse


def _intercepts_from_circle(xc: float, yc: float, r: float) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    disc = r * r - yc * yc
    if disc <= 0:
        return None, None, None, None
    half_width = math.sqrt(disc)
    x_left = xc - half_width
    x_right = xc + half_width
    rs = x_left
    rct = x_right - x_left
    return float(rs), float(rct), float(x_left), float(x_right)


def fit_eis_first_arc(
    z_real: Sequence[float] | np.ndarray,
    z_imag: Sequence[float] | np.ndarray,
    *,
    min_points: int = 8,
) -> EISCircleFitResult:
    """
    Main entry point for app integration.

    Parameters
    ----------
    z_real:
        Z' values in Ohm.
    z_imag:
        Z'' values in Ohm. Internally converted to y=-Z''.

    Returns
    -------
    EISCircleFitResult
        Contains Rs/Rct, circle center/radius, fitting quality and warnings.
    """
    try:
        x, y = _as_clean_arrays(z_real, z_imag)
        if len(x) < min_points:
            return _fail_result(f"not enough valid numeric points: {len(x)}")

        start, end, warnings = find_first_arc_segment(x, y, min_points=min_points)
        x_fit = x[start : end + 1]
        y_fit = y[start : end + 1]

        xc, yc, r, rmse, nrmse = refine_circle_fit(x_fit, y_fit, warnings=warnings)
        rs, rct, x_left, x_right = _intercepts_from_circle(xc, yc, r)
        observed_rs = _observed_x_axis_intercept(x, y, start=0)

        status = "ok"
        if nrmse > 0.05:
            status = "warn"
            warnings.append("normalized_rmse > 0.05; fit quality is low or tail may be included.")
        elif nrmse > 0.02:
            status = "warn"
            warnings.append("normalized_rmse > 0.02; inspect fit visually if this value is important.")

        if rs is None or rct is None:
            status = "warn"
            warnings.append("Fitted circle does not cross x-axis; Rs/Rct intercept values are unavailable.")

        if rct is not None and rct < 0:
            status = "warn"
            warnings.append("Calculated Rct is negative; check data order and selected arc.")

        angle = None
        if r > 0 and abs(yc / r) <= 1:
            angle = float(math.degrees(math.asin(yc / r)))

        return EISCircleFitResult(
            schema_version="eis_circle_fit_v1",
            status=status,
            warnings=warnings,
            rs_ohm=rs,
            rct_ohm=rct,
            x_left_intercept_ohm=x_left,
            x_right_intercept_ohm=x_right,
            center_x_ohm=float(xc),
            center_y_ohm=float(yc),
            radius_ohm=float(r),
            depression_angle_deg=angle,
            observed_rs_ohm=observed_rs,
            segment_start_index=int(start),
            segment_end_index=int(end),
            segment_point_count=int(len(x_fit)),
            rmse_ohm=float(rmse),
            normalized_rmse=float(nrmse),
            computed_at_utc=_now_utc_iso(),
        )

    except Exception as exc:
        return _fail_result(str(exc))


def file_signature(path: str | Path, *, full_hash: bool = False) -> dict:
    """
    Lightweight signature for cache invalidation.

    full_hash=False is much faster for large datasets: it hashes first/last 1MB
    plus size and mtime. For strict reproducibility use full_hash=True.
    """
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256()
    block_size = 1024 * 1024

    with p.open("rb") as f:
        if full_hash or st.st_size <= 2 * block_size:
            while True:
                chunk = f.read(block_size)
                if not chunk:
                    break
                h.update(chunk)
        else:
            h.update(f.read(block_size))
            f.seek(max(0, st.st_size - block_size))
            h.update(f.read(block_size))

    return {
        "path": str(p),
        "name": p.name,
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "hash_mode": "full_sha256" if full_hash or st.st_size <= 2 * block_size else "head_tail_sha256",
        "sha256": h.hexdigest(),
    }


def sidecar_path_for(source_path: str | Path) -> Path:
    """
    Adjacent sidecar: sample.xlsx -> sample.xlsx.eisfit.json
    This keeps the metadata file traveling with the raw data file.
    """
    p = Path(source_path)
    return p.with_name(p.name + ".eisfit.json")


def save_fit_metadata(source_path: str | Path, fit_result: EISCircleFitResult, *, extra: Optional[dict] = None) -> Path:
    source = file_signature(source_path, full_hash=False)
    fit = fit_result.to_dict()
    metadata = {
        "parser_version": "eis_circle_fit_v1",
        "source_path": source.get("path"),
        "source_size": source.get("size_bytes"),
        "source_mtime": source.get("mtime_ns"),
        "start_offset": extra.get("start_offset") if extra else None,
        "point_count": extra.get("point_count") if extra else fit.get("segment_point_count"),
        "created_at": fit.get("computed_at_utc"),
        "fit_success": fit_result.status in {"ok", "warn"} and fit_result.rs_ohm is not None and fit_result.rct_ohm is not None,
        "Rs": fit_result.rs_ohm,
        "Rct": fit_result.rct_ohm,
        "fit_rmse": fit_result.rmse_ohm,
        "fit_warning": "; ".join(fit_result.warnings),
        "source": source,
        "fit": fit,
    }
    if extra:
        metadata["extra"] = extra

    out = sidecar_path_for(source_path)
    out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_valid_fit_metadata(source_path: str | Path) -> Optional[dict]:
    p = Path(source_path)
    sidecar = sidecar_path_for(p)
    if not sidecar.exists():
        return None

    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        current = file_signature(p, full_hash=False)
        saved = metadata.get("source", {})
        keys = ["size_bytes", "mtime_ns", "sha256"]
        if all(saved.get(k) == current.get(k) for k in keys):
            return metadata
        return None
    except Exception:
        return None
