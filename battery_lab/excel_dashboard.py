from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import column_index_from_string, get_column_letter

from .config import BATTERY_CONDITION_WORKBOOK

DEFAULT_CONDITION_WORKBOOK = BATTERY_CONDITION_WORKBOOK
DEFAULT_CONDITION_SHEET = "JYJ"
FILTER_RULES = {
    "참고": {"12파이_cufoil"},
    "전해질": {"1.0mlipf6ec/dec1:1"},
    "종류": {"lib"},
    "binder": {"2wt%cmc", "2wt%cmc/40wt%sbr"},
    "voltagerange": {"0.01~2v"},
}
FORMULA_TEMPLATES_BY_HEADER = {
    "current(a)": "=I{row}*S{row}*1000/10^6",
    "activematerial(g)": "=(P{row}-Q{row})*R{row}",
    "arealmassdensity(mg/cm2)": "=I{row}*1000/(PI()*(0.6)^2)",
    "전극두께(mm)": "=U{row}-V{row}",
    "electrode(g)": "=P{row}-Q{row}",
    "volume(mm3)": "=113.1*W{row}",
    "합제밀도(g/cm3)": "=X{row}/(Y{row}/1000)",
}
EXTRA_EDITABLE_ROWS = 100
FAST_VIEW_ROW_LIMIT = 40
FAST_VIEW_EXTRA_ROWS = 15
DEFAULT_VIEWER_ZOOM = 0.55

_SERVERS: dict[tuple[Path, str, str, int], "ExcelDashboardServer"] = {}


@dataclass
class ExcelDashboardServer:
    httpd: ThreadingHTTPServer
    thread: threading.Thread
    url: str


class WorkbookStore:
    def __init__(self, workbook_path: Path, sheet_name: str) -> None:
        self.workbook_path = workbook_path
        self.sheet_name = sheet_name
        self.lock = threading.Lock()

    def sheet_payload(
        self,
        include_ignored: bool = True,
        row_limit: int | None = None,
        extra_rows: int = EXTRA_EDITABLE_ROWS,
    ) -> dict[str, Any]:
        with self.lock:
            workbook = load_workbook(self.workbook_path, data_only=False)
            if self.sheet_name not in workbook.sheetnames:
                raise KeyError(f"Sheet not found: {self.sheet_name}")
            worksheet = workbook[self.sheet_name]
            payload = build_sheet_payload(
                worksheet,
                self.workbook_path,
                include_ignored=include_ignored,
                row_limit=row_limit,
                extra_rows=extra_rows,
            )
            workbook.close()
            return payload

    def update_cell(self, row: int, column: int, value: Any) -> dict[str, Any]:
        if row < 1 or column < 1:
            raise ValueError("Cell coordinates must be positive.")
        with self.lock:
            workbook = load_workbook(self.workbook_path, data_only=False)
            if self.sheet_name not in workbook.sheetnames:
                workbook.close()
                raise KeyError(f"Sheet not found: {self.sheet_name}")
            worksheet = workbook[self.sheet_name]
            cell = worksheet.cell(row=row, column=column)
            if isinstance(cell, MergedCell):
                workbook.close()
                raise ValueError("Merged child cells cannot be edited directly.")
            cell.value = normalize_edit_value(value)
            apply_row_formulas(worksheet, row)
            workbook.save(self.workbook_path)
            payload = cell_payload(cell, worksheet, self.workbook_path, formula_columns=formula_columns(worksheet), computed_cache={})
            workbook.close()
            return payload


def ensure_excel_dashboard_server(
    workbook_path: Path = DEFAULT_CONDITION_WORKBOOK,
    sheet_name: str = DEFAULT_CONDITION_SHEET,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ExcelDashboardServer:
    workbook_path = workbook_path.resolve()
    key = (workbook_path, sheet_name, host, port)
    existing = _SERVERS.get(key)
    if existing and existing.thread.is_alive():
        return existing

    store = WorkbookStore(workbook_path, sheet_name)
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, name=f"excel-dashboard-{actual_port}", daemon=True)
    thread.start()
    server = ExcelDashboardServer(httpd=httpd, thread=thread, url=f"http://{host}:{actual_port}/")
    _SERVERS[key] = server
    return server


def make_handler(store: WorkbookStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_text(render_page(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/sheet":
                try:
                    params = parse_qs(parsed.query)
                    include_ignored = (params.get("filter", ["all"])[0]).strip().lower() not in {"hide", "matched"}
                    self.send_json(
                        store.sheet_payload(
                            include_ignored=include_ignored,
                            row_limit=parse_positive_int(params.get("limit", [""])[0]),
                            extra_rows=parse_positive_int(params.get("extra", [""])[0], default=EXTRA_EDITABLE_ROWS),
                        )
                    )
                except Exception as exc:  # pragma: no cover - browser-facing error path
                    self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/cell":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body or "{}")
                result = store.update_cell(int(data["row"]), int(data["column"]), data.get("value", ""))
                self.send_json({"ok": True, "cell": result})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def send_text(self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def parse_positive_int(value: Any, default: int | None = None) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_sheet_payload(
    worksheet: Any,
    workbook_path: Path,
    include_ignored: bool = True,
    row_limit: int | None = None,
    extra_rows: int = EXTRA_EDITABLE_ROWS,
) -> dict[str, Any]:
    header_map = header_columns(worksheet)
    formula_map = formula_columns(worksheet)
    source_max_row = worksheet.max_row
    extra_rows = max(0, int(extra_rows))
    computed_cache: dict[tuple[int, int], Any] = {}
    merged_lookup: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    merged_children: set[tuple[int, int]] = set()
    for merged_range in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = merged_range.bounds
        merged_lookup[(min_row, min_col)] = (max_row - min_row + 1, max_col - min_col + 1, max_row, max_col)
        for row in range(min_row, max_row + 1):
            for column in range(min_col, max_col + 1):
                if (row, column) != (min_row, min_col):
                    merged_children.add((row, column))

    source_rows: list[tuple[int, bool]] = []
    ignored_count = 0
    for row_idx in range(1, source_max_row + 1):
        ignored = row_idx > 1 and not row_matches_filter(worksheet, row_idx, header_map)
        if ignored:
            ignored_count += 1
        if ignored and not include_ignored:
            continue
        source_rows.append((row_idx, ignored))

    last_body_row = max((row_idx for row_idx, _ in source_rows if row_idx > 1), default=source_max_row)
    extra_start_row = (source_max_row + 1) if include_ignored else (last_body_row + 1)
    rendered_max_row = extra_start_row + extra_rows - 1 if extra_rows else max(last_body_row, source_max_row)
    extra_template_row = last_body_row if last_body_row > 1 else 1

    partial_rows = False
    if row_limit is not None and row_limit > 0 and len(source_rows) > row_limit + 1:
        header_rows = [row for row in source_rows if row[0] == 1]
        body_rows = [row for row in source_rows if row[0] != 1]
        source_rows = header_rows + body_rows[-row_limit:]
        partial_rows = True

    row_plan = [(row_idx, row_idx, ignored, False) for row_idx, ignored in source_rows]
    row_plan.extend((row_idx, extra_template_row, False, True) for row_idx in range(extra_start_row, extra_start_row + extra_rows))

    rows = []
    for display_row_idx, source_row_idx, ignored, is_extra_row in row_plan:
        row_dimension = worksheet.row_dimensions[source_row_idx]
        row_height = points_to_px(row_dimension.height) if row_dimension.height else None
        cells = []
        for column_idx in range(1, worksheet.max_column + 1):
            if not is_extra_row and (source_row_idx, column_idx) in merged_children:
                continue
            template_cell = worksheet.cell(row=source_row_idx, column=column_idx)
            if is_extra_row:
                cells.append(extra_cell_payload(display_row_idx, column_idx, template_cell, formula_map))
            else:
                rowspan, colspan, _, _ = merged_lookup.get(
                    (source_row_idx, column_idx), (1, 1, source_row_idx, column_idx)
                )
                cells.append(
                    cell_payload(
                        template_cell,
                        worksheet,
                        workbook_path,
                        rowspan=rowspan,
                        colspan=colspan,
                        formula_columns=formula_map,
                        computed_cache=computed_cache,
                    )
                )
        rows.append(
            {
                "index": display_row_idx,
                "height": row_height,
                "hidden": bool(row_dimension.hidden),
                "ignored": ignored,
                "extra": is_extra_row,
                "cells": cells,
            }
        )

    columns = []
    for column_idx in range(1, worksheet.max_column + 1):
        letter = get_column_letter(column_idx)
        dimension = worksheet.column_dimensions[letter]
        columns.append(
            {
                "index": column_idx,
                "letter": letter,
                "width": excel_width_to_px(dimension.width),
                "hidden": bool(dimension.hidden),
            }
        )

    return {
        "title": f"실험 일지 / {worksheet.title}",
        "workbook": str(workbook_path),
        "sheet": worksheet.title,
        "maxRow": rendered_max_row,
        "sourceMaxRow": source_max_row,
        "extraRows": extra_rows,
        "extraStartRow": extra_start_row,
        "includeIgnoredRows": include_ignored,
        "partialRows": partial_rows,
        "rowLimit": row_limit,
        "renderedRowCount": len(rows),
        "maxColumn": worksheet.max_column,
        "freezePane": str(worksheet.freeze_panes or ""),
        "zoom": DEFAULT_VIEWER_ZOOM * 100,
        "filter": {
            "required": filter_description(),
            "available": all(key in header_map for key in FILTER_RULES),
            "matchedRows": max(0, source_max_row - 1 - ignored_count),
            "ignoredRows": ignored_count,
        },
        "columns": columns,
        "rows": rows,
    }


def cell_payload(
    cell: Any,
    worksheet: Any,
    workbook_path: Path,
    rowspan: int = 1,
    colspan: int = 1,
    formula_columns: dict[int, str] | None = None,
    computed_cache: dict[tuple[int, int], Any] | None = None,
) -> dict[str, Any]:
    formula_columns = formula_columns or {}
    if computed_cache is None:
        computed_cache = {}
    formula = formula_for_cell(cell.row, cell.column, formula_columns, cell.value)
    computed = evaluate_cell_value(worksheet, cell.row, cell.column, formula_columns, computed_cache) if formula else cell.value
    formula_cell = bool(formula and cell.row > 1)
    return {
        "row": cell.row,
        "column": cell.column,
        "address": cell.coordinate,
        "value": display_value(computed),
        "rawValue": display_value(cell.value),
        "formula": formula or "",
        "formulaCell": formula_cell,
        "rowspan": rowspan,
        "colspan": colspan,
        "editable": not isinstance(cell, MergedCell) and not formula_cell,
        "style": cell_style(cell),
    }


def extra_cell_payload(row: int, column: int, template_cell: Any, formula_columns: dict[int, str]) -> dict[str, Any]:
    formula = formula_for_cell(row, column, formula_columns, None)
    return {
        "row": row,
        "column": column,
        "address": f"{get_column_letter(column)}{row}",
        "value": "",
        "rawValue": "",
        "formula": formula,
        "formulaCell": bool(formula),
        "rowspan": 1,
        "colspan": 1,
        "editable": not bool(formula),
        "style": cell_style(template_cell),
    }


def formula_columns(worksheet: Any) -> dict[int, str]:
    output: dict[int, str] = {}
    for column_idx in range(1, worksheet.max_column + 1):
        key = normalize_formula_header(worksheet.cell(row=1, column=column_idx).value)
        template = FORMULA_TEMPLATES_BY_HEADER.get(key)
        if template:
            output[column_idx] = template
    return output


def normalize_formula_header(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def formula_for_cell(row: int, column: int, formula_map: dict[int, str], raw_value: Any) -> str:
    if row <= 1:
        return str(raw_value) if isinstance(raw_value, str) and raw_value.startswith("=") else ""
    template = formula_map.get(column)
    if template:
        return template.format(row=row)
    return str(raw_value) if isinstance(raw_value, str) and raw_value.startswith("=") else ""


def apply_row_formulas(worksheet: Any, row: int) -> None:
    if row <= 1:
        return
    for column, template in formula_columns(worksheet).items():
        worksheet.cell(row=row, column=column).value = template.format(row=row)


def evaluate_cell_value(
    worksheet: Any,
    row: int,
    column: int,
    formula_map: dict[int, str],
    computed_cache: dict[tuple[int, int], Any],
    visiting: set[tuple[int, int]] | None = None,
) -> Any:
    key = (row, column)
    if key in computed_cache:
        return computed_cache[key]
    visiting = visiting or set()
    if key in visiting:
        return None
    visiting.add(key)
    cell = worksheet.cell(row=row, column=column)
    formula = formula_for_cell(row, column, formula_map, cell.value)
    if formula:
        value = evaluate_formula(worksheet, formula, formula_map, computed_cache, visiting)
    else:
        value = cell.value
    visiting.discard(key)
    computed_cache[key] = value
    return value


def evaluate_formula(
    worksheet: Any,
    formula: str,
    formula_map: dict[int, str],
    computed_cache: dict[tuple[int, int], Any],
    visiting: set[tuple[int, int]],
) -> Any:
    expression = formula[1:] if formula.startswith("=") else formula
    references: list[Any] = []

    def replace_reference(match: re.Match[str]) -> str:
        col = column_index_from_string(match.group(1))
        row = int(match.group(2))
        value = evaluate_cell_value(worksheet, row, col, formula_map, computed_cache, visiting)
        references.append(value)
        return str(float(value)) if is_numeric_value(value) else "0"

    expression = re.sub(r"\b([A-Z]+)(\d+)\b", replace_reference, expression)
    if references and all(value in (None, "") for value in references):
        return None
    expression = expression.replace("^", "**")
    expression = re.sub(r"\bPI\(\)", f"({math.pi})", expression, flags=re.IGNORECASE)
    if not re.fullmatch(r"[0-9eE+\-*/().\s]+", expression):
        return None
    try:
        result = eval(expression, {"__builtins__": {}}, {})
    except Exception:
        return None
    if isinstance(result, (int, float)) and math.isfinite(float(result)):
        return float(result)
    return None


def is_numeric_value(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def header_columns(worksheet: Any) -> dict[str, int]:
    headers = {}
    for column_idx in range(1, worksheet.max_column + 1):
        value = normalize_filter_value(worksheet.cell(row=1, column=column_idx).value)
        if value:
            headers[value] = column_idx
    return headers


def row_matches_filter(worksheet: Any, row_idx: int, header_map: dict[str, int]) -> bool:
    for header, allowed_values in FILTER_RULES.items():
        column_idx = header_map.get(header)
        if column_idx is None:
            return True
        value = normalize_filter_value(worksheet.cell(row=row_idx, column=column_idx).value)
        if value not in allowed_values:
            return False
    return True


def normalize_filter_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(text.split())


def filter_description() -> list[str]:
    return [
        "참고 = 12파이_Cu foil",
        "전해질 = 1.0M LiPF6 EC/DEC 1:1",
        "종류 = LIB",
        "Binder = 2wt% cmc 또는 2wt%cmc/40wt%SBR",
        "Voltage range = 0.01~2V",
    ]


def normalize_edit_value(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None
    if text.startswith("="):
        return text
    try:
        if "." not in text and "e" not in text.lower():
            return int(text)
        return float(text)
    except ValueError:
        return value


def cell_style(cell: Any) -> dict[str, Any]:
    font = cell.font
    fill = cell.fill
    alignment = cell.alignment
    border = cell.border
    return {
        "fontFamily": font.name,
        "fontSize": points_to_px(font.sz) if font.sz else None,
        "bold": bool(font.bold),
        "italic": bool(font.italic),
        "underline": bool(font.underline),
        "color": openpyxl_color(font.color),
        "backgroundColor": openpyxl_color(fill.fgColor) if fill and fill.fill_type else None,
        "horizontal": css_horizontal_alignment(alignment.horizontal),
        "vertical": css_vertical_alignment(alignment.vertical),
        "wrapText": bool(alignment.wrap_text),
        "border": border_style(border),
    }


def openpyxl_color(color: Any) -> str | None:
    if color is None:
        return None
    if color.type == "rgb" and color.rgb:
        rgb = str(color.rgb)
        if len(rgb) == 8:
            alpha = int(rgb[:2], 16)
            if alpha == 0:
                return None
            return f"#{rgb[2:]}"
        if len(rgb) == 6:
            return f"#{rgb}"
    return None


def css_horizontal_alignment(value: str | None) -> str | None:
    if value in {None, "general", "fill", "centerContinuous", "distributed", "justify"}:
        return None if value in {None, "general"} else "center"
    return value


def css_vertical_alignment(value: str | None) -> str | None:
    mapping = {
        "top": "top",
        "center": "middle",
        "bottom": "bottom",
        "justify": "middle",
        "distributed": "middle",
    }
    return mapping.get(value or "")


def border_style(border: Any) -> dict[str, str]:
    return {
        side: border_color(getattr(border, side))
        for side in ("left", "right", "top", "bottom")
        if getattr(border, side).style
    }


def border_color(side: Any) -> str:
    color = openpyxl_color(side.color) or "#d0d7de"
    return f"1px solid {color}"


def points_to_px(points: float) -> int:
    return max(1, round(float(points) * 96 / 72))


def excel_width_to_px(width: float | None) -> int:
    if width is None:
        width = 8.43
    return max(24, round(float(width) * 7 + 5))


def render_page(sheet_api_url: str = "/api/sheet", cell_api_url: str = "/api/cell") -> str:
    page = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>실험 일지</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #f3f4f6;
      color: #111827;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
      letter-spacing: 0;
    }
    .bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 52px;
      padding: 10px 14px;
      background: #ffffff;
      border-bottom: 1px solid #d0d7de;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .title { min-width: 0; }
    .title strong { display: block; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .title span { display: block; color: #6b7280; font-size: 12px; margin-top: 2px; }
    .rule-note {
      border-bottom: 1px solid #d0d7de;
      background: #fbfcfe;
      color: #6b7280;
      font-size: 11px;
      line-height: 1.45;
      padding: 5px 14px 6px;
    }
    .tools {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .filter-control {
      display: flex;
      align-items: center;
      gap: 7px;
      color: #57606a;
      font-size: 13px;
      white-space: nowrap;
    }
    .filter-control select {
      min-width: 146px;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      background: #ffffff;
      color: #111827;
      padding: 6px 8px;
      outline: none;
    }
    .zoom-control {
      display: flex;
      align-items: center;
      gap: 6px;
      color: #57606a;
      font-size: 13px;
      white-space: nowrap;
    }
    .zoom-control button {
      width: 30px;
      height: 30px;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      background: #ffffff;
      color: #111827;
      cursor: pointer;
      font-size: 17px;
      line-height: 1;
    }
    .zoom-control output {
      min-width: 46px;
      text-align: center;
      color: #57606a;
      font-variant-numeric: tabular-nums;
    }
    .status { color: #6b7280; font-size: 13px; white-space: nowrap; }
    .sheet-wrap {
      height: calc(100vh - 79px);
      overflow: auto;
      background: #f8fafc;
    }
    table.sheet {
      border-collapse: separate;
      border-spacing: 0;
      table-layout: fixed;
      background: #ffffff;
      font-size: 12px;
    }
    col.row-head { width: 48px; }
    thead th {
      position: sticky;
      top: 0;
      z-index: 3;
      height: 26px;
      background: #f6f8fa;
      color: #57606a;
      border-right: 1px solid #d0d7de;
      border-bottom: 1px solid #d0d7de;
      font-weight: 600;
      text-align: center;
    }
    thead th.corner {
      left: 0;
      z-index: 4;
    }
    tbody th {
      position: sticky;
      left: 0;
      z-index: 2;
      min-width: 48px;
      background: #f6f8fa;
      color: #57606a;
      border-right: 1px solid #d0d7de;
      border-bottom: 1px solid #d0d7de;
      font-weight: 600;
      text-align: center;
    }
    tbody tr.header-row th,
    tbody tr.header-row td {
      position: sticky;
      top: 26px;
      z-index: 3;
      box-shadow: 0 1px 0 #d0d7de;
    }
    tbody tr.header-row th {
      z-index: 5;
    }
    td {
      min-height: 22px;
      padding: 4px 6px;
      border-right: 1px solid #d0d7de;
      border-bottom: 1px solid #d0d7de;
      vertical-align: middle;
      overflow: hidden;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      line-height: 1.25;
      background: #ffffff;
      outline: none;
      cursor: cell;
      user-select: none;
    }
    td:focus {
      box-shadow: inset 0 0 0 2px #1a73e8;
      z-index: 1;
      position: relative;
    }
    td.selected-cell {
      background: #dbeafe !important;
      box-shadow: inset 0 0 0 1px #2563eb;
    }
    td.selection-anchor {
      box-shadow: inset 0 0 0 2px #1d4ed8;
    }
    td.formula-cell {
      color: #374151;
      cursor: default;
      box-shadow: inset 3px 0 0 #1a73e8;
    }
    tbody tr.ignored th,
    td.ignored {
      background: #e5e7eb !important;
      color: #6b7280 !important;
    }
    .hidden { display: none; }
  </style>
</head>
<body>
  <div class="bar">
    <div class="title">
      <strong id="title">실험 일지</strong>
      <span id="meta">JYJ 시트를 불러오는 중입니다.</span>
    </div>
    <div class="tools">
      <label class="filter-control" for="filterMode">
        필터
        <select id="filterMode">
          <option value="hide" selected>무시 행 표시안함</option>
          <option value="gray">무시 행 회색</option>
          <option value="all">전체 보기</option>
        </select>
      </label>
      <div class="zoom-control" aria-label="뷰어 배율">
        <button type="button" id="zoomOut" title="축소">-</button>
        <output id="zoomValue">55%</output>
        <button type="button" id="zoomIn" title="확대">+</button>
      </div>
      <div class="status" id="status">Loading</div>
    </div>
  </div>
  <div class="rule-note" id="ruleNote">
    필터 기준: 참고=12파이_Cu foil · 전해질=1.0M LiPF6 EC/DEC 1:1 · 종류=LIB · Binder=2wt% cmc 또는 2wt%cmc/40wt%SBR · Voltage range=0.01~2V
  </div>
  <div class="sheet-wrap" id="sheet"></div>
  <script>
    const api = { sheet: __SHEET_API_URL__, cell: __CELL_API_URL__ };
    const state = { data: null, filterMode: 'hide', zoom: 0.55, didInitialScroll: false, selection: null, dragging: false, fullDataLoaded: false, loadingFilteredRows: false };
    const statusEl = document.getElementById('status');
    const titleEl = document.getElementById('title');
    const metaEl = document.getElementById('meta');
    const sheetEl = document.getElementById('sheet');
    const filterModeEl = document.getElementById('filterMode');
    const zoomOutEl = document.getElementById('zoomOut');
    const zoomInEl = document.getElementById('zoomIn');
    const zoomValueEl = document.getElementById('zoomValue');
    filterModeEl.addEventListener('change', () => {
      state.filterMode = filterModeEl.value;
      if (!state.data) return;
      if (state.filterMode !== 'hide' && !state.fullDataLoaded) {
        loadSheet(true, { preserveScroll: true }).catch(error => {
          sheetEl.textContent = error.message;
          setStatus('Error');
        });
        return;
      }
      renderSheet(state.data);
    });
    zoomOutEl.addEventListener('click', () => setZoom(state.zoom - 0.08, viewportCenterAnchor()));
    zoomInEl.addEventListener('click', () => setZoom(state.zoom + 0.08, viewportCenterAnchor()));
    sheetEl.addEventListener('wheel', event => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const step = event.deltaY > 0 ? -0.06 : 0.06;
      setZoom(state.zoom + step, { clientX: event.clientX, clientY: event.clientY });
    }, { passive: false });
    document.addEventListener('pointerup', () => { state.dragging = false; });
    document.addEventListener('copy', event => {
      const copied = copySelectedCells(event);
      if (copied) setStatus(`Copied ${copied.rows}x${copied.columns}`);
    });

    function setStatus(text) { statusEl.textContent = text; }
    function viewportCenterAnchor() {
      const rect = sheetEl.getBoundingClientRect();
      return { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
    }
    function setZoom(value, anchor = null) {
      const oldZoom = state.zoom;
      const nextZoom = Math.max(0.45, Math.min(1.8, value));
      const table = sheetEl.querySelector('table.sheet');
      let anchorPoint = null;
      if (table && anchor && oldZoom > 0) {
        const rect = sheetEl.getBoundingClientRect();
        const viewportX = anchor.clientX - rect.left;
        const viewportY = anchor.clientY - rect.top;
        anchorPoint = {
          viewportX,
          viewportY,
          contentX: (sheetEl.scrollLeft + viewportX) / oldZoom,
          contentY: (sheetEl.scrollTop + viewportY) / oldZoom,
        };
      }
      state.zoom = nextZoom;
      zoomValueEl.textContent = `${Math.round(state.zoom * 100)}%`;
      if (table) table.style.zoom = state.zoom;
      if (anchorPoint) {
        sheetEl.scrollLeft = anchorPoint.contentX * state.zoom - anchorPoint.viewportX;
        sheetEl.scrollTop = anchorPoint.contentY * state.zoom - anchorPoint.viewportY;
      }
    }

    function sheetUrl(includeIgnored, fastView = false) {
      const separator = api.sheet.includes('?') ? '&' : '?';
      const params = new URLSearchParams({ filter: includeIgnored ? 'all' : 'hide' });
      if (fastView && !includeIgnored) {
        params.set('limit', '40');
        params.set('extra', '15');
      }
      return `${api.sheet}${separator}${params.toString()}`;
    }

    async function loadSheet(includeIgnored = false, options = {}) {
      setStatus('Loading');
      const response = await fetch(sheetUrl(includeIgnored, Boolean(options.fastView)));
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      state.data = data;
      state.fullDataLoaded = Boolean(data.includeIgnoredRows);
      titleEl.textContent = data.title;
      const filter = data.filter || {};
      const filterText = filter.available === false
        ? '필터 기준 헤더를 찾지 못했습니다.'
        : `조건 만족 ${filter.matchedRows ?? 0}행 · 무시 ${filter.ignoredRows ?? 0}행`;
      const partialText = data.partialRows ? ` · 빠른 보기 ${data.renderedRowCount ?? data.rows.length}행` : '';
      metaEl.textContent = `${data.maxRow} rows x ${data.maxColumn} columns · ${filterText}${partialText}`;
      renderSheet(data);
      if (!options.preserveScroll) scrollToLatestRows();
      setStatus('Ready');
      if (data.partialRows && options.fastView) loadCompleteFilteredRows();
    }

    async function loadCompleteFilteredRows() {
      if (state.loadingFilteredRows || state.fullDataLoaded) return;
      state.loadingFilteredRows = true;
      setStatus('Loading all matched rows');
      try {
        const response = await fetch(sheetUrl(false, false));
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        if (state.fullDataLoaded || state.filterMode !== 'hide') {
          setStatus('Ready');
          return;
        }
        state.data = data;
        titleEl.textContent = data.title;
        const filter = data.filter || {};
        const filterText = filter.available === false
          ? '필터 기준 헤더를 찾지 못했습니다.'
          : `조건 만족 ${filter.matchedRows ?? 0}행 · 무시 ${filter.ignoredRows ?? 0}행`;
        metaEl.textContent = `${data.maxRow} rows x ${data.maxColumn} columns · ${filterText}`;
        if (state.filterMode === 'hide') {
          renderSheet(data);
          scrollToLatestRows(true);
        }
        setStatus('Ready');
      } catch (error) {
        setStatus(`Partial view: ${error.message}`);
      } finally {
        state.loadingFilteredRows = false;
      }
    }

    function scrollToLatestRows(force = false) {
      if (state.didInitialScroll && !force) return;
      state.didInitialScroll = true;
      requestAnimationFrame(() => {
        const extraStartRow = Number(state.data?.extraStartRow || 0);
        const sourceMaxRow = Number(state.data?.sourceMaxRow || state.data?.maxRow || 1);
        const targetIndex = extraStartRow || sourceMaxRow;
        const targetRow = sheetEl.querySelector(`tr[data-row-index="${targetIndex}"]`)
          || sheetEl.querySelector(`tr[data-row-index="${sourceMaxRow}"]`);
        if (!targetRow) {
          sheetEl.scrollTop = sheetEl.scrollHeight;
          return;
        }
        const visibleExtraRows = 6;
        const rowHeight = Math.max(1, targetRow.getBoundingClientRect().height || targetRow.offsetHeight * state.zoom);
        const targetTop = targetRow.offsetTop * state.zoom;
        sheetEl.scrollTop = Math.max(0, targetTop - sheetEl.clientHeight + rowHeight * visibleExtraRows);
      });
    }

    function renderSheet(data) {
      const table = document.createElement('table');
      table.className = 'sheet';
      table.style.zoom = state.zoom;
      const colgroup = document.createElement('colgroup');
      const rowHead = document.createElement('col');
      rowHead.className = 'row-head';
      colgroup.appendChild(rowHead);
      data.columns.forEach(column => {
        const col = document.createElement('col');
        col.style.width = `${column.width}px`;
        if (column.hidden) col.className = 'hidden';
        colgroup.appendChild(col);
      });
      table.appendChild(colgroup);

      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      const corner = document.createElement('th');
      corner.className = 'corner';
      headRow.appendChild(corner);
      data.columns.forEach(column => {
        const th = document.createElement('th');
        th.textContent = column.letter;
        if (column.hidden) th.className = 'hidden';
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      data.rows.forEach(row => {
        if (row.ignored && state.filterMode === 'hide') return;
        const tr = document.createElement('tr');
        tr.dataset.rowIndex = row.index;
        if (row.height) tr.style.height = `${row.height}px`;
        if (row.hidden) tr.className = 'hidden';
        if (row.index === 1) tr.classList.add('header-row');
        if (row.ignored && state.filterMode === 'gray') tr.classList.add('ignored');
        const rowLabel = document.createElement('th');
        rowLabel.textContent = row.index;
        tr.appendChild(rowLabel);
        row.cells.forEach(cell => {
          const td = document.createElement('td');
          td.dataset.row = cell.row;
          td.dataset.column = cell.column;
          td.dataset.original = cell.value ?? '';
          td.dataset.formula = cell.formula ?? '';
          td.textContent = cell.value ?? '';
          td.rowSpan = cell.rowspan || 1;
          td.colSpan = cell.colspan || 1;
          td.tabIndex = 0;
          td.contentEditable = 'false';
          applyStyle(td, cell.style || {});
          if (cell.formulaCell) {
            td.classList.add('formula-cell');
            td.title = cell.formula || '';
          }
          if (row.ignored && state.filterMode === 'gray') td.classList.add('ignored');
          td.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            event.preventDefault();
            state.dragging = true;
            selectCells(cell.row, cell.column, cell.row, cell.column);
            td.focus({ preventScroll: true });
          });
          td.addEventListener('pointerenter', () => {
            if (!state.dragging || !state.selection) return;
            selectCells(state.selection.anchorRow, state.selection.anchorColumn, cell.row, cell.column);
          });
          td.addEventListener('keydown', event => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'c') return;
            if (event.key === 'Escape') clearSelection();
          });
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      sheetEl.replaceChildren(table);
      setZoom(state.zoom);
      applySelection();
    }

    function selectCells(anchorRow, anchorColumn, focusRow, focusColumn) {
      state.selection = { anchorRow, anchorColumn, focusRow, focusColumn };
      applySelection();
    }

    function clearSelection() {
      state.selection = null;
      applySelection();
    }

    function selectionBounds() {
      if (!state.selection) return null;
      return {
        top: Math.min(state.selection.anchorRow, state.selection.focusRow),
        bottom: Math.max(state.selection.anchorRow, state.selection.focusRow),
        left: Math.min(state.selection.anchorColumn, state.selection.focusColumn),
        right: Math.max(state.selection.anchorColumn, state.selection.focusColumn),
      };
    }

    function applySelection() {
      const bounds = selectionBounds();
      sheetEl.querySelectorAll('td[data-row][data-column]').forEach(td => {
        const row = Number(td.dataset.row);
        const column = Number(td.dataset.column);
        const selected = bounds && row >= bounds.top && row <= bounds.bottom && column >= bounds.left && column <= bounds.right;
        td.classList.toggle('selected-cell', Boolean(selected));
        td.classList.toggle('selection-anchor', Boolean(state.selection && row === state.selection.anchorRow && column === state.selection.anchorColumn));
      });
    }

    function selectedCellMatrix() {
      const bounds = selectionBounds();
      if (!bounds) return null;
      const byAddress = new Map();
      sheetEl.querySelectorAll('td[data-row][data-column]').forEach(td => {
        byAddress.set(`${td.dataset.row}:${td.dataset.column}`, td.dataset.original || td.textContent || '');
      });
      const rows = [];
      for (let row = bounds.top; row <= bounds.bottom; row += 1) {
        const values = [];
        for (let column = bounds.left; column <= bounds.right; column += 1) {
          values.push(byAddress.get(`${row}:${column}`) || '');
        }
        rows.push(values);
      }
      return rows;
    }

    function copySelectedCells(event) {
      const rows = selectedCellMatrix();
      if (!rows) return null;
      const text = rows.map(row => row.join('\\t')).join('\\n');
      event.preventDefault();
      event.clipboardData.setData('text/plain', text);
      return { rows: rows.length, columns: rows[0]?.length || 0 };
    }

    function applyStyle(td, style) {
      if (style.fontFamily) td.style.fontFamily = `"${style.fontFamily}", sans-serif`;
      if (style.fontSize) td.style.fontSize = `${style.fontSize}px`;
      if (style.bold) td.style.fontWeight = '700';
      if (style.italic) td.style.fontStyle = 'italic';
      if (style.underline) td.style.textDecoration = 'underline';
      if (style.color) td.style.color = style.color;
      if (style.backgroundColor) td.style.backgroundColor = style.backgroundColor;
      if (style.horizontal) td.style.textAlign = style.horizontal;
      if (style.vertical) td.style.verticalAlign = style.vertical;
      if (style.wrapText === false) td.style.whiteSpace = 'nowrap';
      const border = style.border || {};
      Object.entries(border).forEach(([side, value]) => { td.style[`border${side[0].toUpperCase()}${side.slice(1)}`] = value; });
    }

    loadSheet(false, { fastView: true }).catch(error => {
      sheetEl.textContent = error.message;
      setStatus('Error');
    });
  </script>
</body>
</html>
"""
    return page.replace("__SHEET_API_URL__", json.dumps(sheet_api_url)).replace("__CELL_API_URL__", json.dumps(cell_api_url))
