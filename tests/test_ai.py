import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy


class BatteryAiTests(unittest.TestCase):
    def test_ai_smoke_creates_dry_run_without_openai_call(self):
        db = SQLAlchemy()
        fake_apps = types.ModuleType("apps")
        fake_apps.db = db
        previous_apps = sys.modules.get("apps")
        sys.modules["apps"] = fake_apps
        try:
            job_models = importlib.import_module("battery_lab.job_models")
            if getattr(job_models, "db", None) is not db:
                job_models = importlib.reload(job_models)
            ai_service = importlib.import_module("battery_lab.ai_service")
            if getattr(ai_service, "db", None) is not db:
                ai_service = importlib.reload(ai_service)

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            with tempfile.TemporaryDirectory() as tmp, app.app_context():
                output_root = Path(tmp) / "battery_visual_outputs"
                output_root.mkdir()
                (output_root / "summary_metrics.csv").write_text(
                    "metric,value\n"
                    "eis_files,2\n"
                    "capacity_files,3\n",
                    encoding="utf-8",
                )

                result = ai_service.run_ai_smoke(output_root, call_api=False)

                self.assertTrue(result["available"])
                self.assertEqual(result["run"]["status"], "dry_run")
                self.assertEqual(result["run"]["prompt_version"], ai_service.PROMPT_VERSION)
                self.assertIn("No OpenAI API call was made", result["run"]["output_text"])
                self.assertIn("summary_metrics.csv", result["run"]["input_summary"]["files"][0]["name"])
                self.assertEqual(ai_service.list_ai_runs()[0]["status"], "dry_run")
        finally:
            if previous_apps is None:
                sys.modules.pop("apps", None)
            else:
                sys.modules["apps"] = previous_apps
            import battery_lab.job_models as job_models
            import battery_lab.ai_service as ai_service

            importlib.reload(job_models)
            importlib.reload(ai_service)


if __name__ == "__main__":
    unittest.main()
