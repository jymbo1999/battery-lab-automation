import unittest

from battery_lab.excel_dashboard import render_page
from battery_lab.flask_app import create_app


class ExcelDashboardRowFeaturesTests(unittest.TestCase):
    def test_render_page_wires_row_selection_and_popup(self):
        html = render_page(
            sheet_api_url="/s",
            cell_api_url="/c",
            row_types_api_url="/rt",
            row_detail_api_url="/rd",
        )
        # Row-number click selection + tooltip + popup hooks are present.
        self.assertIn("row-head-cell", html)
        self.assertIn("handleRowHeadClick", html)
        self.assertIn("selectWholeRow", html)
        self.assertIn("applyRowTooltip", html)
        self.assertIn("openRowPopup", html)
        self.assertIn("rowPopup", html)
        # API urls are substituted into the page (json-encoded).
        self.assertIn('"/rt"', html)
        self.assertIn('"/rd"', html)
        # No unreplaced placeholders remain.
        self.assertNotIn("__ROW_TYPES_API_URL__", html)
        self.assertNotIn("__ROW_DETAIL_API_URL__", html)


class JournalRowApiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app({"TESTING": True}).test_client()

    def test_journal_excel_page_references_new_endpoints(self):
        resp = self.client.get("/battery/journal/excel")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("/api/journal/row-types", html)
        self.assertIn("/api/journal/row-detail", html)

    def test_row_types_endpoint_returns_mapping(self):
        resp = self.client.get("/battery/api/journal/row-types")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertIn("row_types", payload)
        self.assertIsInstance(payload["row_types"], dict)
        # Every value must be a list of type strings.
        for types in payload["row_types"].values():
            self.assertIsInstance(types, list)

    def test_row_detail_requires_numeric_row(self):
        resp = self.client.get("/battery/api/journal/row-detail?row=abc")
        self.assertEqual(resp.status_code, 400)

    def test_row_detail_returns_expected_shape(self):
        resp = self.client.get("/battery/api/journal/row-detail?row=1")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload.get("row"), 1)
        for key in ("types", "previews", "info_fields"):
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], list)


if __name__ == "__main__":
    unittest.main()
