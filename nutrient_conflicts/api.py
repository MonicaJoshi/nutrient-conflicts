# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""HTTP API (FastAPI + Pydantic v2).

The API is a thin adapter over :class:`nutrient_conflicts.services.NutrientConflictsService`.
OpenAPI documentation is generated automatically at ``/docs``. The React GUI calls
the same endpoints through the Fastify proxy (``/api/v1`` is rewritten to ``/``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Config, ConfigError
from .datasource import DatasetError
from .logsetup import configure_logging
from .services import NotFoundError, NutrientConflictsService, ServiceError

logger = logging.getLogger(__name__)


class AdhocEvaluateRequest(BaseModel):
    nutrient_keys: list[str] = Field(default_factory=list, description="Nutrient keys, e.g. ['IRON', 'VITAMIN_C']")
    fdc_ids: list[int] = Field(default_factory=list, description="Branded food fdc_ids to evaluate")


class PairRow(BaseModel):
    """One synergistic or antagonistic nutrient pair."""

    pair: str
    nutrient_a: str
    nutrient_b: str
    nutrient_a_name: str
    nutrient_b_name: str
    relation: str
    highlighted: bool = False
    strength: str
    evidence_level: str
    mechanism: str
    dose_note: str = ""
    source: str = ""
    quantity_known: bool = False
    quantity_a: str = ""
    quantity_b: str = ""
    dv_percent_a: float | None = None
    dv_percent_b: float | None = None
    amount_a: float | None = None
    amount_b: float | None = None
    unit_a: str = ""
    unit_b: str = ""
    evidences_a: list[str] = Field(default_factory=list)
    evidences_b: list[str] = Field(default_factory=list)


class SummaryCounts(BaseModel):
    synergistic: int
    antagonistic: int
    context_dependent: int
    combinations: int
    pairs_present: int = 0


class Summary(BaseModel):
    conflicting: bool
    statement: str
    counts: SummaryCounts


class Warning(BaseModel):
    required: bool
    text: str | None = None
    pairs: list[str] = Field(default_factory=list)
    quantities: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ProductResponse(BaseModel):
    """The fdc_id attribute: identity, nutrient list, tables, summary, warning."""

    brand: str
    fdc_id: int
    description: str
    food_category_id: str
    data_type: str
    brand_owner: str
    nutrient_list: list[str]
    nutrient_keys: list[str]
    nutrients: list[dict[str, Any]] = Field(default_factory=list)
    synergistic: list[PairRow] = Field(default_factory=list)
    antagonistic: list[PairRow] = Field(default_factory=list)
    context_dependent: list[PairRow] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summary: Summary
    warning: Warning
    basis: str = "per_100g"
    min_dv_percent: float | None = None
    scores: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    neutral_nutrients: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = ""


class BrandResponse(BaseModel):
    """The brand_owner attribute: per-product rows plus a brand rollup."""

    brand_owner: str
    total_products: int
    page: int
    size: int
    pages: int
    products: list[ProductResponse] = Field(default_factory=list)
    aggregate: dict[str, Any] = Field(default_factory=dict)
    summary: Summary
    warning: Warning
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = ""


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()
    configure_logging(config)
    service = NutrientConflictsService(config)
    app = FastAPI(
        title=config.get("app.name", "NutrientConflicts"),
        version=config.get("app.version", "0.0.0"),
        description=(
            "Identify which nutrients in a branded food act synergistically or antagonistically. "
            "Nutrients are derived from the USDA branded-food ingredient string and cross-referenced "
            "with a curated interaction knowledge base. Informational only, not medical advice."
        ),
    )
    app.state.service = service
    app.state.config = config

    origins = config.get_list("api.cors.origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)

    @app.get("/health", tags=["meta"], summary="Service health and reference statistics")
    def health() -> dict[str, Any]:
        return service.health()

    @app.get("/nutrients", tags=["reference"], summary="Nutrients tracked by the knowledge base")
    def nutrients() -> dict[str, Any]:
        return service.nutrients()

    @app.get("/interactions", tags=["reference"], summary="Reference interaction pairs")
    def interactions(relation: str | None = Query(default=None, description="SYNERGISTIC, ANTAGONISTIC or CONTEXT_DEPENDENT"),
                     nutrient: str | None = Query(default=None, description="Filter by nutrient key")) -> dict[str, Any]:
        return service.interactions(relation=relation, nutrient=nutrient)

    @app.get("/crosswalk", tags=["reference"], summary="Ingredient keyword to nutrient mapping")
    def crosswalk() -> dict[str, Any]:
        return {"crosswalk": service.crosswalk()}

    @app.get("/foods/search", tags=["foods"], summary="Search branded foods")
    def search_foods(brand: str | None = None, q: str | None = None, category: str | None = None,
                     limit: int | None = Query(default=None, ge=1), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        return service.search_foods(brand=brand, query=q, category=category, limit=limit, offset=offset)

    @app.get("/brands", tags=["foods"], summary="List brand owners")
    def brands(prefix: str | None = None, limit: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
        return service.brands(prefix=prefix, limit=limit)

    @app.get("/foods/{fdc_id}", tags=["foods"], summary="One food with derived and measured nutrients")
    def get_food(fdc_id: int) -> dict[str, Any]:
        return service.get_food(fdc_id)

    @app.get("/foods/{fdc_id}/ingredients", tags=["foods"],
             summary="Original ingredient string (re-read from the CSV, not stored)")
    def raw_ingredients(fdc_id: int) -> dict[str, Any]:
        return service.raw_ingredients(fdc_id)

    @app.get("/foods/{fdc_id}/evaluation", tags=["analysis"],
             summary="Synergistic and antagonistic nutrients for one food",
             response_model=ProductResponse)
    def evaluate_food(fdc_id: int,
                      basis: str = Query(default="per_100g", pattern="^(per_100g|serving)$"),
                      min_dv: float | None = Query(default=None, ge=0)) -> dict[str, Any]:
        return service.evaluate_food(fdc_id, basis=basis, min_dv_percent=min_dv)

    @app.get("/brands/{brand_owner}/evaluation", tags=["analysis"],
             summary="Per product evaluation plus a brand level rollup",
             response_model=BrandResponse)
    def evaluate_brand(brand_owner: str, page: int = Query(default=1, ge=1),
                       size: int | None = Query(default=None, ge=1),
                       basis: str = Query(default="per_100g", pattern="^(per_100g|serving)$")) -> dict[str, Any]:
        return service.brand_conflicts(brand_owner, page=page, size=size, basis=basis)

    @app.get("/brands/{brand_owner}/conflicts", tags=["analysis"],
             summary="Brand attribute: per-product rows, tables, summary and warning",
             response_model=BrandResponse)
    def brand_conflicts(brand_owner: str, page: int = Query(default=1, ge=1),
                        size: int | None = Query(default=None, ge=1),
                        basis: str = Query(default="per_100g", pattern="^(per_100g|serving)$")) -> dict[str, Any]:
        return service.brand_conflicts(brand_owner, page=page, size=size, basis=basis)

    @app.post("/evaluate", tags=["analysis"],
              summary="Ad hoc evaluation from a nutrient list or a set of fdc_ids")
    def evaluate(request: AdhocEvaluateRequest) -> dict[str, Any]:
        if request.fdc_ids:
            return {"results": [service.evaluate_food(fdc_id) for fdc_id in request.fdc_ids]}
        if request.nutrient_keys:
            return service.evaluate_adhoc(request.nutrient_keys)
        raise HTTPException(status_code=422, detail="provide nutrient_keys or fdc_ids")

    @app.get("/admin/store", tags=["admin"], summary="Store status, manifest and row counts")
    def store_status() -> dict[str, Any]:
        return service.store_status()

    @app.post("/admin/store/ingest", tags=["admin"], summary="Build or rebuild the store")
    def store_ingest(force: bool = False) -> dict[str, Any]:
        return service.ingest(force=force)

    @app.delete("/admin/store", tags=["admin"], summary="Delete the store and exports")
    def store_purge() -> dict[str, Any]:
        return service.store_purge()

    @app.get("/admin/cache", tags=["admin"], summary="Cache statistics")
    def cache_stats() -> dict[str, Any]:
        return service.cache_stats()

    @app.delete("/admin/cache", tags=["admin"], summary="Clear the memory and persisted caches")
    def cache_clear() -> dict[str, Any]:
        return service.cache_clear()

    @app.get("/admin/reference", tags=["admin"],
             summary="Reference data status: sources, persisted results, merged KB")
    def reference_status() -> dict[str, Any]:
        return service.reference_status()

    @app.post("/admin/reference/build", tags=["admin"],
              summary="Read the source documents and rebuild the reference data")
    def reference_build(source: list[str] | None = Query(default=None),
                        allow_removals: bool = False,
                        accept_source_change: bool = False) -> dict[str, Any]:
        return service.reference_build(sources=source or None,
                                       allow_removals=True if allow_removals else None,
                                       accept_source_change=accept_source_change)

    @app.delete("/admin/reference", tags=["admin"],
                summary="Delete the persisted reference results")
    def reference_clear(keep_kb: bool = False) -> dict[str, Any]:
        return service.reference_clear(keep_kb=keep_kb)

    @app.get("/admin/conflicts", tags=["admin"], summary="Brand conflict result status")
    def conflicts_status() -> dict[str, Any]:
        return service.conflicts_status()

    @app.post("/admin/conflicts/build", tags=["admin"],
              summary="Rescan every brand for nutrient conflicts")
    def conflicts_build(force: bool = False) -> dict[str, Any]:
        return service.conflicts_build(force=force)

    @app.delete("/admin/conflicts", tags=["admin"],
                summary="Delete result file 3 and its cache entries")
    def conflicts_clear() -> dict[str, Any]:
        return service.conflicts_clear()

    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _problem(404, "not_found", str(exc))

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return _problem(503, "service_unavailable", str(exc))

    @app.exception_handler(DatasetError)
    async def dataset_error(_: Request, exc: DatasetError) -> JSONResponse:
        return _problem(503, "dataset_unavailable", str(exc))

    @app.exception_handler(ConfigError)
    async def config_error(_: Request, exc: ConfigError) -> JSONResponse:
        return _problem(500, "configuration_error", str(exc))

    @app.exception_handler(FileNotFoundError)
    async def store_missing(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return _problem(503, "store_not_built", str(exc))

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return _problem(422, "invalid_request", str(exc))


def _problem(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": f"https://NutrientConflicts.local/problems/{code}", "title": code.replace("_", " "),
                 "status": status, "detail": detail, "code": code},
        media_type="application/problem+json",
    )


__all__ = ["create_app"]
