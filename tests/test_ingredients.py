# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Ingredient parsing tests (rules a, b, c plus the real-world normalisations)."""

from __future__ import annotations

import unittest

from nutrient_conflicts.config import Config
from nutrient_conflicts.ingredients import IngredientParser

CONFIG = Config.load(profile="test")


class IngredientParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = IngredientParser(CONFIG)

    def texts(self, raw: str) -> list[str]:
        return [phrase.text for phrase in self.parser.parse(raw)]

    def test_rule_a_ignores_boiler_plate_prefixes(self):
        result = self.texts(
            "INGREDIENTS: WATER, SALT, MADE FROM: OATS, CONTAINS LESS THAN 2% OF: ZINC OXIDE, "
            "CONCENTRATED JUICES OF GRAPE"
        )
        self.assertEqual(result, ["WATER", "SALT", "OATS", "ZINC OXIDE", "GRAPE"])

    def test_rule_a_tolerates_spacing_before_the_colon(self):
        result = self.texts("INGREDIENTS : EGG WHITE, SALT; CONTAINS 2% OR LESS OF : GUAR GUM")
        self.assertEqual(result, ["EGG WHITE", "SALT", "GUAR GUM"])

    def test_rule_b_splits_on_comma_and_semicolon(self):
        self.assertEqual(self.texts("WATER, SALT; SUGAR"), ["WATER", "SALT", "SUGAR"])

    def test_rule_c_sub_ingredients_replace_the_parent(self):
        result = self.texts("MIREPOIX (CARROTS, CELERY, ONIONS), SALT")
        self.assertEqual(result, ["CARROTS", "CELERY", "ONIONS", "SALT"])
        self.assertNotIn("MIREPOIX", result)

    def test_square_brackets_behave_like_parentheses(self):
        result = self.texts("CHICKEN BASE [COOKED CHICKEN, SALT], WATER")
        self.assertEqual(result, ["COOKED CHICKEN", "SALT", "WATER"])

    def test_nested_groups_flatten_to_leaves(self):
        result = self.texts("BASE [COOKED CHICKEN (CHICKEN MEAT, WATER), SUGAR]")
        self.assertEqual(result, ["CHICKEN MEAT", "WATER", "SUGAR"])

    def test_percentage_group_is_dropped_and_parent_kept(self):
        result = self.texts("BUTTER (18%) (MILK), SUGAR")
        self.assertEqual(result, ["MILK", "SUGAR"])

    def test_pure_percentage_group_keeps_parent(self):
        result = self.texts("BUTTER (18%), SUGAR")
        self.assertEqual(result, ["BUTTER", "SUGAR"])

    def test_qualifier_group_keeps_parent(self):
        result = self.texts("POTASSIUM SORBATE (PRESERVATIVE), SUGAR")
        self.assertEqual(result, ["POTASSIUM SORBATE", "SUGAR"])

    def test_ampersand_splits_items(self):
        self.assertEqual(self.texts("SALT & PEPPER"), ["SALT", "PEPPER"])

    def test_marketing_marks_and_punctuation_are_stripped(self):
        self.assertEqual(self.texts("CHEDDAR CHEESE**."), ["CHEDDAR CHEESE"])

    def test_empty_and_blank_input(self):
        self.assertEqual(self.parser.parse(""), [])
        self.assertEqual(self.parser.parse(None), [])
        self.assertEqual(self.parser.parse("   "), [])

    def test_duplicates_are_collapsed_and_indexes_are_sequential(self):
        phrases = self.parser.parse("SALT, SALT, SUGAR")
        self.assertEqual([item.text for item in phrases], ["SALT", "SUGAR"])
        self.assertEqual([item.index for item in phrases], [0, 1])

    def test_unbalanced_group_does_not_crash(self):
        result = self.texts("FLOUR (WHEAT FLOUR, NIACIN")
        self.assertTrue(result)
        self.assertIn("NIACIN", result)

    def test_overlong_phrase_is_dropped(self):
        long_phrase = "A" * (CONFIG.get_int("parse.max.phrase.length", 80) + 1)
        self.assertEqual(self.texts(long_phrase + ", SALT"), ["SALT"])

    def test_numeric_only_phrase_is_dropped(self):
        self.assertEqual(self.texts("2%, SALT"), ["SALT"])

    def test_phrase_limit_is_enforced(self):
        parser = IngredientParser(CONFIG.with_overrides({"parse.max.phrases.per.food": "3"}))
        self.assertEqual(len(parser.parse("A, B, C, D, E")), 3)

    def test_real_fixture_row(self):
        raw = ("INGREDIENTS: SPINACH, CREAM (MILK, CARRAGEENAN), WHEAT FLOUR, SALT, FOLIC ACID, "
               "CONTAINS LESS THAN 2% OF: CALCIUM PROPIONATE (PRESERVATIVE), ZINC OXIDE.")
        self.assertEqual(
            self.texts(raw),
            ["SPINACH", "MILK", "CARRAGEENAN", "WHEAT FLOUR", "SALT", "FOLIC ACID",
             "CALCIUM PROPIONATE", "ZINC OXIDE"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
