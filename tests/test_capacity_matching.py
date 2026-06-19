import unittest
from pathlib import Path

from battery_lab.capacity_matching import (
    CAPACITY_PROTOCOL_TYPE_1,
    CAPACITY_PROTOCOL_TYPE_2,
    CAPACITY_PROTOCOL_TYPE_3,
    build_capacity_match_report,
    classify_capacity_protocol,
    collect_capacity_inventory,
)


class CapacityMatchingTests(unittest.TestCase):
    def test_inventory_extracts_journal_row_prefix(self):
        root = Path("/tmp/capacity")
        paths = [root / "260522" / "460_1.5act_3T_0.5C_024_Capacity.csv"]

        inventory = collect_capacity_inventory(paths, root)

        self.assertEqual(inventory[0].row_prefix, 460)
        self.assertEqual(inventory[0].relative_path, "260522/460_1.5act_3T_0.5C_024_Capacity.csv")

    def test_matcher_prefers_exact_journal_row_prefix(self):
        root = Path("/tmp/capacity")
        path = root / "260522" / "460_1.5act_3T_0.5C_024_Capacity.csv"
        conditions = {
            "wrong text same date": {
                "cell_id": "wrong text same date",
                "sample": "wrong text same date",
                "date": "260522",
                "_source_row_number": 459,
            },
            "1.5 act 3T": {
                "cell_id": "1.5 act 3T",
                "sample": "1.5 act 3T",
                "date": "260522",
                "areal_mass_density": 7.1,
                "_source_row_number": 460,
            },
        }

        report = build_capacity_match_report([path], conditions, root)

        self.assertEqual(report.matches[0].status, "verified")
        self.assertEqual(report.matches[0].condition_key, "1.5 act 3T")
        self.assertEqual(report.matches[0].journal_row, 460)
        self.assertIn("row_prefix=460", report.matches[0].reason)

    def test_capacity_protocol_uses_filename_rules_first(self):
        examples = [
            ("492_pure_900_4T_1_0.1C_low_temperature_Capacity.csv", CAPACITY_PROTOCOL_TYPE_1),
            ("489_1.5act_3T_1_0.5C_023_Capacity.csv", CAPACITY_PROTOCOL_TYPE_2),
            ("490_1.5act_3T_2_rate per_029_Capacity.csv", CAPACITY_PROTOCOL_TYPE_3),
        ]

        for filename, expected_type in examples:
            with self.subTest(filename=filename):
                result = classify_capacity_protocol(filename, [(1, 300), (2, 150), (3, 280), (4, 100)])

                self.assertEqual(result.protocol_type, expected_type)
                self.assertEqual(result.rule_source, "filename")

    def test_capacity_protocol_falls_back_to_shape_for_unlabeled_smooth_curve(self):
        points = [(cycle, 150 + cycle) for cycle in range(1, 20)]

        result = classify_capacity_protocol("unknown_capacity.csv", points)

        self.assertEqual(result.protocol_type, CAPACITY_PROTOCOL_TYPE_1)
        self.assertEqual(result.rule_source, "shape")
        self.assertEqual(result.bend_count, 0)

    def test_capacity_protocol_falls_back_to_shape_for_single_bend(self):
        points = [(cycle, 360) for cycle in range(1, 11)] + [(11, 280), (12, 255), (13, 245), (14, 248)]

        result = classify_capacity_protocol("unknown_capacity.csv", points)

        self.assertEqual(result.protocol_type, CAPACITY_PROTOCOL_TYPE_2)
        self.assertEqual(result.rule_source, "shape")
        self.assertEqual(result.bend_count, 1)

    def test_capacity_protocol_falls_back_to_shape_for_rate_performance(self):
        points = [
            (1, 350),
            (2, 345),
            (6, 230),
            (12, 215),
            (18, 150),
            (24, 95),
            (31, 60),
            (38, 42),
            (46, 265),
            (60, 240),
        ]

        result = classify_capacity_protocol("unknown_capacity.csv", points)

        self.assertEqual(result.protocol_type, CAPACITY_PROTOCOL_TYPE_3)
        self.assertEqual(result.rule_source, "shape")
        self.assertGreaterEqual(result.bend_count, 3)


if __name__ == "__main__":
    unittest.main()
