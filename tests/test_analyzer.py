# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Analyzer tests: thresholds, scoring, additive handling, competing influences."""

from __future__ import annotations

import unittest

from nutrient_conflicts.analyzer import Analyzer
from nutrient_conflicts.config import Config
from nutrient_conflicts.ingredients import IngredientParser
from nutrient_conflicts.models import FoodRecord, MeasuredAmount
from nutrient_conflicts.reference import ReferenceData

CONFIG = Config.load(profile="test")
REFERENCE = ReferenceData.load(CONFIG)
PARSER = IngredientParser(CONFIG)

SPINACH_DIP = ("INGREDIENTS: SPINACH, CREAM (MILK, CARRAGEENAN), WHEAT FLOUR, SALT, FOLIC ACID, "
               "CONTAINS LESS THAN 2% OF: CALCIUM PROPIONATE (PRESERVATIVE), ZINC OXIDE.")
SUPPLEMENT = "ASCORBIC ACID, FERROUS SULFATE, MICROCRYSTALLINE CELLULOSE, MAGNESIUM STEARATE"
IRON_PLUS = "SPINACH, ASCORBIC ACID, CALCIUM CARBONATE"


def measured(pairs: dict[str, tuple[float, int]]) -> dict[str, MeasuredAmount]:
    result = {}
    for key, (amount, usda_id) in pairs.items():
        normalised, percent, unit = REFERENCE.dv_percent(key, amount, usda_id)
        result[key] = MeasuredAmount(nutrient_key=key, amount=normalised, unit=unit, dv_percent=percent)
    return result


def evaluate(ingredients: str, quantities: dict[str, tuple[float, int]] | None = None,
             config: Config = CONFIG, basis: str = "per_100g", food: FoodRecord | None = None):
    analyzer = Analyzer(config, REFERENCE)
    derived = REFERENCE.derive_nutrients(PARSER.parse(ingredients))
    record = food or FoodRecord(fdc_id=1, brand_owner="Test", description="fixture")
    return analyzer.evaluate(record, derived, measured(quantities or {}), basis=basis)


class ThresholdTests(unittest.TestCase):
    def test_pairs_above_threshold_are_active(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087),
                                            "ZINC": (1.0, 1095), "SODIUM": (400.0, 1093)})
        finding = _finding(evaluation, "CALCIUM", "IRON")
        self.assertTrue(finding.active)
        self.assertAlmostEqual(finding.dv_percent_a, 9.23, places=2)
        self.assertAlmostEqual(finding.pressure, 0.0923, places=3)

    def test_pairs_below_threshold_are_dormant(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (0.1, 1089), "CALCIUM": (120.0, 1087)})
        finding = _finding(evaluation, "CALCIUM", "IRON")
        self.assertFalse(finding.active)
        self.assertEqual(finding.pressure, 0.0)

    def test_threshold_override_changes_the_verdict(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087)},
                              config=CONFIG.with_overrides({"analysis.min.dv.percent": "50"}))
        finding = _finding(evaluation, "CALCIUM", "IRON")
        self.assertFalse(finding.active)

    def test_missing_quantity_activates_and_is_excluded_from_score(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087)})
        phytate = _finding(evaluation, "CALCIUM", "PHYTATE")
        self.assertTrue(phytate.active)
        self.assertFalse(phytate.quantity_known)
        self.assertTrue(any("measured quantity is unknown" in note for note in evaluation.notes))

    def test_serving_basis_scales_quantities(self):
        food = FoodRecord(fdc_id=1, brand_owner="Test", description="fixture",
                          serving_size=30.0, serving_size_unit="g")
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087)},
                              basis="serving", food=food)
        finding = _finding(evaluation, "CALCIUM", "IRON")
        self.assertFalse(finding.active)
        self.assertTrue(any("scaled to one serving" in note for note in evaluation.notes))

    def test_count_based_serving_falls_back_to_per_100g(self):
        food = FoodRecord(fdc_id=1, brand_owner="Test", description="fixture",
                          serving_size=1.0, serving_size_unit="bar")
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087)},
                              basis="serving", food=food)
        self.assertTrue(any("per 100 g" in note for note in evaluation.notes))


class ScoringTests(unittest.TestCase):
    def test_net_negative_classification(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087),
                                            "ZINC": (1.0, 1095), "SODIUM": (400.0, 1093)})
        self.assertGreater(evaluation.antagonism_score, evaluation.synergy_score)
        self.assertEqual(evaluation.classification, "net_antagonistic")

    def test_additive_pairs_are_shown_but_not_scored(self):
        evaluation = evaluate(SUPPLEMENT, {"IRON": (18.0, 1089), "VITAMIN_C": (90.0, 1162)})
        finding = _finding(evaluation, "IRON", "VITAMIN_C")
        self.assertTrue(finding.active)
        self.assertEqual(evaluation.synergy_score, 0.0)
        self.assertTrue(any("fortificant or preservative" in note for note in evaluation.notes))

    def test_additives_can_be_scored_when_enabled(self):
        evaluation = evaluate(SUPPLEMENT, {"IRON": (18.0, 1089), "VITAMIN_C": (90.0, 1162)},
                              config=CONFIG.with_overrides({"analysis.include.additives.in.score": "true"}))
        self.assertGreater(evaluation.synergy_score, 0.0)
        self.assertEqual(evaluation.classification, "net_synergistic")

    def test_competing_influences_are_reported(self):
        evaluation = evaluate(IRON_PLUS, {"IRON": (3.0, 1089), "VITAMIN_C": (30.0, 1162),
                                          "CALCIUM": (200.0, 1087)})
        self.assertIn("IRON", evaluation.competing_influences)
        relations = {item["relation"] for item in evaluation.competing_influences["IRON"]}
        self.assertEqual(relations, {"SYNERGISTIC", "ANTAGONISTIC"})

    def test_counts_are_consistent(self):
        evaluation = evaluate(SPINACH_DIP, {"IRON": (2.0, 1089), "CALCIUM": (120.0, 1087)})
        counts = evaluation.counts
        self.assertEqual(counts["pairs_present"], len(evaluation.findings))
        self.assertEqual(counts["pairs_present"], counts["pairs_active"] + counts["pairs_inactive"])

    def test_no_derived_nutrients_yields_unknown(self):
        evaluation = evaluate("WATER, CITRIC ACID")
        self.assertEqual(evaluation.classification, "unknown")
        self.assertEqual(evaluation.findings, [])

    def test_pairs_present_but_unscored_are_not_called_neutral(self):
        # SUPPLEMENT maps VITAMIN_C to an additive, so the pair is shown but excluded
        # from the score and no net direction can be claimed.
        evaluation = evaluate(SUPPLEMENT, {"IRON": (18.0, 1089), "VITAMIN_C": (90.0, 1162)})
        self.assertTrue(any(item.active for item in evaluation.findings))
        self.assertEqual(evaluation.classification, "unknown")


class ContextDependentTests(unittest.TestCase):
    def test_context_dependent_can_be_switched_off(self):
        analyzer = Analyzer(CONFIG, REFERENCE)
        with_context = analyzer.evaluate_adhoc(["VITAMIN_A", "VITAMIN_E", "IRON", "VITAMIN_C"])
        relations = {item.relation for item in with_context.findings}
        self.assertIn("CONTEXT_DEPENDENT", relations)
        self.assertIn("SYNERGISTIC", relations)

        without = Analyzer(CONFIG.with_overrides({"analysis.include.context.dependent": "false"}),
                           REFERENCE).evaluate_adhoc(["VITAMIN_A", "VITAMIN_E", "IRON", "VITAMIN_C"])
        self.assertFalse([item for item in without.findings if item.relation == "CONTEXT_DEPENDENT"])
        self.assertTrue([item for item in without.findings if item.relation == "SYNERGISTIC"])


class AdhocTests(unittest.TestCase):
    def test_adhoc_nutrient_list(self):
        analyzer = Analyzer(CONFIG, REFERENCE)
        evaluation = analyzer.evaluate_adhoc(["IRON", "VITAMIN_C", "CALCIUM"])
        self.assertEqual(evaluation.food.fdc_id, 0)
        keys = {(item.nutrient_a, item.nutrient_b) for item in evaluation.findings}
        self.assertIn(("IRON", "VITAMIN_C"), keys)
        self.assertIn(("CALCIUM", "IRON"), keys)

    def test_adhoc_unknown_key_is_rejected(self):
        analyzer = Analyzer(CONFIG, REFERENCE)
        with self.assertRaises(ValueError):
            analyzer.evaluate_adhoc(["UNOBTAINIUM"])


def _finding(evaluation, a: str, b: str):
    pair = tuple(sorted((a, b)))
    for finding in evaluation.findings:
        if (finding.nutrient_a, finding.nutrient_b) == pair:
            return finding
    seen = [(f.nutrient_a, f.nutrient_b) for f in evaluation.findings]
    raise AssertionError(f"pair {pair} not found in {seen}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
