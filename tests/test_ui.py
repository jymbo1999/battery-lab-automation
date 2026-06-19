import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from battery_lab import ui
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
                    "label": "sample A (ICE=91.2%, density=2.3 g/cm3) charge",
                    "short_label": "460_cell_Capacity",
                    "sample_label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "curve_kind": "Charge",
                    "marker_shape": "circle",
                    "condition": {"areal_mass_density": 7.1, "electrode_density": 2.3, "binder": "CMC/SBR"},
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
                    "label": "sample A (ICE=91.2%, density=2.3 g/cm3) discharge",
                    "short_label": "460_cell_Capacity",
                    "sample_label": "sample A (ICE=91.2%, density=2.3 g/cm3)",
                    "curve_kind": "Discharge",
                    "marker_shape": "square",
                    "condition": {"areal_mass_density": 7.1, "electrode_density": 2.3, "binder": "CMC/SBR"},
                    "metrics": {"ice_percent": 91.2, "first_discharge_capacity": 300, "last_discharge_capacity": 295, "retention@100": 98.3},
                    "match": None,
                    "protocol_cluster_id": "P001",
                    "protocol_label": "1번 · 0.1C continuous",
                    "protocol_reason": "filename rule",
                    "bend_count": 0,
                }
            ],
            performance_mode=True,
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
        self.assertIn(">Curve<", html)
        self.assertIn(">Charge<", html)
        self.assertIn(">Discharge<", html)
        self.assertIn("sample A (ICE=91.2%, density=2.3 g/cm3)", html)
        self.assertIn(">ICE<", html)
        self.assertIn(">Density<", html)
        self.assertIn("data-cx=", html)
        self.assertIn('fill-opacity=".24"', html)
        self.assertIn('stroke="#111111"', html)
        self.assertIn('stroke-width=".45"', html)
        self.assertIn('data-base-stroke-width=".45"', html)
        self.assertIn("<rect data-zoom-radius", html)
        self.assertIn(">Row<", html)
        self.assertIn(">Type<", html)
        self.assertIn(">Bends<", html)
        self.assertIn("P001 1번", html)
        self.assertIn(">First<", html)
        self.assertIn(">R@100<", html)

    def test_overlay_999_label_is_fourth_when_clusters_exist(self):
        labels = insert_overlay_999_label(["C001", "C002", "C003", "C004"], "C999")

        self.assertEqual(labels, ["C001", "C002", "C003", "C999", "C004"])

    def test_density_stack_label_positions_use_third_largest_x_and_areal_order(self):
        series = [
            {"points": [(1, 10), (8, 12), (10, 14)], "label": "high", "short_label": "b", "condition": {"areal_mass_density": 9.0}},
            {"points": [(1, 20), (7, 22), (9, 24)], "label": "low", "short_label": "a", "condition": {"areal_mass_density": 5.0}},
        ]

        positions = overlay_density_stack_label_positions(series, lambda value: value * 10, lambda value: value * 10, 0, 0, 500, 500)

        self.assertEqual(positions[1][0], 85)
        self.assertGreater(positions[1][1], positions[0][1])
        self.assertEqual(positions[0][2], "left_corner")


if __name__ == "__main__":
    unittest.main()
