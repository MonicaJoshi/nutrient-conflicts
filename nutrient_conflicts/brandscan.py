# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Brand level nutrient conflict scan (result file 3).

The question this module answers is broader than a single product:

> For each brand, does the brand contain a nutrient combination that the reference
> data calls synergistic or antagonistic?

Two facts are recorded for every (brand, pair):

* **presence** - both nutrients appear somewhere in the brand's product line;
* **co-occurrence** - how many products of that brand actually contain both, with
  sample ``fdc_id`` values and the highest measured percent DV for each nutrient.

The scan is persisted to ``data/results/Brand-Nutrient-Conflicts.csv`` (plus a
Markdown summary) and cached, so the brand attribute returns immediately after the
first build. ``clear`` removes the results and the cache entries.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .reference import Interaction, ReferenceData
from .refsources.builder import read_rows
from .refsources.names import preferred_display
from .store import Store, utc_now

logger = logging.getLogger(__name__)

RELATION_ORDER = {"ANTAGONISTIC": 0, "SYNERGISTIC": 1, "CONTEXT_DEPENDENT": 2}

COLUMNS = [
    "brand_owner", "nutrient_a", "nutrient_b", "relation", "strength", "evidence_level",
    "co_occurring_products", "nutrient_a_products", "nutrient_b_products", "sample_fdc_ids",
    "max_dv_percent_a", "max_dv_percent_b", "mechanism", "source",
]


class BrandScanError(RuntimeError):
    """Raised when the scan cannot run (no store, no reference data)."""


@dataclass(frozen=True)
class BrandPairRow:
    brand_owner: str
    nutrient_a: str
    nutrient_b: str
    relation: str
    strength: str
    evidence_level: str
    co_occurring_products: int
    nutrient_a_products: int
    nutrient_b_products: int
    sample_fdc_ids: tuple[int, ...]
    max_dv_percent_a: float | None
    max_dv_percent_b: float | None
    mechanism: str
    source: str

    def as_row(self) -> list[str]:
        return [
            self.brand_owner, self.nutrient_a, self.nutrient_b, self.relation, self.strength,
            self.evidence_level, str(self.co_occurring_products), str(self.nutrient_a_products),
            str(self.nutrient_b_products), "|".join(str(item) for item in self.sample_fdc_ids),
            "" if self.max_dv_percent_a is None else f"{self.max_dv_percent_a:.2f}",
            "" if self.max_dv_percent_b is None else f"{self.max_dv_percent_b:.2f}",
            self.mechanism, self.source,
        ]


class BrandConflictScanner:
    def __init__(self, config: Config, store: Store | None = None,
                 reference: ReferenceData | None = None) -> None:
        self.config = config
        self.store = store or Store(config)
        self._reference = reference
        self.directory = config.path("conflicts.output.dir")
        self.csv_path = self.directory / config.get("conflicts.csv.file")
        self.report_path = self.directory / config.get("conflicts.report.file")
        self.manifest_path = self.directory / config.get("conflicts.manifest.file")
        self.cache_namespace = config.get("conflicts.cache.namespace", "brand_conflicts")
        self.sample_size = config.get_int("conflicts.sample.products", 5)
        self.top_brands = config.get_int("conflicts.report.top.brands", 20)
        self.include_context = config.get_bool("conflicts.include.context.dependent", True)
        # USDA rows above this percent DV are data-entry artefacts (values in the
        # billions); they are ignored when summarising a brand's quantities.
        self.max_dv_percent = config.get_float("conflicts.quantity.max.dv.percent", 1000.0)
        self._display = preferred_display(config.path("reference.name.map"))
        self._brand_index: dict[str, list[BrandPairRow]] | None = None

    # -- collaborators ---------------------------------------------------
    @property
    def reference(self) -> ReferenceData:
        if self._reference is None:
            self._reference = ReferenceData.load(self.config)
        return self._reference

    def interactions(self) -> list[Interaction]:
        items = self.reference.interactions
        if not self.include_context:
            items = [item for item in items if item.relation != "CONTEXT_DEPENDENT"]
        return items

    def display(self, nutrient_key: str) -> str:
        return self._display.get(nutrient_key, nutrient_key)

    def paths(self) -> dict[str, str]:
        return {"csv": str(self.csv_path), "report": str(self.report_path),
                "manifest": str(self.manifest_path)}

    # -- build -----------------------------------------------------------
    def build(self, force: bool = False) -> dict[str, Any]:
        started = time.time()
        if not self.store.exists():
            raise BrandScanError(f"store not built at {self.store.path}; run ingest first")
        status = self.status()
        if status["exists"] and not status["stale"] and not force:
            return {**status, "skipped": "results already current",
                    "elapsed_seconds": round(time.time() - started, 2)}

        interactions = self.interactions()
        if not interactions:
            raise BrandScanError("the interaction knowledge base is empty")
        with self.store.session() as connection:
            presence, cooccurrence, samples, maxima, brand_products = self._scan(
                connection, interactions)

        rows: list[BrandPairRow] = []
        for interaction in interactions:
            key = (interaction.nutrient_a, interaction.nutrient_b)
            for brand, (products_a, products_b) in presence.get(key, {}).items():
                co_products = cooccurrence.get((brand, *key), 0)
                rows.append(BrandPairRow(
                    brand_owner=brand,
                    nutrient_a=interaction.nutrient_a,
                    nutrient_b=interaction.nutrient_b,
                    relation=interaction.relation,
                    strength=interaction.strength,
                    evidence_level=interaction.evidence_level,
                    co_occurring_products=co_products,
                    nutrient_a_products=products_a,
                    nutrient_b_products=products_b,
                    sample_fdc_ids=tuple(sorted(samples.get((brand, *key), []))[:self.sample_size]),
                    max_dv_percent_a=maxima.get((brand, interaction.nutrient_a)),
                    max_dv_percent_b=maxima.get((brand, interaction.nutrient_b)),
                    mechanism=interaction.mechanism,
                    source=interaction.source,
                ))
        rows.sort(key=lambda item: (item.brand_owner, RELATION_ORDER.get(item.relation, 9),
                                    item.nutrient_a, item.nutrient_b))
        self._write_csv(rows)
        manifest = self._write_manifest(rows, brand_products, interactions)
        self._write_report(rows, manifest)
        self._brand_index = None
        summary = {
            "built_at": manifest["built_at"],
            "brands": manifest["brands"],
            "brands_with_conflicts": manifest["brands_with_conflicts"],
            "pairs": manifest["pairs"],
            "rows": manifest["rows"],
            "relations": manifest["relations"],
            "outputs": self.paths(),
            "elapsed_seconds": round(time.time() - started, 2),
        }
        return summary

    def _scan(self, connection: sqlite3.Connection,
              interactions: list[Interaction]) -> tuple[
                  dict[tuple[str, str], dict[str, tuple[int, int]]],
                  dict[tuple[str, str, str], int],
                  dict[tuple[str, str, str], set[int]],
                  dict[tuple[str, str], float],
                  dict[str, int]]:
        pairs = [(item.nutrient_a, item.nutrient_b) for item in interactions]
        nutrients = sorted({key for pair in pairs for key in pair})
        connection.execute("CREATE TEMP TABLE kb_pair (a TEXT, b TEXT, PRIMARY KEY (a, b))")
        connection.executemany("INSERT INTO kb_pair (a, b) VALUES (?, ?)", pairs)
        connection.execute("CREATE TEMP TABLE kb_nutrient (nutrient_key TEXT PRIMARY KEY)")
        connection.executemany("INSERT INTO kb_nutrient (nutrient_key) VALUES (?)",
                              [(key,) for key in nutrients])
        connection.execute("""
            CREATE TEMP TABLE brand_nutrient AS
            SELECT f.brand_owner AS brand_owner, d.nutrient_key AS nutrient_key,
                   COUNT(DISTINCT d.fdc_id) AS products
            FROM derived_nutrient d
            JOIN food f ON f.fdc_id = d.fdc_id
            WHERE f.brand_owner IS NOT NULL AND TRIM(f.brand_owner) <> ''
              AND d.nutrient_key IN (SELECT nutrient_key FROM kb_nutrient)
            GROUP BY 1, 2
        """)
        logger.info("brand scan: brand/nutrient aggregation ready")

        presence: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
        for row in connection.execute("""
                SELECT a.brand_owner, a.nutrient_key, b.nutrient_key, a.products, b.products
                FROM brand_nutrient a
                JOIN brand_nutrient b ON b.brand_owner = a.brand_owner
                JOIN kb_pair k ON k.a = a.nutrient_key AND k.b = b.nutrient_key
        """):
            presence.setdefault((row[1], row[2]), {})[row[0]] = (int(row[3]), int(row[4]))
        logger.info("brand scan: pair presence computed for %d brand/pair combinations",
                    sum(len(items) for items in presence.values()))

        cooccurrence: dict[tuple[str, str, str], int] = {}
        samples: dict[tuple[str, str, str], set[int]] = {}
        for brand, a, b, fdc_id in connection.execute("""
                SELECT f.brand_owner, d1.nutrient_key, d2.nutrient_key, d1.fdc_id
                FROM derived_nutrient d1
                JOIN derived_nutrient d2 ON d2.fdc_id = d1.fdc_id
                JOIN kb_pair k ON k.a = d1.nutrient_key AND k.b = d2.nutrient_key
                JOIN food f ON f.fdc_id = d1.fdc_id
                WHERE f.brand_owner IS NOT NULL AND TRIM(f.brand_owner) <> ''
        """):
            key = (brand, a, b)
            cooccurrence[key] = cooccurrence.get(key, 0) + 1
            bucket = samples.setdefault(key, set())
            if len(bucket) < self.sample_size or fdc_id < max(bucket):
                bucket.add(int(fdc_id))
                if len(bucket) > self.sample_size:
                    bucket.discard(max(bucket))
        logger.info("brand scan: %d brand/pair combinations co-occur inside a product",
                    len(cooccurrence))

        maxima: dict[tuple[str, str], float] = {}
        for brand, nutrient, dv_percent in connection.execute("""
                SELECT f.brand_owner, m.nutrient_key,
                       MAX(CASE WHEN m.dv_percent BETWEEN 0 AND ? THEN m.dv_percent END)
                FROM measured_nutrient m
                JOIN food f ON f.fdc_id = m.fdc_id
                WHERE m.dv_percent IS NOT NULL
                  AND m.nutrient_key IN (SELECT nutrient_key FROM kb_nutrient)
                GROUP BY 1, 2
        """, (self.max_dv_percent,)):
            if dv_percent is not None:
                maxima[(brand, nutrient)] = float(dv_percent)

        brand_products = {row[0]: int(row[1]) for row in connection.execute(
            "SELECT brand_owner, COUNT(*) FROM food WHERE brand_owner IS NOT NULL "
            "AND TRIM(brand_owner) <> '' GROUP BY 1")}
        return presence, cooccurrence, samples, maxima, brand_products

    # -- outputs ---------------------------------------------------------
    def _write_csv(self, rows: list[BrandPairRow]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("# Result file 3 - brand level nutrient conflicts\n")
            handle.write("# One row per brand and nutrient pair. co_occurring_products counts the\n")
            handle.write("# products of that brand that contain BOTH nutrients.\n")
            handle.write(f"# built_at {utc_now()}\n")
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for row in rows:
                writer.writerow(row.as_row())

    def _write_manifest(self, rows: list[BrandPairRow], brand_products: dict[str, int],
                        interactions: list[Interaction]) -> dict[str, Any]:
        relations: dict[str, int] = {}
        brands_with_conflicts = set()
        for row in rows:
            relations[row.relation] = relations.get(row.relation, 0) + 1
            brands_with_conflicts.add(row.brand_owner)
        manifest = {
            "built_at": utc_now(),
            "rows": len(rows),
            "brands": len(brand_products),
            "brands_with_conflicts": len(brands_with_conflicts),
            "pairs": len(interactions),
            "relations": relations,
            "kb_sha256": _file_hash(self.config.path("reference.dir")
                                    / self.config.get("reference.file.interactions")),
            "store_signature": _file_signature(self.store.path),
            "outputs": self.paths(),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
        return manifest

    def _write_report(self, rows: list[BrandPairRow], manifest: dict[str, Any]) -> None:
        antagonistic = [row for row in rows if row.relation == "ANTAGONISTIC"]
        per_brand: dict[str, int] = {}
        for row in antagonistic:
            per_brand[row.brand_owner] = per_brand.get(row.brand_owner, 0) + 1
        lines = [
            "# Brand Nutrient Conflicts",
            "",
            f"Built {manifest['built_at']} from {manifest['pairs']} reference pairs across "
            f"{manifest['brands']:,} brands.",
            "",
            f"- brands with at least one conflicting combination: "
            f"**{manifest['brands_with_conflicts']:,}**",
            f"- antagonistic combinations: **{manifest['relations'].get('ANTAGONISTIC', 0):,}**",
            f"- synergistic combinations: **{manifest['relations'].get('SYNERGISTIC', 0):,}**",
            f"- context dependent combinations: "
            f"**{manifest['relations'].get('CONTEXT_DEPENDENT', 0):,}**",
            "",
            "A combination is listed when both nutrients appear in the brand's product line; "
            "`co_occurring_products` shows how many products actually contain both.",
            "",
            "## Brands with the most antagonistic combinations",
            "",
            "| Brand | Antagonistic combinations |",
            "|---|---|",
        ]
        for brand, count in sorted(per_brand.items(), key=lambda item: (-item[1], item[0]))[:self.top_brands]:
            lines.append(f"| {brand} | {count} |")
        lines += ["", "## Combinations inside a single product",
                  "", "| Brand | Pair | Products with both |", "|---|---|---|"]
        co_rows = sorted([row for row in rows if row.co_occurring_products > 0],
                         key=lambda row: (-row.co_occurring_products, row.brand_owner))[:self.top_brands]
        for row in co_rows:
            lines.append(f"| {row.brand_owner} | {row.nutrient_a} + {row.nutrient_b} | "
                         f"{row.co_occurring_products} |")
        lines += ["", "> Informational only. Conflicting nutrient combinations are not medical "
                      "advice; discuss any result with a registered dietitian."]
        self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- status / clear --------------------------------------------------
    def status(self) -> dict[str, Any]:
        manifest = _read_json(self.manifest_path)
        rows = read_rows(self.csv_path)
        reasons: list[str] = []
        exists = self.csv_path.is_file()
        if not exists:
            reasons.append("result file does not exist")
        elif not manifest:
            reasons.append("no manifest for the result file: run conflicts build")
        else:
            current_kb = _file_hash(self.config.path("reference.dir")
                                    / self.config.get("reference.file.interactions"))
            if manifest.get("kb_sha256") != current_kb:
                reasons.append("reference knowledge base changed since the last scan")
            if self.store.exists() and manifest.get("store_signature") != _file_signature(self.store.path):
                reasons.append("store changed since the last scan")
        return {
            "exists": exists,
            "stale": bool(reasons),
            "reasons": reasons,
            "rows": len(rows),
            "brands": len({row.get("brand_owner", "") for row in rows}) if rows else 0,
            "manifest": manifest,
            "paths": self.paths(),
        }

    def clear(self) -> dict[str, Any]:
        removed: list[str] = []
        for path in (self.csv_path, self.report_path, self.manifest_path):
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        self._brand_index = None
        return {"removed": removed}

    # -- query -----------------------------------------------------------
    def brand_rows(self, brand_owner: str) -> list[BrandPairRow]:
        self._ensure_index()
        return self._brand_index.get(brand_owner, [])

    def brand_names(self) -> list[str]:
        self._ensure_index()
        return sorted(self._brand_index)

    def _ensure_index(self) -> None:
        if self._brand_index is not None:
            return
        index: dict[str, list[BrandPairRow]] = {}
        for row in read_rows(self.csv_path):
            index.setdefault(row["brand_owner"], []).append(BrandPairRow(
                brand_owner=row["brand_owner"],
                nutrient_a=row["nutrient_a"],
                nutrient_b=row["nutrient_b"],
                relation=row["relation"],
                strength=row["strength"],
                evidence_level=row["evidence_level"],
                co_occurring_products=int(row["co_occurring_products"] or 0),
                nutrient_a_products=int(row["nutrient_a_products"] or 0),
                nutrient_b_products=int(row["nutrient_b_products"] or 0),
                sample_fdc_ids=tuple(int(item) for item in
                                     (row.get("sample_fdc_ids") or "").split("|") if item),
                max_dv_percent_a=_optional_float(row.get("max_dv_percent_a")),
                max_dv_percent_b=_optional_float(row.get("max_dv_percent_b")),
                mechanism=row.get("mechanism", ""),
                source=row.get("source", ""),
            ))
        self._brand_index = index

def _optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _file_signature(path: Path) -> str:
    """Cheap identity for a large file (the store is ~2 GB, so never hash it)."""
    if not path.is_file():
        return ""
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


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
