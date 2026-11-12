# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Reference data and crosswalk tests."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.config import Config
from nutrient_conflicts.ingredients import IngredientParser
from nutrient_conflicts.reference import ReferenceData, ReferenceError

CONFIG = Config.load(profile="test")


class ReferenceLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ReferenceData.load(CONFIG)

    def test_nutrients_and_daily_values_are_loaded(self):
        self.assertIn("IRON", self.reference.nutrients)
        self.assertAlmostEqual(self.reference.nutrients["IRON"].dv_amount, 18.0)
        self.assertEqual(self.reference.nutrients["VITAMIN_A"].primary_usda_id, 1106)

    def test_interactions_are_symmetric_and_unique(self):
        pairs = [item.pair for item in self.reference.interactions]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(all(a < b for a, b in pairs))

    def test_known_pairs_from_the_research_chart(self):
        self.assertEqual(self.reference.interaction_for("IRON", "VITAMIN_C").relation, "SYNERGISTIC")
        self.assertEqual(self.reference.interaction_for("CALCIUM", "IRON").relation, "ANTAGONISTIC")
        self.assertEqual(self.reference.interaction_for("COPPER", "ZINC").relation, "ANTAGONISTIC")
        self.assertEqual(self.reference.interaction_for("PHYTATE", "CALCIUM").relation, "ANTAGONISTIC")

    def test_context_dependent_pairs_record_conflicting_claims(self):
        interaction = self.reference.interaction_for("CALCIUM", "VITAMIN_D")
        self.assertEqual(interaction.relation, "SYNERGISTIC")
        self.assertIn(interaction.relation, {"SYNERGISTIC", "CONTEXT_DEPENDENT"})

    def test_usda_percent_dv_conversion(self):
        amount, percent, unit = self.reference.dv_percent("VITAMIN_D", 400.0, 1110)
        self.assertAlmostEqual(amount, 10.0)
        self.assertAlmostEqual(percent, 50.0)
        self.assertEqual(unit, "UG")


class CrosswalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ReferenceData.load(CONFIG)

    def keys(self, phrase: str) -> set[str]:
        return {match.nutrient_key for match in self.reference.resolve_phrase(phrase)}

    def test_whole_food_source(self):
        self.assertEqual(self.keys("SPINACH"), {"IRON", "VITAMIN_B9", "VITAMIN_K", "OXALATE"})

    def test_additive_is_tagged(self):
        matches = self.reference.resolve_phrase("CALCIUM PROPIONATE")
        self.assertEqual([(item.nutrient_key, item.source_kind) for item in matches], [("CALCIUM", "additive")])

    def test_food_source_outranks_additive(self):
        matches = self.reference.resolve_phrase("MILK")
        self.assertEqual([(item.nutrient_key, item.source_kind) for item in matches], [("CALCIUM", "food")])

    def test_blocked_by_suppresses_false_positive(self):
        self.assertEqual(self.keys("SOY LECITHIN"), set())
        self.assertEqual(self.keys("SOYBEAN OIL"), set())

    def test_unblocked_keyword_still_matches(self):
        self.assertIn("PHYTATE", self.keys("SOY PROTEIN ISOLATE"))

    def test_multiple_nutrients_from_one_phrase(self):
        self.assertEqual(self.keys("SODIUM PHOSPHATE"), {"SODIUM", "PHOSPHORUS"})

    def test_word_boundary_prevents_partial_match(self):
        self.assertEqual(self.keys("EGGPLANT"), set())
        self.assertEqual(self.keys("EGG WHITES"), {"SELENIUM", "VITAMIN_B12"})

    def test_derive_nutrients_collects_provenance(self):
        parser = IngredientParser(CONFIG)
        phrases = parser.parse("SPINACH, SALT, WATER")
        derived = {item.nutrient_key: item for item in self.reference.derive_nutrients(phrases)}
        self.assertEqual(derived["IRON"].phrases, ("SPINACH",))
        self.assertEqual(derived["SODIUM"].source_kind, "food")
        self.assertNotIn("WATER", derived)

    def test_unknown_phrase_is_neutral(self):
        self.assertEqual(self.keys("XANTHAN GUM"), set())


class ReferenceValidationTests(unittest.TestCase):
    def test_unknown_nutrient_in_interactions_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference_dir = Path(tmp)
            for name in ("nutrient_aliases.csv", "dv_reference.csv",
                         "nutrient_interactions.csv", "ingredient_nutrient_map.csv"):
                shutil.copy(Path(CONFIG.path("reference.dir")) / name, reference_dir / name)
            target = reference_dir / "nutrient_interactions.csv"
            target.write_text(target.read_text(encoding="utf-8")
                              + "BOGUS,NUTRIENT,ANTAGONISTIC,weak,chart,test,,,test\n", encoding="utf-8")
            config = CONFIG.with_overrides({"reference.dir": str(reference_dir)})
            with self.assertRaises(ReferenceError):
                ReferenceData.load(config)

    def test_unknown_daily_value_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference_dir = Path(tmp)
            for name in ("nutrient_aliases.csv", "dv_reference.csv",
                         "nutrient_interactions.csv", "ingredient_nutrient_map.csv"):
                shutil.copy(Path(CONFIG.path("reference.dir")) / name, reference_dir / name)
            target = reference_dir / "dv_reference.csv"
            target.write_text(target.read_text(encoding="utf-8") + "NOT_A_NUTRIENT,1,MG,test\n", encoding="utf-8")
            config = CONFIG.with_overrides({"reference.dir": str(reference_dir)})
            with self.assertRaises(ReferenceError):
                ReferenceData.load(config)

    def test_missing_reference_file_is_reported(self):
        missing = str(Path(tempfile.mkdtemp(prefix="nc-missing-")) / "absent")
        config = CONFIG.with_overrides({"reference.dir": missing, "reference.source.dir": missing})
        with self.assertRaises(ReferenceError):
            ReferenceData.load(config)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
