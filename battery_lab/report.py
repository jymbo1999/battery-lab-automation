from __future__ import annotations

import csv
import html
from pathlib import Path

from .conditions import CONDITION_FIELDS, compatibility_notes, find_condition
from .interactive import write_interactive_dashboard
from .insights import daily_summary, record_insights
from .models import MetricRecord, ParsedDataset
from .plots import write_dataset_plot


CONDITION_LABELS = {
    "sample": "Sample",
    "batch": "Batch",
    "cell_no": "Cell No.",
    "date": "Date",
    "areal_mass_density": "Areal mass density (mg/cm2)",
    "electrode_density": "합제밀도 (g/cm3)",
    "electrolyte": "전해질",
    "binder": "Binder",
    "voltage_range": "Voltage range",
    "ratio": "Ratio",
    "note": "메모",
}

ANALYSIS_LABELS = {
    "capacity": "Capacity",
    "voltage_profile": "Voltage profile",
    "eis": "EIS",
    "sheet_resistance": "면저항",
    "unknown": "미분류",
}


def write_outputs(
    datasets: list[ParsedDataset],
    records: list[MetricRecord],
    output_dir: Path,
    conditions: dict[str, dict[str, object]] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_paths = {}
    for dataset in datasets:
        path = write_dataset_plot(dataset, output_dir)
        if path:
            plot_paths[dataset.meta.original_filename] = path
    write_summary_csv(records, output_dir / "summary_metrics.csv", conditions or {})
    write_html_report(records, plot_paths, output_dir / "report.html", conditions or {})
    write_interactive_dashboard(datasets, records, output_dir / "dashboard.html", conditions or {})


def write_summary_csv(
    records: list[MetricRecord],
    path: Path,
    conditions: dict[str, dict[str, object]] | None = None,
) -> None:
    metric_keys = sorted({key for record in records for key in record.metrics})
    condition_keys = [key for key in CONDITION_FIELDS if any(key in row for row in (conditions or {}).values())]
    headers = ["cell_id", "analysis_type", "source_file", "warning"] + condition_keys + metric_keys
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for record in records:
            row = {
                "cell_id": record.cell_id,
                "analysis_type": record.analysis_type,
                "source_file": record.source_file,
                "warning": record.warning,
            }
            condition = find_condition(record.cell_id, conditions or {})
            row.update({key: condition.get(key, "") for key in condition_keys})
            row.update(record.metrics)
            writer.writerow(row)


def write_html_report(
    records: list[MetricRecord],
    plot_paths: dict[str, Path],
    path: Path,
    conditions: dict[str, dict[str, object]] | None = None,
) -> None:
    cards = []
    for record in records:
        plot = plot_paths.get(record.source_file)
        condition_rows = condition_table(find_condition(record.cell_id, conditions or {}))
        metric_rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in sorted(record.metrics.items())
        )
        insights = "".join(f"<li>{html.escape(note)}</li>" for note in record_insights(record))
        image = f'<img src="{html.escape(plot.relative_to(path.parent).as_posix())}" alt="{html.escape(record.source_file)} graph">' if plot else ""
        cards.append(
            f"""
            <section class="card">
              <div class="card-head">
                <h2>{html.escape(record.cell_id)}</h2>
                <span>{html.escape(ANALYSIS_LABELS.get(record.analysis_type, record.analysis_type))}</span>
              </div>
              <p class="source">{html.escape(record.source_file)}</p>
              {image}
              {condition_rows}
              <table>{metric_rows}</table>
              <h3>자동 해석 / 주의사항</h3>
              <ul>{insights}</ul>
            </section>
            """
        )
    notes = daily_summary(records) + compatibility_notes([record.cell_id for record in records], conditions or {})
    summary = "".join(f"<li>{html.escape(note)}</li>" for note in notes)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>배터리 실험 리포트</title>
  <style>
    * {{ box-sizing: border-box; }}
    :root {{ color-scheme: light; --ink: #1f2933; --muted: #697586; --line: #d7dde4; --panel: #f7f9fb; --accent: #256f7b; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: white; }}
    header {{ padding: 28px 36px 18px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    h1 {{ margin: 0 0 10px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 18px 0 8px; font-size: 14px; letter-spacing: 0; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 390px), 1fr)); gap: 18px; padding: 24px 36px 44px; max-width: 100%; }}
    .summary {{ padding: 14px 0 0; margin: 0; display: grid; gap: 5px; }}
    .card {{ border: 1px solid var(--line); border-radius: 8px; padding: 16px; overflow: hidden; }}
    .card-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .card-head span {{ color: white; background: var(--accent); border-radius: 999px; padding: 3px 10px; font-size: 12px; }}
    .source {{ color: var(--muted); margin: 5px 0 12px; font-size: 13px; }}
    img {{ width: 100%; max-height: 430px; object-fit: contain; border: 1px solid var(--line); border-radius: 6px; background: white; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 6px 4px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; width: 52%; }}
    ul {{ margin: 0 0 0 18px; padding: 0; color: var(--ink); }}
    @media (max-width: 640px) {{
      header {{ padding: 22px 18px 16px; }}
      main {{ padding: 18px; }}
      .card {{ padding: 12px; }}
      table {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>배터리 실험 자동 리포트</h1>
    <ul class="summary">{summary}</ul>
  </header>
  <main>
    {''.join(cards)}
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def condition_table(condition: dict[str, object]) -> str:
    rows = []
    for key in CONDITION_FIELDS:
        value = condition.get(key)
        if value not in (None, ""):
            rows.append(f"<tr><th>{html.escape(CONDITION_LABELS.get(key, key))}</th><td>{html.escape(str(value))}</td></tr>")
    if not rows:
        return ""
    return f"<h3>셀 조건</h3><table>{''.join(rows)}</table>"
