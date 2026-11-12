# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Tests for result file 3: the brand level conflict scan and the brand attribute."""

from __future__ import annotations

import unittest

from nutrient_conflicts.brandscan import BrandConflictScanner
from nutrient_conflicts.services import NotFoundError
from support import StoreTestCase


class BrandScanTests(StoreTestCase):
    def setUp(self) -> None:
        self.service.reference_build()

    def test_scan_persists_every_brand_pair(self):
        summary = self.service.conflicts_build(force=True)
        self.assertEqual(summary["pairs"], 58)
        self.assertGreaterEqual(summary["rows"], 1)
        self.assertGreaterEqual(summary["brands"], 3)
        self.assertTrue(self.service.brand_scanner.csv_path.is_file())
        self.assertTrue(self.service.brand_scanner.report_path.is_file())

    def test_second_build_is_skipped(self):
        self.service.conflicts_build(force=True)
        summary = self.service.conflicts_build()
        self.assertIn("skipped", summary)

    def test_status_is_current_after_the_build(self):
        self.service.conflicts_build(force=True)
        status = self.service.conflicts_status()
        self.assertTrue(status["exists"])
        self.assertFalse(status["stale"], status["reasons"])

    def test_clear_removes_results_and_marks_them_missing(self):
        self.service.conflicts_build(force=True)
        payload = self.service.conflicts_clear()
        self.assertTrue(payload["removed"])
        self.assertFalse(self.service.conflicts_status()["exists"])

    def test_rescan_is_required_when_the_knowledge_base_changes(self):
        self.service.conflicts_build(force=True)
        kb = self.service.config.path("reference.file.interactions")
        kb.write_text(kb.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        reasons = " ".join(self.service.conflicts_status()["reasons"])
        self.assertIn("reference knowledge base changed", reasons)


class BrandAttributeTests(StoreTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.service.reference_build()
        cls.service.conflicts_build(force=True)

    def test_report_lists_brand_nutrients_and_pairs(self):
        payload = self.service.brand_conflicts("Test Brand")
        self.assertEqual(payload["brand_owner"], "Test Brand")
        self.assertEqual(payload["total_products"], 2)
        nutrients = {item["nutrient_key"] for item in payload["nutrients"]}
        self.assertIn("IRON", nutrients)
        self.assertIn("CALCIUM", nutrients)
        pairs = {(item["nutrient_a"], item["nutrient_b"]): item for item in payload["interactions"]}
        self.assertEqual(pairs[("CALCIUM", "IRON")]["relation"], "ANTAGONISTIC")
        self.assertTrue(pairs[("CALCIUM", "IRON")]["highlighted"])
        self.assertEqual(pairs[("IRON", "VITAMIN_C")]["relation"], "SYNERGISTIC")
        self.assertFalse(pairs[("IRON", "VITAMIN_C")]["highlighted"])

    def test_antagonistic_pairs_carry_a_dietitian_warning(self):
        payload = self.service.brand_conflicts("Test Brand")
        self.assertTrue(payload["summary"]["conflicting"])
        self.assertIn("antagonistic", payload["summary"]["statement"])
        warning = payload["warning"]
        self.assertTrue(warning["required"])
        self.assertIn("registered dietitian", warning["text"])
        self.assertTrue(warning["quantities"])
        self.assertTrue(warning["next_steps"])
        self.assertIn("Calcium + Iron", warning["pairs"])

    def test_quantities_are_reported_or_flagged_as_missing(self):
        payload = self.service.brand_conflicts("Test Brand")
        quantities = " ".join(payload["warning"]["quantities"])
        self.assertIn("% DV", quantities)
        self.assertIn("quantity not published", quantities)

    def test_co_occurrence_distinguishes_same_product_from_same_brand(self):
        payload = self.service.brand_conflicts("Test Brand")
        by_pair = {item["pair"].upper(): item for item in payload["interactions"]}
        self.assertGreaterEqual(by_pair["CALCIUM + IRON"]["co_occurring_products"], 1)

    def test_brand_lookup_is_case_insensitive(self):
        lower = self.service.brand_conflicts("test brand")
        upper = self.service.brand_conflicts("TEST BRAND")
        self.assertEqual(lower["brand_owner"], "Test Brand")
        self.assertEqual(lower["summary"], upper["summary"])

    def test_brand_without_conflicts_says_so(self):
        brands = [item["brand_owner"] for item in self.service.brands(limit=50)["items"]]
        quiet = [brand for brand in brands
                 if brand and brand != "Test Brand" and not self.service.brand_scanner.brand_rows(brand)]
        if not quiet:
            self.skipTest("the fixture has no brand without conflicts")
        payload = self.service.brand_conflicts(quiet[0])
        self.assertFalse(payload["summary"]["conflicting"])
        self.assertFalse(payload["warning"]["required"])
        self.assertIn("No conflicting nutrient combinations", payload["summary"]["statement"])

    def test_unknown_brand_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.brand_conflicts("No Such Brand At All")

    def test_blank_brand_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.brand_conflicts("   ")

    def test_scan_is_reusable_without_rebuilding(self):
        scanner = BrandConflictScanner(self.service.config)
        self.assertGreaterEqual(len(scanner.brand_rows("Test Brand")), 1)
        self.assertIn("Test Brand", scanner.brand_names())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
