from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wonatech_parsers.wrd import build_capacity_summary, parse_wrd_file

from .file_io import ANALYSIS_CAPACITY, parse_file


@dataclass(frozen=True)
class CapacityCsvWrdAuditRow:
    wrd_path: str
    csv_path: str
    status: str
    reason: str
    common_cycles: int = 0
    wrd_cycles: int = 0
    csv_cycles: int = 0
    max_charge_abs_diff: float | None = None
    max_discharge_abs_diff: float | None = None
    max_ce_abs_diff: float | None = None
    max_relative_diff: float | None = None


def audit_capacity_csv_wrd_pairs(
    capacity_root: Path,
    output_root: Path,
    *,
    abs_tol: float = 1e-3,
    rel_tol: float = 0.005,
) -> dict[str, Any]:
    rows = audit_capacity_csv_wrd_rows(capacity_root, abs_tol=abs_tol, rel_tol=rel_tol)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    report_dir = output_root / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = report_dir / f"capacity_csv_wrd_audit_{stamp}.csv"
    json_path = report_dir / f"capacity_csv_wrd_audit_{stamp}.json"
    write_audit_csv(rows, csv_path)
    payload = {
        "ok": True,
        "capacity_root": str(capacity_root),
        "output_root": str(output_root),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "counts": counts,
        "row_count": len(rows),
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "rows": [asdict(row) for row in rows],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def audit_capacity_csv_wrd_rows(capacity_root: Path, *, abs_tol: float, rel_tol: float) -> list[CapacityCsvWrdAuditRow]:
    wrd_paths = sorted(path for path in capacity_root.rglob("*.wrd") if path.is_file() and not path.name.startswith("."))
    csv_paths = sorted(path for path in capacity_root.rglob("*.csv") if is_official_capacity_csv(path))
    csv_by_local_key: dict[tuple[Path, str], list[Path]] = {}
    csv_by_key: dict[str, list[Path]] = {}
    for path in csv_paths:
        key = pair_key(path)
        csv_by_local_key.setdefault((path.parent, key), []).append(path)
        csv_by_key.setdefault(key, []).append(path)

    rows: list[CapacityCsvWrdAuditRow] = []
    paired_csvs: set[Path] = set()
    for wrd_path in wrd_paths:
        key = pair_key(wrd_path)
        candidates = csv_by_local_key.get((wrd_path.parent, key)) or csv_by_key.get(key) or []
        csv_path = candidates[0] if candidates else None
        if csv_path is None:
            rows.append(CapacityCsvWrdAuditRow(str(wrd_path), "", "keep", "matching official capacity CSV was not found"))
            continue
        paired_csvs.add(csv_path)
        rows.append(compare_wrd_and_csv(wrd_path, csv_path, abs_tol=abs_tol, rel_tol=rel_tol))

    for csv_path in csv_paths:
        if csv_path not in paired_csvs:
            rows.append(CapacityCsvWrdAuditRow("", str(csv_path), "keep", "matching WRD file was not found"))
    return rows


def compare_wrd_and_csv(wrd_path: Path, csv_path: Path, *, abs_tol: float, rel_tol: float) -> CapacityCsvWrdAuditRow:
    try:
        wrd_rows = wrd_summary_rows(wrd_path)
        csv_rows = csv_summary_rows(csv_path)
    except Exception as exc:
        return CapacityCsvWrdAuditRow(str(wrd_path), str(csv_path), "manual_review", f"parse failed: {exc}")

    common_cycles = sorted(set(wrd_rows) & set(csv_rows))
    if not common_cycles:
        return CapacityCsvWrdAuditRow(
            str(wrd_path),
            str(csv_path),
            "manual_review",
            "no common cycle numbers",
            wrd_cycles=len(wrd_rows),
            csv_cycles=len(csv_rows),
        )

    diffs: dict[str, list[float]] = {"charge": [], "discharge": [], "ce": []}
    rel_diffs: list[float] = []
    for cycle in common_cycles:
        wrd = wrd_rows[cycle]
        official = csv_rows[cycle]
        for field in ("charge", "discharge", "ce"):
            left = wrd.get(field)
            right = official.get(field)
            if left is None or right is None:
                continue
            diff = abs(left - right)
            diffs[field].append(diff)
            denom = max(abs(left), abs(right), abs_tol)
            rel_diffs.append(diff / denom)

    max_charge = max(diffs["charge"], default=None)
    max_discharge = max(diffs["discharge"], default=None)
    max_ce = max(diffs["ce"], default=None)
    max_rel = max(rel_diffs, default=None)
    numeric_diffs = [value for values in diffs.values() for value in values]
    if not numeric_diffs:
        status = "manual_review"
        reason = "no comparable numeric capacity or CE columns"
    elif all(value <= abs_tol for value in numeric_diffs) or (max_rel is not None and max_rel <= rel_tol):
        status = "archive_candidate" if len(wrd_rows) == len(csv_rows) else "manual_review"
        reason = "official CSV matches WRD-generated summary" if status == "archive_candidate" else "values match but cycle counts differ"
    else:
        status = "keep"
        reason = "official CSV differs from WRD-generated summary"

    return CapacityCsvWrdAuditRow(
        str(wrd_path),
        str(csv_path),
        status,
        reason,
        common_cycles=len(common_cycles),
        wrd_cycles=len(wrd_rows),
        csv_cycles=len(csv_rows),
        max_charge_abs_diff=max_charge,
        max_discharge_abs_diff=max_discharge,
        max_ce_abs_diff=max_ce,
        max_relative_diff=max_rel,
    )


def wrd_summary_rows(path: Path) -> dict[int, dict[str, float | None]]:
    records, _ = parse_wrd_file(path)
    rows = {}
    for row in build_capacity_summary(records):
        cycle = int(float(row.get("Cycle") or 0))
        rows[cycle] = {
            "charge": maybe_float(row.get("Q_Charge_mAh")),
            "discharge": maybe_float(row.get("Q_Discharge_mAh")),
            "ce": maybe_float(row.get("CE_export_Qch_over_Qdis_percent")),
        }
    return rows


def csv_summary_rows(path: Path) -> dict[int, dict[str, float | None]]:
    dataset = parse_file(path)
    if dataset.meta.analysis_type != ANALYSIS_CAPACITY:
        raise ValueError(f"not a capacity CSV: {path.name}")
    rows = {}
    for row in dataset.rows:
        cycle = maybe_float(row.get("cycle"))
        if cycle is None:
            continue
        rows[int(cycle)] = {
            "charge": maybe_float(row.get("charge_capacity")),
            "discharge": maybe_float(row.get("discharge_capacity")),
            "ce": row_ce(row),
        }
    return rows


def row_ce(row: dict[str, Any]) -> float | None:
    for key, value in row.items():
        lowered = str(key).lower()
        if "ce_export" in lowered or "coulomb" in lowered or lowered in {"ce", "efficiency"}:
            number = maybe_float(value)
            if number is not None:
                return number
    charge = maybe_float(row.get("charge_capacity"))
    discharge = maybe_float(row.get("discharge_capacity"))
    return charge / discharge * 100 if charge is not None and discharge else None


def maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def is_official_capacity_csv(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".csv"):
        return False
    if "diff_anal" in name or name.endswith("_cycle.csv") or "raw_timeseries" in name:
        return False
    if name.endswith("_capacity_summary.csv"):
        return False
    return "capacity" in name


def pair_key(path: Path) -> str:
    stem = path.stem.lower()
    stem = re.sub(r"(?i)(?:_?capacity|_?capacity_summary)$", "", stem)
    stem = re.sub(r"[^0-9a-z가-힣.]+", "", stem)
    return stem


def write_audit_csv(rows: list[CapacityCsvWrdAuditRow], path: Path) -> None:
    fieldnames = list(CapacityCsvWrdAuditRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
