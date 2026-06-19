from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, url_for

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


blueprint = Blueprint(
    "battery_lab",
    __name__,
    url_prefix="/battery",
    template_folder="templates",
)


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
        rel_path = path.relative_to(root)
        artifacts.append(
            {
                "name": path.name,
                "relative_path": str(rel_path),
                "url": url_for("battery_lab.artifact", analysis=analysis, rel_path=str(rel_path)),
                "is_svg": path.suffix.lower() == ".svg",
            }
        )
    return sorted(artifacts, key=lambda item: item["name"].lower())[:limit]


def _output_file(name: str) -> Path:
    path = (BATTERY_OUTPUT_ROOT / name).resolve()
    if not _is_relative_to(path, BATTERY_OUTPUT_ROOT):
        abort(404)
    return path


def _app_context() -> dict:
    eis_artifacts = _analysis_artifacts("eis")
    capacity_artifacts = _analysis_artifacts("capacity")
    selected_tab = request.args.get("tab", "journal")
    selected_eis = request.args.get("eis") or (eis_artifacts[0]["relative_path"] if eis_artifacts else "")
    selected_capacity = request.args.get("capacity") or (
        capacity_artifacts[0]["relative_path"] if capacity_artifacts else ""
    )
    dashboard_path = _output_file("dashboard.html")
    report_path = _output_file("report.html")
    return {
        "selected_tab": selected_tab,
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
        "eis_artifacts": eis_artifacts,
        "capacity_artifacts": capacity_artifacts,
        "selected_eis": selected_eis,
        "selected_capacity": selected_capacity,
        "dashboard_url": url_for("battery_lab.output_asset", name="dashboard.html") if dashboard_path.exists() else "",
        "report_url": url_for("battery_lab.output_asset", name="report.html") if report_path.exists() else "",
    }


@blueprint.route("/")
def index():
    if BATTERY_STREAMLIT_URL:
        return render_template(
            "battery_lab/streamlit.html",
            layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
            streamlit_url=BATTERY_STREAMLIT_URL,
        )
    return render_template(
        "battery_lab/app.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        **_app_context(),
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
        "battery_lab/files.html",
        layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
        roots=roots,
    )


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
