from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from .file_io import ANALYSIS_CAPACITY, ANALYSIS_EIS, ANALYSIS_SHEET, ANALYSIS_VOLTAGE
from .models import MetricRecord, ParsedDataset


def compute_metrics(dataset: ParsedDataset) -> MetricRecord:
    analysis = dataset.meta.analysis_type
    if analysis == ANALYSIS_CAPACITY:
        metrics = capacity_metrics(dataset.rows)
    elif analysis == ANALYSIS_EIS:
        metrics = eis_metrics(dataset.rows)
    elif analysis == ANALYSIS_VOLTAGE:
        metrics = voltage_metrics(dataset.rows)
    elif analysis == ANALYSIS_SHEET:
        metrics = sheet_resistance_metrics(dataset.rows)
    else:
        metrics = {"rows": len(dataset.rows)}
    warning = dataset.meta.warning
    if not dataset.rows:
        warning = (warning + " " if warning else "") + "No parseable rows."
    return MetricRecord(dataset.meta.cell_id, analysis, dataset.meta.original_filename, metrics, warning.strip())


def capacity_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for row in rows:
        cycle = to_float(row.get("cycle"))
        charge = to_float(row.get("charge_capacity"))
        discharge = to_float(row.get("discharge_capacity"))
        if cycle is None or charge is None or discharge is None:
            continue
        ce = charge / discharge * 100 if discharge else None
        points.append({"cycle": cycle, "charge": charge, "discharge": discharge, "ce": ce})
    if not points:
        return {"rows": len(rows), "valid_points": 0}
    points.sort(key=lambda item: item["cycle"])
    first = points[0]
    last = points[-1]
    discharges = [p["discharge"] for p in points]
    ce_values = [p["ce"] for p in points if p["ce"] is not None]
    metrics = {
        "rows": len(rows),
        "valid_points": len(points),
        "ice_percent": round(first["ce"], 3) if first["ce"] is not None else "",
        "first_discharge_capacity": round(first["discharge"], 4),
        "max_discharge_capacity": round(max(discharges), 4),
        "last_discharge_capacity": round(last["discharge"], 4),
        "last_cycle": int(last["cycle"]) if float(last["cycle"]).is_integer() else last["cycle"],
        "ce_mean": round(mean(ce_values), 4) if ce_values else "",
        "ce_std": round(pstdev(ce_values), 4) if len(ce_values) > 1 else 0,
        "fade_slope": round(linear_slope([p["cycle"] for p in points], discharges), 6),
        "cycle_to_80": cycle_to_threshold(points, 80.0),
    }
    for target in (50, 100, 300):
        metrics[f"retention@{target}"] = retention_at(points, target)
    return metrics


def voltage_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_cycle: dict[float, list[tuple[float, float]]] = {}
    for row in rows:
        cycle = to_float(row.get("cycle")) or 1.0
        capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
        voltage = to_float(row.get("voltage"))
        if capacity is None or voltage is None:
            continue
        by_cycle.setdefault(cycle, []).append((capacity, voltage))
    if not by_cycle:
        return {"rows": len(rows), "valid_points": 0}
    metrics: dict[str, Any] = {"rows": len(rows), "valid_points": sum(len(v) for v in by_cycle.values())}
    for cycle in sorted(by_cycle)[:8]:
        capacities = [item[0] for item in by_cycle[cycle]]
        key = int(cycle) if float(cycle).is_integer() else cycle
        metrics[f"profile_capacity_{key}"] = round(max(capacities) - min(capacities), 4)
    return metrics


def eis_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for row in rows:
        z_real = to_float(row.get("z_real"))
        z_imag_raw = to_float(row.get("z_imag"))
        if z_real is None or z_imag_raw is None:
            continue
        neg_z_imag = -z_imag_raw
        points.append((z_real, neg_z_imag))
    if not points:
        return {"rows": len(rows), "valid_points": 0}
    rs_point = min(points, key=lambda item: (abs(item[1]), item[0]))
    rs = rs_point[0]
    max_real = max(point[0] for point in points)
    rct = max(0.0, max_real - rs)
    circle = circle_fit(points)
    metrics = {
        "rows": len(rows),
        "valid_points": len(points),
        "rs_auto": round(rs, 5),
        "rct_auto": round(circle["diameter"] if circle else rct, 5),
        "rct_span": round(rct, 5),
        "semicircle_quality": round(circle["quality"], 4) if circle else "",
        "fitting_method": "circle_rough" if circle else "span_rough",
    }
    return metrics


def sheet_resistance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [value for value in (to_float(row.get("sheet_resistance")) for row in rows) if value is not None]
    if not values:
        return {"rows": len(rows), "valid_points": 0}
    avg = mean(values)
    std = pstdev(values) if len(values) > 1 else 0.0
    return {
        "rows": len(rows),
        "valid_points": len(values),
        "mean_sheet_resistance": round(avg, 5),
        "std_sheet_resistance": round(std, 5),
        "cv_percent": round((std / avg * 100) if avg else 0, 4),
        "outlier_count": count_outliers(values),
    }


def retention_at(points: list[dict[str, float]], target_cycle: int) -> Any:
    first = points[0]["discharge"]
    if not first:
        return ""
    closest = min(points, key=lambda item: abs(item["cycle"] - target_cycle))
    if abs(closest["cycle"] - target_cycle) > 0.5:
        return ""
    return round(closest["discharge"] / first * 100, 3)


def cycle_to_threshold(points: list[dict[str, float]], threshold_percent: float) -> Any:
    first = points[0]["discharge"]
    if not first:
        return ""
    for point in points:
        if point["discharge"] / first * 100 <= threshold_percent:
            return int(point["cycle"]) if float(point["cycle"]).is_integer() else point["cycle"]
    return ""


def linear_slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    x_avg = mean(xs)
    y_avg = mean(ys)
    denom = sum((x - x_avg) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, ys)) / denom


def circle_fit(points: list[tuple[float, float]]) -> dict[str, float] | None:
    if len(points) < 5:
        return None
    rows = []
    rhs = []
    for x, y in points:
        rows.append([x, y, 1.0])
        rhs.append(-(x * x + y * y))
    ata = [[sum(row[i] * row[j] for row in rows) for j in range(3)] for i in range(3)]
    atb = [sum(row[i] * value for row, value in zip(rows, rhs)) for i in range(3)]
    solved = solve_3x3(ata, atb)
    if solved is None:
        return None
    a, b, c = solved
    center_x = -a / 2
    center_y = -b / 2
    radius_sq = center_x * center_x + center_y * center_y - c
    if radius_sq <= 0:
        return None
    radius = math.sqrt(radius_sq)
    residuals = [abs(math.hypot(x - center_x, y - center_y) - radius) for x, y in points]
    avg_residual = mean(residuals)
    quality = max(0.0, 1.0 - avg_residual / radius) if radius else 0.0
    return {"diameter": radius * 2, "quality": quality}


def solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    a = [row[:] + [vector[idx]] for idx, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [value / scale for value in a[col]]
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            a[row] = [value - factor * pivot_value for value, pivot_value in zip(a[row], a[col])]
    return [a[row][3] for row in range(3)]


def count_outliers(values: list[float]) -> int:
    if len(values) < 4:
        return 0
    avg = mean(values)
    std = pstdev(values)
    if std == 0:
        return 0
    return sum(1 for value in values if abs(value - avg) > 2.5 * std)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = __import__("re").search(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", text)
    return float(match.group(0)) if match else None
