from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capacity_matching import build_capacity_match_report
from .conditions import read_conditions
from .eis_matching import build_eis_match_report


EIS_SUFFIXES = {".seo", ".sde", ".csv", ".xlsx", ".xls"}
CAPACITY_SUFFIXES = {".csv", ".wrd", ".xlsx", ".xls"}
RISKY_EIS_STATUSES = {"unmatched", "ambiguous", "blocked", "manual"}
RISKY_CAPACITY_STATUSES = {"unmatched", "ambiguous", "blocked", "manual", "review"}


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


def collect_capacity_summary_sources(root: Path) -> list[Path]:
    return [path for path in collect_source_files(root, {".csv", ".xlsx", ".xls"}) if is_capacity_summary_source(path)]


def is_capacity_summary_source(path: Path) -> bool:
    name = path.name.lower()
    if "diff_anal" in name or name.endswith("_cycle.csv"):
        return False
    return "capacity" in name


def load_match_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_match_overrides(path: Path, overrides: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_match_payload(
    kind: str,
    source_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    condition_sheet: str | None = "JYJ",
    limit: int = 300,
) -> dict[str, Any]:
    if kind == "eis":
        source_paths = collect_source_files(source_root, EIS_SUFFIXES)
    elif kind == "capacity":
        source_paths = collect_capacity_summary_sources(source_root)
    else:
        raise ValueError(f"Unsupported match kind: {kind}")

    overrides = load_match_overrides(override_path)
    conditions = read_conditions(condition_workbook, sheet_name=condition_sheet) if condition_workbook.exists() else {}
    report = build_report(kind, source_paths, conditions, source_root, overrides) if conditions else None
    matches = [asdict(row) for row in report.matches] if report else []
    risky_statuses = RISKY_EIS_STATUSES if kind == "eis" else RISKY_CAPACITY_STATUSES
    risky = [row for row in matches if row.get("status") in risky_statuses][:limit]
    editor_rows = candidate_editor_rows(kind, risky, overrides)
    return {
        "kind": kind,
        "condition_workbook": str(condition_workbook),
        "condition_workbook_exists": condition_workbook.exists(),
        "condition_sheet": condition_sheet,
        "source_root": str(source_root),
        "source_root_exists": source_root.exists(),
        "override_path": str(override_path),
        "override_count": len(overrides),
        "source_count": len(source_paths),
        "condition_count": len(conditions),
        "status_counts": report.status_counts if report else {},
        "rows": editor_rows,
        "risky_count": len(risky),
        "report_ready": report is not None,
    }


def build_report(
    kind: str,
    source_paths: list[Path],
    conditions: dict[str, dict[str, Any]],
    source_root: Path,
    overrides: dict[str, dict[str, Any]],
) -> Any:
    if kind == "eis":
        return build_eis_match_report(source_paths, conditions, source_root, overrides)
    if kind == "capacity":
        return build_capacity_match_report(source_paths, conditions, source_root, overrides)
    raise ValueError(f"Unsupported match kind: {kind}")


def save_match_selections(
    kind: str,
    source_root: Path,
    condition_workbook: Path,
    override_path: Path,
    selections: list[dict[str, Any]],
    *,
    condition_sheet: str | None = "JYJ",
) -> dict[str, Any]:
    payload = build_match_payload(kind, source_root, condition_workbook, override_path, condition_sheet=condition_sheet)
    valid_rows = {
        (row["file"], row["condition_key"]): row
        for row in payload["rows"]
        if row.get("file") and row.get("condition_key")
    }
    known_files = {row["file"] for row in payload["rows"] if row.get("file")}
    overrides = load_match_overrides(override_path)
    selected_by_file: dict[str, dict[str, Any]] = {}
    duplicates = set()

    for selection in selections:
        file_key = str(selection.get("file") or selection.get("relative_path") or "").strip()
        condition_key = str(selection.get("condition_key") or "").strip()
        if not file_key or not condition_key:
            continue
        if file_key in selected_by_file:
            duplicates.add(file_key)
        row = valid_rows.get((file_key, condition_key))
        if row is None:
            raise ValueError(f"Unknown match candidate: {file_key} -> {condition_key}")
        selected_by_file[file_key] = row

    if duplicates:
        raise ValueError(f"Only one candidate can be selected for each file: {', '.join(sorted(duplicates)[:3])}")

    for file_key, row in selected_by_file.items():
        overrides[file_key] = {
            "condition_key": row.get("condition_key"),
            "journal_row": row.get("journal_row"),
            "sample": row.get("sample"),
            "date": row.get("date"),
            "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }

    for file_key in known_files:
        if file_key not in selected_by_file and any(str(selection.get("file") or selection.get("relative_path") or "") == file_key for selection in selections):
            overrides.pop(file_key, None)

    save_match_overrides(override_path, overrides)
    next_payload = build_match_payload(kind, source_root, condition_workbook, override_path, condition_sheet=condition_sheet)
    next_payload["saved_count"] = len(selected_by_file)
    return next_payload


def candidate_editor_rows(kind: str, rows: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        file_key = str(row.get("relative_path") or "")
        candidates = parse_candidate_options(row)
        if not candidates and row.get("condition_key"):
            candidates = [
                {
                    "condition_key": row.get("condition_key"),
                    "journal_row": row.get("journal_row"),
                    "sample": row.get("condition_sample"),
                    "date": row.get("condition_date"),
                    "date_delta_days": row.get("date_delta_days"),
                    "score": row.get("score"),
                    "overlap_tokens": row.get("overlap_tokens"),
                }
            ]
        if not candidates:
            output.append(candidate_editor_row(kind, row, file_key, {}, overrides, show_file=True))
            continue
        for idx, candidate in enumerate(candidates):
            output.append(candidate_editor_row(kind, row, file_key, candidate, overrides, show_file=idx == 0))
    return output


def candidate_editor_row(
    kind: str,
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
    reason = explain_capacity_match_status(row) if kind == "capacity" else explain_eis_match_status(row)
    return {
        "file": file_key,
        "file_label": file_key if show_file else "",
        "row_prefix": row.get("row_prefix") if kind == "capacity" and show_file else "",
        "selected": bool(selected_condition and selected_condition == condition_key),
        "condition_key": condition_key,
        "journal_row": journal_row,
        "sample": sample,
        "date": date,
        "date_delta": date_delta_text,
        "overlap_tokens": candidate.get("overlap_tokens") or row.get("overlap_tokens", ""),
        "conflict_tokens": row.get("conflict_tokens", ""),
        "score": candidate.get("score") or row.get("score", ""),
        "margin": row.get("margin", ""),
        "status": row.get("status", ""),
        "reason": reason,
    }


def parse_candidate_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        options = json.loads(str(row.get("candidate_options") or "[]"))
    except json.JSONDecodeError:
        return []
    return options if isinstance(options, list) else []


def explain_eis_match_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    margin = int(row.get("margin") or 0)
    score = int(row.get("score") or 0)
    date_delta = row.get("date_delta_days")
    overlap = [token for token in str(row.get("overlap_tokens") or "").split(";") if token]
    conflict = [token for token in str(row.get("conflict_tokens") or "").split(";") if token]
    if status == "manual":
        return "사용자가 수동 확정한 매칭입니다."
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
