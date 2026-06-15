import tempfile
import unittest
from pathlib import Path

from battery_lab.cli import collect_paths
from battery_lab.conditions import read_conditions
from battery_lab.file_io import parse_file
from battery_lab.journal import normalize_journal_date, write_journal
from battery_lab.metrics import compute_metrics


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


class JournalTests(unittest.TestCase):
    def test_normalize_journal_date_handles_filename_and_condition_formats(self):
        self.assertEqual(normalize_journal_date("20260615"), "2026-06-15")
        self.assertEqual(normalize_journal_date("260615"), "2026-06-15")
        self.assertEqual(normalize_journal_date("2026.6.15"), "2026-06-15")

    def test_write_journal_creates_date_index_and_daily_dashboard(self):
        paths = [path for path in collect_paths(SAMPLE_DATA_DIR) if path.name != "cell_conditions.csv"]
        datasets = [parse_file(path) for path in paths]
        records = [compute_metrics(dataset) for dataset in datasets]
        conditions = read_conditions(SAMPLE_DATA_DIR / "cell_conditions.csv")

        with tempfile.TemporaryDirectory() as tmp:
            journal_dir = Path(tmp) / "lab_journal"
            days = write_journal(datasets, records, journal_dir, conditions)

            self.assertEqual([day.date_key for day in days], ["2026-06-15"])
            self.assertTrue((journal_dir / "index.html").exists())
            self.assertTrue((journal_dir / "journal_manifest.csv").exists())
            self.assertTrue((journal_dir / "2026-06-15" / "dashboard.html").exists())
            self.assertTrue((journal_dir / "2026-06-15" / "report.html").exists())
            self.assertIn("배터리 실험 일지", (journal_dir / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
