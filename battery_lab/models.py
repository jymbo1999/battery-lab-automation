from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileMeta:
    path: Path
    original_filename: str
    analysis_type: str
    cell_id: str
    time_point: str = ""
    date: str = ""
    parser_status: str = "success"
    warning: str = ""


@dataclass
class ParsedDataset:
    meta: FileMeta
    rows: list[dict[str, Any]]
    columns: list[str] = field(default_factory=list)


@dataclass
class MetricRecord:
    cell_id: str
    analysis_type: str
    source_file: str
    metrics: dict[str, Any]
    warning: str = ""


@dataclass(frozen=True)
class AnalysisFileRecord:
    file_id: str
    cell_id: str
    sample_batch_id: str
    analysis_type: str
    file_name: str
    file_path: str
    time_point: str = ""
    cycle_protocol: str = ""
    upload_date: str = ""
    parse_status: str = "success"
    warning: str = ""


@dataclass(frozen=True)
class AnalysisAvailability:
    cell_id: str
    canonical_cell_id: str
    display_label: str
    sample_batch_id: str
    has_capacity: bool = False
    has_voltage_profile: bool = False
    has_eis: bool = False
    has_eis_time_series: bool = False
    has_sheet_resistance: bool = False
    has_raman: bool = False
    has_tga: bool = False
    file_count: int = 0
    missing_note: str = ""


@dataclass(frozen=True)
class ComparisonCandidate:
    cell_id_a: str
    cell_id_b: str
    same_electrolyte: bool
    same_binder: bool
    same_voltage_range: bool
    same_ratio: bool
    areal_mass_density_diff: float | None
    comparison_grade: str
    reason: str


@dataclass(frozen=True)
class AnalysisComparisonValidation:
    analysis_type: str
    cell_id_a: str
    cell_id_b: str
    status: str
    reason: str
    protocol_a: str = ""
    protocol_b: str = ""
    common_cycles: str = ""
