# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Command line interface (click).

Every command delegates to :class:`nutrient_conflicts.services.NutrientConflictsService`, the same
layer the HTTP API and the GUI use.
"""

from __future__ import annotations

import json
import logging
import textwrap
from typing import Any

import click

from .config import Config, ConfigError
from .logsetup import configure_logging
from .services import NotFoundError, NutrientConflictsService, ServiceError


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--profile", default=None, help="Configuration profile (application-<profile>.properties).")
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, profile: str | None, verbose: bool) -> None:
    """NutrientConflicts - synergistic and antagonistic nutrients in branded foods.

    \b
    First run:  nutrient-conflicts ingest
    Then:       nutrient-conflicts evaluate --fdc-id 12345
                nutrient-conflicts evaluate --brand-owner "CAMPBELL SOUP COMPANY"
    """
    try:
        config = Config.load(profile=profile)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if verbose:
        config = config.with_overrides({"log.level": "DEBUG"})
    configure_logging(config)
    ctx.obj = {"config": config, "service": NutrientConflictsService(config), "log": logging.getLogger("nutrient_conflicts")}


def _service(ctx: click.Context) -> NutrientConflictsService:
    return ctx.obj["service"]


def _emit(payload: Any, as_json: bool) -> None:
    """Print a payload as JSON, or as a flat key/value listing."""
    if as_json:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    click.echo(_flatten(payload))


def _flatten(payload: Any, prefix: str = "") -> str:
    lines: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            label = f"{prefix}{key}"
            if isinstance(value, dict):
                lines.append(_flatten(value, f"{label}."))
            elif isinstance(value, list):
                lines.append(f"{label}: {len(value)} item(s)")
            else:
                lines.append(f"{label}: {value}")
    else:
        lines.append(str(payload))
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------- commands
@main.command("ingest")
@click.option("--force", is_flag=True, help="Rebuild even when the store is current.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a summary table.")
@click.pass_context
def ingest_cmd(ctx: click.Context, force: bool, as_json: bool) -> None:
    """Build the persisted store and exports from the USDA CSV release."""
    service = _service(ctx)
    try:
        summary = service.ingest(force=force)
    except Exception as exc:  # dataset problems surface here
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(summary, True)
        return
    click.echo(f"store built at {summary.get('built_at', '-')}")
    click.echo(f"  foods                : {summary.get('foods', 0):,}")
    click.echo(f"  derived nutrient rows: {summary.get('derived_nutrients', 0):,}")
    click.echo(f"  measured quantities  : {summary.get('measured_nutrients', 0):,}")
    click.echo(f"  excluded (no ingredients): {summary.get('excluded_without_ingredients', 0):,}")
    click.echo(f"  elapsed              : {summary.get('elapsed_seconds', 0)}s")
    exports = summary.get("exports", {})
    for name, path in exports.items():
        click.echo(f"  {name:<20} : {path}")


@main.command("status")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def status_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show store, reference data, brand conflict results and cache status."""
    service = _service(ctx)
    payload = {
        "store": service.store_status(),
        "reference": service.reference_status(),
        "conflicts": service.conflicts_status(),
        "cache": service.cache_stats(),
    }
    if as_json:
        _emit(payload, True)
        return
    store = payload["store"]
    click.echo(f"store : {store['path']}")
    click.echo(f"exists: {store['exists']}   stale: {store['stale']}")
    for reason in store["reasons"]:
        click.echo(f"  - {reason}")
    for key, value in store["counts"].items():
        click.echo(f"  {key:<18}: {value:,}")
    reference = payload["reference"]
    click.echo(f"reference : merged {reference['merged']['pairs']} pair(s)   "
               f"stale: {reference['stale']}")
    for reason in reference["reasons"]:
        click.echo(f"  - {reason}")
    conflicts = payload["conflicts"]
    click.echo(f"conflicts : {conflicts['rows']:,} row(s) across {conflicts['brands']:,} brand(s)   "
               f"stale: {conflicts['stale']}")
    for reason in conflicts["reasons"]:
        click.echo(f"  - {reason}")
    cache = payload["cache"]
    click.echo(f"cache : {cache['backend']} ({cache['l1_entries']} memory / {cache['l2_entries']} persisted)")


@main.command("search")
@click.option("--brand", help="Exact brand owner (case insensitive).")
@click.option("--query", "-q", help="Free text over description, brand name and UPC.")
@click.option("--category", help="Branded food category.")
@click.option("--limit", type=int, default=None)
@click.option("--offset", type=int, default=0)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def search_cmd(ctx: click.Context, brand: str | None, query: str | None, category: str | None,
               limit: int | None, offset: int, as_json: bool) -> None:
    """Search foods by brand, text or category."""
    payload = _service(ctx).search_foods(brand=brand, query=query, category=category,
                                         limit=limit, offset=offset)
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"{payload['total']:,} match(es); showing {len(payload['items'])} from offset {offset}")
    rows = [[str(item["fdc_id"]), item["brand_owner"] or "-", (item["description"] or "")[:60]]
            for item in payload["items"]]
    click.echo(_table(["fdc_id", "brand_owner", "description"], rows))


@main.command("brands")
@click.option("--prefix", help="Filter brands containing this text.")
@click.option("--limit", type=int, default=50)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def brands_cmd(ctx: click.Context, prefix: str | None, limit: int, as_json: bool) -> None:
    """List brands with the number of products in the store."""
    payload = _service(ctx).brands(prefix=prefix, limit=limit)
    if as_json:
        _emit(payload, True)
        return
    rows = [[item["brand_owner"] or "-", str(item["foods"])] for item in payload["items"]]
    click.echo(_table(["brand_owner", "foods"], rows))


@main.command("show")
@click.argument("fdc_id", type=int)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def show_cmd(ctx: click.Context, fdc_id: int, as_json: bool) -> None:
    """Show one food, its derived nutrients and measured quantities."""
    try:
        payload = _service(ctx).get_food(fdc_id)
    except (NotFoundError, ServiceError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(payload, True)
        return
    food = payload["food"]
    click.echo(f"{food['fdc_id']}  {food['brand_owner']} - {food['description']}")
    click.echo(f"category: {food['food_category_id']}   data_type: {food['data_type']}")
    measured = {item["nutrient_key"]: item for item in payload["measured_nutrients"]}
    rows = []
    for item in payload["derived_nutrients"]:
        quantity = measured.get(item["nutrient_key"], {})
        rows.append([item["nutrient_key"], item["source_kind"],
                     "-" if quantity.get("dv_percent") is None else f"{quantity['dv_percent']:.1f}",
                     ", ".join(item["phrases"])[:70]])
    click.echo(_table(["nutrient", "source", "%DV", "ingredient evidence"], rows))


@main.command("evaluate")
@click.option("--fdc-id", type=int, help="Evaluate one branded food.")
@click.option("--brand-owner", "--brand", "brand_owner",
              help="Evaluate the products of a brand owner (case insensitive).")
@click.option("--nutrients", help="Comma separated nutrient keys for an ad hoc evaluation.")
@click.option("--basis", type=click.Choice(["per_100g", "serving"]), default="per_100g")
@click.option("--min-dv", type=float, default=None, help="Override analysis.min.dv.percent.")
@click.option("--page", type=int, default=1, help="Page of products for --brand-owner.")
@click.option("--size", "--limit", "size", type=int, default=25,
              help="Products per page for --brand-owner.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def evaluate_cmd(ctx: click.Context, fdc_id: int | None, brand_owner: str | None,
                 nutrients: str | None, basis: str, min_dv: float | None, page: int,
                 size: int, as_json: bool) -> None:
    """Report synergistic and antagonistic nutrient pairs for a food or brand."""
    service = _service(ctx)
    if not any([fdc_id, brand_owner, nutrients]):
        raise click.UsageError("provide --fdc-id, --brand-owner or --nutrients")
    try:
        if nutrients:
            payload = service.evaluate_adhoc(nutrients.split(","))
        elif brand_owner:
            payload = service.brand_conflicts(brand_owner, page=page, size=size, basis=basis)
        else:
            payload = service.evaluate_food(int(fdc_id), basis=basis, min_dv_percent=min_dv)
    except (NotFoundError, ServiceError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(payload, True)
        return
    if brand_owner:
        click.echo(_render_brand(payload))
    elif nutrients:
        click.echo(_render_evaluation(payload))
    else:
        click.echo(_render_product(payload))


@main.command("interactions")
@click.option("--relation", type=click.Choice(["SYNERGISTIC", "ANTAGONISTIC", "CONTEXT_DEPENDENT"]))
@click.option("--nutrient", help="Only pairs involving this nutrient key.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def interactions_cmd(ctx: click.Context, relation: str | None, nutrient: str | None, as_json: bool) -> None:
    """List the reference nutrient interaction pairs."""
    payload = _service(ctx).interactions(relation=relation, nutrient=nutrient)
    if as_json:
        _emit(payload, True)
        return
    rows = [[item["nutrient_a"], item["nutrient_b"], item["relation"], item["strength"],
             item["evidence_level"]] for item in payload["interactions"]]
    click.echo(f"{payload['count']} pair(s)")
    click.echo(_table(["nutrient_a", "nutrient_b", "relation", "strength", "evidence"], rows))


@main.command("nutrients")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def nutrients_cmd(ctx: click.Context, as_json: bool) -> None:
    """List the nutrients tracked by the reference data."""
    payload = _service(ctx).nutrients()
    if as_json:
        _emit(payload, True)
        return
    rows = [[item["nutrient_key"], item["name"], item["category"], f"{item['dv_amount']:g} {item['dv_unit']}"]
            for item in payload["nutrients"]]
    click.echo(_table(["key", "name", "category", "daily value"], rows))


@main.group("reference")
def reference_group() -> None:
    """Reference data 1 and 2: build, inspect or clear the documents-derived data."""


@reference_group.command("build")
@click.option("--source", "sources", multiple=True,
              type=click.Choice(["all", "vitamin-mineral", "biological"]),
              help="Which source document to read (default: all).")
@click.option("--allow-removals", is_flag=True,
              help="Accept losing a pair that the knowledge base currently contains.")
@click.option("--accept-source-change", is_flag=True,
              help="Accept a changed matrix image and refresh its fingerprint.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def reference_build_cmd(ctx: click.Context, sources: tuple[str, ...], allow_removals: bool,
                        accept_source_change: bool, as_json: bool) -> None:
    """Read the PDF chart and the matrix image, persist both results and merge the KB."""
    service = _service(ctx)
    try:
        summary = service.reference_build(
            sources=list(sources) or None,
            allow_removals=True if allow_removals else None,
            accept_source_change=accept_source_change,
        )
    except ServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(summary, True)
        return
    click.echo(f"reference data built at {summary['built_at']} ({summary['elapsed_seconds']}s)")
    for name, path in summary["outputs"].items():
        click.echo(f"  {name:<14}: {path}")
    merged = summary["merged"]
    relations = ", ".join(f"{key} {value}" for key, value in sorted(merged["relations"].items()))
    click.echo(f"  merged KB     : {merged['pairs']} pair(s) ({relations})")
    for warning in summary["warnings"]:
        click.echo(f"  note: {warning}")


@reference_group.command("status")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def reference_status_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show whether the persisted reference data still matches its sources."""
    payload = _service(ctx).reference_status()
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"merged KB : {payload['merged']['path']}")
    click.echo(f"exists    : {payload['merged']['exists']}   pairs: {payload['merged']['pairs']}"
               f"   stale: {payload['stale']}")
    for reason in payload["reasons"]:
        click.echo(f"  - {reason}")
    for name, item in payload["sources"].items():
        click.echo(f"  source {name:<16}: exists {item['exists']}   "
                   f"matches manifest {item['matches_manifest']}")
    for name, item in payload["outputs"].items():
        click.echo(f"  output {name:<16}: {item['path']} ({'present' if item['exists'] else 'missing'})")


@reference_group.command("crosscheck")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def reference_crosscheck_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show the generated knowledge base against the curated seed file."""
    try:
        payload = _service(ctx).reference_crosscheck()
    except ServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"seed     : {payload['seed_file']} ({payload['seed_pairs']} pairs)")
    click.echo(f"generated: {payload['merged_pairs']} pairs")
    for key in ("added", "removed", "relation_changed", "metadata_changed"):
        items = payload.get(key) or []
        click.echo(f"  {key:<18}: {len(items)}")
        for item in items:
            click.echo(f"    - {item}")


@reference_group.command("clear")
@click.option("--keep-kb", is_flag=True,
              help="Keep the merged knowledge base; remove only the derived results.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def reference_clear_cmd(ctx: click.Context, keep_kb: bool, as_json: bool) -> None:
    """Delete the persisted reference results (and their cache entries)."""
    payload = _service(ctx).reference_clear(keep_kb=keep_kb)
    if as_json:
        _emit(payload, True)
        return
    for path in payload["removed"]:
        click.echo(f"removed {path}")
    if not payload["removed"]:
        click.echo("nothing to remove")
    click.echo(f"cleared {payload['cache_cleared']} cache entr(ies)")


@main.group("conflicts")
def conflicts_group() -> None:
    """Result file 3: nutrient conflicts for every brand, and the brand attribute."""


@conflicts_group.command("build")
@click.option("--force", is_flag=True, help="Rescan even when the results are current.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def conflicts_build_cmd(ctx: click.Context, force: bool, as_json: bool) -> None:
    """Scan every brand for synergistic and antagonistic nutrient combinations."""
    try:
        summary = _service(ctx).conflicts_build(force=force)
    except ServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(summary, True)
        return
    if summary.get("skipped"):
        click.echo(f"conflict results are current ({summary['rows']:,} rows); "
                   "use --force to rescan")
        return
    click.echo(f"brand conflict scan built at {summary['built_at']} ({summary['elapsed_seconds']}s)")
    click.echo(f"  brands             : {summary['brands']:,}")
    click.echo(f"  brands with pairs  : {summary['brands_with_conflicts']:,}")
    click.echo(f"  rows               : {summary['rows']:,}")
    for relation, count in sorted(summary["relations"].items()):
        click.echo(f"  {relation:<19}: {count:,}")
    for name, path in summary["outputs"].items():
        click.echo(f"  {name:<19}: {path}")


@conflicts_group.command("show")
@click.option("--brand", required=True, help="Brand owner, case insensitive.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def conflicts_show_cmd(ctx: click.Context, brand: str, as_json: bool) -> None:
    """The brand attribute: nutrients, synergistic/antagonistic table and warning."""
    try:
        payload = _service(ctx).brand_conflicts(brand)
    except (NotFoundError, ServiceError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        _emit(payload, True)
        return
    click.echo(_render_brand_conflicts(payload))


@conflicts_group.command("status")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def conflicts_status_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show whether result file 3 is current."""
    payload = _service(ctx).conflicts_status()
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"results : {payload['paths']['csv']}")
    click.echo(f"exists  : {payload['exists']}   rows: {payload['rows']:,}   "
               f"brands: {payload['brands']:,}   stale: {payload['stale']}")
    for reason in payload["reasons"]:
        click.echo(f"  - {reason}")
    manifest = payload.get("manifest") or {}
    if manifest:
        click.echo(f"built at: {manifest.get('built_at', '-')}")


@conflicts_group.command("clear")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def conflicts_clear_cmd(ctx: click.Context, as_json: bool) -> None:
    """Delete result file 3 and its cache entries."""
    payload = _service(ctx).conflicts_clear()
    if as_json:
        _emit(payload, True)
        return
    for path in payload["removed"]:
        click.echo(f"removed {path}")
    if not payload["removed"]:
        click.echo("nothing to remove")
    click.echo(f"cleared {payload['cache_cleared']} cache entr(ies)")


@main.group("cache")
def cache_group() -> None:
    """Inspect or clear the result cache (L1 memory + L2 persisted)."""


@cache_group.command("stats")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def cache_stats(ctx: click.Context, as_json: bool) -> None:
    payload = _service(ctx).cache_stats()
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"backend      : {payload['backend']}")
    click.echo(f"directory    : {payload['directory']}")
    click.echo(f"memory       : {payload['l1_entries']} / {payload['l1_max_entries']}")
    click.echo(f"persisted    : {payload['l2_entries']}")
    click.echo(f"hits         : l1={payload['l1_hits']} l2={payload['l2_hits']} misses={payload['misses']}")


@cache_group.command("clear")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def cache_clear(ctx: click.Context, as_json: bool) -> None:
    payload = _service(ctx).cache_clear()
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"cleared {payload['cleared']} cache entr(ies)")


@main.group("store")
def store_group() -> None:
    """Inspect, purge or export the persisted store."""


@store_group.command("status")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def store_status(ctx: click.Context, as_json: bool) -> None:
    payload = _service(ctx).store_status()
    if as_json:
        _emit(payload, True)
        return
    click.echo(f"path  : {payload['path']}")
    click.echo(f"exists: {payload['exists']}   stale: {payload['stale']}")
    for reason in payload["reasons"]:
        click.echo(f"  - {reason}")
    for key, value in payload["counts"].items():
        click.echo(f"  {key:<18}: {value:,}")


@store_group.command("purge")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def store_purge(ctx: click.Context, as_json: bool) -> None:
    payload = _service(ctx).store_purge()
    if as_json:
        _emit(payload, True)
        return
    if payload["removed"]:
        for path in payload["removed"]:
            click.echo(f"removed {path}")
    else:
        click.echo("nothing to remove")


@store_group.command("export")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def store_export(ctx: click.Context, as_json: bool) -> None:
    payload = _service(ctx).export()
    if as_json:
        _emit(payload, True)
        return
    for name, value in payload.items():
        click.echo(f"{name}: {value}")


@main.command("serve")
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
@click.option("--reload", is_flag=True, help="Auto-reload on code changes (development).")
@click.pass_context
def serve_cmd(ctx: click.Context, host: str | None, port: int | None, reload: bool) -> None:
    """Run the FastAPI application (OpenAPI documentation at /docs)."""
    import uvicorn

    from .api import create_app

    config = ctx.obj["config"]
    uvicorn.run(create_app(config), host=host or config.get("api.host", "127.0.0.1"),
                port=port or config.get_int("api.port", 8077), reload=reload,
                timeout_keep_alive=config.get_int("api.request.timeout.seconds", 30))


@main.command("gui")
@click.option("--rebuild", is_flag=True, help="Force a fresh React production build.")
@click.pass_context
def gui_cmd(ctx: click.Context, rebuild: bool) -> None:
    """Build and serve the React GUI through the Fastify proxy (one command)."""
    from .gui_launcher import GuiError, run_gui

    try:
        raise SystemExit(run_gui(ctx.obj["config"], force_build=rebuild))
    except GuiError as exc:
        raise click.ClickException(str(exc)) from exc


# --------------------------------------------------------------------------- rendering
def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines)


def _render_evaluation(payload: dict) -> str:
    food = payload["food"]
    scores = payload["scores"]
    counts = payload.get("counts", {})
    lines = [
        f"{food['fdc_id']}  {food['brand_owner']} - {food['description']}",
        f"category: {food['food_category_id'] or '-'}",
        f"basis: {payload['basis']}   threshold: {payload['min_dv_percent']:g}% DV   "
        f"classification: {scores['classification']}",
        f"synergy {scores['synergy']:.3f} - antagonism {scores['antagonism']:.3f} = net {scores['net']:.3f}",
        f"nutrients present {counts.get('nutrients_present', 0)}, pairs active {counts.get('pairs_active', 0)}",
        "",
    ]
    for label, relation in (("SYNERGISTIC", "SYNERGISTIC"), ("ANTAGONISTIC", "ANTAGONISTIC"),
                            ("CONTEXT DEPENDENT", "CONTEXT_DEPENDENT")):
        rows = [item for item in payload["findings"] if item["relation"] == relation]
        if not rows:
            continue
        lines.append(f"{label}:")
        for item in rows:
            flag = "active" if item["active"] else "dormant"
            dv = _dv_text(item)
            lines.append(f"  {item['nutrient_a']} ~ {item['nutrient_b']}  [{flag}] "
                         f"{item['strength']}/{item['evidence_level']}  {dv}")
            lines.append(f"      {item['mechanism']}")
            if item["evidences_a"] or item["evidences_b"]:
                lines.append(f"      evidence: {', '.join(item['evidences_a'] + item['evidences_b'])[:100]}")
        lines.append("")
    if payload.get("competing_influences"):
        lines.append("COMPETING INFLUENCES:")
        for nutrient_key, items in payload["competing_influences"].items():
            detail = ", ".join(f"{item['relation'].lower()} with {item['with']}" for item in items)
            lines.append(f"  {nutrient_key}: {detail}")
        lines.append("")
    if payload.get("neutral_nutrients"):
        lines.append(f"NEUTRAL NUTRIENTS: {', '.join(payload['neutral_nutrients'])}")
    for note in payload.get("notes", []):
        lines.append(f"note: {note}")
    if payload.get("disclaimer"):
        lines.append("")
        lines.append(payload["disclaimer"])
    return "\n".join(lines)


def _dv_text(item: dict) -> str:
    parts = []
    for key in ("a", "b"):
        value = item[f"dv_percent_{key}"]
        parts.append("n/a" if value is None else f"{value:.1f}% DV")
    return f"({parts[0]} / {parts[1]})"


def _render_brand(payload: dict) -> str:
    summary = payload.get("summary", {})
    lines = [
        f"{payload['brand_owner']}  ({payload['total_products']:,} product(s); "
        f"page {payload.get('page', 1)} of {payload.get('pages', 1)})",
        "",
        summary.get("statement", ""),
        "",
        "PRODUCTS",
    ]
    for product in payload.get("products", []):
        lines.append("")
        lines.append(_render_product(product))
    warning = payload.get("warning") or {}
    if warning.get("required"):
        lines += ["", "BRAND WARNING", f"  {warning.get('text', '')}"]
    if payload.get("aggregate", {}).get("interactions"):
        lines += ["", "BRAND ROLLUP (persisted scan across the product line)",
                  _table(["relation", "pair", "products with both", "%DV a", "%DV b"],
                         [[item["relation"], item["pair"], str(item["co_occurring_products"]),
                           "-" if item["dv_percent_a"] is None else f"{item['dv_percent_a']:.1f}",
                           "-" if item["dv_percent_b"] is None else f"{item['dv_percent_b']:.1f}"]
                          for item in payload["aggregate"]["interactions"]])]
    return "\n".join(lines)


def _render_product(payload: dict) -> str:
    summary = payload.get("summary", {})
    lines = [
        f"{payload.get('brand', '')} - {payload.get('description', '')}",
        f"fdc_id: {payload.get('fdc_id')}   brand_owner: {payload.get('brand_owner', '')}",
        f"category: {payload.get('food_category_id', '')}   data_type: {payload.get('data_type', '')}",
        f"basis: {payload.get('basis', 'per_100g')}   threshold: {payload.get('min_dv_percent', '')}% DV"
        f"   classification: {payload.get('scores', {}).get('classification', 'neutral')}",
        "",
        "NUTRIENT LIST: " + (", ".join(payload.get("nutrient_list", [])) or "(none)"),
        "",
    ]
    for title, key in (("SYNERGISTIC", "synergistic"), ("ANTAGONISTIC", "antagonistic")):
        rows = payload.get(key, [])
        lines.append(f"{title}: {'none' if not rows else ''}")
        for item in rows:
            flag = "!!" if item.get("highlighted") else "  "
            lines.append(f"  {flag} {item['pair']}  [{item['strength']}/{item['evidence_level']}]")
            lines.append(f"     {item['quantity_a']} | {item['quantity_b']}")
            lines.append(f"     {item['mechanism']}")
        lines.append("")
    lines.append("SUMMARY")
    lines.append(f"  {summary.get('statement', '')}")
    warning = payload.get("warning") or {}
    if warning.get("required"):
        lines += ["", "WARNING"]
        for line in textwrap.wrap(warning.get("text", ""), width=110):
            lines.append(f"  {line}")
        if warning.get("quantities"):
            lines.append("  Quantities involved:")
            for quantity in warning["quantities"]:
                lines.append(f"    - {quantity}")
        if warning.get("next_steps"):
            lines.append("  Suggested next steps:")
            for step in warning["next_steps"]:
                lines.append(f"    - {step}")
    for note in payload.get("notes", []):
        lines.append(f"note: {note}")
    if payload.get("disclaimer"):
        lines += ["", payload["disclaimer"]]
    return "\n".join(lines)


def _render_brand_conflicts(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        f"{payload['brand_owner']}   ({payload['total_products']:,} product(s))",
        "",
        "NUTRIENTS",
    ]
    nutrient_rows = [
        [item["nutrient_key"], item["name"], str(item["products"]),
         "-" if item["max_dv_percent"] is None else f"{item['max_dv_percent']:.1f}"]
        for item in payload["nutrients"]
    ]
    if nutrient_rows:
        lines.append(_table(["key", "nutrient", "products", "max %DV"], nutrient_rows))
    else:
        lines.append("  (no nutrient combination from the reference data is present)")

    lines += ["", "SYNERGISTIC / ANTAGONISTIC COMBINATIONS"]
    table_rows = []
    for item in payload["interactions"]:
        dv_a = "-" if item["dv_percent_a"] is None else f"{item['dv_percent_a']:.1f}"
        dv_b = "-" if item["dv_percent_b"] is None else f"{item['dv_percent_b']:.1f}"
        table_rows.append([
            "!!" if item["highlighted"] else "  ",
            item["relation"],
            f"{item['nutrient_a']} + {item['nutrient_b']}",
            str(item["co_occurring_products"]),
            dv_a, dv_b,
            f"{item['strength']}/{item['evidence_level']}",
        ])
    if table_rows:
        lines.append(_table(["", "relation", "pair", "in 1 product", "%DV a", "%DV b", "evidence"],
                            table_rows))
        lines.append("  (!! marks an antagonistic combination)")
    else:
        lines.append("  none")

    lines += ["", "SUMMARY", f"  {summary['statement']}"]
    warning = payload.get("warning") or {}
    if warning.get("required"):
        lines.append("")
        lines.append("WARNING")
        for line in textwrap.wrap(warning["text"], width=100):
            lines.append(f"  {line}")
        if warning.get("quantities"):
            lines.append("  Quantities involved (highest measured %DV per 100 g):")
            for quantity in warning["quantities"]:
                for index, line in enumerate(textwrap.wrap(quantity, width=96)):
                    lines.append(("    - " if index == 0 else "      ") + line)
        if warning.get("next_steps"):
            lines.append("  Suggested next steps:")
            for step in warning["next_steps"]:
                lines.append(f"    - {step}")
    lines.append("")
    for note in payload.get("notes", []):
        lines.append(f"note: {note}")
    if payload.get("disclaimer"):
        lines += ["", payload["disclaimer"]]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    main()
