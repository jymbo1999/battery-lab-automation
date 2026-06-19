from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from .file_io import ANALYSIS_CAPACITY, ANALYSIS_EIS, ANALYSIS_SHEET, ANALYSIS_VOLTAGE
from .metrics import to_float
from .models import ParsedDataset

try:
    from eis_fit_handoff.eis_circle_fit import load_valid_fit_metadata
except ModuleNotFoundError:  # pragma: no cover
    load_valid_fit_metadata = None


COLORS = ["#111111", "#f4a742", "#ef4444", "#c0448f", "#4b238f", "#f7c9cf", "#f87171"]
VOLTAGE_CYCLE_COLORS = {"1": "#f2b777", "2": "#ff5a5f", "10": "#c43c9b", "20": "#5a2ca0"}


def write_dataset_plot(dataset: ParsedDataset, output_dir: Path) -> Path | None:
    plot_dir = output_dir / dataset.meta.analysis_type
    plot_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_path_for_dataset(dataset, output_dir)
    svg = dataset_svg(dataset)
    if not svg:
        return None
    target.write_text(svg, encoding="utf-8")
    write_artifact_metadata(dataset, target)
    return target


def artifact_path_for_dataset(dataset: ParsedDataset, output_dir: Path) -> Path:
    plot_dir = output_dir / dataset.meta.analysis_type
    source_hash = source_path_hash(dataset.meta.path)
    return plot_dir / f"{safe_name(dataset.meta.cell_id)}__{safe_name(dataset.meta.original_filename)}__{source_hash}.svg"


def source_path_hash(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8", errors="replace")).hexdigest()[:10]


def write_artifact_metadata(dataset: ParsedDataset, artifact_path: Path) -> Path:
    source = dataset.meta.path
    stat = source.stat() if source.exists() else None
    fit_metadata = load_valid_fit_metadata(source) if dataset.meta.analysis_type == ANALYSIS_EIS and load_valid_fit_metadata is not None else None
    fit = fit_metadata.get("fit", {}) if fit_metadata else {}
    payload = {
        "parser_version": "battery_lab_parser_v2",
        "source_path": str(source),
        "source_size": int(stat.st_size) if stat else None,
        "source_mtime": stat.st_mtime if stat else None,
        "start_offset": fit_metadata.get("extra", {}).get("start_offset") if fit_metadata else None,
        "point_count": len(dataset.rows),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_path": str(artifact_path),
        "analysis_type": dataset.meta.analysis_type,
        "source_format": source.suffix.lower().lstrip("."),
        "fit_success": fit.get("status") in {"ok", "warn"} if fit else False,
        "Rs": fit.get("rs_ohm"),
        "Rct": fit.get("rct_ohm"),
        "fit_rmse": fit.get("rmse_ohm"),
        "fit_warning": "; ".join(fit.get("warnings", [])) if fit else "",
    }
    meta_path = artifact_path.with_name(artifact_path.name + ".meta.json")
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta_path


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
    metadata = load_valid_fit_metadata(dataset.meta.path) if load_valid_fit_metadata is not None else None
    return eis_fit_svg(f"{dataset.meta.cell_id} Nyquist plot", points, metadata)


def eis_fit_svg(
    title: str,
    points: list[tuple[float, float]],
    metadata: dict[str, Any] | None,
    width: int = 760,
    height: int = 460,
    equal_aspect: bool = False,
    show_last_label: bool = False,
) -> str:
    if not points:
        return ""
    fit = metadata.get("fit", {}) if metadata else {}
    start = fit.get("segment_start_index")
    end = fit.get("segment_end_index")
    segment = points[start : end + 1] if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end else []
    xc = to_float(fit.get("center_x_ohm"))
    yc = to_float(fit.get("center_y_ohm"))
    radius = to_float(fit.get("radius_ohm"))
    x_left = to_float(fit.get("x_left_intercept_ohm"))
    x_right = to_float(fit.get("x_right_intercept_ohm"))
    circle_points: list[tuple[float, float]] = []
    if xc is not None and yc is not None and radius is not None and radius > 0:
        circle_points = [
            (xc + radius * math.cos(2 * math.pi * idx / 180), yc + radius * math.sin(2 * math.pi * idx / 180))
            for idx in range(181)
        ]
    intercept_points = [(x, 0.0) for x in (x_left, x_right) if x is not None]

    all_points = points + segment + circle_points + intercept_points
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    x_min, x_max = pad_range(min(xs), max(xs))
    y_min, y_max = pad_range(min(ys), max(ys))
    left, right, top, bottom = 76, 30, 48, 66
    plot_w = width - left - right
    plot_h = height - top - bottom
    if equal_aspect:
        x_min, x_max, y_min, y_max = equal_ohm_range(x_min, x_max, y_min, y_max, plot_w, plot_h)

    def sx(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    def path_for(items: list[tuple[float, float]]) -> str:
        return " ".join(("M" if idx == 0 else "L") + f" {sx(x):.2f} {sy(y):.2f}" for idx, (x, y) in enumerate(items))

    items = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<style>.eis-dot{opacity:.42}.fit-segment{opacity:.9}.fit-circle{opacity:.72}.fit-marker{stroke:#111;stroke-width:1.1}</style>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="29" font-family="Arial" font-size="18" font-weight="700">{html.escape(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#111" stroke-width="1.2"/>',
        f'<text x="{left + plot_w / 2}" y="{height - 20}" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">Z&apos; (ohm)</text>',
        f'<text x="18" y="{top + plot_h / 2}" transform="rotate(-90 18 {top + plot_h / 2})" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">-Z&apos;&apos; (ohm)</text>',
    ]
    if y_min <= 0 <= y_max:
        y0 = sy(0)
        items.append(f'<line x1="{left}" y1="{y0:.1f}" x2="{left + plot_w}" y2="{y0:.1f}" stroke="#999" stroke-dasharray="4 4"/>')
    for tick in range(6):
        x_value = x_min + (x_max - x_min) * tick / 5
        y_value = y_min + (y_max - y_min) * tick / 5
        x = sx(x_value)
        y = sy(y_value)
        items.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#eeeeee"/>')
        items.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#eeeeee"/>')
        items.append(f'<text x="{x:.1f}" y="{top + plot_h + 18}" font-family="Arial" font-size="10" text-anchor="middle">{x_value:.3g}</text>')
        items.append(f'<text x="{left - 8}" y="{y + 3:.1f}" font-family="Arial" font-size="10" text-anchor="end">{y_value:.3g}</text>')
    for x, y in points:
        items.append(f'<circle class="eis-dot" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="1.35" fill="#111111"/>')
    if segment:
        items.append(f'<path class="fit-segment" d="{path_for(segment)}" fill="none" stroke="#ef4444" stroke-width="2.1"/>')
        for x, y in segment:
            items.append(f'<circle cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="2.1" fill="#ef4444" opacity=".78"/>')
    if circle_points:
        items.append(f'<path class="fit-circle" d="{path_for(circle_points)}" fill="none" stroke="#2563eb" stroke-width="1.7"/>')
    for x, y in intercept_points:
        items.append(f'<circle class="fit-marker" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="4.2" fill="#f4a742"/>')
    if fit:
        status = str(fit.get("status") or "unknown")
        lines = [
            f"status: {status}",
            f"Rs: {format_ohm(fit.get('rs_ohm'))}",
            f"Rct: {format_ohm(fit.get('rct_ohm'))}",
            f"center: ({format_ohm(fit.get('center_x_ohm'))}, {format_ohm(fit.get('center_y_ohm'))})",
            f"radius: {format_ohm(fit.get('radius_ohm'))}",
            f"depression: {format_number(fit.get('depression_angle_deg'))} deg",
            f"nRMSE: {format_number(fit.get('normalized_rmse'))}",
        ]
        box_x, box_y = left + plot_w - 222, top + 14
        box_h = 20 + len(lines) * 17
        items.append(f'<rect x="{box_x}" y="{box_y}" width="210" height="{box_h}" rx="5" fill="#ffffff" stroke="#cccccc" opacity=".94"/>')
        for idx, line in enumerate(lines):
            items.append(f'<text x="{box_x + 10}" y="{box_y + 22 + idx * 17}" font-family="Arial" font-size="11" fill="#111">{html.escape(line)}</text>')
    if show_last_label and points:
        last_x, last_y = points[-1]
        label = f"Rs {format_ohm(fit.get('rs_ohm'))} · Rct {format_ohm(fit.get('rct_ohm'))}" if fit else "Rs null · Rct null"
        label_x = sx(last_x)
        label_y = sy(last_y) - 14
        label_w = min(230, max(126, len(label) * 6.0))
        items.append(
            f'<rect x="{label_x - label_w / 2:.1f}" y="{label_y - 16:.1f}" width="{label_w:.1f}" height="20" rx="4" fill="#fff7ed" stroke="#d6a454" opacity=".95"/>'
        )
        items.append(
            f'<text x="{label_x:.1f}" y="{label_y - 2:.1f}" font-family="Arial" font-size="11" font-weight="700" text-anchor="middle">{html.escape(label)}</text>'
        )
    items.append("</svg>")
    return "\n".join(items)


def equal_ohm_range(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    plot_w: float,
    plot_h: float,
) -> tuple[float, float, float, float]:
    x_center = (x_min + x_max) / 2
    y_center = (y_min + y_max) / 2
    x_span = max(1e-12, x_max - x_min)
    y_span = max(1e-12, y_max - y_min)
    ohm_per_pixel = max(x_span / plot_w, y_span / plot_h)
    half_x = ohm_per_pixel * plot_w / 2
    half_y = ohm_per_pixel * plot_h / 2
    return x_center - half_x, x_center + half_x, y_center - half_y, y_center + half_y


def format_ohm(value: Any) -> str:
    number = to_float(value)
    return "null" if number is None else f"{number:.4g} ohm"


def format_number(value: Any) -> str:
    number = to_float(value)
    return "null" if number is None else f"{number:.4g}"


def voltage_svg(dataset: ParsedDataset) -> str:
    by_cycle_step: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in dataset.rows:
        cycle = normalize_cycle(row.get("cycle") or "1")
        direction = str(row.get("direction") or "").lower()
        capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
        voltage = to_float(row.get("voltage"))
        if capacity is not None and voltage is not None:
            by_cycle_step.setdefault((cycle, direction), []).append((capacity, voltage))
    cycles = select_voltage_cycles([cycle for cycle, _ in by_cycle_step])
    series = []
    for idx, cycle in enumerate(cycles):
        legend = cycle_legend(cycle)
        color = VOLTAGE_CYCLE_COLORS.get(cycle, COLORS[idx % len(COLORS)])
        directions = sorted(
            (direction for grouped_cycle, direction in by_cycle_step if grouped_cycle == cycle),
            key=direction_sort_key,
        )
        for direction in directions:
            suffix = f" {direction}" if direction else ""
            series.append((f"{legend}{suffix}", by_cycle_step[(cycle, direction)], color, legend))
    return multi_line_svg(
        f"{dataset.meta.cell_id} Voltage profile",
        series,
        "Specific Capacity [mAh/g]",
        "Voltage [V]",
        hide_markers=True,
        legend_top=True,
        title_right=True,
    )


def sheet_svg(dataset: ParsedDataset) -> str:
    points = []
    for idx, row in enumerate(dataset.rows, start=1):
        resistance = to_float(row.get("sheet_resistance"))
        if resistance is not None:
            points.append((idx, resistance))
    return multi_line_svg(f"{dataset.meta.cell_id} 면저항", [("ohm/sq", points)], "Point", "ohm/sq", scatter=True)


def multi_line_svg(
    title: str,
    series: list[
        tuple[str, list[tuple[float, float]]]
        | tuple[str, list[tuple[float, float]], str]
        | tuple[str, list[tuple[float, float]], str, str]
    ],
    x_label: str,
    y_label: str,
    scatter: bool = False,
    hide_markers: bool = False,
    legend_top: bool = False,
    title_right: bool = False,
    width: int = 760,
    height: int = 420,
) -> str:
    normalized_series = []
    for idx, item in enumerate(series):
        name, points = item[0], item[1]
        color = item[2] if len(item) > 2 else COLORS[idx % len(COLORS)]
        legend = item[3] if len(item) > 3 else name
        if points:
            normalized_series.append((name, sorted(points), color, legend))
    series = normalized_series
    if not series:
        return ""
    all_points = [point for _, points, _, _ in series for point in points]
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
        '<style>.data-path{opacity:.72;transition:opacity .12s,stroke-width .12s}.data-dot{opacity:.46;transition:opacity .12s,r .12s,stroke-width .12s}.series-group:hover .data-path{opacity:1;stroke-width:1.6}.series-group:hover .data-dot{opacity:.95;r:2.2;stroke-width:.8}</style>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left + plot_w if title_right else left}" y="28" text-anchor="{"end" if title_right else "start"}" font-family="Arial" font-size="18" font-weight="700">{html.escape(title)}</text>',
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
    legend_seen: set[str] = set()
    legend_idx = 0
    for idx, (name, points, color, legend) in enumerate(series):
        path = " ".join(("M" if point_idx == 0 else "L") + f" {sx(x):.2f} {sy(y):.2f}" for point_idx, (x, y) in enumerate(points))
        items.append('<g class="series-group">')
        if not scatter:
            items.append(f'<path class="data-path" d="{path}" fill="none" stroke="{color}" stroke-width="{"1.1" if hide_markers else "0.8"}"/>')
        if not hide_markers:
            for x, y in points:
                items.append(f'<circle class="data-dot" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="1.15" fill="{color}" opacity="0.46"/>')
        items.append("</g>")
        if legend in legend_seen:
            continue
        legend_seen.add(legend)
        legend_x = left + plot_w * 0.32 if legend_top else left + 16 + legend_idx * 132
        legend_y = 72 + legend_idx * 20 if legend_top else height - 40
        items.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 24}" y2="{legend_y}" stroke="{color}" stroke-width="0.9"/>')
        items.append(f'<text x="{legend_x + 31}" y="{legend_y + 4}" font-family="Arial" font-size="12" font-weight="700">{html.escape(legend)}</text>')
        legend_idx += 1
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
        '<style>.data-path{opacity:.72;transition:opacity .12s,stroke-width .12s}.data-dot{opacity:.46;transition:opacity .12s,r .12s,stroke-width .12s}.series-group:hover .data-path{opacity:1;stroke-width:1.45}.series-group:hover .data-dot{opacity:.95;r:2.2;stroke-width:.8}</style>',
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
        items.append('<g class="series-group">')
        items.append(f'<path class="data-path" d="{path}" fill="none" stroke="{color}" stroke-width="0.8"/>')
        for point_idx, (x, y) in enumerate(points):
            if point_idx % max(1, len(points) // 130) == 0:
                items.append(f'<circle class="data-dot" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="1.15" fill="#ffffff" stroke="{color}" stroke-width="0.45"/>')
        items.append("</g>")
        legend_x = left + 12 + idx * 140
        items.append(f'<line x1="{legend_x}" y1="{height - 42}" x2="{legend_x + 24}" y2="{height - 42}" stroke="{color}" stroke-width="1.8"/>')
        items.append(f'<circle cx="{legend_x + 12}" cy="{height - 42}" r="3.5" fill="#ffffff" stroke="{color}" stroke-width="1.4"/>')
        items.append(f'<text x="{legend_x + 30}" y="{height - 38}" font-family="Arial" font-size="11">{html.escape(name)}</text>')
    if ce:
        ce_path = " ".join(("M" if point_idx == 0 else "L") + f" {sx(x):.2f} {sy_ce(y):.2f}" for point_idx, (x, y) in enumerate(ce))
        items.append('<g class="series-group">')
        items.append(f'<path class="data-path" d="{ce_path}" fill="none" stroke="#003cff" stroke-width="0.85"/>')
        for point_idx, (x, y) in enumerate(ce):
            if point_idx % max(1, len(ce) // 130) == 0:
                items.append(f'<circle class="data-dot" cx="{sx(x):.2f}" cy="{sy_ce(y):.2f}" r="1.15" fill="#ffffff" stroke="#003cff" stroke-width="0.45"/>')
        items.append("</g>")
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


def normalize_cycle(value: object) -> str:
    raw = str(value).strip()
    numeric = to_float(raw)
    if numeric is not None and float(numeric).is_integer():
        return str(int(numeric))
    return raw


def cycle_sort_key(value: str) -> tuple[float, str]:
    numeric = to_float(value)
    if numeric is None:
        return (float("inf"), value)
    return (numeric, value)


def direction_sort_key(value: str) -> tuple[int, str]:
    order = {"charge": 0, "discharge": 1, "ch": 0, "dis": 1}
    return (order.get(value, 2), value)


def select_voltage_cycles(cycles: list[str], limit: int = 6) -> list[str]:
    unique_cycles = sorted(set(cycles), key=cycle_sort_key)
    selected = [cycle for cycle in ["1", "2", "10", "20"] if cycle in unique_cycles]
    for cycle in unique_cycles:
        if cycle not in selected:
            selected.append(cycle)
        if len(selected) >= limit:
            break
    return selected[:limit]


def cycle_legend(cycle: str) -> str:
    numeric = to_float(cycle)
    if numeric is None or not float(numeric).is_integer():
        return f"{cycle} cycle"
    cycle_no = int(numeric)
    if 10 <= cycle_no % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(cycle_no % 10, "th")
    return f"{cycle_no}{suffix} cycle"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
