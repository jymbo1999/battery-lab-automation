from __future__ import annotations

import json
import threading
from collections import OrderedDict
from html import escape
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .capacity_matching import CAPACITY_PROTOCOL_CLUSTER_IDS, CAPACITY_PROTOCOL_LABELS, CAPACITY_PROTOCOL_ORDER, build_capacity_match_report
from .conditions import CAPACITY_COMPARISON_FIELDS, clean, read_conditions
from .eis_matching import EIS_SUFFIXES, build_eis_match_report, compact_date
from .file_io import parse_file
from .metrics import to_float
from .plots import eis_fit_svg
from . import config
from . import perf
from . import render_cache
from . import ui as streamlit_ui
from wonatech_parsers.wrd import build_capacity_summary, parse_wrd_file


CAPACITY_SUMMARY_SUFFIXES = {".csv", ".xlsx", ".xls"}
CAPACITY_LIVE_SUFFIXES = {".wrd", ".csv", ".xlsx", ".xls"}
_STREAMLIT_UI_LOCK = threading.RLock()

# In-process memo for the match report. Rebuilding it (build_*_match_report) is the
# dominant per-request cost on Render (~2.2s CPU); it ran on every cluster click in
# front of the render cache, defeating it. The report is a pure function of
# (source files, condition workbook, overrides), so memoizing it by that identity
# turns repeated clicks on unchanged data into a dict lookup. Keyed by
# render_cache.match_report_key, so it self-invalidates exactly like the disk cache.
_MATCH_REPORT_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_MATCH_REPORT_CACHE_LOCK = threading.Lock()
_MATCH_REPORT_CACHE_MAX = 8


def clear_match_report_cache() -> None:
    with _MATCH_REPORT_CACHE_LOCK:
        _MATCH_REPORT_CACHE.clear()


def _memoized_match_report(cache_key: str, builder: Any) -> tuple[Any, bool]:
    if render_cache._disabled():
        return builder(), False
    with _MATCH_REPORT_CACHE_LOCK:
        if cache_key in _MATCH_REPORT_CACHE:
            _MATCH_REPORT_CACHE.move_to_end(cache_key)
            return _MATCH_REPORT_CACHE[cache_key], True
    report = builder()  # built outside the lock; cold build is already serialized by _STREAMLIT_UI_LOCK
    with _MATCH_REPORT_CACHE_LOCK:
        _MATCH_REPORT_CACHE[cache_key] = report
        _MATCH_REPORT_CACHE.move_to_end(cache_key)
        while len(_MATCH_REPORT_CACHE) > _MATCH_REPORT_CACHE_MAX:
            _MATCH_REPORT_CACHE.popitem(last=False)
    return report, False


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
        time_series_groups = sorted(
            [group for group in report.time_series_groups if group.file_count >= 2],
            key=lambda group: date_sort_key(group.folder_date),
        )
        comparison_clusters = sorted(
            list(report.comparison_clusters),
            key=lambda cluster: date_sort_key(eis_cluster_date(cluster.source_paths)),
        )
        time_series_options = [
            {
                "value": group.cluster_id,
                "label": eis_time_series_option_label(group, conditions),
                "file_count": group.file_count,
                "source_paths": group.member_paths,
            }
            for group in time_series_groups
        ]
        comparison_options = [
            {
                "value": cluster.cluster_id,
                "label": eis_comparison_option_label(cluster, conditions),
                "file_count": cluster.file_count,
                "source_paths": cluster.source_paths,
            }
            for cluster in comparison_clusters
        ]
        # Matched files that join no multi-file cluster still deserve their own
        # 1-file cluster so they are viewable/grouped rather than lost.
        clustered_members: set[str] = set()
        for cluster in comparison_clusters:
            clustered_members.update(p for p in cluster.source_paths.split(";") if p)
        for group in time_series_groups:  # only groups actually shown (file_count >= 2)
            clustered_members.update(p for p in group.member_paths.split(";") if p)
        comparison_options.extend(
            eis_independent_cluster_options(report, conditions, clustered_members)
        )
        if source_options:
            comparison_options.append(
                {
                    "value": "C999",
                    "label": f"{'all dates':<9} · {'all EIS data':<14} · {'all':<3} · {'all':<9} · {'all':<5} · {len(source_options):>3} files",
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
            "time_series_rows": eis_time_series_rows(time_series_groups, conditions),
            "comparison_rows": eis_comparison_rows(comparison_clusters, conditions, len(source_options)),
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
    marks: dict[str, Any] = {}
    total = perf.now()
    with streamlit_roots(eis_root, capacity_root):
        t = perf.now()
        source_paths, conditions, report = build_eis_viewer_report(eis_root, condition_workbook, override_path, timings=marks)
        marks["report_ms"] = perf.ms(t)
        all_rel_paths = [str(path.relative_to(eis_root)) for path in source_paths]
        title = "EIS Nyquist"
        color_mode = "comparison"
        performance_mode = False

        if mode == "source":
            rel_paths = [key] if key else all_rel_paths[:1]
            title = f"{Path(rel_paths[0]).stem} Nyquist" if rel_paths else "EIS Nyquist"
        elif mode == "time_series":
            groups = sorted(
                [group for group in report.time_series_groups if group.file_count >= 2],
                key=lambda group: date_sort_key(group.folder_date),
            )
            group = next((item for item in groups if item.cluster_id == key), groups[0] if groups else None)
            rel_paths = [item for item in group.member_paths.split(";") if item] if group else []
            title = eis_time_series_title(group, conditions) if group else "EIS time-series Nyquist"
            color_mode = "time_series"
        else:
            clusters = sorted(
                list(report.comparison_clusters),
                key=lambda cluster: date_sort_key(eis_cluster_date(cluster.source_paths)),
            )
            if key == "C999" or (not key and all_rel_paths):
                rel_paths = all_rel_paths
                title = "all dates · all EIS data · Nyquist"
                performance_mode = True
            elif key.startswith("IND::"):
                # Single-file "independent" cluster for a matched file that joins
                # no multi-file comparison cluster (see eis_independent_cluster_options).
                rel = key[len("IND::"):]
                rel_paths = [rel] if rel in all_rel_paths else []
                title = f"{Path(rel).stem} · 단일 EIS"
            else:
                cluster = next((item for item in clusters if item.cluster_id == key), clusters[0] if clusters else None)
                rel_paths = [item for item in cluster.source_paths.split(";") if item] if cluster else all_rel_paths
                title = eis_comparison_title(cluster, conditions) if cluster else "EIS comparison Nyquist"

        if not rel_paths:
            return {"available": False, "html": "", "errors": ["표시할 EIS source가 없습니다."], "title": title}

        member_paths = [eis_root / rel for rel in rel_paths]
        flags = {"show_fit": bool(show_fit), "label_layout": "dated_standard_v2"}
        t = perf.now()
        ctx = render_cache.context_hash(condition_workbook, override_path)
        msig = render_cache.membersig(member_paths, eis_root)
        marks["membersig_ms"] = perf.ms(t)
        cache_id = key or f"{mode}:all"
        t = perf.now()
        cached = render_cache.cluster_cache_get("eis", mode, cache_id, msig, ctx, flags)
        marks["cache_get_ms"] = perf.ms(t)
        if cached is not None:
            marks["cache_hit"] = True
            perf.emit_overlay("eis", mode, key, marks, total, (eis_root, capacity_root, config.BATTERY_OUTPUT_ROOT))
            return cached

        t = perf.now()
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
        marks["render_ms"] = perf.ms(t)
        payload = {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
        render_cache.cluster_cache_put("eis", mode, cache_id, msig, ctx, flags, payload)
        marks["cache_hit"] = False
        perf.emit_overlay("eis", mode, key, marks, total, (eis_root, capacity_root, config.BATTERY_OUTPUT_ROOT))
        return payload


def capacity_viewer_options(capacity_root: Path, eis_root: Path, condition_workbook: Path, override_path: Path) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        source_paths, summary_paths, conditions, report = build_capacity_viewer_report(capacity_root, condition_workbook, override_path)
        source_options = path_options(source_paths, capacity_root)
        summary_options = path_options(summary_paths, capacity_root)
        rel_paths = [option["value"] for option in summary_options]
        groups = capacity_comparison_path_groups(rel_paths, report, conditions)
        cluster_options = [
            {
                "value": group["value"],
                "label": group["label"],
                "file_count": len(group["paths"]),
                "protocol_type": group["protocol_type"],
            }
            for group in groups
        ]
        for protocol_type in CAPACITY_PROTOCOL_ORDER:
            protocol_paths = [path for group in groups if group["protocol_type"] == protocol_type for path in group["paths"]]
            if protocol_paths:
                protocol_label = capacity_protocol_label(protocol_type)
                cluster_options.append(
                    {
                        "value": f"{protocol_type}|P999",
                        "label": f"{'all dates':<9} · {protocol_label:<14} · {'all':<3} · {'all':<9} · {'all':<5} · {len(protocol_paths):>3} files",
                        "file_count": len(protocol_paths),
                        "protocol_type": protocol_type,
                    },
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
            "cluster_rows": capacity_comparison_cluster_rows(groups, len(rel_paths)),
        }


def capacity_source_payload(capacity_root: Path, eis_root: Path, rel_path: str) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        try:
            path = safe_child(capacity_root, rel_path)
            title = path.name
            flags: dict = {}
            ctx = render_cache.context_hash(capacity_root / "__none__", capacity_root / "__none__")
            msig = render_cache.membersig([path], capacity_root)
            cached = render_cache.cluster_cache_get("capacity", "source", rel_path, msig, ctx, flags)
            if cached is not None:
                return cached

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
                payload = {
                    "available": bool(records),
                    "html": html_doc,
                    "errors": [] if records else ["WRD record를 찾지 못했습니다."],
                    "title": title,
                    "row_count": len(summary),
                }
                render_cache.cluster_cache_put("capacity", "source", rel_path, msig, ctx, flags, payload)
                return payload

            dataset = parse_file(path)
            graph = capacity_dataset_svg(title, dataset)
            table = rows_table_html(dataset.rows[:300])
            meta = f"{dataset.meta.analysis_type} · rows {len(dataset.rows)}"
            html_doc = source_preview_html(title, meta, graph, table)
            payload = {
                "available": bool(dataset.rows or graph),
                "html": html_doc,
                "errors": [],
                "title": title,
                "row_count": len(dataset.rows),
            }
            render_cache.cluster_cache_put("capacity", "source", rel_path, msig, ctx, flags, payload)
            return payload
        except Exception as exc:
            return {"available": False, "html": "", "errors": [str(exc)], "title": rel_path or "Capacity source", "row_count": 0}


# Data types surfaced in the journal row-number tooltip / detail popup. EIS files
# whose name carries the ``_hr`` time-series marker are split out from plain EIS
# comparison files; capacity files inherit their cluster's protocol_type when known.
def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def journal_row_data_index(
    eis_root: Path,
    capacity_root: Path,
    condition_workbook: Path,
    eis_override_path: Path,
    capacity_override_path: Path,
) -> dict[int, list[dict[str, Any]]]:
    """Map journal row number -> list of registered data files and their type.

    Pure read: reuses the (memoized) match reports, so it is cheap on warm cache
    and self-invalidating when sources/overrides change. Failures degrade to an
    empty/partial map rather than raising, since this only feeds tooltips/preview.
    """
    from .matching_service import build_match_payload

    index: dict[int, list[dict[str, Any]]] = {}

    def add(row_no: int, entry: dict[str, Any]) -> None:
        bucket = index.setdefault(row_no, [])
        if not any(e["rel_path"] == entry["rel_path"] and e["type"] == entry["type"] for e in bucket):
            bucket.append(entry)

    # EIS files -> rows
    try:
        payload = build_match_payload("eis", eis_root, condition_workbook, eis_override_path)
        for row in payload.get("final_rows", []):
            if row.get("override_action") == "delete_file":
                continue
            row_no = _positive_int(row.get("journal_row"))
            rel = str(row.get("relative_path") or "")
            if not row_no or not rel:
                continue
            type_ = "eis_time_series" if "_hr" in Path(rel).name.lower() else "eis_comparison"
            add(row_no, {"rel_path": rel, "kind": "eis", "type": type_})
    except Exception:
        pass

    # Capacity protocol classification (rel_path -> protocol_type) for the type label.
    protocol_by_path: dict[str, str] = {}
    try:
        with streamlit_roots(eis_root, capacity_root):
            _, summary_paths, conditions, report = build_capacity_viewer_report(
                capacity_root, condition_workbook, capacity_override_path
            )
            rel_paths = [str(path.relative_to(capacity_root)) for path in summary_paths]
            for group in capacity_comparison_path_groups(rel_paths, report, conditions):
                for rel in group["paths"]:
                    protocol_by_path[rel] = group["protocol_type"]
    except Exception:
        protocol_by_path = {}

    # Capacity files -> rows
    try:
        payload = build_match_payload("capacity", capacity_root, condition_workbook, capacity_override_path)
        for row in payload.get("final_rows", []):
            if row.get("override_action") == "delete_file":
                continue
            row_no = _positive_int(row.get("journal_row"))
            rel = str(row.get("relative_path") or "")
            if not row_no or not rel:
                continue
            protocol = protocol_by_path.get(rel)
            type_ = protocol if protocol in CAPACITY_PROTOCOL_ORDER else "capacity"
            add(row_no, {"rel_path": rel, "kind": "capacity", "type": type_})
    except Exception:
        pass

    return index


def journal_row_types_payload(
    eis_root: Path,
    capacity_root: Path,
    condition_workbook: Path,
    eis_override_path: Path,
    capacity_override_path: Path,
) -> dict[str, Any]:
    index = journal_row_data_index(
        eis_root, capacity_root, condition_workbook, eis_override_path, capacity_override_path
    )
    row_types: dict[str, list[str]] = {}
    for row_no, entries in index.items():
        seen: list[str] = []
        for entry in entries:
            if entry["type"] not in seen:
                seen.append(entry["type"])
        row_types[str(row_no)] = seen

    # Orphan rows = real experiment rows in the journal that have no linked data
    # file ("데이터 파일 없음"). Used to shade their row-number cell darker.
    orphan_set: set[int] = set()
    try:
        conditions = render_cache.cached_read_conditions(condition_workbook)
        for condition in conditions.values():
            row_no = _positive_int(condition.get("_source_row_number"))
            if row_no and row_no not in index:
                orphan_set.add(row_no)
    except Exception:
        orphan_set = set()

    return {"available": True, "row_types": row_types, "orphan_rows": sorted(orphan_set)}


def journal_orphan_files_payload(
    eis_root: Path,
    capacity_root: Path,
    condition_workbook: Path,
    eis_override_path: Path,
    capacity_override_path: Path,
) -> dict[str, Any]:
    """Data files that are not matched to any journal row ("고아 파일").

    Lists name, relative path and file creation time so they can be reviewed in a
    table under the journal. Best-effort per kind; a missing root is skipped.
    """
    from .matching_service import build_match_payload
    import datetime as _dt

    files: list[dict[str, Any]] = []
    for kind, root, override_path in (
        ("eis", eis_root, eis_override_path),
        ("capacity", capacity_root, capacity_override_path),
    ):
        try:
            payload = build_match_payload(kind, root, condition_workbook, override_path)
        except Exception:
            continue
        for row in payload.get("final_rows", []):
            if str(row.get("journal_row") or "").strip():
                continue  # has a journal row -> not orphan
            rel = str(row.get("relative_path") or "")
            if not rel:
                continue
            abs_path = root / rel
            created = ""
            try:
                stat = abs_path.stat()
                ts = getattr(stat, "st_birthtime", None) or stat.st_mtime
                created = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except OSError:
                created = ""
            files.append(
                {
                    "name": Path(rel).name,
                    "rel_path": rel,
                    "kind": kind,
                    "status": row.get("status") or "",
                    "created": created,
                }
            )
    files.sort(key=lambda item: (item["kind"], item["rel_path"]))
    return {
        "available": True,
        "count": len(files),
        "eis_count": sum(1 for f in files if f["kind"] == "eis"),
        "capacity_count": sum(1 for f in files if f["kind"] == "capacity"),
        "files": files,
    }


def journal_row_detail_payload(
    eis_root: Path,
    capacity_root: Path,
    condition_workbook: Path,
    eis_override_path: Path,
    capacity_override_path: Path,
    *,
    row: int,
    max_previews: int = 8,
) -> dict[str, Any]:
    index = journal_row_data_index(
        eis_root, capacity_root, condition_workbook, eis_override_path, capacity_override_path
    )
    entries = index.get(row, [])
    types: list[str] = []
    for entry in entries:
        if entry["type"] not in types:
            types.append(entry["type"])

    previews: list[dict[str, Any]] = []
    for entry in entries[:max_previews]:
        rel = entry["rel_path"]
        try:
            if entry["kind"] == "eis":
                payload = eis_source_payload(eis_root, capacity_root, rel)
            else:
                payload = capacity_source_payload(capacity_root, eis_root, rel)
        except Exception as exc:  # pragma: no cover - preview is best-effort
            payload = {"available": False, "html": "", "errors": [str(exc)], "title": Path(rel).name}
        previews.append(
            {
                "file": rel,
                "title": payload.get("title") or Path(rel).name,
                "type": entry["type"],
                "kind": entry["kind"],
                "available": payload.get("available", False),
                "html": payload.get("html", ""),
                "errors": payload.get("errors", []),
            }
        )

    return {"row": row, "types": types, "previews": previews}


def capacity_overlay_payload(
    capacity_root: Path,
    eis_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    mode: str,
    key: str,
) -> dict[str, Any]:
    marks: dict[str, Any] = {}
    total = perf.now()
    with streamlit_roots(eis_root, capacity_root):
        t = perf.now()
        _, summary_paths, conditions, report = build_capacity_viewer_report(capacity_root, condition_workbook, override_path, timings=marks)
        marks["report_ms"] = perf.ms(t)
        rel_paths = [str(path.relative_to(capacity_root)) for path in summary_paths]
        title = "Capacity datasets"
        performance_mode = False
        if mode == "source":
            selected_paths = [key] if key else rel_paths[:1]
            title = f"{Path(selected_paths[0]).stem} Capacity" if selected_paths else title
        else:
            groups = capacity_comparison_path_groups(rel_paths, report, conditions)
            if mode in CAPACITY_PROTOCOL_ORDER:
                groups = [group for group in groups if group["protocol_type"] == mode]
            protocol_label = capacity_protocol_label(mode) if mode in CAPACITY_PROTOCOL_ORDER else "all protocols"
            if key == "P999" or key == f"{mode}|P999" or (not key and groups):
                selected_paths = [path for group in groups for path in group["paths"]]
                title = f"all dates · {protocol_label} · Capacity datasets"
                performance_mode = True
            else:
                selected_group = next((group for group in groups if group["value"] == key), None) or (groups[0] if groups else None)
                selected_paths = selected_group["paths"] if selected_group else []
                title = selected_group["title"] if selected_group else title

        if not selected_paths:
            return {"available": False, "html": "", "errors": ["표시할 Capacity summary source가 없습니다."], "title": title}

        member_paths = [capacity_root / rel for rel in selected_paths]
        flags: dict = {"table_layout": "protocol_kpi_columns_v5", "cluster_layout": "journal_date_bucket_v4", "label_layout": "charge_mean_center_y_v5"}
        t = perf.now()
        ctx = render_cache.context_hash(condition_workbook, override_path)
        msig = render_cache.membersig(member_paths, capacity_root)
        marks["membersig_ms"] = perf.ms(t)
        cache_id = key or f"{mode}:all"
        t = perf.now()
        cached = render_cache.cluster_cache_get("capacity", mode, cache_id, msig, ctx, flags)
        marks["cache_get_ms"] = perf.ms(t)
        if cached is not None:
            marks["cache_hit"] = True
            perf.emit_overlay("capacity", mode, key, marks, total, (eis_root, capacity_root, config.BATTERY_OUTPUT_ROOT))
            return cached

        t = perf.now()
        series, errors = streamlit_ui.load_capacity_overlay_series(selected_paths, report, conditions, performance_mode=performance_mode)
        html_doc = streamlit_ui.capacity_overlay_html(title, series, width=1180, height=590, performance_mode=performance_mode)
        marks["render_ms"] = perf.ms(t)
        payload = {"available": bool(html_doc), "html": html_doc, "errors": errors, "title": title, "series_count": len(series)}
        render_cache.cluster_cache_put("capacity", mode, cache_id, msig, ctx, flags, payload)
        marks["cache_hit"] = False
        perf.emit_overlay("capacity", mode, key, marks, total, (eis_root, capacity_root, config.BATTERY_OUTPUT_ROOT))
        return payload


def draft_overlay_payload(
    draft_root: Path,
    rel_paths: list[str],
    *,
    kind: str,
    color_mode: str = "comparison",
    title: str = "",
) -> dict[str, Any]:
    """Render uncommitted draft files through the live-viewer overlay pipeline.

    Uses the same ``load_*_overlay_series`` + ``*_overlay_html`` functions the EIS/Capacity
    live viewers use, so the wizard preview matches the live viewer (scaling, fit circle,
    KPI table) instead of the legacy dashboard plot. Paths resolve against ``draft_root``
    (both EIS_ROOT and CAPACITY_ROOT are pointed there), and an empty match report is used
    since draft files are not yet matched to a journal condition.
    """
    if not rel_paths:
        return {"available": False, "html": "", "errors": ["선택된 파일이 없습니다."], "title": title, "series_count": 0}
    report = SimpleNamespace(matches=[])
    with streamlit_roots(draft_root, draft_root):
        if kind == "capacity":
            series, errors = streamlit_ui.load_capacity_overlay_series(rel_paths, report, {})
            html_doc = streamlit_ui.capacity_overlay_html(title or "Capacity preview", series)
        else:
            show_fit = color_mode != "time_series"
            series, errors = streamlit_ui.load_eis_overlay_series(rel_paths, report, {}, color_mode=color_mode)
            if show_fit:
                for item in series:
                    item["label"] = streamlit_ui.overlay_fit_label(item)
            html_doc = streamlit_ui.eis_overlay_html(title or "EIS preview", series, color_mode=color_mode, show_fit=show_fit)
    return {
        "available": bool(html_doc and series),
        "html": html_doc,
        "errors": errors,
        "title": title,
        "series_count": len(series),
    }


def capacity_comparison_path_groups(
    rel_paths: list[str],
    report: Any,
    conditions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    protocol_groups = streamlit_ui.capacity_protocol_path_groups(rel_paths)
    matches = {match.relative_path: match for match in getattr(report, "matches", [])}
    conditions_by_row = streamlit_ui.conditions_by_source_row(conditions)
    groups: list[dict[str, Any]] = []
    for protocol_type in CAPACITY_PROTOCOL_ORDER:
        buckets: dict[tuple[str, ...], list[str]] = {}
        for rel_path in protocol_groups.get(protocol_type, []):
            match = matches.get(rel_path)
            condition = conditions.get(match.condition_key, {}) if match and match.condition_key else {}
            key = capacity_comparison_key(condition)
            buckets.setdefault(key, []).append(rel_path)
        for idx, (condition_key, paths) in enumerate(
            sorted(buckets.items(), key=lambda item: (capacity_date_range_sort_key(capacity_paths_journal_date_label(item[1], matches, conditions_by_row)), item[0])),
            start=1,
        ):
            date = capacity_paths_journal_date_label(paths, matches, conditions_by_row)
            values = capacity_comparison_values(condition_key)
            protocol_label = capacity_protocol_label(protocol_type)
            details = capacity_comparison_details(values)
            title = capacity_cluster_title(date, protocol_label, values, len(paths))
            groups.append(
                {
                    "value": f"{protocol_type}|{date}|{idx:02d}",
                    "date": date,
                    "protocol_type": protocol_type,
                    "protocol_label": protocol_label,
                    "condition_key": condition_key,
                    "condition_label": details,
                    "cell_type": values.get("cell_type", "unknown"),
                    "voltage_range": values.get("voltage_range", "unknown"),
                    "ratio": values.get("ratio", "unknown"),
                    "title": title,
                    "label": capacity_cluster_option_label(date, protocol_label, values, len(paths)),
                    "paths": paths,
                }
            )
    protocol_order = {protocol_type: idx for idx, protocol_type in enumerate(CAPACITY_PROTOCOL_ORDER)}
    return sorted(
        groups,
        key=lambda group: (
            capacity_date_range_sort_key(group["date"]),
            protocol_order.get(group["protocol_type"], 999),
            group["cell_type"],
            group["voltage_range"],
            group["ratio"],
        ),
    )


def capacity_cluster_date(rel_path: str) -> str:
    for part in Path(rel_path).parts:
        if len(part) == 6 and part.isdigit():
            return part
    return "unknown"


def capacity_paths_date_label(paths: list[str]) -> str:
    dates = sorted({capacity_cluster_date(path) for path in paths if capacity_cluster_date(path) != "unknown"})
    if not dates:
        return "unknown"
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]}-{dates[-1]}"


def capacity_paths_journal_date_label(
    paths: list[str],
    matches: dict[str, Any],
    conditions_by_row: dict[int, dict[str, Any]],
) -> str:
    """클러스터 날짜 라벨을 실험일지 기준(파일명 행번호 → 원본 조건표 행 date)으로 만든다.

    조건표 date가 6자리(YYMMDD)로 정규화되면 그 값을, 정규화 불가/누락이면
    파일 경로의 폴더 날짜로 안전하게 fallback 한다(정렬/범위 표기 호환 유지).
    """
    tokens: set[str] = set()
    for path in paths:
        match = matches.get(path)
        row_prefix = getattr(match, "row_prefix", None) if match else None
        raw = streamlit_ui.journal_date_by_row(conditions_by_row, row_prefix, fallback="")
        token = compact_date(raw) or capacity_cluster_date(path)
        if token and token != "unknown":
            tokens.add(token)
    if not tokens:
        return "unknown"
    ordered = sorted(tokens)
    return ordered[0] if len(ordered) == 1 else f"{ordered[0]}-{ordered[-1]}"


def capacity_date_range_sort_key(date_label: str) -> tuple[int, int]:
    dates = [part for part in str(date_label or "").split("-") if len(part) == 6 and part.isdigit()]
    if dates:
        return (0, -int(dates[-1]))
    return (1, 0)


def capacity_comparison_key(condition: dict[str, Any]) -> tuple[str, ...]:
    values = []
    for field in CAPACITY_COMPARISON_FIELDS:
        value = clean(condition.get(field)) or "unknown"
        if field == "ratio":
            value = capacity_ratio_bucket(value)
        values.append(value)
    return tuple(values)


def capacity_ratio_bucket(value: str) -> str:
    number = to_float(value)
    if number is None:
        return value
    bucket = round(number / 0.05) * 0.05
    text = f"{bucket:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def capacity_comparison_values(key: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(CAPACITY_COMPARISON_FIELDS, key))


def capacity_protocol_label(protocol_type: str) -> str:
    label = CAPACITY_PROTOCOL_LABELS.get(protocol_type, protocol_type)
    return label.split("·", 1)[-1].strip()


def capacity_comparison_details(values: dict[str, str]) -> str:
    return " · ".join(
        [
            f"type {values.get('cell_type', 'unknown')}",
            f"electrolyte {values.get('electrolyte', 'unknown')}",
            f"binder {values.get('binder', 'unknown')}",
            f"voltage {values.get('voltage_range', 'unknown')}",
            f"ratio {values.get('ratio', 'unknown')}",
        ]
    )


def capacity_cluster_title(date: str, protocol_label: str, values: dict[str, str], file_count: int) -> str:
    return " · ".join(
        [
            date,
            protocol_label,
            values.get("cell_type", "unknown"),
            values.get("binder", "unknown"),
            values.get("voltage_range", "unknown"),
            f"r{values.get('ratio', 'unknown')}",
            f"{file_count} files",
        ]
    )


def capacity_cluster_option_label(date: str, protocol_label: str, values: dict[str, str], file_count: int) -> str:
    return (
        f"{date:<9} · "
        f"{protocol_label:<14} · "
        f"{values.get('cell_type', 'unknown'):<3} · "
        f"{short_condition_text(values.get('binder', 'unknown'), 16):<16} · "
        f"{values.get('voltage_range', 'unknown'):<9} · "
        f"r{values.get('ratio', 'unknown'):<5} · "
        f"{file_count:>3} files"
    )


def short_condition_text(value: str, max_len: int) -> str:
    text = str(value or "unknown")
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def capacity_comparison_cluster_rows(groups: list[dict[str, Any]], total_count: int) -> list[dict[str, Any]]:
    rows = [
        {
            "Date": group["date"],
            "Protocol": group["protocol_label"],
            "protocol_type": group["protocol_type"],
            "Cell type": group["cell_type"],
            "Voltage": group["voltage_range"],
            "Ratio": group["ratio"],
            "Files": len(group["paths"]),
            "Details": group["condition_label"],
        }
        for group in groups
    ]
    protocol_types = [protocol_type for protocol_type in CAPACITY_PROTOCOL_ORDER if any(group["protocol_type"] == protocol_type for group in groups)]
    if not protocol_types and total_count:
        protocol_types = [CAPACITY_PROTOCOL_ORDER[0]]
    for protocol_type in protocol_types:
        protocol_count = sum(len(group["paths"]) for group in groups if group["protocol_type"] == protocol_type)
        rows.append(
            {
                "Date": "all",
                "Protocol": capacity_protocol_label(protocol_type),
                "protocol_type": protocol_type,
                "Cell type": "all",
                "Voltage": "all",
                "Ratio": "all",
                "Files": protocol_count or total_count,
                "Details": "전체 overlay",
            }
        )
    return rows


def date_sort_key(date: Any) -> tuple[int, int]:
    text = str(date or "")
    compact = text[:6]
    if len(compact) == 6 and compact.isdigit():
        return (0, -int(compact))
    return (1, 0)


def eis_cluster_date(source_paths: str) -> str:
    dates = sorted(
        {
            capacity_cluster_date(path)
            for path in str(source_paths or "").split(";")
            if path
        }
    )
    dates = [date for date in dates if date != "unknown"]
    if not dates:
        return "unknown"
    latest = dates[-1]
    return f"{latest}+" if len(dates) > 1 else latest


def eis_condition_values(condition: dict[str, Any], *, voltage: str = "", ratio: str = "", electrolyte: str = "", binder: str = "") -> dict[str, str]:
    return {
        "cell_type": clean(condition.get("cell_type")) or "unknown",
        "electrolyte": clean(condition.get("electrolyte")) or clean(electrolyte) or "unknown",
        "binder": clean(condition.get("binder")) or clean(binder) or "unknown",
        "voltage_range": clean(condition.get("voltage_range")) or clean(voltage) or "unknown",
        "ratio": clean(condition.get("ratio")) or clean(ratio) or "unknown",
    }


def first_condition_from_keys(condition_keys: str, conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in str(condition_keys or "").split(";"):
        condition = conditions.get(key)
        if condition:
            return condition
    return {}


def eis_comparison_values(cluster: Any, conditions: dict[str, dict[str, Any]]) -> dict[str, str]:
    condition = first_condition_from_keys(getattr(cluster, "condition_keys", ""), conditions)
    return eis_condition_values(
        condition,
        voltage=getattr(cluster, "voltage_range", ""),
        ratio=getattr(cluster, "ratio", ""),
        electrolyte=getattr(cluster, "electrolyte", ""),
        binder=getattr(cluster, "binder", ""),
    )


def eis_time_series_values(group: Any, conditions: dict[str, dict[str, Any]]) -> dict[str, str]:
    condition = conditions.get(getattr(group, "condition_key", ""), {})
    return eis_condition_values(condition)


def standard_option_label(date: str, mode_label: str, values: dict[str, str], file_count: int) -> str:
    return (
        f"{date:<9} · "
        f"{mode_label:<14} · "
        f"{values.get('cell_type', 'unknown'):<3} · "
        f"{values.get('voltage_range', 'unknown'):<9} · "
        f"r{values.get('ratio', 'unknown'):<5} · "
        f"{file_count:>3} files"
    )


def eis_comparison_option_label(cluster: Any, conditions: dict[str, dict[str, Any]]) -> str:
    mode = "EIS independent" if getattr(cluster, "cluster_role", "") == "independent" else "EIS compare"
    return standard_option_label(eis_cluster_date(cluster.source_paths), mode, eis_comparison_values(cluster, conditions), cluster.file_count)


def eis_time_series_option_label(group: Any, conditions: dict[str, dict[str, Any]]) -> str:
    return standard_option_label(group.folder_date or "unknown", "EIS time", eis_time_series_values(group, conditions), group.file_count)


def eis_comparison_title(cluster: Any, conditions: dict[str, dict[str, Any]]) -> str:
    values = eis_comparison_values(cluster, conditions)
    mode = "EIS independent" if getattr(cluster, "cluster_role", "") == "independent" else "EIS compare"
    return f"{eis_cluster_date(cluster.source_paths)} · {mode} · {values['cell_type']} · {values['voltage_range']} · r{values['ratio']} · Nyquist"


def eis_time_series_title(group: Any, conditions: dict[str, dict[str, Any]]) -> str:
    values = eis_time_series_values(group, conditions)
    return f"{group.folder_date or 'unknown'} · EIS time · {values['cell_type']} · {values['voltage_range']} · r{values['ratio']} · Nyquist"


def eis_details(values: dict[str, str], extra: str = "") -> str:
    parts = [
        f"electrolyte {values.get('electrolyte', 'unknown')}",
        f"binder {values.get('binder', 'unknown')}",
    ]
    if extra:
        parts.append(extra)
    return " · ".join(parts)


def eis_comparison_rows(clusters: list[Any], conditions: dict[str, dict[str, Any]], total_count: int) -> list[dict[str, Any]]:
    rows = []
    for cluster in clusters:
        values = eis_comparison_values(cluster, conditions)
        mode = "EIS independent" if getattr(cluster, "cluster_role", "") == "independent" else "EIS compare"
        rows.append(
            {
                "Date": eis_cluster_date(cluster.source_paths),
                "Mode": mode,
                "Cell type": values["cell_type"],
                "Voltage": values["voltage_range"],
                "Ratio": values["ratio"],
                "Files": cluster.file_count,
                "Details": eis_details(values, f"loading {format_optional(cluster.loading_min)}-{format_optional(cluster.loading_max)}"),
            }
        )
    rows.append(
        {
            "Date": "all",
            "Mode": "all EIS data",
            "Cell type": "all",
            "Voltage": "all",
            "Ratio": "all",
            "Files": total_count,
            "Details": "전체 overlay",
        }
    )
    return rows


def eis_time_series_rows(groups: list[Any], conditions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for group in groups:
        values = eis_time_series_values(group, conditions)
        sample = group.condition_sample or group.cluster_signature
        rows.append(
            {
                "Date": group.folder_date or "unknown",
                "Mode": "EIS time",
                "Cell type": values["cell_type"],
                "Voltage": values["voltage_range"],
                "Ratio": values["ratio"],
                "Files": group.file_count,
                "Details": eis_details(values, f"{sample} · {group.time_points}"),
            }
        )
    return rows


def eis_source_payload(eis_root: Path, capacity_root: Path, rel_path: str, *, show_fit: bool = False) -> dict[str, Any]:
    with streamlit_roots(eis_root, capacity_root):
        path = safe_child(eis_root, rel_path)
        flags = {"show_fit": bool(show_fit)}
        ctx = render_cache.context_hash(eis_root / "__none__", eis_root / "__none__")
        msig = render_cache.membersig([path], eis_root)
        cached = render_cache.cluster_cache_get("eis", "source", rel_path, msig, ctx, flags)
        if cached is not None:
            return cached

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
        payload = {"available": bool(html_doc), "html": html_doc, "errors": [], "title": path.name, "point_count": len(points)}
        render_cache.cluster_cache_put("eis", "source", rel_path, msig, ctx, flags, payload)
        return payload


def build_eis_viewer_report(eis_root: Path, condition_workbook: Path, override_path: Path, timings: dict[str, Any] | None = None) -> tuple[list[Path], dict[str, dict[str, Any]], Any]:
    t = perf.now()
    source_paths = streamlit_ui.collect_source_files(eis_root, EIS_SUFFIXES)
    if timings is not None:
        timings["walk_ms"] = perf.ms(t)
        timings["n_files"] = len(source_paths)
    t = perf.now()
    conditions = render_cache.cached_read_conditions(condition_workbook)
    if timings is not None:
        timings["conditions_ms"] = perf.ms(t)
    t = perf.now()
    cache_key = render_cache.match_report_key("eis", source_paths, eis_root, condition_workbook, override_path)

    def _build():
        overrides = load_overrides(override_path)
        return build_eis_match_report(source_paths, conditions, eis_root, overrides)

    report, hit = _memoized_match_report(cache_key, _build)
    if timings is not None:
        timings["match_ms"] = perf.ms(t)
        timings["match_hit"] = hit
    return source_paths, conditions, report


def build_capacity_viewer_report(
    capacity_root: Path,
    condition_workbook: Path,
    override_path: Path,
    timings: dict[str, Any] | None = None,
) -> tuple[list[Path], list[Path], dict[str, dict[str, Any]], Any]:
    t = perf.now()
    source_paths = streamlit_ui.collect_source_files(capacity_root, CAPACITY_LIVE_SUFFIXES)
    summary_paths = [path for path in streamlit_ui.collect_source_files(capacity_root, CAPACITY_SUMMARY_SUFFIXES) if streamlit_ui.is_capacity_summary_source(path)]
    if timings is not None:
        timings["walk_ms"] = perf.ms(t)
        timings["n_files"] = len(source_paths) + len(summary_paths)
    t = perf.now()
    conditions = render_cache.cached_read_conditions(condition_workbook)
    if timings is not None:
        timings["conditions_ms"] = perf.ms(t)
    t = perf.now()
    cache_key = render_cache.match_report_key("capacity", summary_paths, capacity_root, condition_workbook, override_path)

    def _build():
        overrides = load_overrides(override_path)
        return build_capacity_match_report(summary_paths, conditions, capacity_root, overrides)

    report, hit = _memoized_match_report(cache_key, _build)
    if timings is not None:
        timings["match_ms"] = perf.ms(t)
        timings["match_hit"] = hit
    return source_paths, summary_paths, conditions, report


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def eis_independent_cluster_options(
    report: Any,
    conditions: dict[str, dict[str, Any]],
    clustered_members: set[str],
) -> list[dict[str, Any]]:
    """1-file comparison options for confirmed-matched EIS files in no cluster.

    Such files (matched to a journal row but sharing no comparison partner) would
    otherwise only show in the individual 'source' mode. Emitting them as their own
    ``IND::<rel>`` cluster keeps them visible in the comparison dropdown. The
    overlay resolves the ``IND::`` prefix to a single file.
    """
    options: list[dict[str, Any]] = []
    for match in getattr(report, "matches", []):
        rel = getattr(match, "relative_path", "")
        if not rel or rel in clustered_members:
            continue
        if getattr(match, "status", "") not in {"verified", "manual"}:
            continue
        clustered_members.add(rel)
        condition = conditions.get(getattr(match, "condition_key", ""), {})
        date = eis_cluster_date(rel)
        cell = str(clean(condition.get("cell_type")) or "?")
        voltage = str(clean(condition.get("voltage_range")) or "?")
        ratio = str(clean(condition.get("ratio")) or "?")
        label = f"{date:<9} · EIS 단일       · {cell:<3} · {voltage:<9} · r{ratio:<5} ·   1 file · {Path(rel).name}"
        options.append({"value": f"IND::{rel}", "label": label, "file_count": 1, "source_paths": rel})
    options.sort(key=lambda option: option["label"])
    return options


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
    .graph {{ overflow: hidden; border: 1px solid #d8dee8; border-radius: 8px; min-height: 120px; display: grid; place-items: center; padding: 6px; }}
    .graph svg {{ width: 100%; height: auto; max-height: 100%; display: block; }}
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
