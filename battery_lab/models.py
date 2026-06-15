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

