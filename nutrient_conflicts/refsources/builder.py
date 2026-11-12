# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Build, inspect and clear the reference data derived from the source documents.

``build`` does three things, in this order:

1. read the vitamin/mineral chart out of the PDF and persist it (machine readable
   source file plus a human-readable CSV and Markdown report);
2. validate the reviewed biological conflicts matrix transcription against its
   image and persist the same pair of outputs;
3. merge every source claim into ``nutrient_interactions.csv``, refusing to lose a
   pair silently and reporting any strength/evidence change for review.

Everything is written under paths taken from ``application.properties`` and the
build is fingerprinted in ``reference-manifest.json`` so ``status`` can tell whether
the persisted results still describe the files on disk.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..config import Config, ensure_reference_workspace
from ..store import utc_now
from .chart import ChartDocument, extract_chart
from .matrix import MatrixDocument, load_matrix, refresh_fingerprint
from .merge import Claim, MergedPair, diff_against, guard, merge_claims
from .names import NutrientNameMap, preferred_display

SOURCES = ("vitamin-mineral", "biological")

CHART_MECHANISMS = {
    "SYNERGISTIC": ("The chart lists these nutrients as synergistic: one supports the absorption or "
                    "metabolic activation of the other."),
    "ANTAGONISTIC": ("The chart lists these nutrients as antagonistic: they compete for absorption "
                     "pathways, so a high intake of one reduces the availability of the other."),
    "CONTEXT_DEPENDENT": ("The chart says the direction of this interaction depends on the relative "
                          "dose of each nutrient."),
}

RELATIONSHIP_LABEL = {
    "SYNERGISTIC": "SYNERGISTIC",
    "ANTAGONISTIC": "ANTAGONISTIC",
    "CONTEXT_DEPENDENT": "VARIES WITH DOSE",
}


class ReferenceBuildError(RuntimeError):
    """Raised when a source document cannot be turned into reference data."""


@dataclass
class _ChartSource:
    document: ChartDocument
    usable: bool = True


class ReferenceBuilder:
    def __init__(self, config: Config, names: NutrientNameMap | None = None) -> None:
        self.config = config
        ensure_reference_workspace(config)
        self.names = names or NutrientNameMap.load(config.path("reference.name.map"))
        self.evidence_rank = {key: int(value) for key, value in
                              config.get_map("reference.evidence.rank").items()}
        self.strength_rank = {key: int(value) for key, value in
                              config.get_map("reference.strength.rank").items()}
        self._display = preferred_display(self.config.path("reference.name.map"))

    # -- paths -----------------------------------------------------------
    def chart_pdf(self) -> Path:
        return self.config.path("reference.source.vitamin_mineral.pdf")

    def chart_source_file(self) -> Path:
        return self.config.path("reference.source.vitamin_mineral.source_file")

    def chart_output_file(self) -> Path:
        return self.config.path("reference.source.vitamin_mineral.output_file")

    def chart_report_file(self) -> Path:
        return self.config.path("reference.source.vitamin_mineral.report_file")

    def matrix_image(self) -> Path:
        return self.config.path("reference.source.biological.image")

    def matrix_source_file(self) -> Path:
        return self.config.path("reference.source.biological.source_file")

    def matrix_output_file(self) -> Path:
        return self.config.path("reference.source.biological.output_file")

    def matrix_report_file(self) -> Path:
        return self.config.path("reference.source.biological.report_file")

    def manifest_file(self) -> Path:
        return self.config.path("reference.manifest.file")

    def crosscheck_file(self) -> Path:
        return self.config.path("reference.crosscheck.file")

    def seed_file(self) -> Path:
        return self.config.path("reference.dir") / self.config.get(
            "reference.file.interactions.seed", "nutrient_interactions.seed.csv")

    def merged_file(self) -> Path:
        return self.config.path("reference.dir") / self.config.get("reference.file.interactions")

    def paths(self) -> dict[str, str]:
        return {
            "chart_pdf": str(self.chart_pdf()),
            "chart_source": str(self.chart_source_file()),
            "chart_csv": str(self.chart_output_file()),
            "chart_report": str(self.chart_report_file()),
            "matrix_image": str(self.matrix_image()),
            "matrix_source": str(self.matrix_source_file()),
            "matrix_csv": str(self.matrix_output_file()),
            "matrix_report": str(self.matrix_report_file()),
            "merged": str(self.merged_file()),
            "manifest": str(self.manifest_file()),
        }

    # -- build -----------------------------------------------------------
    def build(self, sources: Iterable[str] = SOURCES, allow_removals: bool | None = None,
              accept_source_change: bool = False) -> dict[str, Any]:
        started = time.time()
        selected = _select_sources(sources)
        allow_removals = (self.config.get_bool("reference.kb.allow.removals", False)
                          if allow_removals is None else allow_removals)
        warnings: list[str] = []
        built: dict[str, Any] = {}

        if "vitamin-mineral" in selected:
            document = extract_chart(self.chart_pdf(), self.config, self.names)
            self._write_chart_source(document)
            self._write_chart_outputs(document)
            built["vitamin-mineral"] = {"rows": len(document.rows),
                                        "pairs": _chart_pair_count(document)}
        if "biological" in selected:
            if accept_source_change:
                refresh_fingerprint(self.matrix_source_file(), self.matrix_image())
            document_m = load_matrix(self.matrix_source_file(), self.matrix_image(), self.config,
                                     self.names, accept_source_change=accept_source_change)
            self._write_matrix_outputs(document_m)
            built["biological"] = {"rows": len(document_m.rows),
                                   "pairs": _matrix_pair_count(document_m)}

        claims = self._claims_from_sources()
        merged = merge_claims(claims, self.evidence_rank, self.strength_rank)
        existing = read_rows(self.merged_file())
        diff = diff_against(existing, merged)
        try:
            guard(diff, allow_removals)
        except Exception as exc:
            raise ReferenceBuildError(str(exc)) from exc
        self._write_merged(merged)
        crosscheck = self._write_crosscheck(merged)
        for pair, changes in diff["metadata_changed"]:
            detail = ", ".join(f"{key} {before}->{after}" for key, (before, after) in changes.items())
            warnings.append(f"{pair[0]}/{pair[1]}: {detail}")
        if diff["added"]:
            warnings.append("new pairs: " + ", ".join(f"{a}/{b}" for a, b in diff["added"]))

        manifest = self._write_manifest(merged, built, warnings)
        summary = {
            "built_at": manifest["built_at"],
            "sources": sorted(built),
            "outputs": manifest["outputs"],
            "merged": manifest["merged"],
            "diff": {
                "added": len(diff["added"]),
                "removed": len(diff["removed"]),
                "relation_changed": len(diff["relation_changed"]),
                "metadata_changed": len(diff["metadata_changed"]),
            },
            "warnings": warnings,
            "crosscheck": crosscheck,
            "elapsed_seconds": round(time.time() - started, 2),
        }
        return summary

    def _write_crosscheck(self, merged: list[MergedPair]) -> dict[str, Any]:
        """Persist the difference between the generated KB and the curated seed.

        The seed is the reviewed ``nutrient_interactions.csv`` that shipped with the
        project. Generating from the PDF and the image is authoritative, but the diff
        is written out so a human can review every added, removed or redirected pair.
        """
        seed = read_rows(self.seed_file())
        diff = diff_against(seed, merged)
        payload = {
            "generated_at": utc_now(),
            "seed_file": str(self.seed_file()),
            "seed_pairs": len(seed),
            "merged_pairs": len(merged),
            "added": [list(pair) for pair in diff["added"]],
            "removed": [list(pair) for pair in diff["removed"]],
            "relation_changed": [
                {"pair": list(pair), "before": before, "after": after}
                for pair, before, after in diff["relation_changed"]
            ],
            "metadata_changed": [
                {"pair": list(pair), "changes": {key: list(value) for key, value in changes.items()}}
                for pair, changes in diff["metadata_changed"]
            ],
        }
        path = self.crosscheck_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "file": str(path),
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "relation_changed": len(diff["relation_changed"]),
            "metadata_changed": len(diff["metadata_changed"]),
        }

    # -- chart -----------------------------------------------------------
    def _write_chart_source(self, document: ChartDocument) -> None:
        path = self.chart_source_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(f"# parsed from {self.chart_pdf().name} page {document.page_count}\n")
            handle.write(f"# dose_note {document.dose_note}\n")
            writer = csv.writer(handle)
            writer.writerow(["nutrient_key", "nutrient_text", "synergistic", "varies",
                             "antagonistic", "page"])
            for row in document.rows:
                writer.writerow([row.nutrient_key, row.nutrient_text,
                                 "|".join(row.synergistic), "|".join(row.varies),
                                 "|".join(row.antagonistic), row.page])

    def _write_chart_outputs(self, document: ChartDocument) -> None:
        rows: list[list[str]] = []
        for row in document.rows:
            for keys, relation in ((row.synergistic, "SYNERGISTIC"),
                                   (row.varies, "CONTEXT_DEPENDENT"),
                                   (row.antagonistic, "ANTAGONISTIC")):
                for other in keys:
                    rows.append([self.display(row.nutrient_key), row.nutrient_key,
                                 RELATIONSHIP_LABEL[relation], self.display(other), other])
        _write_csv(self.chart_output_file(),
                   ["nutrient", "nutrient_key", "relationship", "related_nutrient",
                    "related_nutrient_key"], rows,
                   header_comments=[
                       "Reference data 1 - Vitamin-Mineral Interactions",
                       f"Source: {self.chart_pdf().name} (parsed from the PDF text layer)",
                       f"Dose note: {document.dose_note}",
                   ])

        lines = ["# Vitamin-Mineral Interactions", "",
                 f"Source: `{self.chart_pdf().name}` (parsed from the PDF text layer).", ""]
        if document.dose_note:
            lines += [f"> {document.dose_note}", ""]
        for title, attribute in (("Synergistic", "synergistic"),
                                 ("Varies with dose", "varies"),
                                 ("Antagonistic", "antagonistic")):
            lines += [f"## {title}", "", "| Nutrient | Related nutrients |", "|---|---|"]
            for row in document.rows:
                related = getattr(row, attribute)
                if related:
                    lines.append(f"| {self.display(row.nutrient_key)} | "
                                 + ", ".join(self.display(key) for key in related) + " |")
            lines.append("")
        _write_text(self.chart_report_file(), lines)

    # -- matrix ----------------------------------------------------------
    def _write_matrix_outputs(self, document: MatrixDocument) -> None:
        rows: list[list[str]] = []
        for row in document.rows:
            for a in row.nutrients_a:
                for b in row.nutrients_b:
                    rows.append([str(row.row_number), self.display(a), a, self.display(b), b,
                                 RELATIONSHIP_LABEL[row.relation], row.mechanism])
        _write_csv(self.matrix_output_file(),
                   ["row", "nutrient_a", "nutrient_a_key", "nutrient_b", "nutrient_b_key",
                    "relationship", "mechanism"], rows,
                   header_comments=[
                       "Reference data 2 - Biological Conflicts Matrix (A vs B)",
                       f"Source image: {self.matrix_image().name} "
                       f"(sha256 {document.fingerprint.get('image.sha256', '')[:16]}...)",
                       f"Reviewed transcription: {self.matrix_source_file().name}, "
                       f"{document.declared_rows} image rows",
                   ])

        lines = ["# Biological Conflicts Matrix (A vs B)", "",
                 f"Source image: `{self.matrix_image().name}` (validated against the reviewed "
                 f"transcription).", "",
                 "| Row | Nutrient A | Nutrient B | Conflict type | Scientific mechanism |",
                 "|---|---|---|---|---|"]
        for row in document.rows:
            lines.append(
                f"| {row.row_number} | {row.cells[0]} | {row.cells[1]} | "
                f"{RELATIONSHIP_LABEL[row.relation].title()} | {row.mechanism} |"
            )
        lines.append("")
        _write_text(self.matrix_report_file(), lines)

    # -- merge -----------------------------------------------------------
    def _claims_from_sources(self) -> list[Claim]:
        claims: list[Claim] = []
        if self.chart_source_file().is_file():
            claims.extend(self._chart_claims(read_rows(self.chart_source_file())))
        if self.matrix_source_file().is_file():
            document = load_matrix(self.matrix_source_file(), self.matrix_image(), self.config,
                                   self.names)
            claims.extend(self._matrix_claims(document))
        if not claims:
            raise ReferenceBuildError(
                "no reference source is available; run reference build --source all"
            )
        return claims

    def _chart_claims(self, rows: list[dict[str, str]]) -> list[Claim]:
        label = self.config.get("reference.source.vitamin_mineral.label", "Vitamin-mineral interactions chart")
        claims: list[Claim] = []
        for row in rows:
            nutrient = row["nutrient_key"].strip().upper()
            for column, relation, strength in (
                ("synergistic", "SYNERGISTIC", "moderate"),
                ("varies", "CONTEXT_DEPENDENT", "weak"),
                ("antagonistic", "ANTAGONISTIC", "moderate"),
            ):
                for other in _split_list(row.get(column, "")):
                    if other == nutrient:
                        continue
                    claims.append(Claim(
                        nutrient_a=nutrient, nutrient_b=other, relation=relation, source=label,
                        evidence_level="chart", strength=strength,
                        mechanism=CHART_MECHANISMS[relation],
                        dose_note=row.get("dose_note", ""),
                    ))
        return claims

    def _matrix_claims(self, document: MatrixDocument) -> list[Claim]:
        label = self.config.get("reference.source.biological.label", "Biological conflicts matrix")
        claims: list[Claim] = []
        for row in document.rows:
            for a in row.nutrients_a:
                for b in row.nutrients_b:
                    if a == b:
                        continue
                    claims.append(Claim(
                        nutrient_a=a, nutrient_b=b, relation=row.relation, source=label,
                        evidence_level="clinical", strength="strong", mechanism=row.mechanism,
                    ))
        return claims

    def _write_merged(self, merged: list[MergedPair]) -> None:
        path = self.merged_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "nutrient_a", "nutrient_b", "relation", "strength", "evidence_level",
                "mechanism", "conflicting_claims", "dose_note", "source"])
            writer.writeheader()
            for pair in merged:
                writer.writerow(pair.as_row())

    # -- manifest --------------------------------------------------------
    def _write_manifest(self, merged: list[MergedPair], built: dict[str, Any],
                        warnings: list[str]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for pair in merged:
            counts[pair.relation] = counts.get(pair.relation, 0) + 1
        manifest = {
            "built_at": utc_now(),
            "generator": "nutrient_conflicts.refsources",
            "sources": {},
            "outputs": {
                "chart_csv": str(self.chart_output_file()),
                "chart_report": str(self.chart_report_file()),
                "matrix_csv": str(self.matrix_output_file()),
                "matrix_report": str(self.matrix_report_file()),
            },
            "merged": {
                "path": str(self.merged_file()),
                "pairs": len(merged),
                "relations": counts,
                "sha256": _file_hash(self.merged_file()),
            },
            "built_in_this_run": built,
            "warnings": warnings,
        }
        for name, path in (("vitamin-mineral", self.chart_source_file()),
                           ("biological", self.matrix_source_file())):
            if path.is_file():
                manifest["sources"][name] = {
                    "file": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": _file_hash(path),
                }
        if self.matrix_image().is_file() and "biological" in manifest["sources"]:
            manifest["sources"]["biological"]["image"] = str(self.matrix_image())
        path = self.manifest_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return manifest

    # -- status / clear --------------------------------------------------
    def status(self) -> dict[str, Any]:
        manifest = _read_json(self.manifest_file())
        reasons: list[str] = []
        sources: dict[str, Any] = {}
        for name, path in (("vitamin-mineral", self.chart_source_file()),
                           ("biological", self.matrix_source_file())):
            exists = path.is_file()
            recorded = (manifest.get("sources") or {}).get(name, {})
            current = _file_hash(path) if exists else ""
            matches = bool(recorded) and recorded.get("sha256") == current
            sources[name] = {"file": str(path), "exists": exists, "rows_recorded": bool(recorded),
                             "matches_manifest": matches}
            if not exists:
                reasons.append(f"{name} source file is missing")
            elif not manifest:
                reasons.append("no reference manifest: run reference build")
            elif not matches:
                reasons.append(f"{name} source file changed since the last build")
        merged_exists = self.merged_file().is_file()
        merged_hash = _file_hash(self.merged_file()) if merged_exists else ""
        merged = {
            "path": str(self.merged_file()),
            "exists": merged_exists,
            "pairs": len(read_rows(self.merged_file())) if merged_exists else 0,
            "matches_manifest": bool(manifest) and manifest.get("merged", {}).get("sha256") == merged_hash,
        }
        if not merged_exists:
            reasons.append("merged knowledge base is missing")
        elif manifest and not merged["matches_manifest"]:
            reasons.append("merged knowledge base changed since the last build")
        outputs = {name: str(path) for name, path in (
            ("chart_csv", self.chart_output_file()), ("chart_report", self.chart_report_file()),
            ("matrix_csv", self.matrix_output_file()), ("matrix_report", self.matrix_report_file()),
            ("manifest", self.manifest_file()))}
        return {
            "sources": sources,
            "outputs": {name: {"path": path, "exists": Path(path).is_file()}
                        for name, path in outputs.items()},
            "merged": merged,
            "manifest": manifest,
            "stale": bool(reasons),
            "reasons": reasons,
            "paths": self.paths(),
        }

    def clear(self, keep_kb: bool = False) -> dict[str, Any]:
        removed: list[str] = []
        targets = [self.chart_output_file(), self.chart_report_file(),
                   self.matrix_output_file(), self.matrix_report_file(),
                   self.manifest_file(), self.chart_source_file(), self.crosscheck_file()]
        if not keep_kb:
            targets.append(self.merged_file())
        for path in targets:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        return {"removed": removed, "kept_merged_kb": keep_kb}

    # -- helpers ---------------------------------------------------------
    def display(self, nutrient_key: str) -> str:
        return self._display.get(nutrient_key, nutrient_key)


# --------------------------------------------------------------------------- helpers
def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV that may start with ``#`` comment lines (used by every source)."""
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(lines))


def _split_list(value: str) -> list[str]:
    return [item.strip().upper() for item in (value or "").split("|") if item.strip()]


def _select_sources(sources: Iterable[str]) -> set[str]:
    selected = {item.strip().lower() for item in sources if item and item.strip()}
    if "all" in selected or not selected:
        return set(SOURCES)
    unknown = selected - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown reference source(s): {', '.join(sorted(unknown))}")
    return selected


def _chart_pair_count(document: ChartDocument) -> int:
    pairs = set()
    for row in document.rows:
        for keys in (row.synergistic, row.varies, row.antagonistic):
            for other in keys:
                if other != row.nutrient_key:
                    pairs.add(tuple(sorted((row.nutrient_key, other))))
    return len(pairs)


def _matrix_pair_count(document: MatrixDocument) -> int:
    return len({(a, b) for row in document.rows for a in row.nutrients_a for b in row.nutrients_b
                if a != b})


def _write_csv(path: Path, header: list[str], rows: list[list[str]],
               header_comments: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for line in header_comments or []:
            handle.write(f"# {line}\n")
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_text(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
