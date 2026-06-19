from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    BATTERY_AI_ENABLE_API,
    BATTERY_AI_MAX_INPUT_CHARS,
    BATTERY_AI_MAX_RETRIES,
    BATTERY_AI_MODEL,
    BATTERY_AI_TIMEOUT_SECONDS,
)
from .job_models import BatteryAiAnnotation, BatteryAiRun, ai_db_available, db
from .job_service import iso, parse_json


PROMPT_VERSION = "battery-analysis-summary-v1"
AI_RUN_STATUSES = {"draft", "dry_run", "succeeded", "failed", "skipped"}
SUMMARY_FILES = (
    "summary_metrics.csv",
    "analysis_availability.csv",
    "analysis_comparison_validations.csv",
    "eis_match_report.json",
    "capacity_match_report.json",
)
_AI_SCHEMA_READY = False


SYSTEM_INSTRUCTIONS = """You are a battery lab analysis assistant.
Summarize only from the provided data snapshot.
Flag uncertainty and missing data explicitly.
Do not invent experimental results, sample names, or causal explanations.
Return concise Korean text with: 핵심 요약, 이상 징후, 확인 필요, 다음 액션."""


def ai_system_available() -> bool:
    return ai_db_available()


def ensure_ai_tables() -> bool:
    global _AI_SCHEMA_READY
    if not ai_system_available():
        return False
    if _AI_SCHEMA_READY:
        return True
    BatteryAiRun.__table__.create(bind=db.engine, checkfirst=True)
    BatteryAiAnnotation.__table__.create(bind=db.engine, checkfirst=True)
    _AI_SCHEMA_READY = True
    return True


def policy_payload() -> dict[str, Any]:
    return {
        "model": BATTERY_AI_MODEL,
        "api_enabled": BATTERY_AI_ENABLE_API,
        "api_key_present": bool(os.getenv("OPENAI_API_KEY")),
        "timeout_seconds": BATTERY_AI_TIMEOUT_SECONDS,
        "max_retries": BATTERY_AI_MAX_RETRIES,
        "max_input_chars": BATTERY_AI_MAX_INPUT_CHARS,
        "sdk_available": openai_sdk_available(),
        "default_call_mode": "dry_run",
    }


def openai_sdk_available() -> bool:
    try:
        import openai  # noqa: F401
    except Exception:
        return False
    return True


def ai_status_payload(output_root: Path, *, limit: int = 10) -> dict[str, Any]:
    snapshot = build_analysis_snapshot(output_root)
    return {
        "available": ai_system_available(),
        "policy": policy_payload(),
        "prompt_version": PROMPT_VERSION,
        "snapshot": snapshot,
        "recent_runs": list_ai_runs(limit=limit),
    }


def run_ai_smoke(output_root: Path, *, call_api: bool = False, created_by: str = "") -> dict[str, Any]:
    if not ensure_ai_tables():
        raise RuntimeError("Battery AI DB is not available.")

    snapshot = build_analysis_snapshot(output_root)
    prompt = build_summary_prompt(snapshot)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    request_payload = {
        "model": BATTERY_AI_MODEL,
        "instructions": SYSTEM_INSTRUCTIONS,
        "input": prompt,
        "call_api": call_api,
        "prompt_version": PROMPT_VERSION,
    }
    now = datetime.utcnow()
    run = BatteryAiRun(
        run_type="analysis_summary",
        source_kind="battery_outputs",
        source_ref=str(output_root),
        model=BATTERY_AI_MODEL,
        status="draft",
        prompt_version=PROMPT_VERSION,
        prompt_hash=prompt_hash,
        input_summary_json=json_dumps(snapshot),
        request_json=json_dumps(redact_request(request_payload)),
        response_json="{}",
        timeout_seconds=BATTERY_AI_TIMEOUT_SECONDS,
        max_retries=BATTERY_AI_MAX_RETRIES,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.session.add(run)
    db.session.flush()

    if not call_api:
        run.status = "dry_run"
        run.output_text = dry_run_output(snapshot, prompt)
        run.finished_at = now
        add_annotation(run.id, "summary", "battery_outputs", str(output_root), {"mode": "dry_run"})
        db.session.commit()
        return {"available": True, "run": serialize_ai_run(run, include_prompt=True), "policy": policy_payload()}

    skip_reason = api_skip_reason()
    if skip_reason:
        run.status = "skipped"
        run.error_message = skip_reason
        run.finished_at = datetime.utcnow()
        add_annotation(run.id, "summary", "battery_outputs", str(output_root), {"mode": "skipped", "reason": skip_reason})
        db.session.commit()
        return {"available": True, "run": serialize_ai_run(run, include_prompt=True), "policy": policy_payload()}

    run.started_at = datetime.utcnow()
    db.session.flush()
    try:
        response_payload = call_openai_responses(SYSTEM_INSTRUCTIONS, prompt)
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        return {"available": True, "run": serialize_ai_run(run, include_prompt=True), "policy": policy_payload()}

    usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
    run.status = "succeeded"
    run.output_text = str(response_payload.get("output_text") or "")
    run.response_json = json_dumps(response_payload.get("raw") or {})
    run.input_tokens = usage.get("input_tokens")
    run.output_tokens = usage.get("output_tokens")
    run.finished_at = datetime.utcnow()
    add_annotation(run.id, "summary", "battery_outputs", str(output_root), {"mode": "api"})
    db.session.commit()
    return {"available": True, "run": serialize_ai_run(run, include_prompt=True), "policy": policy_payload()}


def api_skip_reason() -> str:
    if not BATTERY_AI_ENABLE_API:
        return "BATTERY_AI_ENABLE_API is not enabled; API smoke was skipped."
    if not os.getenv("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set; API smoke was skipped."
    if not openai_sdk_available():
        return "openai SDK is not installed; API smoke was skipped."
    return ""


def call_openai_responses(instructions: str, prompt: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(timeout=BATTERY_AI_TIMEOUT_SECONDS, max_retries=BATTERY_AI_MAX_RETRIES)
    response = client.responses.create(
        model=BATTERY_AI_MODEL,
        instructions=instructions,
        input=prompt,
    )
    raw = response.model_dump() if hasattr(response, "model_dump") else {}
    return {
        "output_text": getattr(response, "output_text", ""),
        "usage": raw.get("usage", {}),
        "raw": raw,
    }


def build_analysis_snapshot(output_root: Path) -> dict[str, Any]:
    files = []
    for name in SUMMARY_FILES:
        path = output_root / name
        files.append(read_summary_file(path, output_root))
    graph_counts = {
        "eis": count_artifacts(output_root / "eis"),
        "capacity": count_artifacts(output_root / "capacity"),
        "voltage_profile": count_artifacts(output_root / "voltage_profile"),
    }
    return {
        "output_root": str(output_root),
        "files": files,
        "graph_counts": graph_counts,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


def read_summary_file(path: Path, output_root: Path) -> dict[str, Any]:
    item = {
        "name": path.name,
        "relative_path": safe_relative(path, output_root),
        "exists": path.exists(),
        "size_bytes": None,
        "rows": [],
        "truncated": False,
    }
    if not path.exists() or not path.is_file():
        return item
    try:
        item["size_bytes"] = path.stat().st_size
        if path.suffix.lower() == ".csv":
            item["rows"] = read_csv_preview(path)
        elif path.suffix.lower() == ".json":
            item["rows"] = read_json_preview(path)
        else:
            item["rows"] = [path.read_text(encoding="utf-8", errors="replace")[:2000]]
    except (OSError, json.JSONDecodeError) as exc:
        item["error"] = str(exc)
    return item


def read_csv_preview(path: Path, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            if idx >= limit:
                break
            rows.append({str(key): value for key, value in row.items()})
    return rows


def read_json_preview(path: Path, *, limit: int = 25) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data[:limit]
    if isinstance(data, dict):
        return [{key: data[key] for key in list(data)[:limit]}]
    return [data]


def build_summary_prompt(snapshot: dict[str, Any]) -> str:
    body = json_dumps(snapshot)
    if len(body) > BATTERY_AI_MAX_INPUT_CHARS:
        body = body[:BATTERY_AI_MAX_INPUT_CHARS] + "\n...[truncated]"
    return (
        "Battery Lab analysis snapshot follows as JSON.\n"
        "Use it to summarize current data coverage, notable issues, and next actions.\n\n"
        f"{body}"
    )


def dry_run_output(snapshot: dict[str, Any], prompt: str) -> str:
    existing = [item["name"] for item in snapshot["files"] if item.get("exists")]
    missing = [item["name"] for item in snapshot["files"] if not item.get("exists")]
    return (
        "Dry-run only. No OpenAI API call was made.\n"
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Prompt characters: {len(prompt)}\n"
        f"Existing summary files: {', '.join(existing) if existing else 'none'}\n"
        f"Missing summary files: {', '.join(missing) if missing else 'none'}"
    )


def list_ai_runs(*, limit: int = 10) -> list[dict[str, Any]]:
    if not ensure_ai_tables():
        return []
    runs = BatteryAiRun.query.order_by(BatteryAiRun.created_at.desc(), BatteryAiRun.id.desc()).limit(limit).all()
    return [serialize_ai_run(run) for run in runs]


def get_ai_run(run_id: int) -> dict[str, Any] | None:
    if not ensure_ai_tables():
        return None
    run = db.session.get(BatteryAiRun, run_id)
    return serialize_ai_run(run, include_prompt=True, include_annotations=True) if run else None


def serialize_ai_run(run: Any, *, include_prompt: bool = False, include_annotations: bool = False) -> dict[str, Any]:
    payload = {
        "id": run.id,
        "run_type": run.run_type,
        "source_kind": run.source_kind,
        "source_ref": run.source_ref,
        "model": run.model,
        "status": run.status,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "output_text": run.output_text,
        "error_message": run.error_message,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "timeout_seconds": run.timeout_seconds,
        "max_retries": run.max_retries,
        "created_by": run.created_by,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "updated_at": iso(run.updated_at),
    }
    if include_prompt:
        payload["input_summary"] = parse_json(run.input_summary_json)
        payload["request"] = parse_json(run.request_json)
        payload["response"] = parse_json(run.response_json)
    if include_annotations:
        payload["annotations"] = [serialize_annotation(item) for item in run.annotations]
    return payload


def serialize_annotation(annotation: Any) -> dict[str, Any]:
    return {
        "id": annotation.id,
        "run_id": annotation.run_id,
        "annotation_type": annotation.annotation_type,
        "target_type": annotation.target_type,
        "target_ref": annotation.target_ref,
        "payload": parse_json(annotation.payload_json),
        "created_at": iso(annotation.created_at),
    }


def add_annotation(run_id: int, annotation_type: str, target_type: str, target_ref: str, payload: dict[str, Any]) -> None:
    db.session.add(
        BatteryAiAnnotation(
            run_id=run_id,
            annotation_type=annotation_type,
            target_type=target_type,
            target_ref=target_ref,
            payload_json=json_dumps(payload),
            created_at=datetime.utcnow(),
        )
    )


def redact_request(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    redacted["input_preview"] = str(redacted.pop("input", ""))[:2000]
    return redacted


def count_artifacts(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg"})


def safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
