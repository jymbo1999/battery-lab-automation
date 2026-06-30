import unittest
from battery_lab.experiment_import import (
    IMPORT_JOURNAL_FIELDS, field_keys, fixed_defaults, variable_keys,
)


class ImportJournalFieldsTests(unittest.TestCase):
    def test_field_spec_has_16_fields_4_variable_12_fixed(self):
        self.assertEqual(len(IMPORT_JOURNAL_FIELDS), 16)
        self.assertEqual(len(variable_keys()), 4)
        self.assertEqual(sum(1 for f in IMPORT_JOURNAL_FIELDS if f["bucket"] == "fixed"), 12)

    def test_variable_fields_are_blank_user_filled(self):
        self.assertEqual(
            variable_keys(),
            ["date", "sample", "foil_electrode_g", "foil_electrode_mm"],
        )

    def test_fixed_defaults_match_spec(self):
        d = fixed_defaults()
        self.assertEqual(d["reference"], "12 파이_Cu foil")
        self.assertEqual(d["electrolyte"], "1.0M LiPF6 EC/DEC 1:1")
        self.assertEqual(d["cell_type"], "LIB")
        self.assertEqual(d["foil_g"], "0.009928")
        self.assertEqual(d["ratio"], "0.96")
        self.assertEqual(d["current_density"], "37.2")
        self.assertEqual(d["foil_thickness_mm"], "0.00958")
        self.assertEqual(d["drying_condition"], "60도 12시간")

    def test_each_field_maps_to_exact_excel_header(self):
        headers = {f["key"]: f["header"] for f in IMPORT_JOURNAL_FIELDS}
        self.assertEqual(headers["foil_thickness_mm"], "호일 두께(mm)")
        self.assertEqual(headers["foil_electrode_mm"], "전극(foil+electrode) 두께(mm)")
        self.assertEqual(headers["reference"], "참고")
        self.assertEqual(headers["cell_type"], "종류")


import math
from battery_lab.experiment_import import compute_derived_metadata


class ComputeDerivedTests(unittest.TestCase):
    def test_areal_mass_density_from_foil_inputs(self):
        derived = compute_derived_metadata(
            {"foil_electrode_g": "0.0150", "foil_g": "0.009928", "ratio": "0.96",
             "foil_electrode_mm": "0.020", "foil_thickness_mm": "0.00958"}
        )
        active = (0.0150 - 0.009928) * 0.96
        self.assertAlmostEqual(derived["active_material_g"], active, places=9)
        self.assertAlmostEqual(
            derived["areal_mass_density"], active * 1000 / (math.pi * 0.6 ** 2), places=6
        )

    def test_electrode_density_from_thickness(self):
        derived = compute_derived_metadata(
            {"foil_electrode_g": "0.0150", "foil_g": "0.009928", "ratio": "0.96",
             "foil_electrode_mm": "0.020", "foil_thickness_mm": "0.00958"}
        )
        electrode_g = 0.0150 - 0.009928
        thickness = 0.020 - 0.00958
        volume = 113.1 * thickness
        self.assertAlmostEqual(derived["electrode_density"], electrode_g / (volume / 1000), places=6)

    def test_missing_inputs_yield_none(self):
        self.assertIsNone(compute_derived_metadata({"foil_g": "0.009928"})["areal_mass_density"])


if __name__ == "__main__":
    unittest.main()
