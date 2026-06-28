from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .conditions import (
    build_analysis_availability,
    build_analysis_comparison_validations,
    build_comparison_candidates,
    compatibility_notes,
    find_condition,
)
from .insights import daily_summary, record_insights
from .metrics import to_float
from .models import MetricRecord, ParsedDataset

try:
    from eis_fit_handoff.eis_circle_fit import load_valid_fit_metadata
except ModuleNotFoundError:  # pragma: no cover
    load_valid_fit_metadata = None


CONDITION_LABELS = {
    "sample": "Sample",
    "batch": "Batch",
    "cell_no": "Cell No.",
    "date": "Date",
    "areal_mass_density": "Areal mass density (mg/cm2)",
    "electrode_density": "합제밀도 (g/cm3)",
    "electrolyte": "전해질",
    "binder": "Binder",
    "voltage_range": "Voltage range",
    "ratio": "Ratio",
    "note": "메모",
}

ANALYSIS_LABELS = {
    "capacity": "Capacity",
    "voltage_profile": "Voltage profile",
    "eis": "EIS",
    "sheet_resistance": "면저항",
    "raman": "Raman",
    "tga": "TGA",
    "unknown": "미분류",
}

VOLTAGE_CYCLE_COLORS = {
    "1": "#f2b777",
    "2": "#ff5a5f",
    "10": "#c43c9b",
    "20": "#5a2ca0",
}

VOLTAGE_FALLBACK_COLORS = ["#f2b777", "#ff5a5f", "#c43c9b", "#5a2ca0", "#2f7d6b", "#2f5fb8"]


def write_interactive_dashboard(
    datasets: list[ParsedDataset],
    records: list[MetricRecord],
    path: Path,
    conditions: dict[str, dict[str, object]] | None = None,
) -> None:
    payload = build_payload(datasets, records, conditions or {})
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    document = DASHBOARD_TEMPLATE.replace("__BATTERY_DASHBOARD_DATA__", data_json)
    path.write_text(document, encoding="utf-8")


def build_payload(
    datasets: list[ParsedDataset],
    records: list[MetricRecord],
    conditions: dict[str, dict[str, object]],
) -> dict[str, Any]:
    dataset_by_file = {dataset.meta.original_filename: dataset for dataset in datasets}
    cards = []
    for idx, record in enumerate(records):
        dataset = dataset_by_file.get(record.source_file)
        condition = find_condition(record.cell_id, conditions)
        cards.append(
            {
                "id": idx,
                "cell_id": record.cell_id,
                "analysis_type": record.analysis_type,
                "analysis_label": ANALYSIS_LABELS.get(record.analysis_type, record.analysis_type),
                "source_file": record.source_file,
                "warning": record.warning,
                "metrics": record.metrics,
                "condition": condition,
                "insights": record_insights(record),
                "series": extract_series(dataset) if dataset else {},
            }
        )
    return {
        "cards": cards,
        "summary": daily_summary(records),
        "compatibility": compatibility_notes([record.cell_id for record in records], conditions),
        "comparisonCandidates": [asdict(row) for row in build_comparison_candidates([record.cell_id for record in records], conditions)],
        "analysisValidations": [asdict(row) for row in build_analysis_comparison_validations(records, conditions)],
        "availability": [asdict(row) for row in build_analysis_availability(datasets, conditions)],
        "conditionLabels": CONDITION_LABELS,
        "analysisLabels": ANALYSIS_LABELS,
    }


def extract_series(dataset: ParsedDataset) -> dict[str, Any]:
    if dataset.meta.analysis_type == "capacity":
        charge, discharge, ce, retention = [], [], [], []
        first_discharge: float | None = None
        for row in dataset.rows:
            cycle = to_float(row.get("cycle"))
            chg = to_float(row.get("charge_capacity"))
            dchg = to_float(row.get("discharge_capacity"))
            if cycle is None:
                continue
            if chg is not None:
                charge.append([cycle, chg])
            if dchg is not None:
                discharge.append([cycle, dchg])
                if first_discharge is None and dchg:
                    first_discharge = dchg
                if first_discharge:
                    retention.append([cycle, dchg / first_discharge * 100])
            if dchg and chg is not None:
                ce.append([cycle, chg / dchg * 100])
        return {
            "xLabel": "Cycle",
            "yLabel": "Specific Capacity [mAh/g]",
            "lines": [
                {"name": "Charge", "points": downsample(charge)},
                {"name": "Discharge", "points": downsample(discharge)},
                {"name": "CE", "points": downsample(ce), "axis": "right", "yLabel": "Coulombic Efficiency (%)"},
                {"name": "Retention", "points": downsample(retention), "axis": "right", "yLabel": "Retention (%)"},
            ],
        }
    if dataset.meta.analysis_type == "eis":
        points = []
        for row in dataset.rows:
            x = to_float(row.get("z_real"))
            y_raw = to_float(row.get("z_imag"))
            if x is not None and y_raw is not None:
                points.append([x, -y_raw])
        metadata = load_valid_fit_metadata(dataset.meta.path) if load_valid_fit_metadata is not None else None
        return {
            "xLabel": "Z' (Ohm)",
            "yLabel": "-Z'' (Ohm)",
            "chartKind": "eis-fit",
            "fitMetadata": metadata,
            "lines": [{"name": "Nyquist", "points": downsample(points), "rawPoints": points, "fitMetadata": metadata}],
        }
    if dataset.meta.analysis_type == "voltage_profile":
        grouped: dict[tuple[str, str], list[list[float]]] = {}
        for row in dataset.rows:
            cycle = normalize_cycle(row.get("cycle") or "1")
            direction = str(row.get("direction") or "").lower()
            capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
            voltage = to_float(row.get("voltage"))
            if capacity is not None and voltage is not None:
                grouped.setdefault((cycle, direction), []).append([capacity, voltage])
        selected_cycles = select_voltage_cycles([cycle for cycle, _ in grouped])
        lines = []
        for cycle_idx, cycle in enumerate(selected_cycles):
            legend = cycle_legend(cycle)
            color = voltage_cycle_color(cycle, cycle_idx)
            directions = sorted(
                (direction for grouped_cycle, direction in grouped if grouped_cycle == cycle),
                key=direction_sort_key,
            )
            for direction in directions:
                points = grouped[(cycle, direction)]
                suffix = f" {direction}" if direction else ""
                lines.append(
                    {
                        "name": f"{legend}{suffix}",
                        "legend": legend,
                        "cycle": cycle,
                        "color": color,
                        "points": downsample(sorted(points), 260),
                        "hideMarkers": True,
                        "lineWidth": 1.1,
                    }
                )
        return {
            "xLabel": "Specific Capacity [mAh/g]",
            "yLabel": "Voltage [V]",
            "legendPosition": "top-center",
            "titleAlign": "right",
            "chartKind": "voltage-profile",
            "lines": lines,
        }
    if dataset.meta.analysis_type == "sheet_resistance":
        points = []
        for idx, row in enumerate(dataset.rows, start=1):
            resistance = to_float(row.get("sheet_resistance"))
            if resistance is not None:
                points.append([idx, resistance])
        return {"xLabel": "Point", "yLabel": "Ohm/sq", "lines": [{"name": "면저항", "points": points}]}
    return {}


def downsample(points: list[list[float]], limit: int = 420) -> list[list[float]]:
    if len(points) <= limit:
        return points
    step = max(1, len(points) // limit)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


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
    preferred = [cycle for cycle in ["1", "2", "10", "20"] if cycle in unique_cycles]
    for cycle in unique_cycles:
        if cycle not in preferred:
            preferred.append(cycle)
        if len(preferred) >= limit:
            break
    return preferred[:limit]


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


def voltage_cycle_color(cycle: str, idx: int) -> str:
    return VOLTAGE_CYCLE_COLORS.get(cycle, VOLTAGE_FALLBACK_COLORS[idx % len(VOLTAGE_FALLBACK_COLORS)])


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>배터리 실험 대시보드</title>
  <style>
    * { box-sizing: border-box; }
    :root {
      color-scheme: dark;
      --bg: #101418;
      --panel: rgba(255,255,255,0.055);
      --panel-2: rgba(255,255,255,0.085);
      --line: rgba(255,255,255,0.18);
      --ink: #f4f7fb;
      --muted: rgba(244,247,251,0.62);
      --soft: rgba(244,247,251,0.38);
      --accent: #5fc6d1;
      --hot: #ffb15e;
      --danger: #ff7777;
      --good: #79d38c;
    }
    body {
      margin: 0;
      background: radial-gradient(circle at top left, rgba(95,198,209,0.18), transparent 34rem), var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
      letter-spacing: 0;
    }
    button, input, select { font: inherit; }
    .shell { display: grid; grid-template-columns: 292px minmax(0, 1fr); min-height: 100vh; }
    aside {
      border-right: 1px solid var(--line);
      background: rgba(0,0,0,0.18);
      padding: 22px 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    main { padding: 22px 28px 42px; min-width: 0; }
    h1 { margin: 0; font-size: clamp(1.9rem, 3vw, 3.2rem); font-weight: 750; }
    h2 { margin: 0 0 12px; font-size: 1.15rem; }
    h3 { margin: 0 0 8px; font-size: 0.98rem; }
    .eyebrow { color: var(--accent); font-size: 0.8rem; font-weight: 700; letter-spacing: 0.16rem; text-transform: uppercase; margin-bottom: 8px; }
    .subtle { color: var(--muted); font-size: 0.92rem; line-height: 1.55; }
    .sidebar-title { font-size: 1.22rem; font-weight: 750; margin-bottom: 3px; }
    .control { margin-top: 18px; }
    label { display: block; color: var(--muted); font-size: 0.8rem; margin-bottom: 7px; }
    select, input[type="search"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(255,255,255,0.06);
      color: var(--ink);
      padding: 10px 11px;
      outline: none;
    }
    .hint-box {
      border: 1px solid rgba(95,198,209,0.28);
      background: rgba(95,198,209,0.08);
      border-radius: 8px;
      padding: 12px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.55;
    }
    .hero {
      border-bottom: 1px solid var(--line);
      padding: 10px 0 18px;
      margin-bottom: 18px;
    }
    .hero p { max-width: 920px; margin: 8px 0 0; color: var(--muted); line-height: 1.55; }
    .kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }
    .kpi, .card, .chart-panel, .table-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(0,0,0,0.16);
    }
    .kpi { padding: 14px; min-height: 86px; }
    .kpi span { display: block; color: var(--muted); font-size: 0.78rem; }
    .kpi strong { display: block; margin-top: 6px; font-size: 1.55rem; }
    .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }
    .tab {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.045);
      color: var(--muted);
      border-radius: 6px;
      padding: 9px 13px;
      cursor: pointer;
    }
    .tab.active { color: var(--ink); border-color: rgba(95,198,209,0.65); background: rgba(95,198,209,0.12); }
    .view { display: none; }
    .view.active { display: block; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .card { padding: 15px; cursor: pointer; min-height: 178px; }
    .card.active { outline: 2px solid rgba(95,198,209,0.75); }
    .card-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
    .badge { border-radius: 999px; padding: 3px 9px; font-size: 0.75rem; background: rgba(95,198,209,0.16); color: #9ee7ee; white-space: nowrap; }
    .file-name { margin: 8px 0; color: var(--soft); font-size: 0.8rem; overflow-wrap: anywhere; }
    .metric-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
    .mini { border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 8px; background: rgba(255,255,255,0.035); }
    .mini span { display: block; color: var(--soft); font-size: 0.72rem; }
    .mini strong { display: block; margin-top: 3px; font-size: 0.94rem; overflow-wrap: anywhere; }
    .layout-two { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.9fr); gap: 14px; align-items: start; }
    .chart-panel, .table-panel { padding: 16px; overflow: hidden; }
    .chart-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; }
    .line-width-control {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      margin: -2px 0 10px;
      color: var(--muted);
      font-size: 0.82rem;
    }
    .line-width-control input { width: min(220px, 38vw); accent-color: var(--accent); }
    .line-width-control strong { color: var(--ink); min-width: 48px; text-align: right; font-variant-numeric: tabular-nums; }
    .fit-toggle {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      margin: -2px 0 10px;
      color: var(--muted);
      font-size: 0.82rem;
    }
    .fit-toggle input { accent-color: var(--accent); width: 16px; height: 16px; }
    .stepper { display: flex; gap: 8px; }
    .stepper button {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.06);
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .chart { width: 100%; min-height: 430px; border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; background: #ffffff; overflow: hidden; }
    .chart svg .data-path { opacity: 0.72; transition: opacity 120ms ease, stroke-width 120ms ease; }
    .chart svg .data-dot { opacity: 0.46; transition: opacity 120ms ease, r 120ms ease, stroke-width 120ms ease; }
    .chart svg .series-group:hover .data-path { opacity: 1; stroke-width: var(--hover-stroke-width, 1.45); }
    .chart svg .series-group:hover .data-dot { opacity: 0.95; r: 2.2; stroke-width: 0.8; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { border-bottom: 1px solid rgba(255,255,255,0.10); padding: 8px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 650; width: 44%; }
    .note-list { margin: 0; padding-left: 18px; color: var(--muted); line-height: 1.58; }
    .compare-list { display: grid; gap: 8px; }
    .compare-item { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; background: rgba(255,255,255,0.04); color: var(--muted); }
    .compare-item.good { border-color: rgba(121,211,140,0.35); color: #b9efc3; }
    .compare-item.warn { border-color: rgba(255,177,94,0.38); color: #ffd3a1; }
    .compare-item.block { border-color: rgba(255,119,119,0.42); color: #ffc0c0; }
    .metric-summary { margin: 0 0 12px; border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; overflow: hidden; }
    .metric-summary table { margin: 0; }
    .metric-summary th, .metric-summary td { padding: 8px 9px; }
    .metric-summary th { width: auto; white-space: nowrap; }
    .availability-table td:nth-child(n+5), .availability-table th:nth-child(n+5) { text-align: center; }
    .status-ok { color: #b9efc3; font-weight: 750; }
    .status-missing { color: #ffc0c0; font-weight: 750; }
    .empty { padding: 24px; border: 1px dashed var(--line); border-radius: 8px; color: var(--muted); }
    @media (max-width: 1000px) {
      .shell { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      .kpis, .grid, .layout-two { grid-template-columns: 1fr; }
      main { padding: 18px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="sidebar-title">실험 필터</div>
      <div class="subtle">조건이 맞는 셀부터 고르고 그래프를 넘겨보는 화면입니다.</div>
      <div class="control">
        <label for="search">셀 / 파일 검색</label>
        <input id="search" type="search" placeholder="예: 1.5act, pure, EIS">
      </div>
      <div class="control">
        <label for="analysis">분석법</label>
        <select id="analysis"></select>
      </div>
      <div class="control">
        <label for="sample">Sample</label>
        <select id="sample"></select>
      </div>
      <div class="control">
        <label for="compat">비교 기준</label>
        <select id="compat">
          <option value="all">전체 표시</option>
          <option value="strict">Areal mass density ±0.5</option>
          <option value="loose">Areal mass density ±1.0</option>
        </select>
      </div>
      <div class="hint-box">
        PPT 기준: Areal mass density가 비슷하고 전해질, Binder, Voltage range, ratio가 같은 셀끼리 비교하는 것이 안전합니다.
      </div>
    </aside>
    <main>
      <section class="hero">
        <div class="eyebrow">BATTERY LAB BOARD</div>
        <h1>배터리 실험 대시보드</h1>
        <p>Capacity, Voltage profile, EIS 데이터를 셀 조건과 함께 넘겨보며 비교합니다. 자동 Rct/Rs는 빠른 screening용 값입니다.</p>
      </section>
      <section class="kpis" id="kpis"></section>
      <nav class="tabs">
        <button class="tab active" data-tab="overview">개요</button>
        <button class="tab" data-tab="explorer">셀 탐색</button>
        <button class="tab" data-tab="graph">그래프</button>
        <button class="tab" data-tab="availability">분석 가능 여부</button>
        <button class="tab" data-tab="compare">비교 가능성</button>
      </nav>
      <section class="view active" id="view-overview">
        <div class="layout-two">
          <div class="chart-panel">
            <div class="chart-toolbar">
              <h2>선택 그래프</h2>
              <div class="stepper">
                <button id="prev">이전</button>
                <button id="next">다음</button>
              </div>
            </div>
            <div class="line-width-control">
              <label for="voltageLineWidth">Voltage profile line</label>
              <input id="voltageLineWidth" data-voltage-line-width type="range" min="0.6" max="2.4" step="0.05" value="1.1">
              <strong data-voltage-line-width-value>1.10px</strong>
            </div>
            <label class="fit-toggle">
              <input id="showEisFit" data-eis-fit-toggle type="checkbox">
              <span>EIS fitting circle 1:1</span>
            </label>
            <div id="chart" class="chart"></div>
          </div>
          <div class="table-panel" id="detail"></div>
        </div>
      </section>
      <section class="view" id="view-explorer">
        <div class="grid" id="cards"></div>
      </section>
      <section class="view" id="view-graph">
        <div class="chart-panel">
          <div class="chart-toolbar">
            <h2>필터된 그래프 오버레이</h2>
            <span class="subtle" id="overlayCaption"></span>
          </div>
          <div class="line-width-control">
            <label for="overlayVoltageLineWidth">Voltage profile line</label>
            <input id="overlayVoltageLineWidth" data-voltage-line-width type="range" min="0.6" max="2.4" step="0.05" value="1.1">
            <strong data-voltage-line-width-value>1.10px</strong>
          </div>
          <label class="fit-toggle">
            <input id="overlayShowEisFit" data-eis-fit-toggle type="checkbox">
            <span>EIS fitting circle 1:1</span>
          </label>
          <div id="overlayChart" class="chart"></div>
        </div>
      </section>
      <section class="view" id="view-availability">
        <div class="table-panel">
          <h2>Analysis availability matrix</h2>
          <div id="availability"></div>
        </div>
      </section>
      <section class="view" id="view-compare">
        <div class="table-panel">
          <h2>비교 가능성 체크</h2>
          <div class="compare-list" id="compatibility"></div>
          <h2 style="margin-top:18px;">Capacity / Voltage protocol validation</h2>
          <div class="compare-list" id="analysisValidation"></div>
        </div>
      </section>
    </main>
  </div>
  <script id="dashboard-data" type="application/json">__BATTERY_DASHBOARD_DATA__</script>
  <script>
    const DATA = JSON.parse(document.getElementById('dashboard-data').textContent);
    const state = { tab: 'overview', selected: 0, voltageLineWidth: 1.1, showEisFit: false, filters: { search: '', analysis: 'all', sample: 'all', compat: 'all' } };
    const colors = ['#111111', '#f4a742', '#ef4444', '#c0448f', '#4b238f', '#f7c9cf', '#f87171', '#facc15'];
    const eisMarkers = ['square', 'circle', 'triangle-up', 'triangle-down', 'diamond', 'triangle-left', 'triangle-right', 'hexagon', 'star', 'pentagon', 'ring'];
    const $ = (id) => document.getElementById(id);
    const fmt = (v, digits = 3) => {
      if (v === null || v === undefined || v === '') return '-';
      const n = Number(v);
      return Number.isFinite(n) ? n.toFixed(digits).replace(/\.?0+$/, '') : String(v);
    };
    const unique = (arr) => [...new Set(arr.filter(Boolean))].sort();
    function init() {
      fillSelect('analysis', [['all','전체 분석법'], ...Object.entries(DATA.analysisLabels)]);
      fillSelect('sample', [['all','전체 Sample'], ...unique(DATA.cards.map(c => c.condition.sample || c.cell_id)).map(v => [v, v])]);
      ['search', 'analysis', 'sample', 'compat'].forEach(id => $(id).addEventListener('input', () => {
        state.filters[id] = $(id).value;
        const visible = filteredCards();
        if (!visible.find(c => c.id === state.selected) && visible.length) state.selected = visible[0].id;
        render();
      }));
      document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
        state.tab = btn.dataset.tab;
        render();
      }));
      document.querySelectorAll('[data-voltage-line-width]').forEach(input => input.addEventListener('input', () => {
        state.voltageLineWidth = Number(input.value);
        syncVoltageLineWidthControls();
        renderSelected();
        renderOverlay();
      }));
      document.querySelectorAll('[data-eis-fit-toggle]').forEach(input => input.addEventListener('change', () => {
        state.showEisFit = Boolean(input.checked);
        syncEisFitControls();
        renderSelected();
        renderOverlay();
      }));
      $('prev').addEventListener('click', () => stepCard(-1));
      $('next').addEventListener('click', () => stepCard(1));
      render();
    }
    function syncVoltageLineWidthControls() {
      document.querySelectorAll('[data-voltage-line-width]').forEach(input => { input.value = String(state.voltageLineWidth); });
      document.querySelectorAll('[data-voltage-line-width-value]').forEach(el => { el.textContent = `${state.voltageLineWidth.toFixed(2)}px`; });
    }
    function syncEisFitControls() {
      document.querySelectorAll('[data-eis-fit-toggle]').forEach(input => { input.checked = state.showEisFit; });
    }
    function fillSelect(id, pairs) {
      $(id).innerHTML = pairs.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
    }
    function filteredCards() {
      const q = state.filters.search.trim().toLowerCase();
      return DATA.cards.filter(card => {
        const hay = [card.cell_id, card.source_file, card.analysis_label, card.condition.sample, card.warning].join(' ').toLowerCase();
        if (q && !hay.includes(q)) return false;
        if (!analysisMatches(card, state.filters.analysis)) return false;
        if (state.filters.sample !== 'all' && (card.condition.sample || card.cell_id) !== state.filters.sample) return false;
        if (!compatibleWithSelected(card)) return false;
        return true;
      });
    }
    function analysisMatches(card, selectedAnalysis) {
      if (selectedAnalysis === 'all') return true;
      return card.analysis_type === selectedAnalysis;
    }
    function compatibleWithSelected(card) {
      if (state.filters.compat === 'all') return true;
      const reference = DATA.cards.find(c => c.id === state.selected) || DATA.cards[0];
      if (!reference) return true;
      if (reference.id === card.id) return true;
      const limit = state.filters.compat === 'strict' ? 0.5 : 1.0;
      const refAreal = Number(reference.condition.areal_mass_density);
      const cardAreal = Number(card.condition.areal_mass_density);
      if (!Number.isFinite(refAreal) || !Number.isFinite(cardAreal)) return false;
      if (Math.abs(refAreal - cardAreal) > limit) return false;
      return ['cell_type', 'electrolyte', 'binder', 'voltage_range', 'ratio'].every(key => sameCondition(reference, card, key));
    }
    function sameCondition(a, b, key) {
      const av = normalizeConditionValue(a.condition[key]);
      const bv = normalizeConditionValue(b.condition[key]);
      return Boolean(av && bv && av === bv);
    }
    function normalizeConditionValue(value) {
      return String(value ?? '').trim().toLowerCase();
    }
    function render() {
      document.querySelectorAll('.tab').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === state.tab));
      document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
      $(`view-${state.tab}`).classList.add('active');
      renderKpis();
      renderCards();
      syncVoltageLineWidthControls();
      syncEisFitControls();
      renderSelected();
      renderOverlay();
      renderAvailability();
      renderCompatibility();
      renderAnalysisValidation();
    }
    function renderKpis() {
      const cards = DATA.cards;
      const visible = filteredCards();
      const cellCount = unique(cards.map(c => c.cell_id)).length;
      const warnings = cards.filter(c => c.warning).length;
      const bestIce = cards.filter(c => c.metrics.ice_percent !== undefined && c.metrics.ice_percent !== '').sort((a,b) => Number(b.metrics.ice_percent) - Number(a.metrics.ice_percent))[0];
      $('kpis').innerHTML = [
        ['전체 파일', cards.length],
        ['셀 수', cellCount],
        ['현재 표시', visible.length],
        ['최고 ICE', bestIce ? `${fmt(bestIce.metrics.ice_percent)}%` : '-'],
      ].map(([label, value]) => `<div class="kpi"><span>${label}</span><strong>${value}</strong></div>`).join('') +
      `<div class="kpi"><span>확인 필요</span><strong>${warnings}</strong></div>`;
    }
    function renderCards() {
      const cards = filteredCards();
      $('cards').innerHTML = cards.length ? cards.map(card => cardHtml(card)).join('') : '<div class="empty">필터에 맞는 파일이 없습니다.</div>';
      document.querySelectorAll('[data-card-id]').forEach(el => el.addEventListener('click', () => {
        state.selected = Number(el.dataset.cardId);
        state.tab = 'overview';
        render();
      }));
    }
    function cardHtml(card) {
      return `<article class="card ${card.id === state.selected ? 'active' : ''}" data-card-id="${card.id}">
        <div class="card-head"><h3>${escapeHtml(card.cell_id)}</h3><span class="badge">${escapeHtml(card.analysis_label)}</span></div>
        <div class="file-name">${escapeHtml(card.source_file)}</div>
        <div class="subtle">${escapeHtml(card.condition.sample || '조건표 매칭 없음')}</div>
        <div class="metric-row">
          ${mini('Areal', card.condition.areal_mass_density)}
          ${mini('ICE', card.metrics.ice_percent ? `${fmt(card.metrics.ice_percent)}%` : '-')}
          ${mini('Rs', card.metrics.rs_auto)}
          ${mini('Rct', card.metrics.rct_auto)}
        </div>
      </article>`;
    }
    function mini(label, value) {
      return `<div class="mini"><span>${escapeHtml(label)}</span><strong>${escapeHtml(fmt(value))}</strong></div>`;
    }
    function selectedCard() {
      const visible = filteredCards();
      return visible.find(c => c.id === state.selected) || visible[0] || DATA.cards.find(c => c.id === state.selected) || DATA.cards[0];
    }
    function renderSelected() {
      const card = selectedCard();
      if (!card) return;
      if (card.analysis_type === 'eis') {
        const eisOverlay = buildEisOverlayData(DATA.cards, materialName(card));
        drawChart('chart', eisOverlay.series, eisOverlay.title);
      } else if (isCapacityComparisonType(card.analysis_type)) {
        const comparison = buildCapacityComparisonData(DATA.cards);
        drawChart('chart', comparison.series, comparison.title);
      } else {
        drawChart('chart', card.series, `${card.cell_id} · ${card.analysis_label}`);
      }
      const highlight = card.analysis_type === 'capacity'
        ? summaryTable(buildCapacityComparisonData(DATA.cards).items)
        : '';
      const conditionRows = Object.entries(DATA.conditionLabels).map(([key, label]) => {
        const value = card.condition[key];
        return value === undefined || value === '' ? '' : `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(fmt(value))}</td></tr>`;
      }).join('');
      const metricRows = Object.entries(card.metrics).map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(fmt(value))}</td></tr>`).join('');
      $('detail').innerHTML = `<h2>${escapeHtml(card.cell_id)}</h2>
        <p class="file-name">${escapeHtml(card.source_file)}</p>
        ${highlight}
        ${card.warning ? `<div class="compare-item warn">${escapeHtml(card.warning)}</div>` : ''}
        <h3>셀 조건</h3><table>${conditionRows || '<tr><td>조건표 매칭 없음</td></tr>'}</table>
        <h3>핵심 지표</h3><table>${metricRows}</table>
        <h3>자동 해석</h3><ul class="note-list">${card.insights.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`;
    }
    function stepCard(delta) {
      const cards = filteredCards();
      if (!cards.length) return;
      const idx = Math.max(0, cards.findIndex(c => c.id === state.selected));
      state.selected = cards[(idx + delta + cards.length) % cards.length].id;
      render();
    }
    function renderOverlay() {
      const cards = filteredCards().filter(c => c.series && c.series.lines && c.series.lines.length);
      const selectedType = state.filters.analysis !== 'all' ? state.filters.analysis : (selectedCard() ? selectedCard().analysis_type : 'capacity');
      if (selectedType === 'eis') {
        renderEisOverlay(cards);
        return;
      }
      if (selectedType === 'capacity') {
        const comparison = buildCapacityComparisonData(DATA.cards);
        $('overlayCaption').textContent = `Capacity · ${comparison.items.length}개 소재`;
        drawChart('overlayChart', comparison.series, comparison.title);
        return;
      }
      const candidates = cards.filter(c => c.analysis_type === selectedType).slice(0, 6);
      const lines = [];
      let xLabel = '', yLabel = '';
      candidates.forEach((card, idx) => {
        const firstLine = overlayLineFor(card, selectedType);
        xLabel = card.series.xLabel;
        yLabel = card.series.yLabel;
        if (firstLine) lines.push({ name: `${card.cell_id} ${firstLine.name}`, points: firstLine.points, color: colors[idx % colors.length] });
      });
      $('overlayCaption').textContent = `${DATA.analysisLabels[selectedType] || selectedType} · ${candidates.length}개 파일`;
      drawChart('overlayChart', { xLabel, yLabel, lines }, '오버레이');
    }
    function isCapacityComparisonType(analysisType) {
      return analysisType === 'capacity';
    }
    function buildCapacityComparisonData(cards) {
      const candidates = cards
        .filter(card => card.analysis_type === 'capacity')
        .map((card, idx) => capacityItemFromCard(card, idx))
        .filter(item => item && (item.charge.length || item.discharge.length));
      const lines = [];
      candidates.forEach(item => {
        if (item.charge.length) {
          lines.push({
            name: `${item.label} charge`,
            legend: `${item.label} charge (circle)`,
            points: item.charge,
            color: item.color,
            fill: '#ffffff',
            markerShape: 'circle',
            markerSize: 2.15,
            lineWidth: 0.72,
          });
        }
        if (item.discharge.length) {
          lines.push({
            name: `${item.label} discharge`,
            legend: `${item.label} discharge (down tri)`,
            points: item.discharge,
            color: item.color,
            fill: '#ffffff',
            markerShape: 'triangle-down',
            markerSize: 2.45,
            lineWidth: 0.72,
          });
        }
        if (item.ce.length) {
          lines.push({
            name: `${item.label} CE`,
            legend: `${item.label} CE (up tri)`,
            points: item.ce,
            axis: 'right',
            color: item.color,
            fill: '#ffffff',
            markerShape: 'triangle-up',
            markerSize: 2.35,
            lineWidth: 0.64,
          });
        }
      });
      return {
        title: candidates.length === 1 ? `${candidates[0].label} Capacity` : (candidates.map(item => item.label).join(' vs. ') || 'Capacity comparison'),
        items: candidates,
        series: {
          xLabel: 'Cycle',
          yLabel: 'Specific Capacity [mAh/g]',
          legendPosition: 'top-right',
          titleAlign: 'right',
          chartKind: 'capacity-comparison',
          lines,
        },
      };
    }
    function capacityItemFromCard(card, idx) {
      const label = capacityMaterialName(card);
      const color = capacityMaterialColor(label, idx);
      const charge = [];
      const discharge = [];
      const chargeLine = card.series.lines.find(line => /^charge$/i.test(line.name || ''));
      const dischargeLine = card.series.lines.find(line => /^discharge$/i.test(line.name || ''));
      if (chargeLine) charge.push(...chargeLine.points.map(point => [Number(point[0]), Number(point[1])]));
      if (dischargeLine) discharge.push(...dischargeLine.points.map(point => [Number(point[0]), Number(point[1])]));
      charge.sort((a, b) => a[0] - b[0]);
      discharge.sort((a, b) => a[0] - b[0]);
      const ce = computeCePoints(charge, discharge);
      return {
        card,
        label,
        color,
        charge,
        discharge,
        ce,
        ice: ce.length ? ce[0][1] : '',
        mass: card.condition.areal_mass_density || '',
        density: card.condition.electrode_density || '',
      };
    }
    function computeCePoints(charge, discharge) {
      const dischargeByCycle = new Map(discharge.map(point => [String(point[0]), point[1]]));
      return charge
        .map(point => {
          const dchg = dischargeByCycle.get(String(point[0]));
          return dchg ? [point[0], point[1] / dchg * 100] : null;
        })
        .filter(point => point && Number.isFinite(point[1]))
        .sort((a, b) => a[0] - b[0]);
    }
    function capacityMaterialName(card) {
      const source = String(card.source_file || card.cell_id || 'Material');
      if (source) {
        const stem = source.replace(/\.(csv|xlsx?|sde)$/i, '');
        const chunks = stem.split('_').map(part => part.trim()).filter(Boolean);
        const label = chunks.length > 1 && /^\d+$/.test(chunks[0]) ? chunks[1] : chunks[0] || stem;
        return cleanMaterialLabel(label);
      }
      return cleanMaterialLabel(card.condition.sample || card.condition.display_label || card.cell_id || 'Material');
    }
    function cleanMaterialLabel(value) {
      const cleaned = String(value)
        .replace(/\b(capacity|cycle)\b/ig, '')
        .replace(/\b\d+(?:\.\d+)?\s*C\b/ig, '')
        .replace(/\b\d+\s*T\b/ig, '')
        .replace(/\s+\d{2,}$/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      return cleaned || String(value).trim() || 'Material';
    }
    function capacityMaterialColor(label, idx) {
      const key = String(label).toLowerCase();
      if (key.includes('pure')) return '#111111';
      if (key.includes('act') || key.includes('coated')) return '#f2c65f';
      return colors[idx % colors.length];
    }
    function summaryTable(items) {
      return `<div class="metric-summary"><table>
        <thead><tr><th>Material</th><th>ICE (%)</th><th>Mass loading (mg/cm2)</th><th>Electrode density (g/cm3)</th></tr></thead>
        <tbody>${items.map(item => `<tr>
          <td><strong>${escapeHtml(item.label)}</strong></td>
          <td>${escapeHtml(fmt(item.ice))}</td>
          <td>${escapeHtml(fmt(item.mass))}</td>
          <td>${escapeHtml(fmt(item.density))}</td>
        </tr>`).join('')}</tbody>
      </table></div>`;
    }
    function renderEisOverlay(cards) {
      const selected = selectedCard();
      const firstEis = cards.find(card => card.analysis_type === 'eis');
      const baseMaterial = selected && selected.analysis_type === 'eis' ? materialName(selected) : materialName(firstEis);
      const overlay = buildEisOverlayData(cards, baseMaterial);
      $('overlayCaption').textContent = `EIS time series · ${overlay.count}개 시간점`;
      drawChart('overlayChart', overlay.series, overlay.title);
    }
    function buildEisOverlayData(cards, baseMaterial) {
      const candidates = cards
        .filter(card => card.analysis_type === 'eis')
        .filter(card => baseMaterial === 'EIS' || materialName(card) === baseMaterial)
        .map(card => ({ card, time: extractTimePoint(card.source_file) }))
        .filter(item => item.time !== null)
        .sort((a, b) => a.time - b.time || a.card.source_file.localeCompare(b.card.source_file));
      const seen = new Set();
      const uniqueCandidates = candidates.filter(item => {
        const key = `${materialName(item.card)}::${item.time}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      const total = Math.max(1, uniqueCandidates.length - 1);
      const lines = uniqueCandidates.map((item, idx) => {
        const pointLine = item.card.series.lines.find(line => line.points && line.points.length);
        const t = idx / total;
        return {
          name: formatHour(item.time),
          legend: formatHour(item.time),
          points: pointLine ? pointLine.points : [],
          rawPoints: pointLine ? (pointLine.rawPoints || pointLine.points) : [],
          fitMetadata: pointLine ? pointLine.fitMetadata : null,
          color: '#f2474b',
          opacity: 0.22 + t * 0.78,
          lineWidth: 0.66 + t * 0.32,
          markerShape: eisMarkers[idx % eisMarkers.length],
          markerSize: 3.1 + t * 0.9,
        };
      });
      const title = baseMaterial === 'EIS' ? 'EIS' : baseMaterial;
      return {
        title,
        count: uniqueCandidates.length,
        series: {
          xLabel: "Z' (Ohm)",
          yLabel: "-Z'' (Ohm)",
          legendPosition: 'left-stack',
          titleAlign: 'right',
          chartKind: 'eis-overlay',
          lines,
        },
      };
    }
    function overlayLineFor(card, selectedType) {
      if (selectedType === 'capacity' && card.analysis_type === 'capacity') {
        return card.series.lines.find(line => /^discharge$/i.test(line.name) && line.points && line.points.length);
      }
      return card.series.lines.find(line => line.points && line.points.length);
    }
    function materialName(card) {
      if (!card) return 'EIS';
      return String(card.condition.sample || card.condition.display_label || card.cell_id || 'EIS').replace(/_/g, ' ').trim();
    }
    function extractTimePoint(filename) {
      const text = String(filename || '').toLowerCase();
      const match = text.match(/(?:^|[_\-\s])(\d+(?:\.\d+)?)\s*h(?:r|our|ours)?(?:$|[_\-\s.])/);
      return match ? Number(match[1]) : null;
    }
    function formatHour(value) {
      return Number.isInteger(value) ? `${value}hr` : `${value}hr`;
    }
    function renderCompatibility() {
      const structured = DATA.comparisonCandidates || [];
      if (structured.length) {
        $('compatibility').innerHTML = structured.map(item => {
          const cls = item.comparison_grade === 'A' ? 'good' : (item.comparison_grade === 'B' || item.comparison_grade === 'C' ? 'warn' : 'block');
          const diff = item.areal_mass_density_diff === null || item.areal_mass_density_diff === undefined ? '-' : fmt(item.areal_mass_density_diff, 2);
          return `<div class="compare-item ${cls}">
            <strong>${escapeHtml(item.cell_id_a)} vs ${escapeHtml(item.cell_id_b)} · Grade ${escapeHtml(item.comparison_grade)}</strong><br>
            ${escapeHtml(item.reason)}<br>
            <span class="subtle">loading diff ${escapeHtml(diff)} mg/cm2 · type ${yesNo(item.same_cell_type)} · electrolyte ${yesNo(item.same_electrolyte)} · binder ${yesNo(item.same_binder)} · voltage ${yesNo(item.same_voltage_range)} · ratio ${yesNo(item.same_ratio)}</span>
          </div>`;
        }).join('');
        return;
      }
      const items = DATA.compatibility.length ? DATA.compatibility : ['조건표 기반 비교 가능성 메시지가 없습니다.'];
      $('compatibility').innerHTML = items.map(text => `<div class="compare-item">${escapeHtml(text)}</div>`).join('');
    }
    function renderAnalysisValidation() {
      const rows = DATA.analysisValidations || [];
      if (!rows.length) {
        $('analysisValidation').innerHTML = '<div class="empty">Capacity / Voltage Profile 비교 검증 대상이 없습니다.</div>';
        return;
      }
      $('analysisValidation').innerHTML = rows.map(row => {
        const cls = row.status === 'GOOD' ? 'good' : (row.status === 'BLOCK' ? 'block' : 'warn');
        const protocol = row.protocol_a || row.protocol_b ? ` · protocol ${escapeHtml(row.protocol_a || '-')}/${escapeHtml(row.protocol_b || '-')}` : '';
        const cycles = row.common_cycles ? ` · common cycles ${escapeHtml(row.common_cycles)}` : '';
        return `<div class="compare-item ${cls}">
          <strong>${escapeHtml(row.analysis_type)} · ${escapeHtml(row.cell_id_a)} vs ${escapeHtml(row.cell_id_b)} · ${escapeHtml(row.status)}</strong><br>
          ${escapeHtml(row.reason)}<br>
          <span class="subtle">${protocol}${cycles}</span>
        </div>`;
      }).join('');
    }
    function renderAvailability() {
      const rows = DATA.availability || [];
      if (!rows.length) {
        $('availability').innerHTML = '<div class="empty">조건표 또는 분석 파일이 없습니다.</div>';
        return;
      }
      $('availability').innerHTML = `<table class="availability-table">
        <thead><tr>
          <th>Cell</th><th>Label</th><th>Batch</th><th>Files</th>
          <th>Capacity</th><th>Voltage</th><th>EIS</th><th>EIS time</th><th>Sheet R</th><th>Raman</th><th>TGA</th><th>Note</th>
        </tr></thead>
        <tbody>${rows.map(row => `<tr>
          <td>${escapeHtml(row.cell_id)}</td>
          <td>${escapeHtml(row.display_label)}</td>
          <td>${escapeHtml(row.sample_batch_id)}</td>
          <td>${escapeHtml(row.file_count)}</td>
          ${boolCell(row.has_capacity)}
          ${boolCell(row.has_voltage_profile)}
          ${boolCell(row.has_eis)}
          ${boolCell(row.has_eis_time_series)}
          ${boolCell(row.has_sheet_resistance)}
          ${boolCell(row.has_raman)}
          ${boolCell(row.has_tga)}
          <td>${escapeHtml(row.missing_note || '')}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    }
    function boolCell(value) {
      return value ? '<td class="status-ok">있음</td>' : '<td class="status-missing">file missing</td>';
    }
    function yesNo(value) {
      return value ? 'OK' : 'NO';
    }
    function drawChart(targetId, series, title) {
      const el = $(targetId);
      if (!series || !series.lines || !series.lines.some(line => line.points && line.points.length)) {
        el.innerHTML = '<div class="empty">표시할 그래프 데이터가 없습니다.</div>';
        return;
      }
      const lines = series.lines.filter(line => line.points && line.points.length);
      const width = Math.max(680, el.clientWidth || 860), height = 430;
      const hasRightAxis = lines.some(isRightAxis);
      const compactLegend = series.legendPosition === 'left-stack';
      const margin = { left: compactLegend ? 88 : 74, right: hasRightAxis ? 80 : 34, top: 48, bottom: 66 };
      const plotW = width - margin.left - margin.right;
      const plotH = height - margin.top - margin.bottom;
      const leftLines = lines.filter(line => !isRightAxis(line));
      const rightLines = lines.filter(isRightAxis);
      const isEisSeries = String(series.chartKind || '').includes('eis');
      const showEisFit = isEisSeries && state.showEisFit;
      const fitOverlays = showEisFit ? eisFitOverlays(series, lines) : [];
      const points = lines.flatMap(line => line.points).concat(fitOverlays.flatMap(item => item.bounds));
      const xs = points.map(p => Number(p[0])), ys = points.map(p => Number(p[1]));
      const leftYs = (leftLines.length ? leftLines : lines)
        .flatMap(line => line.points.map(p => Number(p[1])))
        .concat(fitOverlays.flatMap(item => item.bounds.map(p => Number(p[1]))));
      const rightYs = rightLines.flatMap(line => line.points.map(p => Number(p[1])));
      let [xmin, xmax] = pad(Math.min(...xs), Math.max(...xs));
      let [ymin, ymax] = pad(Math.min(...leftYs), Math.max(...leftYs));
      if (showEisFit) {
        [xmin, xmax, ymin, ymax] = equalOhmRange(xmin, xmax, ymin, ymax, plotW, plotH);
      }
      const [rightYmin, rightYmax] = rightYs.length ? rightAxisRange(Math.min(...rightYs), Math.max(...rightYs)) : [0, 1];
      const sx = x => margin.left + (Number(x) - xmin) / (xmax - xmin) * plotW;
      const sy = y => margin.top + plotH - (Number(y) - ymin) / (ymax - ymin) * plotH;
      const syRight = y => margin.top + plotH - (Number(y) - rightYmin) / (rightYmax - rightYmin) * plotH;
      const xTicks = [0,1,2,3,4,5].map(i => {
        const xval = xmin + (xmax - xmin) * i / 5;
        return `<line x1="${sx(xval)}" y1="${margin.top}" x2="${sx(xval)}" y2="${margin.top + plotH}" stroke="#eeeeee"/>
          <line x1="${sx(xval)}" y1="${margin.top + plotH}" x2="${sx(xval)}" y2="${margin.top + plotH + 5}" stroke="#111"/>
          <text x="${sx(xval)}" y="${margin.top + plotH + 22}" text-anchor="middle" fill="#111" font-size="11">${shortNum(xval)}</text>`;
      }).join('');
      const yTicks = [0,1,2,3,4,5].map(i => {
        const yval = ymin + (ymax - ymin) * i / 5;
        return `<line x1="${margin.left}" y1="${sy(yval)}" x2="${margin.left + plotW}" y2="${sy(yval)}" stroke="#eeeeee"/>
          <line x1="${margin.left-5}" y1="${sy(yval)}" x2="${margin.left}" y2="${sy(yval)}" stroke="#111"/>
          <text x="${margin.left-9}" y="${sy(yval)+4}" text-anchor="end" fill="#111" font-size="11">${shortNum(yval)}</text>`;
      }).join('');
      const rightTicks = hasRightAxis ? [0,1,2,3,4,5].map(i => {
        const yval = rightYmin + (rightYmax - rightYmin) * i / 5;
        return `<line x1="${margin.left + plotW}" y1="${syRight(yval)}" x2="${margin.left + plotW + 5}" y2="${syRight(yval)}" stroke="#003cff"/>
          <text x="${margin.left + plotW + 9}" y="${syRight(yval)+4}" text-anchor="start" fill="#003cff" font-size="11">${shortNum(yval)}</text>`;
      }).join('') : '';
      const paths = lines.map((line, idx) => {
        const style = lineStyle(line, idx, series);
        const yScale = isRightAxis(line) ? syRight : sy;
        const d = line.points.map((p, i) => `${i ? 'L' : 'M'} ${sx(p[0]).toFixed(2)} ${yScale(p[1]).toFixed(2)}`).join(' ');
        const dots = markerDots(line, sx, yScale, style);
        const hoverWidth = Math.max(style.width * 1.28, style.width + 0.25).toFixed(2);
        return `<g class="series-group" opacity="${style.opacity ?? 1}" style="--hover-stroke-width:${hoverWidth}"><path class="data-path" d="${d}" fill="none" stroke="${style.color}" stroke-width="${style.width}"/>${dots}</g>`;
      }).join('');
      const fitSvg = renderEisFitOverlays(fitOverlays, sx, sy, margin, plotW);
      const eisLastLabels = isEisSeries ? renderEisLastPointLabels(lines, sx, sy, showEisFit) : '';
      const legendItems = uniqueLegendItems(lines);
      const legend = legendItems.map((item, idx) => {
        const style = lineStyle(item.line, idx, series);
        const topCenter = series.legendPosition === 'top-center';
        const topRight = series.legendPosition === 'top-right';
        const leftStack = series.legendPosition === 'left-stack';
        const x = topCenter ? margin.left + plotW * 0.32 : (topRight ? margin.left + plotW - 210 : (leftStack ? margin.left + 24 : margin.left + 14 + (idx % 3) * 170));
        const y = topCenter || topRight ? 72 + idx * 22 : (leftStack ? margin.top + 22 + idx * 19 : 62 + Math.floor(idx / 3) * 18);
        const marker = markerGlyph(x + 12, y, style, item.line, true);
        return `<line x1="${x}" y1="${y}" x2="${x+24}" y2="${y}" stroke="${style.color}" stroke-width="${style.width}" opacity="${style.opacity}"/>
          ${marker}
          <text x="${x+31}" y="${y+4}" fill="#111" font-size="12" font-weight="700">${escapeHtml(item.label).slice(0, 24)}</text>`;
      }).join('');
      const rightAxis = hasRightAxis ? `<line x1="${margin.left + plotW}" y1="${margin.top}" x2="${margin.left + plotW}" y2="${margin.top + plotH}" stroke="#003cff" stroke-width="1.4"/>
        <text x="${width-16}" y="${margin.top + plotH / 2}" transform="rotate(90 ${width-16} ${margin.top + plotH / 2})" text-anchor="middle" fill="#003cff" font-size="12" font-weight="700">Coulombic Efficiency (%)</text>` : '';
      const titleX = series.titleAlign === 'right' ? margin.left + plotW : margin.left;
      const titleAnchor = series.titleAlign === 'right' ? 'end' : 'start';
      el.innerHTML = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}">
        <rect width="${width}" height="${height}" fill="#ffffff"/>
        <text x="${titleX}" y="28" text-anchor="${titleAnchor}" fill="#111" font-size="17" font-weight="700">${escapeHtml(title)}</text>
        <rect x="${margin.left}" y="${margin.top}" width="${plotW}" height="${plotH}" fill="#fff" stroke="#111" stroke-width="1.4"/>
        ${xTicks}${yTicks}${rightTicks}${paths}${fitSvg}${eisLastLabels}${legend}${rightAxis}
        <text x="${margin.left + plotW / 2}" y="${height-16}" text-anchor="middle" fill="#111" font-size="12" font-weight="700">${escapeHtml(series.xLabel || '')}</text>
        <text x="18" y="${margin.top + plotH / 2}" transform="rotate(-90 18 ${margin.top + plotH / 2})" text-anchor="middle" fill="#111" font-size="12" font-weight="700">${escapeHtml(series.yLabel || '')}</text>
      </svg>`;
    }
    function eisFitOverlays(series, lines) {
      if (!String(series.chartKind || '').includes('eis')) return [];
      return lines.map((line, idx) => {
        const metadata = line.fitMetadata || series.fitMetadata;
        const fit = metadata && metadata.fit ? metadata.fit : null;
        if (!fit) return null;
        const rawPoints = line.rawPoints || line.points || [];
        const start = Number.isInteger(fit.segment_start_index) ? fit.segment_start_index : null;
        const end = Number.isInteger(fit.segment_end_index) ? fit.segment_end_index : null;
        const segment = start !== null && end !== null ? rawPoints.slice(start, end + 1) : [];
        const xc = Number(fit.center_x_ohm), yc = Number(fit.center_y_ohm), r = Number(fit.radius_ohm);
        const circle = Number.isFinite(xc) && Number.isFinite(yc) && Number.isFinite(r) && r > 0
          ? Array.from({ length: 181 }, (_, i) => {
              const t = Math.PI * 2 * i / 180;
              return [xc + r * Math.cos(t), yc + r * Math.sin(t)];
            })
          : [];
        const intercepts = [fit.x_left_intercept_ohm, fit.x_right_intercept_ohm]
          .map(x => Number(x))
          .filter(Number.isFinite)
          .map(x => [x, 0]);
        return {
          idx,
          fit,
          segment,
          circle,
          intercepts,
          bounds: segment.concat(circle, intercepts),
          color: line.color || '#2563eb',
        };
      }).filter(item => item && item.bounds.length);
    }
    function equalOhmRange(xmin, xmax, ymin, ymax, plotW, plotH) {
      const xCenter = (xmin + xmax) / 2;
      const yCenter = (ymin + ymax) / 2;
      const xSpan = Math.max(1e-12, xmax - xmin);
      const ySpan = Math.max(1e-12, ymax - ymin);
      const ohmPerPixel = Math.max(xSpan / plotW, ySpan / plotH);
      const halfX = ohmPerPixel * plotW / 2;
      const halfY = ohmPerPixel * plotH / 2;
      return [xCenter - halfX, xCenter + halfX, yCenter - halfY, yCenter + halfY];
    }
    function renderEisFitOverlays(overlays, sx, sy, margin, plotW) {
      if (!overlays.length) return '';
      const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'} ${sx(p[0]).toFixed(2)} ${sy(p[1]).toFixed(2)}`).join(' ');
      const parts = [];
      overlays.forEach((item, idx) => {
        if (item.segment.length) {
          parts.push(`<path d="${path(item.segment)}" fill="none" stroke="#ef4444" stroke-width="2.1" opacity=".82"/>`);
        }
        if (item.circle.length) {
          parts.push(`<path d="${path(item.circle)}" fill="none" stroke="#2563eb" stroke-width="1.5" opacity=".68"/>`);
        }
        item.intercepts.forEach(p => parts.push(`<circle cx="${sx(p[0]).toFixed(2)}" cy="${sy(p[1]).toFixed(2)}" r="4" fill="#f4a742" stroke="#111" stroke-width="1"/>`));
        if (idx === overlays.length - 1) {
          parts.push(eisFitLabel(item.fit, margin.left + plotW - 214, margin.top + 12));
        }
      });
      return parts.join('');
    }
    function renderEisLastPointLabels(lines, sx, sy, detailed) {
      return lines.map((line, idx) => {
        const points = line.rawPoints || line.points || [];
        const last = points[points.length - 1];
        if (!last) return '';
        const metadata = line.fitMetadata;
        const fit = metadata && metadata.fit ? metadata.fit : {};
        const label = `Rs ${fmtOhm(fit.rs_ohm)} · Rct ${fmtOhm(fit.rct_ohm)}`;
        const x = sx(last[0]);
        const y = sy(last[1]) - 12 - (idx % 4) * 17;
        const width = Math.max(112, Math.min(230, label.length * 5.8));
        const fill = detailed ? '#fff7ed' : '#ffffff';
        return `<g>
          <rect x="${(x - width / 2).toFixed(1)}" y="${(y - 15).toFixed(1)}" width="${width.toFixed(1)}" height="18" rx="4" fill="${fill}" stroke="#d6a454" opacity=".95"/>
          <text x="${x.toFixed(1)}" y="${(y - 2).toFixed(1)}" text-anchor="middle" fill="#111" font-size="10.5" font-weight="700">${escapeHtml(label)}</text>
        </g>`;
      }).join('');
    }
    function eisFitLabel(fit, x, y) {
      const rows = [
        `status: ${fit.status || 'unknown'}`,
        `Rs: ${fmtOhm(fit.rs_ohm)}`,
        `Rct: ${fmtOhm(fit.rct_ohm)}`,
        `center: (${fmtOhm(fit.center_x_ohm)}, ${fmtOhm(fit.center_y_ohm)})`,
        `radius: ${fmtOhm(fit.radius_ohm)}`,
        `depression: ${fmtNum(fit.depression_angle_deg)} deg`,
        `nRMSE: ${fmtNum(fit.normalized_rmse)}`,
      ];
      return `<g><rect x="${x}" y="${y}" width="204" height="${20 + rows.length * 16}" rx="5" fill="#fff" stroke="#cccccc" opacity=".94"/>
        ${rows.map((row, i) => `<text x="${x + 9}" y="${y + 21 + i * 16}" fill="#111" font-size="10.5">${escapeHtml(row)}</text>`).join('')}</g>`;
    }
    function fmtOhm(value) {
      const n = Number(value);
      return Number.isFinite(n) ? `${shortNum(n)} ohm` : 'null';
    }
    function fmtNum(value) {
      const n = Number(value);
      return Number.isFinite(n) ? shortNum(n) : 'null';
    }
    function isRightAxis(line) {
      return line.axis === 'right' || /^ce\b|coulombic/i.test(line.name || '');
    }
    function lineStyle(line, idx, series) {
      const name = String(line.name || '').toLowerCase();
      const xLabel = String(series.xLabel || '').toLowerCase();
      const yLabel = String(series.yLabel || '').toLowerCase();
      if (isRightAxis(line)) return { color: line.color || '#003cff', fill: line.fill || '#ffffff', width: effectiveLineWidth(line, series, line.lineWidth || 0.85), opacity: line.opacity ?? 1 };
      if (line.color) return { color: line.color, fill: line.fill || line.color, width: effectiveLineWidth(line, series, line.lineWidth || 0.75), opacity: line.opacity ?? 1 };
      if (name.includes('discharge')) return { color: line.color || '#111111', fill: '#ffffff', width: 0.8, opacity: 1 };
      if (name.includes('charge')) return { color: line.color || '#f4a742', fill: '#ffffff', width: 0.75, opacity: 1 };
      if (xLabel.includes("z'")) return { color: line.color || colors[(idx + 2) % colors.length], fill: line.color || colors[(idx + 2) % colors.length], width: 0.75, opacity: 1 };
      if (xLabel.includes('specific capacity') && yLabel.includes('voltage')) return { color: colors[idx % colors.length], fill: colors[idx % colors.length], width: state.voltageLineWidth, opacity: 1 };
      return { color: line.color || colors[idx % colors.length], fill: '#ffffff', width: 0.8, opacity: 1 };
    }
    function effectiveLineWidth(line, series, fallback) {
      return isVoltageProfileSeries(series) ? state.voltageLineWidth : fallback;
    }
    function isVoltageProfileSeries(series) {
      const xLabel = String(series.xLabel || '').toLowerCase();
      const yLabel = String(series.yLabel || '').toLowerCase();
      return series.chartKind === 'voltage-profile' || (xLabel.includes('specific capacity') && yLabel.includes('voltage'));
    }
    function markerDots(line, sx, sy, style) {
      if (line.hideMarkers) return '';
      const points = line.points || [];
      const step = Math.max(1, Math.ceil(points.length / 130));
      return points
        .filter((_, idx) => idx % step === 0)
        .map(p => markerGlyph(sx(p[0]), sy(p[1]), style, line, false))
        .join('');
    }
    function markerGlyph(cx, cy, style, line, legend) {
      const size = legend ? 4.2 : (line.markerSize || 1.15);
      const shape = line.markerShape || 'circle';
      const fill = line.markerShape === 'ring' ? '#ffffff' : style.fill;
      const stroke = style.color;
      const sw = legend ? 1.3 : 0.65;
      if (shape === 'square') return `<rect class="data-dot" x="${(cx-size).toFixed(2)}" y="${(cy-size).toFixed(2)}" width="${(size*2).toFixed(2)}" height="${(size*2).toFixed(2)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
      if (shape === 'triangle-up') return polygonMarker(cx, cy, [[0,-1], [0.9,0.75], [-0.9,0.75]], size, fill, stroke, sw);
      if (shape === 'triangle-down') return polygonMarker(cx, cy, [[0,1], [0.9,-0.75], [-0.9,-0.75]], size, fill, stroke, sw);
      if (shape === 'triangle-left') return polygonMarker(cx, cy, [[-1,0], [0.75,-0.9], [0.75,0.9]], size, fill, stroke, sw);
      if (shape === 'triangle-right') return polygonMarker(cx, cy, [[1,0], [-0.75,-0.9], [-0.75,0.9]], size, fill, stroke, sw);
      if (shape === 'diamond') return polygonMarker(cx, cy, [[0,-1], [1,0], [0,1], [-1,0]], size, fill, stroke, sw);
      if (shape === 'hexagon') return polygonMarker(cx, cy, [[0,-1], [0.86,-0.5], [0.86,0.5], [0,1], [-0.86,0.5], [-0.86,-0.5]], size, fill, stroke, sw);
      if (shape === 'pentagon') return polygonMarker(cx, cy, [[0,-1], [0.95,-0.3], [0.58,0.9], [-0.58,0.9], [-0.95,-0.3]], size, fill, stroke, sw);
      if (shape === 'star') return polygonMarker(cx, cy, [[0,-1], [0.22,-0.3], [0.95,-0.3], [0.36,0.12], [0.58,0.82], [0,0.38], [-0.58,0.82], [-0.36,0.12], [-0.95,-0.3], [-0.22,-0.3]], size, fill, stroke, sw);
      return `<circle class="data-dot" cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${size}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    function polygonMarker(cx, cy, points, size, fill, stroke, sw) {
      const scaled = points.map(([x, y]) => `${(cx + x * size).toFixed(2)},${(cy + y * size).toFixed(2)}`).join(' ');
      return `<polygon class="data-dot" points="${scaled}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    function uniqueLegendItems(lines) {
      const seen = new Set();
      const items = [];
      lines.forEach(line => {
        if (line.hideLegend) return;
        const label = line.legend || line.name || 'Series';
        if (seen.has(label)) return;
        seen.add(label);
        items.push({ label, line });
      });
      return items;
    }
    function rightAxisRange(min, max) {
      const padded = pad(min, max);
      return [Math.min(0, padded[0]), Math.max(100, padded[1])];
    }
    function pad(min, max) {
      if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
      if (min === max) { const m = Math.abs(min) * 0.1 || 1; return [min - m, max + m]; }
      const m = (max - min) * 0.08;
      return [min - m, max + m];
    }
    function shortNum(value) {
      const abs = Math.abs(value);
      if (abs >= 1000) return value.toFixed(0);
      if (abs >= 10) return value.toFixed(1);
      return value.toFixed(2);
    }
    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    init();
  </script>
</body>
</html>
"""
