from __future__ import annotations

import json
import re
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
    final_rows = final_review_rows(matches, overrides)
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
        "final_rows": final_rows,
        "risky_count": len(risky),
        "final_count": len(final_rows),
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


def save_match_review_actions(
    kind: str,
    source_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    selected_candidates: list[dict[str, Any]],
    direct_matches: list[dict[str, Any]],
    delete_files: list[dict[str, Any]],
    condition_sheet: str | None = "JYJ",
) -> dict[str, Any]:
    payload = build_match_payload(kind, source_root, condition_workbook, override_path, condition_sheet=condition_sheet)
    valid_rows = {
        (row["file"], row["condition_key"]): row
        for row in payload["rows"]
        if row.get("file") and row.get("condition_key")
    }
    known_files = {row["relative_path"] for row in payload.get("final_rows", []) if row.get("relative_path")}
    overrides = load_match_overrides(override_path)
    saved = 0

    seen_candidate_files: set[str] = set()
    for selection in selected_candidates:
        file_key = str(selection.get("file") or selection.get("relative_path") or "").strip()
        condition_key = str(selection.get("condition_key") or "").strip()
        if not file_key or not condition_key:
            continue
        if file_key in seen_candidate_files:
            raise ValueError(f"Only one candidate can be selected for each file: {file_key}")
        row = valid_rows.get((file_key, condition_key))
        if row is None:
            raise ValueError(f"Unknown match candidate: {file_key} -> {condition_key}")
        overrides[file_key] = override_from_candidate_row(row)
        seen_candidate_files.add(file_key)
        saved += 1

    conditions = read_conditions(condition_workbook, sheet_name=condition_sheet) if condition_workbook.exists() else {}
    condition_rows = condition_index_by_row_number(conditions)
    for item in direct_matches:
        file_key = str(item.get("file") or item.get("relative_path") or "").strip()
        raw_row_number = str(item.get("journal_row") or item.get("row_number") or "").strip()
        if not file_key or not raw_row_number:
            continue
        if known_files and file_key not in known_files:
            raise ValueError(f"Unknown source file: {file_key}")
        try:
            row_number = int(raw_row_number)
        except ValueError as exc:
            raise ValueError(f"Journal row must be a number for {file_key}: {raw_row_number}") from exc
        condition_key, condition = condition_rows.get(row_number, ("", {}))
        if not condition_key:
            raise ValueError(f"Unknown journal row number: {row_number}")
        overrides[file_key] = {
            "condition_key": condition_key,
            "journal_row": row_number,
            "sample": condition.get("sample") or condition_key,
            "date": condition.get("date") or "",
            "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "selection_source": "review_direct_row",
        }
        saved += 1

    for item in delete_files:
        file_key = str(item.get("file") or item.get("relative_path") or "").strip()
        if not file_key:
            continue
        if known_files and file_key not in known_files:
            raise ValueError(f"Unknown source file: {file_key}")
        overrides[file_key] = {
            "action": "delete_file",
            "delete_candidate": True,
            "reason": str(item.get("reason") or "review_marked_delete"),
            "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "selection_source": "review_stage_2",
        }
        saved += 1

    save_match_overrides(override_path, overrides)
    next_payload = build_match_payload(kind, source_root, condition_workbook, override_path, condition_sheet=condition_sheet)
    next_payload["saved_count"] = saved
    return next_payload


def override_from_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition_key": row.get("condition_key"),
        "journal_row": row.get("journal_row"),
        "sample": row.get("sample"),
        "date": row.get("date"),
        "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "selection_source": "review_candidate",
    }


def condition_index_by_row_number(conditions: dict[str, dict[str, Any]]) -> dict[int, tuple[str, dict[str, Any]]]:
    rows: dict[int, tuple[str, dict[str, Any]]] = {}
    for key, condition in conditions.items():
        row_number = condition.get("_source_row_number")
        if isinstance(row_number, int):
            rows[row_number] = (key, condition)
    return rows


def final_review_rows(matches: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in matches:
        file_key = str(row.get("relative_path") or "")
        override = overrides.get(file_key) or {}
        action = str(override.get("action") or "")
        override_condition = str(override.get("condition_key") or "")
        rows.append(
            {
                "relative_path": file_key,
                "source_name": row.get("source_name") or Path(file_key).name,
                "status": "delete_candidate" if action == "delete_file" else row.get("status", ""),
                "condition_key": override_condition or row.get("condition_key", ""),
                "journal_row": override.get("journal_row") or row.get("journal_row") or "",
                "sample": override.get("sample") or row.get("condition_sample") or "",
                "date": override.get("date") or row.get("condition_date") or "",
                "score": row.get("score", ""),
                "margin": row.get("margin", ""),
                "override_action": action,
                "override_source": override.get("selection_source") or "",
                "delete_candidate": action == "delete_file",
                "reason": row.get("reason", ""),
            }
        )
    return rows


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


# --- Matching verification (additive, read-only over scoped matching) ---

RISKY_REVIEW_STATUSES = {"unmatched", "ambiguous", "blocked", "review", "manual"}


_PATH_DATE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _path_date(relpath: str) -> str:
    """Best-effort YYMMDD pulled from a dated folder in the path (e.g. '260501/...')."""
    match = _PATH_DATE_RE.search(str(relpath or ""))
    return match.group(1) if match else ""


def _verification_row(kind: str, m: dict[str, Any], conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cond_key = str(m.get("condition_key") or "")
    cond = conditions.get(cond_key, {}) if cond_key else {}
    journal_row = m.get("journal_row") or cond.get("_source_row_number") or ""
    row_prefix = m.get("row_prefix")
    if kind == "capacity":
        try:
            row_exact = row_prefix is not None and journal_row not in ("", None) and int(row_prefix) == int(journal_row)
        except (TypeError, ValueError):
            row_exact = False
    else:
        row_exact = None
    reason = explain_capacity_match_status(m) if kind == "capacity" else explain_eis_match_status(m)
    rel = str(m.get("relative_path") or "")
    return {
        "relative_path": rel,
        "source_name": m.get("source_name") or Path(rel).name,
        "analysis_type": kind,
        "status": str(m.get("status") or ""),
        "in_scope": bool(cond_key and cond_key in conditions),
        "is_time_series": bool(m.get("is_time_series")),
        "file_date": _path_date(rel),
        "journal_row": journal_row,
        "condition_key": cond_key,
        "sample": m.get("condition_sample") or cond.get("sample") or "",
        "date": m.get("condition_date") or cond.get("date") or "",
        "row_exact": row_exact,
        "overlap_tokens": m.get("overlap_tokens", ""),
        "conflict_tokens": m.get("conflict_tokens", ""),
        "date_delta_days": m.get("date_delta_days"),
        "score": m.get("score", ""),
        "margin": m.get("margin", ""),
        "reason": reason,
        "candidate_options": parse_candidate_options(m),
        "override_source": "",
    }


def verification_payload(
    kind: str,
    source_root: Path,
    condition_workbook: Path,
    override_path: Path,
    *,
    condition_sheet: str | None = "JYJ",
) -> dict[str, Any]:
    """Read-only verification view over IN-SCOPE matching.

    Matches source files against only the in-scope journal rows (the 5-rule
    FILTER_RULES subset), then returns every matched file with full evidence
    columns (verified included), the in-scope rows with no file (orphans), and
    1:1 invariant signals (ambiguous / duplicates / unmatched count). Does NOT
    rename files or mutate the journal; additive to the existing review flow.
    """
    from . import scope

    if kind == "eis":
        source_paths = collect_source_files(source_root, EIS_SUFFIXES)
    elif kind == "capacity":
        source_paths = collect_capacity_summary_sources(source_root)
    else:
        raise ValueError(f"Unsupported verification kind: {kind}")

    overrides = load_match_overrides(override_path)
    conditions = read_conditions(condition_workbook, sheet_name=condition_sheet) if condition_workbook.exists() else {}
    in_scope_conditions = scope.filter_in_scope(conditions)
    report = build_report(kind, source_paths, in_scope_conditions, source_root, overrides) if in_scope_conditions else None
    matches = [asdict(row) for row in report.matches] if report else []

    rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    unmatched_files: list[str] = []
    used: dict[Any, list[str]] = {}
    for m in matches:
        vrow = _verification_row(kind, m, in_scope_conditions)
        override = overrides.get(vrow["relative_path"]) or {}
        vrow["override_source"] = str(override.get("selection_source") or ("manual" if override else ""))
        # Time-series EIS (_hr) is held to lower priority: cluster-comparison data is
        # what urgently needs exact row matching, so _hr files are set aside here.
        if vrow.get("is_time_series"):
            deferred_rows.append(vrow)
            continue
        if vrow["status"] == "unmatched" or not vrow["condition_key"]:
            unmatched_files.append(vrow["relative_path"])
            continue
        rows.append(vrow)
        if vrow["journal_row"] not in ("", None):
            used.setdefault(vrow["journal_row"], []).append(vrow["relative_path"])

    matched_keys = {row["condition_key"] for row in rows}
    orphans = [
        {
            "condition_key": key,
            "journal_row": cond.get("_source_row_number") or "",
            "sample": cond.get("sample") or key,
            "date": cond.get("date") or "",
        }
        for key, cond in in_scope_conditions.items()
        if key not in matched_keys
    ]
    ambiguous = [row["relative_path"] for row in rows if row["status"] == "ambiguous"]
    duplicates = [{"journal_row": jr, "files": files} for jr, files in used.items() if len(files) > 1]
    needs_review = [row["relative_path"] for row in rows if row["status"] in RISKY_REVIEW_STATUSES]

    status_order = {"unmatched": 0, "ambiguous": 1, "blocked": 2, "review": 3, "manual": 4}
    rows.sort(key=lambda row: (status_order.get(row["status"], 9), str(row["journal_row"])))
    deferred_rows.sort(key=lambda row: (status_order.get(row["status"], 9), str(row["journal_row"])))

    return {
        "kind": kind,
        "condition_sheet": condition_sheet,
        "source_count": len(source_paths),
        "rows": rows,
        "deferred_rows": deferred_rows,
        "orphans": orphans,
        "summary": {
            "in_scope_rows": len(in_scope_conditions),
            "matched_files": len(rows),
            "needs_review": len(needs_review),
            "orphan_rows": len(orphans),
            "unmatched_files": len(unmatched_files),
            "ambiguous_files": len(ambiguous),
            "duplicate_groups": len(duplicates),
            "deferred_time_series": len(deferred_rows),
        },
        "invariant": {
            "ambiguous": ambiguous,
            "duplicates": duplicates,
            "unmatched_count": len(unmatched_files),
        },
        "report_ready": report is not None,
    }


def apply_checklist_answers(
    answers: dict[str, Any],
    condition_workbook: Path,
    override_path: Path,
    *,
    condition_sheet: str | None = "JYJ",
) -> dict[str, Any]:
    """Merge the research lead's filled checklist back into overrides.json.

    `answers` is the exported blob: {"answers": {file: {"choice": <condition_key|__delete__|__skip__>, "memo": ...}}}.
    A chosen condition_key writes the same manual-override shape the review tab uses, so
    matching picks it up immediately (and the render cache invalidates via override mtime).
    """
    data = answers.get("answers", answers) if isinstance(answers, dict) else {}
    conditions = read_conditions(condition_workbook, sheet_name=condition_sheet) if condition_workbook.exists() else {}
    overrides = load_match_overrides(override_path)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    applied = deleted = skipped = unknown = 0
    for file_key, ans in (data or {}).items():
        choice = str((ans or {}).get("choice") or "").strip()
        memo = str((ans or {}).get("memo") or "")
        if not choice or choice == "__skip__":
            skipped += 1
            continue
        if choice == "__delete__":
            overrides[str(file_key)] = {
                "action": "delete_file",
                "delete_candidate": True,
                "reason": memo or "checklist_delete",
                "selection_source": "checklist",
                "selected_at": now,
            }
            deleted += 1
            continue
        condition = conditions.get(choice)
        if condition is None:
            unknown += 1
            continue
        overrides[str(file_key)] = {
            "condition_key": choice,
            "journal_row": condition.get("_source_row_number"),
            "sample": condition.get("sample") or choice,
            "date": condition.get("date") or "",
            "memo": memo,
            "selection_source": "checklist",
            "selected_at": now,
        }
        applied += 1
    save_match_overrides(override_path, overrides)
    return {"applied": applied, "deleted": deleted, "skipped": skipped, "unknown": unknown, "override_count": len(overrides)}
