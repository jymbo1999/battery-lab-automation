import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from battery_lab.conditions import read_conditions
from battery_lab.eis_matching import EISConditionMatch, build_comparison_clusters, build_eis_match_report, collect_eis_inventory
from battery_lab.ui import (
    areal_density_color,
    capacity_overlay_html,
    eis_overlay_html,
    eis_time_hours_from_text,
    format_time_hours_label,
    overlay_label,
    red_time_series_color,
    vary_similar_color,
)


class EISMatchingTests(unittest.TestCase):
    def test_inventory_classifies_numeric_hr_files_as_time_series(self):
        root = Path("/tmp/EIS")
        paths = [
            root / "260521" / "1hr" / "1.5act 3T_1hr_01.SEO",
            root / "260521" / "pc73 3T_01.SEO",
        ]

        inventory = collect_eis_inventory(paths, root)

        self.assertTrue(inventory[0].is_time_series)
        self.assertEqual(inventory[0].time_point, "1hr")
        self.assertFalse(inventory[1].is_time_series)

    def test_inventory_uses_folder_time_when_filename_has_no_hr_token(self):
        root = Path("/tmp/EIS")
        paths = [root / "260521" / "0hr" / "1.5 act 4T_03.SEO"]

        inventory = collect_eis_inventory(paths, root)

        self.assertTrue(inventory[0].is_time_series)
        self.assertEqual(inventory[0].time_point, "0hr")

    def test_time_series_groups_do_not_merge_distinct_file_groups_by_condition(self):
        root = Path("/tmp/EIS")
        paths = [
            root / "260521" / "0hr" / "1.5 act 4T_03.SEO",
            root / "260521" / "2hr" / "1.5act 4T 2hr_03.SEO",
            root / "260521" / "0hr" / "1.5 act 4T_2_04.SEO",
            root / "260521" / "2hr" / "1.5act 4T_2 2hr_04.SEO",
        ]
        base = {
            "cell_id": "1.5 act 4T",
            "sample": "1.5 act 4T",
            "date": "260521",
            "electrolyte": "1.3M LiPF6",
            "binder": "2wt%cmc/40wt%SBR",
            "voltage_range": "0.01~2V",
            "ratio": "0.95",
            "areal_mass_density": 7.1,
        }

        report = build_eis_match_report(paths, {"1.5 act 4T": base}, root)

        grouped_paths = [set(group.member_paths.split(";")) for group in report.time_series_groups]
        self.assertEqual(len(grouped_paths), 2)
        self.assertIn({str(Path("260521/0hr/1.5 act 4T_03.SEO")), str(Path("260521/2hr/1.5act 4T 2hr_03.SEO"))}, grouped_paths)
        self.assertIn({str(Path("260521/0hr/1.5 act 4T_2_04.SEO")), str(Path("260521/2hr/1.5act 4T_2 2hr_04.SEO"))}, grouped_paths)

    def test_matcher_rejects_numeric_material_conflicts(self):
        root = Path("/tmp/EIS")
        paths = [root / "260508" / "pc91_5T_1_03.SEO"]
        conditions = {
            "pc19 5T": {
                "cell_id": "pc19 5T",
                "sample": "pc19 5T",
                "date": "260507",
                "electrolyte": "1.3M LiPF6",
                "binder": "2wt%cmc/40wt%SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
                "areal_mass_density": 12.8,
            }
        }

        report = build_eis_match_report(paths, conditions, root)

        self.assertEqual(report.matches[0].status, "unmatched")
        self.assertEqual(report.matches[0].condition_key, "")

    def test_matcher_exposes_candidates_and_applies_manual_override(self):
        root = Path("/tmp/EIS")
        path = root / "260521" / "0hr" / "1.5act 3T_01.SEO"
        conditions = {
            "1.5 act 3T_1": {
                "cell_id": "1.5 act 3T_1",
                "sample": "1.5 act 3T_1",
                "date": "260521",
                "electrolyte": "1.3M LiPF6",
                "binder": "2wt%cmc/40wt%SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
                "areal_mass_density": 7.1,
                "_source_row_number": 12,
            },
            "1.5 act 3T_2": {
                "cell_id": "1.5 act 3T_2",
                "sample": "1.5 act 3T_2",
                "date": "260521",
                "electrolyte": "1.3M LiPF6",
                "binder": "2wt%cmc/40wt%SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
                "areal_mass_density": 7.1,
                "_source_row_number": 13,
            },
        }

        report = build_eis_match_report([path], conditions, root)
        options = json.loads(report.matches[0].candidate_options)
        self.assertGreaterEqual(len(options), 2)
        self.assertEqual(options[0]["journal_row"], 12)

        manual = build_eis_match_report(
            [path],
            conditions,
            root,
            {str(Path("260521/0hr/1.5act 3T_01.SEO")): {"condition_key": "1.5 act 3T_2"}},
        )

        self.assertEqual(manual.matches[0].status, "manual")
        self.assertEqual(manual.matches[0].condition_key, "1.5 act 3T_2")
        self.assertEqual(manual.status_counts["manual"], 1)

    def test_comparison_clusters_generate_all_pairs_within_loading_window(self):
        root = Path("/tmp/EIS")
        paths = [
            root / "260521" / "1.5act 3T_01.SEO",
            root / "260521" / "pc73 3T_01.SEO",
            root / "260521" / "DL3T3T_01.SEO",
        ]
        base = {
            "date": "260521",
            "electrolyte": "1.3M LiPF6",
            "binder": "2wt%cmc/40wt%SBR",
            "voltage_range": "0.01~2V",
            "ratio": "0.95",
        }
        conditions = {
            "1.5 act 3T": {"cell_id": "1.5 act 3T", "sample": "1.5 act 3T", "areal_mass_density": 7.1, **base},
            "pc73 3T": {"cell_id": "pc73 3T", "sample": "pc73 3T", "areal_mass_density": 6.9, **base},
            "DL pc 3T3T": {"cell_id": "DL pc 3T3T", "sample": "DL pc 3T3T", "areal_mass_density": 11.1, **base},
        }

        report = build_eis_match_report(paths, conditions, root)

        self.assertEqual(len(report.comparison_clusters), 1)
        self.assertEqual(len(report.comparison_pairs), 1)
        self.assertEqual(report.comparison_pairs[0].comparison_grade, "A")

    def test_comparison_clusters_promote_24hr_time_series_endpoint(self):
        conditions = {
            "steady": {
                "cell_id": "steady",
                "sample": "steady",
                "date": "260610",
                "electrolyte": "1.0M LiPF6 EC/DEC 1:1",
                "binder": "2wt%cmc",
                "voltage_range": "0.01~2V",
                "ratio": "0.96",
                "areal_mass_density": 8.0,
            },
            "ts": {
                "cell_id": "ts",
                "sample": "ts",
                "date": "260610",
                "electrolyte": "1.0M LiPF6 EC/DEC 1:1",
                "binder": "2wt%cmc",
                "voltage_range": "0.01~2V",
                "ratio": "0.96",
                "areal_mass_density": 8.4,
            },
        }
        matches = [
            EISConditionMatch(
                source_path="steady.SEO", relative_path="steady.SEO", is_time_series=False,
                file_group_key="steady", time_point="", status="verified", score=80, margin=20,
                condition_key="steady",
            ),
            EISConditionMatch(
                source_path="ts_24hr.SDE", relative_path="ts_24hr.SDE", is_time_series=True,
                file_group_key="ts", time_point="24hr", status="manual", score=80, margin=999,
                condition_key="ts",
            ),
        ]
        ts_clusters = [
            SimpleNamespace(
                cluster_id="TS001", has_24=True, match_status="manual", condition_key="ts",
                member_paths="ts_24hr.SDE",
            )
        ]

        clusters, _ = build_comparison_clusters(matches, ts_clusters, conditions)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].source_paths, "steady.SEO;ts_24hr.SDE")
        self.assertEqual(clusters[0].member_origins, "steady;ts_24hr")
        self.assertEqual(clusters[0].ts24_source_paths, "ts_24hr.SDE")

    def test_comparison_clusters_do_not_promote_sub_24hr_time_series_endpoint(self):
        conditions = {
            "steady": {
                "cell_id": "steady",
                "sample": "steady",
                "date": "260610",
                "electrolyte": "1.0M LiPF6 EC/DEC 1:1",
                "binder": "2wt%cmc",
                "voltage_range": "0.01~2V",
                "ratio": "0.96",
                "areal_mass_density": 8.0,
            },
            "ts": {
                "cell_id": "ts",
                "sample": "ts",
                "date": "260610",
                "electrolyte": "1.0M LiPF6 EC/DEC 1:1",
                "binder": "2wt%cmc",
                "voltage_range": "0.01~2V",
                "ratio": "0.96",
                "areal_mass_density": 8.4,
            },
        }
        matches = [
            EISConditionMatch(
                source_path="steady.SEO", relative_path="steady.SEO", is_time_series=False,
                file_group_key="steady", time_point="", status="verified", score=80, margin=20,
                condition_key="steady",
            ),
            EISConditionMatch(
                source_path="ts_9hr.SDE", relative_path="ts_9hr.SDE", is_time_series=True,
                file_group_key="ts", time_point="9hr", status="manual", score=80, margin=999,
                condition_key="ts",
            ),
        ]
        ts_clusters = [
            SimpleNamespace(
                cluster_id="TS001", has_24=False, match_status="manual", condition_key="ts",
                member_paths="ts_9hr.SDE",
            )
        ]

        clusters, _ = build_comparison_clusters(matches, ts_clusters, conditions)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].source_paths, "steady.SEO")
        self.assertEqual(clusters[0].cluster_role, "independent")
        self.assertNotIn("ts_9hr.SDE", clusters[0].source_paths)

    def test_single_comparison_file_remains_independent_cluster(self):
        root = Path("/tmp/EIS")
        path = root / "260701" / "sample_01.SEO"
        conditions = {
            "sample": {
                "cell_id": "sample",
                "sample": "sample",
                "date": "260701",
                "electrolyte": "1.0M LiPF6",
                "binder": "CMC/SBR",
                "voltage_range": "0.01~2V",
                "ratio": "0.95",
                "areal_mass_density": 7.1,
            }
        }

        report = build_eis_match_report([path], conditions, root, {"260701/sample_01.SEO": {"condition_key": "sample"}})

        self.assertEqual(len(report.comparison_clusters), 1)
        self.assertEqual(report.comparison_clusters[0].file_count, 1)
        self.assertEqual(report.comparison_clusters[0].cluster_role, "independent")
        self.assertEqual(report.comparison_pairs, [])

    def test_read_conditions_can_select_named_workbook_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conditions.xlsx"
            workbook = Workbook()
            ws = workbook.active
            ws.title = "old"
            ws.append(["Sample", "Binder"])
            ws.append(["old_cell", "PVDF"])
            ws2 = workbook.create_sheet("JYJ")
            ws2.append(["Sample", "Binder"])
            ws2.append(["new_cell", "CMC/SBR"])
            workbook.save(path)

            conditions = read_conditions(path, sheet_name="JYJ")

        self.assertEqual(list(conditions), ["new_cell"])
        self.assertEqual(conditions["new_cell"]["binder"], "CMC/SBR")

    def test_overlay_zoom_uses_cursor_anchored_svg_matrix_transform(self):
        html = eis_overlay_html(
            "overlay",
            [
                {
                    "points": [(0.0, 0.0), (10.0, 5.0)],
                    "color": "#1f77b4",
                    "label": "cell\n(Rs 1, Rct 2)",
                    "short_label": "cell",
                    "condition": {"sample": "pure 5T"},
                    "fit": {},
                }
            ],
        )

        self.assertIn("svgPointFromEvent", html)
        self.assertIn("matrix(${scale} 0 0 ${scale} ${tx} ${ty})", html)
        self.assertIn("wheelZoomIn = 1.048", html)
        self.assertIn("pointerdown", html)
        self.assertIn("pointermove", html)
        self.assertIn("data-label-group", html)
        self.assertIn("updateLabelScale", html)
        self.assertIn("user-select:none", html)
        self.assertIn("inactive-row", html)
        self.assertIn("document.elementFromPoint", html)
        self.assertIn("Color encodes Areal mass density", html)
        self.assertLess(html.index(">Graph<"), html.index(">Areal<"))
        self.assertLess(html.index(">Areal<"), html.index(">T<"))
        self.assertLess(html.index(">T<"), html.index(">Rs<"))
        self.assertIn(">5T<", html)
        self.assertLess(html.index(">Areal<"), html.index(">전해질<"))
        self.assertNotIn("translate(${tx} ${ty}) scale(${scale})", html)

    def test_capacity_overlay_table_places_areal_next_to_graph_name(self):
        html = capacity_overlay_html(
            "capacity",
            [
                {
                    "points": [(1.0, 90.0), (2.0, 91.0)],
                    "color": "#111111",
                    "label": "cell",
                    "short_label": "cell",
                    "condition": {"sample": "cell 3T", "areal_mass_density": 7.1},
                    "metrics": {},
                    "curve_kind": "discharge",
                }
            ],
        )

        self.assertLess(html.index(">Graph<"), html.index(">Areal<"))
        self.assertLess(html.index(">Areal<"), html.index(">T<"))
        self.assertLess(html.index(">T<"), html.index(">Density<"))
        self.assertLess(html.index(">Areal<"), html.index(">Density<"))

    def test_time_series_colors_sort_from_light_to_dark_red(self):
        self.assertEqual(eis_time_hours_from_text("cell_0hr_01.SEO"), 0.0)
        self.assertEqual(eis_time_hours_from_text("cell_24hr_2.SDE"), 24.0)
        self.assertEqual(red_time_series_color(0, 3), "#fecaca")
        self.assertEqual(red_time_series_color(2, 3), "#991b1b")

    def test_time_series_overlay_uses_compact_hour_labels(self):
        html = eis_overlay_html(
            "time",
            [
                {
                    "points": [(0.0, 0.0), (10.0, 5.0)],
                    "color": "#991b1b",
                    "label": "24hr\n(Rs 1, Rct 2)",
                    "short_label": "long_material_name_24hr",
                    "condition": {},
                    "fit": {},
                }
            ],
            color_mode="time_series",
        )
        dataset = SimpleNamespace(meta=SimpleNamespace(time_point="24hr"))

        self.assertIn("data-label-index", html)
        self.assertNotIn("labelPlacement", html)
        self.assertNotIn("updateLabels", html)
        self.assertNotIn("Color encodes Areal mass density", html)
        self.assertEqual(format_time_hours_label(24.0), "24hr")
        self.assertEqual(overlay_label("long_material_name_24hr.SEO", dataset, {"rs_ohm": 1, "rct_ohm": 2}, compact_time=True, time_hours=24.0), "24hr\n(Rs 1.00, Rct 2.00)")

    def test_areal_density_color_maps_low_to_high_continuously(self):
        self.assertEqual(areal_density_color(None, 5, 10), "#64748b")
        self.assertEqual(areal_density_color(5, 5, 10), "#2563eb")
        self.assertEqual(areal_density_color(10, 5, 10), "#dc2626")
        self.assertNotEqual(areal_density_color(7.5, 5, 10), areal_density_color(10, 5, 10))
        self.assertNotEqual(vary_similar_color("#22c55e", 0, 3), vary_similar_color("#22c55e", 1, 3))
        self.assertNotEqual(vary_similar_color("#22c55e", 1, 3), vary_similar_color("#22c55e", 2, 3))


if __name__ == "__main__":
    unittest.main()
