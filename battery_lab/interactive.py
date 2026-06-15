from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .conditions import compatibility_notes, find_condition
from .insights import daily_summary, record_insights
from .metrics import to_float
from .models import MetricRecord, ParsedDataset


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
    "unknown": "미분류",
}


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
        "conditionLabels": CONDITION_LABELS,
        "analysisLabels": ANALYSIS_LABELS,
    }


def extract_series(dataset: ParsedDataset) -> dict[str, Any]:
    if dataset.meta.analysis_type == "capacity":
        charge, discharge, ce = [], [], []
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
            if dchg and chg is not None:
                ce.append([cycle, chg / dchg * 100])
        return {
            "xLabel": "Cycle",
            "yLabel": "Specific Capacity [mAh/g]",
            "lines": [
                {"name": "Charge", "points": downsample(charge)},
                {"name": "Discharge", "points": downsample(discharge)},
                {"name": "CE", "points": downsample(ce), "axis": "right", "yLabel": "Coulombic Efficiency (%)"},
            ],
        }
    if dataset.meta.analysis_type == "eis":
        points = []
        for row in dataset.rows:
            x = to_float(row.get("z_real"))
            y_raw = to_float(row.get("z_imag"))
            if x is not None and y_raw is not None:
                points.append([x, -y_raw])
        return {"xLabel": "Z' (Ohm)", "yLabel": "-Z'' (Ohm)", "lines": [{"name": "Nyquist", "points": downsample(points)}]}
    if dataset.meta.analysis_type == "voltage_profile":
        grouped: dict[str, list[list[float]]] = {}
        for row in dataset.rows:
            cycle = str(row.get("cycle") or "1")
            direction = str(row.get("direction") or "")
            capacity = to_float(row.get("capacity") or row.get("discharge_capacity") or row.get("charge_capacity"))
            voltage = to_float(row.get("voltage"))
            if capacity is not None and voltage is not None:
                key = f"{cycle} cycle {direction}".strip()
                grouped.setdefault(key, []).append([capacity, voltage])
        lines = [{"name": name, "points": downsample(sorted(points), 240)} for name, points in sorted(grouped.items())[:8]]
        return {"xLabel": "Specific Capacity [mAh/g]", "yLabel": "Voltage [V]", "lines": lines}
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
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { border-bottom: 1px solid rgba(255,255,255,0.10); padding: 8px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 650; width: 44%; }
    .note-list { margin: 0; padding-left: 18px; color: var(--muted); line-height: 1.58; }
    .compare-list { display: grid; gap: 8px; }
    .compare-item { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; background: rgba(255,255,255,0.04); color: var(--muted); }
    .compare-item.good { border-color: rgba(121,211,140,0.35); color: #b9efc3; }
    .compare-item.warn { border-color: rgba(255,177,94,0.38); color: #ffd3a1; }
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
          <div id="overlayChart" class="chart"></div>
        </div>
      </section>
      <section class="view" id="view-compare">
        <div class="table-panel">
          <h2>비교 가능성 체크</h2>
          <div class="compare-list" id="compatibility"></div>
        </div>
      </section>
    </main>
  </div>
  <script id="dashboard-data" type="application/json">__BATTERY_DASHBOARD_DATA__</script>
  <script>
    const DATA = JSON.parse(document.getElementById('dashboard-data').textContent);
    const state = { tab: 'overview', selected: 0, filters: { search: '', analysis: 'all', sample: 'all', compat: 'all' } };
    const colors = ['#111111', '#f4a742', '#ef4444', '#c0448f', '#4b238f', '#f7c9cf', '#f87171', '#facc15'];
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
      $('prev').addEventListener('click', () => stepCard(-1));
      $('next').addEventListener('click', () => stepCard(1));
      render();
    }
    function fillSelect(id, pairs) {
      $(id).innerHTML = pairs.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
    }
    function filteredCards() {
      const q = state.filters.search.trim().toLowerCase();
      return DATA.cards.filter(card => {
        const hay = [card.cell_id, card.source_file, card.analysis_label, card.condition.sample, card.warning].join(' ').toLowerCase();
        if (q && !hay.includes(q)) return false;
        if (state.filters.analysis !== 'all' && card.analysis_type !== state.filters.analysis) return false;
        if (state.filters.sample !== 'all' && (card.condition.sample || card.cell_id) !== state.filters.sample) return false;
        if (!compatibleWithSelected(card)) return false;
        return true;
      });
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
      return ['electrolyte', 'binder', 'voltage_range', 'ratio'].every(key => sameCondition(reference, card, key));
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
      renderSelected();
      renderOverlay();
      renderCompatibility();
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
      return DATA.cards.find(c => c.id === state.selected) || filteredCards()[0] || DATA.cards[0];
    }
    function renderSelected() {
      const card = selectedCard();
      if (!card) return;
      drawChart('chart', card.series, `${card.cell_id} · ${card.analysis_label}`);
      const conditionRows = Object.entries(DATA.conditionLabels).map(([key, label]) => {
        const value = card.condition[key];
        return value === undefined || value === '' ? '' : `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(fmt(value))}</td></tr>`;
      }).join('');
      const metricRows = Object.entries(card.metrics).map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(fmt(value))}</td></tr>`).join('');
      $('detail').innerHTML = `<h2>${escapeHtml(card.cell_id)}</h2>
        <p class="file-name">${escapeHtml(card.source_file)}</p>
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
      const candidates = cards.filter(c => c.analysis_type === selectedType).slice(0, 6);
      const lines = [];
      let xLabel = '', yLabel = '';
      candidates.forEach((card, idx) => {
        xLabel = card.series.xLabel;
        yLabel = card.series.yLabel;
        const firstLine = card.series.lines.find(line => line.points && line.points.length);
        if (firstLine) lines.push({ name: `${card.cell_id} ${firstLine.name}`, points: firstLine.points, color: colors[idx % colors.length] });
      });
      $('overlayCaption').textContent = `${DATA.analysisLabels[selectedType] || selectedType} · ${candidates.length}개 파일`;
      drawChart('overlayChart', { xLabel, yLabel, lines }, '오버레이');
    }
    function renderCompatibility() {
      const items = DATA.compatibility.length ? DATA.compatibility : ['조건표 기반 비교 가능성 메시지가 없습니다.'];
      $('compatibility').innerHTML = items.map(text => {
        const cls = text.includes('비교 가능') ? 'good' : (text.includes('주의') || text.includes('실패') ? 'warn' : '');
        return `<div class="compare-item ${cls}">${escapeHtml(text)}</div>`;
      }).join('');
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
      const margin = { left: 74, right: hasRightAxis ? 80 : 34, top: 48, bottom: 66 };
      const plotW = width - margin.left - margin.right;
      const plotH = height - margin.top - margin.bottom;
      const leftLines = lines.filter(line => !isRightAxis(line));
      const rightLines = lines.filter(isRightAxis);
      const points = lines.flatMap(line => line.points);
      const xs = points.map(p => Number(p[0])), ys = points.map(p => Number(p[1]));
      const leftYs = (leftLines.length ? leftLines : lines).flatMap(line => line.points.map(p => Number(p[1])));
      const rightYs = rightLines.flatMap(line => line.points.map(p => Number(p[1])));
      const [xmin, xmax] = pad(Math.min(...xs), Math.max(...xs));
      const [ymin, ymax] = pad(Math.min(...leftYs), Math.max(...leftYs));
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
        const dots = markerDots(line.points, sx, yScale, style);
        return `<path d="${d}" fill="none" stroke="${style.color}" stroke-width="${style.width}"/>${dots}`;
      }).join('');
      const legend = lines.map((line, idx) => {
        const style = lineStyle(line, idx, series);
        const x = margin.left + 14 + (idx % 3) * 170;
        const y = 62 + Math.floor(idx / 3) * 18;
        return `<line x1="${x}" y1="${y}" x2="${x+24}" y2="${y}" stroke="${style.color}" stroke-width="${style.width}"/>
          <circle cx="${x+12}" cy="${y}" r="3.5" fill="${style.fill}" stroke="${style.color}" stroke-width="1.5"/>
          <text x="${x+31}" y="${y+4}" fill="#111" font-size="12" font-weight="600">${escapeHtml(line.name).slice(0, 24)}</text>`;
      }).join('');
      const rightAxis = hasRightAxis ? `<line x1="${margin.left + plotW}" y1="${margin.top}" x2="${margin.left + plotW}" y2="${margin.top + plotH}" stroke="#003cff" stroke-width="1.4"/>
        <text x="${width-16}" y="${margin.top + plotH / 2}" transform="rotate(90 ${width-16} ${margin.top + plotH / 2})" text-anchor="middle" fill="#003cff" font-size="12" font-weight="700">Coulombic Efficiency (%)</text>` : '';
      el.innerHTML = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}">
        <rect width="${width}" height="${height}" fill="#ffffff"/>
        <text x="${margin.left}" y="28" fill="#111" font-size="17" font-weight="700">${escapeHtml(title)}</text>
        <rect x="${margin.left}" y="${margin.top}" width="${plotW}" height="${plotH}" fill="#fff" stroke="#111" stroke-width="1.4"/>
        ${xTicks}${yTicks}${rightTicks}${paths}${legend}${rightAxis}
        <text x="${margin.left + plotW / 2}" y="${height-16}" text-anchor="middle" fill="#111" font-size="12" font-weight="700">${escapeHtml(series.xLabel || '')}</text>
        <text x="18" y="${margin.top + plotH / 2}" transform="rotate(-90 18 ${margin.top + plotH / 2})" text-anchor="middle" fill="#111" font-size="12" font-weight="700">${escapeHtml(series.yLabel || '')}</text>
      </svg>`;
    }
    function isRightAxis(line) {
      return line.axis === 'right' || /^ce\b|coulombic/i.test(line.name || '');
    }
    function lineStyle(line, idx, series) {
      const name = String(line.name || '').toLowerCase();
      const xLabel = String(series.xLabel || '').toLowerCase();
      if (isRightAxis(line)) return { color: '#003cff', fill: '#ffffff', width: 2 };
      if (name.includes('discharge')) return { color: '#111111', fill: '#ffffff', width: 1.8 };
      if (name.includes('charge')) return { color: '#f4a742', fill: '#ffffff', width: 1.8 };
      if (xLabel.includes("z'")) return { color: line.color || colors[(idx + 2) % colors.length], fill: line.color || colors[(idx + 2) % colors.length], width: 1.6 };
      return { color: line.color || colors[idx % colors.length], fill: '#ffffff', width: 1.8 };
    }
    function markerDots(points, sx, sy, style) {
      const step = Math.max(1, Math.ceil(points.length / 130));
      return points
        .filter((_, idx) => idx % step === 0)
        .map(p => `<circle cx="${sx(p[0]).toFixed(2)}" cy="${sy(p[1]).toFixed(2)}" r="3" fill="${style.fill}" stroke="${style.color}" stroke-width="1.4"/>`)
        .join('');
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
