"""Regression tests for material EDA parsing risks; no workbook authoring."""
import unittest
from scripts.eda import parse_date, month_label, column_profile, identify, cross_checks, TOTAL_RE


class EDATests(unittest.TestCase):
    def test_month_and_date_parsing(self):
        self.assertEqual(month_label("сент. 2026"), "2026-09")
        self.assertIsNone(month_label("Коэф. сезонности 2026"))
        self.assertIsNone(parse_date("Итого"))
        self.assertEqual(parse_date("22.09.2026 10:30:00").year, 2026)

    def test_signs_errors_blanks_are_distinct(self):
        p = column_profile([-10, 0, 15, None, "#N/A"], [2,3,4,5,6], "Количество", "H")
        self.assertEqual(p["numeric"]["sum"], 5)
        self.assertEqual(p["numeric"]["negative"], 1)
        self.assertEqual(p["null_count"], 1)
        self.assertEqual(p["error_count"], 1)

    def test_roles_use_content_and_inventory_needs_explicit_evidence(self):
        a = [["Номенклатура.Код", "янв. 2024"], [None, "Количество"], ["001_", 10]]
        self.assertEqual(identify(a)[3], "monthly_quantity_unspecified")
        a.insert(2, [None, "нач. остаток"])
        self.assertEqual(identify(a)[3], "monthly_beginning_inventory")
        self.assertEqual(identify([["Номенклатура.Код", "Кратность"]])[3], "order_constraints")

    def test_coverage_requires_valid_constraint(self):
        a = {"id":"moq", "supplier":"SE", "role":"order_constraints", "skus":{"001_","002_","003_"},
             "constraints":{"001_":1,"002_":0,"003_":"#N/A"}, "months":{}}
        b = {"id":"sales", "supplier":"SE", "role":"transactions", "skus":{"001_","002_","003_","004_"},
             "constraints":{}, "months":{}}
        result = cross_checks([a,b])["constraint_coverage"][0]
        self.assertEqual(result["usable_positive_skus"], 1)
        self.assertEqual(result["absent_skus"], 1)
        self.assertEqual(result["invalid_or_missing_value_skus"], 2)

    def test_totals_do_not_match_substrings(self):
        self.assertTrue(TOTAL_RE.match("Итого:"))
        self.assertFalse(TOTAL_RE.match("Светильник total white"))


if __name__ == "__main__":
    unittest.main()
