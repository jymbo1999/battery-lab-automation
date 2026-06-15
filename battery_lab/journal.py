from __future__ import annotations

import csv
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .conditions import find_condition
from .models import MetricRecord, ParsedDataset
from .report import ANALYSIS_LABELS, write_outputs


UNKNOWN_DATE = "unknown-date"


@dataclass(frozen=True)
class JournalDay:
    date_key: str
    records: list[MetricRecord]
    datasets: list[ParsedDataset]
    output_dir: Path


def write_journal(
    datasets: list[ParsedDataset],
    records: list[MetricRecord],
    journal_dir: Path,
    conditions: dict[str, dict[str, Any]] | None = None,
) -> list[JournalDay]:
    conditions = conditions or {}
    journal_dir.mkdir(parents=True, exist_ok=True)
    groups = group_by_journal_date(datasets, records, conditions)
    days: list[JournalDay] = []
    for date_key in sorted(groups):
        group = groups[date_key]
        day_dir = journal_dir / date_key
        write_outputs(group["datasets"], group["records"], day_dir, conditions)
        days.append(JournalDay(date_key=date_key, records=group["records"], datasets=group["datasets"], output_dir=day_dir))
    write_journal_index(days, journal_dir / "index.html")
    write_journal_manifest(days, journal_dir / "journal_manifest.csv")
    return days


def group_by_journal_date(
    datasets: list[ParsedDataset],
    records: list[MetricRecord],
    conditions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[Any]]]:
    dataset_by_file = {dataset.meta.original_filename: dataset for dataset in datasets}
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"records": [], "datasets": []})
    dataset_names_by_date: dict[str, set[str]] = defaultdict(set)
    for record in records:
        dataset = dataset_by_file.get(record.source_file)
        date_key = infer_journal_date(record, dataset, conditions)
        grouped[date_key]["records"].append(record)
        if dataset and dataset.meta.original_filename not in dataset_names_by_date[date_key]:
            grouped[date_key]["datasets"].append(dataset)
            dataset_names_by_date[date_key].add(dataset.meta.original_filename)
    return dict(grouped)


def infer_journal_date(
    record: MetricRecord,
    dataset: ParsedDataset | None,
    conditions: dict[str, dict[str, Any]],
) -> str:
    candidates: list[Any] = []
    if dataset:
        condition = find_condition(dataset.meta.cell_id, conditions)
        candidates.extend([dataset.meta.date, condition.get("date"), dataset.meta.original_filename, dataset.meta.path.stem])
    record_condition = find_condition(record.cell_id, conditions)
    candidates.extend([record_condition.get("date"), record.source_file])
    for candidate in candidates:
        normalized = normalize_journal_date(candidate)
        if normalized:
            return normalized
    return UNKNOWN_DATE


def normalize_journal_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text:
        return ""
    text = re.sub(r"\.0$", "", text)
    text = text.replace("/", "-").replace(".", "-").replace("_", "-")
    match = re.search(r"(20\d{2})-?(\d{1,2})-?(\d{1,2})", text)
    if match:
        return format_date_parts(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.search(r"\b(\d{2})(\d{2})(\d{2})\b", text)
    if match:
        year = int(match.group(1))
        full_year = 2000 + year if year < 80 else 1900 + year
        return format_date_parts(full_year, int(match.group(2)), int(match.group(3)))
    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if match:
        return format_date_parts(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return ""


def format_date_parts(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def write_journal_index(days: list[JournalDay], path: Path) -> None:
    rows = []
    for day in days:
        records = day.records
        cell_count = len({record.cell_id for record in records})
        analyses = ", ".join(sorted({ANALYSIS_LABELS.get(record.analysis_type, record.analysis_type) for record in records}))
        label = "날짜 미확인" if day.date_key == UNKNOWN_DATE else day.date_key
        rows.append(
            f"""
            <article class="day-card">
              <div>
                <p class="eyebrow">실험 일지</p>
                <h2>{html.escape(label)}</h2>
                <p>{len(records)}개 파일 · {cell_count}개 셀 · {html.escape(analyses or "분석 없음")}</p>
              </div>
              <nav>
                <a href="{html.escape(day.date_key)}/dashboard.html">대시보드</a>
                <a href="{html.escape(day.date_key)}/report.html">리포트</a>
                <a href="{html.escape(day.date_key)}/summary_metrics.csv">요약 CSV</a>
              </nav>
            </article>
            """
        )
    empty = "<p class=\"empty\">아직 생성된 일지가 없습니다.</p>" if not rows else ""
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>배터리 실험 일지</title>
  <style>
    * {{ box-sizing: border-box; }}
    :root {{ color-scheme: light; --ink: #1f2933; --muted: #667085; --line: #d9e0e8; --panel: #f6f8fb; --accent: #256f7b; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; color: var(--ink); background: #ffffff; letter-spacing: 0; }}
    header {{ padding: 30px 36px 20px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    main {{ max-width: 1160px; margin: 0 auto; padding: 24px 24px 48px; display: grid; gap: 14px; }}
    h1 {{ margin: 0; font-size: clamp(1.8rem, 3vw, 2.8rem); }}
    h2 {{ margin: 0; font-size: 1.35rem; }}
    p {{ margin: 8px 0 0; color: var(--muted); line-height: 1.55; }}
    .eyebrow {{ margin: 0 0 4px; color: var(--accent); font-size: 0.78rem; font-weight: 700; }}
    .day-card {{ display: flex; justify-content: space-between; align-items: center; gap: 18px; border: 1px solid var(--line); border-radius: 8px; padding: 18px; background: white; }}
    nav {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }}
    a {{ color: white; background: var(--accent); border-radius: 6px; padding: 9px 12px; text-decoration: none; font-size: 0.9rem; }}
    .empty {{ padding: 22px; border: 1px dashed var(--line); border-radius: 8px; }}
    @media (max-width: 720px) {{
      header {{ padding: 24px 20px 18px; }}
      main {{ padding: 18px; }}
      .day-card {{ align-items: flex-start; flex-direction: column; }}
      nav {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>배터리 실험 일지</h1>
    <p>업로드된 실험 파일을 날짜별로 묶어 대시보드, 리포트, 요약 CSV로 정리합니다.</p>
  </header>
  <main>
    {empty}
    {''.join(rows)}
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def write_journal_manifest(days: list[JournalDay], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "file_count", "cell_count", "analysis_types", "dashboard", "report"])
        writer.writeheader()
        for day in days:
            writer.writerow(
                {
                    "date": day.date_key,
                    "file_count": len(day.records),
                    "cell_count": len({record.cell_id for record in day.records}),
                    "analysis_types": ";".join(sorted({record.analysis_type for record in day.records})),
                    "dashboard": f"{day.date_key}/dashboard.html",
                    "report": f"{day.date_key}/report.html",
                }
            )
