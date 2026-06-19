import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from openpyxl import Workbook, load_workbook

from battery_lab import register_battery_lab
from battery_lab import routes


class BatteryRouteTests(unittest.TestCase):
    def make_client(self, root: Path):
        app = Flask(__name__)
        app.secret_key = "test"
        register_battery_lab(app)
        patches = [
            patch.object(routes, "BATTERY_DATA_ROOT", root),
            patch.object(routes, "BATTERY_EIS_ROOT", root / "EIS"),
            patch.object(routes, "BATTERY_CAPACITY_ROOT", root / "capacity"),
            patch.object(routes, "BATTERY_OUTPUT_ROOT", root / "battery_visual_outputs"),
            patch.object(routes, "BATTERY_CONDITION_WORKBOOK", root / "Project_Abstract" / "Cell condition Calculation.xlsx"),
            patch.object(routes, "BATTERY_MATCH_EIS_JSON", root / "battery_visual_outputs" / "eis_match_overrides.json"),
            patch.object(routes, "BATTERY_MATCH_CAPACITY_JSON", root / "battery_visual_outputs" / "capacity_match_overrides.json"),
            patch.object(routes, "BATTERY_STREAMLIT_URL", ""),
        ]
        return app.test_client(), patches

    def patched_client(self, root: Path):
        client, patches = self.make_client(root)
        stack = ExitStack()
        for item in patches:
            stack.enter_context(item)
        stack.enter_context(client)
        return client, stack

    def test_named_routes_render_and_graph_routes_select_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            (root / "EIS").mkdir(parents=True)
            (root / "capacity").mkdir(parents=True)
            (root / "Project_Abstract").mkdir(parents=True)
            eis_dir = root / "battery_visual_outputs" / "eis"
            capacity_dir = root / "battery_visual_outputs" / "capacity"
            eis_dir.mkdir(parents=True)
            capacity_dir.mkdir(parents=True)
            (eis_dir / "b.svg").write_text("<svg/>", encoding="utf-8")
            (capacity_dir / "c.svg").write_text("<svg/>", encoding="utf-8")

            client, patches = self.make_client(root)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                for path in [
                    "/battery/",
                    "/battery/journal",
                    "/battery/eis",
                    "/battery/capacity",
                    "/battery/files",
                    "/battery/jobs",
                    "/battery/settings",
                    "/battery/status",
                    "/battery/health",
                    "/battery/api/eis/finder",
                    "/battery/api/capacity/finder",
                    "/battery/api/capacity/viewer/source?key=missing.wrd",
                ]:
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)

                eis_response = client.get("/battery/eis?graph=b.svg")
                self.assertIn("b.svg", eis_response.get_data(as_text=True))
                self.assertIn("/battery/artifact/eis/b.svg", eis_response.get_data(as_text=True))
                self.assertIn("원본 EIS 라이브 뷰어", eis_response.get_data(as_text=True))
                self.assertIn("/battery/api/eis/finder", eis_response.get_data(as_text=True))

                capacity_response = client.get("/battery/capacity?graph=c.svg")
                self.assertIn("c.svg", capacity_response.get_data(as_text=True))
                self.assertIn("/battery/artifact/capacity/c.svg", capacity_response.get_data(as_text=True))
                self.assertIn("WRD/raw source preview", capacity_response.get_data(as_text=True))
                self.assertIn("/battery/api/capacity/finder", capacity_response.get_data(as_text=True))

    def test_legacy_query_tab_redirects_to_named_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            client, patches = self.make_client(root)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                response = client.get("/battery/?tab=eis&eis=sample.svg")

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/battery/eis?graph=sample.svg")

    def test_journal_excel_routes_read_and_save_workbook_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            condition_path = root / "Project_Abstract" / "Cell condition Calculation.xlsx"
            condition_path.parent.mkdir(parents=True)
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "JYJ"
            worksheet.append(["Sample", "date", "Binder"])
            worksheet.append(["before", "260101", "2wt%cmc"])
            workbook.save(condition_path)

            client, stack = self.patched_client(root)
            with stack:
                journal = client.get("/battery/journal").get_data(as_text=True)
                self.assertIn("/battery/journal/excel", journal)

                excel = client.get("/battery/journal/excel").get_data(as_text=True)
                self.assertIn("/battery/api/journal/sheet", excel)
                self.assertIn("/battery/api/journal/cell", excel)

                sheet = client.get("/battery/api/journal/sheet").get_json()
                self.assertEqual(sheet["sheet"], "JYJ")
                self.assertEqual(sheet["rows"][1]["cells"][0]["value"], "before")

                response = client.post("/battery/api/journal/cell", json={"row": 2, "column": 1, "value": "after"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["ok"])

            reloaded = load_workbook(condition_path, data_only=False)
            self.assertEqual(reloaded["JYJ"].cell(row=2, column=1).value, "after")
            reloaded.close()

    def test_jobs_api_reports_unavailable_without_main_app_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            client, stack = self.patched_client(root)
            with stack:
                response = client.get("/battery/api/jobs")

            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.get_json()["available"])

    def test_ai_status_api_reports_policy_without_main_app_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            client, stack = self.patched_client(root)
            with stack:
                response = client.get("/battery/api/ai/status")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertFalse(payload["available"])
            self.assertIn("policy", payload)
            self.assertEqual(payload["policy"]["default_call_mode"], "dry_run")

    def test_eis_match_api_saves_streamlit_compatible_override_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            eis_file = root / "EIS" / "260521" / "0hr" / "1.5act 3T_01.SEO"
            eis_file.parent.mkdir(parents=True)
            eis_file.write_text("placeholder", encoding="utf-8")
            condition_path = root / "Project_Abstract" / "conditions.csv"
            condition_path.parent.mkdir(parents=True)
            condition_path.write_text(
                "Sample,date,Binder,Voltage range,ratio,Areal mass density\n"
                "1.5 act 3T_1,260521,CMC/SBR,0.01~2V,0.95,7.1\n"
                "1.5 act 3T_2,260521,CMC/SBR,0.01~2V,0.95,7.1\n",
                encoding="utf-8",
            )
            override_path = root / "battery_visual_outputs" / "eis_match_overrides.json"

            client, stack = self.patched_client(root)
            with stack, patch.object(routes, "BATTERY_CONDITION_WORKBOOK", condition_path), patch.object(routes, "BATTERY_MATCH_EIS_JSON", override_path):
                payload = client.get("/battery/api/eis/matches").get_json()
                self.assertTrue(payload["report_ready"])
                self.assertGreaterEqual(len(payload["rows"]), 2)
                selected = next(row for row in payload["rows"] if row["condition_key"] == "1.5 act 3T_2")

                response = client.post("/battery/api/eis/matches", json={"selections": [selected]})
                self.assertEqual(response.status_code, 200)

                data = json.loads(override_path.read_text(encoding="utf-8"))
                self.assertEqual(data["260521/0hr/1.5act 3T_01.SEO"]["condition_key"], "1.5 act 3T_2")
                self.assertEqual(data["260521/0hr/1.5act 3T_01.SEO"]["sample"], "1.5 act 3T_2")
                self.assertIn("selected_at", data["260521/0hr/1.5act 3T_01.SEO"])

                clear_response = client.delete("/battery/api/eis/matches")
                self.assertEqual(clear_response.status_code, 200)
                self.assertEqual(json.loads(override_path.read_text(encoding="utf-8")), {})

    def test_capacity_match_api_saves_override_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "battery"
            capacity_file = root / "capacity" / "260522" / "1.5act_3T_0.5C_024_Capacity.csv"
            capacity_file.parent.mkdir(parents=True)
            capacity_file.write_text("placeholder", encoding="utf-8")
            condition_path = root / "Project_Abstract" / "conditions.csv"
            condition_path.parent.mkdir(parents=True)
            condition_path.write_text(
                "Sample,date,Areal mass density\n"
                "1.5 act 3T,260522,7.1\n"
                "1.5act 3T,260522,7.1\n",
                encoding="utf-8",
            )
            override_path = root / "battery_visual_outputs" / "capacity_match_overrides.json"

            client, stack = self.patched_client(root)
            with stack, patch.object(routes, "BATTERY_CONDITION_WORKBOOK", condition_path), patch.object(routes, "BATTERY_MATCH_CAPACITY_JSON", override_path):
                payload = client.get("/battery/api/capacity/matches").get_json()
                self.assertTrue(payload["report_ready"])
                selected = next(row for row in payload["rows"] if row["condition_key"] == "1.5 act 3T")

                response = client.post("/battery/api/capacity/matches", json={"selections": [selected]})
                self.assertEqual(response.status_code, 200)

                data = json.loads(override_path.read_text(encoding="utf-8"))
                self.assertEqual(data["260522/1.5act_3T_0.5C_024_Capacity.csv"]["condition_key"], "1.5 act 3T")


if __name__ == "__main__":
    unittest.main()
