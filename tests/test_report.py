import tempfile
import unittest
from pathlib import Path

from battery_lab.cli import collect_paths
from battery_lab.conditions import read_conditions
from battery_lab.file_io import parse_file
from battery_lab.metrics import compute_metrics
from battery_lab.report import write_outputs


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


class ReportTests(unittest.TestCase):
    def test_write_outputs_creates_summary_and_report(self):
        paths = [path for path in collect_paths(SAMPLE_DATA_DIR) if path.name != "cell_conditions.csv"]
        datasets = [parse_file(path) for path in paths]
        records = [compute_metrics(dataset) for dataset in datasets]
        conditions = read_conditions(SAMPLE_DATA_DIR / "cell_conditions.csv")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_outputs(datasets, records, tmp_path, conditions)

            self.assertTrue((tmp_path / "summary_metrics.csv").exists())
            self.assertTrue((tmp_path / "analysis_files.csv").exists())
            self.assertTrue((tmp_path / "analysis_availability.csv").exists())
            self.assertTrue((tmp_path / "comparison_candidates.csv").exists())
            self.assertTrue((tmp_path / "analysis_comparison_validations.csv").exists())
            self.assertTrue((tmp_path / "report.html").exists())
            self.assertTrue((tmp_path / "dashboard.html").exists())
            self.assertTrue(list((tmp_path / "capacity").glob("*.svg")))
            self.assertIn("activated carbon coated graphite", (tmp_path / "report.html").read_text())
            self.assertIn("Analysis availability matrix", (tmp_path / "dashboard.html").read_text())
            self.assertIn("file missing", (tmp_path / "dashboard.html").read_text())


if __name__ == "__main__":
    unittest.main()
