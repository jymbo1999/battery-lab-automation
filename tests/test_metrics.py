import unittest
from pathlib import Path

from battery_lab.file_io import parse_file
from battery_lab.metrics import capacity_metrics, compute_metrics, eis_metrics, sheet_resistance_metrics, voltage_metrics


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


class MetricTests(unittest.TestCase):
    def test_capacity_metrics_compute_ice_and_retention(self):
        rows = [
            {"cycle": "1", "charge_capacity": "100", "discharge_capacity": "90"},
            {"cycle": "100", "charge_capacity": "82", "discharge_capacity": "80"},
        ]

        metrics = capacity_metrics(rows)

        self.assertEqual(metrics["ice_percent"], 111.111)
        self.assertEqual(metrics["first_charge_capacity"], 100)
        self.assertEqual(metrics["retention@100"], 88.889)
        self.assertEqual(metrics["cycle_to_80"], "")
        self.assertEqual(metrics["ce_1st"], 111.111)

    def test_capacity_metrics_classifies_rate_performance(self):
        rows = [
            {"cycle": "1", "charge_capacity": "100", "discharge_capacity": "90", "c_rate": "0.1C"},
            {"cycle": "2", "charge_capacity": "95", "discharge_capacity": "85", "c_rate": "0.5C"},
            {"cycle": "3", "charge_capacity": "80", "discharge_capacity": "70", "c_rate": "1C"},
            {"cycle": "4", "charge_capacity": "60", "discharge_capacity": "50", "c_rate": "2C"},
        ]

        metrics = capacity_metrics(rows)

        self.assertEqual(metrics["protocol"], "RATE_PERFORMANCE")
        self.assertEqual(metrics["capacity@0p1C"], 90)
        self.assertIn("rate_retention_high_vs_base", metrics)

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

    def test_parse_capacity_without_headers_uses_a_d_e_columns(self):
        path = SAMPLE_DATA_DIR / "headerless_capacity_tmp.csv"
        path.write_text("1,0,0,100,90\n2,0,0,95,88\n", encoding="utf-8")
        try:
            dataset = parse_file(path)
            record = compute_metrics(dataset)
        finally:
            path.unlink()

        self.assertEqual(dataset.meta.analysis_type, "capacity")
        self.assertEqual(record.metrics["first_charge_capacity"], 100)
        self.assertEqual(record.metrics["first_discharge_capacity"], 90)

    def test_voltage_metrics_compute_hysteresis_by_cycle(self):
        rows = [
            {"cycle": "1", "direction": "charge", "capacity": "0", "voltage": "1.0"},
            {"cycle": "1", "direction": "charge", "capacity": "10", "voltage": "2.0"},
            {"cycle": "1", "direction": "discharge", "capacity": "0", "voltage": "0.8"},
            {"cycle": "1", "direction": "discharge", "capacity": "10", "voltage": "1.7"},
            {"cycle": "10", "direction": "discharge", "capacity": "0", "voltage": "0.9"},
            {"cycle": "10", "direction": "discharge", "capacity": "8", "voltage": "1.6"},
        ]

        metrics = voltage_metrics(rows)

        self.assertEqual(metrics["profile_available_cycles"], "1,10")
        self.assertEqual(metrics["charge_profile_capacity_1"], 10)
        self.assertEqual(metrics["discharge_profile_capacity_1"], 10)
        self.assertGreater(metrics["mean_hysteresis_1"], 0)
        self.assertEqual(metrics["capacity_loss_vs_first_10"], 20)


if __name__ == "__main__":
    unittest.main()
