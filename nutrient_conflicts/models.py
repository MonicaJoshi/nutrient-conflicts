# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Shared data models used by every interface (CLI, API, GUI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceKind = Literal["food", "additive"]
Relation = Literal["SYNERGISTIC", "ANTAGONISTIC", "CONTEXT_DEPENDENT"]


@dataclass(frozen=True)
class NutrientPresence:
    """A nutrient found in a food, with the ingredient items that revealed it."""

    nutrient_key: str
    source_kind: SourceKind
    phrases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"nutrient_key": self.nutrient_key, "source_kind": self.source_kind, "phrases": list(self.phrases)}


@dataclass(frozen=True)
class MeasuredAmount:
    """Quantity of a nutrient per 100 g, normalised to the daily-value unit."""

    nutrient_key: str
    amount: float
    unit: str
    dv_percent: float

    def as_dict(self) -> dict[str, Any]:
        return {"nutrient_key": self.nutrient_key, "amount": round(self.amount, 4),
                "unit": self.unit, "dv_percent": round(self.dv_percent, 2)}


@dataclass(frozen=True)
class FoodRecord:
    fdc_id: int
    brand_owner: str = ""
    description: str = ""
    data_type: str = ""
    food_category_id: str = ""
    brand_name: str = ""
    gtin_upc: str = ""
    serving_size: float = 0.0
    serving_size_unit: str = ""
    ingredient_list: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "fdc_id": self.fdc_id,
            "brand_owner": self.brand_owner,
            "description": self.description,
            "data_type": self.data_type,
            "food_category_id": self.food_category_id,
            "brand_name": self.brand_name,
            "gtin_upc": self.gtin_upc,
            "serving_size": self.serving_size,
            "serving_size_unit": self.serving_size_unit,
            "ingredient_list": list(self.ingredient_list),
        }


@dataclass(frozen=True)
class Finding:
    """One nutrient pair that is present in the food."""

    nutrient_a: str
    nutrient_b: str
    relation: str
    strength: str
    evidence_level: str
    mechanism: str
    dose_note: str
    source: str
    active: bool
    quantity_known: bool
    pressure: float
    dv_percent_a: float | None
    dv_percent_b: float | None
    source_kind_a: str
    source_kind_b: str
    evidences_a: tuple[str, ...] = ()
    evidences_b: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "nutrient_a": self.nutrient_a,
            "nutrient_b": self.nutrient_b,
            "relation": self.relation,
            "strength": self.strength,
            "evidence_level": self.evidence_level,
            "mechanism": self.mechanism,
            "dose_note": self.dose_note,
            "source": self.source,
            "active": self.active,
            "quantity_known": self.quantity_known,
            "pressure": round(self.pressure, 4),
            "dv_percent_a": None if self.dv_percent_a is None else round(self.dv_percent_a, 2),
            "dv_percent_b": None if self.dv_percent_b is None else round(self.dv_percent_b, 2),
            "source_kind_a": self.source_kind_a,
            "source_kind_b": self.source_kind_b,
            "evidences_a": list(self.evidences_a),
            "evidences_b": list(self.evidences_b),
            "reason": self.reason,
        }


@dataclass
class Evaluation:
    food: FoodRecord
    basis: str
    min_dv_percent: float
    nutrients: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    neutral_nutrients: list[str] = field(default_factory=list)
    competing_influences: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    synergy_score: float = 0.0
    antagonism_score: float = 0.0
    net_score: float = 0.0
    classification: str = "neutral"
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    disclaimer: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "food": self.food.as_dict(),
            "basis": self.basis,
            "min_dv_percent": self.min_dv_percent,
            "nutrients": self.nutrients,
            "findings": [finding.as_dict() for finding in self.findings],
            "neutral_nutrients": self.neutral_nutrients,
            "competing_influences": self.competing_influences,
            "scores": {
                "synergy": round(self.synergy_score, 4),
                "antagonism": round(self.antagonism_score, 4),
                "net": round(self.net_score, 4),
                "classification": self.classification,
            },
            "counts": self.counts,
            "notes": self.notes,
            "disclaimer": self.disclaimer,
        }


@dataclass
class BrandEvaluation:
    brand_owner: str
    total_foods: int
    evaluated_foods: int
    products: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand_owner": self.brand_owner,
            "total_foods": self.total_foods,
            "evaluated_foods": self.evaluated_foods,
            "products": self.products,
            "aggregate": self.aggregate,
            "notes": self.notes,
        }
