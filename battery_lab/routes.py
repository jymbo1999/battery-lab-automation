from pathlib import Path
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
from .matching_service import build_match_payload, save_match_overrides, save_match_review_actions, save_match_selections
from .ai_service import ai_status_payload, get_ai_run, run_ai_smoke
from .excel_dashboard import DEFAULT_CONDITION_SHEET, WorkbookStore, render_page as render_excel_dashboard_page
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
    eis_overlay_payload,
    eis_viewer_options,
    finder_html,
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


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
            "dashboard": "battery_lab.index",
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
        **_app_context("dashboard"),
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
    )
    return Response(html, mimetype="text/html")


@blueprint.route("/api/journal/sheet", methods=["GET"])
def journal_sheet_api():
    try:
        return jsonify(_journal_store().sheet_payload())
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
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context("jobs"),
    )


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
