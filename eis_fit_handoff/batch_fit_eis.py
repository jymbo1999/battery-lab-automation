"""
Batch-create EIS fit metadata sidecars for EIS files under a folder.

Example:
    python -m eis_fit_handoff.batch_fit_eis /path/to/eis-data --recursive --pattern "*.xlsx"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .eis_circle_fit import fit_eis_first_arc, load_valid_fit_metadata, save_fit_metadata

try:
    from battery_lab.file_io import parse_eis_file
except ModuleNotFoundError:  # pragma: no cover - standalone fallback is XLSX-only.
    parse_eis_file = None
    from .xlsx_reader import read_xlsx_z_columns


def iter_files(root: Path, pattern: str, recursive: bool):
    if recursive:
        yield from root.rglob(pattern)
    else:
        yield from root.glob(pattern)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--pattern", default="*.xlsx")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--z-real-col", type=int, default=3)
    parser.add_argument("--z-imag-col", type=int, default=4)
    parser.add_argument("--first-data-row", type=int, default=2)
    parser.add_argument("--force", action="store_true", help="Refit even when valid sidecar exists")
    args = parser.parse_args()

    ok = warn = fail = skipped = 0

    for path in iter_files(args.root, args.pattern, args.recursive):
        if path.name.startswith("~$") or path.name.endswith(".eisfit.json"):
            continue

        if not args.force and load_valid_fit_metadata(path) is not None:
            skipped += 1
            print(f"SKIP  {path}")
            continue

        try:
            if parse_eis_file is not None:
                parsed = parse_eis_file(path)
                result = fit_eis_first_arc(parsed["z_real"], parsed["z_imag"])
                reader_extra = {"reader": "battery_lab.file_io.parse_eis_file", "source_format": parsed["source_format"]}
            else:
                z_real, z_imag = read_xlsx_z_columns(
                    path,
                    sheet_name=args.sheet,
                    z_real_col=args.z_real_col,
                    z_imag_col=args.z_imag_col,
                    first_data_row=args.first_data_row,
                )
                result = fit_eis_first_arc(z_real, z_imag)
                reader_extra = {
                    "reader": "xlsx_reader.read_xlsx_z_columns",
                    "sheet": args.sheet,
                    "z_real_col": args.z_real_col,
                    "z_imag_col": args.z_imag_col,
                    "first_data_row": args.first_data_row,
                }
            sidecar = save_fit_metadata(
                path,
                result,
                extra=reader_extra,
            )

            if result.status == "ok":
                ok += 1
            elif result.status == "warn":
                warn += 1
            else:
                fail += 1

            print(
                f"{result.status.upper():5s} {path} -> {sidecar.name} "
                f"Rs={result.rs_ohm} Rct={result.rct_ohm} nRMSE={result.normalized_rmse}"
            )
        except Exception as exc:
            fail += 1
            print(f"FAIL  {path}: {exc}")

    print(f"\nDONE ok={ok} warn={warn} fail={fail} skipped={skipped}")


if __name__ == "__main__":
    main()
