from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template

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


@blueprint.route("/")
def index():
    if BATTERY_STREAMLIT_URL:
        return render_template(
            "battery_lab/streamlit.html",
            layout_template=current_app.config.get("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html"),
            streamlit_url=BATTERY_STREAMLIT_URL,
        )
    return status()


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
