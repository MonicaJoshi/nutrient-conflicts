# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Turn the spellings used by the reference documents into canonical nutrient keys.

``referenceData/nutrient_name_map.csv`` is the only place that knows that "Thiamin",
"Vitamin B1" and "VITAMIN B-1" all mean ``VITAMIN_B1``. Matching ignores case,
parenthetical qualifiers and punctuation, so a change in the source wording is a
one-line data edit, never a code change.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_WORD = re.compile(r"[^A-Z0-9]+")
_B_GAP = re.compile(r"\bB (\d+)")

SPLIT_PATTERN = re.compile(r"\s*(?:/|,|;|\band\b)\s*", re.IGNORECASE)


class UnknownNutrientError(LookupError):
    """Raised when a source document names a nutrient the map does not cover."""


def normalise_name(text: str) -> str:
    """Canonical comparison form: upper case, no qualifiers, no punctuation."""
    value = text.upper()
    value = _PARENTHETICAL.sub(" ", value)
    value = _NON_WORD.sub(" ", value).strip()
    value = _B_GAP.sub(r"B\1", value)  # "VITAMIN B 12" -> "VITAMIN B12"
    return re.sub(r"\s+", " ", value).strip()


class NutrientNameMap:
    """Mapping of document spellings to nutrient keys."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = {normalise_name(name): key for name, key in mapping.items()}
        self._keys = set(mapping.values())

    @classmethod
    def load(cls, path: Path) -> "NutrientNameMap":
        if not path.is_file():
            raise FileNotFoundError(f"nutrient name map not found: {path}")
        lines = [line for line in path.read_text(encoding="utf-8").splitlines()
                 if not line.lstrip().startswith("#")]
        mapping: dict[str, str] = {}
        for row in csv.DictReader(lines):
            name = (row.get("name") or "").strip()
            key = (row.get("nutrient_key") or "").strip().upper()
            if name and key:
                mapping[name] = key
        if not mapping:
            raise ValueError(f"nutrient name map is empty: {path}")
        return cls(mapping)

    @property
    def keys(self) -> set[str]:
        return set(self._keys)

    def resolve(self, name: str) -> str:
        key = self._mapping.get(normalise_name(name))
        if key is None:
            raise UnknownNutrientError(
                f"no nutrient key for {name!r}; add it to referenceData/nutrient_name_map.csv"
            )
        return key

    def resolve_cell(self, cell: str) -> list[str]:
        """Split one table cell ("Calcium / Zinc, Iron") into nutrient keys."""
        keys: list[str] = []
        for part in SPLIT_PATTERN.split(cell or ""):
            part = part.strip()
            if not part:
                continue
            key = self.resolve(part)
            if key not in keys:
                keys.append(key)
        return keys


def preferred_display(path: Path) -> dict[str, str]:
    """Pick one readable label per nutrient key from the name map."""
    best: dict[str, tuple[int, str]] = {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")] if path.is_file() else []
    for row in csv.DictReader(lines):
        name = (row.get("name") or "").strip()
        key = (row.get("nutrient_key") or "").strip().upper()
        if not name or not key:
            continue
        score = (0 if name.upper().startswith("VITAMIN") else 1, len(name))
        if key not in best or score < best[key][0]:
            best[key] = (score, name)
    return {key: value[1] for key, value in best.items()}
