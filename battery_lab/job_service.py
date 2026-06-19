from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import current_app, has_app_context

from .job_models import BatteryJob, BatteryJobLog, db, jobs_db_available


JOB_TYPES = {
    "rescan_files": "파일 재스캔",
    "build_eis_graphs": "EIS 그래프 재생성",
    "build_capacity_graphs": "Capacity 그래프 재생성",
    "eis_fitting_batch": "EIS fitting batch",
}
JOB_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}
_JOBS_SCHEMA_READY = False
JOB_EXECUTORS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {}


def job_system_available() -> bool:
    return jobs_db_available()


def ensure_job_tables() -> bool:
    global _JOBS_SCHEMA_READY
    if not job_system_available():
        return False
    if _JOBS_SCHEMA_READY:
        return True
    BatteryJob.__table__.create(bind=db.engine, checkfirst=True)
    BatteryJobLog.__table__.create(bind=db.engine, checkfirst=True)
    _JOBS_SCHEMA_READY = True
    return True


def serialize_job(job: Any, *, include_logs: bool = False) -> dict[str, Any]:
    payload = {
        "id": job.id,
        "job_type": job.job_type,
        "job_type_label": JOB_TYPES.get(job.job_type, job.job_type),
        "status": job.status,
        "target": job.target,
        "params": parse_json(job.params_json),
        "result": parse_json(job.result_json),
        "error_message": job.error_message,
        "created_by": job.created_by,
        "created_at": iso(job.created_at),
        "queued_at": iso(job.queued_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "updated_at": iso(job.updated_at),
    }
    if include_logs:
        payload["logs"] = [serialize_log(log) for log in job.logs]
    return payload


def serialize_log(log: Any) -> dict[str, Any]:
    return {
        "id": log.id,
        "job_id": log.job_id,
        "level": log.level,
        "message": log.message,
        "payload": parse_json(log.payload_json),
        "created_at": iso(log.created_at),
    }


def list_jobs(*, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    if not ensure_job_tables():
        return []
    query = BatteryJob.query
    if status:
        query = query.filter(BatteryJob.status == status)
    jobs = query.order_by(BatteryJob.created_at.desc(), BatteryJob.id.desc()).limit(limit).all()
    return [serialize_job(job) for job in jobs]


def job_status_summary() -> dict[str, int]:
    if not ensure_job_tables():
        return {}
    rows = db.session.query(BatteryJob.status, db.func.count(BatteryJob.id)).group_by(BatteryJob.status).all()
    return {status: int(count) for status, count in rows}


def get_job(job_id: int) -> dict[str, Any] | None:
    if not ensure_job_tables():
        return None
    job = db.session.get(BatteryJob, job_id)
    return serialize_job(job, include_logs=True) if job else None


def create_job(job_type: str, *, target: str = "", params: dict[str, Any] | None = None, created_by: str = "") -> dict[str, Any]:
    if not ensure_job_tables():
        raise RuntimeError("Battery job DB is not available.")
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unsupported job_type: {job_type}")
    now = datetime.utcnow()
    job = BatteryJob(
        job_type=job_type,
        status="queued",
        target=target,
        params_json=json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
        result_json="{}",
        created_by=created_by,
        created_at=now,
        queued_at=now,
        updated_at=now,
    )
    db.session.add(job)
    db.session.flush()
    db.session.add(
        BatteryJobLog(
            job_id=job.id,
            level="info",
            message="Job queued.",
            payload_json="{}",
            created_at=now,
        )
    )
    db.session.commit()
    return serialize_job(job, include_logs=True)


def start_job_async(job_id: int) -> bool:
    if not job_system_available():
        return False
    app = current_app._get_current_object() if has_app_context() else None

    def target() -> None:
        if app is None:
            run_job(job_id)
            return
        with app.app_context():
            run_job(job_id)

    thread = threading.Thread(target=target, name=f"battery-job-{job_id}", daemon=True)
    thread.start()
    return True


def run_job(job_id: int) -> dict[str, Any] | None:
    if not ensure_job_tables():
        return None
    job = db.session.get(BatteryJob, job_id)
    if job is None:
        return None
    if job.status != "queued":
        return serialize_job(job, include_logs=True)

    now = datetime.utcnow()
    job.status = "running"
    job.started_at = now
    job.updated_at = now
    append_job_log(job.id, "info", "Job started.", {})
    db.session.commit()

    try:
        result = execute_job(job.job_type, job.target, parse_json(job.params_json))
    except Exception as exc:
        now = datetime.utcnow()
        job = db.session.get(BatteryJob, job_id)
        if job is None:
            return None
        job.status = "failed"
        job.error_message = str(exc)
        job.finished_at = now
        job.updated_at = now
        job.result_json = json.dumps({"error": str(exc)}, ensure_ascii=False, sort_keys=True)
        append_job_log(job.id, "error", "Job failed.", {"error": str(exc)})
        db.session.commit()
        return serialize_job(job, include_logs=True)

    now = datetime.utcnow()
    job = db.session.get(BatteryJob, job_id)
    if job is None:
        return None
    if job.status == "cancelled":
        append_job_log(job.id, "warning", "Job finished after cancellation.", json_safe(result))
        db.session.commit()
        return serialize_job(job, include_logs=True)
    job.status = "succeeded"
    job.result_json = json.dumps(json_safe(result), ensure_ascii=False, sort_keys=True)
    job.finished_at = now
    job.updated_at = now
    append_job_log(job.id, "info", "Job succeeded.", json_safe(result))
    db.session.commit()
    return serialize_job(job, include_logs=True)


def execute_job(job_type: str, target: str, params: dict[str, Any]) -> dict[str, Any]:
    executor = JOB_EXECUTORS.get(job_type) or default_job_executor(job_type)
    return executor(target, params)


def default_job_executor(job_type: str) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    if job_type == "rescan_files":
        return execute_rescan_files
    if job_type == "build_eis_graphs":
        return execute_build_eis_graphs
    if job_type == "build_capacity_graphs":
        return execute_build_capacity_graphs
    if job_type == "eis_fitting_batch":
        return execute_eis_fitting_batch
    raise ValueError(f"Unsupported job_type: {job_type}")


def execute_rescan_files(target: str, params: dict[str, Any]) -> dict[str, Any]:
    from .config import BATTERY_CAPACITY_ROOT, BATTERY_EIS_ROOT, BATTERY_OUTPUT_ROOT
    from . import ui as streamlit_ui

    return {
        "target": target,
        "eis_sources": len(streamlit_ui.collect_source_files(BATTERY_EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})),
        "capacity_sources": len(streamlit_ui.collect_source_files(BATTERY_CAPACITY_ROOT, {".csv", ".wrd", ".xlsx", ".xls"})),
        "eis_artifacts": count_artifacts(BATTERY_OUTPUT_ROOT / "eis"),
        "capacity_artifacts": count_artifacts(BATTERY_OUTPUT_ROOT / "capacity"),
    }


def execute_build_eis_graphs(target: str, params: dict[str, Any]) -> dict[str, Any]:
    from .config import BATTERY_CONDITION_WORKBOOK, BATTERY_EIS_ROOT
    from .excel_dashboard import DEFAULT_CONDITION_SHEET
    from . import ui as streamlit_ui

    return streamlit_ui.build_analysis_artifacts(
        BATTERY_EIS_ROOT,
        "eis",
        {".seo", ".sde", ".csv", ".xlsx", ".xls"},
        recursive=bool_param(params, "recursive", True),
        limit=int_param(params, "limit"),
        skip_existing=bool_param(params, "skip_existing", True),
        force_rebuild=bool_param(params, "force_rebuild", False),
        condition_path=path_param(params, "condition_path", BATTERY_CONDITION_WORKBOOK),
        condition_sheet=str(params.get("condition_sheet") or DEFAULT_CONDITION_SHEET),
    )


def execute_build_capacity_graphs(target: str, params: dict[str, Any]) -> dict[str, Any]:
    from .config import BATTERY_CAPACITY_ROOT, BATTERY_CONDITION_WORKBOOK
    from .excel_dashboard import DEFAULT_CONDITION_SHEET
    from . import ui as streamlit_ui

    return streamlit_ui.build_analysis_artifacts(
        BATTERY_CAPACITY_ROOT,
        "capacity",
        {".csv", ".wrd", ".xlsx", ".xls"},
        recursive=bool_param(params, "recursive", True),
        limit=int_param(params, "limit"),
        skip_existing=bool_param(params, "skip_existing", True),
        force_rebuild=bool_param(params, "force_rebuild", False),
        write_raw_wrd=bool_param(params, "write_raw_wrd", False),
        condition_path=path_param(params, "condition_path", BATTERY_CONDITION_WORKBOOK),
        condition_sheet=str(params.get("condition_sheet") or DEFAULT_CONDITION_SHEET),
    )


def execute_eis_fitting_batch(target: str, params: dict[str, Any]) -> dict[str, Any]:
    from .config import BATTERY_EIS_ROOT
    from .file_io import parse_file
    from . import ui as streamlit_ui

    if streamlit_ui.load_valid_fit_metadata is None:
        raise RuntimeError("EIS fitting module is not available.")
    source_paths = streamlit_ui.collect_source_files(BATTERY_EIS_ROOT, {".seo", ".sde", ".csv", ".xlsx", ".xls"})
    limit = int_param(params, "limit")
    if limit:
        source_paths = source_paths[:limit]
    computed = failed = skipped = 0
    failures: list[str] = []
    for path in source_paths:
        if streamlit_ui.load_valid_fit_metadata(path) is not None:
            skipped += 1
            continue
        try:
            parse_file(path)
            if streamlit_ui.load_valid_fit_metadata(path) is not None:
                computed += 1
            else:
                failed += 1
                failures.append(f"{path.relative_to(BATTERY_EIS_ROOT)}: 표시 가능한 EIS 좌표 없음")
        except Exception as exc:
            failed += 1
            failures.append(f"{path.relative_to(BATTERY_EIS_ROOT)}: {exc}")
    return {"source_count": len(source_paths), "computed": computed, "skipped": skipped, "failed": failed, "failures": failures[:80]}


def append_job_log(job_id: int, level: str, message: str, payload: dict[str, Any]) -> None:
    db.session.add(
        BatteryJobLog(
            job_id=job_id,
            level=level,
            message=message,
            payload_json=json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True),
            created_at=datetime.utcnow(),
        )
    )


def count_artifacts(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg"})


def bool_param(params: dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def int_param(params: dict[str, Any], key: str) -> int | None:
    value = params.get(key)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def path_param(params: dict[str, Any], key: str, default: Path) -> Path:
    value = str(params.get(key) or "").strip()
    return Path(value).expanduser() if value else default


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def cancel_job(job_id: int) -> dict[str, Any] | None:
    if not ensure_job_tables():
        return None
    job = db.session.get(BatteryJob, job_id)
    if job is None:
        return None
    if job.status in {"succeeded", "failed", "cancelled"}:
        return serialize_job(job, include_logs=True)
    now = datetime.utcnow()
    job.status = "cancelled"
    job.finished_at = now
    job.updated_at = now
    db.session.add(
        BatteryJobLog(
            job_id=job.id,
            level="warning",
            message="Job cancelled before worker execution.",
            payload_json="{}",
            created_at=now,
        )
    )
    db.session.commit()
    return serialize_job(job, include_logs=True)


def parse_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"
