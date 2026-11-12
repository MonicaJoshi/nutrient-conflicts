# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""End-to-end ingest and persistence tests against the fixture dataset."""

from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.datasource import DatasetSource
from nutrient_conflicts.ingest import Ingestor
from nutrient_conflicts.ingredients import IngredientParser
from nutrient_conflicts.reference import ReferenceData
from nutrient_conflicts.store import Store
from support import StoreTestCase, fixture_config


class IngestTests(StoreTestCase):
    def test_foods_without_ingredients_are_excluded(self):
        self.assertEqual(self.service.store_status()["counts"]["foods"], 5)
        with self.assertRaises(LookupError):
            self.service.get_food(900003)

    def test_store_is_not_stale_after_build(self):
        status = self.service.store_status()
        self.assertTrue(status["exists"])
        self.assertFalse(status["stale"], status["reasons"])
        self.assertEqual(status["manifest"]["excluded_without_ingredients"], "1")

    def test_derived_nutrients_are_persisted(self):
        payload = self.service.get_food(900001)
        derived = {item["nutrient_key"]: item for item in payload["derived_nutrients"]}
        self.assertEqual(derived["CALCIUM"]["source_kind"], "food")
        self.assertIn("MILK", derived["CALCIUM"]["phrases"])
        self.assertIn("PHYTATE", derived)
        self.assertIn("IRON", derived)

    def test_measured_quantities_are_normalised(self):
        payload = self.service.get_food(900001)
        measured = {item["nutrient_key"]: item for item in payload["measured_nutrients"]}
        self.assertAlmostEqual(measured["CALCIUM"]["amount"], 120.0)
        self.assertAlmostEqual(measured["CALCIUM"]["dv_percent"], 9.23, places=2)
        self.assertEqual(measured["CALCIUM"]["unit"], "MG")

    def test_second_ingest_is_skipped_when_current(self):
        summary = self.service.ingest()
        self.assertIn("skipped", summary["exports"])

    def test_parser_change_marks_the_store_stale(self):
        stale_config = self.config.with_overrides({"parse.separators": ",;&|"})
        service = type(self.service)(stale_config)
        self.assertTrue(service.store_status()["stale"])

    def test_exports_are_written(self):
        store = Store(self.config)
        self.assertTrue(store.jsonl_path.is_file())
        with gzip.open(store.jsonl_path, "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted(rows[0].keys()),
                         ["brand", "brand_owner", "data_type", "description",
                          "fdc_id", "food_category_id", "nutrient_keys", "nutrient_list"])
        first = {str(row["fdc_id"]): row for row in rows}["900001"]
        self.assertIn("CALCIUM", first["nutrient_keys"])
        self.assertIn("MILK", first["nutrient_list"])
        self.assertEqual(first["data_type"], "branded_food")
        self.assertEqual(first["brand_owner"], "Test Brand")
        self.assertEqual(first["food_category_id"], "Dips and Salsas")

    def test_export_can_be_regenerated(self):
        payload = self.service.export()
        self.assertEqual(payload["rows"], 5)


class FreshIngestTests(unittest.TestCase):
    def test_build_force_rebuilds_and_purge_removes_files(self):
        workdir = Path(tempfile.mkdtemp(prefix="NutrientConflicts-fresh-"))
        try:
            config = fixture_config(workdir)
            store = Store(config)
            source = DatasetSource(config)
            ingestor = Ingestor(config, store, source, ReferenceData.load(config), IngredientParser(config))
            summary = ingestor.build(force=True)
            self.assertEqual(summary.foods, 5)
            self.assertGreater(summary.derived_nutrients, 0)
            self.assertTrue(store.path.is_file())

            removed = store.purge()
            self.assertEqual(len(removed["removed"]), 2)
            self.assertFalse(store.path.exists())
            self.assertTrue(store.status()["reasons"])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
