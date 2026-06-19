from __future__ import annotations

import math
import re
from statistics import mean, pstdev
from typing import Any

from .file_io import ANALYSIS_CAPACITY, ANALYSIS_EIS, ANALYSIS_SHEET, ANALYSIS_VOLTAGE
from .models import MetricRecord, ParsedDataset


def compute_metrics(dataset: ParsedDataset) -> MetricRecord:
    analysis = dataset.meta.analysis_type
    if analysis == ANALYSIS_CAPACITY:
        metrics = capacity_metrics(dataset.rows, source_name=dataset.meta.original_filename)
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


CE_CHARGE_OVER_DISCHARGE = "charge_over_discharge"
CE_DISCHARGE_OVER_CHARGE = "discharge_over_charge"


def capacity_metrics(
    rows: list[dict[str, Any]],
    ce_formula: str = CE_CHARGE_OVER_DISCHARGE,
    source_name: str = "",
) -> dict[str, Any]:
    points = []
    for row in rows:
        cycle = to_float(row.get("cycle"))
        charge = to_float(row.get("charge_capacity"))
        discharge = to_float(row.get("discharge_capacity"))
        if cycle is None or charge is None or discharge is None:
            continue
        ce = coulombic_efficiency(charge, discharge, ce_formula)
        c_rate = normalize_c_rate(row.get("c_rate") or row.get("rate") or row.get("current_rate"))
        points.append({"cycle": cycle, "charge": charge, "discharge": discharge, "ce": ce})
        if c_rate:
            points[-1]["c_rate"] = c_rate
    if not points:
        return {"rows": len(rows), "valid_points": 0}
    points.sort(key=lambda item: item["cycle"])
    first = points[0]
    last = points[-1]
    discharges = [p["discharge"] for p in points]
    ce_values = [p["ce"] for p in points if p["ce"] is not None]
    stabilized_ce_values = [p["ce"] for p in points if p["ce"] is not None and p["cycle"] >= 3] or ce_values
    protocol = classify_capacity_protocol(points, source_name)
    rate_metrics = rate_performance_metrics(points, protocol)
    metrics = {
        "rows": len(rows),
        "valid_points": len(points),
        "protocol": protocol,
        "ce_formula": ce_formula,
        "ice_percent": round(first["ce"], 3) if first["ce"] is not None else "",
        "ce_1st": round(first["ce"], 3) if first["ce"] is not None else "",
        "first_charge_capacity": round(first["charge"], 4),
        "first_discharge_capacity": round(first["discharge"], 4),
        "max_discharge_capacity": round(max(discharges), 4),
        "last_discharge_capacity": round(last["discharge"], 4),
        "cycle_count": len({p["cycle"] for p in points}),
        "last_cycle": int(last["cycle"]) if float(last["cycle"]).is_integer() else last["cycle"],
        "ce_mean": round(mean(ce_values), 4) if ce_values else "",
        "ce_std": round(pstdev(ce_values), 4) if len(ce_values) > 1 else 0,
        "ce_mean_after_stabilization": round(mean(stabilized_ce_values), 4) if stabilized_ce_values else "",
        "ce_std_after_stabilization": round(pstdev(stabilized_ce_values), 4) if len(stabilized_ce_values) > 1 else 0,
        "ce_min": round(min(ce_values), 4) if ce_values else "",
        "ce_anomaly_count": ce_anomaly_count(ce_values),
        "fade_slope": round(linear_slope([p["cycle"] for p in points], discharges), 6),
        "cycle_to_80": cycle_to_threshold(points, 80.0),
    }
    for target in (10, 50, 100, 300):
        metrics[f"retention@{target}"] = retention_at(points, target)
    metrics.update(rate_metrics)
    return metrics


def voltage_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_cycle_step: dict[float, dict[str, list[tuple[float, float]]]] = {}
    for row in rows:
        cycle = to_float(row.get("cycle")) or 1.0
        step = normalize_step(row.get("direction") or row.get("step") or row.get("mode"))
        capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
        voltage = to_float(row.get("voltage"))
        if capacity is None or voltage is None:
            continue
        by_cycle_step.setdefault(cycle, {}).setdefault(step, []).append((capacity, voltage))
    if not by_cycle_step:
        return {"rows": len(rows), "valid_points": 0}
    cycles = sorted(by_cycle_step)
    metrics: dict[str, Any] = {
        "rows": len(rows),
        "valid_points": sum(len(points) for steps in by_cycle_step.values() for points in steps.values()),
        "profile_available_cycles": ",".join(format_cycle(cycle) for cycle in cycles),
    }
    first_discharge_capacity = profile_capacity(by_cycle_step[cycles[0]].get("discharge", []))
    for cycle in cycles[:8]:
        steps = by_cycle_step[cycle]
        all_points = [point for points in steps.values() for point in points]
        capacities = [item[0] for item in all_points]
        key = int(cycle) if float(cycle).is_integer() else cycle
        charge_points = steps.get("charge", [])
        discharge_points = steps.get("discharge", [])
        charge_cap = profile_capacity(charge_points)
        discharge_cap = profile_capacity(discharge_points)
        metrics[f"profile_capacity_{key}"] = round(max(capacities) - min(capacities), 4) if capacities else ""
        metrics[f"charge_profile_capacity_{key}"] = round(charge_cap, 4) if charge_cap is not None else ""
        metrics[f"discharge_profile_capacity_{key}"] = round(discharge_cap, 4) if discharge_cap is not None else ""
        metrics[f"end_charge_voltage_{key}"] = round(charge_points[-1][1], 4) if charge_points else ""
        metrics[f"end_discharge_voltage_{key}"] = round(discharge_points[-1][1], 4) if discharge_points else ""
        hysteresis = hysteresis_metrics(charge_points, discharge_points)
        metrics[f"mean_hysteresis_{key}"] = hysteresis["mean"]
        metrics[f"hysteresis_at_q50_{key}"] = hysteresis["q50"]
        if first_discharge_capacity and discharge_cap is not None:
            metrics[f"capacity_loss_vs_first_{key}"] = round((1 - discharge_cap / first_discharge_capacity) * 100, 3)
    return metrics


def coulombic_efficiency(charge: float, discharge: float, ce_formula: str) -> float | None:
    if ce_formula == CE_DISCHARGE_OVER_CHARGE:
        return discharge / charge * 100 if charge else None
    return charge / discharge * 100 if discharge else None


def ce_anomaly_count(ce_values: list[float]) -> int:
    return sum(1 for value in ce_values if value < 90 or value > 110)


def normalize_c_rate(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().lower().replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)c", text)
    if not match:
        return ""
    number = float(match.group(1))
    if number.is_integer():
        return f"{int(number)}C"
    return f"{str(number).replace('.', 'p')}C"


def classify_capacity_protocol(points: list[dict[str, Any]], source_name: str = "") -> str:
    lowered = source_name.lower()
    if "low" in lowered and "temp" in lowered:
        return "LOWTEMP_0p1C" if "0.1c" in lowered else "LOW_TEMP"
    if re.search(r"\bcv\b", lowered):
        return "CV"
    if "rate" in lowered or "per" in lowered:
        return "RATE_PERFORMANCE"
    rates = [str(point.get("c_rate") or "") for point in points if point.get("c_rate")]
    unique_rates = list(dict.fromkeys(rates))
    if len(unique_rates) >= 3:
        return "RATE_PERFORMANCE"
    if len(unique_rates) == 1:
        return f"LONG_{unique_rates[0].replace('.', 'p')}"
    if len(unique_rates) == 2 and unique_rates[0] == "0p1C" and unique_rates[1] == "0p5C":
        return "STABILIZE_THEN_0p5C"
    for token, label in (("0.1c", "LONG_0p1C"), ("0.5c", "LONG_0p5C"), ("1c", "LONG_1C")):
        if token in lowered:
            return label
    return "UNKNOWN"


def rate_performance_metrics(points: list[dict[str, Any]], protocol: str) -> dict[str, Any]:
    segments = c_rate_segments(points, protocol)
    if not segments:
        return {}
    output: dict[str, Any] = {}
    for rate, values in segments.items():
        if values:
            output[f"capacity@{rate}"] = round(mean(values), 4)
    base = output.get("capacity@0p1C") or output.get("capacity@0p5C")
    high_rate = output.get("capacity@2C") or output.get("capacity@1p5C") or output.get("capacity@1C")
    if base and high_rate:
        output["rate_retention_high_vs_base"] = round(high_rate / base * 100, 3)
    recovery = recovery_after_rate(segments)
    if recovery is not None:
        output["recovery_after_rate"] = recovery
    return output


def c_rate_segments(points: list[dict[str, Any]], protocol: str) -> dict[str, list[float]]:
    explicit: dict[str, list[float]] = {}
    for point in points:
        rate = point.get("c_rate")
        if rate:
            explicit.setdefault(str(rate), []).append(point["discharge"])
    if explicit:
        return explicit
    if protocol != "RATE_PERFORMANCE" or len(points) < 10:
        return {}
    rates = ["0p1C", "0p5C", "1C", "1p5C", "2C", "0p5C_recovery"]
    segment_size = max(1, len(points) // len(rates))
    segments: dict[str, list[float]] = {}
    for idx, point in enumerate(points):
        rate_idx = min(idx // segment_size, len(rates) - 1)
        segments.setdefault(rates[rate_idx], []).append(point["discharge"])
    return segments


def recovery_after_rate(segments: dict[str, list[float]]) -> float | None:
    recovery = segments.get("0p5C_recovery") or segments.get("0p1C_recovery")
    reference = segments.get("0p5C") or segments.get("0p1C")
    if not recovery or not reference:
        return None
    ref_mean = mean(reference)
    if not ref_mean:
        return None
    return round(mean(recovery) / ref_mean * 100, 3)


def normalize_step(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "dis" in text or "dchg" in text:
        return "discharge"
    if "ch" in text:
        return "charge"
    return "profile"


def profile_capacity(points: list[tuple[float, float]]) -> float | None:
    if not points:
        return None
    capacities = [point[0] for point in points]
    return max(capacities) - min(capacities)


def hysteresis_metrics(charge_points: list[tuple[float, float]], discharge_points: list[tuple[float, float]]) -> dict[str, Any]:
    if len(charge_points) < 2 or len(discharge_points) < 2:
        return {"mean": "", "q50": ""}
    charge = sorted(charge_points)
    discharge = sorted(discharge_points)
    low = max(charge[0][0], discharge[0][0])
    high = min(charge[-1][0], discharge[-1][0])
    if high <= low:
        return {"mean": "", "q50": ""}
    samples = [low + (high - low) * idx / 10 for idx in range(1, 10)]
    diffs = []
    for capacity in samples:
        charge_v = interpolate_voltage(charge, capacity)
        discharge_v = interpolate_voltage(discharge, capacity)
        if charge_v is not None and discharge_v is not None:
            diffs.append(abs(charge_v - discharge_v))
    q50 = low + (high - low) * 0.5
    q50_charge = interpolate_voltage(charge, q50)
    q50_discharge = interpolate_voltage(discharge, q50)
    return {
        "mean": round(mean(diffs), 4) if diffs else "",
        "q50": round(abs(q50_charge - q50_discharge), 4) if q50_charge is not None and q50_discharge is not None else "",
    }


def interpolate_voltage(points: list[tuple[float, float]], capacity: float) -> float | None:
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 == x1:
            continue
        if min(x0, x1) <= capacity <= max(x0, x1):
            ratio = (capacity - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return None


def format_cycle(cycle: float) -> str:
    return str(int(cycle)) if float(cycle).is_integer() else str(cycle)


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
