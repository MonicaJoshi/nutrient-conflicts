# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Read the vitamin/mineral interactions chart out of its PDF.

The chart is a four column table ("Nutrient", "Synergistic", "Relationship Varies
Based on Nutrient Levels", "Antagonistic Relationship") whose cells wrap. Text
extraction alone loses the column alignment, so this module reads the words with
their page coordinates and rebuilds the table:

* the left column supplies the row anchors (``Vitamin B`` + ``1`` is joined to
  ``Vitamin B1`` because the digit is typeset as a separate token);
* every other word is attached to the nearest anchor by its vertical position;
* superscript reference numbers are ignored, and the column boundaries are
  configurable because they are a property of the document layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from .names import NutrientNameMap, UnknownNutrientError

_NUMERIC = re.compile(r"^[\d,.\-\s]+$")
_B_GAP = re.compile(r"\bB (\d+)")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class ChartError(RuntimeError):
    """Raised when the chart cannot be read as expected."""


@dataclass(frozen=True)
class ChartRow:
    """One nutrient row of the chart, with nutrients already resolved to keys."""

    nutrient_key: str
    nutrient_text: str
    synergistic: tuple[str, ...] = ()
    varies: tuple[str, ...] = ()
    antagonistic: tuple[str, ...] = ()
    page: int = 0


@dataclass(frozen=True)
class ChartDocument:
    rows: tuple[ChartRow, ...]
    dose_note: str
    page_count: int


def extract_chart(pdf_path: Path, config: Config, names: NutrientNameMap) -> ChartDocument:
    """Parse the interactions chart and return its rows plus the chart's dose note."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"reference chart not found: {pdf_path}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
        raise ChartError("pypdf is required to read the reference chart") from exc

    reader = PdfReader(str(pdf_path))
    page_index = config.get_int("reference.pdf.page", 0)
    if page_index >= len(reader.pages):
        raise ChartError(f"{pdf_path.name} has no page {page_index}")
    page = reader.pages[page_index]

    boundaries = [float(item) for item in config.get_list("reference.pdf.column.boundaries")]
    if len(boundaries) != 3:
        raise ChartError("reference.pdf.column.boundaries must list three fractions")
    width = float(page.mediabox.width)
    cuts = [fraction * width for fraction in boundaries]
    tolerance = config.get_float("reference.pdf.row.tolerance.points", 2.0)
    max_offset = config.get_float("reference.pdf.row.max.offset.points", 14.0)
    min_rows = config.get_int("reference.pdf.min.rows", 20)
    marker = config.get("reference.pdf.marker.text", "high doses")

    tokens = _page_tokens(page)
    if not tokens:
        raise ChartError(f"no text layer in {pdf_path.name}; the PDF is scanned and needs OCR")

    anchors = _find_anchors(tokens, cuts[0], names, tolerance)
    if len(anchors) < min_rows:
        raise ChartError(
            f"only {len(anchors)} nutrient rows were recognised (expected at least {min_rows}); "
            "the PDF layout changed - review reference.pdf.column.boundaries"
        )

    cells: dict[int, dict[int, list[tuple[float, float, str]]]] = {
        index: {1: [], 2: [], 3: []} for index in range(len(anchors))
    }
    for x, y, text in tokens:
        column = _column(x, cuts)
        if column == 0:
            continue  # nutrient column: names, wrapped qualifiers, reference numbers
        index = _nearest_anchor(anchors, y, max_offset)
        if index is None:
            continue
        cells[index][column].append((y, x, text))

    rows: list[ChartRow] = []
    for index, (anchor_y, nutrient_key, nutrient_text) in enumerate(anchors):
        row = ChartRow(
            nutrient_key=nutrient_key,
            nutrient_text=nutrient_text,
            synergistic=tuple(_cell_keys(cells[index][1], names, nutrient_text, "synergistic")),
            varies=tuple(_cell_keys(cells[index][2], names, nutrient_text, "varies")),
            antagonistic=tuple(_cell_keys(cells[index][3], names, nutrient_text, "antagonistic")),
            page=page_index,
        )
        rows.append(row)

    populated = [row for row in rows
                 if row.synergistic or row.varies or row.antagonistic]
    min_cells = config.get_int("reference.pdf.min.populated.rows", 15)
    if len(populated) < min_cells:
        raise ChartError(
            f"only {len(populated)} of {len(rows)} rows produced a relationship cell "
            f"(expected at least {min_cells}); review reference.pdf.column.boundaries"
        )

    return ChartDocument(rows=tuple(rows), dose_note=_dose_note(reader.pages[page_index], marker),
                         page_count=len(reader.pages))


def _page_tokens(page) -> list[tuple[float, float, str]]:
    tokens: list[tuple[float, float, str]] = []

    def visit(text: str, cm, tm, font, size) -> None:
        cleaned = _WHITESPACE.sub(" ", text).strip()
        if cleaned:
            # coordinates arrive with sub-point noise; rounding keeps tokens that sit
            # on the same visual line in their left-to-right order
            tokens.append((round(float(tm[4]), 1), round(float(tm[5]), 1), cleaned))

    page.extract_text(visitor_text=visit)
    return tokens


def _column(x: float, cuts: list[float]) -> int:
    for index, cut in enumerate(cuts):
        if x < cut:
            return index
    return len(cuts)


def _find_anchors(tokens, nutrient_cut: float, names: NutrientNameMap,
                  tolerance: float) -> list[tuple[float, str, str]]:
    """Locate the row labels in the nutrient column, joining split names.

    The chart typesets ``Vitamin B1`` as ``Vitamin B`` plus a separate ``1`` token,
    so a label is accepted only when the joined text resolves through the name map.
    Superscript reference numbers are numeric-only and never resolve, so they are
    skipped naturally.
    """
    candidates = [(x, y, text) for x, y, text in tokens if x < nutrient_cut]
    anchors: list[tuple[float, str, str]] = []
    seen: set[str] = set()
    for x, y, text in candidates:
        if _NUMERIC.match(text) or text.startswith("("):
            continue
        label = text
        key = _try_key(label, names)
        if key is None:
            suffix = next((other for ox, oy, other in candidates
                           if ox > x and abs(oy - y) <= tolerance and _NUMERIC.match(other)
                           and len(other) <= 3), None)
            if suffix is None:
                continue
            label = f"{text} {suffix}"
            key = _try_key(label, names)
        if key is None or key in seen:
            continue
        seen.add(key)
        anchors.append((y, key, _B_GAP.sub(r"B\1", label)))
    anchors.sort(key=lambda item: -item[0])
    return anchors


def _try_key(label: str, names: NutrientNameMap) -> str | None:
    try:
        return names.resolve(label)
    except UnknownNutrientError:
        return None


def _nearest_anchor(anchors: list[tuple[float, str, str]], y: float,
                    limit: float) -> int | None:
    best_index, best_distance = None, limit
    for index, (anchor_y, _, _) in enumerate(anchors):
        distance = abs(anchor_y - y)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def _cell_keys(cell: list[tuple[float, float, str]], names: NutrientNameMap,
               nutrient_text: str, column: str) -> list[str]:
    if not cell:
        return []
    text = " ".join(item[2] for item in sorted(cell, key=lambda item: (-item[0], item[1])))
    text = _B_GAP.sub(r"B\1", _WHITESPACE.sub(" ", text)).strip()
    try:
        return names.resolve_cell(text)
    except UnknownNutrientError as exc:
        raise ChartError(f"row {nutrient_text!r}, {column} cell {text!r}: {exc}") from exc


def _dose_note(page, marker: str) -> str:
    text = page.extract_text() or ""
    for sentence in _SENTENCE.split(_WHITESPACE.sub(" ", text)):
        if marker.lower() in sentence.lower():
            return sentence.strip()
    return ""
