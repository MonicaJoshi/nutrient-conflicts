# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Service layer tests (the shared path used by CLI, API and GUI)."""

from __future__ import annotations

import unittest

from nutrient_conflicts.services import NotFoundError
from support import StoreTestCase


class SearchTests(StoreTestCase):
    def test_search_by_brand(self):
        payload = self.service.search_foods(brand="test brand")
        self.assertEqual(payload["total"], 2)
        self.assertEqual({item["fdc_id"] for item in payload["items"]}, {900001, 900004})

    def test_search_by_text_and_upc(self):
        self.assertEqual(self.service.search_foods(query="spinach")["total"], 2)
        self.assertEqual(self.service.search_foods(query="000000000002")["total"], 1)

    def test_search_by_category(self):
        payload = self.service.search_foods(category="Dietary Supplements")
        self.assertEqual(payload["total"], 2)

    def test_paging_and_limit_clamping(self):
        first = self.service.search_foods(limit=1, offset=0)
        second = self.service.search_foods(limit=1, offset=1)
        self.assertEqual(len(first["items"]), 1)
        self.assertNotEqual(first["items"][0]["fdc_id"], second["items"][0]["fdc_id"])
        self.assertLessEqual(self.service.search_foods(limit=9999)["limit"], self.service.max_limit)

    def test_brand_listing_is_ordered_by_product_count(self):
        payload = self.service.brands()
        self.assertEqual(payload["items"][0]["foods"], 2)

    def test_brand_prefix_filter(self):
        payload = self.service.brands(prefix="iron")
        self.assertEqual([item["brand_owner"] for item in payload["items"]], ["Iron Works"])


class FoodTests(StoreTestCase):
    def test_unknown_food_raises(self):
        with self.assertRaises(NotFoundError):
            self.service.get_food(123456)

    def test_unknown_food_message_explains_exclusion(self):
        with self.assertRaises(NotFoundError) as context:
            self.service.get_food(900003)
        self.assertIn("no_ingredients_in_source", str(context.exception))

    def test_raw_ingredients_are_read_from_the_source(self):
        payload = self.service.raw_ingredients(900002)
        self.assertIn("CONCENTRATED JUICES OF", payload["ingredients"])

    def test_raw_ingredients_unknown_food(self):
        with self.assertRaises(NotFoundError):
            self.service.raw_ingredients(999999)


class EvaluationTests(StoreTestCase):
    def test_evaluation_contains_scores_and_findings(self):
        payload = self.service.evaluate_food(900001)
        self.assertEqual(payload["scores"]["classification"], "net_antagonistic")
        self.assertGreater(payload["scores"]["antagonism"], 0)
        self.assertTrue(any(item["active"] for item in payload["findings"]))
        self.assertIn("disclaimer", payload)
        self.assertIn("nutrient_list", payload)
        self.assertIn("summary", payload)
        self.assertIn("warning", payload)
        self.assertTrue(payload["antagonistic"])
        self.assertTrue(payload["warning"]["required"])
        self.assertIn("registered dietitian", payload["warning"]["text"])

    def test_evaluation_is_cached_and_reused(self):
        self.service.evaluate_food(900001)
        self.service.evaluate_food(900001)
        stats = self.service.cache_stats()
        self.assertGreaterEqual(stats["l1_hits"] + stats["l2_hits"], 1)

    def test_min_dv_override_is_honoured(self):
        payload = self.service.evaluate_food(900001, min_dv_percent=500)
        self.assertFalse([item for item in payload["findings"]
                          if item["quantity_known"] and item["active"]])

    def test_serving_basis(self):
        payload = self.service.evaluate_food(900001, basis="serving")
        self.assertEqual(payload["basis"], "serving")

    def test_brand_rollup(self):
        payload = self.service.evaluate_brand("Test Brand", limit=10)
        self.assertEqual(payload["total_products"], 2)
        self.assertEqual(len(payload["products"]), 2)
        self.assertIn("interactions", payload["aggregate"])
        self.assertTrue(payload["summary"]["statement"])

    def test_brand_rollup_unknown_brand(self):
        with self.assertRaises(NotFoundError):
            self.service.evaluate_brand("No Such Brand")

    def test_adhoc_evaluation(self):
        payload = self.service.evaluate_adhoc(["IRON", "VITAMIN_C"])
        self.assertEqual(payload["food"]["brand_owner"], "(adhoc)")
        self.assertTrue(payload["findings"])

    def test_interactions_filtering(self):
        payload = self.service.interactions(relation="SYNERGISTIC", nutrient="IRON")
        self.assertTrue(payload["count"] >= 1)
        self.assertTrue(all(item["relation"] == "SYNERGISTIC" for item in payload["interactions"]))
        self.assertTrue(all("IRON" in (item["nutrient_a"], item["nutrient_b"]) for item in payload["interactions"]))

    def test_nutrients_and_crosswalk(self):
        nutrients = {item["nutrient_key"] for item in self.service.nutrients()["nutrients"]}
        self.assertIn("SELENIUM", nutrients)
        rules = self.service.crosswalk()
        self.assertTrue(any(rule["keyword"] == "SPINACH" for rule in rules))


class AdminTests(StoreTestCase):
    def test_health_reports_reference_counts(self):
        health = self.service.health()
        self.assertEqual(health["status"], "ok")
        self.assertGreater(health["reference"]["interactions"], 10)
        self.assertTrue(health["store"]["exists"])

    def test_cache_clear(self):
        self.service.evaluate_food(900001)
        payload = self.service.cache_clear()
        self.assertGreaterEqual(payload["cleared"], 1)


class FamilyProbeTests(StoreTestCase):
    def test_measured_but_not_derived_nutrient_is_listed(self):
        payload = self.service.evaluate_food(900005)
        keys = {item["nutrient_key"] for item in payload["nutrients"]}
        self.assertIn("PHYTATE", keys)
        self.assertIn("SODIUM", keys)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
