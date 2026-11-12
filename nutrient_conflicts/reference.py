# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Reference knowledge base: nutrient interactions, USDA aliases, daily values and
the ingredient keyword crosswalk.

Everything here is data driven. The three CSV files in ``referenceData`` are the
single source of truth and are meant to be reviewed and extended by a human.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import Config, ensure_reference_workspace
from .models import NutrientPresence

_WORD_SPLIT = re.compile(r"[^A-Z0-9]+")


class ReferenceError(RuntimeError):
    """Raised when the reference data is inconsistent."""


@dataclass(frozen=True)
class NutrientDefinition:
    nutrient_key: str
    canonical_name: str
    category: str
    dv_amount: float
    dv_unit: str
    usda_ids: tuple[int, ...] = ()
    primary_usda_id: int | None = None
    conversion_factor: float = 1.0


@dataclass(frozen=True)
class Interaction:
    nutrient_a: str
    nutrient_b: str
    relation: str
    strength: str
    evidence_level: str
    mechanism: str
    conflicting_claims: str
    dose_note: str
    source: str

    @property
    def pair(self) -> tuple[str, str]:
        return (self.nutrient_a, self.nutrient_b)

    def other(self, nutrient_key: str) -> str:
        return self.nutrient_b if nutrient_key == self.nutrient_a else self.nutrient_a


@dataclass(frozen=True)
class CrosswalkRule:
    keyword: str
    nutrient_key: str
    source_kind: str
    match_type: str
    blocked_by: tuple[str, ...]
    notes: str

    def matches(self, phrase: str) -> bool:
        if self.match_type == "exact":
            return phrase == self.keyword
        if self.match_type == "word":
            return _contains_word_sequence(phrase, self.keyword)
        return self.keyword in phrase

    def is_blocked(self, phrase: str) -> bool:
        return any(_contains_word_sequence(phrase, token) for token in self.blocked_by)


@dataclass(frozen=True)
class PhraseMatch:
    nutrient_key: str
    source_kind: str
    keyword: str
    match_type: str


@dataclass
class ReferenceData:
    nutrients: dict[str, NutrientDefinition] = field(default_factory=dict)
    interactions: list[Interaction] = field(default_factory=list)
    crosswalk: list[CrosswalkRule] = field(default_factory=list)
    usda_index: dict[int, tuple[str, float, str]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    interaction_index: dict[tuple[str, str], Interaction] = field(default_factory=dict)
    _phrase_cache: dict[str, list[PhraseMatch]] = field(default_factory=dict)

    # -- loading ---------------------------------------------------------
    @classmethod
    def load(cls, config: Config) -> "ReferenceData":
        reference = cls()
        ensure_reference_workspace(config)
        base = config.path("reference.dir")
        reference._load_aliases(base / config.get("reference.file.aliases"))
        reference._load_daily_values(base / config.get("reference.file.dv"))
        reference._load_interactions(base / config.get("reference.file.interactions"))
        reference._load_crosswalk(base / config.get("reference.file.crosswalk"))
        reference._validate()
        return reference

    def _load_aliases(self, path: Path) -> None:
        primary: dict[str, tuple[float, str, int]] = {}
        ids: dict[str, list[int]] = {}
        names: dict[str, tuple[str, str]] = {}
        for row in _read_csv(path):
            key = row["nutrient_key"].strip().upper()
            usda_id = int(row["usda_nutrient_id"])
            factor = float(row["conversion_factor"])
            unit = row["usda_unit"].strip().upper()
            self.usda_index[usda_id] = (key, factor, unit)
            ids.setdefault(key, []).append(usda_id)
            names.setdefault(key, (row["canonical_name"].strip(), row["category"].strip()))
            if row["is_primary"].strip().lower() == "true":
                primary[key] = (factor, unit, usda_id)
        for key, (name, category) in names.items():
            factor, unit, usda_id = primary.get(key, (1.0, "", ids[key][0]))
            self.nutrients[key] = NutrientDefinition(
                nutrient_key=key,
                canonical_name=name,
                category=category,
                dv_amount=0.0,
                dv_unit=unit,
                usda_ids=tuple(ids[key]),
                primary_usda_id=usda_id,
                conversion_factor=factor,
            )

    def _load_daily_values(self, path: Path) -> None:
        for row in _read_csv(path):
            key = row["nutrient_key"].strip().upper()
            if key not in self.nutrients:
                raise ReferenceError(f"daily value for unknown nutrient key: {key}")
            existing = self.nutrients[key]
            self.nutrients[key] = NutrientDefinition(
                nutrient_key=key,
                canonical_name=existing.canonical_name,
                category=existing.category,
                dv_amount=float(row["dv_amount"]),
                dv_unit=row["dv_unit"].strip().upper() or existing.dv_unit,
                usda_ids=existing.usda_ids,
                primary_usda_id=existing.primary_usda_id,
                conversion_factor=existing.conversion_factor,
            )

    def _load_interactions(self, path: Path) -> None:
        seen: dict[tuple[str, str], Interaction] = {}
        for row in _read_csv(path):
            a = row["nutrient_a"].strip().upper()
            b = row["nutrient_b"].strip().upper()
            pair = tuple(sorted((a, b)))
            interaction = Interaction(
                nutrient_a=pair[0],
                nutrient_b=pair[1],
                relation=row["relation"].strip().upper(),
                strength=row["strength"].strip().lower() or "moderate",
                evidence_level=row["evidence_level"].strip().lower() or "chart",
                mechanism=row["mechanism"].strip(),
                conflicting_claims=row.get("conflicting_claims", "").strip(),
                dose_note=row.get("dose_note", "").strip(),
                source=row.get("source", "").strip(),
            )
            if pair in seen and seen[pair] != interaction:
                raise ReferenceError(f"duplicate interaction for pair {pair}")
            seen[pair] = interaction
        self.interactions = sorted(seen.values(), key=lambda item: item.pair)
        self.interaction_index = {item.pair: item for item in self.interactions}

    def _load_crosswalk(self, path: Path) -> None:
        for row in _read_csv(path):
            blocked = tuple(token.strip().upper() for token in row.get("blocked_by", "").split("|") if token.strip())
            self.crosswalk.append(
                CrosswalkRule(
                    keyword=row["keyword"].strip().upper(),
                    nutrient_key=row["nutrient_key"].strip().upper(),
                    source_kind=row["source_kind"].strip().lower() or "food",
                    match_type=row["match_type"].strip().lower() or "contains",
                    blocked_by=blocked,
                    notes=row.get("notes", "").strip(),
                )
            )

    # -- validation ------------------------------------------------------
    def _validate(self) -> None:
        interaction_keys = {key for item in self.interactions for key in item.pair}
        missing = sorted(interaction_keys - set(self.nutrients))
        if missing:
            raise ReferenceError(f"interactions reference undefined nutrient keys: {', '.join(missing)}")
        undefined = sorted({rule.nutrient_key for rule in self.crosswalk} - set(self.nutrients))
        if undefined:
            raise ReferenceError(f"crosswalk references undefined nutrient keys: {', '.join(undefined)}")
        not_in_kb = sorted({rule.nutrient_key for rule in self.crosswalk} - interaction_keys)
        if not_in_kb:
            self.diagnostics.append(
                "crosswalk maps nutrients that appear in no interaction and will be neutral: " + ", ".join(not_in_kb)
            )
        for key, nutrient in self.nutrients.items():
            if nutrient.dv_amount <= 0:
                self.diagnostics.append(f"nutrient {key} has no daily value; quantity gating skipped")
        allowed_relations = {"SYNERGISTIC", "ANTAGONISTIC", "CONTEXT_DEPENDENT"}
        for interaction in self.interactions:
            if interaction.relation not in allowed_relations:
                raise ReferenceError(f"invalid relation {interaction.relation!r} for pair {interaction.pair}")

    # -- queries ---------------------------------------------------------
    @property
    def nutrient_keys(self) -> list[str]:
        return sorted(self.nutrients)

    def interaction_for(self, a: str, b: str) -> Interaction | None:
        return self.interaction_index.get(tuple(sorted((a, b))))

    def interactions_for(self, nutrient_key: str) -> list[Interaction]:
        return [item for item in self.interactions if nutrient_key in item.pair]

    def dv_percent(self, nutrient_key: str, amount: float, usda_id: int) -> tuple[float, float, str]:
        """Return (normalised amount, percent DV, unit)."""
        definition = self.nutrients[nutrient_key]
        entry = self.usda_index.get(usda_id)
        factor = entry[1] if entry else definition.conversion_factor
        unit = definition.dv_unit
        normalised = amount * factor
        if definition.dv_amount <= 0:
            return normalised, 0.0, unit
        return normalised, normalised / definition.dv_amount * 100.0, unit

    # -- crosswalk -------------------------------------------------------
    def resolve_phrase(self, phrase: str) -> list[PhraseMatch]:
        """Map one ingredient item to the nutrients it implies (memoised)."""
        text = phrase.strip().upper()
        if not text:
            return []
        cached = self._phrase_cache.get(text)
        if cached is not None:
            return cached
        matches: dict[str, PhraseMatch] = {}
        for rule in self.crosswalk:
            if not rule.matches(text) or rule.is_blocked(text):
                continue
            current = matches.get(rule.nutrient_key)
            if current is None or _prefer(rule, current):
                matches[rule.nutrient_key] = PhraseMatch(
                    nutrient_key=rule.nutrient_key,
                    source_kind=rule.source_kind,
                    keyword=rule.keyword,
                    match_type=rule.match_type,
                )
        result = sorted(matches.values(), key=lambda item: item.nutrient_key)
        self._phrase_cache[text] = result
        return result

    def derive_nutrients(self, phrases: Iterable[object]) -> list[NutrientPresence]:
        """Collapse ingredient items into a nutrient presence list."""
        collected: dict[str, dict[str, object]] = {}
        for phrase in phrases:
            text = getattr(phrase, "text", str(phrase))
            for match in self.resolve_phrase(text):
                entry = collected.setdefault(
                    match.nutrient_key, {"kinds": set(), "phrases": []}
                )
                entry["kinds"].add(match.source_kind)
                if text not in entry["phrases"]:
                    entry["phrases"].append(text)
        result: list[NutrientPresence] = []
        for key, entry in sorted(collected.items()):
            kinds = entry["kinds"]
            source_kind = "food" if "food" in kinds else "additive"
            result.append(
                NutrientPresence(
                    nutrient_key=key,
                    source_kind=source_kind,  # type: ignore[arg-type]
                    phrases=tuple(entry["phrases"]),  # type: ignore[arg-type]
                )
            )
        return result

    def as_dict(self) -> dict:
        return {
            "nutrients": [
                {
                    "nutrient_key": item.nutrient_key,
                    "canonical_name": item.canonical_name,
                    "category": item.category,
                    "dv_amount": item.dv_amount,
                    "dv_unit": item.dv_unit,
                    "usda_nutrient_ids": list(item.usda_ids),
                }
                for item in sorted(self.nutrients.values(), key=lambda n: n.nutrient_key)
            ],
            "interactions": [
                {
                    "nutrient_a": item.nutrient_a,
                    "nutrient_b": item.nutrient_b,
                    "relation": item.relation,
                    "strength": item.strength,
                    "evidence_level": item.evidence_level,
                    "mechanism": item.mechanism,
                    "conflicting_claims": item.conflicting_claims,
                    "dose_note": item.dose_note,
                    "source": item.source,
                }
                for item in self.interactions
            ],
            "crosswalk": [
                {
                    "keyword": rule.keyword,
                    "nutrient_key": rule.nutrient_key,
                    "source_kind": rule.source_kind,
                    "match_type": rule.match_type,
                    "blocked_by": list(rule.blocked_by),
                    "notes": rule.notes,
                }
                for rule in self.crosswalk
            ],
            "diagnostics": self.diagnostics,
        }


def _prefer(new_rule: CrosswalkRule, current: PhraseMatch) -> bool:
    """Food sources outrank additives; longer keywords outrank shorter ones."""
    if new_rule.source_kind != current.source_kind:
        return new_rule.source_kind == "food"
    return len(new_rule.keyword) > len(current.keyword)


def _contains_word_sequence(phrase: str, keyword: str) -> bool:
    if not keyword:
        return False
    haystack = f" {_WORD_SPLIT.sub(' ', phrase.upper()).strip()} "
    needle = f" {_WORD_SPLIT.sub(' ', keyword.upper()).strip()} "
    return needle in haystack


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ReferenceError(f"reference file not found: {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    filtered = "\n".join(lines)
    if not filtered.strip():
        return []
    return list(csv.DictReader(filtered.splitlines()))
