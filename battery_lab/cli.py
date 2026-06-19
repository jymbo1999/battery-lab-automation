from __future__ import annotations

import argparse
from pathlib import Path

from .conditions import read_conditions
from .file_io import parse_file
from .journal import write_journal
from .metrics import compute_metrics
from .report import write_outputs
from .wonatech_service import convert_wonatech_inputs


SUPPORTED = {".csv", ".tsv", ".txt", ".seo", ".sde", ".wrd", ".xlsx", ".xls"}


def main() -> int:
    parser = argparse.ArgumentParser(description="배터리 실험 그래프와 요약 지표를 자동 생성합니다.")
    parser.add_argument("input", type=Path, help="입력 파일 또는 폴더")
    parser.add_argument("--output", type=Path, default=Path("battery_visual_outputs"), help="출력 폴더")
    parser.add_argument("--conditions", type=Path, help="선택 사항: 셀 조건표 CSV/XLSX")
    parser.add_argument("--condition-sheet", default=None, help="조건표 XLSX에서 읽을 sheet 이름. 예: JYJ")
    parser.add_argument("--journal", type=Path, default=Path("lab_journal"), help="날짜별 일지 출력 폴더")
    parser.add_argument("--no-journal", action="store_true", help="날짜별 일지 생성을 끕니다.")
    parser.add_argument("--write-wrd-raw", action="store_true", help="WRD raw time-series CSV도 생성합니다. 파일이 매우 커질 수 있습니다.")
    args = parser.parse_args()

    paths = collect_paths(args.input)
    if args.conditions:
        paths = [path for path in paths if path.resolve() != args.conditions.resolve()]
    paths, conversions, conversion_errors = convert_wonatech_inputs(
        paths,
        args.output / "processed",
        write_raw_wrd=args.write_wrd_raw,
    )
    conditions = read_conditions(args.conditions, sheet_name=args.condition_sheet) if args.conditions else {}
    datasets = []
    records = []
    errors = list(conversion_errors)
    for path in paths:
        try:
            dataset = parse_file(path)
            datasets.append(dataset)
            records.append(compute_metrics(dataset))
        except Exception as exc:  # pragma: no cover - reported to operator
            errors.append(f"{path.name}: {exc}")
    write_outputs(datasets, records, args.output, conditions)
    journal_days = []
    if not args.no_journal:
        journal_days = write_journal(datasets, records, args.journal, conditions)
    print(f"처리 완료: {len(records)}개 파일")
    print(f"요약 CSV: {args.output / 'summary_metrics.csv'}")
    print(f"HTML 리포트: {args.output / 'report.html'}")
    print(f"인터랙티브 대시보드: {args.output / 'dashboard.html'}")
    if conversions:
        print(f"장비 원본 변환: {len(conversions)}개")
        for conversion in conversions:
            validation = conversion.validation or {}
            if conversion.kind == "eis":
                print(
                    f"- {conversion.source_path.name}: EIS {validation.get('point_count', '?')} points, "
                    f"{validation.get('frequency_max_hz', '?')} Hz -> {validation.get('frequency_min_hz', '?')} Hz"
                )
            elif conversion.kind == "wrd":
                print(
                    f"- {conversion.source_path.name}: WRD {validation.get('record_count', '?')} records, "
                    f"cycle {validation.get('cycle_min_export_number', '?')} -> {validation.get('cycle_max_export_number', '?')}"
                )
    if not args.no_journal:
        print(f"날짜별 실험 일지: {args.journal / 'index.html'} ({len(journal_days)}일)")
    if errors:
        print("오류:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


def collect_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(
        path
        for path in input_path.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED
        and not path.name.startswith("~$")
        and "processed" not in path.parts
    )


if __name__ == "__main__":
    raise SystemExit(main())
