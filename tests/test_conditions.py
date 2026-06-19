import unittest
from pathlib import Path

from battery_lab.conditions import (
    build_analysis_availability,
    build_analysis_comparison_validations,
    build_analysis_file_records,
    build_comparison_candidates,
    read_conditions,
)
from battery_lab.file_io import parse_file
from battery_lab.models import MetricRecord


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


class ConditionRuleTests(unittest.TestCase):
    def test_comparison_candidates_grade_pdf_rules(self):
        conditions = {
            "A": {
                "cell_id": "A",
                "areal_mass_density": 7.0,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "B": {
                "cell_id": "B",
                "areal_mass_density": 7.4,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "C": {
                "cell_id": "C",
                "areal_mass_density": 7.8,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "D": {
                "cell_id": "D",
                "areal_mass_density": 8.4,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "E": {
                "cell_id": "E",
                "areal_mass_density": 7.1,
                "electrolyte": "1.0M LiPF6",
                "binder": "PVDF",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
        }

        candidates = build_comparison_candidates(["A", "B", "C", "D", "E"], conditions)
        grades = {(row.cell_id_a, row.cell_id_b): row.comparison_grade for row in candidates}

        self.assertEqual(grades[("A", "B")], "A")
        self.assertEqual(grades[("A", "C")], "B")
        self.assertEqual(grades[("A", "D")], "C")
        self.assertEqual(grades[("A", "E")], "X")

    def test_analysis_availability_keeps_registry_cells_without_files(self):
        conditions = read_conditions(SAMPLE_DATA_DIR / "cell_conditions.csv")
        conditions["missing_cell"] = {
            "cell_id": "missing_cell",
            "sample": "registry only",
            "canonical_cell_id": "missing_cell",
            "display_label": "registry only",
            "sample_batch_id": "registry_only",
        }
        datasets = [parse_file(path) for path in SAMPLE_DATA_DIR.iterdir() if path.name != "cell_conditions.csv"]

        availability = {row.cell_id: row for row in build_analysis_availability(datasets, conditions)}

        self.assertIn("missing_cell", availability)
        self.assertEqual(availability["missing_cell"].file_count, 0)
        self.assertFalse(availability["missing_cell"].has_capacity)
        self.assertIn("Capacity file missing", availability["missing_cell"].missing_note)
        self.assertTrue(availability["1.5act_3T_1"].has_eis)

    def test_analysis_file_records_include_protocol_and_time_point(self):
        datasets = [parse_file(SAMPLE_DATA_DIR / "1.5act_3T_1__EIS__24hr__20260615.sde")]

        records = build_analysis_file_records(datasets, {})

        self.assertEqual(records[0].analysis_type, "eis")
        self.assertEqual(records[0].time_point, "24hr")
        self.assertEqual(records[0].upload_date, "20260615")

    def test_analysis_comparison_validation_blocks_capacity_protocol_mismatch(self):
        conditions = {
            "A": {
                "cell_id": "A",
                "areal_mass_density": 7.0,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "B": {
                "cell_id": "B",
                "areal_mass_density": 7.2,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
        }
        records = [
            MetricRecord("A", "capacity", "a.csv", {"protocol": "LONG_0p1C"}),
            MetricRecord("B", "capacity", "b.csv", {"protocol": "RATE_PERFORMANCE"}),
        ]

        validations = build_analysis_comparison_validations(records, conditions)

        self.assertEqual(validations[0].status, "BLOCK")
        self.assertIn("protocol 다름", validations[0].reason)

    def test_analysis_comparison_validation_warns_on_partial_voltage_cycles(self):
        conditions = {
            "A": {
                "cell_id": "A",
                "areal_mass_density": 7.0,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
            "B": {
                "cell_id": "B",
                "areal_mass_density": 7.2,
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
            },
        }
        records = [
            MetricRecord("A", "voltage_profile", "a.csv", {"profile_available_cycles": "1,2,10,20"}),
            MetricRecord("B", "voltage_profile", "b.csv", {"profile_available_cycles": "1,2,10"}),
        ]

        validations = build_analysis_comparison_validations(records, conditions)

        self.assertEqual(validations[0].status, "WARNING")
        self.assertEqual(validations[0].common_cycles, "1,2,10")


if __name__ == "__main__":
    unittest.main()
