from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import os
import shutil
import threading

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for

from .config import (
    BATTERY_CAPACITY_ROOT,
    BATTERY_CONDITION_WORKBOOK,
    BATTERY_DATA_ROOT,
    BATTERY_EIS_ROOT,
    BATTERY_MATCH_CAPACITY_JSON,
    BATTERY_MATCH_EIS_JSON,
    BATTERY_OUTPUT_ROOT,
    BATTERY_STREAMLIT_URL,
)
from .conditions import read_conditions
from .matching_service import apply_checklist_answers, build_match_payload, save_match_overrides, save_match_review_actions, save_match_selections, verification_payload
from .verification_view import render_verification_html
from .checklist_view import render_checklist_html
from .ai_service import ai_status_payload, get_ai_run, run_ai_smoke
from .capacity_csv_audit import audit_capacity_csv_wrd_pairs
from .excel_dashboard import DEFAULT_CONDITION_SHEET, WorkbookStore, parse_positive_int, render_page as render_excel_dashboard_page
from .experiment_import import (
    BINDER_PRESETS,
    IMPORT_JOURNAL_FIELDS,
    REQUIRED_IMPORT_FIELDS,
    append_import_draft_files,
    build_import_draft_cluster_preview,
    commit_import_draft,
    create_import_draft,
    list_row_units,
    load_import_draft,
    manifest_payload,
    metadata_options_from_conditions,
    preview_normalized_names,
    remove_import_draft_file,
    replace_journal_row_file,
    update_import_draft_assignments,
    update_import_draft_metadata,
)
from .job_service import (
    JOB_TYPES,
    cancel_job,
    create_job,
    get_job,
    job_status_summary,
    job_system_available,
    list_jobs,
    start_job_async,
)
from .viewer_service import (
    capacity_overlay_payload,
    capacity_source_payload,
    capacity_viewer_options,
    clear_match_report_cache,
    draft_overlay_payload,
    eis_overlay_payload,
    eis_viewer_options,
    finder_html,
    journal_orphan_files_payload,
    journal_row_detail_payload,
    journal_row_types_payload,
)


blueprint = Blueprint(
    "battery_lab",
    __name__,
    url_prefix="/battery",
    template_folder="templates",
)
_JOURNAL_STORES: dict[tuple[str, str], WorkbookStore] = {}
_JOURNAL_STORE_LOCK = threading.Lock()


def _path_status(path: Path) -> dict:
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
        "is_file": path.is_file(),
    }


def _top_level_items(root: Path, limit: int = 50) -> list[dict]:
    if not root.exists() or not root.is_dir():
        return []

    items = []
    for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:limit]:
        try:
            stat = child.stat()
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        except OSError:
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size_bytes": None,
                    "mtime": None,
                }
            )
    return items


def _count_files(root: Path, suffixes: set[str]) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes and not path.name.startswith(".")
    )


@blueprint.post("/api/import/drafts")
def create_import_draft_api():
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"ok": False, "error": "No files uploaded."}), 400
    manifest = create_import_draft(
        [(upload.filename or "upload", upload.stream) for upload in uploads],
        BATTERY_OUTPUT_ROOT,
        write_raw_wrd=request.form.get("write_raw_wrd", "").strip().lower() in {"1", "true", "yes", "on"},
    )
    return jsonify({"ok": not manifest.errors, **_import_draft_payload(manifest)})


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _import_draft_payload(manifest) -> dict:
    payload = manifest_payload(manifest)
    draft_root = Path(manifest.draft_root)
    for item in payload.get("files", []):
        plot_path = Path(item.get("plot_path") or "")
        if plot_path and _is_relative_to(plot_path, draft_root):
            item["plot_url"] = url_for(
                "battery_lab.import_draft_artifact",
                draft_id=manifest.draft_id,
                rel_path=str(plot_path.resolve().relative_to(draft_root.resolve())),
            )
        else:
            item["plot_url"] = ""
    payload["units"] = list_row_units(manifest.files)
    return payload


@blueprint.get("/api/import/drafts/<draft_id>/artifact/<path:rel_path>")
def import_draft_artifact(draft_id: str, rel_path: str):
    draft_root = (BATTERY_OUTPUT_ROOT / "import_drafts" / draft_id).resolve()
    path = (draft_root / rel_path).resolve()
    if not _is_relative_to(path, draft_root) or not path.is_file():
        abort(404)
    return send_file(path)


@blueprint.patch("/api/import/drafts/<draft_id>/assignments")
def update_import_draft_assignments_api(draft_id: str):
    payload = request.get_json(silent=True) or {}
    assignments = payload.get("assignments") or {}
    if not isinstance(assignments, dict):
        return jsonify({"ok": False, "error": "assignments must be an object keyed by file_id."}), 400
    try:
        manifest = update_import_draft_assignments(BATTERY_OUTPUT_ROOT, draft_id, {str(k): str(v) for k, v in assignments.items()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **_import_draft_payload(manifest)})


@blueprint.post("/api/import/drafts/<draft_id>/files")
def append_import_draft_files_api(draft_id: str):
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"ok": False, "error": "No files uploaded."}), 400
    try:
        manifest = append_import_draft_files(
            BATTERY_OUTPUT_ROOT,
            draft_id,
            [(upload.filename or "upload", upload.stream) for upload in uploads],
            write_raw_wrd=request.form.get("write_raw_wrd", "").strip().lower() in {"1", "true", "yes", "on"},
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": not manifest.errors, **_import_draft_payload(manifest)})


@blueprint.delete("/api/import/drafts/<draft_id>/files/<file_id>")
def remove_import_draft_file_api(draft_id: str, file_id: str):
    try:
        manifest = remove_import_draft_file(BATTERY_OUTPUT_ROOT, draft_id, file_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **_import_draft_payload(manifest)})


@blueprint.get("/api/import/drafts/<draft_id>/overlay")
def import_draft_overlay_api(draft_id: str):
    """Render the live-viewer overlay (graph + KPI table) for a set of draft files.

    EIS time-series files are passed together so they overlay into one graph.
    """
    file_ids = [fid for fid in request.args.get("file_ids", "").split(",") if fid]
    kind = "capacity" if request.args.get("kind") == "capacity" else "eis"
    color_mode = "time_series" if request.args.get("color_mode") == "time_series" else "comparison"
    try:
        manifest = load_import_draft(BATTERY_OUTPUT_ROOT, draft_id)
    except FileNotFoundError:
        return jsonify({"available": False, "html": "", "errors": ["Draft manifest not found."], "title": ""}), 404
    draft_root = Path(manifest.draft_root)
    by_id = {item.file_id: item for item in manifest.files}
    rel_paths: list[str] = []
    for fid in file_ids:
        item = by_id.get(fid)
        if not item:
            continue
        src = Path(item.processed_path) if (kind == "capacity" and item.processed_path) else Path(item.raw_path)
        try:
            rel_paths.append(str(src.resolve().relative_to(draft_root.resolve())))
        except ValueError:
            rel_paths.append(src.name)
    try:
        payload = draft_overlay_payload(
            draft_root,
            rel_paths,
            kind=kind,
            color_mode=color_mode,
            title=request.args.get("title", ""),
        )
    except Exception as exc:
        return jsonify({"available": False, "html": "", "errors": [str(exc)], "title": request.args.get("title", "")}), 500
    return jsonify(payload)


@blueprint.get("/api/import/metadata-options")
def import_metadata_options_api():
    if not BATTERY_CONDITION_WORKBOOK.exists():
        return jsonify({"ok": True, "required_fields": REQUIRED_IMPORT_FIELDS, "options": {}})
    try:
        conditions = read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name=DEFAULT_CONDITION_SHEET)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "required_fields": REQUIRED_IMPORT_FIELDS, "options": {}}), 500
    return jsonify(
        {
            "ok": True,
            "required_fields": REQUIRED_IMPORT_FIELDS,
            "options": metadata_options_from_conditions(conditions),
        }
    )


@blueprint.get("/api/import/field-spec")
def import_field_spec_api():
    return jsonify({"ok": True, "fields": IMPORT_JOURNAL_FIELDS, "binder_presets": BINDER_PRESETS})


@blueprint.patch("/api/import/drafts/<draft_id>/units/<unit_id>/metadata")
def update_import_draft_metadata_api(draft_id: str, unit_id: str):
    payload = request.get_json(silent=True) or {}
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        return jsonify({"ok": False, "error": "metadata must be an object."}), 400
    try:
        manifest = update_import_draft_metadata(BATTERY_OUTPUT_ROOT, draft_id, unit_id, metadata)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    entry = (manifest.unit_metadata or {}).get(unit_id) or {}
    response = _import_draft_payload(manifest)
    return jsonify(
        {
            "ok": entry.get("metadata_status") == "ready",
            **response,
            "unit_id": unit_id,
            "unit_metadata_status": entry.get("metadata_status"),
            "unit_metadata_errors": entry.get("metadata_errors") or [],
        }
    )


@blueprint.get("/api/import/drafts/<draft_id>/normalized-names")
def import_draft_normalized_names_api(draft_id: str):
    try:
        manifest = load_import_draft(BATTERY_OUTPUT_ROOT, draft_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    rows = preview_normalized_names(manifest, BATTERY_CONDITION_WORKBOOK, DEFAULT_CONDITION_SHEET)
    return jsonify({"ok": True, "rows": rows})


@blueprint.get("/api/import/drafts/<draft_id>/cluster-preview")
def import_draft_cluster_preview_api(draft_id: str):
    try:
        conditions = read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name=DEFAULT_CONDITION_SHEET) if BATTERY_CONDITION_WORKBOOK.exists() else {}
        payload = build_import_draft_cluster_preview(BATTERY_OUTPUT_ROOT, draft_id, conditions)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": payload.get("metadata_status") == "ready", **payload})


@blueprint.post("/api/import/drafts/<draft_id>/commit")
def commit_import_draft_api(draft_id: str):
    try:
        manifest = commit_import_draft(
            BATTERY_OUTPUT_ROOT,
            draft_id,
            eis_root=BATTERY_EIS_ROOT,
            capacity_root=BATTERY_CAPACITY_ROOT,
            condition_workbook=BATTERY_CONDITION_WORKBOOK,
            condition_sheet=DEFAULT_CONDITION_SHEET,
            eis_match_override_path=BATTERY_MATCH_EIS_JSON,
            capacity_match_override_path=BATTERY_MATCH_CAPACITY_JSON,
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    except (KeyError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    response = _import_draft_payload(manifest)
    response["queued_jobs"] = queue_import_rebuild_jobs(manifest)
    return jsonify({"ok": True, **response})


@blueprint.route("/api/capacity/csv-wrd-audit", methods=["GET", "POST"])
def capacity_csv_wrd_audit_api():
    try:
        payload = audit_capacity_csv_wrd_pairs(BATTERY_CAPACITY_ROOT, BATTERY_OUTPUT_ROOT)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify(payload)


def queue_import_rebuild_jobs(manifest) -> list[dict]:
    if not job_system_available():
        return []
    affected = {str(row.get("kind") or "") for row in (manifest.match_overrides or [])}
    jobs = []
    for kind, job_type in (("eis", "build_eis_graphs"), ("capacity", "build_capacity_graphs")):
        if kind not in affected:
            continue
        try:
            job = create_job(
                job_type,
                target=kind,
                params={
                    "recursive": True,
                    "skip_existing": False,
                    "force_rebuild": False,
                    "write_raw_wrd": kind == "capacity",
                    "condition_path": str(BATTERY_CONDITION_WORKBOOK),
                    "condition_sheet": DEFAULT_CONDITION_SHEET,
                    "source": "import_commit",
                    "draft_id": manifest.draft_id,
                    "journal_row": manifest.journal_row,
                },
                created_by="import_wizard",
            )
        except Exception as exc:
            jobs.append({"job_type": job_type, "target": kind, "queued": False, "error": str(exc)})
            continue
        start_job_async(job["id"])
        jobs.append({"job_type": job_type, "target": kind, "queued": True, "job": job})
    return jobs


def _analysis_artifacts(analysis: str, limit: int = 400) -> list[dict]:
    root = BATTERY_OUTPUT_ROOT / analysis
    if not root.exists() or not root.is_dir():
        return []
    artifacts = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg"}:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel_path = path.relative_to(root)
        artifacts.append(
            {
                "name": path.name,
                "relative_path": str(rel_path),
                "url": url_for("battery_lab.artifact", analysis=analysis, rel_path=str(rel_path)),
                "is_svg": path.suffix.lower() == ".svg",
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return sorted(artifacts, key=lambda item: item["name"].lower())[:limit]


def _output_file(name: str) -> Path:
    path = (BATTERY_OUTPUT_ROOT / name).resolve()
    if not _is_relative_to(path, BATTERY_OUTPUT_ROOT):
        abort(404)
    return path


def _selected_artifact(artifacts: list[dict], selected: str) -> dict | None:
    if not artifacts:
        return None
    for artifact in artifacts:
        if artifact["relative_path"] == selected:
            return artifact
    return artifacts[0]


def _app_context(page: str) -> dict:
    eis_artifacts = _analysis_artifacts("eis")
    capacity_artifacts = _analysis_artifacts("capacity")
    selected_eis = request.args.get("graph") or request.args.get("eis") or ""
    selected_capacity = request.args.get("graph") or request.args.get("capacity") or ""
    selected_eis_artifact = _selected_artifact(eis_artifacts, selected_eis)
    selected_capacity_artifact = _selected_artifact(capacity_artifacts, selected_capacity)
    dashboard_path = _output_file("dashboard.html")
    report_path = _output_file("report.html")
    return {
        "page": page,
        "streamlit_url": BATTERY_STREAMLIT_URL,
        "status": {
            "data_root": _path_status(BATTERY_DATA_ROOT),
            "eis_root": _path_status(BATTERY_EIS_ROOT),
            "capacity_root": _path_status(BATTERY_CAPACITY_ROOT),
            "output_root": _path_status(BATTERY_OUTPUT_ROOT),
            "condition_workbook": _path_status(BATTERY_CONDITION_WORKBOOK),
            "eis_match_json": _path_status(BATTERY_MATCH_EIS_JSON),
            "capacity_match_json": _path_status(BATTERY_MATCH_CAPACITY_JSON),
        },
        "summary": {
            "eis_sources": _count_files(BATTERY_EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"}),
            "capacity_sources": _count_files(BATTERY_CAPACITY_ROOT, {".csv", ".wrd", ".xlsx", ".xls"}),
            "eis_artifacts": len(eis_artifacts),
            "capacity_artifacts": len(capacity_artifacts),
            "dashboard_exists": dashboard_path.exists(),
            "report_exists": report_path.exists(),
        },
        "output_root": str(BATTERY_OUTPUT_ROOT),
        "condition_sheet": DEFAULT_CONDITION_SHEET,
        "eis_artifacts": eis_artifacts,
        "capacity_artifacts": capacity_artifacts,
        "selected_eis": selected_eis_artifact["relative_path"] if selected_eis_artifact else "",
        "selected_capacity": selected_capacity_artifact["relative_path"] if selected_capacity_artifact else "",
        "selected_eis_artifact": selected_eis_artifact,
        "selected_capacity_artifact": selected_capacity_artifact,
        "dashboard_url": url_for("battery_lab.output_asset", name="dashboard.html") if dashboard_path.exists() else "",
        "report_url": url_for("battery_lab.output_asset", name="report.html") if report_path.exists() else "",
        "job_types": JOB_TYPES,
        "job_db_available": job_system_available(),
        "ai_db_available": ai_status_payload(BATTERY_OUTPUT_ROOT)["available"],
    }


@blueprint.route("/")
def index():
    legacy_tab = request.args.get("tab")
    if legacy_tab:
        route_map = {
            "dashboard": "battery_lab.journal",
            "journal": "battery_lab.journal",
            "files": "battery_lab.files",
            "eis": "battery_lab.eis",
            "capacity": "battery_lab.capacity",
            "review_EIS_capacity": "battery_lab.review_eis_capacity",
            "voltage_profile": "battery_lab.settings",
        }
        endpoint = route_map.get(legacy_tab, "battery_lab.journal")
        values = {}
        if legacy_tab == "eis" and request.args.get("eis"):
            values["graph"] = request.args["eis"]
        if legacy_tab == "capacity" and request.args.get("capacity"):
            values["graph"] = request.args["capacity"]
        return redirect(url_for(endpoint, **values))
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("journal"),
    )


@blueprint.route("/journal")
def journal():
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("journal"),
    )


def _journal_store() -> WorkbookStore:
    key = (str(BATTERY_CONDITION_WORKBOOK.resolve()), DEFAULT_CONDITION_SHEET)
    with _JOURNAL_STORE_LOCK:
        store = _JOURNAL_STORES.get(key)
        if store is None:
            store = WorkbookStore(BATTERY_CONDITION_WORKBOOK, DEFAULT_CONDITION_SHEET)
            _JOURNAL_STORES[key] = store
        return store


@blueprint.route("/journal/excel")
def journal_excel():
    html = render_excel_dashboard_page(
        sheet_api_url=url_for("battery_lab.journal_sheet_api"),
        cell_api_url=url_for("battery_lab.journal_cell_api"),
        row_types_api_url=url_for("battery_lab.journal_row_types_api"),
        row_detail_api_url=url_for("battery_lab.journal_row_detail_api"),
    )
    return Response(html, mimetype="text/html")


@blueprint.route("/api/journal/row-types", methods=["GET"])
def journal_row_types_api():
    """Map journal row number -> data types (EIS / EIS time series / capacity 1-3).

    Feeds the row-number hover tooltip in the journal viewer. Best-effort: returns
    an empty map instead of erroring so a missing root never breaks the journal.
    """
    try:
        return jsonify(
            journal_row_types_payload(
                BATTERY_EIS_ROOT,
                BATTERY_CAPACITY_ROOT,
                BATTERY_CONDITION_WORKBOOK,
                BATTERY_MATCH_EIS_JSON,
                BATTERY_MATCH_CAPACITY_JSON,
            )
        )
    except Exception as exc:
        return jsonify({"available": False, "row_types": {}, "error": str(exc)})


@blueprint.route("/api/journal/orphan-files", methods=["GET"])
def journal_orphan_files_api():
    """Data files not matched to any journal row ('고아 파일') for the table below the journal."""
    try:
        return jsonify(
            journal_orphan_files_payload(
                BATTERY_EIS_ROOT,
                BATTERY_CAPACITY_ROOT,
                BATTERY_CONDITION_WORKBOOK,
                BATTERY_MATCH_EIS_JSON,
                BATTERY_MATCH_CAPACITY_JSON,
            )
        )
    except Exception as exc:
        return jsonify({"available": False, "count": 0, "files": [], "error": str(exc)})


def _journal_row_info_fields(row_number: int) -> list[dict[str, Any]]:
    """Header-labelled, editable cell list for one journal row (for the detail popup)."""
    payload = _journal_store().sheet_payload(include_ignored=True, extra_rows=0)
    headers: dict[int, str] = {}
    target_cells: list[dict[str, Any]] = []
    for row in payload.get("rows", []):
        if row.get("index") == 1:
            for cell in row.get("cells", []):
                text = str(cell.get("value") or "").strip()
                if text:
                    headers[int(cell["column"])] = text
        if row.get("index") == row_number:
            target_cells = row.get("cells", [])
    fields: list[dict[str, Any]] = []
    for cell in target_cells:
        column = int(cell["column"])
        header = headers.get(column)
        if not header:
            continue
        fields.append(
            {
                "row": int(cell["row"]),
                "column": column,
                "letter": cell.get("letter") or "",
                "header": header,
                "value": cell.get("value"),
                "editable": not bool(cell.get("formulaCell")),
            }
        )
    return fields


@blueprint.route("/api/journal/row-detail", methods=["GET"])
def journal_row_detail_api():
    """Preview + editable experiment-info for one journal row (detail popup)."""
    try:
        row_number = int(request.args.get("row", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "row must be a number"}), 400
    try:
        detail = journal_row_detail_payload(
            BATTERY_EIS_ROOT,
            BATTERY_CAPACITY_ROOT,
            BATTERY_CONDITION_WORKBOOK,
            BATTERY_MATCH_EIS_JSON,
            BATTERY_MATCH_CAPACITY_JSON,
            row=row_number,
        )
        detail["info_fields"] = _journal_row_info_fields(row_number)
        detail["replace_url"] = url_for("battery_lab.journal_row_replace_file_api")
        return jsonify(detail)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@blueprint.route("/api/journal/row-replace-file", methods=["POST"])
def journal_row_replace_file_api():
    """Replace one data file linked to a journal row, then fully recompute.

    multipart/form-data: row, kind (eis|capacity), target (existing rel path),
    write_raw (optional), file (the new raw upload). Backs up the old file(s),
    drops in the new one, and re-derives metrics + match/cluster assignment.
    """
    try:
        row_number = int(request.form.get("row", ""))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "row must be a number"}), 400
    kind = str(request.form.get("kind") or "").strip()
    target = str(request.form.get("target") or "").strip()
    if kind not in {"eis", "capacity"}:
        return jsonify({"ok": False, "error": "kind must be 'eis' or 'capacity'"}), 400
    if not target:
        return jsonify({"ok": False, "error": "교체할 대상 파일이 지정되지 않았습니다."}), 400
    uploaded = request.files.get("file")
    if uploaded is None or not (uploaded.filename or "").strip():
        return jsonify({"ok": False, "error": "새 데이터 파일을 첨부하세요."}), 400
    write_raw = str(request.form.get("write_raw") or "").strip() in {"1", "true", "on"}
    try:
        result = replace_journal_row_file(
            output_root=BATTERY_OUTPUT_ROOT,
            eis_root=BATTERY_EIS_ROOT,
            capacity_root=BATTERY_CAPACITY_ROOT,
            condition_workbook=BATTERY_CONDITION_WORKBOOK,
            condition_sheet=DEFAULT_CONDITION_SHEET,
            eis_match_override_path=BATTERY_MATCH_EIS_JSON,
            capacity_match_override_path=BATTERY_MATCH_CAPACITY_JSON,
            journal_row=row_number,
            target_kind=kind,
            target_rel_path=target,
            upload_stream=uploaded.stream,
            original_filename=uploaded.filename,
            write_raw_wrd=write_raw,
        )
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    # New file on disk + new overrides invalidate the cached match reports.
    clear_match_report_cache()
    return jsonify(result)


@blueprint.route("/journal/download", methods=["GET"])
def journal_download():
    """Download the current condition workbook as an .xlsx file.

    Journal edits and newly registered experiments are saved straight to the
    on-disk workbook (see WorkbookStore.update_cell / append_journal_row), so the
    file served here always reflects the latest journal state.
    """
    wb_path = BATTERY_CONDITION_WORKBOOK
    if not wb_path.is_file():
        return jsonify({"ok": False, "error": "workbook not found", "path": str(wb_path)}), 404
    return send_file(
        wb_path,
        as_attachment=True,
        download_name="Cell condition Calculation.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@blueprint.route("/api/journal/sheet", methods=["GET"])
def journal_sheet_api():
    try:
        include_ignored = request.args.get("filter", "all").strip().lower() not in {"hide", "matched"}
        return jsonify(
            _journal_store().sheet_payload(
                include_ignored=include_ignored,
                row_limit=parse_positive_int(request.args.get("limit")),
                extra_rows=parse_positive_int(request.args.get("extra"), default=100),
            )
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@blueprint.route("/api/journal/cell", methods=["POST"])
def journal_cell_api():
    body = request.get_json(silent=True) or {}
    try:
        cell = _journal_store().update_cell(int(body["row"]), int(body["column"]), body.get("value", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "cell": cell})


_UPLOAD_MAX_BYTES = 20 * 1024 * 1024


@blueprint.route("/api/journal/upload-workbook", methods=["POST"])
def journal_upload_workbook():
    """Admin-only: replace the condition workbook on disk via a token-guarded HTTPS upload.

    The xlsx is intentionally never committed to git (see .gitignore). On Render it lives
    on the persistent disk at BATTERY_CONDITION_WORKBOOK, so this lets a new version be
    pushed straight from a laptop with `curl -F file=@...` instead of pasting into the shell.
    """
    token = os.environ.get("BATTERY_ADMIN_UPLOAD_TOKEN")
    if not token:
        return jsonify({"ok": False, "error": "BATTERY_ADMIN_UPLOAD_TOKEN is not set"}), 500

    given = request.headers.get("X-Upload-Token") or request.form.get("token")
    if given != token:
        abort(403)

    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"ok": False, "error": "missing file field"}), 400
    if not (uploaded.filename or "").lower().endswith(".xlsx"):
        return jsonify({"ok": False, "error": "only .xlsx is allowed"}), 400

    wb_path = BATTERY_CONDITION_WORKBOOK
    wb_path.parent.mkdir(parents=True, exist_ok=True)
    expected_sha = request.headers.get("X-Expected-SHA256", "").strip().lower()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = wb_path.with_name(f".upload-{stamp}.xlsx")

    digest = hashlib.sha256()
    total = 0
    try:
        with tmp_path.open("wb") as handle:
            while True:
                chunk = uploaded.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _UPLOAD_MAX_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    return jsonify({"ok": False, "error": "file too large"}), 413
                digest.update(chunk)
                handle.write(chunk)

        actual_sha = digest.hexdigest()
        if expected_sha and actual_sha != expected_sha:
            tmp_path.unlink(missing_ok=True)
            return jsonify({
                "ok": False,
                "error": "sha256 mismatch",
                "expected": expected_sha,
                "actual": actual_sha,
                "size": total,
            }), 400

        backup_path = None
        if wb_path.exists():
            backup_path = wb_path.with_name(f"{wb_path.name}.bak.{stamp}")
            shutil.copy2(wb_path, backup_path)
            try:
                old_stat = wb_path.stat()
                os.chmod(tmp_path, old_stat.st_mode)
            except OSError:
                pass

        os.replace(tmp_path, wb_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "target": str(wb_path),
        "backup": str(backup_path) if backup_path else None,
        "size": total,
        "sha256": actual_sha,
    })


@blueprint.route("/api/journal/download-workbook", methods=["GET"])
def journal_download_workbook():
    """Admin-only: download the current condition workbook from disk.

    Token-guarded with the same BATTERY_ADMIN_UPLOAD_TOKEN as the upload route.
    The download name is normalized regardless of the on-disk filename.
    """
    token = os.environ.get("BATTERY_ADMIN_UPLOAD_TOKEN")
    if not token:
        return jsonify({"ok": False, "error": "BATTERY_ADMIN_UPLOAD_TOKEN is not set"}), 500

    given = request.headers.get("X-Upload-Token") or request.args.get("token")
    if given != token:
        abort(403)

    wb_path = BATTERY_CONDITION_WORKBOOK
    if not wb_path.is_file():
        return jsonify({"ok": False, "error": "workbook not found", "path": str(wb_path)}), 404

    return send_file(
        wb_path,
        as_attachment=True,
        download_name="Cell condition Calculation.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@blueprint.route("/eis")
def eis():
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("eis"),
    )


@blueprint.route("/capacity")
def capacity():
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("capacity"),
    )


@blueprint.route("/review_EIS_capacity")
def review_eis_capacity():
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("review_EIS_capacity"),
    )


@blueprint.route("/jobs")
def jobs():
    return redirect(url_for("battery_lab.eis"))


@blueprint.route("/settings")
def settings():
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("settings"),
    )


@blueprint.route("/status")
def status():
    status = {
        "data_root": _path_status(BATTERY_DATA_ROOT),
        "eis_root": _path_status(BATTERY_EIS_ROOT),
        "capacity_root": _path_status(BATTERY_CAPACITY_ROOT),
        "output_root": _path_status(BATTERY_OUTPUT_ROOT),
        "condition_workbook": _path_status(BATTERY_CONDITION_WORKBOOK),
        "eis_match_json": _path_status(BATTERY_MATCH_EIS_JSON),
        "capacity_match_json": _path_status(BATTERY_MATCH_CAPACITY_JSON),
    }
    return render_template(
        "battery_lab/index.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        status=status,
        streamlit_url=BATTERY_STREAMLIT_URL,
    )


@blueprint.route("/health")
def health():
    return jsonify(
        {
            "data_root": str(BATTERY_DATA_ROOT),
            "data_root_exists": BATTERY_DATA_ROOT.exists(),
            "eis_root": str(BATTERY_EIS_ROOT),
            "eis_root_exists": BATTERY_EIS_ROOT.exists(),
            "capacity_root": str(BATTERY_CAPACITY_ROOT),
            "capacity_root_exists": BATTERY_CAPACITY_ROOT.exists(),
            "output_root": str(BATTERY_OUTPUT_ROOT),
            "output_root_exists": BATTERY_OUTPUT_ROOT.exists(),
            "condition_workbook": str(BATTERY_CONDITION_WORKBOOK),
            "condition_workbook_exists": BATTERY_CONDITION_WORKBOOK.exists(),
            "eis_match_json": str(BATTERY_MATCH_EIS_JSON),
            "eis_match_json_exists": BATTERY_MATCH_EIS_JSON.exists(),
            "capacity_match_json": str(BATTERY_MATCH_CAPACITY_JSON),
            "capacity_match_json_exists": BATTERY_MATCH_CAPACITY_JSON.exists(),
        }
    )


@blueprint.route("/files")
def files():
    roots = {
        "EIS": {
            "status": _path_status(BATTERY_EIS_ROOT),
            "entries": _top_level_items(BATTERY_EIS_ROOT),
        },
        "Capacity": {
            "status": _path_status(BATTERY_CAPACITY_ROOT),
            "entries": _top_level_items(BATTERY_CAPACITY_ROOT),
        },
        "Outputs": {
            "status": _path_status(BATTERY_OUTPUT_ROOT),
            "entries": _top_level_items(BATTERY_OUTPUT_ROOT),
        },
        "Project_Abstract": {
            "status": _path_status(BATTERY_CONDITION_WORKBOOK.parent),
            "entries": _top_level_items(BATTERY_CONDITION_WORKBOOK.parent),
        },
    }
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("files"),
        roots=roots,
    )


@blueprint.route("/api/<kind>/matches", methods=["GET", "POST", "DELETE"])
def match_api(kind: str):
    config = _match_api_config(kind)
    if config is None:
        abort(404)
    source_root, override_path = config
    if request.method == "GET":
        return jsonify(build_match_payload(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path))
    if request.method == "DELETE":
        save_match_overrides(override_path, {})
        payload = build_match_payload(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path)
        payload["saved_count"] = 0
        return jsonify(payload)
    body = request.get_json(silent=True) or {}
    selections = body.get("selections") or []
    if not isinstance(selections, list):
        return jsonify({"error": "selections must be a list"}), 400
    try:
        return jsonify(save_match_selections(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path, selections))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/<kind>/match-review", methods=["POST"])
def match_review_api(kind: str):
    config = _match_api_config(kind)
    if config is None:
        abort(404)
    source_root, override_path = config
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(
            save_match_review_actions(
                kind,
                source_root,
                BATTERY_CONDITION_WORKBOOK,
                override_path,
                selected_candidates=body.get("selected_candidates") if isinstance(body.get("selected_candidates"), list) else [],
                direct_matches=body.get("direct_matches") if isinstance(body.get("direct_matches"), list) else [],
                delete_files=body.get("delete_files") if isinstance(body.get("delete_files"), list) else [],
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def _match_api_config(kind: str) -> tuple[Path, Path] | None:
    if kind == "eis":
        return BATTERY_EIS_ROOT, BATTERY_MATCH_EIS_JSON
    if kind == "capacity":
        return BATTERY_CAPACITY_ROOT, BATTERY_MATCH_CAPACITY_JSON
    return None


@blueprint.route("/api/<kind>/verification", methods=["GET"])
def verification_api(kind: str):
    """Read-only verification view: per-file matching evidence over the in-scope
    journal rows, plus orphan rows and 1:1 invariant signals."""
    config = _match_api_config(kind)
    if config is None:
        abort(404)
    source_root, override_path = config
    try:
        return jsonify(verification_payload(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path))
    except Exception as exc:  # best-effort; never 500 the review tab
        return jsonify({"kind": kind, "rows": [], "orphans": [], "summary": {}, "error": str(exc)}), 500


@blueprint.route("/verification", methods=["GET"])
def verification_view_page():
    """Server-rendered evidence table: every in-scope file with the why of its match
    (verified included), plus orphan rows and duplicates. Read-only."""
    payloads: dict = {}
    for kind in ("capacity", "eis"):
        cfg = _match_api_config(kind)
        if cfg is None:
            continue
        source_root, override_path = cfg
        try:
            payloads[kind] = verification_payload(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path)
        except Exception as exc:  # never break the page on one kind
            payloads[kind] = {"kind": kind, "summary": {}, "rows": [], "orphans": [], "invariant": {}, "error": str(exc)}
    return Response(render_verification_html(payloads), mimetype="text/html")


@blueprint.route("/checklist", methods=["GET"])
def checklist_page():
    """Self-contained fillable checklist HTML (send to the research lead via KakaoTalk)."""
    payloads: dict = {}
    for kind in ("eis", "capacity"):
        cfg = _match_api_config(kind)
        if cfg is None:
            continue
        source_root, override_path = cfg
        try:
            payloads[kind] = verification_payload(kind, source_root, BATTERY_CONDITION_WORKBOOK, override_path)
        except Exception as exc:
            payloads[kind] = {"kind": kind, "rows": [], "orphans": [], "deferred_rows": [], "summary": {}, "error": str(exc)}
    return Response(render_checklist_html(payloads), mimetype="text/html")


@blueprint.route("/api/checklist/apply", methods=["POST"])
def checklist_apply_api():
    """Apply the lead's returned checklist answers (JSON blob) into overrides.json."""
    kind = request.args.get("kind", "eis")
    cfg = _match_api_config(kind)
    if cfg is None:
        abort(404)
    _, override_path = cfg
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(apply_checklist_answers(body, BATTERY_CONDITION_WORKBOOK, override_path))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/eis/viewer/options", methods=["GET"])
def eis_viewer_options_api():
    return jsonify(eis_viewer_options(BATTERY_EIS_ROOT, BATTERY_CAPACITY_ROOT, BATTERY_CONDITION_WORKBOOK, BATTERY_MATCH_EIS_JSON))


@blueprint.route("/api/eis/finder", methods=["GET"])
def eis_finder_api():
    return Response(finder_html(BATTERY_EIS_ROOT, BATTERY_CAPACITY_ROOT, kind="eis"), mimetype="text/html")


@blueprint.route("/api/eis/viewer/overlay", methods=["GET"])
def eis_viewer_overlay_api():
    try:
        payload = eis_overlay_payload(
            BATTERY_EIS_ROOT,
            BATTERY_CAPACITY_ROOT,
            BATTERY_CONDITION_WORKBOOK,
            BATTERY_MATCH_EIS_JSON,
            mode=request.args.get("mode", "comparison"),
            key=request.args.get("key", ""),
            show_fit=request.args.get("show_fit", "").strip().lower() in {"1", "true", "yes", "on"},
        )
    except Exception as exc:
        return jsonify({"available": False, "html": "", "errors": [str(exc)], "title": "EIS viewer"}), 500
    return jsonify(payload)


@blueprint.route("/api/capacity/viewer/options", methods=["GET"])
def capacity_viewer_options_api():
    return jsonify(capacity_viewer_options(BATTERY_CAPACITY_ROOT, BATTERY_EIS_ROOT, BATTERY_CONDITION_WORKBOOK, BATTERY_MATCH_CAPACITY_JSON))


@blueprint.route("/api/capacity/finder", methods=["GET"])
def capacity_finder_api():
    return Response(finder_html(BATTERY_EIS_ROOT, BATTERY_CAPACITY_ROOT, kind="capacity"), mimetype="text/html")


@blueprint.route("/api/capacity/viewer/overlay", methods=["GET"])
def capacity_viewer_overlay_api():
    try:
        payload = capacity_overlay_payload(
            BATTERY_CAPACITY_ROOT,
            BATTERY_EIS_ROOT,
            BATTERY_CONDITION_WORKBOOK,
            BATTERY_MATCH_CAPACITY_JSON,
            mode=request.args.get("mode", "cluster"),
            key=request.args.get("key", ""),
        )
    except Exception as exc:
        return jsonify({"available": False, "html": "", "errors": [str(exc)], "title": "Capacity viewer"}), 500
    return jsonify(payload)


@blueprint.route("/api/capacity/viewer/source", methods=["GET"])
def capacity_viewer_source_api():
    return jsonify(capacity_source_payload(BATTERY_CAPACITY_ROOT, BATTERY_EIS_ROOT, request.args.get("key", "")))


@blueprint.route("/api/jobs", methods=["GET", "POST"])
def jobs_api():
    if not job_system_available():
        return jsonify({"available": False, "jobs": [], "status_counts": {}, "job_types": JOB_TYPES}), 503
    if request.method == "GET":
        status = request.args.get("status") or None
        limit = _int_arg("limit", 50, minimum=1, maximum=200)
        return jsonify(
            {
                "available": True,
                "jobs": list_jobs(limit=limit, status=status),
                "status_counts": job_status_summary(),
                "job_types": JOB_TYPES,
            }
        )
    body = request.get_json(silent=True) or {}
    try:
        job = create_job(
            str(body.get("job_type") or ""),
            target=str(body.get("target") or ""),
            params=body.get("params") if isinstance(body.get("params"), dict) else {},
            created_by=str(body.get("created_by") or ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    start_job_async(int(job["id"]))
    job = get_job(int(job["id"])) or job
    return jsonify({"available": True, "job": job}), 201


@blueprint.route("/api/jobs/<int:job_id>", methods=["GET"])
def job_detail_api(job_id: int):
    if not job_system_available():
        return jsonify({"available": False, "job": None}), 503
    job = get_job(job_id)
    if job is None:
        abort(404)
    return jsonify({"available": True, "job": job})


@blueprint.route("/api/jobs/<int:job_id>/cancel", methods=["POST"])
def job_cancel_api(job_id: int):
    if not job_system_available():
        return jsonify({"available": False, "job": None}), 503
    job = cancel_job(job_id)
    if job is None:
        abort(404)
    return jsonify({"available": True, "job": job})


@blueprint.route("/api/ai/status", methods=["GET"])
def ai_status_api():
    return jsonify(ai_status_payload(BATTERY_OUTPUT_ROOT))


@blueprint.route("/api/ai/smoke", methods=["POST"])
def ai_smoke_api():
    body = request.get_json(silent=True) or {}
    try:
        payload = run_ai_smoke(
            BATTERY_OUTPUT_ROOT,
            call_api=bool(body.get("call_api")),
            created_by=str(body.get("created_by") or ""),
        )
    except RuntimeError as exc:
        return jsonify({"available": False, "error": str(exc), "policy": ai_status_payload(BATTERY_OUTPUT_ROOT)["policy"]}), 503
    return jsonify(payload)


@blueprint.route("/api/ai/runs/<int:run_id>", methods=["GET"])
def ai_run_detail_api(run_id: int):
    run = get_ai_run(run_id)
    if run is None:
        abort(404)
    return jsonify({"available": True, "run": run})


def _int_arg(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@blueprint.route("/output/<path:name>")
def output_asset(name: str):
    path = _output_file(name)
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(path)


@blueprint.route("/artifact/<analysis>/<path:rel_path>")
def artifact(analysis: str, rel_path: str):
    if analysis not in {"eis", "capacity", "voltage_profile"}:
        abort(404)
    root = (BATTERY_OUTPUT_ROOT / analysis).resolve()
    path = (root / rel_path).resolve()
    if not _is_relative_to(path, root) or not path.exists() or not path.is_file():
        abort(404)
    if path.suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg"}:
        abort(404)
    return send_file(path)
