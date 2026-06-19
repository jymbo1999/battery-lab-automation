from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wonatech_parsers import (
    build_capacity_summary,
    parse_eis_file,
    parse_wrd_file,
    write_capacity_summary_csv,
    write_eis_csv,
    write_wrd_raw_csv,
)


WONATECH_SUFFIXES = {".seo", ".sde", ".wrd"}
PARSER_VERSION = "wonatech_zive_parser_v2"


@dataclass(frozen=True)
class WonatechConversion:
    source_path: Path
    kind: str
    primary_csv_path: Path
    meta_path: Path
    raw_csv_path: Path | None = None
    validation: dict[str, Any] | None = None


def is_wonatech_source(path: Path) -> bool:
    return path.suffix.lower() in WONATECH_SUFFIXES


def convert_wonatech_inputs(
    paths: list[Path],
    processed_dir: Path,
    *,
    write_raw_wrd: bool = False,
    mass_g: float | None = None,
) -> tuple[list[Path], list[WonatechConversion], list[str]]:
    converted_paths: list[Path] = []
    conversions: list[WonatechConversion] = []
    errors: list[str] = []
    for path in paths:
        if not is_wonatech_source(path):
            converted_paths.append(path)
            continue
        try:
            conversion = convert_wonatech_file(path, processed_dir, write_raw_wrd=write_raw_wrd, mass_g=mass_g)
        except Exception as exc:
            errors.append(f"{path.name}: {friendly_parser_error(exc)}")
            continue
        converted_paths.append(conversion.primary_csv_path)
        conversions.append(conversion)
    return converted_paths, conversions, errors


def convert_wonatech_file(
    source_path: Path,
    processed_dir: Path,
    *,
    write_raw_wrd: bool = False,
    mass_g: float | None = None,
) -> WonatechConversion:
    processed_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower()
    stem = safe_stem(source_path.stem)
    meta_path = processed_dir / f"{stem}_parser_meta.json"
    try:
        if suffix in {".seo", ".sde"}:
            return convert_eis_file(source_path, processed_dir, stem, meta_path)
        if suffix == ".wrd":
            return convert_wrd_file(source_path, processed_dir, stem, meta_path, write_raw_wrd=write_raw_wrd, mass_g=mass_g)
        raise ValueError(f"Unsupported WonATech file extension: {source_path.suffix}")
    except Exception as exc:
        write_parser_meta(
            meta_path,
            {
                "ok": False,
                "source_file": source_path.name,
                "source_path": str(source_path),
                "extension": source_path.suffix,
                "file_size_bytes": source_path.stat().st_size if source_path.exists() else None,
                "first_64_bytes_hex": first_bytes_hex(source_path),
                "error": str(exc),
            },
        )
        raise


def convert_eis_file(source_path: Path, processed_dir: Path, stem: str, meta_path: Path) -> WonatechConversion:
    result = parse_eis_file(source_path)
    csv_path = processed_dir / f"{stem}_eis.csv"
    write_eis_csv(result.records, csv_path)
    source_stat = source_path.stat()
    meta = {
        "ok": True,
        "kind": "eis",
        "parser_version": PARSER_VERSION,
        "source_file": source_path.name,
        "source_path": str(source_path),
        "source_size": int(source_stat.st_size),
        "source_mtime": source_stat.st_mtime,
        "primary_csv": str(csv_path),
        "start_offset": result.start_offset,
        "point_count": len(result.records),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stride": result.stride,
        "layout": result.layout,
        "validation": result.validation,
    }
    write_parser_meta(meta_path, meta)
    return WonatechConversion(source_path, "eis", csv_path, meta_path, validation=result.validation)


def convert_wrd_file(
    source_path: Path,
    processed_dir: Path,
    stem: str,
    meta_path: Path,
    *,
    write_raw_wrd: bool = False,
    mass_g: float | None = None,
) -> WonatechConversion:
    records, validation = parse_wrd_file(source_path)
    summary = build_capacity_summary(records, mass_g=mass_g)
    summary_path = processed_dir / f"{stem}_capacity_summary.csv"
    raw_path = processed_dir / f"{stem}_raw_timeseries.csv"
    write_capacity_summary_csv(summary, summary_path)
    raw_written = False
    if write_raw_wrd:
        write_wrd_raw_csv(records, raw_path)
        raw_written = True
    source_stat = source_path.stat()
    meta = {
        "ok": True,
        "kind": "wrd",
        "parser_version": PARSER_VERSION,
        "source_file": source_path.name,
        "source_path": str(source_path),
        "source_size": int(source_stat.st_size),
        "source_mtime": source_stat.st_mtime,
        "primary_csv": str(summary_path),
        "raw_csv": str(raw_path) if raw_written else None,
        "raw_csv_written": raw_written,
        "start_offset": validation.get("data_start_offset"),
        "point_count": len(records),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cycle_count": len(summary),
        "raw_record_count": len(records),
        "validation": validation,
    }
    write_parser_meta(meta_path, meta)
    return WonatechConversion(
        source_path,
        "wrd",
        summary_path,
        meta_path,
        raw_csv_path=raw_path if raw_written else None,
        validation=validation,
    )


def write_parser_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"\s+", "_", value.strip())
    cleaned = re.sub(r"[^A-Za-z0-9_.가-힣-]+", "_", cleaned)
    return cleaned.strip("._") or "wonatech_file"


def first_bytes_hex(path: Path, size: int = 64) -> str:
    try:
        return path.read_bytes()[:size].hex()
    except OSError:
        return ""


def friendly_parser_error(exc: Exception) -> str:
    return (
        "장비 원본 파일 구조가 기존 검증 샘플과 달라 자동 변환에 실패했습니다. "
        "원본 파일과 공식 export CSV/XLSX 한 쌍을 추가로 제공하면 parser rule을 확장할 수 있습니다. "
        f"({exc})"
    )


def conversions_to_rows(conversions: list[WonatechConversion]) -> list[dict[str, Any]]:
    rows = []
    for conversion in conversions:
        row = asdict(conversion)
        row["source_path"] = str(conversion.source_path)
        row["primary_csv_path"] = str(conversion.primary_csv_path)
        row["meta_path"] = str(conversion.meta_path)
        row["raw_csv_path"] = str(conversion.raw_csv_path) if conversion.raw_csv_path else ""
        rows.append(row)
    return rows
