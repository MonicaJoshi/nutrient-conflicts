# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""HTTP API tests using FastAPI's TestClient."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from nutrient_conflicts.api import create_app
from support import StoreTestCase


class ApiTests(StoreTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = TestClient(create_app(cls.config))

    def test_health_and_openapi(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["status"], "ok")
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/foods/{fdc_id}/evaluation", schema.json()["paths"])

    def test_nutrients_and_interactions(self):
        nutrients = self.client.get("/nutrients").json()["nutrients"]
        self.assertTrue(any(item["nutrient_key"] == "IRON" for item in nutrients))
        pairs = self.client.get("/interactions", params={"relation": "ANTAGONISTIC"}).json()
        self.assertTrue(all(item["relation"] == "ANTAGONISTIC" for item in pairs["interactions"]))
        self.assertIn("crosswalk", self.client.get("/crosswalk").json())

    def test_search_and_get_food(self):
        results = self.client.get("/foods/search", params={"brand": "Test Brand"}).json()
        self.assertEqual(results["total"], 2)
        food = self.client.get("/foods/900001").json()
        self.assertEqual(food["food"]["brand_owner"], "Test Brand")
        self.assertTrue(food["derived_nutrients"])

    def test_evaluation_endpoint(self):
        response = self.client.get("/foods/900001/evaluation", params={"basis": "serving", "min_dv": 5})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["basis"], "serving")
        self.assertIn("scores", payload)

    def test_brand_endpoint(self):
        payload = self.client.get("/brands/Test Brand/evaluation", params={"size": 5}).json()
        self.assertEqual(payload["total_products"], 2)
        self.assertEqual(len(payload["products"]), 2)

    def test_adhoc_post(self):
        payload = self.client.post("/evaluate", json={"nutrient_keys": ["IRON", "VITAMIN_C"]}).json()
        self.assertTrue(payload["findings"])
        batch = self.client.post("/evaluate", json={"fdc_ids": [900001, 900002]}).json()
        self.assertEqual(len(batch["results"]), 2)

    def test_adhoc_requires_input(self):
        response = self.client.post("/evaluate", json={})
        self.assertEqual(response.status_code, 422)

    def test_unknown_nutrient_is_422_problem_json(self):
        response = self.client.post("/evaluate", json={"nutrient_keys": ["UNOBTAINIUM"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_request")

    def test_unknown_food_is_404_problem_json(self):
        response = self.client.get("/foods/123456")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_admin_endpoints(self):
        status = self.client.get("/admin/store").json()
        self.assertTrue(status["exists"])
        self.assertIn("l1_entries", self.client.get("/admin/cache").json())
        self.assertGreaterEqual(self.client.delete("/admin/cache").json()["cleared"], 0)

    def test_gui_is_served_by_fastify_not_fastapi(self):
        # The React GUI is a separate Fastify process; the API only exposes JSON and docs.
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 404)


class ReferenceAndConflictApiTests(StoreTestCase):
    """The new admin endpoints and the brand conflict attribute."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = TestClient(create_app(cls.config))
        cls.client.post("/admin/reference/build")

    def test_reference_status_and_rebuild(self):
        status = self.client.get("/admin/reference").json()
        self.assertEqual(status["merged"]["pairs"], 58)
        self.assertFalse(status["stale"], status["reasons"])
        rebuilt = self.client.post("/admin/reference/build").json()
        self.assertEqual(rebuilt["merged"]["pairs"], 58)

    def test_conflicts_admin_and_attribute(self):
        status = self.client.get("/admin/conflicts").json()
        self.assertTrue(status["exists"])
        self.assertFalse(status["stale"], status["reasons"])

        payload = self.client.get("/brands/Test Brand/conflicts").json()
        self.assertEqual(payload["brand_owner"], "Test Brand")
        self.assertTrue(payload["aggregate"]["nutrients"])
        self.assertTrue(payload["aggregate"]["interactions"])
        self.assertTrue(payload["summary"]["conflicting"])
        self.assertTrue(payload["warning"]["required"])
        self.assertIn("registered dietitian", payload["warning"]["text"])
        self.assertTrue(payload["products"])

    def test_conflict_rebuild_and_clear(self):
        self.assertEqual(self.client.post("/admin/conflicts/build", params={"force": True})
                         .status_code, 200)
        cleared = self.client.delete("/admin/conflicts").json()
        self.assertTrue(cleared["removed"])
        self.assertFalse(self.client.get("/admin/conflicts").json()["exists"])
        self.client.post("/admin/conflicts/build")

    def test_unknown_brand_conflicts_is_problem_json(self):
        response = self.client.get("/brands/Nope Nothing/conflicts")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_health_reports_brand_conflicts(self):
        health = self.client.get("/health").json()
        self.assertIn("brand_conflicts", health)
        self.assertGreaterEqual(health["brand_conflicts"]["rows"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
