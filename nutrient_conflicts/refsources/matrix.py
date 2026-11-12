# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Load and validate the reviewed transcription of the biological conflicts matrix.

The source image (``BiologicalConflicts-Matrix-AvsB.png``) is a raster table with no
text layer. Rather than run OCR at build time, the five rows are transcribed once
into ``referenceData/sources/biological_conflicts_matrix.csv`` and reviewed by a
human. The program then validates that transcription against the image itself:

* the image file hash, size and pixel dimensions recorded in the transcription must
  still match the image on disk;
* the declared row count must match the rows that are actually present;
* every nutrient name must resolve through ``nutrient_name_map.csv``.

If the image is replaced deliberately, ``--accept-source-change`` rewrites the
fingerprint so the new image becomes the reference for the next build.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from .names import NutrientNameMap, UnknownNutrientError

_META = re.compile(r"^#\s*([A-Za-z0-9_.]+)\s*=\s*(.+)$")
_RELATIONS = (
    ("ANTAGONISTIC", "ANTAGONISTIC"),
    ("SYNERGISTIC", "SYNERGISTIC"),
    ("VARIES", "CONTEXT_DEPENDENT"),
)


class SourceChangedError(RuntimeError):
    """Raised when the transcription no longer matches the source image."""


@dataclass(frozen=True)
class MatrixRow:
    row_number: int
    nutrients_a: tuple[str, ...]
    nutrients_b: tuple[str, ...]
    cells: tuple[str, str]
    relation: str
    mechanism: str


@dataclass(frozen=True)
class MatrixDocument:
    rows: tuple[MatrixRow, ...]
    fingerprint: dict[str, str]
    declared_rows: int


def load_matrix(transcription_path: Path, image_path: Path, config: Config,
                names: NutrientNameMap, accept_source_change: bool = False) -> MatrixDocument:
    """Parse the transcription and check it against the image it describes."""
    if not transcription_path.is_file():
        raise FileNotFoundError(f"matrix transcription not found: {transcription_path}")
    text = transcription_path.read_text(encoding="utf-8")
    meta = _read_meta(text)
    data_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    if not reader.fieldnames:
        raise ValueError(f"{transcription_path.name} has no CSV header")

    fingerprint = image_fingerprint(image_path)
    _verify(fingerprint, meta, transcription_path, accept_source_change)

    rows: list[MatrixRow] = []
    for index, raw in enumerate(reader, start=1):
        number = int((raw.get("row") or index))
        cell_a = (raw.get("nutrient_a_cell") or "").strip()
        cell_b = (raw.get("nutrient_b_cell") or "").strip()
        conflict = (raw.get("conflict_type") or "").strip()
        mechanism = (raw.get("mechanism") or "").strip()
        relation = _relation(conflict, number)
        try:
            keys_a = tuple(names.resolve_cell(cell_a))
            keys_b = tuple(names.resolve_cell(cell_b))
        except UnknownNutrientError as exc:
            raise ValueError(f"transcription row {number}: {exc}") from exc
        if not keys_a or not keys_b:
            raise ValueError(f"transcription row {number}: both nutrient cells must name a nutrient")
        rows.append(MatrixRow(row_number=number, nutrients_a=keys_a, nutrients_b=keys_b,
                              cells=(cell_a, cell_b), relation=relation, mechanism=mechanism))

    declared = int(meta.get("image.rows", len(rows)))
    if declared != len(rows):
        raise ValueError(
            f"transcription declares {declared} image rows but contains {len(rows)} data rows"
        )
    if not rows:
        raise ValueError(f"transcription is empty: {transcription_path}")
    return MatrixDocument(rows=tuple(rows), fingerprint=fingerprint, declared_rows=declared)


def image_fingerprint(image_path: Path) -> dict[str, str]:
    if not image_path.is_file():
        raise FileNotFoundError(f"matrix image not found: {image_path}")
    data = image_path.read_bytes()
    fingerprint = {
        "image.file": image_path.name,
        "image.bytes": str(len(data)),
        "image.sha256": hashlib.sha256(data).hexdigest(),
    }
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(data)) as image:
            fingerprint["image.width"], fingerprint["image.height"] = (str(value) for value in image.size)
    except ImportError:  # pragma: no cover - Pillow is optional for this check
        pass
    return fingerprint


def refresh_fingerprint(transcription_path: Path, image_path: Path) -> dict[str, str]:
    """Rewrite the fingerprint header after a reviewed image change."""
    fingerprint = image_fingerprint(image_path)
    lines = transcription_path.read_text(encoding="utf-8").splitlines()
    keys = set(fingerprint)
    kept = [line for line in lines if (_META.match(line) is None or
                                       _META.match(line).group(1) not in keys)]
    insert_at = 0
    for index, line in enumerate(kept):
        if line.lstrip().startswith("#"):
            insert_at = index + 1
    block = [f"# {key}={value}" for key, value in fingerprint.items()]
    updated = kept[:insert_at] + block + kept[insert_at:]
    transcription_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return fingerprint


def _read_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        match = _META.match(line.strip())
        if match:
            meta[match.group(1)] = match.group(2).strip()
    return meta


def _verify(fingerprint: dict[str, str], meta: dict[str, str], transcription_path: Path,
            accept_source_change: bool) -> None:
    declared = {key: meta[key] for key in meta if key.startswith("image.")}
    mismatched = {key: (declared.get(key), value) for key, value in fingerprint.items()
                  if declared.get(key) and declared[key] != value}
    if not mismatched:
        return
    detail = ", ".join(f"{key}: recorded {old!r} -> image {new!r}"
                       for key, (old, new) in sorted(mismatched.items()))
    if accept_source_change:
        return
    raise SourceChangedError(
        f"the transcription {transcription_path.name} does not match the image ({detail}). "
        "Review the image, update the transcription, then rerun with --accept-source-change"
    )


def _relation(conflict: str, row_number: int) -> str:
    upper = conflict.upper()
    for marker, relation in _RELATIONS:
        if marker in upper:
            return relation
    raise ValueError(
        f"transcription row {row_number}: cannot read conflict type {conflict!r} "
        "(expected 'Antagonistic', 'Synergistic' or 'Varies')"
    )
