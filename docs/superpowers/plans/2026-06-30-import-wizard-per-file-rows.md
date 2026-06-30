# 새 실험 등록 위저드: 파일 1개 = 실험일지 1행 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** import 위저드를 "N개 파일 → 1행"에서 "row-unit 1개 → 실험일지 1행"(시계열만 묶음)으로 바꾸고, 파일별 실험정보 폼·기본값·정규화 파일명·역추적 저장을 구현한다.

**Architecture:** 백엔드(`experiment_import.py`)는 단일 `metadata`를 manifest-level `unit_metadata`(unit_id별)로 바꾸고, journal 기록을 정확한 헤더→컬럼 매핑 + Python 계산 리터럴(areal/density)로 교체, commit을 unit 루프로 N행 생성. 16개 입력 필드는 서버 단일 출처 `IMPORT_JOURNAL_FIELDS`로 정의해 프론트가 폼을 생성. 프론트(`app.html`)는 row-unit별 아코디언 폼 + 고정값 토글 + raw→정규화 파일명 표기.

**Tech Stack:** Python 3.11, Flask, openpyxl, vanilla JS, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-06-29-import-wizard-per-file-rows-design.md`

**Run tests:** `cd battery-lab-automation && .venv/bin/python -m pytest tests/test_experiment_import.py -v`

---

## File Structure

- **Modify** `battery_lab/experiment_import.py` — 데이터 모델(unit_metadata), 필드 스펙, 검증/도출, journal writer, 저장 명명, commit 루프
- **Modify** `battery_lab/routes.py` — field-spec 엔드포인트, unit별 metadata PATCH, 정규화명 미리보기, commit 응답
- **Modify** `battery_lab/templates/battery_lab/app.html` — 업로드 안내, 아코디언 폼, 고정값 토글, Binder 드롭다운, Date 자동, raw→정규화 표기, 필드 삭제
- **Modify** `tests/test_experiment_import.py` — per-unit 모델·신규 스키마로 재작성
- **Create** `tests/test_import_journal_fields.py` — 필드 스펙·journal writer·도출값 단위 테스트

---

## Phase 1 — 필드 스펙 + 도출값 계산 (순수 함수, TDD)

### Task 1: 입력 필드 단일 출처 `IMPORT_JOURNAL_FIELDS`

**Files:**
- Modify: `battery_lab/experiment_import.py` (상수 영역, `REQUIRED_METADATA_FIELDS`(46-55) 부근)
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_import_journal_fields.py
import unittest
from battery_lab.experiment_import import (
    IMPORT_JOURNAL_FIELDS, field_keys, fixed_defaults, variable_keys,
)


class ImportJournalFieldsTests(unittest.TestCase):
    def test_field_spec_has_16_fields_4_variable_12_fixed(self):
        self.assertEqual(len(IMPORT_JOURNAL_FIELDS), 16)
        self.assertEqual(len(variable_keys()), 4)
        self.assertEqual(sum(1 for f in IMPORT_JOURNAL_FIELDS if f["bucket"] == "fixed"), 12)

    def test_variable_fields_are_blank_user_filled(self):
        self.assertEqual(
            variable_keys(),
            ["date", "sample", "foil_electrode_g", "foil_electrode_mm"],
        )

    def test_fixed_defaults_match_spec(self):
        d = fixed_defaults()
        self.assertEqual(d["reference"], "12 파이_Cu foil")
        self.assertEqual(d["electrolyte"], "1.0M LiPF6 EC/DEC 1:1")
        self.assertEqual(d["cell_type"], "LIB")
        self.assertEqual(d["foil_g"], "0.009928")
        self.assertEqual(d["ratio"], "0.96")
        self.assertEqual(d["current_density"], "37.2")
        self.assertEqual(d["foil_thickness_mm"], "0.00958")
        self.assertEqual(d["drying_condition"], "60도 12시간")

    def test_each_field_maps_to_exact_excel_header(self):
        headers = {f["key"]: f["header"] for f in IMPORT_JOURNAL_FIELDS}
        self.assertEqual(headers["foil_thickness_mm"], "호일 두께(mm)")
        self.assertEqual(headers["foil_electrode_mm"], "전극(foil+electrode) 두께(mm)")
        self.assertEqual(headers["reference"], "참고")
        self.assertEqual(headers["cell_type"], "종류")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py -v`
Expected: FAIL — `ImportError: cannot import name 'IMPORT_JOURNAL_FIELDS'`

- [ ] **Step 3: 최소 구현**

`experiment_import.py`의 `REQUIRED_METADATA_FIELDS` 정의 위/아래에 추가:

```python
# Single source of truth for the import form + journal writer.
# Each field: stable key, EXACT Excel header (sheet JYJ), bucket, default.
# Mapping by EXACT header avoids the condition_column 'mm' collision
# (호일 두께/전극 두께/압연 전 두께 all normalize to 'mm').
IMPORT_JOURNAL_FIELDS = [
    {"key": "date", "header": "Date", "bucket": "variable", "default": ""},
    {"key": "sample", "header": "Sample", "bucket": "variable", "default": ""},
    {"key": "foil_electrode_g", "header": "foil+electrode (g)", "bucket": "variable", "default": ""},
    {"key": "foil_electrode_mm", "header": "전극(foil+electrode) 두께(mm)", "bucket": "variable", "default": ""},
    {"key": "reference", "header": "참고", "bucket": "fixed", "default": "12 파이_Cu foil"},
    {"key": "electrolyte", "header": "전해질", "bucket": "fixed", "default": "1.0M LiPF6 EC/DEC 1:1"},
    {"key": "cell_type", "header": "종류", "bucket": "fixed", "default": "LIB"},
    {"key": "conductive_agent", "header": "Conductive agent", "bucket": "fixed", "default": "-"},
    {"key": "binder", "header": "Binder", "bucket": "fixed", "default": "2wt%cmc"},
    {"key": "voltage_range", "header": "Voltage range", "bucket": "fixed", "default": "0.01~2V"},
    {"key": "foil_g", "header": "foil (g)", "bucket": "fixed", "default": "0.009928"},
    {"key": "ratio", "header": "ratio", "bucket": "fixed", "default": "0.96"},
    {"key": "current_density", "header": "Current density (mA/g)", "bucket": "fixed", "default": "37.2"},
    {"key": "foil_thickness_mm", "header": "호일 두께(mm)", "bucket": "fixed", "default": "0.00958"},
    {"key": "electrolyte_ul", "header": "Electrolyte (ul)", "bucket": "fixed", "default": "80"},
    {"key": "drying_condition", "header": "Drying Condition", "bucket": "fixed", "default": "60도 12시간"},
]
# Binder presets offered in the form dropdown (still free-text editable).
BINDER_PRESETS = ["2wt%cmc", "2wt%cmc/40wt%SBR"]


def field_keys() -> list[str]:
    return [f["key"] for f in IMPORT_JOURNAL_FIELDS]


def variable_keys() -> list[str]:
    return [f["key"] for f in IMPORT_JOURNAL_FIELDS if f["bucket"] == "variable"]


def fixed_defaults() -> dict[str, str]:
    return {f["key"]: f["default"] for f in IMPORT_JOURNAL_FIELDS if f["bucket"] == "fixed"}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add tests/test_import_journal_fields.py battery_lab/experiment_import.py
git commit -m "feat(import): add IMPORT_JOURNAL_FIELDS single-source field spec"
```

---

### Task 2: 도출값 계산 `compute_derived_metadata`

**Files:**
- Modify: `battery_lab/experiment_import.py`
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# append to tests/test_import_journal_fields.py
import math
from battery_lab.experiment_import import compute_derived_metadata


class ComputeDerivedTests(unittest.TestCase):
    def test_areal_mass_density_from_foil_inputs(self):
        # active = (foil_electrode - foil) * ratio ; areal = active*1000/(pi*0.6^2)
        derived = compute_derived_metadata(
            {"foil_electrode_g": "0.0150", "foil_g": "0.009928", "ratio": "0.96",
             "foil_electrode_mm": "0.020", "foil_thickness_mm": "0.00958"}
        )
        active = (0.0150 - 0.009928) * 0.96
        self.assertAlmostEqual(derived["active_material_g"], active, places=9)
        self.assertAlmostEqual(
            derived["areal_mass_density"], active * 1000 / (math.pi * 0.6 ** 2), places=6
        )

    def test_electrode_density_from_thickness(self):
        derived = compute_derived_metadata(
            {"foil_electrode_g": "0.0150", "foil_g": "0.009928", "ratio": "0.96",
             "foil_electrode_mm": "0.020", "foil_thickness_mm": "0.00958"}
        )
        electrode_g = 0.0150 - 0.009928
        thickness = 0.020 - 0.00958
        volume = 113.1 * thickness
        self.assertAlmostEqual(derived["electrode_density"], electrode_g / (volume / 1000), places=6)

    def test_missing_inputs_yield_none(self):
        self.assertIsNone(compute_derived_metadata({"foil_g": "0.009928"})["areal_mass_density"])
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::ComputeDerivedTests -v`
Expected: FAIL — `cannot import name 'compute_derived_metadata'`

- [ ] **Step 3: 구현**

```python
import math  # ensure imported at top of experiment_import.py

def _to_float(value: object) -> float | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def compute_derived_metadata(metadata: dict[str, object]) -> dict[str, float | None]:
    """Compute the journal's formula-column values in Python so the app can read
    them numerically before Excel ever recalculates (read_xlsx_optional uses
    data_only=True). Mirrors excel_dashboard.FORMULA_TEMPLATES_BY_HEADER."""
    foil_electrode_g = _to_float(metadata.get("foil_electrode_g"))
    foil_g = _to_float(metadata.get("foil_g"))
    ratio = _to_float(metadata.get("ratio"))
    foil_electrode_mm = _to_float(metadata.get("foil_electrode_mm"))
    foil_thickness_mm = _to_float(metadata.get("foil_thickness_mm"))

    active = None
    areal = None
    if None not in (foil_electrode_g, foil_g, ratio):
        active = (foil_electrode_g - foil_g) * ratio
        areal = active * 1000 / (math.pi * 0.6 ** 2)

    electrode_g = None
    electrode_density = None
    if None not in (foil_electrode_g, foil_g):
        electrode_g = foil_electrode_g - foil_g
        if None not in (foil_electrode_mm, foil_thickness_mm):
            thickness = foil_electrode_mm - foil_thickness_mm
            volume = 113.1 * thickness
            if volume:
                electrode_density = electrode_g / (volume / 1000)

    return {
        "active_material_g": active,
        "areal_mass_density": areal,
        "electrode_g": electrode_g,
        "electrode_density": electrode_density,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::ComputeDerivedTests -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): compute derived areal/electrode density in Python"
```

---

### Task 3: 신규 검증 `validate_metadata`

**Files:**
- Modify: `battery_lab/experiment_import.py:810-828` (`validate_metadata`), `:796-807` (`clean_metadata`)
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# append to tests/test_import_journal_fields.py
from battery_lab.experiment_import import validate_metadata, clean_metadata


class ValidateMetadataTests(unittest.TestCase):
    def _valid(self):
        return {
            "date": "260627", "sample": "cell A",
            "foil_electrode_g": "0.0150", "foil_electrode_mm": "0.020",
            "foil_g": "0.009928", "ratio": "0.96",
        }

    def test_valid_metadata_has_no_errors(self):
        self.assertEqual(validate_metadata(self._valid()), [])

    def test_required_variable_fields_enforced(self):
        m = self._valid(); del m["foil_electrode_g"]
        self.assertIn("foil_electrode_g is required", validate_metadata(m))

    def test_foil_electrode_must_exceed_foil(self):
        m = self._valid(); m["foil_electrode_g"] = "0.005"
        self.assertTrue(any("foil+electrode" in e for e in validate_metadata(m)))

    def test_ratio_range(self):
        m = self._valid(); m["ratio"] = "1.5"
        self.assertTrue(any("ratio" in e for e in validate_metadata(m)))

    def test_clean_metadata_keeps_only_spec_keys(self):
        cleaned = clean_metadata({**self._valid(), "sample_group": "x", "junk": "y"})
        self.assertNotIn("sample_group", cleaned)
        self.assertNotIn("junk", cleaned)
        self.assertIn("sample", cleaned)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::ValidateMetadataTests -v`
Expected: FAIL (현 `validate_metadata`는 `areal_mass_density` 필수 → 신규 규칙 없음)

- [ ] **Step 3: 구현 — `validate_metadata`/`clean_metadata` 교체**

`REQUIRED_METADATA_FIELDS`(46-55)를 제거하고 아래로 교체:

```python
REQUIRED_IMPORT_FIELDS = ["date", "sample", "foil_electrode_g", "foil_electrode_mm"]
NUMERIC_IMPORT_FIELDS = ["foil_electrode_g", "foil_electrode_mm", "foil_g", "ratio", "current_density", "foil_thickness_mm", "electrolyte_ul"]
```

`clean_metadata`(796-807) 교체:

```python
def clean_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = set(field_keys())
    cleaned: dict[str, object] = {}
    for key in allowed:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value != "":
            cleaned[key] = value
    return cleaned
```

`validate_metadata`(810-828) 교체:

```python
def validate_metadata(metadata: dict[str, object]) -> list[str]:
    errors = [f"{field} is required" for field in REQUIRED_IMPORT_FIELDS if metadata.get(field) in (None, "")]
    for field in NUMERIC_IMPORT_FIELDS:
        value = metadata.get(field)
        if value in (None, ""):
            continue
        if _to_float(value) is None:
            errors.append(f"{field} must be numeric")
    fe = _to_float(metadata.get("foil_electrode_g"))
    foil = _to_float(metadata.get("foil_g"))
    if fe is not None and foil is not None and fe <= foil:
        errors.append("foil+electrode (g) must be greater than foil (g)")
    ratio = _to_float(metadata.get("ratio"))
    if ratio is not None and not (0 < ratio <= 1):
        errors.append("ratio must be between 0 and 1")
    date = str(metadata.get("date") or "")
    if date and not re.fullmatch(r"(?:\d{6}|\d{8}|\d{4}[-./]\d{1,2}[-./]\d{1,2})", date):
        errors.append("date must be YYMMDD, YYYYMMDD, or YYYY-MM-DD")
    return errors
```

> NOTE: `REQUIRED_METADATA_FIELDS`를 참조하던 다른 곳을 찾아 교체: `grep -rn REQUIRED_METADATA_FIELDS battery_lab/`. 라우트 `import_metadata_options_api`(`routes.py:252,256,261`)와 `metadata_options_from_conditions`(`experiment_import.py:831-837`)에서 사용 → Task 8에서 field-spec 엔드포인트로 대체하므로, 우선 `REQUIRED_IMPORT_FIELDS`로 임시 치환해 import 에러 방지.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): new per-file metadata validation schema"
```

---

## Phase 2 — journal writer (정확한 헤더 + 리터럴) + 저장 명명

### Task 4: 정확한 헤더 기반 행 기록 `write_journal_row`

**Files:**
- Modify: `battery_lab/experiment_import.py:571-587` (`append_journal_row`)
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# append to tests/test_import_journal_fields.py
import tempfile
from pathlib import Path
from openpyxl import Workbook, load_workbook
from battery_lab.experiment_import import append_journal_row


class JournalWriterTests(unittest.TestCase):
    def _make_book(self, path):
        wb = Workbook(); ws = wb.active; ws.title = "JYJ"
        ws.append(["참고", "전해질", "종류", "Date", "Sample", "Conductive agent", "Binder",
                   "Current (A)", "Active material (g)", "CV (uA)", "Cut capacity (Ah)", "Voltage range",
                   "Cell 자리", "Theoretical capacity (mAh/g)", "C-Rate (1/h)", "foil+electrode (g)",
                   "foil (g)", "ratio", "Current density (mA/g)", "Areal mass density (mg/cｍ2)",
                   "전극(foil+electrode) 두께(mm)", "호일 두께(mm)", "전극 두께(mm)", "electrode(g)",
                   "volume (mm3)", "합제밀도(g/cm3)"])
        wb.save(path); wb.close()

    def test_writes_thickness_to_correct_distinct_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "j.xlsx"; self._make_book(p)
            metadata = {"sample": "A", "foil_electrode_g": "0.0150", "foil_g": "0.009928",
                        "ratio": "0.96", "foil_electrode_mm": "0.020", "foil_thickness_mm": "0.00958",
                        "reference": "12 파이_Cu foil"}
            row = append_journal_row(p, "JYJ", metadata)
            wb = load_workbook(p); ws = wb["JYJ"]
            self.assertEqual(row, 2)
            self.assertEqual(ws.cell(row=2, column=21).value, "0.020")  # 전극 두께(mm)
            self.assertEqual(ws.cell(row=2, column=22).value, "0.00958")  # 호일 두께(mm) — no 'mm' collision
            self.assertEqual(ws.cell(row=2, column=1).value, "12 파이_Cu foil")
            wb.close()

    def test_areal_density_written_as_numeric_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "j.xlsx"; self._make_book(p)
            metadata = {"sample": "A", "foil_electrode_g": "0.0150", "foil_g": "0.009928", "ratio": "0.96"}
            append_journal_row(p, "JYJ", metadata)
            wb = load_workbook(p, data_only=True); ws = wb["JYJ"]
            self.assertIsInstance(ws.cell(row=2, column=20).value, float)  # Areal mass density literal
            self.assertGreater(ws.cell(row=2, column=20).value, 0)
            wb.close()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::JournalWriterTests -v`
Expected: FAIL — 현 `append_journal_row`는 `condition_column` 키 매핑(‘mm’ 충돌) + areal 수식(literal 아님)

- [ ] **Step 3: 구현 — `append_journal_row` 교체**

```python
def header_column_map(worksheet) -> dict[str, int]:
    """Map EXACT header text -> column index (1-based)."""
    out: dict[str, int] = {}
    for col in range(1, worksheet.max_column + 1):
        header = worksheet.cell(row=1, column=col).value
        if header not in (None, ""):
            out[str(header).strip()] = col
    return out


def column_by_condition_key(worksheet, key: str) -> int | None:
    for col in range(1, worksheet.max_column + 1):
        if condition_column(worksheet.cell(row=1, column=col).value) == key:
            return col
    return None


def write_journal_row(worksheet, row: int, metadata: dict[str, object]) -> None:
    """Write one journal row by EXACT header, apply display formulas, then
    overwrite the app-read columns (areal_mass_density, electrode_density)
    with Python literals so data_only reads return numbers immediately."""
    by_header = header_column_map(worksheet)
    for field in IMPORT_JOURNAL_FIELDS:
        value = metadata.get(field["key"])
        if value in (None, ""):
            continue
        col = by_header.get(field["header"])
        if col:
            worksheet.cell(row=row, column=col).value = value
    apply_row_formulas(worksheet, row)
    derived = compute_derived_metadata(metadata)
    for key in ("areal_mass_density", "electrode_density"):
        col = column_by_condition_key(worksheet, key)
        if col and derived.get(key) is not None:
            worksheet.cell(row=row, column=col).value = derived[key]


def append_journal_row(condition_workbook: Path, condition_sheet: str, metadata: dict[str, object]) -> int:
    condition_workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook = load_workbook(condition_workbook)
    if condition_sheet not in workbook.sheetnames:
        workbook.close()
        raise KeyError(f"Sheet not found: {condition_sheet}")
    worksheet = workbook[condition_sheet]
    row = worksheet.max_row + 1
    write_journal_row(worksheet, row, metadata)
    workbook.save(condition_workbook)
    workbook.close()
    return row
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::JournalWriterTests -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): exact-header journal writer with literal derived values"
```

---

### Task 5: Capacity 저장 명명 — 프로토콜 라벨

**Files:**
- Modify: `battery_lab/experiment_import.py:721-732` (`final_directory_for_item`, `final_filename_for_item`)
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# append to tests/test_import_journal_fields.py
from battery_lab.experiment_import import assignment_protocol_token


class ProtocolNamingTests(unittest.TestCase):
    def test_assignment_maps_to_human_protocol_token(self):
        self.assertEqual(assignment_protocol_token("capacity_1"), "0.1C")
        self.assertEqual(assignment_protocol_token("capacity_2"), "0.5C")
        self.assertEqual(assignment_protocol_token("capacity_3"), "rate per")
        self.assertEqual(assignment_protocol_token("eis_comparison"), "eis_comparison")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::ProtocolNamingTests -v`
Expected: FAIL — `cannot import name 'assignment_protocol_token'`

- [ ] **Step 3: 구현**

```python
# Human protocol tokens so capacity_matching.capacity_protocol_from_filename
# (looks for "rate per" / "0.5c" / "0.1c") and the data browser recognize
# imported capacity files exactly like legacy folders.
CAPACITY_PROTOCOL_TOKENS = {"capacity_1": "0.1C", "capacity_2": "0.5C", "capacity_3": "rate per"}


def assignment_protocol_token(assignment: str) -> str:
    return CAPACITY_PROTOCOL_TOKENS.get(assignment, assignment)
```

`final_directory_for_item`(721-726) 내 capacity 분기 교체:

```python
    if item.assignment.startswith("capacity_"):
        protocol = assignment_protocol_token(item.assignment)
        folder = safe_stem(f"{journal_row}_{sample}_{protocol}_cyc")
        return capacity_root / yymmdd / folder / long_metadata_date(metadata.get("date"))
```

`final_filename_for_item`(729-732) 교체:

```python
def final_filename_for_item(item: DraftImportFile, metadata: dict[str, object], journal_row: int, suffix: str) -> str:
    sample = safe_stem(str(metadata.get("sample") or item.cell_id or "sample"))
    if item.assignment.startswith("capacity_"):
        token = assignment_protocol_token(item.assignment)
    else:
        token = safe_stem(item.time_point) if item.time_point else item.assignment
    return safe_filename(f"{journal_row}_{sample}_{token}{suffix.lower()}")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::ProtocolNamingTests -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): name capacity files by protocol token for browser traceability"
```

---

## Phase 3 — row-unit 모델 + commit 루프

### Task 6: row-unit 그룹핑 + manifest `unit_metadata`

**Files:**
- Modify: `battery_lab/experiment_import.py:87-106` (`DraftImportManifest`), `:773-789` (`manifest_from_payload`), `:376-392` (`update_import_draft_metadata`)
- Test: `tests/test_import_journal_fields.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# append to tests/test_import_journal_fields.py
from dataclasses import replace
from battery_lab.experiment_import import DraftImportFile, unit_id_for_file, list_row_units


def _file(file_id, assignment, cell_id):
    return DraftImportFile(
        file_id=file_id, original_filename=file_id + ".dat", raw_path="", suffix=".seo",
        sha256="x", size_bytes=1, parser_kind="table", analysis_type="eis", cell_id=cell_id,
        normalized_rows=1, assignment=assignment, suggested_assignment=assignment,
        assignment_options=[], time_point="",
    )


class RowUnitTests(unittest.TestCase):
    def test_non_timeseries_each_file_is_its_own_unit(self):
        f1 = _file("f1", "eis_comparison", "A")
        f2 = _file("f2", "capacity_1", "B")
        self.assertEqual(unit_id_for_file(f1), "f1")
        self.assertEqual(unit_id_for_file(f2), "f2")

    def test_timeseries_files_group_by_cell_id(self):
        f1 = replace(_file("f1", "eis_time_series", "cellA"), time_point="0hr")
        f2 = replace(_file("f2", "eis_time_series", "cellA"), time_point="3hr")
        f3 = replace(_file("f3", "eis_time_series", "cellB"), time_point="0hr")
        units = list_row_units([f1, f2, f3])
        ids = {u["unit_id"]: u["file_ids"] for u in units}
        self.assertEqual(ids["ts__cellA"], ["f1", "f2"])
        self.assertEqual(ids["ts__cellB"], ["f3"])
        self.assertEqual(len(units), 2)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::RowUnitTests -v`
Expected: FAIL — `cannot import name 'unit_id_for_file'`

- [ ] **Step 3: 구현**

`DraftImportManifest`(87-106)에 필드 추가(`metadata`는 호환 위해 남겨두되 미사용):

```python
    unit_metadata: dict[str, object] | None = None
```

`manifest_from_payload`(773-789)에 추가:

```python
    data.setdefault("unit_metadata", {})
```

그룹핑 헬퍼 추가:

```python
def unit_id_for_file(item: DraftImportFile) -> str:
    if item.assignment == "eis_time_series":
        return f"ts__{item.cell_id or item.file_id}"
    return item.file_id


def list_row_units(files: list[DraftImportFile]) -> list[dict[str, object]]:
    """Group files into journal-row units (strict per-file, time-series by cell_id)."""
    order: list[str] = []
    groups: dict[str, list[DraftImportFile]] = {}
    for item in files:
        if item.assignment == "exclude":
            continue
        uid = unit_id_for_file(item)
        if uid not in groups:
            groups[uid] = []
            order.append(uid)
        groups[uid].append(item)
    units = []
    for uid in order:
        members = groups[uid]
        rep = members[0]
        units.append({
            "unit_id": uid,
            "file_ids": [m.file_id for m in members],
            "assignment": rep.assignment,
            "is_time_series": rep.assignment == "eis_time_series",
            "representative_filename": rep.original_filename,
            "filenames": [m.original_filename for m in members],
            "cell_id": rep.cell_id,
        })
    return units
```

`update_import_draft_metadata`(376-392) 교체 → unit별 저장:

```python
def update_import_draft_metadata(output_root: Path, draft_id: str, unit_id: str, metadata: dict[str, object]) -> DraftImportManifest:
    manifest = load_import_draft(output_root, draft_id)
    cleaned = clean_metadata(metadata)
    errors = validate_metadata(cleaned)
    unit_meta = dict(manifest.unit_metadata or {})
    unit_meta[unit_id] = {
        "metadata": cleaned,
        "metadata_status": "ready" if not errors else "invalid",
        "metadata_errors": errors,
    }
    updated = replace(manifest, unit_metadata=unit_meta, updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    write_manifest(updated, output_root / "import_drafts" / draft_id / "manifest.json")
    return updated
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py::RowUnitTests -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): row-unit grouping + per-unit manifest metadata"
```

---

### Task 7: commit을 unit 루프로 (N행, 1회 save)

**Files:**
- Modify: `battery_lab/experiment_import.py:395-456` (`commit_import_draft`), `:590-625` (`save_draft_files_to_final_locations`), `:628-689` (`write_commit_match_overrides`)
- Test: Task 11 (API 통합 테스트)에서 검증

- [ ] **Step 1: `commit_import_draft` 교체 (unit 루프 + 1회 워크북 save)**

```python
def commit_import_draft(output_root, draft_id, *, eis_root, capacity_root, condition_workbook, condition_sheet,
                        eis_match_override_path=None, capacity_match_override_path=None) -> DraftImportManifest:
    manifest = load_import_draft(output_root, draft_id)
    if manifest.commit_status == "committed":
        return manifest
    units = list_row_units(list(manifest.files))
    if not units:
        raise ValueError("No files selected for commit.")
    unit_meta = manifest.unit_metadata or {}
    # 1) validate ALL units up front (atomicity: no partial rows).
    for unit in units:
        entry = unit_meta.get(unit["unit_id"]) or {}
        if entry.get("metadata_status") != "ready":
            raise ValueError(f"Draft metadata must be ready for all units before commit (unit {unit['unit_id']}).")
    # 2) one workbook open -> append N rows -> one save.
    condition_workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook = load_workbook(condition_workbook)
    if condition_sheet not in workbook.sheetnames:
        workbook.close()
        raise KeyError(f"Sheet not found: {condition_sheet}")
    worksheet = workbook[condition_sheet]
    unit_rows: dict[str, int] = {}
    for unit in units:
        row = worksheet.max_row + 1
        write_journal_row(worksheet, row, (unit_meta[unit["unit_id"]] or {})["metadata"])
        unit_rows[unit["unit_id"]] = row
    workbook.save(condition_workbook)
    workbook.close()
    # 3) per-unit: save files + overrides (heavy rebuild stays once, below).
    saved_files = []
    match_overrides = []
    by_file = {item.file_id: item for item in manifest.files}
    for unit in units:
        row = unit_rows[unit["unit_id"]]
        meta = (unit_meta[unit["unit_id"]] or {})["metadata"]
        for file_id in unit["file_ids"]:
            item = by_file[file_id]
            saved_files.extend(save_one_file_to_final_location(item, meta, row, eis_root, capacity_root))
        match_overrides.extend(write_commit_match_overrides_for_row(
            manifest, journal_row=row, file_ids=unit["file_ids"], metadata=meta,
            eis_root=eis_root, capacity_root=capacity_root,
            condition_workbook=condition_workbook, condition_sheet=condition_sheet,
            eis_match_override_path=eis_match_override_path,
            capacity_match_override_path=capacity_match_override_path,
        ))
    persist_outputs = persist_commit_outputs(
        manifest, saved_files=saved_files, output_root=output_root, eis_root=eis_root, capacity_root=capacity_root,
        condition_workbook=condition_workbook, condition_sheet=condition_sheet,
        eis_match_override_path=eis_match_override_path, capacity_match_override_path=capacity_match_override_path,
    )
    committed = replace(
        manifest, commit_status="committed",
        committed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        journal_row=next(iter(unit_rows.values()), None), journal_rows=list(unit_rows.values()),
        saved_files=saved_files, match_overrides=match_overrides, persist_outputs=persist_outputs,
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    write_manifest(committed, output_root / "import_drafts" / draft_id / "manifest.json")
    return committed
```

> Add `journal_rows: list | None = None` to `DraftImportManifest` and `data.setdefault("journal_rows", [])` to `manifest_from_payload`. Keep `journal_row` (first row) for backward-compat with the existing test/JS.

- [ ] **Step 2: `save_draft_files_to_final_locations`를 단일 파일 버전으로 분해**

기존 함수를 보존하되, 한 파일 저장 헬퍼를 추가:

```python
def save_one_file_to_final_location(item, metadata, journal_row, eis_root, capacity_root) -> list[dict]:
    if item.assignment == "exclude":
        return []
    destination_dir = final_directory_for_item(item, metadata, journal_row, eis_root, capacity_root)
    destination_dir.mkdir(parents=True, exist_ok=True)
    raw_source = Path(item.raw_path)
    raw_target = collision_safe_path(destination_dir / final_filename_for_item(item, metadata, journal_row, raw_source.suffix))
    shutil.copy2(raw_source, raw_target)
    row = {"file_id": item.file_id, "assignment": item.assignment, "journal_row": journal_row,
           "source_path": str(raw_source), "saved_path": str(raw_target), "processed_saved_path": ""}
    if item.processed_path:
        processed_source = Path(item.processed_path)
        processed_target = collision_safe_path(destination_dir / f"{raw_target.stem}_{processed_source.name}")
        shutil.copy2(processed_source, processed_target)
        row["processed_saved_path"] = str(processed_target)
    return [row]
```

- [ ] **Step 3: `write_commit_match_overrides`를 per-row 버전으로**

기존 `write_commit_match_overrides`(628-689)를 참고해 한 행/일부 file_ids만 처리하는 `write_commit_match_overrides_for_row(...)`를 추가(시그니처는 위 commit 호출과 일치). 기존 함수의 루프 본문을 `saved_files`를 해당 file_ids로 필터링하도록 좁히고 `journal_row`/`metadata`를 인자로 받게 한다. (기존 `condition_for_journal_row`, `override_source_path_for_saved_file`, `relative_to_root` 재사용.)

- [ ] **Step 4: 전체 단위 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_import_journal_fields.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat(import): commit creates one journal row per unit (single workbook save)"
```

---

## Phase 4 — routes

### Task 8: field-spec 엔드포인트 + unit별 metadata PATCH + 정규화명 미리보기

**Files:**
- Modify: `battery_lab/routes.py:249-289` (metadata-options/metadata/cluster-preview 라우트)
- Modify: `battery_lab/experiment_import.py` (`metadata_options_from_conditions` 정리, 정규화명 예측 헬퍼)
- Test: Task 11

- [ ] **Step 1: field-spec 엔드포인트 추가 (`routes.py`)**

```python
from .experiment_import import IMPORT_JOURNAL_FIELDS, BINDER_PRESETS  # add to imports

@blueprint.get("/api/import/field-spec")
def import_field_spec_api():
    return jsonify({"ok": True, "fields": IMPORT_JOURNAL_FIELDS, "binder_presets": BINDER_PRESETS})
```

- [ ] **Step 2: metadata PATCH를 unit_id 인자로 (`routes.py:266-277`)**

```python
@blueprint.patch("/api/import/drafts/<draft_id>/units/<unit_id>/metadata")
def update_import_draft_metadata_api(draft_id: str, unit_id: str):
    payload = request.get_json(silent=True) or {}
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        return jsonify({"ok": False, "error": "metadata must be an object."}), 400
    try:
        manifest = update_import_draft_metadata(BATTERY_OUTPUT_ROOT, draft_id, unit_id, metadata)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    entry = (manifest.unit_metadata or {}).get(unit_id) or {}
    response = _import_draft_payload(manifest)
    return jsonify({"ok": entry.get("metadata_status") == "ready", "unit_id": unit_id, **response})
```

- [ ] **Step 3: 정규화명 미리보기 엔드포인트**

`experiment_import.py`에 예측 헬퍼:

```python
def preview_normalized_names(manifest: DraftImportManifest, condition_workbook: Path, condition_sheet: str) -> list[dict]:
    """Predict raw -> normalized filename per file using sequential next rows."""
    next_row = 1
    if condition_workbook.exists():
        wb = load_workbook(condition_workbook); ws = wb[condition_sheet]; next_row = ws.max_row + 1; wb.close()
    units = list_row_units(list(manifest.files))
    unit_meta = manifest.unit_metadata or {}
    by_file = {item.file_id: item for item in manifest.files}
    rows = []
    for offset, unit in enumerate(units):
        row = next_row + offset
        meta = (unit_meta.get(unit["unit_id"]) or {}).get("metadata") or {}
        for file_id in unit["file_ids"]:
            item = by_file[file_id]
            normalized = final_filename_for_item(item, meta, row, Path(item.raw_path).suffix)
            rows.append({"unit_id": unit["unit_id"], "file_id": file_id,
                         "raw_name": item.original_filename, "normalized_name": normalized,
                         "predicted_row": row, "assignment": item.assignment})
    return rows
```

`routes.py`에 엔드포인트:

```python
@blueprint.get("/api/import/drafts/<draft_id>/normalized-names")
def import_draft_normalized_names_api(draft_id: str):
    try:
        manifest = load_import_draft(BATTERY_OUTPUT_ROOT, draft_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Draft manifest not found."}), 404
    rows = preview_normalized_names(manifest, BATTERY_CONDITION_WORKBOOK, DEFAULT_CONDITION_SHEET)
    return jsonify({"ok": True, "rows": rows})
```

- [ ] **Step 4: cluster-preview를 unit metadata 기반으로**

`build_import_draft_cluster_preview`(840-859)/`cluster_preview_row`(862-886)가 `manifest.metadata`(단일)를 참조 → unit별 metadata를 사용하도록 수정. 각 file의 unit_id로 `unit_metadata[uid]["metadata"]`를 찾아 매칭. status는 기존 `matched_existing_cluster`/`new_independent_cluster` 유지.

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `.venv/bin/python -m pytest tests/test_experiment_import.py -v` (Task 11 후 GREEN)
```bash
git add -A && git commit -m "feat(import): field-spec, per-unit metadata, normalized-name preview routes"
```

---

## Phase 5 — 프론트엔드 (`app.html`)

> 프론트는 단위 테스트 대신 **브라우저 수동 검증**. 각 Task 끝에 Preview MCP 또는 로컬 실행으로 확인.
> 로컬 실행: `cd battery-lab-automation && .venv/bin/python -m battery_lab.flask_app` (또는 프로젝트의 실행 스킬) → `/battery` 실험일지 → '새 실험 등록'.

### Task 9: 업로드 안내문구 + 삭제 필드 제거

**Files:** Modify `battery_lab/templates/battery_lab/app.html:580-611`

- [ ] **Step 1:** 업로드 영역(`battery-import-options` 위, `:580`)에 안내 추가:

```html
<div class="battery-match-message" style="color:#9a3412;font-weight:600">
  ⚠ EIS 시계열 데이터의 경우 파일명 내에 <code>_hr</code> 표기를 반드시 포함해 주십시오. (예: <code>..._3hr_02.SEO</code>)
</div>
```

- [ ] **Step 2:** 2단계 폼(`:604-607`)에서 `sample group`/`material family`/`treatment`/`note` `<label>` 4줄 삭제. `:611` "과거 조건값을 빠르게 선택할 수 있습니다." 메시지 삭제.

- [ ] **Step 3:** 브라우저로 업로드 화면 확인 — 안내문구 노출, 삭제 필드 사라짐.

- [ ] **Step 4: 커밋**

```bash
git add battery_lab/templates/battery_lab/app.html
git commit -m "feat(import-ui): _hr notice + remove unused metadata fields"
```

---

### Task 10: row-unit 아코디언 폼 (가변 4 + 고정 토글 12 + Binder + Date 자동)

**Files:** Modify `app.html` — 2단계 패널(`:590-616`) + JS(`:658-1016`)

- [ ] **Step 1: 업로드 시 파일별 `lastModified` 캡처**

`uploadFiles`(`:781`)에서 `body.append("files", file)` 직후 각 파일의 `file.lastModified`를 함께 전송(예: `body.append("last_modified", JSON.stringify(list.map(f=>({name:f.name, ms:f.lastModified}))))`). 서버 `create_import_draft_api`/`build_draft_file`에서 받아 `DraftImportFile`에 `source_mtime_ms` 저장. JS에서 `YYMMDD` 변환:

```js
function ymdFromMs(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const yy = String(d.getFullYear()).slice(2);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yy}${mm}${dd}`;
}
```

- [ ] **Step 2: field-spec 로드 + 아코디언 렌더**

`loadMetadataOptions`를 `loadFieldSpec`로 교체 — `/api/import/field-spec` fetch 후 `fieldSpec` 보관. 2단계 진입 시 `list_row_units` 대응 데이터(서버 payload의 `units` — Task 6에서 `_import_draft_payload`에 `units: list_row_units(files)` 추가)를 순회하며 아코디언 항목 렌더:

```js
function renderUnitForms() {
  previewBoxes.innerHTML = "";
  (currentPayload.units || []).forEach((unit, idx) => {
    const item = document.createElement("div");
    item.className = "biw-unit";
    item.innerHTML = `
      <button type="button" class="biw-unit-head" data-acc="${idx}">
        ${idx === 0 ? "▾" : "▸"} ${escapeHtml(unit.representative_filename)}
        ${unit.is_time_series ? `· 시계열 ${unit.file_ids.length}개` : ""} <span class="biw-chip">${escapeHtml(assignmentLabel(unit.assignment))}</span>
      </button>
      <div class="biw-unit-body" data-body="${idx}" ${idx === 0 ? "" : "hidden"}>
        ${variableFieldsHtml(unit)}
        <button type="button" class="biw-toggle" data-fixed="${idx}">고정값 보기 ▸</button>
        <div class="biw-fixed" data-fixedbody="${idx}" hidden>${fixedFieldsHtml()}</div>
      </div>`;
    previewBoxes.appendChild(item);
  });
  bindAccordion(); prefillDates();
}
```

`variableFieldsHtml(unit)` → date/sample/foil_electrode_g/foil_electrode_mm 입력(다른 `name`은 `u{idx}__{key}`로 네임스페이스). `fixedFieldsHtml()` → 고정 12칸을 `fieldSpec`의 default로 채움; Binder는 `<select>`(BINDER_PRESETS) + "직접입력" 옵션 시 `<input>` 토글.

- [ ] **Step 3: 아코디언/토글/Date prefill 바인딩**

`bindAccordion()` — `data-acc` 클릭 시 해당 body만 표시(한 번에 하나). `data-fixed` 클릭 시 `data-fixedbody` 토글. `prefillDates()` — 각 unit의 대표 파일 `source_mtime_ms`로 date input 기본값 채움(비어있을 때만).

- [ ] **Step 4: unit별 저장 — '최종 확인' 시 모든 unit PATCH**

`gotoStep3`(`:968`)을 교체: 각 unit 폼 값을 모아 `PATCH /units/{unit_id}/metadata` 순차 호출(또는 Promise.all). 하나라도 `ok=false`면 해당 아코디언을 펼치고 에러 표시, 중단.

- [ ] **Step 5: CSS 추가** (`<style>` 영역, `:73` 부근)

```css
.biw-unit { border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; }
.biw-unit-head { width:100%; text-align:left; padding:10px; background:#f8fafc; border:0; cursor:pointer; font-size:13px; }
.biw-unit-body { padding:12px; }
.biw-toggle { margin-top:8px; background:none; border:0; color:#2563eb; cursor:pointer; }
.biw-fixed { margin-top:8px; padding-top:8px; border-top:1px dashed #e2e8f0; }
```

- [ ] **Step 6: 브라우저 검증 + 커밋**

확인: 파일 N개 업로드 → 아코디언 N항목(시계열은 1항목으로 묶임), Date 자동, 고정값 토글, Binder 드롭다운. 
```bash
git add battery_lab/templates/battery_lab/app.html battery_lab/experiment_import.py battery_lab/routes.py
git commit -m "feat(import-ui): per-unit accordion forms with fixed-value toggle"
```

---

### Task 11: 3단계 — 클러스터 배정 + raw→정규화 파일명

**Files:** Modify `app.html` 3단계(`:618-628`) + JS `renderConfirm`(`:903-919`)

- [ ] **Step 1:** 커밋 전 `/normalized-names` fetch → 파일별 `raw → normalized` 표기:

```js
async function renderConfirm() {
  const res = await fetch(`${uploadUrl}/${encodeURIComponent(currentPayload.draft_id)}/normalized-names`);
  const data = await res.json().catch(() => ({rows: []}));
  confirmFiles.innerHTML = (data.rows || []).map((r) => `
    <div class="biw-confirm-file">
      <span class="biw-cf-name">${escapeHtml(r.raw_name)} <b>→</b> ${escapeHtml(r.normalized_name)}</span>
      <span class="biw-chip">${escapeHtml(assignmentLabel(r.assignment))}</span>
      <span class="biw-chip">${escapeHtml(clusterLabelFor(r.unit_id))}</span>
    </div>`).join("") || '<div class="biw-empty">저장할 파일이 없습니다.</div>';
}
```

- [ ] **Step 2:** `loadClusterPreview`를 unit 기준으로 — `clusterLabelFor(unit_id)`가 `matched_existing_cluster`/`new_independent_cluster` 라벨 반환. 3단계 상단에 unit별 행/클러스터 요약 표시.

- [ ] **Step 3:** 커밋 성공 메시지(`:1009`)를 N행으로: `저장 완료 · 새 행 ${(payload.journal_rows||[]).length}개 · 파일 ${(payload.saved_files||[]).length}개`.

- [ ] **Step 4: 브라우저 검증 + 커밋**

확인: 3단계에서 `raw → 정규화` 표기, 각 파일 클러스터 칩.
```bash
git add battery_lab/templates/battery_lab/app.html
git commit -m "feat(import-ui): step3 cluster assignment + raw→normalized filenames"
```

---

## Phase 6 — 통합 테스트 재작성 + 그래프 검증

### Task 12: `test_experiment_import.py` per-unit 재작성

**Files:** Modify `tests/test_experiment_import.py:100-233`

- [ ] **Step 1:** `test_import_draft_api_accepts_multipart_upload`을 신규 계약으로 수정:
  - metadata PATCH URL → `/units/{unit_id}/metadata`, unit_id = 단일 capacity 파일이므로 `file_id`.
  - metadata payload → 신규 스키마(`date,sample,foil_electrode_g,foil_electrode_mm,foil_g,ratio` 등; `areal_mass_density` 제거).
  - 워크북 헤더(`:155`)를 실제 JYJ 헤더 33개 부분집합으로(최소 `참고,전해질,종류,Date,Sample,Conductive agent,Binder,Voltage range,foil+electrode (g),foil (g),ratio,Current density (mA/g),Areal mass density (mg/cｍ2),전극(foil+electrode) 두께(mm),호일 두께(mm),합제밀도(g/cm3)`).
  - commit 후 검증: `journal_rows == [2]`, 저장 파일명에 `rate per`(capacity_3) 포함(`capacity_3_cyc` 아님), 워크북 row2의 Sample/Date/종류, **Areal mass density(col)이 float**.

- [ ] **Step 2:** 새 테스트 추가 — 시계열 2파일 업로드 → 1 unit → commit 시 1행, 2파일 모두 같은 행 저장:

```python
def test_timeseries_two_hr_files_make_one_row(self):
    # upload two _hr SEO files for the same cell -> list_row_units => 1 unit
    # assign eis_time_series, PATCH unit metadata, commit -> journal_rows length 1
    ...  # mirror the multipart test setup with two files "cellA_0hr_01.SEO", "cellA_3hr_01.SEO"
```

- [ ] **Step 3: 실행**

Run: `.venv/bin/python -m pytest tests/test_experiment_import.py -v`
Expected: PASS

- [ ] **Step 4: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 기존 통과 테스트 유지(회귀 없음). 실패 시 `grep -rn REQUIRED_METADATA_FIELDS battery_lab/`로 잔존 참조 정리.

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "test(import): rewrite for per-unit rows + new schema"
```

---

### Task 13: 저장 후 그래프 생성 검증 (수동 + 자동)

**Files:** Create assertion in integration test or manual checklist

- [ ] **Step 1 (자동):** 통합 테스트에서 commit 후 `persist_outputs`의 `plot` 항목 `ok=True` + artifact 경로 존재 assert (기존 `persist_by_kind` 패턴 활용).

- [ ] **Step 2 (수동):** 로컬 실행 → 실제 capacity/EIS 파일로 '새 실험 등록' 전 과정 → 저장 후:
  - `battery_visual_outputs/capacity/`·`/eis/`에 새 행 artifact(SVG) 생성 확인.
  - **데이터 분석 탭**에서 새 그래프가 목록/뷰어에 실제로 추가되는지 확인.
  - **데이터 브라우저**에서 새 capacity 파일이 `0.1C/0.5C/rate per` 프로토콜·선두 행번호로 올바르게 그룹/인식되는지 확인(2번 역추적).

- [ ] **Step 3: 커밋**

```bash
git add -A && git commit -m "test(import): assert graph artifacts generated on commit"
```

---

## Self-Review (작성자 체크 결과)

- **Spec coverage:** 결정①~⑥ + 요청1~4 모두 Task로 매핑됨 — ①Task10, ②Task10, ③Task4, ④Task10, ⑤Task6, ⑥Task5; 요청1(1:1/안내)Task9, 요청2(역추적)Task5+Task13, 요청3(raw→정규화)Task11, 요청4(클러스터+그래프)Task11+Task13. 예상문제 A~J 모두 Task로 커버.
- **Placeholder scan:** Task 7-Step3(`write_commit_match_overrides_for_row`)와 Task 12-Step2 시계열 테스트는 기존 함수 패턴 재사용 지시로 코드 골격만 제시 — 실행자는 인접 기존 코드(`write_commit_match_overrides` 628-689)를 그대로 좁혀 구현. 그 외 단계는 완전한 코드 포함.
- **Type consistency:** `unit_id_for_file`/`list_row_units`/`unit_metadata`/`journal_rows`/`compute_derived_metadata`/`assignment_protocol_token`/`write_journal_row` 시그니처가 Task 간 일치. `update_import_draft_metadata`는 `(output_root, draft_id, unit_id, metadata)`로 통일(Task6 정의 = Task8 호출).
- **알려진 주의:** `update_import_draft_metadata`(현 `routes.py` 호출부 포함)·`metadata_options_from_conditions`·`REQUIRED_METADATA_FIELDS`의 잔존 참조를 Task 3 NOTE대로 정리해야 import 에러가 없음 — 실행 시작 시 `grep -rn 'REQUIRED_METADATA_FIELDS\|update_import_draft_metadata' battery_lab/`로 일괄 확인.

---

## Execution Handoff

계획 저장 완료. 실행 방식 2가지:

1. **Subagent-Driven (권장)** — Task마다 새 서브에이전트 디스패치, Task 사이 리뷰, 빠른 반복.
2. **Inline Execution** — 이 세션에서 executing-plans로 체크포인트 배치 실행.
