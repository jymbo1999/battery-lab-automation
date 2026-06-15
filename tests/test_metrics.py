import unittest
from pathlib import Path

from battery_lab.file_io import parse_file
from battery_lab.metrics import capacity_metrics, compute_metrics, eis_metrics, sheet_resistance_metrics


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


class MetricTests(unittest.TestCase):
    def test_capacity_metrics_compute_ice_and_retention(self):
        rows = [
            {"cycle": "1", "charge_capacity": "100", "discharge_capacity": "90"},
            {"cycle": "100", "charge_capacity": "82", "discharge_capacity": "80"},
        ]

        metrics = capacity_metrics(rows)

        self.assertEqual(metrics["ice_percent"], 111.111)
        self.assertEqual(metrics["retention@100"], 88.889)
        self.assertEqual(metrics["cycle_to_80"], "")

    def test_eis_metrics_estimate_rs_and_rct(self):
        rows = [
            {"z_real": "1.5", "z_imag": "-0.01"},
            {"z_real": "2.5", "z_imag": "-0.8"},
            {"z_real": "4.5", "z_imag": "-0.02"},
        ]

        metrics = eis_metrics(rows)

        self.assertEqual(metrics["rs_auto"], 1.5)
        self.assertGreaterEqual(metrics["rct_auto"], 3.0)

    def test_sheet_resistance_metrics(self):
        rows = [{"sheet_resistance": "10"}, {"sheet_resistance": "11"}, {"sheet_resistance": "12"}]

        metrics = sheet_resistance_metrics(rows)

        self.assertEqual(metrics["mean_sheet_resistance"], 11)
        self.assertGreater(metrics["cv_percent"], 0)

    def test_parse_sample_capacity(self):
        dataset = parse_file(SAMPLE_DATA_DIR / "1.5act_3T_1__capacity__cycle100__20260615.csv")
        record = compute_metrics(dataset)

        self.assertEqual(dataset.meta.analysis_type, "capacity")
        self.assertEqual(dataset.meta.cell_id, "1.5act_3T_1")
        self.assertGreater(record.metrics["ice_percent"], 95)


if __name__ == "__main__":
    unittest.main()
