from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .file_io import ANALYSIS_CAPACITY, ANALYSIS_EIS, ANALYSIS_SHEET, ANALYSIS_VOLTAGE
from .metrics import to_float
from .models import ParsedDataset


COLORS = ["#111111", "#f4a742", "#ef4444", "#c0448f", "#4b238f", "#f7c9cf", "#f87171"]


def write_dataset_plot(dataset: ParsedDataset, output_dir: Path) -> Path | None:
    plot_dir = output_dir / dataset.meta.analysis_type
    plot_dir.mkdir(parents=True, exist_ok=True)
    target = plot_dir / f"{safe_name(dataset.meta.cell_id)}__{safe_name(dataset.meta.original_filename)}.svg"
    svg = dataset_svg(dataset)
    if not svg:
        return None
    target.write_text(svg, encoding="utf-8")
    return target


def dataset_svg(dataset: ParsedDataset) -> str:
    if dataset.meta.analysis_type == ANALYSIS_CAPACITY:
        return capacity_svg(dataset)
    if dataset.meta.analysis_type == ANALYSIS_EIS:
        return eis_svg(dataset)
    if dataset.meta.analysis_type == ANALYSIS_VOLTAGE:
        return voltage_svg(dataset)
    if dataset.meta.analysis_type == ANALYSIS_SHEET:
        return sheet_svg(dataset)
    return ""


def capacity_svg(dataset: ParsedDataset) -> str:
    charge = []
    discharge = []
    ce = []
    for row in dataset.rows:
        cycle = to_float(row.get("cycle"))
        chg = to_float(row.get("charge_capacity"))
        dchg = to_float(row.get("discharge_capacity"))
        if cycle is None:
            continue
        if chg is not None:
            charge.append((cycle, chg))
        if dchg is not None:
            discharge.append((cycle, dchg))
        if dchg and chg is not None:
            ce.append((cycle, chg / dchg * 100))
    return capacity_dual_axis_svg(
        title=f"{dataset.meta.cell_id} 용량 + CE",
        charge=charge,
        discharge=discharge,
        ce=ce,
    )


def eis_svg(dataset: ParsedDataset) -> str:
    points = []
    for row in dataset.rows:
        x = to_float(row.get("z_real"))
        y_raw = to_float(row.get("z_imag"))
        if x is not None and y_raw is not None:
            points.append((x, -y_raw))
    return multi_line_svg(
        title=f"{dataset.meta.cell_id} Nyquist plot",
        series=[("-Z''", points)],
        x_label="Z' (ohm)",
        y_label="-Z'' (ohm)",
        scatter=True,
    )


def voltage_svg(dataset: ParsedDataset) -> str:
    by_cycle: dict[str, list[tuple[float, float]]] = {}
    for row in dataset.rows:
        cycle = str(row.get("cycle") or "1")
        capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
        voltage = to_float(row.get("voltage"))
        if capacity is not None and voltage is not None:
            by_cycle.setdefault(cycle, []).append((capacity, voltage))
    series = [(f"Cycle {cycle}", points) for cycle, points in sorted(by_cycle.items())[:6]]
    return multi_line_svg(f"{dataset.meta.cell_id} Voltage profile", series, "Capacity", "Voltage")


def sheet_svg(dataset: ParsedDataset) -> str:
    points = []
    for idx, row in enumerate(dataset.rows, start=1):
        resistance = to_float(row.get("sheet_resistance"))
        if resistance is not None:
            points.append((idx, resistance))
    return multi_line_svg(f"{dataset.meta.cell_id} 면저항", [("ohm/sq", points)], "Point", "ohm/sq", scatter=True)


def multi_line_svg(
    title: str,
    series: list[tuple[str, list[tuple[float, float]]]],
    x_label: str,
    y_label: str,
    scatter: bool = False,
    width: int = 760,
    height: int = 420,
) -> str:
    series = [(name, sorted(points)) for name, points in series if points]
    if not series:
        return ""
    all_points = [point for _, points in series for point in points]
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    x_min, x_max = pad_range(min(xs), max(xs))
    y_min, y_max = pad_range(min(ys), max(ys))
    left, right, top, bottom = 72, 28, 48, 62
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    items = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="28" font-family="Arial" font-size="18" font-weight="700">{html.escape(title)}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>',
        f'<text x="{left + plot_w / 2}" y="{height - 18}" font-family="Arial" font-size="12" text-anchor="middle">{html.escape(x_label)}</text>',
        f'<text x="18" y="{top + plot_h / 2}" transform="rotate(-90 18 {top + plot_h / 2})" font-family="Arial" font-size="12" text-anchor="middle">{html.escape(y_label)}</text>',
    ]
    for tick in range(6):
        x_value = x_min + (x_max - x_min) * tick / 5
        y_value = y_min + (y_max - y_min) * tick / 5
        x = sx(x_value)
        y = sy(y_value)
        items.append(f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" y2="{top + plot_h + 5}" stroke="#555"/>')
        items.append(f'<text x="{x:.1f}" y="{top + plot_h + 20}" font-family="Arial" font-size="10" text-anchor="middle">{x_value:.3g}</text>')
        items.append(f'<line x1="{left - 5}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" stroke="#555"/>')
        items.append(f'<text x="{left - 9}" y="{y + 3:.1f}" font-family="Arial" font-size="10" text-anchor="end">{y_value:.3g}</text>')
    for idx, (name, points) in enumerate(series):
        color = COLORS[idx % len(COLORS)]
        path = " ".join(("M" if point_idx == 0 else "L") + f" {sx(x):.2f} {sy(y):.2f}" for point_idx, (x, y) in enumerate(points))
        if not scatter:
            items.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        for x, y in points:
            items.append(f'<circle cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="3" fill="{color}" opacity="0.82"/>')
        legend_x = left + 16 + idx * 132
        items.append(f'<circle cx="{legend_x}" cy="{height - 40}" r="5" fill="{color}"/>')
        items.append(f'<text x="{legend_x + 10}" y="{height - 36}" font-family="Arial" font-size="11">{html.escape(name)}</text>')
    items.append("</svg>")
    return "\n".join(items)


def capacity_dual_axis_svg(
    title: str,
    charge: list[tuple[float, float]],
    discharge: list[tuple[float, float]],
    ce: list[tuple[float, float]],
    width: int = 760,
    height: int = 440,
) -> str:
    series = [("Charge", charge, "#f4a742"), ("Discharge", discharge, "#111111")]
    series = [(name, sorted(points), color) for name, points, color in series if points]
    ce = sorted(ce)
    if not series and not ce:
        return ""
    all_points = [point for _, points, _ in series for point in points] + ce
    xs = [point[0] for point in all_points]
    capacity_points = [point for _, points, _ in series for point in points]
    cap_y = [point[1] for point in capacity_points] or [0, 1]
    ce_y = [point[1] for point in ce] or [0, 100]
    x_min, x_max = pad_range(min(xs), max(xs))
    y_min, y_max = pad_range(min(cap_y), max(cap_y))
    ce_min_raw, ce_max_raw = pad_range(min(ce_y), max(ce_y))
    ce_min, ce_max = min(0, ce_min_raw), max(100, ce_max_raw)
    left, right, top, bottom = 76, 76, 50, 66
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    def sy_ce(y: float) -> float:
        return top + plot_h - (y - ce_min) / (ce_max - ce_min) * plot_h

    items = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="30" font-family="Arial" font-size="18" font-weight="700">{html.escape(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#111" stroke-width="1.4"/>',
        f'<text x="{left + plot_w / 2}" y="{height - 18}" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">Cycle</text>',
        f'<text x="18" y="{top + plot_h / 2}" transform="rotate(-90 18 {top + plot_h / 2})" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">Specific Capacity [mAh/g]</text>',
        f'<text x="{width - 18}" y="{top + plot_h / 2}" transform="rotate(90 {width - 18} {top + plot_h / 2})" font-family="Arial" font-size="12" font-weight="700" fill="#003cff" text-anchor="middle">Coulombic Efficiency (%)</text>',
    ]
    for tick in range(6):
        x_value = x_min + (x_max - x_min) * tick / 5
        y_value = y_min + (y_max - y_min) * tick / 5
        ce_value = ce_min + (ce_max - ce_min) * tick / 5
        x = sx(x_value)
        y = sy(y_value)
        y_right = sy_ce(ce_value)
        items.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#eeeeee"/>')
        items.append(f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" y2="{top + plot_h + 5}" stroke="#111"/>')
        items.append(f'<text x="{x:.1f}" y="{top + plot_h + 20}" font-family="Arial" font-size="10" text-anchor="middle">{x_value:.3g}</text>')
        items.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#eeeeee"/>')
        items.append(f'<line x1="{left - 5}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" stroke="#111"/>')
        items.append(f'<text x="{left - 9}" y="{y + 3:.1f}" font-family="Arial" font-size="10" text-anchor="end">{y_value:.3g}</text>')
        items.append(f'<line x1="{left + plot_w}" y1="{y_right:.1f}" x2="{left + plot_w + 5}" y2="{y_right:.1f}" stroke="#003cff"/>')
        items.append(f'<text x="{left + plot_w + 9}" y="{y_right + 3:.1f}" font-family="Arial" font-size="10" fill="#003cff">{ce_value:.3g}</text>')
    for idx, (name, points, color) in enumerate(series):
        path = " ".join(("M" if point_idx == 0 else "L") + f" {sx(x):.2f} {sy(y):.2f}" for point_idx, (x, y) in enumerate(points))
        items.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        for point_idx, (x, y) in enumerate(points):
            if point_idx % max(1, len(points) // 130) == 0:
                items.append(f'<circle cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="3" fill="#ffffff" stroke="{color}" stroke-width="1.4"/>')
        legend_x = left + 12 + idx * 140
        items.append(f'<line x1="{legend_x}" y1="{height - 42}" x2="{legend_x + 24}" y2="{height - 42}" stroke="{color}" stroke-width="1.8"/>')
        items.append(f'<circle cx="{legend_x + 12}" cy="{height - 42}" r="3.5" fill="#ffffff" stroke="{color}" stroke-width="1.4"/>')
        items.append(f'<text x="{legend_x + 30}" y="{height - 38}" font-family="Arial" font-size="11">{html.escape(name)}</text>')
    if ce:
        ce_path = " ".join(("M" if point_idx == 0 else "L") + f" {sx(x):.2f} {sy_ce(y):.2f}" for point_idx, (x, y) in enumerate(ce))
        items.append(f'<path d="{ce_path}" fill="none" stroke="#003cff" stroke-width="2"/>')
        for point_idx, (x, y) in enumerate(ce):
            if point_idx % max(1, len(ce) // 130) == 0:
                items.append(f'<circle cx="{sx(x):.2f}" cy="{sy_ce(y):.2f}" r="3" fill="#ffffff" stroke="#003cff" stroke-width="1.4"/>')
        legend_x = left + 292
        items.append(f'<line x1="{legend_x}" y1="{height - 42}" x2="{legend_x + 24}" y2="{height - 42}" stroke="#003cff" stroke-width="2"/>')
        items.append(f'<circle cx="{legend_x + 12}" cy="{height - 42}" r="3.5" fill="#ffffff" stroke="#003cff" stroke-width="1.4"/>')
        items.append(f'<text x="{legend_x + 30}" y="{height - 38}" font-family="Arial" font-size="11" fill="#003cff">CE %</text>')
    items.append("</svg>")
    return "\n".join(items)


def pad_range(low: float, high: float) -> tuple[float, float]:
    if low == high:
        margin = abs(low) * 0.1 or 1.0
        return low - margin, high + margin
    margin = (high - low) * 0.08
    return low - margin, high + margin


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
