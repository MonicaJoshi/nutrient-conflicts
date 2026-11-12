# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""SQLite persistence layer plus the JSON Lines snapshot.

The store is the first-run cache: it is built once from the USDA CSVs and every
later query reads it directly. ``status`` explains why a rebuild is needed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE food_stage (
    fdc_id INTEGER PRIMARY KEY,
    brand_owner TEXT,
    brand_name TEXT,
    gtin_upc TEXT,
    serving_size REAL,
    serving_size_unit TEXT,
    ingredient_list TEXT
);

CREATE TABLE food_meta (
    fdc_id INTEGER PRIMARY KEY,
    description TEXT,
    data_type TEXT,
    food_category_id TEXT
);

CREATE TABLE derived_nutrient (
    fdc_id INTEGER NOT NULL,
    nutrient_key TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    phrases TEXT NOT NULL,
    PRIMARY KEY (fdc_id, nutrient_key)
);

CREATE TABLE measured_nutrient (
    fdc_id INTEGER NOT NULL,
    nutrient_key TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    dv_percent REAL,
    PRIMARY KEY (fdc_id, nutrient_key)
);
"""

FINAL_SQL = """
CREATE TABLE food (
    fdc_id INTEGER PRIMARY KEY,
    brand_owner TEXT,
    brand_name TEXT,
    gtin_upc TEXT,
    serving_size REAL,
    serving_size_unit TEXT,
    description TEXT,
    data_type TEXT,
    food_category_id TEXT,
    ingredient_list TEXT
);

INSERT INTO food (fdc_id, brand_owner, brand_name, gtin_upc, serving_size, serving_size_unit,
                  description, data_type, food_category_id, ingredient_list)
SELECT s.fdc_id, s.brand_owner, s.brand_name, s.gtin_upc, s.serving_size, s.serving_size_unit,
       m.description, m.data_type, m.food_category_id, s.ingredient_list
FROM food_stage s LEFT JOIN food_meta m ON m.fdc_id = s.fdc_id;

DROP TABLE food_stage;
DROP TABLE food_meta;

CREATE INDEX ix_food_brand_owner ON food (brand_owner COLLATE NOCASE);
CREATE INDEX ix_food_brand_name ON food (brand_name COLLATE NOCASE);
CREATE INDEX ix_food_upc ON food (gtin_upc);
CREATE INDEX ix_derived_nutrient_key ON derived_nutrient (nutrient_key);
"""


class Store:
    """Owns the SQLite file, its manifest and the export snapshots."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.directory = config.path("store.dir")
        self.path = self.directory / config.get("store.db.file", "nutrientconflicts.sqlite")
        self.jsonl_path = self.directory / config.get("store.jsonl.file", "branded_foods.jsonl.gz")
        self.schema_version = config.get_int("store.schema.version", 1)
        self._write_lock = threading.Lock()

    # -- connection ------------------------------------------------------
    def connect(self, readonly: bool = False) -> sqlite3.Connection:
        if not readonly:
            self.directory.mkdir(parents=True, exist_ok=True)
        if readonly:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def exists(self) -> bool:
        return self.path.is_file()

    def open_readonly(self) -> sqlite3.Connection:
        if not self.exists():
            raise FileNotFoundError(
                f"store not built at {self.path}; run: nutrient-conflicts ingest"
            )
        return self.connect(readonly=True)

    @contextmanager
    def session(self, readonly: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self.open_readonly() if readonly else self.connect()
        try:
            yield connection
        finally:
            connection.close()

    # -- manifest --------------------------------------------------------
    def read_manifest(self) -> dict[str, str]:
        if not self.exists():
            return {}
        try:
            with self.session() as connection:
                rows = connection.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {row["key"]: row["value"] for row in rows}

    def write_manifest(self, values: dict[str, Any]) -> None:
        with self.session(readonly=False) as connection:
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, str(value)) for key, value in values.items()],
            )
            connection.commit()

    def counts(self) -> dict[str, int]:
        if not self.exists():
            return {}
        try:
            with self.session() as connection:
                return {
                    "foods": _scalar(connection, "SELECT COUNT(*) FROM food"),
                    "derived_nutrients": _scalar(connection, "SELECT COUNT(*) FROM derived_nutrient"),
                    "measured_nutrients": _scalar(connection, "SELECT COUNT(*) FROM measured_nutrient"),
                }
        except sqlite3.DatabaseError:
            # a partially written or zero-byte store is simply "not built"
            return {}

    def status(self, signatures: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = self.read_manifest()
        reasons: list[str] = []
        if not self.exists():
            reasons.append("store file does not exist")
        else:
            if manifest.get("schema_version") != str(self.schema_version):
                reasons.append(
                    f"schema version changed ({manifest.get('schema_version')} -> {self.schema_version})"
                )
            if signatures:
                if manifest.get("dataset_signature") != signatures.get("dataset_signature"):
                    reasons.append("dataset files changed")
                if manifest.get("reference_signature") != signatures.get("reference_signature"):
                    reasons.append("reference data changed")
                if manifest.get("parser_signature") != signatures.get("parser_signature"):
                    reasons.append("ingredient parsing configuration changed")
        return {
            "exists": self.exists(),
            "path": str(self.path),
            "stale": bool(reasons),
            "reasons": reasons,
            "manifest": manifest,
            "counts": self.counts(),
            "exports": {
                "jsonl": str(self.jsonl_path) if self.jsonl_path.is_file() else "",
            },
        }

    def purge(self) -> dict[str, Any]:
        removed = []
        for path in (self.path, self.jsonl_path):
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        return {"removed": removed}

    def signature_inputs(self, dataset: dict, reference_files: list[Path], parser_config: dict[str, str]) -> dict[str, str]:
        return {
            "dataset_signature": _hash_payload(dataset),
            "reference_signature": _hash_payload(
                [{"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime}
                 for path in reference_files if path.is_file()]
            ),
            "parser_signature": _hash_payload(parser_config),
        }

    # -- export ----------------------------------------------------------
    def export(self) -> dict[str, Any]:
        """Write the py-readable JSON Lines snapshot from the food + derived tables.

        One JSON object per line:
        ``{fdc_id, brand, brand_owner, data_type, food_category_id, description,
        nutrient_list}``.
        """
        if not self.exists():
            raise FileNotFoundError("store not built; run ingest first")
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = self.open_readonly()
        try:
            query = """
                SELECT f.fdc_id, f.brand_name, f.brand_owner, f.data_type, f.food_category_id,
                       f.description, d.nutrients, f.ingredient_list
                FROM food f
                LEFT JOIN (
                    SELECT fdc_id, group_concat(nutrient_key, '|') AS nutrients
                    FROM (SELECT fdc_id, nutrient_key FROM derived_nutrient ORDER BY fdc_id, nutrient_key)
                    GROUP BY fdc_id
                ) d ON d.fdc_id = f.fdc_id
                ORDER BY f.fdc_id
            """
            written = self._write_jsonl(connection, query)
        finally:
            connection.close()
        return {"jsonl": str(self.jsonl_path), "rows": written}

    def _write_jsonl(self, connection: sqlite3.Connection, query: str) -> int:
        temporary = self.jsonl_path.with_suffix(".gz.tmp")
        rows = 0
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            for row in connection.execute(query):
                nutrients = [item for item in (row[6] or "").split("|") if item]
                ingredient_list = [item for item in (row[7] or "").split("|") if item]
                handle.write(json.dumps({
                    "fdc_id": row[0],
                    "brand": row[1] or "",
                    "brand_owner": row[2] or "",
                    "data_type": row[3] or "",
                    "food_category_id": row[4] or "",
                    "description": row[5] or "",
                    "nutrient_list": ingredient_list,
                    "nutrient_keys": nutrients,
                }, ensure_ascii=False) + "\n")
                rows += 1
        temporary.replace(self.jsonl_path)
        return rows

    def warm_cache(self, cache: Any, limit: int) -> int:
        """Load the first ``limit`` persisted rows into the in-process cache.

        This is the "load from disk to cache at startup" step. The JSONL is the
        persisted result of the first run; the cache avoids re-reading it for the
        foods a user is most likely to query first.
        """
        if not self.jsonl_path.is_file() or limit <= 0:
            return 0
        loaded = 0
        with gzip.open(self.jsonl_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fdc_id = record.get("fdc_id")
                if fdc_id is None:
                    continue
                cache.set("food_index", str(fdc_id), record)
                loaded += 1
                if loaded >= limit:
                    break
        return loaded


def _scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    return int(row[0]) if row else 0


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_batches(items: Iterator[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
