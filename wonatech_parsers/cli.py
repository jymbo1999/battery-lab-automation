#!/usr/bin/env python3
"""CLI for WonATech/ZIVE parser handoff package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .eis import parse_eis_file, write_eis_csv
from .wrd import build_capacity_summary, parse_wrd_file, write_capacity_summary_csv, write_wrd_raw_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert WonATech/ZIVE .SEO/.SDE/.wrd files to CSV")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", "-o", type=Path, help="Output CSV path. For WRD this means summary CSV unless --raw-output is used.")
    parser.add_argument("--raw-output", type=Path, help="WRD raw time-series CSV output path")
    parser.add_argument("--mass-g", type=float, default=None, help="Active material mass in gram for specific capacity")
    parser.add_argument("--json-meta", type=Path, help="Optional parser metadata/validation JSON path")
    args = parser.parse_args()

    suffix = args.input.suffix.lower()

    if suffix in {".seo", ".sde"}:
        result = parse_eis_file(args.input)
        output = args.output or args.input.with_suffix(".parsed_eis.csv")
        write_eis_csv(result.records, output)
        meta = {
            "kind": "eis",
            "input": str(args.input),
            "output": str(output),
            "start_offset": result.start_offset,
            "stride": result.stride,
            "layout": result.layout,
            "validation": result.validation,
        }
        print(f"EIS parsed: {len(result.records)} points -> {output}")

    elif suffix == ".wrd":
        records, validation = parse_wrd_file(args.input)
        summary_rows = build_capacity_summary(records, mass_g=args.mass_g)
        output = args.output or args.input.with_suffix(".capacity_summary.csv")
        write_capacity_summary_csv(summary_rows, output)
        if args.raw_output:
            write_wrd_raw_csv(records, args.raw_output)
        meta = {
            "kind": "wrd",
            "input": str(args.input),
            "summary_output": str(output),
            "raw_output": str(args.raw_output) if args.raw_output else None,
            "validation": validation,
            "summary_cycle_count": len(summary_rows),
        }
        print(f"WRD parsed: {len(records)} raw records, {len(summary_rows)} cycles -> {output}")

    else:
        raise SystemExit(f"Unsupported extension: {args.input.suffix}")

    if args.json_meta:
        args.json_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Metadata JSON -> {args.json_meta}")


if __name__ == "__main__":
    main()
