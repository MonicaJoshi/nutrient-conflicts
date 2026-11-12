# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Fold the source claims into the runtime interaction knowledge base.

Two documents can say different things about the same pair. The rules are explicit:

* a pair claimed in both directions (SYNERGISTIC *and* ANTAGONISTIC) is genuinely
  dose dependent, so it becomes ``CONTEXT_DEPENDENT`` and the conflicting claims are
  recorded in ``conflicting_claims``;
* "varies with dose" plus one direction keeps that direction (the direction is the
  stronger statement) and records the conflict;
* the surviving strength and evidence level are the strongest claim, and the
  mechanism comes from the claim with the strongest evidence (the detailed one).

The result is stable: the same inputs always produce the same rows in the same
order, so the merged file can be diffed and reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DIRECTIONS = {"SYNERGISTIC", "ANTAGONISTIC"}


class MergeError(RuntimeError):
    """Raised when regenerating the knowledge base would lose a known pair."""


@dataclass(frozen=True)
class Claim:
    """One statement about one pair, as made by one source document."""

    nutrient_a: str
    nutrient_b: str
    relation: str
    source: str
    evidence_level: str = "chart"
    strength: str = "moderate"
    mechanism: str = ""
    dose_note: str = ""

    @property
    def pair(self) -> tuple[str, str]:
        return tuple(sorted((self.nutrient_a, self.nutrient_b)))  # type: ignore[return-value]


@dataclass(frozen=True)
class MergedPair:
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

    def as_row(self) -> dict[str, str]:
        return {
            "nutrient_a": self.nutrient_a,
            "nutrient_b": self.nutrient_b,
            "relation": self.relation,
            "strength": self.strength,
            "evidence_level": self.evidence_level,
            "mechanism": self.mechanism,
            "conflicting_claims": self.conflicting_claims,
            "dose_note": self.dose_note,
            "source": self.source,
        }


def merge_claims(claims: Iterable[Claim], evidence_rank: dict[str, int],
                 strength_rank: dict[str, int]) -> list[MergedPair]:
    """Merge every claim about a pair into one row, sorted by pair."""
    grouped: dict[tuple[str, str], list[Claim]] = {}
    for claim in claims:
        if claim.nutrient_a == claim.nutrient_b:
            continue
        grouped.setdefault(claim.pair, []).append(claim)

    merged: list[MergedPair] = []
    for pair, group in sorted(grouped.items()):
        relations = {claim.relation for claim in group}
        relation = _resolve_relation(relations)
        ordered = sorted(group, key=lambda claim: (-evidence_rank.get(claim.evidence_level, 0),
                                                   claim.evidence_level, claim.source))
        strongest_strength = max(group, key=lambda claim: strength_rank.get(claim.strength, 0)).strength
        strongest_evidence = ordered[0].evidence_level
        mechanism = max(group, key=lambda claim: (evidence_rank.get(claim.evidence_level, 0),
                                                  len(claim.mechanism or ""))).mechanism
        dose_note = max((claim.dose_note for claim in group), key=len, default="")
        sources = []
        for claim in group:
            if claim.source not in sources:
                sources.append(claim.source)
        merged.append(MergedPair(
            nutrient_a=pair[0],
            nutrient_b=pair[1],
            relation=relation,
            strength=strongest_strength,
            evidence_level=strongest_evidence,
            mechanism=mechanism,
            conflicting_claims=_conflict_text(relations),
            dose_note=dose_note,
            source="; ".join(sources),
        ))
    return merged


def diff_against(existing: Iterable[dict[str, str]],
                 merged: Iterable[MergedPair]) -> dict[str, list]:
    """Compare a regenerated knowledge base with the one currently on disk."""
    old = {tuple(sorted((row["nutrient_a"].upper(), row["nutrient_b"].upper()))): row
           for row in existing}
    new = {pair.pair: pair for pair in merged}
    removed = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    relation_changed = []
    metadata_changed = []
    for pair in sorted(set(old) & set(new)):
        before, after = old[pair], new[pair]
        if before.get("relation", "").upper() != after.relation:
            relation_changed.append((pair, before.get("relation", ""), after.relation))
            continue
        changes = {}
        if before.get("strength", "").lower() != after.strength:
            changes["strength"] = (before.get("strength", ""), after.strength)
        if before.get("evidence_level", "").lower() != after.evidence_level:
            changes["evidence_level"] = (before.get("evidence_level", ""), after.evidence_level)
        if changes:
            metadata_changed.append((pair, changes))
    return {
        "removed": removed,
        "added": added,
        "relation_changed": relation_changed,
        "metadata_changed": metadata_changed,
    }


def guard(diff: dict[str, list], allow_removals: bool) -> None:
    """Fail when regeneration would silently lose or redirect a known pair."""
    if allow_removals:
        return
    problems: list[str] = []
    if diff["removed"]:
        problems.append("pairs that would disappear: "
                        + ", ".join(f"{a}/{b}" for a, b in diff["removed"]))
    if diff["relation_changed"]:
        problems.append("pairs whose relation would change: "
                        + ", ".join(f"{a}/{b} {before}->{after}"
                                    for (a, b), before, after in diff["relation_changed"]))
    if problems:
        raise MergeError(
            "; ".join(problems)
            + ". Rerun with reference.kb.allow.removals=true (or --allow-removals) if this is intended."
        )


def _resolve_relation(relations: set[str]) -> str:
    if DIRECTIONS <= relations:
        return "CONTEXT_DEPENDENT"
    for relation in ("SYNERGISTIC", "ANTAGONISTIC", "CONTEXT_DEPENDENT"):
        if relation in relations:
            return relation
    return "CONTEXT_DEPENDENT"


def _conflict_text(relations: set[str]) -> str:
    if len(relations) < 2:
        return ""
    return "/".join(sorted(relations))
