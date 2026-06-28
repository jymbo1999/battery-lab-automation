#!/usr/bin/env python3
"""
WonATech WBCS .wrd battery cycling data parser.

Validated against official Capacity CSV pairs supplied by the user:
- 387_#3 GF C_9532_6T_0.1C_029.wrd vs official Capacity.csv
- 409_new no1_9532_6T_0.1C_002.wrd vs official Capacity.csv
- 411_new no2_9532_6T_2_0.1C_006.wrd vs official Capacity.csv

Observed structure:
- The beginning contains .NET BinaryFormatter-like metadata and column names.
- Repeated measurement records follow later in the file.
- Each record has a variable length because the I range string length varies.
- CHARGE Q / DISCHARGE Q are stored in Ah. Official CSV displays mAh, so multiply by 1000.
- In validated official exports, CE matched Q_Charge / Q_Discharge * 100.
  Keep conventional Q_Discharge / Q_Charge separately if needed in the app.

No third-party dependency is required for parsing.
"""
from __future__ import annotations

import csv
import math
import struct
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional


# Electrode geometry for specific-capacity normalization.
# 12 mm disc electrode -> radius r = 0.6 cm, area A = pi * r**2 ~= 1.13097 cm^2.
# Defined once here so the literal is not repeated across callers.
ELECTRODE_AREA_CM2 = math.pi * 0.6 ** 2


def mass_g_from_areal_density(areal_mass_density_mg_cm2: float | None) -> float | None:
    """Active-material mass in grams from areal mass density (mg/cm^2).

        mass_g = areal_mass_density * ELECTRODE_AREA_CM2 / 1000

    Returns ``None`` when the input is missing or non-positive so callers fall
    back to absolute-mAh-only summaries. See ``build_capacity_summary`` for how
    ``mass_g`` is consumed.
    """
    if not areal_mass_density_mg_cm2 or areal_mass_density_mg_cm2 <= 0:
        return None
    return areal_mass_density_mg_cm2 * ELECTRODE_AREA_CM2 / 1000.0


@dataclass(frozen=True)
class WrdRecord:
    offset: int
    record_len: int
    datetime_ticks: int
    datetime_iso: str
    channel: int
    test_time_s: float
    step_time_s: float
    cycle_time_s: float
    step_index: int
    total_step: int
    cycle_index: int
    run_status: int
    running_status: int
    cell_status: int
    irange_index: int
    irange: str
    voltage_v: float
    current_a: float
    charge_q_ah: float
    discharge_q_ah: float
    charge_e_wh: float
    discharge_e_wh: float
    aux_voltage_v: float
    temperature_c: float
    ocp_v: float


def dotnet_ticks_to_iso(ticks: int) -> str:
    """Convert .NET DateTime ticks to a readable ISO string when possible."""
    ticks = ticks & 0x3FFFFFFFFFFFFFFF  # tolerate DateTime kind bits
    try:
        dt = datetime(1, 1, 1) + timedelta(microseconds=ticks / 10)
        return dt.isoformat(sep=" ")
    except Exception:
        return ""


def _is_printable_ascii(s: bytes) -> bool:
    return all(32 <= c <= 126 for c in s)


def parse_one_record(buf: bytes, pos: int) -> Optional[WrdRecord]:
    n = len(buf)
    if pos + 128 > n:
        return None

    try:
        datetime_ticks = struct.unpack_from("<q", buf, pos)[0]
        channel = struct.unpack_from("<i", buf, pos + 8)[0]
        test_ticks = struct.unpack_from("<q", buf, pos + 12)[0]
        step_ticks = struct.unpack_from("<q", buf, pos + 20)[0]
        cycle_ticks = struct.unpack_from("<q", buf, pos + 28)[0]
        step_index = struct.unpack_from("<i", buf, pos + 36)[0]
        total_step = struct.unpack_from("<i", buf, pos + 40)[0]
        cycle_index = struct.unpack_from("<i", buf, pos + 44)[0]
        run_status = buf[pos + 48]
        running_status = buf[pos + 49]
        cell_status = buf[pos + 50]
        irange_index = struct.unpack_from("<i", buf, pos + 51)[0]
        strlen = buf[pos + 55]
    except struct.error:
        return None

    if not (1 <= strlen <= 30):
        return None

    s_start = pos + 56
    s_end = s_start + strlen
    vals_end = s_end + 9 * 8
    if vals_end > n:
        return None

    s_bytes = buf[s_start:s_end]
    if not _is_printable_ascii(s_bytes):
        return None

    try:
        irange = s_bytes.decode("ascii")
        vals = struct.unpack_from("<9d", buf, s_end)
    except Exception:
        return None

    voltage, current, charge_q, discharge_q, charge_e, discharge_e, aux_v, temp, ocp = vals

    # Loose sanity checks: keep them broad so other channel/range settings still parse.
    if not (0 <= channel < 10000):
        return None
    if test_ticks < 0 or step_ticks < 0 or cycle_ticks < 0:
        return None
    if step_index < 0 or total_step < 0 or cycle_index < 0:
        return None
    if not all(math.isfinite(x) for x in vals):
        return None
    if not (-1e4 < voltage < 1e4 and -1e4 < current < 1e4 and -1e4 < ocp < 1e4):
        return None
    if not (-1e6 < charge_q < 1e6 and -1e6 < discharge_q < 1e6):
        return None

    return WrdRecord(
        offset=pos,
        record_len=56 + strlen + 72,
        datetime_ticks=datetime_ticks,
        datetime_iso=dotnet_ticks_to_iso(datetime_ticks),
        channel=channel,
        test_time_s=test_ticks / 1e7,
        step_time_s=step_ticks / 1e7,
        cycle_time_s=cycle_ticks / 1e7,
        step_index=step_index,
        total_step=total_step,
        cycle_index=cycle_index,
        run_status=run_status,
        running_status=running_status,
        cell_status=cell_status,
        irange_index=irange_index,
        irange=irange,
        voltage_v=voltage,
        current_a=current,
        charge_q_ah=charge_q,
        discharge_q_ah=discharge_q,
        charge_e_wh=charge_e,
        discharge_e_wh=discharge_e,
        aux_voltage_v=aux_v,
        temperature_c=temp,
        ocp_v=ocp,
    )


def parse_record_chain(buf: bytes, start: int, max_records: Optional[int] = None) -> list[WrdRecord]:
    records: list[WrdRecord] = []
    pos = start
    while pos < len(buf):
        if max_records is not None and len(records) >= max_records:
            break
        rec = parse_one_record(buf, pos)
        if rec is None:
            break
        records.append(rec)
        pos += rec.record_len
    return records


def find_data_start(buf: bytes) -> int:
    """Find the first repeated measurement record block."""
    marker = buf.find(b"DATE TIME")
    if marker >= 0:
        search_from = max(0, marker)
        search_to = min(len(buf), marker + 20000)
    else:
        search_from = 0
        search_to = min(len(buf), 1_000_000)

    best_start: Optional[int] = None
    best_len = 0

    for pos in range(search_from, search_to):
        trial = parse_record_chain(buf, pos, max_records=20)
        if len(trial) > best_len:
            best_len = len(trial)
            best_start = pos

    if best_start is None or best_len < 5:
        raise ValueError("Could not find WRD data record start")

    return best_start


def validate_wrd_records(records: list[WrdRecord]) -> dict:
    if not records:
        return {"ok": False, "reason": "no_records"}

    cycle_values = [r.cycle_index for r in records]
    time_values = [r.test_time_s for r in records]
    checks = {
        "enough_records": len(records) >= 5,
        "cycle_nonnegative": min(cycle_values) >= 0,
        "test_time_mostly_non_decreasing": sum(
            1 for i in range(len(time_values) - 1) if time_values[i + 1] >= time_values[i]
        ) / max(1, len(time_values) - 1) >= 0.9,
        "values_finite": all(
            math.isfinite(x)
            for r in records
            for x in (
                r.voltage_v,
                r.current_a,
                r.charge_q_ah,
                r.discharge_q_ah,
                r.charge_e_wh,
                r.discharge_e_wh,
                r.ocp_v,
            )
        ),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "record_count": len(records),
        "cycle_min_export_number": min(cycle_values) + 1,
        "cycle_max_export_number": max(cycle_values) + 1,
        "test_time_start_s": records[0].test_time_s,
        "test_time_end_s": records[-1].test_time_s,
    }


def parse_wrd_bytes(buf: bytes) -> tuple[list[WrdRecord], dict]:
    start = find_data_start(buf)
    records = parse_record_chain(buf, start)
    validation = validate_wrd_records(records)
    if not validation["ok"]:
        raise ValueError(f"WRD validation failed: {validation}")
    validation["data_start_offset"] = start
    return records, validation


def parse_wrd_file(path: str | Path) -> tuple[list[WrdRecord], dict]:
    return parse_wrd_bytes(Path(path).read_bytes())


def wrd_records_to_dicts(records: Iterable[WrdRecord]) -> list[dict]:
    return [asdict(r) for r in records]


def write_wrd_raw_csv(records: Iterable[WrdRecord], path: str | Path) -> None:
    rows = wrd_records_to_dicts(records)
    if not rows:
        raise ValueError("No WRD records to write")
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_capacity_summary(records: list[WrdRecord], mass_g: float | None = None) -> list[dict[str, object]]:
    """
    Build cycle-level capacity summary.

    Confirmed mapping:
      raw cycle_index 0 -> official export Cycle 1

    Confirmed capacity unit:
      raw Ah * 1000 -> official mAh

    Specific-capacity normalization (mAh -> mAh/g):
      When ``mass_g`` (active-material mass in grams) is given, this also emits
      ``Q_*_mAh_g = Q_*_mAh / mass_g``. Without ``mass_g`` only absolute mAh is
      produced.

      Why this matters: the official "_Capacity.csv" exports we historically
      matched against were ALREADY specific capacity (mAh/g), while a raw WRD
      summary is absolute mAh. The two therefore differ purely by the constant
      ``mass_g`` factor (coulombic efficiency, being a ratio, is identical both
      ways). Empirically confirmed on cell 471: WRD/CSV ratio was a constant
      0.0098629 g across every cycle.

      ``mass_g`` is derived from the journal experiment info, not from the WRD:
        electrode area A = ELECTRODE_AREA_CM2 = pi * r**2, r = 0.6 cm -> ~1.13097 cm^2
        mass_g = mass_g_from_areal_density(areal_mass_density mg/cm^2)
               = areal_mass_density * ELECTRODE_AREA_CM2 / 1000
      (cell 471: areal 8.72 mg/cm^2 * 1.13097 / 1000 = 0.0098629 g, matches above.)
      See battery_lab.ui.build_analysis_artifacts (the build_capacity_graphs
      rebuild path) for where areal_mass_density -> mass_g is wired once the
      experiment info is known.
    """
    grouped: dict[int, list[WrdRecord]] = {}
    for r in records:
        grouped.setdefault(r.cycle_index, []).append(r)

    rows: list[dict[str, object]] = []
    for cycle_index in sorted(grouped):
        rs = grouped[cycle_index]
        q_ch_mAh = max(r.charge_q_ah for r in rs) * 1000
        q_dis_mAh = max(r.discharge_q_ah for r in rs) * 1000

        ce_export = (q_ch_mAh / q_dis_mAh * 100) if q_dis_mAh else 0.0
        ce_conventional = (q_dis_mAh / q_ch_mAh * 100) if q_ch_mAh else 0.0

        row: dict[str, object] = {
            "Cycle": cycle_index + 1,
            "raw_cycle_index": cycle_index,
            "Q_Charge_mAh": q_ch_mAh,
            "Q_Discharge_mAh": q_dis_mAh,
            "CE_export_Qch_over_Qdis_percent": ce_export,
            "CE_conventional_Qdis_over_Qch_percent": ce_conventional,
            "n_points": len(rs),
            "first_test_time_s": min(r.test_time_s for r in rs),
            "last_test_time_s": max(r.test_time_s for r in rs),
            "first_total_step": min(r.total_step for r in rs),
            "last_total_step": max(r.total_step for r in rs),
            "last_voltage_v": rs[-1].voltage_v,
            "last_current_a": rs[-1].current_a,
        }
        if mass_g and mass_g > 0:
            row["Q_Charge_mAh_g"] = q_ch_mAh / mass_g
            row["Q_Discharge_mAh_g"] = q_dis_mAh / mass_g
        rows.append(row)

    return rows


def write_capacity_summary_csv(rows: list[dict[str, object]], path: str | Path) -> None:
    if not rows:
        raise ValueError("No capacity summary rows to write")
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
