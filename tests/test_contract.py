# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Contract tests for the response required by the project brief."""

from __future__ import annotations

import unittest

from support import StoreTestCase

REQUIRED = {
    "brand", "fdc_id", "description", "food_category_id", "data_type", "brand_owner",
    "nutrient_list", "synergistic", "antagonistic", "summary", "warning",
}


class ProductContractTests(StoreTestCase):
    def test_product_has_every_required_field(self):
        payload = self.service.evaluate_food(900001)
        self.assertTrue(REQUIRED.issubset(payload), REQUIRED - set(payload))
        self.assertEqual(payload["brand"], "Spinach Dip")
        self.assertEqual(payload["brand_owner"], "Test Brand")
        self.assertEqual(payload["data_type"], "branded_food")
        self.assertTrue(payload["nutrient_list"])
        self.assertIn("MILK", payload["nutrient_list"])

    def test_synergistic_and_antagonistic_are_separate_tables(self):
        payload = self.service.evaluate_food(900001)
        self.assertTrue(all(row["relation"] == "SYNERGISTIC" for row in payload["synergistic"]))
        self.assertTrue(all(row["relation"] == "ANTAGONISTIC" for row in payload["antagonistic"]))
        self.assertTrue(payload["antagonistic"])
        self.assertTrue(all(row["highlighted"] for row in payload["antagonistic"]))

    def test_summary_and_warning_include_quantities(self):
        payload = self.service.evaluate_food(900001)
        self.assertTrue(payload["summary"]["conflicting"])
        self.assertIn("antagonistic", payload["summary"]["statement"])
        warning = payload["warning"]
        self.assertTrue(warning["required"])
        self.assertIn("registered dietitian", warning["text"])
        self.assertTrue(warning["pairs"])
        self.assertTrue(warning["quantities"])
        self.assertTrue(any("MG" in item or "UG" in item for item in warning["quantities"]))
        self.assertTrue(warning["next_steps"])

    def test_no_warning_when_only_synergistic_pairs(self):
        payload = self.service.evaluate_food(900005)
        if not payload["antagonistic"]:
            self.assertFalse(payload["warning"]["required"])


class BrandContractTests(StoreTestCase):
    def test_brand_returns_products_and_aggregate(self):
        payload = self.service.brand_conflicts("Test Brand", page=1, size=1)
        self.assertEqual(payload["total_products"], 2)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(len(payload["products"]), 1)
        self.assertIn("interactions", payload["aggregate"])
        self.assertIn("summary", payload)
        self.assertIn("warning", payload)
        self.assertTrue(REQUIRED.issubset(payload["products"][0]))

    def test_second_page_returns_a_different_product(self):
        first = self.service.brand_conflicts("Test Brand", page=1, size=1)["products"][0]["fdc_id"]
        second = self.service.brand_conflicts("Test Brand", page=2, size=1)["products"][0]["fdc_id"]
        self.assertNotEqual(first, second)


class PersistenceContractTests(StoreTestCase):
    def test_jsonl_contains_the_persisted_fields(self):
        record = self.service.store.warm_cache(self.service.cache, 5)
        self.assertGreater(record, 0)
        cached = self.service.cache.get("food_index", "900001")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["brand_owner"], "Test Brand")
        self.assertTrue(cached["nutrient_list"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
