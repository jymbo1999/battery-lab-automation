from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .job_models import BatteryJob, BatteryJobLog, db, jobs_db_available


JOB_TYPES = {
    "rescan_files": "파일 재스캔",
    "build_eis_graphs": "EIS 그래프 재생성",
    "build_capacity_graphs": "Capacity 그래프 재생성",
    "eis_fitting_batch": "EIS fitting batch",
}
JOB_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}
_JOBS_SCHEMA_READY = False


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
            message="Job queued. Worker execution is not enabled in this phase.",
            payload_json="{}",
            created_at=now,
        )
    )
    db.session.commit()
    return serialize_job(job, include_logs=True)


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
