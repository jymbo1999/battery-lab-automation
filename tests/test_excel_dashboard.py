import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from battery_lab.excel_dashboard import WorkbookStore, build_sheet_payload, render_page


def make_formula_workbook() -> Workbook:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "JYJ"
    headers = {
        8: "Current (A)",
        9: "Active material (g)",
        16: "전극+Cu foil (g)",
        17: "Cu foil (g)",
        18: "활물질 비율",
        19: "용량",
        20: "Areal mass density (mg/cｍ2)",
        21: "전극(foil+electrode) 두께(mm)",
        22: "foil 두께(mm)",
        23: "전극 두께(mm)",
        24: "electrode(g)",
        25: "volume (mm3)",
        26: "합제밀도(g/cm3)",
    }
    for column, header in headers.items():
        worksheet.cell(row=1, column=column, value=header)
    worksheet["P2"] = 0.020
    worksheet["Q2"] = 0.010
    worksheet["R2"] = 0.8
    worksheet["S2"] = 186
    worksheet["U2"] = 0.08
    worksheet["V2"] = 0.01
    return workbook


class ExcelDashboardTests(unittest.TestCase):
    def test_sheet_payload_preserves_values_dimensions_and_styles(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "JYJ"
        worksheet["A1"] = "참고"
        worksheet["A1"].font = Font(bold=True, color="FFFFFFFF")
        worksheet["A1"].fill = PatternFill("solid", fgColor="FF4472C4")
        worksheet["B2"] = "merged"
        worksheet.merge_cells("B2:C3")
        worksheet.column_dimensions["A"].width = 15
        worksheet.row_dimensions[1].height = 27

        payload = build_sheet_payload(worksheet, Path("conditions.xlsx"))

        self.assertEqual(payload["sheet"], "JYJ")
        self.assertGreater(payload["columns"][0]["width"], 100)
        self.assertEqual(payload["rows"][0]["height"], 36)
        self.assertEqual(payload["rows"][0]["cells"][0]["value"], "참고")
        self.assertTrue(payload["rows"][0]["cells"][0]["style"]["bold"])
        self.assertEqual(payload["rows"][0]["cells"][0]["style"]["backgroundColor"], "#4472C4")
        merged_cell = next(cell for cell in payload["rows"][1]["cells"] if cell["address"] == "B2")
        self.assertEqual((merged_cell["rowspan"], merged_cell["colspan"]), (2, 2))

    def test_sheet_payload_marks_rows_outside_condition_filter(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "JYJ"
        headers = ["참고", "전해질", "종류", "Binder", "Voltage range"]
        for column, header in enumerate(headers, start=1):
            worksheet.cell(row=1, column=column, value=header)
        valid = ["12 파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "2wt% cmc", "0.01~2V"]
        invalid = ["12 파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "5wt% pvdf/nmp", "0.01~2V"]
        for column, value in enumerate(valid, start=1):
            worksheet.cell(row=2, column=column, value=value)
        for column, value in enumerate(invalid, start=1):
            worksheet.cell(row=3, column=column, value=value)

        payload = build_sheet_payload(worksheet, Path("conditions.xlsx"))

        self.assertFalse(payload["rows"][1]["ignored"])
        self.assertTrue(payload["rows"][2]["ignored"])
        self.assertEqual(payload["filter"]["matchedRows"], 1)
        self.assertEqual(payload["filter"]["ignoredRows"], 1)

    def test_store_updates_workbook_cell_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conditions.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "JYJ"
            worksheet["A1"] = "Sample"
            workbook.save(path)

            store = WorkbookStore(path, "JYJ")
            result = store.update_cell(1, 1, "updated")

            self.assertEqual(result["value"], "updated")
            saved = load_workbook(path)
            self.assertEqual(saved["JYJ"]["A1"].value, "updated")
            saved.close()

    def test_sheet_payload_computes_excel_style_formula_columns(self):
        workbook = make_formula_workbook()
        worksheet = workbook.active

        payload = build_sheet_payload(worksheet, Path("conditions.xlsx"))

        row = payload["rows"][1]
        cells = {cell["address"]: cell for cell in row["cells"]}
        self.assertEqual(cells["I2"]["formula"], "=(P2-Q2)*R2")
        self.assertTrue(cells["I2"]["formulaCell"])
        self.assertFalse(cells["I2"]["editable"])
        self.assertAlmostEqual(float(cells["I2"]["value"]), 0.008)
        self.assertAlmostEqual(float(cells["H2"]["value"]), 0.001488)
        self.assertAlmostEqual(float(cells["T2"]["value"]), 0.008 * 1000 / (3.141592653589793 * 0.6 * 0.6))
        self.assertAlmostEqual(float(cells["W2"]["value"]), 0.07)
        self.assertAlmostEqual(float(cells["X2"]["value"]), 0.01)
        self.assertAlmostEqual(float(cells["Y2"]["value"]), 113.1 * 0.07)
        self.assertAlmostEqual(float(cells["Z2"]["value"]), 0.01 / ((113.1 * 0.07) / 1000))

    def test_store_rewrites_dependent_formula_columns_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conditions.xlsx"
            workbook = make_formula_workbook()
            workbook.save(path)

            store = WorkbookStore(path, "JYJ")
            store.update_cell(2, 16, "0.03")

            saved = load_workbook(path, data_only=False)
            sheet = saved["JYJ"]
            self.assertEqual(sheet["H2"].value, "=I2*S2*1000/10^6")
            self.assertEqual(sheet["I2"].value, "=(P2-Q2)*R2")
            self.assertEqual(sheet["T2"].value, "=I2*1000/(PI()*(0.6)^2)")
            self.assertEqual(sheet["W2"].value, "=U2-V2")
            self.assertEqual(sheet["X2"].value, "=P2-Q2")
            self.assertEqual(sheet["Y2"].value, "=113.1*W2")
            self.assertEqual(sheet["Z2"].value, "=X2/(Y2/1000)")
            saved.close()

    def test_render_page_contains_editable_grid_hooks(self):
        page = render_page()

        self.assertIn("/api/sheet", page)
        self.assertIn("/api/cell", page)
        self.assertIn("contentEditable", page)
        self.assertIn("filterMode", page)
        self.assertIn("실험 일지", page)
        self.assertIn("무시 행 표시안함", page)
        self.assertIn("무시 행 회색", page)
        self.assertIn("필터 기준", page)
        self.assertIn("header-row", page)
        self.assertIn("zoomOut", page)
        self.assertIn("zoomIn", page)
        self.assertIn("state.zoom", page)
        self.assertIn("formula-cell", page)
        self.assertIn("dataset.formula", page)


if __name__ == "__main__":
    unittest.main()
