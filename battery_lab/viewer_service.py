from __future__ import annotations

import json
import threading
from html import escape
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .capacity_matching import CAPACITY_PROTOCOL_CLUSTER_IDS, CAPACITY_PROTOCOL_LABELS, CAPACITY_PROTOCOL_ORDER, build_capacity_match_report
from .conditions import read_conditions
from .eis_matching import EIS_SUFFIXES, build_eis_match_report
from .file_io import parse_file
from .metrics import to_float
from .plots import eis_fit_svg
from . import render_cache
from . import ui as streamlit_ui
from wonatech_parsers.wrd import build_capacity_summary, parse_wrd_file


CAPACITY_SUMMARY_SUFFIXES = {".csv", ".xlsx", ".xls"}
CAPACITY_LIVE_SUFFIXES = {".wrd", ".csv", ".xlsx", ".xls"}
_STREAMLIT_UI_LOCK = threading.RLock()


@contextmanager
def streamlit_roots(eis_root: Path, capacity_root: Path) -> Any:
    with _STREAMLIT_UI_LOCK:
        old_eis_root = streamlit_ui.EIS_ROOT
        old_capacity_root = streamlit_ui.CAPACITY_ROOT
        streamlit_ui.EIS_ROOT = eis_root
        streamlit_ui.CAPACITY_ROOT = capacity_root
        try:
            yield
        finally:
            streamlit_ui.EIS_ROOT = old_eis_root
            streamlit_ui.CAPACITY_ROOT = old_capacity_root


def eis_viewer_options(eis_root: Path, capacity_root: Path, condition_workbook: Path, override_path: Path) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        source_paths, conditions, report = build_eis_viewer_report(eis_root, condition_workbook, override_path)
        source_options = path_options(source_paths, eis_root)
        time_series_options = [
            {
                "value": group.group_id,
                "label": f"{group.group_id} · {group.condition_sample or group.group_key} · {group.file_count} files",
                "file_count": group.file_count,
                "source_paths": group.source_paths,
            }
            for group in report.time_series_groups
            if group.file_count >= 2
        ]
        comparison_options = [
            {
                "value": cluster.cluster_id,
                "label": (
                    f"{cluster.cluster_id} · {cluster.condition_count} conditions · "
                    f"loading {format_optional(cluster.loading_min)}-{format_optional(cluster.loading_max)}"
                ),
                "file_count": cluster.file_count,
                "source_paths": cluster.source_paths,
            }
            for cluster in report.comparison_clusters
            if cluster.file_count >= 2
        ]
        if source_options:
            comparison_options.insert(
                min(3, len(comparison_options)),
                {
                    "value": "C999",
                    "label": f"C999 · all EIS datasets · {len(source_options)} files",
                    "file_count": len(source_options),
                    "source_paths": ";".join(option["value"] for option in source_options),
                },
            )
        return {
            "available": True,
            "source_count": len(source_paths),
            "condition_count": len(conditions),
            "status_counts": report.status_counts,
            "class_counts": report.class_counts,
            "source_options": source_options,
            "time_series_options": time_series_options,
            "comparison_options": comparison_options,
            "time_series_groups": [asdict(row) for row in report.time_series_groups[:200]],
            "comparison_clusters": [asdict(row) for row in report.comparison_clusters[:200]],
            "comparison_pairs": [asdict(row) for row in report.comparison_pairs[:200]],
        }


def finder_html(eis_root: Path, capacity_root: Path, *, kind: str) -> str:
    with streamlit_roots(eis_root, capacity_root):
        if kind == "eis":
            root = eis_root
            label = "EIS"
        elif kind == "capacity":
            root = capacity_root
            label = "Capacity"
        else:
            raise ValueError(f"Unsupported finder kind: {kind}")
        return streamlit_ui.render_finder_html({"roots": [streamlit_ui.finder_tree(label, root)]})


def eis_overlay_payload(
    eis_root: Path,
    capacity_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    mode: str,
    key: str,
    show_fit: bool = False,
) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        source_paths, conditions, report = build_eis_viewer_report(eis_root, condition_workbook, override_path)
        all_rel_paths = [str(path.relative_to(eis_root)) for path in source_paths]
        title = "EIS Nyquist"
        color_mode = "comparison"
        performance_mode = False

        if mode == "source":
            rel_paths = [key] if key else all_rel_paths[:1]
            title = f"{Path(rel_paths[0]).stem} Nyquist" if rel_paths else "EIS Nyquist"
        elif mode == "time_series":
            groups = [group for group in report.time_series_groups if group.file_count >= 2]
            group = next((item for item in groups if item.group_id == key), groups[0] if groups else None)
            rel_paths = [item for item in group.source_paths.split(";") if item] if group else []
            title = f"{group.condition_sample or group.group_key} time-series Nyquist" if group else "EIS time-series Nyquist"
            color_mode = "time_series"
        else:
            clusters = [cluster for cluster in report.comparison_clusters if cluster.file_count >= 2]
            if key == "C999" or (not key and all_rel_paths):
                rel_paths = all_rel_paths
                title = "C999 all EIS datasets Nyquist"
                performance_mode = True
            else:
                cluster = next((item for item in clusters if item.cluster_id == key), clusters[0] if clusters else None)
                rel_paths = [item for item in cluster.source_paths.split(";") if item] if cluster else all_rel_paths
                title = f"{cluster.cluster_id} comparison Nyquist" if cluster else "EIS comparison Nyquist"

        if not rel_paths:
            return {"available": False, "html": "", "errors": ["표시할 EIS source가 없습니다."], "title": title}

        series, errors = streamlit_ui.load_eis_overlay_series(
            rel_paths,
            report,
            conditions,
            color_mode=color_mode,
            performance_mode=performance_mode,
        )
        if performance_mode:
            errors = [error for error in errors if "좌표 스케일이 비정상적으로 커서" not in error]
        if show_fit:
            for item in series:
                item["label"] = streamlit_ui.overlay_fit_label(item)
        html_doc = streamlit_ui.eis_overlay_html(
            title,
            series,
            width=1180,
            height=590,
            color_mode=color_mode,
            show_fit=show_fit,
            performance_mode=performance_mode,
        )
        return {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}


def capacity_viewer_options(capacity_root: Path, eis_root: Path, condition_workbook: Path, override_path: Path) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        source_paths, summary_paths, conditions, report = build_capacity_viewer_report(capacity_root, condition_workbook, override_path)
        source_options = path_options(source_paths, capacity_root)
        summary_options = path_options(summary_paths, capacity_root)
        rel_paths = [option["value"] for option in summary_options]
        groups = streamlit_ui.capacity_protocol_path_groups(rel_paths)
        cluster_options = []
        for protocol_type in CAPACITY_PROTOCOL_ORDER:
            paths = groups.get(protocol_type, [])
            if not paths:
                continue
            cluster_options.append(
                {
                    "value": protocol_type,
                    "label": f"{CAPACITY_PROTOCOL_CLUSTER_IDS[protocol_type]} · {CAPACITY_PROTOCOL_LABELS[protocol_type]} · {len(paths)} files",
                    "file_count": len(paths),
                }
            )
        if rel_paths:
            cluster_options.insert(
                min(3, len(cluster_options)),
                {"value": "P999", "label": f"P999 · all Capacity datasets · {len(rel_paths)} files", "file_count": len(rel_paths)},
            )
        return {
            "available": True,
            "source_count": len(source_paths),
            "summary_source_count": len(summary_paths),
            "condition_count": len(conditions),
            "status_counts": report.status_counts,
            "source_options": source_options,
            "summary_options": summary_options,
            "cluster_options": cluster_options,
            "cluster_rows": streamlit_ui.capacity_protocol_path_cluster_rows(groups, len(rel_paths)),
        }


def capacity_source_payload(capacity_root: Path, eis_root: Path, rel_path: str) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        try:
            path = safe_child(capacity_root, rel_path)
            title = path.name
            if path.suffix.lower() == ".wrd":
                records, validation = parse_wrd_file(path)
                summary = build_capacity_summary(records)
                svg = streamlit_ui.wrd_voltage_profile_svg(path.name, records)
                table = rows_table_html(summary[:300])
                meta = (
                    f"WRD records {validation.get('record_count', 0)} · cycle "
                    f"{validation.get('cycle_min_export_number', '?')} -> {validation.get('cycle_max_export_number', '?')}"
                )
                html_doc = source_preview_html(title, meta, svg, table)
                return {
                    "available": bool(records),
                    "html": html_doc,
                    "errors": [] if records else ["WRD record를 찾지 못했습니다."],
                    "title": title,
                    "row_count": len(summary),
                }

            dataset = parse_file(path)
            graph = capacity_dataset_svg(title, dataset)
            table = rows_table_html(dataset.rows[:300])
            meta = f"{dataset.meta.analysis_type} · rows {len(dataset.rows)}"
            html_doc = source_preview_html(title, meta, graph, table)
            return {
                "available": bool(dataset.rows or graph),
                "html": html_doc,
                "errors": [],
                "title": title,
                "row_count": len(dataset.rows),
            }
        except Exception as exc:
            return {"available": False, "html": "", "errors": [str(exc)], "title": rel_path or "Capacity source", "row_count": 0}


def capacity_overlay_payload(
    capacity_root: Path,
    eis_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    mode: str,
    key: str,
) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        _, summary_paths, conditions, report = build_capacity_viewer_report(capacity_root, condition_workbook, override_path)
        rel_paths = [str(path.relative_to(capacity_root)) for path in summary_paths]
        title = "Capacity datasets"
        performance_mode = False
        if mode == "source":
            selected_paths = [key] if key else rel_paths[:1]
            title = f"{Path(selected_paths[0]).stem} Capacity" if selected_paths else title
        else:
            groups = streamlit_ui.capacity_protocol_path_groups(rel_paths)
            if key == "P999" or (not key and rel_paths):
                selected_paths = rel_paths
                title = "P999 all Capacity datasets"
                performance_mode = True
            else:
                protocol_type = key if key in groups else next((item for item in CAPACITY_PROTOCOL_ORDER if groups.get(item)), "")
                selected_paths = groups.get(protocol_type, [])
                title = f"{CAPACITY_PROTOCOL_CLUSTER_IDS.get(protocol_type, '')} {CAPACITY_PROTOCOL_LABELS.get(protocol_type, '')}".strip()

        if not selected_paths:
            return {"available": False, "html": "", "errors": ["표시할 Capacity summary source가 없습니다."], "title": title}

        series, errors = streamlit_ui.load_capacity_overlay_series(selected_paths, report, conditions, performance_mode=performance_mode)
        html_doc = streamlit_ui.capacity_overlay_html(title, series, width=1180, height=590, performance_mode=performance_mode)
        return {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}


def eis_source_payload(eis_root: Path, capacity_root: Path, rel_path: str, *, show_fit: bool = False) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        path = safe_child(eis_root, rel_path)
        dataset = parse_file(path)
        points = streamlit_ui.eis_points(dataset)
        metadata = streamlit_ui.valid_fit_metadata_cached(path) if show_fit else None
        html_doc = eis_fit_svg(
            f"{dataset.meta.cell_id} Nyquist plot",
            points,
            metadata,
            width=980,
            height=560,
            equal_aspect=show_fit,
            show_last_label=True,
        )
        return {"available": bool(html_doc), "html": html_doc, "errors": [], "title": path.name, "point_count": len(points)}


def build_eis_viewer_report(eis_root: Path, condition_workbook: Path, override_path: Path) -> tuple[list[Path], dict[str, dict[str, Any]], Any]:
    source_paths = streamlit_ui.collect_source_files(eis_root, EIS_SUFFIXES)
    conditions = render_cache.cached_read_conditions(condition_workbook)
    overrides = load_overrides(override_path)
    report = build_eis_match_report(source_paths, conditions, eis_root, overrides)
    return source_paths, conditions, report


def build_capacity_viewer_report(
    capacity_root: Path,
    condition_workbook: Path,
    override_path: Path,
) -> tuple[list[Path], list[Path], dict[str, dict[str, Any]], Any]:
    source_paths = streamlit_ui.collect_source_files(capacity_root, CAPACITY_LIVE_SUFFIXES)
    summary_paths = [path for path in streamlit_ui.collect_source_files(capacity_root, CAPACITY_SUMMARY_SUFFIXES) if streamlit_ui.is_capacity_summary_source(path)]
    conditions = render_cache.cached_read_conditions(condition_workbook)
    overrides = load_overrides(override_path)
    report = build_capacity_match_report(summary_paths, conditions, capacity_root, overrides)
    return source_paths, summary_paths, conditions, report


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def path_options(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    options = []
    for path in paths:
        rel_path = str(path.relative_to(root))
        options.append({"value": rel_path, "label": rel_path, "name": path.name})
    return options


def capacity_dataset_svg(title: str, dataset: Any) -> str:
    charge = streamlit_ui.capacity_charge_points(dataset)
    discharge = streamlit_ui.capacity_discharge_points(dataset)
    series = []
    if charge:
        series.append(("Charge capacity", charge, "#d97706"))
    if discharge:
        series.append(("Discharge capacity", discharge, "#111827"))
    if not series:
        return ""
    return streamlit_ui.multi_line_svg(
        f"{title} capacity",
        series,
        "Cycle",
        "Specific capacity (mAh/g)",
        hide_markers=True,
        width=980,
        height=480,
    )


def source_preview_html(title: str, meta: str, graph_html: str, table_html: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: #202733; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: 0; }}
    .source-preview {{ padding: 12px; }}
    .source-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin-bottom: 10px; }}
    h1 {{ margin: 0; font-size: 16px; line-height: 1.25; }}
    .meta {{ color: #647084; font-size: 12px; white-space: nowrap; }}
    .graph {{ overflow: auto; border: 1px solid #d8dee8; border-radius: 8px; min-height: 120px; display: grid; place-items: center; }}
    .table-wrap {{ margin-top: 12px; max-height: 300px; overflow: auto; border: 1px solid #d8dee8; border-radius: 8px; }}
    table {{ width: max-content; min-width: 100%; border-collapse: collapse; font-size: 12px; line-height: 1.25; }}
    th, td {{ border-bottom: 1px solid #e5e9f0; padding: 6px 8px; text-align: left; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #f7f8fa; z-index: 1; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .empty {{ color: #647084; padding: 32px; }}
  </style>
</head>
<body>
  <div class="source-preview">
    <div class="source-head"><h1>{escape(title)}</h1><div class="meta">{escape(meta)}</div></div>
    <div class="graph">{graph_html or '<div class="empty">표시할 source graph가 없습니다.</div>'}</div>
    <div class="table-wrap">{table_html}</div>
  </div>
</body>
</html>"""


def rows_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty">표시할 table row가 없습니다.</div>'
    headers = list(rows[0].keys())
    body = []
    for row in rows:
        cells = []
        for header in headers:
            value = row.get(header)
            class_name = ' class="num"' if isinstance(value, (int, float)) else ""
            cells.append(f"<td{class_name}>{escape(format_table_value(value))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{escape(str(header))}</th>" for header in headers)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def format_table_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def safe_child(root: Path, rel_path: str) -> Path:
    path = (root / rel_path).resolve()
    root_resolved = root.resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Invalid source path.") from exc
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(rel_path)
    return path


def format_optional(value: Any) -> str:
    number = to_float(value)
    return "?" if number is None else f"{number:.2f}"
