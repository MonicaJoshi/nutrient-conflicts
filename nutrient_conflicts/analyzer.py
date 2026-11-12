# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Pure nutrient interaction analysis.

No database, no network, no interface code: the analyzer takes a food record plus
its derived (ingredient based) and measured (quantity based) nutrient profiles and
returns an :class:`Evaluation`. That keeps CLI, API and GUI on identical logic.
"""

from __future__ import annotations

from typing import Iterable

from .config import Config
from .models import Evaluation, Finding, FoodRecord, MeasuredAmount, NutrientPresence
from .reference import Interaction, ReferenceData


class Analyzer:
    def __init__(self, config: Config, reference: ReferenceData) -> None:
        self.config = config
        self.reference = reference
        self.min_dv_percent = config.get_float("analysis.min.dv.percent", 5.0)
        self.include_additives = config.get_bool("analysis.include.additives.in.score", False)
        self.include_context_dependent = config.get_bool("analysis.include.context.dependent", True)
        self.quantity_unknown_activates = config.get_bool("analysis.quantity.unknown.activates", True)
        self.quantity_unknown_in_score = config.get_bool("analysis.quantity.unknown.in.score", False)
        self.unknown_pressure = config.get_float("analysis.unknown.quantity.pressure", 0.5)
        self.pressure_cap = config.get_float("analysis.pressure.cap", 1.0)
        self.neutral_band = config.get_float("analysis.net.neutral.band", 0.1)
        self.strength_weights = {key: float(value) for key, value in
                                 config.get_map("analysis.strength.weights").items()}
        self.evidence_weights = {key: float(value) for key, value in
                                 config.get_map("analysis.evidence.weights").items()}
        self.top_findings = config.get_int("analysis.top.findings", 25)
        self.disclaimer = config.get("app.disclaimer", "")
        self.inferred_keys = set(config.get_list("analysis.inferred.nutrients"))

    # -- public API ------------------------------------------------------
    def evaluate(self, food: FoodRecord, derived: Iterable[NutrientPresence],
                 measured: dict[str, MeasuredAmount], basis: str = "per_100g") -> Evaluation:
        derived_list = list(derived)
        presence = {item.nutrient_key: item for item in derived_list}
        factor, basis_note = self._basis_factor(food, basis)
        evaluation = Evaluation(food=food, basis=basis, min_dv_percent=self.min_dv_percent,
                                disclaimer=self.disclaimer)
        if basis_note:
            evaluation.notes.append(basis_note)
        evaluation.nutrients = self._nutrient_rows(presence, measured, factor)
        if not presence:
            evaluation.notes.append("no nutrient could be derived from the ingredient list")
            evaluation.classification = "unknown"
            return evaluation

        findings: list[Finding] = []
        for interaction in self.reference.interactions:
            if interaction.relation == "CONTEXT_DEPENDENT" and not self.include_context_dependent:
                continue
            if interaction.nutrient_a not in presence or interaction.nutrient_b not in presence:
                continue
            findings.append(self._build_finding(interaction, presence, measured, factor))
        findings.sort(key=lambda item: (not item.active, -item.pressure, item.nutrient_a, item.nutrient_b))
        evaluation.findings = findings
        self._score(evaluation, findings, presence)
        return evaluation

    def evaluate_adhoc(self, nutrient_keys: Iterable[str]) -> Evaluation:
        """Evaluate a hand supplied nutrient list (what-if, no brand data)."""
        keys = {key.strip().upper() for key in nutrient_keys if key.strip()}
        unknown = sorted(keys - set(self.reference.nutrients))
        if unknown:
            raise ValueError(f"unknown nutrient keys: {', '.join(unknown)}")
        derived = [NutrientPresence(nutrient_key=key, source_kind="food", phrases=("(manual)",))
                   for key in sorted(keys)]
        food = FoodRecord(fdc_id=0, brand_owner="(adhoc)", description="Ad hoc nutrient list")
        evaluation = self.evaluate(food, derived, {})
        if unknown:
            evaluation.notes.append("unknown keys ignored")
        return evaluation

    # -- internals -------------------------------------------------------
    def _basis_factor(self, food: FoodRecord, basis: str) -> tuple[float, str]:
        if basis != "serving":
            return 1.0, ""
        unit = (food.serving_size_unit or "").strip().lower()
        if food.serving_size and unit in {"g", "gram", "grams", "ml", "milliliter", "millilitre"}:
            factor = float(food.serving_size) / 100.0
            return factor, (f"quantities scaled to one serving of {food.serving_size:g} {unit} "
                            f"(USDA data is stored per 100 g)")
        return 1.0, ("serving size is missing or not a mass/volume unit; quantities stay per 100 g")

    def _nutrient_rows(self, presence: dict[str, NutrientPresence],
                       measured: dict[str, MeasuredAmount], factor: float) -> list[dict]:
        keys = sorted(set(presence) | set(measured))
        rows = []
        for key in keys:
            definition = self.reference.nutrients[key]
            item = presence.get(key)
            amount = measured.get(key)
            rows.append({
                "nutrient_key": key,
                "name": definition.canonical_name,
                "category": definition.category,
                "unit": definition.dv_unit,
                "source_kind": item.source_kind if item else "measured",
                "phrases": list(item.phrases) if item else [],
                "inferred": key in self.inferred_keys,
                "amount": None if amount is None else round(amount.amount * factor, 4),
                "dv_percent": None if amount is None else round(amount.dv_percent * factor, 2),
            })
        return rows

    def _build_finding(self, interaction: Interaction, presence: dict[str, NutrientPresence],
                       measured: dict[str, MeasuredAmount], factor: float) -> Finding:
        key_a, key_b = interaction.nutrient_a, interaction.nutrient_b
        presence_a, presence_b = presence[key_a], presence[key_b]
        measured_a, measured_b = measured.get(key_a), measured.get(key_b)
        dv_a = _dv(measured_a, self.reference.nutrients[key_a].dv_amount, factor)
        dv_b = _dv(measured_b, self.reference.nutrients[key_b].dv_amount, factor)
        quantity_known = dv_a is not None and dv_b is not None
        if quantity_known:
            active = dv_a >= self.min_dv_percent and dv_b >= self.min_dv_percent
            pressure = min(dv_a, dv_b) / 100.0
            reason = (f"both nutrients at or above {self.min_dv_percent:g}% DV"
                      if active else
                      f"below the {self.min_dv_percent:g}% DV threshold")
        else:
            active = self.quantity_unknown_activates
            pressure = self.unknown_pressure
            reason = "measured quantity unavailable; pair judged on ingredient presence"
        pressure = max(0.0, min(pressure, self.pressure_cap))
        return Finding(
            nutrient_a=key_a,
            nutrient_b=key_b,
            relation=interaction.relation,
            strength=interaction.strength,
            evidence_level=interaction.evidence_level,
            mechanism=interaction.mechanism,
            dose_note=interaction.dose_note,
            source=interaction.source,
            active=active,
            quantity_known=quantity_known,
            pressure=pressure if active else 0.0,
            dv_percent_a=dv_a,
            dv_percent_b=dv_b,
            source_kind_a=presence_a.source_kind,
            source_kind_b=presence_b.source_kind,
            evidences_a=presence_a.phrases,
            evidences_b=presence_b.phrases,
            reason=reason,
        )

    def _score(self, evaluation: Evaluation, findings: list[Finding],
               presence: dict[str, NutrientPresence]) -> None:
        synergy = 0.0
        antagonism = 0.0
        counts = {"nutrients_present": len(presence), "pairs_present": len(findings),
                  "pairs_active": 0, "synergistic": 0, "antagonistic": 0,
                  "context_dependent": 0, "quantity_unknown": 0,
                  "excluded_additive": 0, "pairs_inactive": 0}
        additive_excluded = 0
        unknown_in_score = 0
        has_additive = False
        competing: dict[str, list[dict]] = {}
        neutral = set(presence)
        for finding in findings:
            neutral.discard(finding.nutrient_a)
            neutral.discard(finding.nutrient_b)
            if not finding.active:
                counts["pairs_inactive"] += 1
                continue
            counts["pairs_active"] += 1
            # competing influences describe direction conflicts, so they are recorded
            # for every active pair even when the pair is excluded from the score
            if finding.relation in {"SYNERGISTIC", "ANTAGONISTIC"}:
                sign = 1.0 if finding.relation == "SYNERGISTIC" else -1.0
                for key, other in ((finding.nutrient_a, finding.nutrient_b),
                                   (finding.nutrient_b, finding.nutrient_a)):
                    competing.setdefault(key, []).append(
                        {"with": other, "relation": finding.relation,
                         "contribution": round(sign * finding.pressure, 4)})
            if not finding.quantity_known:
                counts["quantity_unknown"] += 1
                if not self.quantity_unknown_in_score:
                    unknown_in_score += 1
                    continue
            if "additive" in (finding.source_kind_a, finding.source_kind_b) and not self.include_additives:
                additive_excluded += 1
                has_additive = True
                continue
            weight = (self.strength_weights.get(finding.strength, 0.5)
                      * self.evidence_weights.get(finding.evidence_level, 0.5))
            contribution = weight * finding.pressure
            if finding.relation == "SYNERGISTIC":
                synergy += contribution
                counts["synergistic"] += 1
            elif finding.relation == "ANTAGONISTIC":
                antagonism += contribution
                counts["antagonistic"] += 1
            else:
                counts["context_dependent"] += 1
        evaluation.synergy_score = synergy
        evaluation.antagonism_score = antagonism
        evaluation.net_score = synergy - antagonism
        active_pairs = counts["pairs_active"]
        if synergy == 0.0 and antagonism == 0.0 and not active_pairs:
            evaluation.classification = "neutral"
        elif synergy == 0.0 and antagonism == 0.0:
            # pairs are present but every one of them was excluded from the score
            # (unknown quantity, additive, or context dependent), so no net direction exists
            evaluation.classification = "unknown"
        elif evaluation.net_score > self.neutral_band:
            evaluation.classification = "net_synergistic"
        elif evaluation.net_score < -self.neutral_band:
            evaluation.classification = "net_antagonistic"
        else:
            evaluation.classification = "mixed"
        counts["excluded_additive"] = additive_excluded
        counts["nutrients_neutral"] = len(neutral)
        evaluation.counts = counts
        evaluation.neutral_nutrients = sorted(neutral)
        evaluation.competing_influences = {
            key: sorted(items, key=lambda item: -item["contribution"])
            for key, items in competing.items()
            if len({item["relation"] for item in items}) > 1
        }
        if unknown_in_score:
            evaluation.notes.append(
                f"{unknown_in_score} active pair(s) excluded from the score because the measured "
                "quantity is unknown (analysis.quantity.unknown.in.score=false)")
        if has_additive:
            evaluation.notes.append(
                f"{additive_excluded} pair(s) involve a fortificant or preservative salt and are shown "
                "without contributing to the score (analysis.include.additives.in.score=false)")


def _dv(measured: MeasuredAmount | None, dv_amount: float, factor: float) -> float | None:
    """Percent DV, or None when the quantity (or the daily value) is unknown."""
    if measured is None or dv_amount <= 0:
        return None
    return measured.dv_percent * factor
