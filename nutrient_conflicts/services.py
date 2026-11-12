# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Shared query/service layer.

CLI, HTTP API and GUI all call these methods and nothing else, so the three
interfaces cannot drift apart. Results are cached (memory then persisted) because
the same food or brand is usually inspected several times in one session.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Iterable

from .analyzer import Analyzer
from .brandscan import BrandConflictScanner, BrandScanError
from .cache import Cache
from .config import Config
from .datasource import DatasetSource, DatasetError
from .ingest import Ingestor, store_reference_files
from .models import FoodRecord, MeasuredAmount, NutrientPresence
from .reference import ReferenceData
from .refsources.builder import ReferenceBuilder, ReferenceBuildError
from .store import Store

logger = logging.getLogger(__name__)


class NotFoundError(LookupError):
    """Raised when a food or brand cannot be found."""


class ServiceError(RuntimeError):
    """Raised for operational problems (store not built, dataset missing)."""


class NutrientConflictsService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config)
        self.cache = Cache(config)
        self._reference: ReferenceData | None = None
        self._analyzer: Analyzer | None = None
        self._reference_builder: ReferenceBuilder | None = None
        self._brand_scanner: BrandConflictScanner | None = None
        self._warmed = False
        self.default_limit = config.get_int("api.default.page.size", 25)
        self.max_limit = config.get_int("api.max.page.size", 200)

    # -- lazily built collaborators --------------------------------------
    @property
    def reference(self) -> ReferenceData:
        if self._reference is None:
            self._reference = ReferenceData.load(self.config)
        return self._reference

    @property
    def analyzer(self) -> Analyzer:
        if self._analyzer is None:
            self._analyzer = Analyzer(self.config, self.reference)
        return self._analyzer

    @property
    def reference_builder(self) -> ReferenceBuilder:
        if self._reference_builder is None:
            self._reference_builder = ReferenceBuilder(self.config)
        return self._reference_builder

    @property
    def brand_scanner(self) -> BrandConflictScanner:
        if self._brand_scanner is None:
            self._brand_scanner = BrandConflictScanner(self.config, store=self.store,
                                                      reference=self.reference)
        return self._brand_scanner

    # -- store lifecycle -------------------------------------------------
    def store_status(self) -> dict[str, Any]:
        ingestor = self._ingestor()
        return self.store.status(ingestor.signatures())

    def ensure_store(self) -> None:
        status = self.store_status()
        if not status["exists"] or status["stale"]:
            if not self.config.get_bool("store.auto.build", True):
                raise ServiceError(
                    "store is missing or stale; run 'nutrient-conflicts ingest' "
                    f"({'; '.join(status['reasons'])})"
                )
            logger.info("building store: %s", "; ".join(status["reasons"]) or "first run")
            self.ingest()
        self._warm_once()

    def _warm_once(self) -> None:
        """First startup: load the small reference + persisted index into cache."""
        if self._warmed:
            return
        self._warmed = True
        _ = self.reference
        limit = self.config.get_int("store.warm.entries", 2000)
        try:
            loaded = self.store.warm_cache(self.cache, limit)
            if loaded:
                logger.info("warmed cache with %s persisted food record(s)", loaded)
        except OSError as exc:  # pragma: no cover - unreadable export
            logger.warning("could not warm the cache from the JSONL export: %s", exc)

    def ingest(self, force: bool = False) -> dict[str, Any]:
        summary = self._ingestor().build(force=force)
        self.cache.clear()
        return summary.as_dict()

    def _ingestor(self) -> Ingestor:
        return Ingestor(self.config, self.store, DatasetSource(self.config), self.reference,
                        _parser(self.config))

    def store_purge(self) -> dict[str, Any]:
        removed = self.store.purge()
        self.cache.clear()
        return removed

    def export(self) -> dict[str, Any]:
        self.ensure_store()
        return self.store.export()

    # -- reference queries -----------------------------------------------
    def nutrients(self) -> dict[str, Any]:
        cached = self.cache.get("nutrients", "all")
        if cached is not None:
            return cached
        payload = {
            "nutrients": [
                {
                    "nutrient_key": item.nutrient_key,
                    "name": item.canonical_name,
                    "category": item.category,
                    "dv_amount": item.dv_amount,
                    "dv_unit": item.dv_unit,
                    "usda_nutrient_ids": list(item.usda_ids),
                }
                for item in sorted(self.reference.nutrients.values(), key=lambda n: n.nutrient_key)
            ]
        }
        self.cache.set("nutrients", "all", payload)
        return payload

    def interactions(self, relation: str | None = None, nutrient: str | None = None) -> dict[str, Any]:
        key = json.dumps({"relation": relation, "nutrient": nutrient}, sort_keys=True)
        cached = self.cache.get("interactions", key)
        if cached is not None:
            return cached
        items = self.reference.interactions
        if relation:
            wanted = relation.strip().upper()
            items = [item for item in items if item.relation == wanted]
        if nutrient:
            wanted_nutrient = nutrient.strip().upper()
            items = [item for item in items if wanted_nutrient in item.pair]
        payload = {
            "count": len(items),
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
                for item in items
            ],
            "diagnostics": self.reference.diagnostics,
        }
        self.cache.set("interactions", key, payload)
        return payload

    def crosswalk(self) -> dict[str, Any]:
        return self.reference.as_dict()["crosswalk"]

    # -- reference source pipeline ---------------------------------------
    def reference_status(self) -> dict[str, Any]:
        return self.reference_builder.status()

    def reference_build(self, sources: Iterable[str] | None = None,
                        allow_removals: bool | None = None,
                        accept_source_change: bool = False) -> dict[str, Any]:
        """Regenerate reference data 1 and 2 and the merged knowledge base."""
        try:
            summary = self.reference_builder.build(
                sources=sources or ("vitamin-mineral", "biological"),
                allow_removals=allow_removals,
                accept_source_change=accept_source_change,
            )
        except (ReferenceBuildError, ValueError, FileNotFoundError) as exc:
            raise ServiceError(str(exc)) from exc
        self.cache.clear()
        self._reference = None
        self._analyzer = None
        self._brand_scanner = None
        return summary

    def reference_crosscheck(self) -> dict[str, Any]:
        """The generated-vs-curated diff persisted by the last reference build."""
        path = self.config.path("reference.crosscheck.file")
        if not path.is_file():
            raise ServiceError(
                "no reference cross-check file; run 'nutrient-conflicts reference build' first"
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServiceError(f"could not read {path}: {exc}") from exc

    def reference_clear(self, keep_kb: bool = False) -> dict[str, Any]:
        removed = self.reference_builder.clear(keep_kb=keep_kb)
        cleared = self.cache.clear()
        return {**removed, "cache_cleared": cleared}

    # -- brand conflicts (result file 3) ---------------------------------
    def conflicts_status(self) -> dict[str, Any]:
        return self.brand_scanner.status()

    def conflicts_build(self, force: bool = False) -> dict[str, Any]:
        try:
            summary = self.brand_scanner.build(force=force)
        except (BrandScanError, ValueError, FileNotFoundError) as exc:
            raise ServiceError(str(exc)) from exc
        self.cache.clear()
        return summary

    def conflicts_clear(self) -> dict[str, Any]:
        removed = self.brand_scanner.clear()
        cleared = self.cache.clear()
        return {**removed, "cache_cleared": cleared}

    def brand_conflicts(self, brand_owner: str, page: int = 1, size: int | None = None,
                        basis: str = "per_100g") -> dict[str, Any]:
        """The brand attribute: per-product rows plus a brand level rollup.

        Each product carries the same fields as a single ``fdc_id`` query. The
        ``aggregate`` block is built from the persisted brand scan so it covers the
        whole product line without evaluating every product on each request.
        """
        name = (brand_owner or "").strip()
        if not name:
            raise ValueError("brand name is required")
        size = self._clamp(size, default=self.config.get_int("api.default.page.size", 25))
        page = max(1, int(page))
        cache_key = json.dumps({"brand": name.upper(), "page": page, "size": size, "basis": basis},
                               sort_keys=True)
        cached = self.cache.get("brand_conflicts", cache_key)
        if cached is not None:
            return cached

        self.ensure_conflicts()
        canonical, rows = self._brand_rows(name)
        if canonical is None:
            if not self.store.exists():
                raise NotFoundError(f"brand not found: {brand_owner}")
            with self.store.session() as connection:
                found = connection.execute(
                    "SELECT brand_owner FROM food WHERE brand_owner = ? COLLATE NOCASE LIMIT 1",
                    (name,)).fetchone()
            if found is None:
                raise NotFoundError(f"brand not found: {brand_owner}")
            canonical = found[0]

        aggregate = self._conflict_payload(canonical, rows)
        with self.store.session() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM food WHERE brand_owner = ? COLLATE NOCASE",
                (canonical,)).fetchone()[0])
            ids = [row[0] for row in connection.execute(
                "SELECT fdc_id FROM food WHERE brand_owner = ? COLLATE NOCASE "
                "ORDER BY description, fdc_id LIMIT ? OFFSET ?",
                (canonical, size, (page - 1) * size)).fetchall()]
        products = [self.evaluate_food(fdc_id, basis=basis) for fdc_id in ids]
        payload = {
            "brand_owner": canonical,
            "total_products": total,
            "total_foods": total,
            "evaluated_foods": len(products),
            "nutrients": aggregate["nutrients"],
            "interactions": aggregate["interactions"],
            "page": page,
            "size": size,
            "pages": (total + size - 1) // size if size else 0,
            "products": products,
            "aggregate": {
                "nutrients": aggregate["nutrients"],
                "interactions": aggregate["interactions"],
                "summary": aggregate["summary"],
                "warning": aggregate["warning"],
            },
            "summary": aggregate["summary"],
            "warning": aggregate["warning"],
            "notes": aggregate["notes"],
            "disclaimer": aggregate["disclaimer"],
        }
        self.cache.set("brand_conflicts", cache_key, payload)
        return payload

    def ensure_conflicts(self) -> None:
        status = self.brand_scanner.status()
        if status["exists"] and not status["stale"]:
            return
        if not self.config.get_bool("conflicts.auto.build", True):
            raise ServiceError(
                "brand conflict results are missing or stale; run 'nutrient-conflicts conflicts build' "
                f"({'; '.join(status['reasons'])})"
            )
        self.ensure_store()
        self.conflicts_build(force=True)

    def _brand_rows(self, brand_owner: str):
        """Find the brand case-insensitively and return its persisted pair rows."""
        scanner = self.brand_scanner
        rows = scanner.brand_rows(brand_owner)
        if rows:
            return rows[0].brand_owner, rows
        wanted = brand_owner.upper()
        for candidate in self._brand_keys():
            if candidate.upper() == wanted:
                rows = scanner.brand_rows(candidate)
                if rows:
                    return candidate, rows
        return None, []

    def _brand_keys(self) -> list[str]:
        cached = self.cache.get("brand_conflict_names", "all")
        if cached is not None:
            return cached
        keys = self.brand_scanner.brand_names()
        self.cache.set("brand_conflict_names", "all", keys)
        return keys

    def _conflict_payload(self, brand_owner: str, rows) -> dict[str, Any]:
        nutrients = self._brand_nutrients(brand_owner, rows)
        order = {"ANTAGONISTIC": 0, "SYNERGISTIC": 1, "CONTEXT_DEPENDENT": 2}
        table = [self._conflict_row(row) for row in
                 sorted(rows, key=lambda item: (order.get(item.relation, 9),
                                                item.nutrient_a, item.nutrient_b))]
        antagonistic = [item for item in table if item["relation"] == "ANTAGONISTIC"]
        synergistic = [item for item in table if item["relation"] == "SYNERGISTIC"]
        context = [item for item in table if item["relation"] == "CONTEXT_DEPENDENT"]
        conflicting = bool(antagonistic)

        summary_text = (
            self.config.get("conflicts.summary.conflict.text")
            .format(brand=brand_owner, antagonistic=len(antagonistic),
                    synergistic=len(synergistic), context_dependent=len(context),
                    pairs=len(table))
            if conflicting
            else self.config.get("conflicts.summary.none.text").format(
                brand=brand_owner, antagonistic=len(antagonistic),
                synergistic=len(synergistic), context_dependent=len(context), pairs=len(table))
        )

        warning: dict[str, Any] = {"required": conflicting}
        if conflicting:
            pair_labels = ", ".join(item["pair"] for item in antagonistic)
            warning.update({
                "text": self.config.get("conflicts.warning.text").format(
                    brand=brand_owner, pairs=pair_labels,
                    quantities="; ".join(self._quantity_text(item) for item in antagonistic)),
                "pairs": [item["pair"] for item in antagonistic],
                "quantities": [self._quantity_text(item) for item in antagonistic],
                "next_steps": self.config.get_list("conflicts.next.steps", separator="|"),
            })

        payload = {
            "brand_owner": brand_owner,
            "total_products": self._brand_product_count(brand_owner),
            "nutrients": nutrients,
            "interactions": table,
            "table": table,
            "summary": {
                "conflicting": conflicting,
                "statement": summary_text,
                "counts": {"antagonistic": len(antagonistic), "synergistic": len(synergistic),
                           "context_dependent": len(context), "combinations": len(table)},
            },
            "warning": warning,
            "notes": [
                "A combination is reported when both nutrients appear in the brand's product "
                "line; co_occurring_products counts the products that contain both.",
                "Quantities are the highest measured value for that brand, as percent of the "
                "FDA daily value per 100 g; 'quantity not published' means the dataset has no "
                "measurement for that nutrient.",
                f"Values above {self.config.get_float('conflicts.quantity.max.dv.percent', 1000.0):g}% DV "
                "are treated as data-entry artefacts and ignored when summarising quantities.",
            ],
            "disclaimer": self.config.get("app.disclaimer", ""),
        }
        return payload

    def _conflict_row(self, row) -> dict[str, Any]:
        return {
            "pair": f"{self.brand_scanner.display(row.nutrient_a)} + "
                    f"{self.brand_scanner.display(row.nutrient_b)}",
            "nutrient_a": row.nutrient_a,
            "nutrient_b": row.nutrient_b,
            "relation": row.relation,
            "highlighted": row.relation == "ANTAGONISTIC",
            "strength": row.strength,
            "evidence_level": row.evidence_level,
            "co_occurring_products": row.co_occurring_products,
            "products_a": row.nutrient_a_products,
            "products_b": row.nutrient_b_products,
            "sample_fdc_ids": list(row.sample_fdc_ids),
            "dv_percent_a": row.max_dv_percent_a,
            "dv_percent_b": row.max_dv_percent_b,
            "quantity_known": row.max_dv_percent_a is not None and row.max_dv_percent_b is not None,
            "mechanism": row.mechanism,
            "source": row.source,
        }

    def _quantity_text(self, item: dict[str, Any]) -> str:
        def part(key: str, dv_key: str) -> str:
            display = self.brand_scanner.display(item[key])
            value = item[dv_key]
            return f"{display} {'quantity not published' if value is None else f'up to {value:.1f}% DV'}"

        return f"{item['pair']}: {part('nutrient_a', 'dv_percent_a')}, {part('nutrient_b', 'dv_percent_b')}"

    def _brand_nutrients(self, brand_owner: str, rows) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        for row in rows:
            for key, products, dv in ((row.nutrient_a, row.nutrient_a_products, row.max_dv_percent_a),
                                      (row.nutrient_b, row.nutrient_b_products, row.max_dv_percent_b)):
                entry = collected.setdefault(key, {"nutrient_key": key,
                                                   "name": self.brand_scanner.display(key),
                                                   "products": 0, "max_dv_percent": None,
                                                   "in_combinations": 0})
                entry["products"] = max(entry["products"], products)
                if dv is not None and (entry["max_dv_percent"] is None or dv > entry["max_dv_percent"]):
                    entry["max_dv_percent"] = dv
                entry["in_combinations"] += 1
        return [collected[key] for key in sorted(collected)]

    def _brand_product_count(self, brand_owner: str) -> int:
        if not self.store.exists():
            return 0
        with self.store.session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM food WHERE brand_owner = ? COLLATE NOCASE",
                (brand_owner,)).fetchone()
        return int(row[0]) if row else 0

    # -- food queries ----------------------------------------------------
    def search_foods(self, brand: str | None = None, query: str | None = None,
                     category: str | None = None, limit: int | None = None,
                     offset: int = 0) -> dict[str, Any]:
        self.ensure_store()
        limit = self._clamp(limit)
        cache_key = json.dumps({"brand": brand, "query": query, "category": category,
                                "limit": limit, "offset": offset}, sort_keys=True)
        cached = self.cache.get("search", cache_key)
        if cached is not None:
            return cached
        where, params = [], []
        if brand:
            where.append("brand_owner = ? COLLATE NOCASE")
            params.append(brand.strip())
        if query:
            where.append("(description LIKE ? OR brand_name LIKE ? OR gtin_upc = ?)")
            like = f"%{query.strip()}%"
            params.extend([like, like, query.strip()])
        if category:
            where.append("food_category_id = ? COLLATE NOCASE")
            params.append(category.strip())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.store.session() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM food {clause}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT fdc_id, brand_owner, brand_name, description, food_category_id "
                f"FROM food {clause} ORDER BY brand_owner, description LIMIT ? OFFSET ?",
                [*params, limit, offset]).fetchall()
        payload = {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [dict(row) for row in rows],
        }
        self.cache.set("search", cache_key, payload)
        return payload

    def brands(self, prefix: str | None = None, limit: int | None = None) -> dict[str, Any]:
        self.ensure_store()
        limit = self._clamp(limit, default=50)
        cache_key = json.dumps({"prefix": prefix, "limit": limit}, sort_keys=True)
        cached = self.cache.get("brands", cache_key)
        if cached is not None:
            return cached
        params: list[Any] = []
        clause = ""
        if prefix:
            clause = "WHERE brand_owner LIKE ? COLLATE NOCASE"
            params.append(f"%{prefix.strip()}%")
        with self.store.session() as connection:
            rows = connection.execute(
                f"SELECT brand_owner, COUNT(*) AS foods FROM food {clause} "
                f"GROUP BY brand_owner ORDER BY foods DESC, brand_owner LIMIT ?",
                [*params, limit]).fetchall()
        payload = {"items": [dict(row) for row in rows]}
        self.cache.set("brands", cache_key, payload)
        return payload

    def get_food(self, fdc_id: int) -> dict[str, Any]:
        self.ensure_store()
        cache_key = str(fdc_id)
        cached = self.cache.get("food", cache_key)
        if cached is not None:
            return cached
        with self.store.session() as connection:
            row = connection.execute("SELECT * FROM food WHERE fdc_id = ?", (fdc_id,)).fetchone()
            if row is None:
                raise NotFoundError(_not_found_message(connection, fdc_id))
            derived = connection.execute(
                "SELECT nutrient_key, source_kind, phrases FROM derived_nutrient WHERE fdc_id = ? "
                "ORDER BY nutrient_key", (fdc_id,)).fetchall()
            measured = connection.execute(
                "SELECT nutrient_key, amount, unit, dv_percent FROM measured_nutrient WHERE fdc_id = ? "
                "ORDER BY nutrient_key", (fdc_id,)).fetchall()
        payload = {
            "food": dict(row),
            "derived_nutrients": [
                {"nutrient_key": item["nutrient_key"], "source_kind": item["source_kind"],
                 "phrases": [phrase for phrase in (item["phrases"] or "").split("|") if phrase]}
                for item in derived
            ],
            "measured_nutrients": [
                {"nutrient_key": item["nutrient_key"], "amount": item["amount"], "unit": item["unit"],
                 "dv_percent": item["dv_percent"]}
                for item in measured
            ],
        }
        self.cache.set("food", cache_key, payload)
        return payload

    # -- evaluation ------------------------------------------------------
    def evaluate_food(self, fdc_id: int, basis: str = "per_100g",
                      min_dv_percent: float | None = None) -> dict[str, Any]:
        self.ensure_store()
        cache_key = json.dumps({"fdc_id": fdc_id, "basis": basis, "min_dv": min_dv_percent})
        cached = self.cache.get("evaluation", cache_key)
        if cached is not None:
            return cached
        record, derived, measured = self._load_profile(fdc_id)
        analyzer = self.analyzer if min_dv_percent is None else Analyzer(
            self.config.with_overrides({"analysis.min.dv.percent": str(min_dv_percent)}),
            self.reference)
        evaluation = analyzer.evaluate(record, derived, measured, basis=basis)
        payload = self._product_payload(evaluation.as_dict())
        self.cache.set("evaluation", cache_key, payload)
        return payload

    # -- response contract ----------------------------------------------
    def _product_payload(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        """Shape one evaluation into the required response object."""
        food = dict(evaluation["food"])
        brand = food.get("brand_name") or food.get("brand_owner") or ""
        product = food.get("description") or str(food.get("fdc_id", ""))
        nutrients = evaluation.get("nutrients", [])
        raw_list = food.get("ingredient_list") or []
        if isinstance(raw_list, str):
            nutrient_list = [item for item in raw_list.split("|") if item]
        else:
            nutrient_list = [item for item in raw_list if item]
        if not nutrient_list:
            for row in nutrients:
                for phrase in row.get("phrases", []):
                    if phrase not in nutrient_list:
                        nutrient_list.append(phrase)
        by_key = {row["nutrient_key"]: row for row in nutrients}
        findings = evaluation.get("findings", [])
        active = [row for row in findings if row.get("active")]
        rows = [self._pair_row(row, by_key) for row in active]
        synergistic = [row for row in rows if row["relation"] == "SYNERGISTIC"]
        antagonistic = [row for row in rows if row["relation"] == "ANTAGONISTIC"]
        context = [row for row in rows if row["relation"] == "CONTEXT_DEPENDENT"]
        conflicting = bool(antagonistic)
        template_key = ("product.summary.conflict.text" if conflicting
                        else "product.summary.none.text")
        statement = self.config.get(template_key).format(
            brand=brand, product=product, antagonistic=len(antagonistic),
            synergistic=len(synergistic), context_dependent=len(context), pairs=len(rows))
        warning: dict[str, Any] = {"required": conflicting}
        if conflicting:
            warning.update({
                "text": self.config.get("product.warning.text").format(
                    brand=brand, product=product,
                    pairs=", ".join(row["pair"] for row in antagonistic),
                    quantities="; ".join(self._product_quantity_text(row) for row in antagonistic)),
                "pairs": [row["pair"] for row in antagonistic],
                "quantities": [self._product_quantity_text(row) for row in antagonistic],
                "next_steps": self.config.get_list("conflicts.next.steps", separator="|"),
            })
        food["brand"] = brand
        return {
            "brand": brand,
            "fdc_id": food.get("fdc_id"),
            "description": food.get("description", ""),
            "food_category_id": food.get("food_category_id", ""),
            "data_type": food.get("data_type", ""),
            "brand_owner": food.get("brand_owner", ""),
            "nutrient_list": nutrient_list,
            "nutrient_keys": [row["nutrient_key"] for row in nutrients],
            "nutrients": nutrients,
            "synergistic": synergistic,
            "antagonistic": antagonistic,
            "context_dependent": context,
            "findings": findings,
            "summary": {
                "conflicting": conflicting,
                "statement": statement,
                "counts": {
                    "synergistic": len(synergistic),
                    "antagonistic": len(antagonistic),
                    "context_dependent": len(context),
                    "combinations": len(rows),
                    "pairs_present": len(findings),
                },
            },
            "warning": warning,
            "food": food,
            "basis": evaluation.get("basis", "per_100g"),
            "min_dv_percent": evaluation.get("min_dv_percent"),
            "scores": evaluation.get("scores", {}),
            "counts": evaluation.get("counts", {}),
            "neutral_nutrients": evaluation.get("neutral_nutrients", []),
            "notes": evaluation.get("notes", []),
            "disclaimer": evaluation.get("disclaimer", ""),
        }

    def _pair_row(self, finding: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
        display = self.brand_scanner.display
        key_a, key_b = finding["nutrient_a"], finding["nutrient_b"]
        row = {
            "pair": f"{display(key_a)} + {display(key_b)}",
            "nutrient_a": key_a,
            "nutrient_b": key_b,
            "nutrient_a_name": display(key_a),
            "nutrient_b_name": display(key_b),
            "relation": finding["relation"],
            "highlighted": finding["relation"] == "ANTAGONISTIC",
            "strength": finding["strength"],
            "evidence_level": finding["evidence_level"],
            "mechanism": finding["mechanism"],
            "dose_note": finding.get("dose_note", ""),
            "source": finding.get("source", ""),
            "quantity_known": finding.get("quantity_known", False),
            "dv_percent_a": finding.get("dv_percent_a"),
            "dv_percent_b": finding.get("dv_percent_b"),
            "amount_a": self._amount(by_key.get(key_a)),
            "amount_b": self._amount(by_key.get(key_b)),
            "unit_a": (by_key.get(key_a) or {}).get("unit", ""),
            "unit_b": (by_key.get(key_b) or {}).get("unit", ""),
            "evidences_a": finding.get("evidences_a", []),
            "evidences_b": finding.get("evidences_b", []),
        }
        row["quantity_a"] = self._product_quantity_part(row, "a")
        row["quantity_b"] = self._product_quantity_part(row, "b")
        return row

    @staticmethod
    def _amount(row: dict[str, Any] | None) -> float | None:
        return None if not row or row.get("amount") is None else float(row["amount"])

    def _product_quantity_part(self, row: dict[str, Any], side: str) -> str:
        name = row[f"nutrient_{side}_name"]
        amount = row.get(f"amount_{side}")
        unit = row.get(f"unit_{side}") or ""
        dv = row.get(f"dv_percent_{side}")
        if amount is None:
            return f"{name}: quantity not published"
        suffix = "" if dv is None else f" ({dv:.1f}% DV)"
        return f"{name}: {amount:g} {unit}{suffix}".strip()

    def _product_quantity_text(self, row: dict[str, Any]) -> str:
        return f"{row['pair']}: {row['quantity_a']}, {row['quantity_b']}"

    def evaluate_brand(self, brand_owner: str, limit: int | None = None,
                       basis: str = "per_100g", page: int = 1) -> dict[str, Any]:
        return self.brand_conflicts(brand_owner, page=page, size=limit, basis=basis)

    def evaluate_adhoc(self, nutrient_keys: Iterable[str]) -> dict[str, Any]:
        evaluation = self.analyzer.evaluate_adhoc(nutrient_keys)
        return evaluation.as_dict()

    def raw_ingredients(self, fdc_id: int) -> dict[str, Any]:
        """Re-read the original ingredient string from the CSV (not stored)."""
        cached = self.cache.get("raw_ingredients", str(fdc_id))
        if cached is not None:
            return cached
        source = DatasetSource(self.config)
        filename = self.config.get("dataset.file.branded_food")
        try:
            for row in source.rows(filename, ["fdc_id", "ingredients"]):
                if int(row["fdc_id"]) == fdc_id:
                    payload = {"fdc_id": fdc_id, "ingredients": row["ingredients"]}
                    self.cache.set("raw_ingredients", str(fdc_id), payload)
                    return payload
        except DatasetError as exc:
            raise ServiceError(str(exc)) from exc
        raise NotFoundError(f"food {fdc_id} not present in {filename}")

    # -- cache / diagnostics ---------------------------------------------
    def cache_stats(self) -> dict[str, Any]:
        return self.cache.stats()

    def cache_clear(self) -> dict[str, Any]:
        removed = self.cache.clear()
        return {"cleared": removed}

    def health(self) -> dict[str, Any]:
        conflicts = self.brand_scanner.status()
        return {
            "status": "ok",
            "app": self.config.get("app.name", "NutrientConflicts"),
            "version": self.config.get("app.version", "0.0.0"),
            "profile": self.config.profile or "default",
            "reference": {
                "nutrients": len(self.reference.nutrients),
                "interactions": len(self.reference.interactions),
                "crosswalk_rules": len(self.reference.crosswalk),
            },
            "store": {
                "exists": self.store.exists(),
                "path": str(self.store.path),
            },
            "brand_conflicts": {
                "exists": conflicts["exists"],
                "rows": conflicts["rows"],
                "stale": conflicts["stale"],
            },
            "cache": {"backend": self.cache.backend, "enabled": self.cache.enabled},
        }

    # -- helpers ---------------------------------------------------------
    def _clamp(self, limit: int | None, default: int | None = None) -> int:
        value = limit if limit is not None else (default or self.default_limit)
        return max(1, min(int(value), self.max_limit))

    def _load_profile(self, fdc_id: int) -> tuple[FoodRecord, list[NutrientPresence], dict[str, MeasuredAmount]]:
        payload = self.get_food(fdc_id)
        food = payload["food"]
        raw_list = food.get("ingredient_list") or ""
        record = FoodRecord(
            fdc_id=int(food["fdc_id"]),
            brand_owner=food["brand_owner"] or "",
            description=food["description"] or "",
            data_type=food["data_type"] or "",
            food_category_id=food["food_category_id"] or "",
            brand_name=food["brand_name"] or "",
            gtin_upc=food["gtin_upc"] or "",
            serving_size=float(food["serving_size"] or 0.0),
            serving_size_unit=food["serving_size_unit"] or "",
            ingredient_list=tuple(item for item in raw_list.split("|") if item),
        )
        derived = [
            NutrientPresence(
                nutrient_key=item["nutrient_key"],
                source_kind=item["source_kind"],
                phrases=tuple(item["phrases"]),
            )
            for item in payload["derived_nutrients"]
        ]
        measured = {
            item["nutrient_key"]: MeasuredAmount(
                nutrient_key=item["nutrient_key"],
                amount=float(item["amount"] or 0.0),
                unit=item["unit"] or "",
                dv_percent=float(item["dv_percent"] or 0.0),
            )
            for item in payload["measured_nutrients"]
        }
        return record, derived, measured


def _not_found_message(connection: sqlite3.Connection, fdc_id: int) -> str:
    try:
        row = connection.execute("SELECT 1 FROM food WHERE fdc_id = ?", (fdc_id,)).fetchone()
    except sqlite3.DatabaseError:
        row = None
    if row is None:
        return (f"food {fdc_id} not found. Foods without an ingredients string are excluded from the "
                "store (reason: no_ingredients_in_source), as are unknown fdc_ids")
    return f"food {fdc_id} not found"


def _parser(config: Config):
    from .ingredients import IngredientParser
    return IngredientParser(config)


def build_service(config: Config) -> NutrientConflictsService:
    return NutrientConflictsService(config)


__all__ = ["NutrientConflictsService", "NotFoundError", "ServiceError", "build_service", "store_reference_files"]
