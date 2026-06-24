from __future__ import annotations

import csv
import html
import json
import math
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .capacity_matching import (
    CAPACITY_PROTOCOL_CLUSTER_IDS,
    CAPACITY_PROTOCOL_LABELS,
    CAPACITY_PROTOCOL_ORDER,
    build_capacity_match_report,
    capacity_protocol_from_filename,
    classify_capacity_protocol,
    write_capacity_match_outputs,
)
from .config import (
    BATTERY_CAPACITY_ROOT,
    BATTERY_DATA_ROOT,
    BATTERY_EIS_ROOT,
    BATTERY_MATCH_CAPACITY_JSON,
    BATTERY_MATCH_EIS_JSON,
    BATTERY_OUTPUT_ROOT,
)
from .conditions import read_conditions
from .eis_matching import build_eis_match_report, write_eis_match_outputs
from .excel_dashboard import DEFAULT_CONDITION_SHEET, DEFAULT_CONDITION_WORKBOOK
from .file_io import ANALYSIS_EIS, parse_file
from .metrics import compute_metrics, to_float
from .plots import artifact_path_for_dataset, eis_fit_svg, multi_line_svg
from .report import write_outputs
from . import render_cache
from .wonatech_service import convert_wonatech_inputs
from wonatech_parsers.wrd import build_capacity_summary, parse_wrd_file

try:
    from eis_fit_handoff.eis_circle_fit import load_valid_fit_metadata
except ModuleNotFoundError:  # pragma: no cover
    load_valid_fit_metadata = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = BATTERY_DATA_ROOT
CAPACITY_ROOT = BATTERY_CAPACITY_ROOT
EIS_ROOT = BATTERY_EIS_ROOT
ANALYSIS_OUTPUT_ROOT = BATTERY_OUTPUT_ROOT
EIS_MATCH_OVERRIDES_PATH = BATTERY_MATCH_EIS_JSON
CAPACITY_MATCH_OVERRIDES_PATH = BATTERY_MATCH_CAPACITY_JSON
OVERLAY_CACHE_LOCK = threading.Lock()
OVERLAY_WARMING_KEYS: set[tuple[str, tuple[str, ...]]] = set()

ANALYSIS_OPTIONS = {
    "eis": "EIS",
    "voltage_profile": "Voltage Profiles",
    "capacity": "Capacity",
}

NAV_ITEMS = [
    ("journal", "실험 일지", "core"),
    ("files", "데이터 브라우저", "core"),
    ("eis", "EIS", "analysis"),
    ("voltage_profile", "Voltage Profiles", "analysis"),
    ("capacity", "Capacity", "analysis"),
]


@dataclass(frozen=True)
class FileEntry:
    path: Path
    name: str
    is_dir: bool
    size: int


@dataclass(frozen=True)
class AnalysisArtifact:
    path: Path
    name: str
    analysis_type: str


def inject_app_chrome(st: Any) -> None:
    st.markdown(
        """
        <style>
          :root {
            --battery-sidebar-bg: #f4f6f8;
            --battery-sidebar-border: #dfe4ea;
            --battery-sidebar-text: #424852;
            --battery-sidebar-muted: #7b8490;
            --battery-sidebar-hover: #e9edf2;
            --battery-sidebar-active: #dfe8f3;
            --battery-sidebar-active-text: #18202b;
            --battery-sidebar-accent: #3d6f9f;
          }
          section[data-testid="stSidebar"] {
            width: 286px !important;
            background: var(--battery-sidebar-bg);
            border-right: 1px solid var(--battery-sidebar-border);
          }
          section[data-testid="stSidebar"] > div {
            width: 286px !important;
            background: var(--battery-sidebar-bg);
            padding: 30px 18px 22px;
          }
          .block-container {
            padding-top: 2rem;
            max-width: 100%;
          }
          section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0;
          }
          section[data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] {
            width: 100%;
          }
          section[data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] p {
            margin: 0;
          }
          .battery-sidebar {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
            letter-spacing: 0;
            color: var(--battery-sidebar-text);
            min-height: calc(100vh - 72px);
          }
          .battery-sidebar .sidebar-brand {
            color: #242a33;
            font-size: 18px;
            font-weight: 740;
            line-height: 1.1;
            margin: 0 0 28px;
            padding: 0 10px;
          }
          .battery-sidebar .sidebar-nav {
            display: flex;
            flex-direction: column;
            gap: 3px;
          }
          .battery-sidebar .sidebar-link {
            position: relative;
            display: flex;
            align-items: center;
            gap: 12px;
            min-height: 42px;
            padding: 0 12px;
            border-radius: 11px;
            color: var(--battery-sidebar-text);
            text-decoration: none;
            font-size: 15px;
            font-weight: 590;
            line-height: 1;
            white-space: nowrap;
            transition: background 120ms ease, color 120ms ease;
          }
          .battery-sidebar .sidebar-link:hover {
            background: var(--battery-sidebar-hover);
            color: var(--battery-sidebar-active-text);
            text-decoration: none;
          }
          .battery-sidebar .sidebar-link.active {
            background: var(--battery-sidebar-active);
            color: var(--battery-sidebar-active-text);
            font-weight: 730;
          }
          .battery-sidebar .sidebar-link.active::before {
            content: "";
            position: absolute;
            left: 0;
            top: 10px;
            bottom: 10px;
            width: 3px;
            border-radius: 999px;
            background: var(--battery-sidebar-accent);
          }
          .battery-sidebar .sidebar-category {
            display: flex;
            align-items: center;
            gap: 7px;
            margin: 24px 0 7px;
            padding: 0 10px;
            color: #363c45;
            font-size: 14px;
            font-weight: 760;
            line-height: 1;
          }
          .battery-sidebar .sidebar-category .chevron {
            color: #626b77;
            font-size: 18px;
            line-height: 0.8;
          }
          .battery-sidebar .sidebar-link.child {
            min-height: 38px;
            margin-left: 18px;
            padding-left: 12px;
            font-size: 14px;
            font-weight: 560;
            color: #555d68;
          }
          .battery-sidebar .sidebar-link.child.active {
            color: var(--battery-sidebar-active-text);
            font-weight: 700;
          }
          .battery-sidebar .sidebar-icon {
            position: relative;
            width: 20px;
            height: 20px;
            flex: 0 0 20px;
            color: #77818e;
            display: inline-block;
          }
          .battery-sidebar .sidebar-link.active .sidebar-icon {
            color: var(--battery-sidebar-accent);
          }
          .battery-sidebar .sidebar-link.child .sidebar-icon {
            width: 17px;
            height: 17px;
            flex-basis: 17px;
            color: #8a94a1;
          }
          .battery-sidebar .sidebar-icon.sheet {
            border: 1.7px solid currentColor;
            border-radius: 4px;
          }
          .battery-sidebar .sidebar-icon.sheet::before {
            content: "";
            position: absolute;
            left: 4px;
            right: 4px;
            top: 5px;
            height: 1.5px;
            border-radius: 999px;
            background: currentColor;
            box-shadow: 0 4.5px 0 currentColor, 0 9px 0 currentColor;
            opacity: 0.9;
          }
          .battery-sidebar .sidebar-icon.files::before {
            content: "";
            position: absolute;
            left: 2px;
            top: 4px;
            width: 8px;
            height: 5px;
            border: 1.7px solid currentColor;
            border-bottom: 0;
            border-radius: 4px 4px 0 0;
          }
          .battery-sidebar .sidebar-icon.files::after {
            content: "";
            position: absolute;
            left: 1px;
            right: 1px;
            top: 7px;
            bottom: 3px;
            border: 1.7px solid currentColor;
            border-radius: 4px;
          }
          .battery-sidebar .sidebar-icon.analysis::before {
            content: "";
            position: absolute;
            left: 3px;
            bottom: 3px;
            width: 2px;
            height: 8px;
            border-radius: 999px;
            background: currentColor;
            box-shadow: 5px -4px 0 currentColor, 10px -1px 0 currentColor;
          }
          .battery-sidebar .sidebar-icon.analysis::after {
            content: "";
            position: absolute;
            left: 2px;
            right: 2px;
            bottom: 1px;
            height: 1.7px;
            border-radius: 999px;
            background: currentColor;
            opacity: 0.9;
          }
          section[data-testid="stSidebar"] .stButton {
            margin: 0;
          }
          section[data-testid="stSidebar"] .stButton > button {
            justify-content: flex-start !important;
            border: 0 !important;
            background: transparent !important;
            color: var(--battery-sidebar-text) !important;
            box-shadow: none !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(st: Any) -> str:
    st.session_state.setdefault("page", "journal")
    query_page = read_query_page(st)
    if query_page:
        st.session_state["page"] = query_page
    active_page = str(st.session_state["page"])
    with st.sidebar:
        st.markdown(render_sidebar_html(active_page), unsafe_allow_html=True)
    return active_page


def read_query_page(st: Any) -> str | None:
    valid_pages = {key for key, _, _ in NAV_ITEMS}
    try:
        value = st.query_params.get("page")
    except Exception:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value in valid_pages:
        return str(value)
    return None


def render_sidebar_html(active_page: str) -> str:
    def active_class(key: str) -> str:
        return " active" if active_page == key else ""

    def link(key: str, label: str, icon: str, child: bool = False) -> str:
        child_class = " child" if child else ""
        return (
            f'<a class="sidebar-link{child_class}{active_class(key)}" href="?page={key}" target="_self">'
            f'<span class="sidebar-icon {icon}" aria-hidden="true"></span><span>{label}</span></a>'
        )

    analysis_links = "\n".join(
        link(key, label, "analysis", child=True) for key, label, group in NAV_ITEMS if group == "analysis"
    )
    return f"""
    <div class="battery-sidebar">
      <div class="sidebar-brand">Battery Lab</div>
      <nav class="sidebar-nav" aria-label="Battery Lab navigation">
        {link("journal", "실험 일지", "sheet")}
        {link("files", "데이터 브라우저", "files")}
        <div class="sidebar-category"><span class="chevron">›</span><span>분석</span></div>
        {analysis_links}
      </nav>
    </div>
    """


def render_file_browser(st: Any, roots: dict[str, Path], max_columns: int = 4) -> Path | None:
    root_labels = list(roots.keys())
    selected_root_label = st.selectbox("Library", root_labels, key="finder_root")
    selected_root = roots[selected_root_label]
    st.session_state.setdefault("finder_columns", [selected_root])
    if not st.session_state["finder_columns"] or st.session_state["finder_columns"][0] != selected_root:
        st.session_state["finder_columns"] = [selected_root]

    columns = st.columns(max_columns)
    visible_paths = st.session_state["finder_columns"][:max_columns]
    for level, base_path in enumerate(visible_paths):
        with columns[level]:
            st.caption(base_path.name or str(base_path))
            entries = list_directory(base_path)
            if not entries:
                st.caption("비어 있음")
            for entry in entries[:80]:
                suffix = "/" if entry.is_dir else ""
                label = f"{entry.name}{suffix}"
                if st.button(label, key=f"finder:{level}:{entry.path}", use_container_width=True):
                    if entry.is_dir:
                        st.session_state["finder_columns"] = st.session_state["finder_columns"][: level + 1] + [entry.path]
                    else:
                        st.session_state["finder_selected_file"] = entry.path
                    st.rerun()

    selected = st.session_state.get("finder_selected_file")
    if selected:
        st.divider()
        st.caption("Selected")
        st.code(str(selected), language=None)
    return selected


def render_finder_page(st: Any, components: Any) -> None:
    st.subheader("데이터 브라우저")
    st.caption("분석별 원본 데이터 브라우저는 각 분석 페이지 상단으로 이동했습니다.")
    st.markdown("- EIS 데이터: 사이드바의 **분석 > EIS**")
    st.markdown("- Capacity 데이터: 사이드바의 **분석 > Capacity**")


def render_analysis_file_browser(st: Any, components: Any, label: str, root: Path) -> None:
    st.markdown(f"#### {label} 데이터 브라우저")
    components.html(render_finder_html({"roots": [finder_tree(label, root)]}), height=760, scrolling=False)
    st.divider()


def list_directory(path: Path) -> list[FileEntry]:
    if not path.exists() or not path.is_dir():
        return []
    entries = []
    for child in path.iterdir():
        if child.name.startswith("."):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(FileEntry(path=child, name=child.name, is_dir=child.is_dir(), size=stat.st_size))
    return sorted(entries, key=lambda item: (not item.is_dir, item.name.lower()))


def finder_tree(name: str, path: Path, max_depth: int = 6, max_entries: int = 140) -> dict[str, Any]:
    node = {
        "name": name,
        "path": str(path),
        "kind": "folder" if path.is_dir() else "file",
        "children": [],
    }
    if max_depth <= 0 or not path.is_dir():
        return node
    children = []
    for entry in list_directory(path)[:max_entries]:
        children.append(finder_tree(entry.name, entry.path, max_depth - 1, max_entries))
    node["children"] = children
    return node


def render_finder_html(payload: dict[str, Any]) -> str:
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #1f202b;
      color: #e5e7eb;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    .finder {{
      height: 760px;
      display: flex;
      flex-direction: column;
      background: #1f202b;
      border: 1px solid #3b3d4c;
      overflow: hidden;
      border-radius: 8px;
    }}
    .toolbar {{
      height: 44px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 14px;
      border-bottom: 1px solid #3b3d4c;
      background: #242632;
    }}
    .root-btn {{
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: #cfd3dc;
      padding: 7px 10px;
      cursor: pointer;
      font-size: 14px;
    }}
    .root-btn.active {{ background: #0a6ee8; color: #fff; }}
    .columns {{
      flex: 1;
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(260px, 360px);
      overflow-x: auto;
      overflow-y: hidden;
      min-width: 0;
    }}
    .column {{
      min-width: 260px;
      height: 100%;
      overflow-y: auto;
      border-right: 1px solid #333543;
      padding: 8px 10px;
    }}
    .row {{
      height: 34px;
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr) 18px;
      align-items: center;
      gap: 8px;
      border-radius: 8px;
      padding: 0 8px;
      color: #e0e3ea;
      cursor: default;
      user-select: none;
      font-size: 15px;
      font-weight: 590;
    }}
    .row:hover {{ background: rgba(255,255,255,0.08); }}
    .row.active {{ background: #0969da; color: white; }}
    .name {{
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }}
    .chevron {{ color: #9ca3af; text-align: right; }}
    .row.active .chevron {{ color: white; }}
    .folder-icon {{
      width: 24px;
      height: 17px;
      border-radius: 4px;
      background: linear-gradient(#6ed3ff, #31a9d6);
      position: relative;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.25);
    }}
    .folder-icon:before {{
      content: "";
      position: absolute;
      left: 2px;
      top: -5px;
      width: 11px;
      height: 7px;
      border-radius: 3px 3px 0 0;
      background: #77d7ff;
    }}
    .file-icon {{
      width: 18px;
      height: 23px;
      border-radius: 4px;
      background: #f3f4f6;
      position: relative;
      justify-self: center;
    }}
    .file-icon:after {{
      content: "";
      position: absolute;
      right: 0;
      top: 0;
      border-top: 7px solid #d1d5db;
      border-left: 7px solid transparent;
    }}
    .selected-path {{
      min-height: 38px;
      padding: 9px 14px;
      border-top: 1px solid #3b3d4c;
      background: #242632;
      color: #aeb4c0;
      font-size: 12px;
      overflow-x: auto;
      overflow-y: hidden;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <div class="finder">
    <div class="toolbar" id="roots"></div>
    <div class="columns" id="columns"></div>
    <div class="selected-path" id="selectedPath">선택된 파일 없음</div>
  </div>
  <script>
    const payload = {data_json};
    const state = {{ rootIndex: 0, selection: [] }};
    const rootsEl = document.getElementById('roots');
    const columnsEl = document.getElementById('columns');
    const selectedPathEl = document.getElementById('selectedPath');

    function currentRoot() {{ return payload.roots[state.rootIndex]; }}
    function nodeAt(level) {{
      let node = currentRoot();
      for (let i = 0; i <= level; i += 1) {{
        const idx = state.selection[i];
        if (idx === undefined || !node.children) return node;
        node = node.children[idx];
      }}
      return node;
    }}
    function renderRoots() {{
      rootsEl.replaceChildren(...payload.roots.map((root, idx) => {{
        const button = document.createElement('button');
        button.className = `root-btn ${{idx === state.rootIndex ? 'active' : ''}}`;
        button.textContent = root.name;
        button.onclick = () => {{ state.rootIndex = idx; state.selection = []; render(); }};
        return button;
      }}));
    }}
    function render() {{
      renderRoots();
      columnsEl.replaceChildren();
      let node = currentRoot();
      let level = 0;
      while (node && node.children && level < 8) {{
        const columnLevel = level;
        const column = document.createElement('div');
        column.className = 'column';
        node.children.forEach((child, idx) => {{
          const row = document.createElement('div');
          row.className = `row ${{state.selection[columnLevel] === idx ? 'active' : ''}}`;
          row.title = child.path;
          row.innerHTML = `<span class="${{child.kind === 'folder' ? 'folder-icon' : 'file-icon'}}"></span><span class="name"></span><span class="chevron">${{child.kind === 'folder' ? '›' : ''}}</span>`;
          row.querySelector('.name').textContent = child.name;
          row.onclick = () => {{
            state.selection = state.selection.slice(0, columnLevel);
            state.selection[columnLevel] = idx;
            selectedPathEl.textContent = child.path;
            render();
          }};
          column.appendChild(row);
        }});
        columnsEl.appendChild(column);
        const selectedIdx = state.selection[level];
        node = selectedIdx === undefined ? null : node.children[selectedIdx];
        level += 1;
      }}
    }}
    render();
  </script>
</body>
</html>"""


def render_analysis_panel(st: Any, components: Any, analysis_type: str) -> None:
    st.subheader(ANALYSIS_OPTIONS.get(analysis_type, "Analysis"))
    if analysis_type == "eis":
        render_eis_panel(st, components)
    elif analysis_type == "capacity":
        render_capacity_panel(st, components)
    elif analysis_type == "voltage_profile":
        render_voltage_profile_panel(st)


def render_eis_panel(st: Any, components: Any) -> None:
    render_analysis_file_browser(st, components, "EIS", EIS_ROOT)
    source_count = count_files(EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    artifacts = collect_analysis_artifacts("eis")
    render_analysis_summary(st, source_count, artifacts)
    render_analysis_actions(st, EIS_ROOT, "eis", {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    render_eis_fit_batch_action(st)
    render_eis_match_review(st, components)


def render_eis_fit_batch_action(st: Any) -> None:
    if load_valid_fit_metadata is None:
        return
    source_paths = collect_source_files(EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    missing = [path for path in source_paths if load_valid_fit_metadata(path) is None]
    label = f"모든 EIS 그래프 circle fitting 계산 (누락 {len(missing)}개)"
    if st.button(label, key="eis_fit_compute_missing", disabled=not missing):
        computed = failed = skipped = 0
        failures: list[str] = []
        progress = st.progress(0, text="EIS fitting sidecar 확인 중...")
        with st.spinner("누락된 EIS fitting metadata를 계산하는 중..."):
            for idx, path in enumerate(source_paths, start=1):
                progress.progress(idx / max(1, len(source_paths)), text=f"{idx}/{len(source_paths)} {path.name}")
                if load_valid_fit_metadata(path) is not None:
                    skipped += 1
                    continue
                try:
                    parse_file(path)
                    if load_valid_fit_metadata(path) is not None:
                        computed += 1
                    else:
                        failed += 1
                        failures.append(f"{path.relative_to(EIS_ROOT)}: 표시 가능한 EIS 좌표 없음")
                except Exception as exc:
                    failed += 1
                    failures.append(f"{path.relative_to(EIS_ROOT)}: {exc}")
        progress.empty()
        st.success(f"EIS fitting 계산 완료: 생성 {computed}개, 기존 스킵 {skipped}개, 실패 {failed}개")
        if failures:
            with st.expander("계산 실패/제외 파일", expanded=False):
                for failure in failures[:80]:
                    st.code(failure, language=None)


def render_capacity_panel(st: Any, components: Any) -> None:
    render_analysis_file_browser(st, components, "Capacity", CAPACITY_ROOT)
    source_count = count_files(CAPACITY_ROOT, {".csv", ".wrd", ".xlsx", ".xls"})
    artifacts = collect_analysis_artifacts("capacity")
    render_analysis_summary(st, source_count, artifacts)
    render_analysis_actions(st, CAPACITY_ROOT, "capacity", {".csv", ".wrd", ".xlsx", ".xls"})
    render_capacity_match_review(st, components)
    render_capacity_live_viewer(st, components)
    render_artifact_viewer(st, components, artifacts, "capacity")


def render_voltage_profile_panel(st: Any) -> None:
    st.info("Voltage Profiles 모듈은 분리해두었습니다. 현재 원본 데이터 연결은 하지 않은 상태입니다.")


def render_analysis_summary(st: Any, source_count: int, artifacts: list[AnalysisArtifact]) -> None:
    left, right = st.columns(2)
    left.metric("Source files", source_count)
    right.metric("Graph artifacts", len(artifacts))


def render_analysis_actions(st: Any, source_root: Path, analysis_type: str, suffixes: set[str]) -> None:
    with st.expander("그래프 산출물 생성/갱신", expanded=False):
        st.caption("원본 폴더를 앱 파서로 다시 읽어서 SVG, 요약 CSV, 대시보드를 생성합니다. SEO/SDE/WRD는 WonATech/ZIVE 파서를 먼저 통과합니다.")
        recursive = st.checkbox("하위 폴더 포함", value=True, key=f"{analysis_type}_recursive")
        skip_existing = st.checkbox("이미 생성된 artifact 건너뛰기", value=True, key=f"{analysis_type}_skip_existing")
        force_rebuild = st.checkbox("강제 재생성", value=False, key=f"{analysis_type}_force_rebuild")
        limit_text = st.text_input("처리 개수 제한 (비우면 전체)", "", key=f"{analysis_type}_limit")
        write_raw_wrd = False
        condition_path: Path | None = None
        condition_sheet: str | None = None
        if analysis_type == "capacity":
            write_raw_wrd = st.checkbox("WRD raw time-series CSV 저장", value=False, key=f"{analysis_type}_write_raw_wrd")
        if analysis_type in {"eis", "capacity"}:
            default_condition = str(DEFAULT_CONDITION_WORKBOOK) if DEFAULT_CONDITION_WORKBOOK.exists() else ""
            condition_text = st.text_input("조건표 XLSX/CSV", default_condition, key=f"{analysis_type}_condition_path")
            condition_sheet_text = st.text_input("조건표 sheet", DEFAULT_CONDITION_SHEET, key=f"{analysis_type}_condition_sheet")
            condition_path = Path(condition_text).expanduser() if condition_text.strip() else None
            condition_sheet = condition_sheet_text.strip() or None
        if st.button("그래프 다시 만들기", key=f"{analysis_type}_build", type="primary"):
            limit = int(limit_text) if limit_text.strip().isdigit() else None
            progress = st.progress(0.0)
            status_box = st.empty()

            def progress_callback(done: int, total: int, current: str, counts: dict[str, int]) -> None:
                progress.progress(done / total if total else 1.0)
                status_box.caption(
                    f"{done}/{total} · {current} · success {counts['success']} / error {counts['error']} / skipped {counts['skipped']}"
                )

            with st.spinner("원본 파일 파싱 및 그래프 생성 중..."):
                result = build_analysis_artifacts(
                    source_root,
                    analysis_type,
                    suffixes,
                    recursive=recursive,
                    limit=limit,
                    skip_existing=skip_existing,
                    force_rebuild=force_rebuild,
                    write_raw_wrd=write_raw_wrd,
                    condition_path=condition_path,
                    condition_sheet=condition_sheet,
                    progress_callback=progress_callback,
                )
            if result["errors"]:
                st.warning(
                    f"success {result['success']} / skipped {result['skipped']} / error {result['error']} "
                    f"(error log: {result['error_csv'].name})"
                )
                with st.expander("오류 목록", expanded=False):
                    for error in result["errors"][:80]:
                        st.code(error["message"], language=None)
            else:
                st.success(f"success {result['success']} / skipped {result['skipped']} / error 0. 그래프 {result['artifacts']}개.")
            st.json(result["counts"])


def build_analysis_artifacts(
    source_root: Path,
    analysis_type: str,
    suffixes: set[str],
    *,
    recursive: bool = True,
    limit: int | None = None,
    skip_existing: bool = True,
    force_rebuild: bool = False,
    write_raw_wrd: bool = False,
    condition_path: Path | None = None,
    condition_sheet: str | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    source_paths = collect_source_files(source_root, suffixes, recursive=recursive)
    if limit:
        source_paths = source_paths[:limit]
    processed_dir = ANALYSIS_OUTPUT_ROOT / "processed"
    datasets = []
    records = []
    errors: list[dict[str, Any]] = []
    counts = {"success": 0, "error": 0, "skipped": 0}
    conditions = {}
    if condition_path and condition_path.exists():
        try:
            conditions = read_conditions(condition_path, sheet_name=condition_sheet)
        except Exception as exc:
            counts["error"] += 1
            errors.append(
                {
                    "source_path": str(condition_path),
                    "source_name": condition_path.name,
                    "extension": condition_path.suffix.lower(),
                    "message": f"Condition workbook read failed: {exc}",
                }
            )
    total = len(source_paths)
    for idx, source_path in enumerate(source_paths, start=1):
        if progress_callback:
            progress_callback(idx - 1, total, source_path.name, counts)
        try:
            if analysis_type == "eis":
                path = source_path
            else:
                converted_paths, conversions, conversion_errors = convert_wonatech_inputs(
                    [source_path],
                    processed_dir,
                    write_raw_wrd=write_raw_wrd,
                )
                if conversion_errors:
                    raise ValueError("; ".join(conversion_errors))
                path = converted_paths[0]
            dataset = parse_file(path)
            if dataset.meta.analysis_type != analysis_type:
                counts["skipped"] += 1
                continue
            target = artifact_path_for_dataset(dataset, ANALYSIS_OUTPUT_ROOT)
            if skip_existing and not force_rebuild and target.exists():
                counts["skipped"] += 1
                continue
            datasets.append(dataset)
            records.append(compute_metrics(dataset))
            counts["success"] += 1
        except Exception as exc:
            counts["error"] += 1
            errors.append(
                {
                    "source_path": str(source_path),
                    "source_name": source_path.name,
                    "extension": source_path.suffix.lower(),
                    "message": str(exc),
                }
            )
        if progress_callback:
            progress_callback(idx, total, source_path.name, counts)
    if records:
        write_outputs(datasets, records, ANALYSIS_OUTPUT_ROOT, conditions)
    if analysis_type == "eis" and source_paths and conditions:
        write_eis_match_outputs(source_paths, conditions, ANALYSIS_OUTPUT_ROOT, source_root)
    if analysis_type == "capacity" and source_paths and conditions:
        capacity_sources = [path for path in source_paths if is_capacity_summary_source(path)]
        write_capacity_match_outputs(capacity_sources, conditions, ANALYSIS_OUTPUT_ROOT, source_root)
    error_csv, error_json = write_batch_error_logs(analysis_type, errors)
    count_log = artifact_count_log(source_paths, analysis_type)
    return {
        "records": len(records),
        "success": counts["success"],
        "error": counts["error"],
        "skipped": counts["skipped"],
        "artifacts": len(collect_analysis_artifacts(analysis_type)),
        "errors": errors,
        "error_csv": error_csv,
        "error_json": error_json,
        "counts": count_log,
    }


def write_batch_error_logs(analysis_type: str, errors: list[dict[str, Any]]) -> tuple[Path, Path]:
    log_dir = ANALYSIS_OUTPUT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = log_dir / f"{analysis_type}_batch_errors_{stamp}.csv"
    json_path = log_dir / f"{analysis_type}_batch_errors_{stamp}.json"
    headers = ["source_path", "source_name", "extension", "message"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(errors)
    json_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


def artifact_count_log(source_paths: list[Path], analysis_type: str) -> dict[str, Any]:
    artifacts = collect_analysis_artifacts(analysis_type)
    by_extension: dict[str, int] = {}
    for path in source_paths:
        by_extension[path.suffix.lower() or "<none>"] = by_extension.get(path.suffix.lower() or "<none>", 0) + 1
    by_artifact_extension: dict[str, int] = {}
    by_source_type: dict[str, int] = {}
    for artifact in artifacts:
        by_artifact_extension[artifact.path.suffix.lower()] = by_artifact_extension.get(artifact.path.suffix.lower(), 0) + 1
        meta_path = artifact.path.with_name(artifact.path.name + ".meta.json")
        source_format = "unknown"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                source_format = str(meta.get("source_format") or Path(str(meta.get("source_path") or "")).suffix.lower().lstrip(".") or "unknown")
            except Exception:
                source_format = "metadata_error"
        by_source_type[source_format] = by_source_type.get(source_format, 0) + 1
    return {
        "source_total": len(source_paths),
        "source_by_extension": by_extension,
        "artifact_total": len(artifacts),
        "artifact_by_extension": by_artifact_extension,
        "artifact_by_source_type": by_source_type,
        "artifact_minus_source": len(artifacts) - len(source_paths),
    }


def render_eis_match_review(st: Any, components: Any) -> None:
    source_paths = collect_source_files(EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    if not source_paths:
        return
    with st.expander("EIS 파일명-조건표 매칭/클러스터 검토", expanded=False):
        default_condition = str(DEFAULT_CONDITION_WORKBOOK) if DEFAULT_CONDITION_WORKBOOK.exists() else ""
        condition_text = st.text_input("검토 조건표", default_condition, key="eis_match_condition_path")
        condition_sheet = st.text_input("검토 sheet", DEFAULT_CONDITION_SHEET, key="eis_match_condition_sheet")
        condition_path = Path(condition_text).expanduser() if condition_text.strip() else None
        if not condition_path or not condition_path.exists():
            st.info("조건표 경로를 지정하면 EIS 파일명/폴더명과 실험일지 row를 대조합니다.")
            return
        try:
            conditions = read_conditions(condition_path, sheet_name=condition_sheet.strip() or None)
            overrides = load_eis_match_overrides()
            report = build_eis_match_report(source_paths, conditions, EIS_ROOT, overrides)
        except Exception as exc:
            st.error(f"EIS 매칭 검토를 만들지 못했습니다: {exc}")
            return
        left, mid, right = st.columns(3)
        left.metric("EIS source", report.source_count)
        mid.metric("Time-series", report.class_counts.get("time_series", 0))
        right.metric("Comparison", report.class_counts.get("comparison", 0))
        st.json({"match_status": report.status_counts, "journal_rows": report.condition_count})

        match_rows = [asdict(row) for row in report.matches]
        risky = [row for row in match_rows if row["status"] in {"unmatched", "ambiguous", "blocked", "manual"}]
        st.markdown("#### 확인 필요 파일")
        render_eis_manual_match_editor(st, risky[:300], overrides)

        st.markdown("#### 시계열 그룹")
        st.dataframe([asdict(row) for row in report.time_series_groups], use_container_width=True, height=260)

        st.markdown("#### 비교 cluster / 가능한 pair")
        cluster_col, pair_col = st.columns(2)
        cluster_col.dataframe([asdict(row) for row in report.comparison_clusters], use_container_width=True, height=260)
        pair_col.dataframe([asdict(row) for row in report.comparison_pairs], use_container_width=True, height=260)

        st.markdown("#### EIS overlay preview")
        render_eis_match_overlay_preview(st, components, report, conditions)

        if st.button("EIS 매칭 CSV/JSON 저장", key="eis_match_write_outputs"):
            write_eis_match_outputs(source_paths, conditions, ANALYSIS_OUTPUT_ROOT, EIS_ROOT, load_eis_match_overrides())
            st.success(f"저장 완료: {ANALYSIS_OUTPUT_ROOT / 'eis_match_report.json'}")


def render_capacity_match_review(st: Any, components: Any) -> None:
    source_paths = collect_capacity_summary_sources()
    if not source_paths:
        return
    with st.expander("Capacity 파일명-조건표 매칭 검토", expanded=False):
        default_condition = str(DEFAULT_CONDITION_WORKBOOK) if DEFAULT_CONDITION_WORKBOOK.exists() else ""
        condition_text = st.text_input("검토 조건표", default_condition, key="capacity_match_condition_path")
        condition_sheet = st.text_input("검토 sheet", DEFAULT_CONDITION_SHEET, key="capacity_match_condition_sheet")
        condition_path = Path(condition_text).expanduser() if condition_text.strip() else None
        if not condition_path or not condition_path.exists():
            st.info("조건표 경로를 지정하면 Capacity 파일명 앞 행번호와 실험일지 row를 대조합니다.")
            return
        try:
            conditions = read_conditions(condition_path, sheet_name=condition_sheet.strip() or None)
            overrides = load_capacity_match_overrides()
            report = build_capacity_match_report(source_paths, conditions, CAPACITY_ROOT, overrides)
        except Exception as exc:
            st.error(f"Capacity 매칭 검토를 만들지 못했습니다: {exc}")
            return
        left, mid, right = st.columns(3)
        left.metric("Capacity source", report.source_count)
        mid.metric("Journal rows", report.condition_count)
        right.metric("Verified", report.status_counts.get("verified", 0))
        st.json({"match_status": report.status_counts})

        match_rows = [asdict(row) for row in report.matches]
        risky = [row for row in match_rows if row["status"] in {"unmatched", "ambiguous", "blocked", "manual", "review"}]
        st.markdown("#### 확인 필요 파일")
        render_capacity_manual_match_editor(st, risky[:300], overrides)

        st.markdown("#### Capacity overlay preview")
        render_capacity_a999_overlay_preview(st, components, report, conditions)

        if st.button("Capacity 매칭 CSV/JSON 저장", key="capacity_match_write_outputs"):
            write_capacity_match_outputs(source_paths, conditions, ANALYSIS_OUTPUT_ROOT, CAPACITY_ROOT, load_capacity_match_overrides())
            st.success(f"저장 완료: {ANALYSIS_OUTPUT_ROOT / 'capacity_match_report.json'}")


def load_eis_match_overrides(path: Path = EIS_MATCH_OVERRIDES_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_eis_match_overrides(overrides: dict[str, dict[str, Any]], path: Path = EIS_MATCH_OVERRIDES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_capacity_match_overrides(path: Path = CAPACITY_MATCH_OVERRIDES_PATH) -> dict[str, dict[str, Any]]:
    return load_eis_match_overrides(path)


def save_capacity_match_overrides(overrides: dict[str, dict[str, Any]], path: Path = CAPACITY_MATCH_OVERRIDES_PATH) -> None:
    save_eis_match_overrides(overrides, path)


def render_eis_manual_match_editor(st: Any, rows: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> None:
    editor_rows = eis_candidate_editor_rows(rows, overrides)
    if not editor_rows:
        st.dataframe(readable_eis_risk_rows(rows), use_container_width=True, height=260)
        return
    edited = st.data_editor(
        editor_rows,
        use_container_width=True,
        height=320,
        hide_index=True,
        key="eis_manual_match_editor",
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", help="이 파일에 대응되는 실험일지 후보를 하나만 선택하세요."),
            "_file": None,
            "_condition_key": None,
            "_journal_row": None,
            "_sample": None,
            "_date": None,
        },
        disabled=[
            "파일",
            "추정 실험일지 후보",
            "파일 생성날짜와 일지상 날짜 차이",
            "겹친 단서",
            "score",
            "margin",
            "상태",
            "확인 이유",
        ],
    )
    edited_rows = data_editor_rows(edited)
    left, right = st.columns([1, 3])
    if left.button("선택 저장", key="eis_manual_match_save"):
        selected_by_file: dict[str, dict[str, Any]] = {}
        duplicates = set()
        for row in edited_rows:
            if not row.get("선택"):
                continue
            if not row.get("_condition_key"):
                continue
            file_key = str(row.get("_file") or "")
            if file_key in selected_by_file:
                duplicates.add(file_key)
            selected_by_file[file_key] = row
        if duplicates:
            st.error(f"파일 하나에는 후보 하나만 선택할 수 있습니다: {', '.join(sorted(duplicates)[:3])}")
        else:
            next_overrides = dict(overrides)
            for file_key, row in selected_by_file.items():
                next_overrides[file_key] = {
                    "condition_key": row.get("_condition_key"),
                    "journal_row": row.get("_journal_row"),
                    "sample": row.get("_sample"),
                    "date": row.get("_date"),
                    "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                }
            save_eis_match_overrides(next_overrides)
            st.success(f"수동 매칭 저장: {len(selected_by_file)}개")
            st.rerun()
    if right.button("수동 매칭 전체 해제", key="eis_manual_match_clear"):
        save_eis_match_overrides({})
        st.success("수동 매칭을 모두 해제했습니다.")
        st.rerun()


def render_capacity_manual_match_editor(st: Any, rows: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> None:
    editor_rows = capacity_candidate_editor_rows(rows, overrides)
    if not editor_rows:
        st.dataframe(readable_capacity_risk_rows(rows), use_container_width=True, height=260)
        return
    edited = st.data_editor(
        editor_rows,
        use_container_width=True,
        height=320,
        hide_index=True,
        key="capacity_manual_match_editor",
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", help="이 파일에 대응되는 실험일지 후보를 하나만 선택하세요."),
            "_file": None,
            "_condition_key": None,
            "_journal_row": None,
            "_sample": None,
            "_date": None,
        },
        disabled=[
            "파일",
            "파일 행번호",
            "추정 실험일지 후보",
            "파일 생성날짜와 일지상 날짜 차이",
            "겹친 단서",
            "score",
            "margin",
            "상태",
            "확인 이유",
        ],
    )
    edited_rows = data_editor_rows(edited)
    left, right = st.columns([1, 3])
    if left.button("선택 저장", key="capacity_manual_match_save"):
        selected_by_file: dict[str, dict[str, Any]] = {}
        duplicates = set()
        for row in edited_rows:
            if not row.get("선택"):
                continue
            if not row.get("_condition_key"):
                continue
            file_key = str(row.get("_file") or "")
            if file_key in selected_by_file:
                duplicates.add(file_key)
            selected_by_file[file_key] = row
        if duplicates:
            st.error(f"파일 하나에는 후보 하나만 선택할 수 있습니다: {', '.join(sorted(duplicates)[:3])}")
        else:
            next_overrides = dict(overrides)
            for file_key, row in selected_by_file.items():
                next_overrides[file_key] = {
                    "condition_key": row.get("_condition_key"),
                    "journal_row": row.get("_journal_row"),
                    "sample": row.get("_sample"),
                    "date": row.get("_date"),
                    "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                }
            save_capacity_match_overrides(next_overrides)
            st.success(f"수동 매칭 저장: {len(selected_by_file)}개")
            st.rerun()
    if right.button("수동 매칭 전체 해제", key="capacity_manual_match_clear"):
        save_capacity_match_overrides({})
        st.success("수동 매칭을 모두 해제했습니다.")
        st.rerun()


def data_editor_rows(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return list(value or [])


def eis_candidate_editor_rows(rows: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        file_key = str(row.get("relative_path") or "")
        candidates = parse_candidate_options(row)
        if not candidates:
            output.append(eis_candidate_editor_row(row, file_key, {}, overrides, show_file=True))
            continue
        for idx, candidate in enumerate(candidates):
            output.append(eis_candidate_editor_row(row, file_key, candidate, overrides, show_file=idx == 0))
    return output


def eis_candidate_editor_row(
    row: dict[str, Any],
    file_key: str,
    candidate: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
    *,
    show_file: bool,
) -> dict[str, Any]:
    selected_condition = str((overrides.get(file_key) or {}).get("condition_key") or "")
    condition_key = str(candidate.get("condition_key") or row.get("condition_key") or "")
    journal_row = candidate.get("journal_row") or "?"
    sample = candidate.get("sample") or row.get("condition_sample") or "-"
    date = candidate.get("date") or row.get("condition_date") or "-"
    date_delta = candidate.get("date_delta_days")
    date_delta_text = "-" if date_delta in (None, "") else f"{date_delta}일"
    return {
        "파일": file_key if show_file else "",
        "선택": bool(selected_condition and selected_condition == condition_key),
        "추정 실험일지 후보": f"행 {journal_row}, {sample}, {date}" if condition_key else "",
        "파일 생성날짜와 일지상 날짜 차이": f"행 {journal_row}: {date_delta_text}" if condition_key else "",
        "겹친 단서": candidate.get("overlap_tokens") or row.get("overlap_tokens", ""),
        "score": candidate.get("score") or row.get("score", ""),
        "margin": row.get("margin", ""),
        "상태": row.get("status", ""),
        "확인 이유": explain_eis_match_status(row),
        "_file": file_key,
        "_condition_key": condition_key,
        "_journal_row": journal_row,
        "_sample": sample,
        "_date": date,
    }


def capacity_candidate_editor_rows(rows: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        file_key = str(row.get("relative_path") or "")
        candidates = parse_candidate_options(row)
        if not candidates:
            output.append(capacity_candidate_editor_row(row, file_key, {}, overrides, show_file=True))
            continue
        for idx, candidate in enumerate(candidates):
            output.append(capacity_candidate_editor_row(row, file_key, candidate, overrides, show_file=idx == 0))
    return output


def capacity_candidate_editor_row(
    row: dict[str, Any],
    file_key: str,
    candidate: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
    *,
    show_file: bool,
) -> dict[str, Any]:
    selected_condition = str((overrides.get(file_key) or {}).get("condition_key") or "")
    condition_key = str(candidate.get("condition_key") or row.get("condition_key") or "")
    journal_row = candidate.get("journal_row") or row.get("journal_row") or "?"
    sample = candidate.get("sample") or row.get("condition_sample") or "-"
    date = candidate.get("date") or row.get("condition_date") or "-"
    date_delta = candidate.get("date_delta_days")
    date_delta_text = "-" if date_delta in (None, "") else f"{date_delta}일"
    return {
        "파일": file_key if show_file else "",
        "파일 행번호": row.get("row_prefix") if show_file else "",
        "선택": bool(selected_condition and selected_condition == condition_key),
        "추정 실험일지 후보": f"행 {journal_row}, {sample}, {date}" if condition_key else "",
        "파일 생성날짜와 일지상 날짜 차이": f"행 {journal_row}: {date_delta_text}" if condition_key else "",
        "겹친 단서": candidate.get("overlap_tokens") or row.get("overlap_tokens", ""),
        "score": candidate.get("score") or row.get("score", ""),
        "margin": row.get("margin", ""),
        "상태": row.get("status", ""),
        "확인 이유": explain_capacity_match_status(row),
        "_file": file_key,
        "_condition_key": condition_key,
        "_journal_row": journal_row,
        "_sample": sample,
        "_date": date,
    }


def parse_candidate_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        options = json.loads(str(row.get("candidate_options") or "[]"))
    except json.JSONDecodeError:
        return []
    return options if isinstance(options, list) else []


def readable_eis_risk_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append(
            {
                "파일": row.get("relative_path", ""),
                "추정 실험일지 후보": row.get("candidate_summary") or journal_candidate_fallback(row),
                "파일 생성날짜와 일지상 날짜 차이": row.get("candidate_date_deltas") or journal_date_delta_fallback(row),
                "겹친 단서": row.get("overlap_tokens", ""),
                "충돌 단서": row.get("conflict_tokens", ""),
                "score": row.get("score", ""),
                "margin": row.get("margin", ""),
                "상태": row.get("status", ""),
                "확인 이유": explain_eis_match_status(row),
            }
        )
    return output


def readable_capacity_risk_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append(
            {
                "파일": row.get("relative_path", ""),
                "파일 행번호": row.get("row_prefix", ""),
                "추정 실험일지 후보": row.get("candidate_summary") or journal_candidate_fallback(row),
                "파일 생성날짜와 일지상 날짜 차이": row.get("candidate_date_deltas") or journal_date_delta_fallback(row),
                "겹친 단서": row.get("overlap_tokens", ""),
                "충돌 단서": row.get("conflict_tokens", ""),
                "score": row.get("score", ""),
                "margin": row.get("margin", ""),
                "상태": row.get("status", ""),
                "확인 이유": explain_capacity_match_status(row),
            }
        )
    return output


def journal_candidate_fallback(row: dict[str, Any]) -> str:
    sample = row.get("condition_sample") or "-"
    date = row.get("condition_date") or "-"
    return f"행 ?, {sample}, {date}" if sample != "-" or date != "-" else ""


def journal_date_delta_fallback(row: dict[str, Any]) -> str:
    value = row.get("date_delta_days")
    if value in (None, ""):
        return ""
    return f"행 ?: {value}일"


def explain_eis_match_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    margin = int(row.get("margin") or 0)
    score = int(row.get("score") or 0)
    date_delta = row.get("date_delta_days")
    overlap = [token for token in str(row.get("overlap_tokens") or "").split(";") if token]
    conflict = [token for token in str(row.get("conflict_tokens") or "").split(";") if token]
    if status == "blocked":
        return f"재료명 단서가 충돌합니다: {', '.join(conflict) or '충돌 단서 있음'}."
    if status == "unmatched":
        if score:
            return "후보는 있으나 점수/간격이 낮아 자동 확정하지 않았습니다."
        return "실험일지에서 날짜와 재료명 guard를 동시에 통과한 후보가 없습니다."
    if status == "ambiguous":
        return f"상위 후보끼리 너무 가깝습니다(margin {margin}). 같은 파일이 여러 실험일지 row에 붙을 수 있습니다."
    if status == "review":
        if isinstance(date_delta, int) and date_delta > 7:
            return f"재료명은 맞지만 날짜 차이가 큽니다({date_delta}일). 실험일/측정일 차이인지 확인하세요."
        return f"단서가 일부만 겹칩니다({', '.join(overlap) or '부분 일치'}). 실험일지 row 확인이 필요합니다."
    return "자동 매칭 후보입니다."


def explain_capacity_match_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    row_prefix = row.get("row_prefix")
    journal_row = row.get("journal_row")
    margin = int(row.get("margin") or 0)
    if status == "verified" and row_prefix and journal_row and int(row_prefix) == int(journal_row):
        return f"파일명 앞 행번호 {row_prefix}가 실험일지 행 {journal_row}와 일치합니다."
    if status == "blocked":
        conflict = [token for token in str(row.get("conflict_tokens") or "").split(";") if token]
        return f"재료명 단서가 충돌합니다: {', '.join(conflict) or '충돌 단서 있음'}."
    if status == "unmatched":
        return "파일명 앞 행번호와 일치하는 실험일지 row가 없고, 파일명 후보도 충분하지 않습니다."
    if status == "ambiguous":
        return f"상위 후보끼리 너무 가깝습니다(margin {margin}). 실험일지 row 확인이 필요합니다."
    if status == "review":
        return "행번호 직접 일치는 아니지만 파일명 단서가 일부 겹칩니다."
    if status == "manual":
        return "사용자가 수동 확정한 매칭입니다."
    return "자동 매칭 후보입니다."


def render_eis_match_overlay_preview(st: Any, components: Any, report: Any, conditions: dict[str, dict[str, Any]]) -> None:
    mode = st.radio("Overlay mode", ["시계열", "비교 cluster"], horizontal=True, key="eis_overlay_mode")
    if mode == "시계열":
        groups = [group for group in report.time_series_groups if group.file_count >= 2]
        if not groups:
            st.info("겹쳐 그릴 시계열 그룹이 없습니다.")
            return
        labels = [f"{group.cluster_id} · {group.condition_sample or group.cluster_signature} · {group.file_count} files" for group in groups]
        selected = st.selectbox("Time-series group", labels, key="eis_timeseries_overlay_group")
        group = groups[labels.index(selected)]
        rel_paths = [item for item in group.member_paths.split(";") if item]
        title = f"{group.condition_sample or group.cluster_signature} time-series Nyquist"
    else:
        clusters = [cluster for cluster in report.comparison_clusters if cluster.file_count >= 2]
        all_eis_rel_paths = [str(path.relative_to(EIS_ROOT)) for path in collect_source_files(EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})]
        if not clusters and not all_eis_rel_paths:
            st.info("겹쳐 그릴 comparison cluster가 없습니다.")
            return
        cluster_labels = [
            f"{cluster.cluster_id} · {cluster.condition_count} conditions · loading {format_optional(cluster.loading_min)}-{format_optional(cluster.loading_max)}"
            for cluster in clusters
        ]
        all_label = f"C999 · all EIS datasets · {len(all_eis_rel_paths)} files"
        labels = insert_overlay_999_label(cluster_labels, all_label)
        selected = st.selectbox("Comparison cluster", labels, key="eis_comparison_overlay_cluster")
        performance_mode = selected == all_label
        if selected == all_label:
            rel_paths = all_eis_rel_paths
            title = "C999 all EIS datasets Nyquist"
        else:
            cluster = clusters[cluster_labels.index(selected)]
            rel_paths = [item for item in cluster.source_paths.split(";") if item]
            title = f"{cluster.cluster_id} comparison Nyquist"
    if mode == "시계열":
        performance_mode = False

    show_fit = st.toggle("fitting circle 보기 (1:1 Ohm 축척)", value=False, key=f"eis_overlay_fit_{mode}")
    with st.spinner("선택한 EIS 파일을 파싱해서 overlay를 그리는 중..."):
        try:
            series, errors = load_eis_overlay_series(
                rel_paths,
                report,
                conditions,
                color_mode="time_series" if mode == "시계열" else "comparison",
                performance_mode=performance_mode,
            )
        except TypeError as exc:
            if "performance_mode" not in str(exc):
                raise
            series, errors = load_eis_overlay_series(
                rel_paths,
                report,
                conditions,
                color_mode="time_series" if mode == "시계열" else "comparison",
            )
    if performance_mode:
        errors = [error for error in errors if "좌표 스케일이 비정상적으로 커서" not in error]
    if errors:
        with st.expander("Overlay 제외/parse 오류", expanded=False):
            for error in errors[:30]:
                st.code(error, language=None)
    if show_fit:
        for item in series:
            item["label"] = overlay_fit_label(item)
    html_doc = eis_overlay_html(
        title,
        series,
        width=1180,
        height=590,
        color_mode="time_series" if mode == "시계열" else "comparison",
        show_fit=show_fit,
        performance_mode=performance_mode,
    )
    if html_doc:
        components.html(html_doc, height=620, scrolling=True)
        if mode == "비교 cluster":
            start_overlay_cache_warm("eis", all_eis_rel_paths)
    else:
        st.warning("표시할 EIS 좌표가 없습니다.")


def render_capacity_a999_overlay_preview(st: Any, components: Any, report: Any, conditions: dict[str, dict[str, Any]]) -> None:
    rel_paths = [str(path.relative_to(CAPACITY_ROOT)) for path in collect_capacity_summary_sources()]
    if not rel_paths:
        st.info("겹쳐 그릴 Capacity summary 파일이 없습니다.")
        return
    path_groups = capacity_protocol_path_groups(rel_paths)
    cluster_rows = capacity_protocol_path_cluster_rows(path_groups, len(rel_paths))
    if cluster_rows:
        st.dataframe(cluster_rows, use_container_width=True, hide_index=True)
    cluster_labels = []
    cluster_keys = []
    for protocol_type in CAPACITY_PROTOCOL_ORDER:
        paths = path_groups.get(protocol_type, [])
        if not paths:
            continue
        cluster_labels.append(
            f"{CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type]} · {CAPACITY_PROTOCOL_LABELS[protocol_type]} · {len(paths)} files"
        )
        cluster_keys.append(protocol_type)
    all_label = f"P999 · all Capacity datasets · {len(rel_paths)} files"
    labels = insert_overlay_999_label(cluster_labels, all_label)
    selected = st.selectbox("Capacity overlay", labels, key="capacity_a999_overlay")
    if not selected:
        return
    if selected == all_label:
        selected_paths = rel_paths
        title = "P999 all Capacity datasets"
        performance_mode = True
    else:
        protocol_type = cluster_keys[cluster_labels.index(selected)]
        selected_paths = path_groups.get(protocol_type, [])
        title = f"{CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type]} {CAPACITY_PROTOCOL_LABELS[protocol_type]}"
        performance_mode = False
    with st.spinner("Capacity 파일을 파싱해서 overlay를 그리는 중..."):
        series, errors = load_capacity_overlay_series(selected_paths, report, conditions, performance_mode=performance_mode)
    if errors:
        with st.expander("Overlay 제외/parse 오류", expanded=False):
            for error in errors[:40]:
                st.code(error, language=None)
    html_doc = capacity_overlay_html(
        title,
        series,
        width=1180,
        height=590,
        performance_mode=performance_mode,
    )
    if html_doc:
        components.html(html_doc, height=620, scrolling=True)
        start_overlay_cache_warm("capacity", rel_paths)
    else:
        st.warning("표시할 Capacity 좌표가 없습니다.")


def capacity_protocol_clusters(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    clusters: dict[str, list[dict[str, Any]]] = {protocol_type: [] for protocol_type in CAPACITY_PROTOCOL_ORDER}
    for item in series:
        protocol_type = str(item.get("protocol_type") or "")
        if protocol_type in clusters:
            clusters[protocol_type].append(item)
    return clusters


def insert_overlay_999_label(labels: list[str], all_label: str) -> list[str]:
    ordered = list(labels)
    insert_at = min(3, len(ordered))
    ordered.insert(insert_at, all_label)
    return ordered


def capacity_protocol_path_groups(relative_paths: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {protocol_type: [] for protocol_type in CAPACITY_PROTOCOL_ORDER}
    unknown_paths = []
    for rel_path in relative_paths:
        protocol_type = capacity_protocol_from_filename(Path(rel_path).name)
        if protocol_type:
            groups[protocol_type].append(rel_path)
        else:
            unknown_paths.append(rel_path)
    for rel_path in unknown_paths:
        path = CAPACITY_ROOT / rel_path
        try:
            dataset = parse_file_cached(path)
            classification = classify_capacity_protocol(Path(rel_path).name, capacity_points(dataset))
            groups[classification.protocol_type].append(rel_path)
        except Exception:
            groups[CAPACITY_PROTOCOL_ORDER[0]].append(rel_path)
    return groups


def capacity_protocol_path_cluster_rows(groups: dict[str, list[str]], total_count: int) -> list[dict[str, Any]]:
    rows = []
    for protocol_type in CAPACITY_PROTOCOL_ORDER:
        paths = groups.get(protocol_type, [])
        if not paths:
            continue
        rows.append(
            {
                "Cluster": CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type],
                "Type": CAPACITY_PROTOCOL_LABELS[protocol_type],
                "Files": len(paths),
                "Rule": "filename first, shape fallback",
            }
        )
    rows.append(
        {
            "Cluster": "P999",
            "Type": "전체 Capacity datasets",
            "Files": total_count,
            "Rule": "전체 overlay",
        }
    )
    return rows


def capacity_protocol_cluster_rows(clusters: dict[str, list[dict[str, Any]]], total_count: int) -> list[dict[str, Any]]:
    rows = [
        {
            "Cluster": "P999",
            "Type": "전체 Capacity datasets",
            "Files": total_count,
            "Rule": "전체 overlay",
            "Bends": "-",
        }
    ]
    for protocol_type in CAPACITY_PROTOCOL_ORDER:
        items = clusters.get(protocol_type, [])
        if not items:
            continue
        filename_count = sum(1 for item in items if item.get("protocol_rule_source") == "filename")
        shape_count = sum(1 for item in items if item.get("protocol_rule_source") == "shape")
        bend_values = [item.get("bend_count") for item in items if item.get("bend_count") not in (None, "")]
        rows.append(
            {
                "Cluster": CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type],
                "Type": CAPACITY_PROTOCOL_LABELS[protocol_type],
                "Files": len(items),
                "Rule": f"filename {filename_count}, shape {shape_count}",
                "Bends": bend_range_label(bend_values),
            }
        )
    return rows


def bend_range_label(values: list[Any]) -> str:
    numbers = sorted(int(value) for value in values if str(value).lstrip("-").isdigit())
    if not numbers:
        return "-"
    if numbers[0] == numbers[-1]:
        return str(numbers[0])
    return f"{numbers[0]}-{numbers[-1]}"


def parse_file_cached(path: Path) -> Any:
    stat = path.stat()
    return parse_file_cached_by_mtime(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=512)
def parse_file_cached_by_mtime(path_text: str, mtime_ns: int, size: int) -> Any:
    path = Path(path_text)
    return render_cache.cached_parse_file(path, path.parent)


def valid_fit_metadata_cached(path: Path) -> dict[str, Any]:
    if load_valid_fit_metadata is None:
        return {}
    stat = path.stat()
    return valid_fit_metadata_cached_by_mtime(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=512)
def valid_fit_metadata_cached_by_mtime(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    if load_valid_fit_metadata is None:
        return {}
    metadata = load_valid_fit_metadata(Path(path_text)) or {}
    return metadata.get("fit", {}) if isinstance(metadata, dict) else {}


def start_overlay_cache_warm(kind: str, relative_paths: list[str]) -> None:
    paths_key = tuple(sorted(relative_paths))
    warm_key = (kind, paths_key)
    with OVERLAY_CACHE_LOCK:
        if warm_key in OVERLAY_WARMING_KEYS:
            return
        OVERLAY_WARMING_KEYS.add(warm_key)
    thread = threading.Thread(target=warm_overlay_cache, args=(kind, list(paths_key), warm_key), daemon=True)
    thread.start()


def warm_overlay_cache(kind: str, relative_paths: list[str], warm_key: tuple[str, tuple[str, ...]]) -> None:
    root = EIS_ROOT if kind == "eis" else CAPACITY_ROOT
    try:
        for rel_path in relative_paths:
            path = root / rel_path
            try:
                parse_file_cached(path)
                if kind == "eis":
                    valid_fit_metadata_cached(path)
            except Exception:
                continue
    finally:
        with OVERLAY_CACHE_LOCK:
            OVERLAY_WARMING_KEYS.discard(warm_key)


def load_eis_overlay_series(
    relative_paths: list[str],
    report: Any,
    conditions: dict[str, dict[str, Any]],
    color_mode: str = "comparison",
    performance_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    colors = [
        "#111827",
        "#2563eb",
        "#dc2626",
        "#059669",
        "#7c3aed",
        "#d97706",
        "#0891b2",
        "#be123c",
        "#4d7c0f",
        "#4338ca",
        "#b45309",
        "#0f766e",
    ]
    series = []
    errors = []
    matches = {match.relative_path: match for match in report.matches}
    for idx, rel_path in enumerate(relative_paths):
        path = EIS_ROOT / rel_path
        try:
            dataset = parse_file_cached(path)
            points = eis_points(dataset)
            original_point_count = len(points)
            if performance_mode:
                points = downsample_eis_points(points, 220)
            match = matches.get(rel_path)
            condition = conditions.get(match.condition_key, {}) if match and match.condition_key else {}
            fit = valid_fit_metadata_cached(path)
            time_hours = eis_time_hours(dataset, rel_path)
            if points:
                series.append(
                    {
                        "series_id": f"series-{len(series)}",
                        "label": overlay_label(rel_path, dataset, fit, compact_time=color_mode == "time_series", time_hours=time_hours),
                        "short_label": Path(rel_path).stem,
                        "relative_path": rel_path,
                        "points": points,
                        "original_point_count": original_point_count,
                        "color": colors[idx % len(colors)],
                        "fit": fit,
                        "condition": condition,
                        "match": match,
                        "time_hours": time_hours,
                    }
                )
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")
    if color_mode == "time_series":
        series.sort(key=lambda item: item["time_hours"] if item["time_hours"] is not None else float("inf"))
        series, quality_errors = filter_eis_overlay_outliers(series)
        errors.extend(quality_errors)
        for idx, item in enumerate(series):
            item["color"] = red_time_series_color(idx, len(series))
    elif color_mode == "comparison":
        series, quality_errors = filter_eis_overlay_outliers(series)
        errors.extend(quality_errors)
        apply_areal_density_colors(series)
    return series, errors


def load_capacity_overlay_series(
    relative_paths: list[str],
    report: Any,
    conditions: dict[str, dict[str, Any]],
    performance_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    colors = [
        "#111827",
        "#2563eb",
        "#dc2626",
        "#059669",
        "#7c3aed",
        "#d97706",
        "#0891b2",
        "#be123c",
        "#4d7c0f",
        "#4338ca",
        "#b45309",
        "#0f766e",
    ]
    series = []
    errors = []
    matches = {match.relative_path: match for match in report.matches}
    for idx, rel_path in enumerate(relative_paths):
        path = CAPACITY_ROOT / rel_path
        try:
            dataset = parse_file_cached(path)
            if dataset.meta.analysis_type != "capacity":
                continue
            discharge_points = capacity_discharge_points(dataset)
            charge_points = capacity_charge_points(dataset)
            points_for_classification = discharge_points or charge_points
            original_point_count = len(points_for_classification)
            classification = classify_capacity_protocol(Path(rel_path).name, points_for_classification)
            if performance_mode:
                discharge_points = downsample_eis_points(discharge_points, 260)
                charge_points = downsample_eis_points(charge_points, 260)
            match = matches.get(rel_path)
            condition = conditions.get(match.condition_key, {}) if match and match.condition_key else {}
            metrics = compute_metrics(dataset).metrics
            sample_label = capacity_sample_label(rel_path, dataset, match, condition, metrics)
            base_item = {
                "relative_path": rel_path,
                "original_point_count": original_point_count,
                "color": colors[idx % len(colors)],
                "condition": condition,
                "match": match,
                "metrics": metrics,
                "sample_label": sample_label,
                "protocol_type": classification.protocol_type,
                "protocol_label": classification.protocol_label,
                "protocol_cluster_id": classification.cluster_id,
                "protocol_rule_source": classification.rule_source,
                "bend_count": classification.bend_count,
                "protocol_reason": classification.reason,
            }
            if charge_points:
                series.append(
                    {
                        **base_item,
                        "series_id": f"series-{len(series)}",
                        "label": f"{sample_label} charge",
                        "short_label": Path(rel_path).stem,
                        "curve_kind": "Charge",
                        "marker_shape": "circle",
                        "points": charge_points,
                    }
                )
            if discharge_points:
                series.append(
                    {
                        **base_item,
                        "series_id": f"series-{len(series)}",
                        "label": f"{sample_label} discharge",
                        "short_label": Path(rel_path).stem,
                        "curve_kind": "Discharge",
                        "marker_shape": "square",
                        "points": discharge_points,
                    }
                )
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")
    apply_areal_density_colors(series)
    sync_capacity_pair_colors(series)
    return series, errors


def capacity_sample_label(
    relative_path: str,
    dataset: Any,
    match: Any,
    condition: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    sample = (
        condition.get("sample")
        or condition.get("display_label")
        or getattr(match, "condition_sample", "")
        or dataset.meta.cell_id
        or Path(relative_path).stem
    )
    return f"{sample} (ICE={format_capacity_ice(metrics)}, density={format_electrode_density(condition)})"


def format_capacity_ice(metrics: dict[str, Any]) -> str:
    ice = to_float(metrics.get("ice_percent") or metrics.get("ce_1st"))
    return "?" if ice is None else f"{ice:.1f}%"


def format_electrode_density(condition: dict[str, Any]) -> str:
    density = first_numeric_condition_value(
        condition,
        [
            "electrode_density",
            "합제밀도",
            "합제밀도(g/cm3)",
            "electrode_density_g_cm3",
        ],
    )
    return "?" if density is None else f"{density:.3g} g/cm3"


def first_numeric_condition_value(condition: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = to_float(condition.get(key))
        if value is not None:
            return value
    return None


def downsample_eis_points(points: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    sampled = [points[round(idx * step)] for idx in range(limit)]
    sampled[-1] = points[-1]
    return sampled


def filter_eis_overlay_outliers(series: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if len(series) < 2:
        return series, []
    spans = [eis_series_scale(item.get("points") or []) for item in series]
    finite_spans = [value for value in spans if math.isfinite(value) and value > 0]
    if len(finite_spans) < 2:
        return series, []
    baseline = median_value(finite_spans)
    threshold = max(10_000.0, baseline * 50.0)
    kept = []
    errors = []
    for item, scale in zip(series, spans):
        if scale > threshold:
            rel_path = item.get("relative_path") or item.get("short_label") or "unknown"
            errors.append(
                f"{rel_path}: 좌표 스케일이 비정상적으로 커서 overlay에서 제외했습니다 "
                f"(max |Z| {scale:g} Ω, 같은 overlay 기준 {baseline:g} Ω의 {scale / baseline:g}배). "
                "손상 파일이거나 실패한 EIS 측정으로 보입니다."
            )
            continue
        kept.append(item)
    return kept, errors


def eis_series_scale(points: list[tuple[float, float]]) -> float:
    values = [abs(value) for point in points for value in point if math.isfinite(value)]
    return max(values) if values else 0.0


def median_value(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def eis_time_hours(dataset: Any, relative_path: str) -> float | None:
    candidates = [getattr(dataset.meta, "time_point", None), relative_path, Path(relative_path).stem]
    for candidate in candidates:
        value = eis_time_hours_from_text(str(candidate or ""))
        if value is not None:
            return value
    return None


def eis_time_hours_from_text(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*hr(?![a-z])", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def red_time_series_color(index: int, total: int) -> str:
    start = (254, 202, 202)
    end = (153, 27, 27)
    ratio = 1.0 if total <= 1 else index / (total - 1)
    channels = [round(start[idx] + (end[idx] - start[idx]) * ratio) for idx in range(3)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def apply_areal_density_colors(series: list[dict[str, Any]]) -> None:
    values = [to_float(item["condition"].get("areal_mass_density")) for item in series]
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        for item in series:
            item["color"] = "#64748b"
        return
    low = min(valid_values)
    high = max(valid_values)
    buckets: dict[float | None, int] = {}
    totals: dict[float | None, int] = {}
    for value in values:
        key = round(value, 6) if value is not None else None
        totals[key] = totals.get(key, 0) + 1
    for item, value in zip(series, values):
        key = round(value, 6) if value is not None else None
        offset = buckets.get(key, 0)
        buckets[key] = offset + 1
        item["color"] = vary_similar_color(areal_density_color(value, low, high), offset, totals.get(key, 1))


def sync_capacity_pair_colors(series: list[dict[str, Any]]) -> None:
    by_path: dict[str, str] = {}
    for item in series:
        rel_path = str(item.get("relative_path") or "")
        if not rel_path:
            continue
        if rel_path not in by_path:
            by_path[rel_path] = item.get("color") or "#64748b"
        item["color"] = by_path[rel_path]


def areal_density_color(value: float | None, low: float, high: float) -> str:
    if value is None:
        return "#64748b"
    if math.isclose(low, high):
        ratio = 0.5
    else:
        ratio = max(0.0, min(1.0, (value - low) / (high - low)))
    stops = [
        (0.00, (37, 99, 235)),
        (0.35, (14, 165, 233)),
        (0.65, (34, 197, 94)),
        (0.82, (245, 158, 11)),
        (1.00, (220, 38, 38)),
    ]
    for idx in range(len(stops) - 1):
        left_pos, left_rgb = stops[idx]
        right_pos, right_rgb = stops[idx + 1]
        if left_pos <= ratio <= right_pos:
            local = (ratio - left_pos) / (right_pos - left_pos)
            channels = [round(left_rgb[channel] + (right_rgb[channel] - left_rgb[channel]) * local) for channel in range(3)]
            return "#" + "".join(f"{channel:02x}" for channel in channels)
    return "#dc2626"


def vary_similar_color(color: str, index: int, total: int) -> str:
    if total <= 1 or not color.startswith("#") or len(color) != 7:
        return color
    rgb = [int(color[pos : pos + 2], 16) for pos in (1, 3, 5)]
    midpoint = (total - 1) / 2
    amount = (index - midpoint) / max(1, midpoint) * 0.12 if midpoint else 0.0
    target = 255 if amount > 0 else 0
    weight = abs(amount)
    channels = [round(channel + (target - channel) * weight) for channel in rgb]
    return "#" + "".join(f"{max(0, min(255, channel)):02x}" for channel in channels)


def overlay_label(relative_path: str, dataset: Any, fit: dict[str, Any], compact_time: bool = False, time_hours: float | None = None) -> str:
    label = dataset.meta.time_point or Path(relative_path).stem
    if dataset.meta.time_point:
        label = f"{dataset.meta.time_point} · {Path(relative_path).stem}"
    if compact_time:
        label = format_time_hours_label(time_hours) or dataset.meta.time_point or Path(relative_path).stem
    separator = "\n" if compact_time else " "
    return f"{label}{separator}(Rs {format_optional(fit.get('rs_ohm'))}, Rct {format_optional(fit.get('rct_ohm'))})"


def overlay_fit_label(item: dict[str, Any]) -> str:
    fit = item.get("fit") or {}
    base = str(item.get("label") or item.get("short_label") or item.get("relative_path") or "EIS").splitlines()[0]
    return (
        f"{base} (Rs {format_optional(fit.get('rs_ohm'))}, Rct {format_optional(fit.get('rct_ohm'))}, "
        f"R {format_optional(fit.get('radius_ohm'))}, dep {format_depression(fit.get('depression_angle_deg'))})"
    )


def format_time_hours_label(time_hours: float | None) -> str:
    if time_hours is None:
        return ""
    if float(time_hours).is_integer():
        return f"{int(time_hours)}hr"
    return f"{time_hours:g}hr"


def eis_overlay_html(
    title: str,
    series: list[dict[str, Any]],
    width: int = 1180,
    height: int = 590,
    color_mode: str = "comparison",
    show_fit: bool = False,
    performance_mode: bool = False,
) -> str:
    return overlay_viewer_html(
        title,
        series,
        width=width,
        height=height,
        color_mode=color_mode,
        show_fit=show_fit,
        performance_mode=performance_mode,
        label_layout="time_series" if color_mode == "time_series" else "density_stack",
        x_axis_label="Z&apos; (ohm)",
        y_axis_label="-Z&apos;&apos; (ohm)",
        table_html=eis_overlay_table(series, color_mode=color_mode),
        fit_shape_builder=overlay_fit_shape,
    )


def overlay_viewer_html(
    title: str,
    series: list[dict[str, Any]],
    width: int = 1180,
    height: int = 590,
    color_mode: str = "comparison",
    show_fit: bool = False,
    performance_mode: bool = False,
    label_layout: str = "repel",
    x_axis_label: str = "X",
    y_axis_label: str = "Y",
    table_html: str | None = None,
    fit_shape_builder: Any | None = None,
) -> str:
    series = [{**item, "series_id": item.get("series_id") or f"series-{idx}"} for idx, item in enumerate(series)]
    svg = overlay_viewer_svg(
        title,
        series,
        width=820,
        height=560,
        show_fit=show_fit,
        performance_mode=performance_mode,
        label_layout=label_layout,
        x_axis_label=x_axis_label,
        y_axis_label=y_axis_label,
        fit_shape_builder=fit_shape_builder,
    )
    if not svg:
        return ""
    table = table_html if table_html is not None else overlay_basic_table(series)
    return f"""
<div class="eis-overlay-shell" style="width:{width}px;height:{height}px;display:flex;gap:10px;align-items:stretch;font-family:Arial,sans-serif;user-select:none;-webkit-user-select:none;">
  <div style="width:840px;position:relative;border:1px solid #d7dce2;background:#fff;user-select:none;-webkit-user-select:none;">
    <div style="position:absolute;right:10px;top:8px;z-index:3;display:flex;gap:4px;">
      <button type="button" data-zoom="out" style="width:28px;height:24px;border:1px solid #b8c0ca;background:#fff;border-radius:4px;">-</button>
      <button type="button" data-zoom="in" style="width:28px;height:24px;border:1px solid #b8c0ca;background:#fff;border-radius:4px;">+</button>
      <button type="button" data-zoom="reset" style="height:24px;border:1px solid #b8c0ca;background:#fff;border-radius:4px;font-size:11px;">reset</button>
    </div>
    {svg}
  </div>
  <div style="flex:1;min-width:300px;max-width:330px;overflow:auto;border:1px solid #d7dce2;background:#fff;">
    <div style="position:sticky;top:0;z-index:4;display:flex;gap:6px;align-items:center;justify-content:flex-end;padding:6px 7px;border-bottom:1px solid #d7dce2;background:#fff;">
      <button type="button" data-series-toggle="show-all" style="height:24px;padding:0 8px;border:1px solid #b8c0ca;background:#fff;border-radius:4px;font-size:11px;color:#334155;">Show all</button>
      <button type="button" data-series-toggle="hide-all" style="height:24px;padding:0 8px;border:1px solid #b8c0ca;background:#fff;border-radius:4px;font-size:11px;color:#334155;">Hide all</button>
    </div>
    {table}
  </div>
</div>
<script>
(function() {{
  const root = document.currentScript.previousElementSibling;
  const svg = root.querySelector('svg');
  const zoomLayer = svg.querySelector('[data-zoom-layer]');
  const labelZoomLayer = svg.querySelector('[data-label-zoom-layer]');
  let scale = 1, tx = 0, ty = 0;
  let isPanning = false;
  let lastPoint = null;
  const wheelZoomIn = 1.048;
  const wheelZoomOut = 0.955;
  const buttonZoomIn = 1.063;
  const buttonZoomOut = 0.939;
  svg.style.cursor = 'grab';
  svg.style.touchAction = 'none';
  svg.style.userSelect = 'none';
  svg.querySelectorAll('text').forEach(node => {{
    node.style.userSelect = 'none';
    node.style.webkitUserSelect = 'none';
    node.style.pointerEvents = 'none';
  }});
  function apply() {{
    zoomLayer.setAttribute('transform', `matrix(${{scale}} 0 0 ${{scale}} ${{tx}} ${{ty}})`);
    if (labelZoomLayer) labelZoomLayer.setAttribute('transform', `matrix(${{scale}} 0 0 ${{scale}} ${{tx}} ${{ty}})`);
    updateZoomVisualWeight();
    updateAxes();
  }}
  function updateZoomVisualWeight() {{
    const divisor = Math.pow(scale, 0.72);
    root.querySelectorAll('[data-zoom-stroke]').forEach(node => {{
      const base = Number(node.dataset.baseStrokeWidth || node.getAttribute('stroke-width') || '1');
      node.setAttribute('stroke-width', String(Math.max(0.18, base / divisor)));
    }});
    root.querySelectorAll('[data-zoom-radius]').forEach(node => {{
      const base = Number(node.dataset.baseRadius || node.getAttribute('r') || '1');
      const next = Math.max(0.45, base / divisor);
      if (node.tagName.toLowerCase() === 'rect') {{
        const cx = Number(node.dataset.cx || (Number(node.getAttribute('x') || '0') + Number(node.getAttribute('width') || '0') / 2));
        const cy = Number(node.dataset.cy || (Number(node.getAttribute('y') || '0') + Number(node.getAttribute('height') || '0') / 2));
        const size = next * 2;
        node.dataset.cx = String(cx);
        node.dataset.cy = String(cy);
        node.setAttribute('x', String(cx - size / 2));
        node.setAttribute('y', String(cy - size / 2));
        node.setAttribute('width', String(size));
        node.setAttribute('height', String(size));
      }} else {{
        node.setAttribute('r', String(next));
      }}
    }});
    updateLabelScale(divisor);
  }}
  function updateLabelScale(divisor) {{
    root.querySelectorAll('[data-label-line]').forEach(node => {{
      const base = Number(node.dataset.baseStrokeWidth || '0.8');
      node.setAttribute('stroke-width', String(Math.max(0.18, base / divisor)));
    }});
  }}
  function setInitialViewport() {{
    const left = Number(svg.dataset.plotLeft);
    const top = Number(svg.dataset.plotTop);
    const plotW = Number(svg.dataset.plotWidth);
    const plotH = Number(svg.dataset.plotHeight);
    const xMin0 = Number(svg.dataset.xMin);
    const xMax0 = Number(svg.dataset.xMax);
    const yMin0 = Number(svg.dataset.yMin);
    const yMax0 = Number(svg.dataset.yMax);
    const viewXMin = Number(svg.dataset.viewXMin || xMin0);
    const viewXMax = Number(svg.dataset.viewXMax || xMax0);
    const viewYMin = Number(svg.dataset.viewYMin || yMin0);
    const viewYMax = Number(svg.dataset.viewYMax || yMax0);
    const domainXSpan = xMax0 - xMin0;
    const domainYSpan = yMax0 - yMin0;
    const viewXSpan = viewXMax - viewXMin;
    const viewYSpan = viewYMax - viewYMin;
    if (!domainXSpan || !domainYSpan || !viewXSpan || !viewYSpan) return;
    scale = Math.min(domainXSpan / viewXSpan, domainYSpan / viewYSpan);
    const viewLeftPx = left + (viewXMin - xMin0) / domainXSpan * plotW;
    const viewBottomPx = top + plotH - (viewYMin - yMin0) / domainYSpan * plotH;
    tx = left - scale * viewLeftPx;
    ty = (top + plotH) - scale * viewBottomPx;
  }}
  function niceStep(raw) {{
    if (!Number.isFinite(raw) || raw <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const scaled = raw / power;
    const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return nice * power;
  }}
  function formatTick(value) {{
    if (Math.abs(value) >= 100 || Number.isInteger(value)) return String(Math.round(value));
    return String(Math.round(value * 10) / 10);
  }}
  function updateAxes() {{
    const xAxis = svg.querySelector('[data-x-axis-layer]');
    const yAxis = svg.querySelector('[data-y-axis-layer]');
    if (!xAxis || !yAxis) return;
    const left = Number(svg.dataset.plotLeft);
    const top = Number(svg.dataset.plotTop);
    const plotW = Number(svg.dataset.plotWidth);
    const plotH = Number(svg.dataset.plotHeight);
    const xMin0 = Number(svg.dataset.xMin);
    const xMax0 = Number(svg.dataset.xMax);
    const yMin0 = Number(svg.dataset.yMin);
    const yMax0 = Number(svg.dataset.yMax);
    const visibleXMin = xMin0 + ((left - tx - left * scale) / scale) * (xMax0 - xMin0) / plotW;
    const visibleXMax = xMin0 + ((left + plotW - tx - left * scale) / scale) * (xMax0 - xMin0) / plotW;
    const visibleYMax = yMin0 + ((top + plotH - ((top - ty) / scale)) / plotH) * (yMax0 - yMin0);
    const visibleYMin = yMin0 + ((top + plotH - ((top + plotH - ty) / scale)) / plotH) * (yMax0 - yMin0);
    const xStep = niceStep((visibleXMax - visibleXMin) / 7);
    const yStep = niceStep((visibleYMax - visibleYMin) / 7);
    xAxis.innerHTML = '';
    yAxis.innerHTML = '';
    for (let value = Math.ceil(visibleXMin / xStep) * xStep; value <= visibleXMax + xStep * 0.001; value += xStep) {{
      const x = left + (value - visibleXMin) / (visibleXMax - visibleXMin) * plotW;
      xAxis.insertAdjacentHTML('beforeend', `<line x1="${{x.toFixed(1)}}" y1="${{top}}" x2="${{x.toFixed(1)}}" y2="${{top + plotH}}" stroke="#eeeeee"/>`);
      xAxis.insertAdjacentHTML('beforeend', `<text x="${{x.toFixed(1)}}" y="${{top + plotH + 18}}" font-family="Arial" font-size="10" text-anchor="middle">${{formatTick(value)}}</text>`);
    }}
    for (let value = Math.ceil(visibleYMin / yStep) * yStep; value <= visibleYMax + yStep * 0.001; value += yStep) {{
      const y = top + plotH - (value - visibleYMin) / (visibleYMax - visibleYMin) * plotH;
      yAxis.insertAdjacentHTML('beforeend', `<line x1="${{left}}" y1="${{y.toFixed(1)}}" x2="${{left + plotW}}" y2="${{y.toFixed(1)}}" stroke="#eeeeee"/>`);
      yAxis.insertAdjacentHTML('beforeend', `<text x="${{left - 8}}" y="${{(y + 3).toFixed(1)}}" font-family="Arial" font-size="10" text-anchor="end">${{formatTick(value)}}</text>`);
    }}
  }}
  function svgPointFromEvent(ev) {{
    const box = svg.getBoundingClientRect();
    const viewBox = svg.viewBox.baseVal;
    return {{
      x: viewBox.x + (ev.clientX - box.left) * viewBox.width / box.width,
      y: viewBox.y + (ev.clientY - box.top) * viewBox.height / box.height
    }};
  }}
  function zoomAt(factor, cx, cy) {{
    const next = Math.max(0.4, Math.min(8, scale * factor));
    const k = next / scale;
    tx = cx - k * (cx - tx);
    ty = cy - k * (cy - ty);
    scale = next;
    apply();
  }}
  svg.addEventListener('wheel', function(ev) {{
    ev.preventDefault();
    const point = svgPointFromEvent(ev);
    zoomAt(ev.deltaY < 0 ? wheelZoomIn : wheelZoomOut, point.x, point.y);
  }}, {{passive:false}});
  svg.addEventListener('pointerdown', function(ev) {{
    if (ev.button !== 0) return;
    isPanning = true;
    lastPoint = svgPointFromEvent(ev);
    svg.setPointerCapture?.(ev.pointerId);
    svg.style.cursor = 'grabbing';
  }});
  svg.addEventListener('pointermove', function(ev) {{
    if (!isPanning || !lastPoint) return;
    ev.preventDefault();
    const point = svgPointFromEvent(ev);
    tx += point.x - lastPoint.x;
    ty += point.y - lastPoint.y;
    lastPoint = point;
    apply();
  }});
  function stopPan(ev) {{
    if (!isPanning) return;
    isPanning = false;
    lastPoint = null;
    svg.releasePointerCapture?.(ev.pointerId);
    svg.style.cursor = 'grab';
  }}
  svg.addEventListener('pointerup', stopPan);
  svg.addEventListener('pointercancel', stopPan);
  svg.addEventListener('pointerleave', stopPan);
  root.querySelector('[data-zoom="in"]').addEventListener('click', () => zoomAt(buttonZoomIn, 410, 280));
  root.querySelector('[data-zoom="out"]').addEventListener('click', () => zoomAt(buttonZoomOut, 410, 280));
  root.querySelector('[data-zoom="reset"]').addEventListener('click', () => {{ setInitialViewport(); apply(); }});
  let tableDragActive = false;
  let tableDragTarget = false;
  const toggledRows = new Set();
  function setSeriesActive(id, active) {{
    root.querySelectorAll(`svg [data-series-id="${{id}}"]`).forEach(node => {{
      node.style.display = active ? '' : 'none';
      node.dataset.hidden = active ? '' : '1';
    }});
    root.querySelectorAll(`tr[data-series-id="${{id}}"]`).forEach(row => row.classList.toggle('inactive-row', !active));
  }}
  function setAllSeriesActive(active) {{
    root.querySelectorAll('tr[data-series-id]').forEach(row => setSeriesActive(row.dataset.seriesId, active));
  }}
  function applyRowToggle(id) {{
    if (!id || toggledRows.has(id)) return;
    toggledRows.add(id);
    setSeriesActive(id, tableDragTarget);
  }}
  root.querySelector('[data-series-toggle="show-all"]').addEventListener('click', () => setAllSeriesActive(true));
  root.querySelector('[data-series-toggle="hide-all"]').addEventListener('click', () => setAllSeriesActive(false));
  root.querySelectorAll('tr[data-series-id]').forEach(row => {{
    row.addEventListener('pointerdown', ev => {{
      ev.preventDefault();
      tableDragActive = true;
      toggledRows.clear();
      tableDragTarget = row.classList.contains('inactive-row');
      applyRowToggle(row.dataset.seriesId);
    }});
    row.addEventListener('pointerup', ev => {{
      tableDragActive = false;
      toggledRows.clear();
    }});
  }});
  root.addEventListener('pointermove', ev => {{
    if (!tableDragActive) return;
    const row = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.('tr[data-series-id]');
    if (row && root.contains(row)) applyRowToggle(row.dataset.seriesId);
  }});
  root.addEventListener('pointerup', () => {{ tableDragActive = false; toggledRows.clear(); }});
  setInitialViewport();
  apply();
}})();
</script>
"""


def eis_overlay_table(series: list[dict[str, Any]], color_mode: str = "comparison") -> str:
    rows = sorted(series, key=lambda item: to_float(item["condition"].get("areal_mass_density")) or 1e18)
    body = []
    for idx, item in enumerate(rows):
        condition = item["condition"]
        fit = item["fit"]
        color = item["color"]
        series_id = html.escape(str(item.get("series_id") or f"series-{idx}"))
        body.append(
            f'<tr data-series-id="{series_id}">'
            f'<td class="graph-cell" title="{html.escape(str(item["short_label"]))}"><span class="swatch" style="background:{color};"></span>{html.escape(str(item["short_label"]))}</td>'
            f"<td class=\"num-cell\">{format_optional(condition.get('areal_mass_density'))}</td>"
            f"<td class=\"text-cell\" title=\"{html.escape(str(condition.get('electrolyte') or ''))}\">{html.escape(str(condition.get('electrolyte') or ''))}</td>"
            f"<td class=\"text-cell\" title=\"{html.escape(str(condition.get('binder') or ''))}\">{html.escape(str(condition.get('binder') or ''))}</td>"
            f"<td class=\"nowrap-cell\">{html.escape(str(condition.get('voltage_range') or ''))}</td>"
            f"<td class=\"nowrap-cell\">{html.escape(str(condition.get('ratio') or ''))}</td>"
            f"<td class=\"num-cell\">{format_optional(fit.get('rs_ohm'))}</td>"
            f"<td class=\"num-cell\">{format_optional(fit.get('rct_ohm'))}</td>"
            "</tr>"
        )
    legend = ""
    if color_mode == "comparison":
        legend = (
            '<div style="background:#fff;padding:6px 7px 5px;border-bottom:1px solid #d7dce2;">'
            '<div style="height:7px;border-radius:999px;background:linear-gradient(90deg,#2563eb,#0ea5e9,#22c55e,#f59e0b,#dc2626);"></div>'
            '<div style="margin-top:4px;color:#475569;font-size:9.5px;line-height:1.25;white-space:normal;">'
            'Color encodes Areal mass density: blue=lower, red=higher. Larger color gaps mean larger loading differences.'
            '</div></div>'
        )
    return (
        legend
        + '<style>'
        '.eis-overlay-shell table th,.eis-overlay-shell table td{padding:4px 5px;border-bottom:1px solid #e5e7eb;vertical-align:top;}'
        '.eis-overlay-shell table th{font-weight:700;color:#334155;background:#f8fafc;white-space:nowrap;line-height:1.1;}'
        '.eis-overlay-shell .graph-cell{white-space:nowrap;max-width:170px;overflow:hidden;text-overflow:ellipsis;font-weight:600;}'
        '.eis-overlay-shell .text-cell{max-width:128px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
        '.eis-overlay-shell .nowrap-cell{white-space:nowrap;}'
        '.eis-overlay-shell .num-cell{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;}'
        '.eis-overlay-shell .swatch{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:-1px;}'
        '.eis-overlay-shell tr[data-series-id]{cursor:pointer;}'
        '.eis-overlay-shell tr.inactive-row td{background:#475569;color:#e2e8f0;opacity:.72;}'
        '</style>'
        '<table style="border-collapse:collapse;font-size:9.5px;line-height:1.18;width:max-content;min-width:100%;table-layout:auto;">'
        '<thead style="position:sticky;top:0;background:#f8fafc;z-index:1;">'
        "<tr>"
        '<th style="text-align:left;">Graph</th>'
        '<th style="text-align:right;">Areal</th>'
        '<th style="text-align:left;">전해질</th>'
        '<th style="text-align:left;">Binder</th>'
        '<th style="text-align:left;">Voltage</th>'
        '<th style="text-align:left;">ratio</th>'
        '<th style="text-align:right;">Rs</th>'
        '<th style="text-align:right;">Rct</th>'
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def capacity_overlay_html(
    title: str,
    series: list[dict[str, Any]],
    width: int = 1180,
    height: int = 590,
    performance_mode: bool = False,
) -> str:
    return overlay_viewer_html(
        title,
        series,
        width=width,
        height=height,
        color_mode="comparison",
        show_fit=False,
        performance_mode=performance_mode,
        label_layout="density_stack",
        x_axis_label="Cycle",
        y_axis_label="Specific capacity (mAh/g)",
        table_html=capacity_overlay_table(series),
    )


def capacity_overlay_table(series: list[dict[str, Any]]) -> str:
    rows = sorted(series, key=lambda item: capacity_table_sort_key(item))
    body = []
    for idx, item in enumerate(rows):
        condition = item.get("condition") or {}
        metrics = item.get("metrics") or {}
        match = item.get("match")
        color = item["color"]
        series_id = html.escape(str(item.get("series_id") or f"series-{idx}"))
        row_number = getattr(match, "journal_row", None) or getattr(match, "row_prefix", None) or ""
        protocol_text = f"{item.get('protocol_cluster_id') or ''} {item.get('protocol_label') or ''}".strip()
        protocol_title = item.get("protocol_reason") or protocol_text
        bend_count = item.get("bend_count")
        curve_kind = item.get("curve_kind") or ""
        graph_label = item.get("sample_label") or item["short_label"]
        body.append(
            f'<tr data-series-id="{series_id}">'
            f'<td class="graph-cell" title="{html.escape(str(graph_label))}"><span class="swatch" style="background:{color};"></span>{html.escape(str(item["short_label"]))}</td>'
            f'<td class="nowrap-cell">{html.escape(str(curve_kind))}</td>'
            f'<td class="num-cell">{html.escape(str(row_number or ""))}</td>'
            f"<td class=\"text-cell\" title=\"{html.escape(str(protocol_title or ''))}\">{html.escape(str(protocol_text or ''))}</td>"
            f'<td class="num-cell">{html.escape(str(bend_count if bend_count not in (None, "") else ""))}</td>'
            f"<td class=\"num-cell\">{html.escape(format_capacity_ice(metrics))}</td>"
            f"<td class=\"num-cell\">{html.escape(format_electrode_density(condition))}</td>"
            f"<td class=\"num-cell\">{format_optional(condition.get('areal_mass_density'))}</td>"
            f"<td class=\"text-cell\" title=\"{html.escape(str(condition.get('electrolyte') or ''))}\">{html.escape(str(condition.get('electrolyte') or ''))}</td>"
            f"<td class=\"text-cell\" title=\"{html.escape(str(condition.get('binder') or ''))}\">{html.escape(str(condition.get('binder') or ''))}</td>"
            f"<td class=\"nowrap-cell\">{html.escape(str(condition.get('voltage_range') or ''))}</td>"
            f"<td class=\"nowrap-cell\">{html.escape(str(condition.get('ratio') or ''))}</td>"
            f"<td class=\"num-cell\">{format_optional(metrics.get('first_discharge_capacity'))}</td>"
            f"<td class=\"num-cell\">{format_optional(metrics.get('last_discharge_capacity'))}</td>"
            f"<td class=\"num-cell\">{format_optional(metrics.get('retention@100'))}</td>"
            "</tr>"
        )
    legend = (
        '<div style="background:#fff;padding:6px 7px 5px;border-bottom:1px solid #d7dce2;">'
        '<div style="height:7px;border-radius:999px;background:linear-gradient(90deg,#2563eb,#0ea5e9,#22c55e,#f59e0b,#dc2626);"></div>'
        '<div style="margin-top:4px;color:#475569;font-size:9.5px;line-height:1.25;white-space:normal;">'
        'Color encodes Areal mass density: blue=lower, red=higher. Rows toggle graph visibility.'
        '</div></div>'
    )
    header = (
        '<th style="text-align:left;">Graph</th>'
        '<th style="text-align:left;">Curve</th>'
        '<th style="text-align:right;">Row</th>'
        '<th style="text-align:left;">Type</th>'
        '<th style="text-align:right;">Bends</th>'
        '<th style="text-align:right;">ICE</th>'
        '<th style="text-align:right;">Density</th>'
        '<th style="text-align:right;">Areal</th>'
        '<th style="text-align:left;">전해질</th>'
        '<th style="text-align:left;">Binder</th>'
        '<th style="text-align:left;">Voltage</th>'
        '<th style="text-align:left;">ratio</th>'
        '<th style="text-align:right;">First</th>'
        '<th style="text-align:right;">Last</th>'
        '<th style="text-align:right;">R@100</th>'
    )
    return overlay_table_shell(header, "".join(body), legend)


def capacity_table_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    condition = item.get("condition") or {}
    areal = to_float(condition.get("areal_mass_density"))
    return (areal if areal is not None else 1e18, str(item.get("short_label") or ""))


def eis_overlay_svg(
    title: str,
    series: list[dict[str, Any]],
    width: int = 820,
    height: int = 560,
    show_fit: bool = False,
    performance_mode: bool = False,
    label_layout: str = "repel",
) -> str:
    return overlay_viewer_svg(
        title,
        series,
        width=width,
        height=height,
        show_fit=show_fit,
        performance_mode=performance_mode,
        label_layout=label_layout,
        x_axis_label="Z&apos; (ohm)",
        y_axis_label="-Z&apos;&apos; (ohm)",
        fit_shape_builder=overlay_fit_shape,
    )


def overlay_basic_table(series: list[dict[str, Any]]) -> str:
    body = []
    for idx, item in enumerate(series):
        color = html.escape(str(item.get("color") or "#64748b"))
        series_id = html.escape(str(item.get("series_id") or f"series-{idx}"))
        label = html.escape(str(item.get("short_label") or item.get("label") or f"series {idx + 1}"))
        body.append(
            f'<tr data-series-id="{series_id}">'
            f'<td class="graph-cell" title="{label}"><span class="swatch" style="background:{color};"></span>{label}</td>'
            "</tr>"
        )
    return overlay_table_shell("<th style=\"text-align:left;\">Graph</th>", "".join(body))


def overlay_table_shell(header_html: str, body_html: str, legend: str = "") -> str:
    return (
        legend
        + '<style>'
        '.eis-overlay-shell table th,.eis-overlay-shell table td{padding:4px 5px;border-bottom:1px solid #e5e7eb;vertical-align:top;}'
        '.eis-overlay-shell table th{font-weight:700;color:#334155;background:#f8fafc;white-space:nowrap;line-height:1.1;}'
        '.eis-overlay-shell .graph-cell{white-space:nowrap;max-width:170px;overflow:hidden;text-overflow:ellipsis;font-weight:600;}'
        '.eis-overlay-shell .text-cell{max-width:128px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
        '.eis-overlay-shell .nowrap-cell{white-space:nowrap;}'
        '.eis-overlay-shell .num-cell{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;}'
        '.eis-overlay-shell .swatch{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:-1px;}'
        '.eis-overlay-shell tr[data-series-id]{cursor:pointer;}'
        '.eis-overlay-shell tr.inactive-row td{background:#475569;color:#e2e8f0;opacity:.72;}'
        '</style>'
        '<table style="border-collapse:collapse;font-size:9.5px;line-height:1.18;width:max-content;min-width:100%;table-layout:auto;">'
        '<thead style="position:sticky;top:0;background:#f8fafc;z-index:1;">'
        f"<tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
    )


def overlay_viewer_svg(
    title: str,
    series: list[dict[str, Any]],
    width: int = 820,
    height: int = 560,
    show_fit: bool = False,
    performance_mode: bool = False,
    label_layout: str = "repel",
    x_axis_label: str = "X",
    y_axis_label: str = "Y",
    fit_shape_builder: Any | None = None,
) -> str:
    fit_shapes = [fit_shape_builder(item) for item in series] if show_fit and fit_shape_builder else []
    all_points = [point for item in series for point in item["points"]]
    for shape in fit_shapes:
        all_points.extend(shape.get("segment") or [])
        all_points.extend(shape.get("circle") or [])
        all_points.extend(shape.get("intercepts") or [])
    if not all_points:
        return ""
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    x_tick_step, y_tick_step = overlay_tick_steps(min(xs), max(xs), min(ys), max(ys), show_fit=show_fit)
    domain_x_min, domain_x_max = tick_range(min(xs), max(xs), x_tick_step)
    domain_y_min, domain_y_max = tick_range(min(ys), max(ys), y_tick_step)
    if show_fit:
        x_floor, y_floor = 0.0, -40.0
    else:
        x_floor, y_floor = -50.0, -50.0
    view_x_min, view_x_max = tick_range(max(x_floor, min(xs)), max(xs), x_tick_step)
    view_y_min, view_y_max = tick_range(max(y_floor, min(ys)), max(ys), y_tick_step)
    left, right, top, bottom = 76, 30, 48, 66
    plot_w = width - left - right
    plot_h = height - top - bottom
    if show_fit:
        view_x_min, view_x_max, view_y_min, view_y_max = equal_ohm_plot_range(
            view_x_min,
            view_x_max,
            view_y_min,
            view_y_max,
            plot_w,
            plot_h,
            x_tick_step,
            y_tick_step,
            x_floor=x_floor,
            y_floor=y_floor,
        )
    else:
        view_y_min, view_y_max = compressed_y_range(view_y_min, view_y_max, view_x_min, view_x_max, plot_w, plot_h)
        view_x_min = min(view_x_min, x_floor)
        view_y_min = min(view_y_min, y_floor)
    x_min = min(domain_x_min, view_x_min)
    x_max = max(domain_x_max, view_x_max)
    y_min = min(domain_y_min, view_y_min)
    y_max = max(domain_y_max, view_y_max)
    if show_fit:
        x_min, x_max, y_min, y_max = equalize_plot_domain(x_min, x_max, y_min, y_max, plot_w, plot_h)

    def sx(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    def path_for(points: list[tuple[float, float]]) -> str:
        return " ".join(("M" if idx == 0 else "L") + f" {sx(x):.2f} {sy(y):.2f}" for idx, (x, y) in enumerate(points))

    if performance_mode:
        label_positions = {}
    elif label_layout == "time_series":
        label_positions = overlay_time_series_label_positions(series, sx, sy, left, top, plot_w, plot_h)
    elif label_layout == "density_stack":
        label_positions = overlay_density_stack_label_positions(series, sx, sy, left, top, plot_w, plot_h)
    else:
        label_positions = overlay_label_positions(series, sx, sy, left, top, plot_w, plot_h)
    items = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'data-plot-left="{left}" data-plot-top="{top}" data-plot-width="{plot_w}" data-plot-height="{plot_h}" '
            f'data-x-min="{x_min}" data-x-max="{x_max}" data-y-min="{y_min}" data-y-max="{y_max}" '
            f'data-view-x-min="{view_x_min}" data-view-x-max="{view_x_max}" data-view-y-min="{view_y_min}" data-view-y-max="{view_y_max}">'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="29" font-family="Arial" font-size="18" font-weight="700">{html.escape(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#111" stroke-width="1.2"/>',
        f'<text x="{left + plot_w / 2}" y="{height - 20}" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">{x_axis_label}</text>',
        f'<text x="18" y="{top + plot_h / 2}" transform="rotate(-90 18 {top + plot_h / 2})" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">{y_axis_label}</text>',
        '<g data-x-axis-layer></g>',
        '<g data-y-axis-layer></g>',
        f'<clipPath id="eis-plot-clip"><rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}"/></clipPath>',
        '<g data-zoom-layer clip-path="url(#eis-plot-clip)">',
    ]
    label_items = []
    if y_min <= 0 <= y_max:
        y0 = sy(0)
        items.append(f'<line x1="{left}" y1="{y0:.1f}" x2="{left + plot_w}" y2="{y0:.1f}" stroke="#999" stroke-dasharray="4 4"/>')
    for idx, item in enumerate(series):
        color = item["color"]
        points = item["points"]
        series_id = html.escape(str(item.get("series_id") or f"series-{idx}"))
        marker_shape = str(item.get("marker_shape") or "circle")
        stroke_width = "1.15" if performance_mode else "1.9"
        stroke_opacity = ".54" if performance_mode else ".82"
        items.append(f'<g data-series-id="{series_id}">')
        items.append(
            f'<path data-zoom-stroke data-base-stroke-width="{stroke_width}" d="{path_for(points)}" '
            f'fill="none" stroke="{color}" stroke-width="{stroke_width}" opacity="{stroke_opacity}"/>'
        )
        if not performance_mode or marker_shape in {"circle", "square"}:
            marker_step = max(1, len(points) // (120 if performance_mode else 80))
            for x, y in points[::marker_step]:
                items.append(overlay_marker_svg(marker_shape, sx(x), sy(y), color))
        if show_fit and idx < len(fit_shapes):
            shape = fit_shapes[idx]
            segment = shape.get("segment") or []
            circle = shape.get("circle") or []
            intercepts = shape.get("intercepts") or []
            if segment:
                items.append(f'<path data-zoom-stroke data-base-stroke-width="2.25" d="{path_for(segment)}" fill="none" stroke="#ef4444" stroke-width="2.25" opacity=".92"/>')
                if not performance_mode:
                    for x, y in segment[:: max(1, len(segment) // 36)]:
                        items.append(f'<circle data-zoom-radius data-base-radius="2.05" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="2.05" fill="#ef4444" opacity=".78"/>')
            if circle:
                circle_points = circle[::2] if performance_mode else circle
                items.append(f'<path data-zoom-stroke data-base-stroke-width=".75" d="{path_for(circle_points)}" fill="none" stroke="#2563eb" stroke-width=".75" opacity=".34"/>')
            for x, y in intercepts:
                items.append(
                    f'<circle data-zoom-radius data-base-radius="1.65" data-zoom-stroke data-base-stroke-width=".45" '
                    f'cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="1.65" fill="#f4a742" stroke="#111" stroke-width=".45" opacity=".82"/>'
                )
        if points and idx in label_positions:
            label = str(item["label"])
            last_x, last_y = points[-1]
            label_x, label_y = label_positions[idx][:2]
            end_x, end_y = sx(last_x), sy(last_y)
            label_w = overlay_label_width(label)
            if len(label_positions[idx]) > 2 and label_positions[idx][2] == "left_corner":
                leader_x = label_x
                leader_y = label_y - 4
            elif len(label_positions[idx]) > 2 and label_positions[idx][2] == "left":
                leader_x = label_x
                leader_y = label_y - 4
            else:
                leader_x = label_x + label_w + 4 if label_x < end_x else label_x - 5
                leader_y = label_y - 4
            label_items.append(f'<g data-series-id="{series_id}" data-label-group data-label-index="{idx}">')
            label_items.append(f'<line data-label-line data-base-stroke-width="0.8" x1="{end_x:.1f}" y1="{end_y:.1f}" x2="{leader_x:.1f}" y2="{leader_y:.1f}" stroke="{color}" stroke-width=".8" opacity=".55"/>')
            label_items.append(f'<text data-label-text data-base-font-size="8.8" x="{label_x:.1f}" y="{label_y:.1f}" font-family="Arial" font-size="8.8" fill="{color}">{html.escape(label[:78])}</text>')
            label_items.append("</g>")
        items.append("</g>")
    items.append("</g>")
    if label_items:
        items.append('<g data-label-zoom-layer>')
        items.extend(label_items)
        items.append("</g>")
    items.append("</svg>")
    return "\n".join(items)


def overlay_marker_svg(shape: str, x: float, y: float, color: str) -> str:
    radius = 2.05
    size = radius * 2
    if shape == "square":
        half = size / 2
        return (
            f'<rect data-zoom-radius data-base-radius="{half}" data-zoom-stroke data-base-stroke-width=".45" '
            f'data-cx="{x:.2f}" data-cy="{y:.2f}" x="{x - half:.2f}" y="{y - half:.2f}" '
            f'width="{size}" height="{size}" fill="{color}" fill-opacity=".24" stroke="#111111" stroke-width=".45" opacity=".95"/>'
        )
    return (
        f'<circle data-zoom-radius data-base-radius="{radius}" data-zoom-stroke data-base-stroke-width=".45" '
        f'cx="{x:.2f}" cy="{y:.2f}" r="{radius}" '
        f'fill="{color}" fill-opacity=".24" stroke="#111111" stroke-width=".45" opacity=".95"/>'
    )


def overlay_fit_shape(item: dict[str, Any]) -> dict[str, Any]:
    points = item.get("points") or []
    fit = item.get("fit") or {}
    start = fit.get("segment_start_index")
    end = fit.get("segment_end_index")
    segment = []
    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
        if end < len(points):
            segment = points[start : end + 1]
        else:
            original_count = item.get("original_point_count")
            if isinstance(original_count, int) and original_count > 1 and len(points) > 1:
                scale = (len(points) - 1) / (original_count - 1)
                display_start = max(0, min(len(points) - 1, round(start * scale)))
                display_end = max(display_start, min(len(points) - 1, round(end * scale)))
                segment = points[display_start : display_end + 1]
    xc = to_float(fit.get("center_x_ohm"))
    yc = to_float(fit.get("center_y_ohm"))
    radius = to_float(fit.get("radius_ohm"))
    circle = []
    if xc is not None and yc is not None and radius is not None and radius > 0:
        circle = [
            (xc + radius * math.cos(2 * math.pi * idx / 180), yc + radius * math.sin(2 * math.pi * idx / 180))
            for idx in range(181)
        ]
    intercepts = [(x, 0.0) for x in (to_float(fit.get("x_left_intercept_ohm")), to_float(fit.get("x_right_intercept_ohm"))) if x is not None]
    return {"segment": segment, "circle": circle, "intercepts": intercepts}


def format_depression(value: Any) -> str:
    number = to_float(value)
    return "?" if number is None else f"{number:.1f}°"


def overlay_tick_steps(x_min: float, x_max: float, y_min: float, y_max: float, show_fit: bool = False) -> tuple[int, int]:
    span = max(x_max - x_min, y_max - y_min)
    if show_fit:
        return 50, 20
    return 50, 50


def equal_ohm_plot_range(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    plot_w: float,
    plot_h: float,
    x_tick_step: int,
    y_tick_step: int,
    x_floor: float,
    y_floor: float,
) -> tuple[float, float, float, float]:
    x_min = x_floor
    y_min = y_floor
    x_span = max(1e-12, x_max - x_min)
    y_span = max(1e-12, y_max - y_min)
    ohm_per_pixel = max(x_span / plot_w, y_span / plot_h)
    x_max = math.ceil((x_min + ohm_per_pixel * plot_w) / x_tick_step) * x_tick_step
    ohm_per_pixel = (x_max - x_min) / plot_w
    y_max = y_min + ohm_per_pixel * plot_h
    if y_max < y_floor + y_span:
        y_max = y_floor + y_span
        ohm_per_pixel = (y_max - y_min) / plot_h
        x_max = x_min + ohm_per_pixel * plot_w
    return x_min, x_max, y_min, y_max


def equalize_plot_domain(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    plot_w: float,
    plot_h: float,
) -> tuple[float, float, float, float]:
    ohm_per_pixel = max((x_max - x_min) / plot_w, (y_max - y_min) / plot_h)
    return x_min, x_min + ohm_per_pixel * plot_w, y_min, y_min + ohm_per_pixel * plot_h


def overlay_time_series_label_positions(
    series: list[dict[str, Any]],
    sx: Any,
    sy: Any,
    _left: float,
    _top: float,
    _plot_w: float,
    _plot_h: float,
) -> dict[int, tuple[float, float, str]]:
    anchors = []
    for idx, item in enumerate(series):
        points = item.get("points") or []
        if not points:
            continue
        last_x, last_y = points[-1]
        label = str(item.get("label") or item.get("short_label") or "")
        time_hours = item.get("time_hours")
        sort_time = time_hours if time_hours is not None else float(idx)
        anchors.append(
            {
                "idx": idx,
                "x": sx(last_x),
                "y": sy(last_y),
                "w": overlay_label_width(label),
                "time": sort_time,
            }
        )
    if not anchors:
        return {}
    anchors.sort(key=lambda row: row["time"])
    latest = anchors[-1]
    line_gap = 12.0
    base_x = latest["x"] + 10.0
    base_y = latest["y"] + 10.0
    return {int(anchor["idx"]): (base_x, base_y - (len(anchors) - 1 - pos) * line_gap, "left") for pos, anchor in enumerate(anchors)}


def overlay_density_stack_label_positions(
    series: list[dict[str, Any]],
    sx: Any,
    sy: Any,
    left: float,
    top: float,
    plot_w: float,
    plot_h: float,
) -> dict[int, tuple[float, float, str]]:
    anchors = []
    all_points = []
    for idx, item in enumerate(series):
        points = item.get("points") or []
        if not points:
            continue
        all_points.extend(points)
        condition = item.get("condition") or {}
        areal = to_float(condition.get("areal_mass_density"))
        label = str(item.get("label") or item.get("short_label") or "")
        anchors.append(
            {
                "idx": idx,
                "areal": areal if areal is not None else float("inf"),
                "label": label,
                "sort_label": str(item.get("short_label") or label),
                "curve": str(item.get("curve_kind") or ""),
            }
        )
    if not anchors or not all_points:
        return {}

    reference = sorted(all_points, key=lambda point: point[0], reverse=True)[min(2, len(all_points) - 1)]
    max_label_width = max(overlay_label_width(row["label"]) for row in anchors)
    label_x = sx(reference[0]) + 5
    label_x = max(left + 4, min(left + plot_w - max_label_width - 8, label_x))

    line_gap = 12.0
    base_y = sy(reference[1]) + 40
    min_base_y = top + line_gap * (len(anchors) - 1) + 12
    max_base_y = top + plot_h - 6
    base_y = max(min_base_y, min(max_base_y, base_y))

    ordered = sorted(anchors, key=lambda row: (row["areal"], row["sort_label"], row["curve"]))
    return {int(row["idx"]): (label_x, base_y - pos * line_gap, "left_corner") for pos, row in enumerate(ordered)}


def overlay_label_positions(
    series: list[dict[str, Any]],
    sx: Any,
    sy: Any,
    left: float,
    top: float,
    plot_w: float,
    plot_h: float,
) -> dict[int, tuple[float, float]]:
    anchors = []
    graph_points = []
    for idx, item in enumerate(series):
        points = item.get("points") or []
        if not points:
            continue
        step = max(1, len(points) // 90)
        graph_points.extend((sx(x), sy(y)) for x, y in points[::step])
        last_x, last_y = points[-1]
        end_x = sx(last_x)
        end_y = sy(last_y)
        label = str(item.get("label") or item.get("short_label") or "")
        label_w = overlay_label_width(label)
        label_h = 11.0
        anchors.append({"idx": idx, "x": end_x, "y": end_y, "w": label_w, "h": label_h})

    lower = top + 12
    upper = top + plot_h - 18
    placed: list[dict[str, float]] = []
    output = {}
    for anchor in sorted(anchors, key=lambda row: row["y"]):
        candidates = label_position_candidates(anchor, lower, upper, left + 8, left + plot_w - anchor["w"] - 8)
        best = min(candidates, key=lambda candidate: label_position_cost(candidate, anchor, placed, graph_points, left, top))
        placed.append(best)
        output[int(anchor["idx"])] = (best["x"], best["y"])
    return output


def overlay_label_width(label: str) -> float:
    return min(360.0, max(80.0, len(label) * 4.9))


def label_position_candidates(
    anchor: dict[str, float],
    lower: float,
    upper: float,
    min_x: float,
    max_x: float,
) -> list[dict[str, float]]:
    candidates = []
    left_dx_values = [8.0, 12.0, 18.0, 26.0, 36.0, 50.0]
    right_dx_values = [10.0, 18.0, 30.0, 44.0]
    dy_values = [-24.0, -16.0, -8.0, 0.0, 8.0, 16.0, -34.0, 28.0, -44.0, 38.0]
    for side, dx_values in (("left", left_dx_values), ("right", right_dx_values)):
        for dx in dx_values:
            x = anchor["x"] - anchor["w"] - dx if side == "left" else anchor["x"] + dx
            x = min(max(x, min_x), max_x)
            for dy in dy_values:
                y = min(max(anchor["y"] + dy, lower), upper)
                attach_x = x + anchor["w"] + 4 if x < anchor["x"] else x - 5
                attach_y = y - 4
                candidates.append(
                    {
                        "x": x,
                        "y": y,
                        "w": anchor["w"],
                        "h": anchor["h"],
                        "side": side,
                        "leader": math.hypot(attach_x - anchor["x"], attach_y - anchor["y"]),
                    }
                )
    return candidates


def label_position_cost(
    candidate: dict[str, float],
    anchor: dict[str, float],
    placed: list[dict[str, float]],
    graph_points: list[tuple[float, float]],
    left: float,
    top: float,
) -> float:
    cost = candidate["leader"] * 3.1 + abs(candidate["y"] - anchor["y"]) * 0.55
    if candidate["leader"] > 72:
        cost += (candidate["leader"] - 72) * 18
    if candidate.get("side") == "left":
        cost -= 55
    else:
        cost += 65
    if candidate["y"] <= anchor["y"]:
        cost -= 28
    else:
        cost += 18
    cost += max(0.0, candidate["y"] - top) * 0.06
    box = label_box(candidate)
    for other in placed:
        overlap = rect_overlap_area(box, label_box(other))
        if overlap:
            cost += 12_000 + overlap * 45
        vertical_gap = max(0.0, max(box[1], label_box(other)[1]) - min(box[3], label_box(other)[3]))
        if vertical_gap < 2:
            cost += (2 - vertical_gap) * 45
    expanded = (box[0] - 5, box[1] - 4, box[2] + 5, box[3] + 4)
    for x, y in graph_points:
        if expanded[0] <= x <= expanded[2] and expanded[1] <= y <= expanded[3]:
            cost += 650
    return cost


def label_box(label: dict[str, float]) -> tuple[float, float, float, float]:
    return (label["x"] - 2, label["y"] - 10, label["x"] + label["w"], label["y"] + label["h"])


def rect_overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def tick_range(min_value: float, max_value: float, step: int) -> tuple[float, float]:
    if math.isclose(min_value, max_value):
        min_value -= step
        max_value += step
    return math.floor(min_value / step) * step, math.ceil(max_value / step) * step


def numeric_ticks(min_value: float, max_value: float, step: int) -> list[float]:
    start = int(math.floor(min_value / step) * step)
    end = int(math.ceil(max_value / step) * step)
    return [float(value) for value in range(start, end + step, step)]


def compressed_y_range(
    y_min: float,
    y_max: float,
    x_min: float,
    x_max: float,
    plot_w: float,
    plot_h: float,
) -> tuple[float, float]:
    x_span = max(1e-9, x_max - x_min)
    y_span = max(1e-9, y_max - y_min)
    target_y_span = x_span * 2.0 * plot_h / max(1e-9, plot_w)
    if y_span >= target_y_span:
        return y_min, y_max
    center = (y_min + y_max) / 2
    half = target_y_span / 2
    return tick_range(center - half, center + half, 100)


def format_optional(value: Any) -> str:
    number = to_float(value)
    return "?" if number is None else f"{number:.2f}"


def collect_source_files(root: Path, suffixes: set[str], *, recursive: bool = True) -> list[Path]:
    if not root.exists():
        return []
    iterator = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file()
        and path.suffix.lower() in suffixes
        and not path.name.startswith(("~$", "."))
        and "processed" not in path.parts
    )


def render_eis_live_viewer(st: Any, components: Any) -> None:
    source_paths = collect_source_files(EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    if not source_paths:
        return
    st.markdown("### 원본 EIS 라이브 뷰어")
    labels = [str(path.relative_to(EIS_ROOT)) for path in source_paths]
    selected_label = st.selectbox("Source graph", labels, key="eis_source_graph")
    selected_path = EIS_ROOT / selected_label
    show_fit = st.toggle("fitting circle 보기 (1:1 Ohm 축척)", value=False, key="eis_live_fit")
    try:
        dataset = parse_file(selected_path)
        points = eis_points(dataset)
        metadata = load_valid_fit_metadata(selected_path) if show_fit and load_valid_fit_metadata is not None else None
        svg = eis_fit_svg(
            f"{dataset.meta.cell_id} Nyquist plot",
            points,
            metadata,
            width=980,
            height=560,
            equal_aspect=show_fit,
            show_last_label=True,
        )
        if svg:
            components.html(svg, height=590, scrolling=True)
        else:
            st.warning("이 파일에서 표시할 EIS 좌표를 찾지 못했습니다.")
        st.caption(str(selected_path))
    except Exception as exc:
        st.error(f"원본 EIS 파일을 읽지 못했습니다: {exc}")


def eis_points(dataset: Any) -> list[tuple[float, float]]:
    points = []
    for row in dataset.rows:
        x = to_float(row.get("z_real"))
        y_raw = to_float(row.get("z_imag"))
        if x is not None and y_raw is not None:
            points.append((x, -y_raw))
    return points


def capacity_points(dataset: Any) -> list[tuple[float, float]]:
    return capacity_discharge_points(dataset) or capacity_charge_points(dataset)


def capacity_charge_points(dataset: Any) -> list[tuple[float, float]]:
    return capacity_curve_points(dataset, "charge_capacity")


def capacity_discharge_points(dataset: Any) -> list[tuple[float, float]]:
    return capacity_curve_points(dataset, "discharge_capacity")


def capacity_curve_points(dataset: Any, capacity_key: str) -> list[tuple[float, float]]:
    points = []
    for row in dataset.rows:
        cycle = to_float(row.get("cycle"))
        capacity = to_float(row.get(capacity_key))
        if cycle is not None and capacity is not None and capacity > 0:
            points.append((cycle, capacity))
    return sorted(points, key=lambda point: point[0])


def collect_capacity_summary_sources() -> list[Path]:
    return [path for path in collect_source_files(CAPACITY_ROOT, {".csv", ".xlsx", ".xls"}) if is_capacity_summary_source(path)]


def is_capacity_summary_source(path: Path) -> bool:
    name = path.name.lower()
    if "diff_anal" in name or name.endswith("_cycle.csv"):
        return False
    return "capacity" in name


def render_capacity_live_viewer(st: Any, components: Any) -> None:
    source_paths = collect_source_files(CAPACITY_ROOT, {".wrd", ".csv", ".xlsx", ".xls"})
    if not source_paths:
        return
    st.markdown("### 원본 Capacity/WRD 라이브 뷰어")
    labels = [str(path.relative_to(CAPACITY_ROOT)) for path in source_paths]
    selected_label = st.selectbox("Source capacity file", labels, key="capacity_source_graph")
    selected_path = CAPACITY_ROOT / selected_label
    try:
        if selected_path.suffix.lower() == ".wrd":
            records, validation = parse_wrd_file(selected_path)
            summary = build_capacity_summary(records)
            st.caption(
                f"WRD records {validation.get('record_count')} · cycle "
                f"{validation.get('cycle_min_export_number')} → {validation.get('cycle_max_export_number')}"
            )
            st.dataframe(summary, use_container_width=True, height=260)
            svg = wrd_voltage_profile_svg(selected_path.name, records)
            if svg:
                components.html(svg, height=500, scrolling=True)
        else:
            dataset = parse_file(selected_path)
            st.dataframe(dataset.rows[:300], use_container_width=True, height=260)
        st.caption(str(selected_path))
    except Exception as exc:
        st.error(f"원본 capacity 파일을 읽지 못했습니다: {exc}")


def wrd_voltage_profile_svg(title: str, records: list[Any]) -> str:
    charge = []
    discharge = []
    for record in records:
        capacity_mah = max(record.charge_q_ah, record.discharge_q_ah) * 1000
        if capacity_mah <= 0:
            continue
        if record.current_a >= 0:
            charge.append((capacity_mah, record.voltage_v))
        else:
            discharge.append((capacity_mah, record.voltage_v))
    series = []
    if charge:
        series.append(("Charge voltage", charge, "#f4a742"))
    if discharge:
        series.append(("Discharge voltage", discharge, "#111111"))
    return multi_line_svg(
        f"{title} voltage profile",
        series,
        "Capacity (mAh)",
        "Voltage (V)",
        hide_markers=True,
        width=980,
        height=480,
    )


def render_artifact_viewer(st: Any, components: Any, artifacts: list[AnalysisArtifact], key_prefix: str) -> None:
    if not artifacts:
        st.info("아직 표시할 그래프 산출물이 없습니다.")
        return
    selected_name = st.selectbox("Graph", [artifact.name for artifact in artifacts], key=f"{key_prefix}_artifact")
    artifact = next(item for item in artifacts if item.name == selected_name)
    if artifact.path.suffix.lower() == ".svg":
        components.html(artifact.path.read_text(encoding="utf-8"), height=470, scrolling=True)
    else:
        st.image(str(artifact.path))
    st.caption(str(artifact.path))


def collect_analysis_artifacts(analysis_type: str) -> list[AnalysisArtifact]:
    root = ANALYSIS_OUTPUT_ROOT / analysis_type
    if not root.exists():
        return []
    artifacts = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg"}:
            artifacts.append(AnalysisArtifact(path=path, name=path.name, analysis_type=analysis_type))
    return sorted(artifacts, key=lambda item: item.name.lower())


def count_files(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes and not path.name.startswith("."))
