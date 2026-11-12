# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""First-run ingest: USDA CSVs -> SQLite store -> parquet and csv.gz snapshots.

Passes
------
1. ``food.csv``        -> ``food_meta``  (description, data_type, category per fdc_id)
2. ``branded_food.csv``-> ``food_stage`` + ``derived_nutrient`` (ingredient parsing + crosswalk)
3. join                -> ``food`` (the persisted row: fdc_id, brand_owner, derived nutrients, ...)
4. ``food_nutrient.csv``-> ``measured_nutrient`` (quantities per 100 g, optional)
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .datasource import DatasetSource
from .ingredients import IngredientParser
from .reference import ReferenceData
from .store import FINAL_SQL, SCHEMA_SQL, Store, utc_now

logger = logging.getLogger(__name__)

MEASURED_STAGE_SQL = """
CREATE TABLE measured_stage (
    fdc_id INTEGER NOT NULL,
    nutrient_key TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    dv_percent REAL,
    alias_rank INTEGER
);
"""

MEASURED_FINALISE_SQL = """
INSERT INTO measured_nutrient (fdc_id, nutrient_key, amount, unit, dv_percent)
SELECT fdc_id, nutrient_key, amount, unit, dv_percent FROM (
    SELECT fdc_id, nutrient_key, amount, unit, dv_percent,
           ROW_NUMBER() OVER (PARTITION BY fdc_id, nutrient_key ORDER BY alias_rank) AS row_number
    FROM measured_stage
)
WHERE row_number = 1;

DROP TABLE measured_stage;
"""


@dataclass
class IngestSummary:
    built_at: str
    elapsed_seconds: float
    foods: int
    derived_nutrients: int
    measured_nutrients: int
    excluded_without_ingredients: int
    dataset: dict
    exports: dict

    def as_dict(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "foods": self.foods,
            "derived_nutrients": self.derived_nutrients,
            "measured_nutrients": self.measured_nutrients,
            "excluded_without_ingredients": self.excluded_without_ingredients,
            "dataset": self.dataset,
            "exports": self.exports,
        }


class Ingestor:
    """Builds the persisted store from the USDA release."""

    def __init__(self, config: Config, store: Store, source: DatasetSource,
                 reference: ReferenceData, parser: IngredientParser) -> None:
        self.config = config
        self.store = store
        self.source = source
        self.reference = reference
        self.parser = parser
        self.batch_size = config.get_int("ingest.batch.size", 20000)
        self.progress_every = config.get_int("dataset.ingest.progress_every", 250000)
        self.include_measured = config.get_bool("dataset.ingest.include_measured", True)

    # -- public API ------------------------------------------------------
    def signatures(self) -> dict[str, str]:
        reference_files = store_reference_files(self.config)
        parser_config = {key: value for key, value in self.config.values.items() if key.startswith("parse.")}
        return self.store.signature_inputs(self.source.describe(), reference_files, parser_config)

    def build(self, force: bool = False) -> IngestSummary:
        started = time.perf_counter()
        status = self.store.status(self.signatures())
        if status["exists"] and not status["stale"] and not force:
            logger.info("store is up to date at %s", self.store.path)
            return IngestSummary(
                built_at=status["manifest"].get("built_at", ""),
                elapsed_seconds=0.0,
                foods=status["counts"].get("foods", 0),
                derived_nutrients=status["counts"].get("derived_nutrients", 0),
                measured_nutrients=status["counts"].get("measured_nutrients", 0),
                excluded_without_ingredients=int(status["manifest"].get("excluded_without_ingredients", 0)),
                dataset=self.source.describe(),
                exports={"skipped": "store already current"},
            )
        if force or status["exists"]:
            logger.info("rebuilding store (%s)", "; ".join(status["reasons"]) or "forced")
        self.store.directory.mkdir(parents=True, exist_ok=True)
        if self.store.path.exists():
            self.store.path.unlink()
        connection = self.store.connect()
        connection.execute("PRAGMA journal_mode = MEMORY")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA cache_size = -200000")
        try:
            connection.executescript(SCHEMA_SQL + MEASURED_STAGE_SQL)
            self._load_food_meta(connection)
            branded_ids, excluded = self._load_branded_food(connection)
            connection.executescript(FINAL_SQL)
            measured = self._load_measured(connection, branded_ids) if self.include_measured else 0
            connection.commit()
            counts = self.store.counts()
            signatures = self.signatures()
            manifest = {
                "schema_version": self.store.schema_version,
                "built_at": utc_now(),
                "app_version": self.config.get("app.version", "1.0.0"),
                "include_measured": self.include_measured,
                "excluded_without_ingredients": excluded,
                "foods": counts.get("foods", 0),
                "derived_nutrients": counts.get("derived_nutrients", 0),
                "measured_nutrients": counts.get("measured_nutrients", 0),
                "dataset": self.source.describe(),
                **signatures,
            }
            self.store.write_manifest({key: value if not isinstance(value, dict) else _json(value)
                                       for key, value in manifest.items()})
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA optimize")
        finally:
            connection.close()

        exports = self.store.export()
        summary = IngestSummary(
            built_at=manifest["built_at"],
            elapsed_seconds=time.perf_counter() - started,
            foods=counts.get("foods", 0),
            derived_nutrients=counts.get("derived_nutrients", 0),
            measured_nutrients=measured,
            excluded_without_ingredients=excluded,
            dataset=self.source.describe(),
            exports=exports,
        )
        logger.info("store built in %.1fs: %s foods, %s derived nutrient rows",
                    summary.elapsed_seconds, summary.foods, summary.derived_nutrients)
        return summary

    # -- passes ----------------------------------------------------------
    def _load_food_meta(self, connection: sqlite3.Connection) -> None:
        filename = self.config.get("dataset.file.food")
        logger.info("reading %s", filename)
        buffer: list[tuple] = []
        total = 0
        for row in self.source.rows(filename, ["fdc_id", "data_type", "description", "food_category_id"]):
            if row["data_type"] != "branded_food":
                continue
            buffer.append((int(row["fdc_id"]), row["description"], row["data_type"], row["food_category_id"]))
            total += 1
            if len(buffer) >= self.batch_size:
                connection.executemany(
                    "INSERT OR REPLACE INTO food_meta (fdc_id, description, data_type, food_category_id) "
                    "VALUES (?, ?, ?, ?)", buffer)
                buffer.clear()
            if total % self.progress_every == 0:
                logger.info("food.csv: %s branded rows", total)
        if buffer:
            connection.executemany(
                "INSERT OR REPLACE INTO food_meta (fdc_id, description, data_type, food_category_id) "
                "VALUES (?, ?, ?, ?)", buffer)
        connection.commit()
        logger.info("food.csv: %s branded rows loaded", total)

    def _load_branded_food(self, connection: sqlite3.Connection) -> tuple[set[int], int]:
        filename = self.config.get("dataset.file.branded_food")
        logger.info("reading %s (ingredient parsing + crosswalk)", filename)
        staged: list[tuple] = []
        derived: list[tuple] = []
        excluded = 0
        total = 0
        branded_ids: set[int] = set()
        for row in self.source.rows(
                filename, ["fdc_id", "brand_owner", "brand_name", "gtin_upc",
                           "serving_size", "serving_size_unit", "ingredients"]):
            total += 1
            phrases = self.parser.parse(row["ingredients"])
            if not phrases:
                excluded += 1
                continue
            fdc_id = int(row["fdc_id"])
            branded_ids.add(fdc_id)
            staged.append((
                fdc_id,
                row["brand_owner"],
                row["brand_name"],
                row["gtin_upc"],
                _as_float(row["serving_size"]),
                row["serving_size_unit"],
                "|".join(phrase.text for phrase in phrases),
            ))
            for presence in self.reference.derive_nutrients(phrases):
                derived.append((fdc_id, presence.nutrient_key, presence.source_kind,
                                "|".join(presence.phrases)))
            if len(staged) >= self.batch_size:
                self._flush_branded(connection, staged, derived)
            if total % self.progress_every == 0:
                logger.info("branded_food.csv: %s rows, %s foods kept, %s excluded",
                            total, len(branded_ids), excluded)
        self._flush_branded(connection, staged, derived)
        connection.commit()
        logger.info("branded_food.csv: %s rows, %s foods kept, %s excluded (no ingredients)",
                    total, len(branded_ids), excluded)
        return branded_ids, excluded

    def _flush_branded(self, connection: sqlite3.Connection, staged: list[tuple], derived: list[tuple]) -> None:
        if staged:
            connection.executemany(
                "INSERT OR REPLACE INTO food_stage (fdc_id, brand_owner, brand_name, gtin_upc, "
                "serving_size, serving_size_unit, ingredient_list) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", staged)
            staged.clear()
        if derived:
            connection.executemany(
                "INSERT OR REPLACE INTO derived_nutrient (fdc_id, nutrient_key, source_kind, phrases) "
                "VALUES (?, ?, ?, ?)", derived)
            derived.clear()

    def _load_measured(self, connection: sqlite3.Connection, branded_ids: set[int]) -> int:
        filename = self.config.get("dataset.file.food_nutrient")
        logger.info("reading %s (quantities)", filename)
        buffer: list[tuple] = []
        total = 0
        kept = 0
        for row in self.source.rows(filename, ["fdc_id", "nutrient_id", "amount"]):
            total += 1
            try:
                nutrient_id = int(row["nutrient_id"])
            except (TypeError, ValueError):
                continue
            alias = self.reference.usda_index.get(nutrient_id)
            if alias is None:
                continue
            try:
                fdc_id = int(row["fdc_id"])
                amount = float(row["amount"])
            except (TypeError, ValueError):
                continue
            if fdc_id not in branded_ids:
                continue
            nutrient_key, _factor, _unit = alias
            normalised, dv_percent, unit = self.reference.dv_percent(nutrient_key, amount, nutrient_id)
            rank = 0 if self.reference.nutrients[nutrient_key].primary_usda_id == nutrient_id else 1
            buffer.append((fdc_id, nutrient_key, normalised, unit, dv_percent, rank))
            kept += 1
            if len(buffer) >= self.batch_size:
                connection.executemany(
                    "INSERT INTO measured_stage (fdc_id, nutrient_key, amount, unit, dv_percent, alias_rank) "
                    "VALUES (?, ?, ?, ?, ?, ?)", buffer)
                buffer.clear()
            if total % self.progress_every == 0:
                logger.info("food_nutrient.csv: %s rows, %s relevant", total, kept)
        if buffer:
            connection.executemany(
                "INSERT INTO measured_stage (fdc_id, nutrient_key, amount, unit, dv_percent, alias_rank) "
                "VALUES (?, ?, ?, ?, ?, ?)", buffer)
        connection.commit()
        connection.executescript(MEASURED_FINALISE_SQL)
        connection.commit()
        logger.info("food_nutrient.csv: %s rows, %s relevant quantities stored", total, kept)
        return kept


def _as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json(value: dict) -> str:
    import json
    return json.dumps(value, sort_keys=True)


def build_store(config: Config, force: bool = False) -> dict[str, Any]:
    """Convenience entry point used by the CLI, API and GUI."""
    from .reference import ReferenceData

    store = Store(config)
    source = DatasetSource(config)
    reference = ReferenceData.load(config)
    parser = IngredientParser(config)
    ingestor = Ingestor(config, store, source, reference, parser)
    return ingestor.build(force=force).as_dict()


def store_reference_files(config: Config) -> list[Path]:
    """Reference files the persisted store actually depends on.

    The interaction knowledge base is deliberately absent: it is consumed by the
    analyzer at query time, never by the ingest, so regenerating it must not force a
    2 million row rebuild. Aliases, daily values and the crosswalk do affect the
    derived/measured data, so they stay.
    """
    return [config.path("reference.dir") / config.get(key) for key in
            ("reference.file.aliases", "reference.file.dv",
             "reference.file.crosswalk")]
