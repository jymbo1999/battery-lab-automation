#!/usr/bin/env python3
"""
WonATech/ZIVE .SEO/.SDE EIS binary parser.

Validated against exported XLSX pairs supplied by the user:
- pc 91_1_02.SEO vs pc91_1.xlsx
- pc 91_2_02.SEO vs pc91_2.xlsx
- 1.5act 3T_4hr_01.SEO vs 1.5act 3T_4hr.xlsx

Observed EIS record layout:
    record_stride = 112 bytes
    frequency_hz  = little-endian float32 at record_start + 0
    zreal_ohm     = little-endian float32 at record_start + 28
    zimag_ohm     = little-endian float32 at record_start + 32

Important correction from validation:
    An earlier exploratory parser used frequency at +92. XLSX comparison showed that
    +92 is the next record's frequency. The verified layout is frequency +0.

No third-party dependency is required for parsing.
"""
from __future__ import annotations

import csv
import math
import statistics
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional

STRIDE = 112
FREQ_OFF = 0
ZREAL_OFF = 28
ZIMAG_OFF = 32


@dataclass(frozen=True)
class EISRecord:
    point: int
    offset: int
    frequency_hz: float
    zreal_ohm: float
    zimag_ohm: float
    minus_zimag_ohm: float
    zmod_ohm: float
    phase_deg: float


@dataclass(frozen=True)
class EISParseResult:
    records: List[EISRecord]
    start_offset: int
    stride: int
    layout: dict
    validation: dict


def _f32(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def _candidate_rows(buf: bytes, start: int, stride: int = STRIDE) -> list[tuple[int, float, float, float]]:
    rows: list[tuple[int, float, float, float]] = []
    n = len(buf)
    max_i = (n - start - max(FREQ_OFF, ZREAL_OFF, ZIMAG_OFF) - 4) // stride + 1

    for i in range(max_i):
        off = start + i * stride
        try:
            freq = _f32(buf, off + FREQ_OFF)
            zre = _f32(buf, off + ZREAL_OFF)
            zim = _f32(buf, off + ZIMAG_OFF)
        except struct.error:
            break

        # Stop at the first value that cannot be part of the EIS sweep.
        if not (math.isfinite(freq) and 0 < freq < 1e9):
            break
        if not (math.isfinite(zre) and math.isfinite(zim)):
            break
        if abs(zre) > 1e7 or abs(zim) > 1e7:
            break
        rows.append((off, freq, zre, zim))

    return rows


def _score_rows(rows: list[tuple[int, float, float, float]]) -> Optional[float]:
    if len(rows) < 8:
        return None

    freqs = [r[1] for r in rows]
    if min(freqs) <= 0:
        return None

    ratios = [freqs[i + 1] / freqs[i] for i in range(len(freqs) - 1)]
    if not ratios:
        return None

    # EIS sweep is expected to be mostly descending and approximately log-spaced.
    desc_frac = sum(1 for r in ratios if 0.3 < r < 0.98) / len(ratios)
    dynamic_range = max(freqs) / min(freqs)
    median_ratio = statistics.median(ratios)
    zero_frac = sum(1 for r in rows if abs(r[2]) + abs(r[3]) < 1e-10) / len(rows)

    target_ratio = 10 ** -0.1  # 10 points per decade ≈ 0.7943
    ratio_score = 0.0
    if median_ratio > 0:
        ratio_score = 1 / (1 + abs(math.log10(median_ratio) - math.log10(target_ratio)) * 10)

    if desc_frac < 0.75 or dynamic_range < 10 or zero_frac >= 0.2:
        return None

    return len(rows) * 10 + math.log10(dynamic_range) * 20 + desc_frac * 50 + ratio_score * 30 - zero_frac * 50


def _validate_records(rows: list[tuple[int, float, float, float]]) -> dict:
    if not rows:
        return {"ok": False, "reason": "no_records"}

    freqs = [r[1] for r in rows]
    zreal = [r[2] for r in rows]
    zimag = [r[3] for r in rows]
    ratios = [freqs[i + 1] / freqs[i] for i in range(len(freqs) - 1)]

    freq_descending_fraction = (
        sum(1 for i in range(len(freqs) - 1) if freqs[i] > freqs[i + 1]) / max(1, len(freqs) - 1)
    )
    median_ratio = statistics.median(ratios) if ratios else None
    dynamic_range = max(freqs) / min(freqs) if min(freqs) > 0 else 0

    checks = {
        "enough_points": len(rows) >= 8,
        "freq_positive": all(f > 0 for f in freqs),
        "freq_mostly_descending": freq_descending_fraction >= 0.75,
        "freq_dynamic_range_gt_10": dynamic_range > 10,
        "z_finite": all(math.isfinite(x) for x in zreal + zimag),
    }

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "point_count": len(rows),
        "frequency_max_hz": max(freqs),
        "frequency_min_hz": min(freqs),
        "frequency_dynamic_range": dynamic_range,
        "frequency_descending_fraction": freq_descending_fraction,
        "median_frequency_ratio": median_ratio,
    }


def _candidate_start_offsets(buf: bytes, max_start: int) -> list[int]:
    starts: list[int] = []
    seen: set[int] = set()
    known_first_frequencies = (1_000_000.0, 100_000.0, 10_000.0)

    for frequency in known_first_frequencies:
        pattern = struct.pack("<f", frequency)
        pos = buf.find(pattern)
        while pos != -1:
            if pos <= max_start and pos not in seen:
                starts.append(pos)
                seen.add(pos)
            pos = buf.find(pattern, pos + 1)

    probe_limit = max_start - STRIDE
    for start in range(0, max(0, probe_limit), 4):
        for aligned_start in (start, start + 2):
            if aligned_start > probe_limit or aligned_start in seen:
                continue
            try:
                freq0 = _f32(buf, aligned_start + FREQ_OFF)
                freq1 = _f32(buf, aligned_start + STRIDE + FREQ_OFF)
                zre0 = _f32(buf, aligned_start + ZREAL_OFF)
                zim0 = _f32(buf, aligned_start + ZIMAG_OFF)
            except struct.error:
                continue
            if not (math.isfinite(freq0) and math.isfinite(freq1) and math.isfinite(zre0) and math.isfinite(zim0)):
                continue
            if not (0 < freq0 < 1e9 and 0 < freq1 < freq0):
                continue
            ratio = freq1 / freq0
            if 0.3 < ratio < 0.98 and abs(zre0) < 1e7 and abs(zim0) < 1e7:
                starts.append(aligned_start)
                seen.add(aligned_start)

    return starts


def parse_eis_bytes(buf: bytes) -> EISParseResult:
    """Parse a WonATech/ZIVE .SEO or .SDE EIS binary file from bytes."""
    n = len(buf)
    max_start = n - 7 * STRIDE - max(FREQ_OFF, ZREAL_OFF, ZIMAG_OFF) - 4
    if max_start <= 0:
        raise ValueError("File too small for EIS records")

    candidates: list[tuple[float, int, list[tuple[int, float, float, float]]]] = []
    for start in _candidate_start_offsets(buf, max_start):
        rows = _candidate_rows(buf, start)
        score = _score_rows(rows)
        if score is not None:
            candidates.append((score, start, rows))

    if not candidates:
        raise ValueError("Could not find a valid EIS sweep block")

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, start, rows = candidates[0]

    records: list[EISRecord] = []
    for point, (off, freq, zre, zim) in enumerate(rows, start=1):
        records.append(
            EISRecord(
                point=point,
                offset=off,
                frequency_hz=freq,
                zreal_ohm=zre,
                zimag_ohm=zim,
                minus_zimag_ohm=-zim,
                zmod_ohm=math.hypot(zre, zim),
                phase_deg=math.degrees(math.atan2(zim, zre)),
            )
        )

    validation = _validate_records(rows)
    if not validation["ok"]:
        raise ValueError(f"EIS validation failed: {validation}")

    return EISParseResult(
        records=records,
        start_offset=start,
        stride=STRIDE,
        layout={
            "frequency_hz": {"offset": FREQ_OFF, "type": "<f4"},
            "zreal_ohm": {"offset": ZREAL_OFF, "type": "<f4"},
            "zimag_ohm": {"offset": ZIMAG_OFF, "type": "<f4"},
        },
        validation=validation,
    )


def parse_eis_file(path: str | Path) -> EISParseResult:
    return parse_eis_bytes(Path(path).read_bytes())


def eis_records_to_dicts(records: Iterable[EISRecord]) -> list[dict]:
    return [asdict(r) for r in records]


def write_eis_csv(records: Iterable[EISRecord], output_path: str | Path) -> None:
    rows = eis_records_to_dicts(records)
    if not rows:
        raise ValueError("No EIS records to write")
    fieldnames = list(rows[0].keys())
    with Path(output_path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
