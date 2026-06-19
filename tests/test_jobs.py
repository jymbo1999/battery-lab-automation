import importlib
import sys
import types
import unittest

from flask import Flask
from flask_sqlalchemy import SQLAlchemy


class BatteryJobTests(unittest.TestCase):
    def test_job_model_creates_sqlite_tables_and_queues_job(self):
        db = SQLAlchemy()
        fake_apps = types.ModuleType("apps")
        fake_apps.db = db
        previous_apps = sys.modules.get("apps")
        sys.modules["apps"] = fake_apps
        try:
            job_models = importlib.import_module("battery_lab.job_models")
            if getattr(job_models, "db", None) is not db:
                job_models = importlib.reload(job_models)
            job_service = importlib.import_module("battery_lab.job_service")
            if getattr(job_service, "db", None) is not db:
                job_service = importlib.reload(job_service)

            app = Flask(__name__)
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            db.init_app(app)

            with app.app_context():
                job = job_service.create_job("rescan_files", target="battery", params={"source": "test"})
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["job_type"], "rescan_files")
                self.assertEqual(job_service.job_status_summary(), {"queued": 1})

                detail = job_service.get_job(job["id"])
                self.assertEqual(detail["logs"][0]["message"], "Job queued. Worker execution is not enabled in this phase.")

                cancelled = job_service.cancel_job(job["id"])
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(job_service.job_status_summary(), {"cancelled": 1})
        finally:
            if previous_apps is None:
                sys.modules.pop("apps", None)
            else:
                sys.modules["apps"] = previous_apps
            import battery_lab.job_models as job_models
            import battery_lab.job_service as job_service

            importlib.reload(job_models)
            importlib.reload(job_service)


if __name__ == "__main__":
    unittest.main()
