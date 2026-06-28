import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from battery_lab import ui, viewer_service
from battery_lab.capacity_matching import CAPACITY_PROTOCOL_TYPE_1, CAPACITY_PROTOCOL_TYPE_3
from battery_lab.ui import (
    capacity_overlay_html,
    collect_analysis_artifacts,
    count_files,
    filter_eis_overlay_outliers,
    finder_tree,
    insert_overlay_999_label,
    list_directory,
    overlay_density_stack_label_positions,
    render_finder_html,
    render_sidebar_html,
)


class UiTests(unittest.TestCase):
    def test_list_directory_sorts_directories_before_files_and_skips_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z-file.csv").write_text("x", encoding="utf-8")
            (root / "A Folder").mkdir()
            (root / ".hidden").write_text("x", encoding="utf-8")

            entries = list_directory(root)

            self.assertEqual([entry.name for entry in entries], ["A Folder", "z-file.csv"])
            self.assertTrue(entries[0].is_dir)

    def test_count_files_filters_suffixes_and_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.csv").write_text("x", encoding="utf-8")
            (root / "two.wrd").write_text("x", encoding="utf-8")
            (root / ".hidden.csv").write_text("x", encoding="utf-8")
            (root / "skip.txt").write_text("x", encoding="utf-8")

            self.assertEqual(count_files(root, {".csv", ".wrd"}), 2)

    def test_collect_analysis_artifacts_reads_analysis_output_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            (output_root / "capacity").mkdir()
            (output_root / "capacity" / "b.svg").write_text("<svg/>", encoding="utf-8")
            (output_root / "capacity" / "a.png").write_text("png", encoding="utf-8")
            (output_root / "capacity" / "ignore.txt").write_text("x", encoding="utf-8")

            with patch.object(ui, "ANALYSIS_OUTPUT_ROOT", output_root):
                artifacts = collect_analysis_artifacts("capacity")

            self.assertEqual([artifact.name for artifact in artifacts], ["a.png", "b.svg"])

    def test_finder_html_contains_column_view_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "260417").mkdir()
            (root / "260417" / "sample.csv").write_text("x", encoding="utf-8")

            html = render_finder_html({"roots": [finder_tree("capacity", root)]})

            self.assertIn("class=\"finder\"", html)
            self.assertIn("class=\"columns\"", html)
            self.assertIn("folder-icon", html)
            self.assertIn("file-icon", html)
            self.assertIn("columnLevel", html)
            self.assertIn("overflow-x: auto", html)
            self.assertIn("selectedPath", html)
            self.assertIn("260417", html)
            self.assertIn("sample.csv", html)

    def test_sidebar_html_uses_consistent_link_navigation(self):
        html = render_sidebar_html("eis")

        self.assertIn("battery-sidebar", html)
        self.assertIn("sidebar-brand", html)
        self.assertIn("데이터 브라우저", html)
        self.assertIn("<span>분석</span>", html)
        self.assertIn('href="?page=journal"', html)
        self.assertIn('href="?page=files"', html)
        self.assertIn('href="?page=eis"', html)
        self.assertIn('sidebar-link child active', html)
        self.assertNotIn("●", html)

    def test_eis_overlay_outlier_filter_excludes_damaged_scale_with_reason(self):
        series = [
            {"relative_path": "ok-0hr.SEO", "points": [(1.0, 0.5), (90.0, 120.0)]},
            {"relative_path": "bad-0hr.SEO", "points": [(-557717.0, 199757.0), (1459143.0, 8049483.0)]},
            {"relative_path": "ok-1hr.SEO", "points": [(1.5, 0.5), (110.0, 130.0)]},
        ]

        kept, errors = filter_eis_overlay_outliers(series)

        self.assertEqual([item["relative_path"] for item in kept], ["ok-0hr.SEO", "ok-1hr.SEO"])
        self.assertEqual(len(errors), 1)
        self.assertIn("bad-0hr.SEO", errors[0])
        self.assertIn("좌표 스케일이 비정상적으로 커서", errors[0])

    def test_capacity_overlay_uses_shared_zoomable_viewer(self):
        html = capacity_overlay_html(
            "P999 all Capacity datasets",
            [
                {
                    "points": [(1.0, 300.0), (2.0, 295.0)],
                    "color": "#2563eb",
                    "label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "short_label": "460_cell_Capacity",
                    "sample_label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "curve_kind": "Charge",
                    "marker_shape": "circle",
                    "show_graph_label": False,
                    "condition": {"sample": "sample A 4T", "areal_mass_density": 7.1, "electrode_density": 2.3, "binder": "CMC/SBR"},
                    "metrics": {"ice_percent": 91.2, "first_discharge_capacity": 300, "last_discharge_capacity": 295, "retention@100": 98.3},
                    "match": None,
                    "protocol_cluster_id": "P001",
                    "protocol_label": "1번 · 0.1C continuous",
                    "protocol_reason": "filename rule",
                    "bend_count": 0,
                },
                {
                    "points": [(1.0, 285.0), (2.0, 280.0)],
                    "color": "#2563eb",
                    "label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "short_label": "460_cell_Capacity",
                    "sample_label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "curve_kind": "Discharge",
                    "marker_shape": "square",
                    "show_graph_label": True,
                    "condition": {"sample": "sample A 4T", "areal_mass_density": 7.1, "electrode_density": 2.3, "binder": "CMC/SBR"},
                    "metrics": {"ice_percent": 91.2, "first_discharge_capacity": 300, "last_discharge_capacity": 295, "retention@100": 98.3},
                    "match": None,
                    "protocol_cluster_id": "P001",
                    "protocol_label": "1번 · 0.1C continuous",
                    "protocol_reason": "filename rule",
                    "bend_count": 0,
                }
            ],
            performance_mode=False,
        )

        self.assertIn("P999 all Capacity datasets", html)
        self.assertIn("Specific capacity (mAh/g)", html)
        self.assertIn("svgPointFromEvent", html)
        self.assertIn("matrix(${scale} 0 0 ${scale} ${tx} ${ty})", html)
        self.assertIn("inactive-row", html)
        self.assertIn("Show all", html)
        self.assertIn("Hide all", html)
        self.assertIn("setAllSeriesActive", html)
        self.assertIn('data-series-toggle="hide-all"', html)
        self.assertLess(html.index(">Graph<"), html.index(">Areal<"))
        self.assertLess(html.index(">Areal<"), html.index(">T<"))
        self.assertLess(html.index(">T<"), html.index(">Density<"))
        self.assertNotIn(">Curve<", html)
        self.assertIn("data-series-ids=", html)
        self.assertEqual(html.count("<tr data-series-ids="), 1)
        self.assertIn("data-marker-legend", html)
        self.assertNotIn("동그라미", html)
        self.assertNotIn("네모", html)
        self.assertEqual(html.count("data-label-group"), 1)
        self.assertIn("sample A (ICE=91.2%, density=2.3 g/cm3)", html)
        self.assertIn(">ICE<", html)
        self.assertIn(">Density<", html)
        self.assertIn(">4T<", html)
        self.assertIn("data-cx=", html)
        self.assertIn('fill-opacity=".24"', html)
        self.assertIn('stroke="#111111"', html)
        self.assertIn('stroke-width=".45"', html)
        self.assertIn('data-base-stroke-width=".45"', html)
        self.assertIn("<rect data-series-marker data-zoom-radius", html)
        # 0.1C continuous protocol KPI columns
        self.assertIn(">N<", html)
        self.assertIn(">Qd1<", html)
        self.assertIn(">QdN<", html)
        self.assertIn(">RetN<", html)
        self.assertIn(">F/cyc<", html)
        self.assertIn(">ΔV<", html)
        # legacy / removed columns must not reappear
        self.assertNotIn(">Row<", html)
        self.assertNotIn(">Type<", html)
        self.assertNotIn(">Bends<", html)
        self.assertNotIn(">충방전v∆<", html)
        self.assertNotIn("P001 1번", html)
        self.assertNotIn(">First<", html)
        self.assertNotIn(">Last<", html)
        self.assertNotIn(">R@100<", html)

    def test_capacity_clusters_split_by_comparison_conditions(self):
        rel_paths = ["260522/lib_a.csv", "260522/lib_b.csv", "260603/lib_c.csv", "260522/zib_a.csv", "260603/voltage_a.csv"]
        report = SimpleNamespace(
            matches=[
                SimpleNamespace(relative_path="260522/lib_a.csv", condition_key="lib_a"),
                SimpleNamespace(relative_path="260522/lib_b.csv", condition_key="lib_b"),
                SimpleNamespace(relative_path="260603/lib_c.csv", condition_key="lib_c"),
                SimpleNamespace(relative_path="260522/zib_a.csv", condition_key="zib_a"),
                SimpleNamespace(relative_path="260603/voltage_a.csv", condition_key="voltage_a"),
            ]
        )
        base = {
            "cell_type": "LIB",
            "electrolyte": "1.0M LiPF6",
            "binder": "CMC/SBR",
            "voltage_range": "0.01~2V",
            "ratio": "0.95",
        }
        conditions = {
            "lib_a": dict(base),
            "lib_b": dict(base),
            "lib_c": dict(base),
            "zib_a": {**base, "cell_type": "ZIB"},
            "voltage_a": {**base, "voltage_range": "0.2~1.5V"},
        }

        with patch.object(viewer_service.streamlit_ui, "capacity_protocol_path_groups", return_value={CAPACITY_PROTOCOL_TYPE_1: rel_paths}):
            groups = viewer_service.capacity_comparison_path_groups(rel_paths, report, conditions)

        self.assertEqual([len(group["paths"]) for group in groups], [3, 1, 1])
        self.assertEqual([group["date"] for group in groups], ["260522-260603", "260603", "260522"])
        labels = " ".join(group["condition_label"] for group in groups)
        self.assertIn("type lib", labels)
        self.assertIn("type zib", labels)
        self.assertIn("voltage 0.2~1.5v", labels)
        self.assertTrue(groups[0]["label"].startswith("260522-260603"))
        self.assertNotIn("P001", groups[0]["label"])

    def test_sample_thickness_label_parses_single_and_layered_t_tokens(self):
        self.assertEqual(ui.sample_thickness_label({"sample": "pure4T"}), "4T")
        self.assertEqual(ui.sample_thickness_label({"sample": "DL pc 2T2T"}), "2T2T")
        self.assertEqual(ui.sample_thickness_label({"sample": "dl pure 5T_#3 7T_9532"}), "5T+7T")
        self.assertEqual(ui.sample_thickness_label({"sample": "no thickness"}), "—")

    def test_capacity_all_data_row_is_last(self):
        rows = viewer_service.capacity_comparison_cluster_rows(
            [
                {
                    "date": "260603",
                    "protocol_type": CAPACITY_PROTOCOL_TYPE_3,
                    "protocol_label": "rate performance",
                    "cell_type": "LIB",
                    "voltage_range": "0.01~2v",
                    "ratio": "0.95",
                    "paths": ["a.csv"],
                    "condition_label": "type lib",
                }
            ],
            4,
        )

        self.assertEqual(rows[-1]["Date"], "all")
        self.assertEqual(rows[-1]["Protocol"], "rate performance")
        self.assertEqual(rows[-1]["protocol_type"], CAPACITY_PROTOCOL_TYPE_3)

    def test_eis_standard_labels_sort_latest_and_put_all_last(self):
        conditions = {
            "k1": {"cell_type": "LIB", "voltage_range": "0.01~2V", "ratio": "0.95", "electrolyte": "E", "binder": "B"},
            "k2": {"cell_type": "ZIB", "voltage_range": "0.2~1.5V", "ratio": "0.7", "electrolyte": "E", "binder": "B"},
        }
        clusters = [
            SimpleNamespace(source_paths="260522/a.SEO;260522/b.SEO", condition_keys="k1", file_count=2, loading_min=7.0, loading_max=7.2),
            SimpleNamespace(source_paths="260603/c.SEO;260603/d.SEO", condition_keys="k2", file_count=2, loading_min=8.0, loading_max=8.1),
            SimpleNamespace(source_paths="260604/e.SEO", condition_keys="k2", file_count=1, loading_min=8.0, loading_max=8.0, cluster_role="independent"),
        ]
        clusters = sorted(clusters, key=lambda cluster: viewer_service.date_sort_key(viewer_service.eis_cluster_date(cluster.source_paths)))
        rows = viewer_service.eis_comparison_rows(clusters, conditions, 5)

        self.assertEqual([row["Date"] for row in rows], ["260604", "260603", "260522", "all"])
        self.assertEqual(rows[0]["Mode"], "EIS independent")
        self.assertTrue(viewer_service.eis_comparison_option_label(clusters[0], conditions).startswith("260604"))
        self.assertIn("EIS independent", viewer_service.eis_comparison_option_label(clusters[0], conditions))
        self.assertNotIn("C999", rows[-1]["Mode"])

    def test_eis_time_series_standard_labels_use_folder_date(self):
        conditions = {"k1": {"cell_type": "LIB", "voltage_range": "0.01~2V", "ratio": "0.95", "electrolyte": "E", "binder": "B"}}
        group = SimpleNamespace(
            folder_date="260603",
            condition_key="k1",
            condition_sample="sample",
            cluster_signature="sig",
            time_points="0,1,24",
            file_count=3,
        )

        label = viewer_service.eis_time_series_option_label(group, conditions)
        rows = viewer_service.eis_time_series_rows([group], conditions)

        self.assertTrue(label.startswith("260603"))
        self.assertIn("EIS time", label)
        self.assertEqual(rows[0]["Date"], "260603")

    def test_overlay_999_label_is_fourth_when_clusters_exist(self):
        labels = insert_overlay_999_label(["C001", "C002", "C003", "C004"], "C999")

        self.assertEqual(labels, ["C001", "C002", "C003", "C999", "C004"])

    def test_density_stack_label_positions_use_charge_mean_and_right_collision_avoidance(self):
        series = [
            {"points": [(1, 10), (8, 12), (10, 14)], "label": "lower", "short_label": "b", "charge_mean_capacity": 12.0},
            {"points": [(1, 20), (7, 22), (9, 24)], "label": "higher", "short_label": "a", "charge_mean_capacity": 22.0},
        ]

        positions = overlay_density_stack_label_positions(series, lambda value: value * 10, lambda value: value * 10, 0, 0, 500, 500)

        self.assertLess(positions[1][1], positions[0][1])
        self.assertGreater(positions[0][0], 350)
        self.assertAlmostEqual((positions[0][1] + positions[1][1]) / 2, 170)
        self.assertEqual(positions[0][2], "left_corner")


if __name__ == "__main__":
    unittest.main()
