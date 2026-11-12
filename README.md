NOTE:

The food and nutrient data used in this project are publicly available and were downloaded from FoodData Central https://fdc.nal.usda.gov/download-datasets#bkmk-3.

The reference data indicating nutrient conflicts were downloaded from:
1.  https://www.google.com/search?q=conflicting+nutrients+in+a+brand&rlz=1C1CHBF_enCA855CA855&oq=conflicting+nutrients+in+a+brand&gs_lcrp=EgZjaHJvbWUyBggAEEUYOTIHCAEQIRigATIHCAIQIRiPAjIHCAMQIRiPAtIBCTE1NTUzajBqN6gCALACAA&sourceid=chrome&source=chrome.ob&ie=UTF-8&mstk=AUtExfCx0RLGe2davsLRcv6OjJlwE0oRcUUA6Wkkc6cJ2n2Xqr8LqEqZZNiN6ems77-uY3DsLWQm6oDtgJoe7rJfQGj7GDn-VLB57Vpxxq9MTjPgikE0PaQDns9vXzzRQn4v1ASpUHsocsEkxYeKKZg3Hmy7Q1JjiOYS8ynWwBRamL1mbMbXbJ9XpheCVkzahKehm-g4wNpgka3sC4C2gnMkeeSIU8pLUtOUh57rN15lCs8O48ZihGReTKIDbA&csuir=1&udm=50&aioh=3&mtid=biT0aujHKLCZhvcP0L_EyQc#aof=1

2.  https://deannaminich.com/wp-content/uploads/2018/10/MET2557-Vitamin-Mineral-Interactions-Chart_IPAD.pdf

The project was built for very limited reference data; Can be extended to wider reference data sources when available.


# Nutrient Conflicts Evaluation

Identifies which constituent nutrients of a branded food are **synergistic** or
**antagonistic** for the human body, using a pre-downloaded USDA FoodData Central
branded-food release and reference charts. Nutrients that appear in neither
category are reported as neutral.

The same analysis code is exposed through three interfaces:

| Interface | Technology | Entry point |
|---|---|---|
| CLI | Python, click | `uv run nutrient-conflicts ...` |
| API | FastAPI + Pydantic v2 (OpenAPI at `/docs`) | `uv run nutrient-conflicts serve` |
| GUI | React (Vite) served by Fastify, proxying `/api/v1` | `uv run nutrient-conflicts gui` |

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+ and npm (GUI only)
- The USDA CSV release and the reference folder, already downloaded. Their paths
  are configured in `application.properties`; the folders are treated as
  **read only**.
- No database server is required. Redis is optional and disabled by default.

The default paths point to:

```
/projects/ds/nutrient-conflicts/brandData
/projects/ds/nutrient-conflicts/referenceData
```

Change `dataset.root` and `reference.source.dir` if your data lives elsewhere.

## Install

```bash
uv venv
uv pip install -e ".[test]"
```

The program is also runnable without installing:

```bash
uv run python -m nutrient_conflicts.cli --help
```

## First run

The first command of any interface builds the persisted store from the CSV
release. This is a one-time cost of roughly 10–20 minutes and 2–3 GB of disk:

```bash
uv run nutrient-conflicts ingest
```

It writes:

```
data/store/nutrientconflicts.sqlite     query store (foods, derived and measured nutrients)
data/store/branded_foods.jsonl.gz       py-readable export, one JSON object per fdc_id
                                        (nutrient_list = derived ingredient words,
                                         nutrient_keys = mapped canonical nutrients)
data/reference/...                      copied inputs, parsed PDF/matrix outputs, merged KB
data/results/...                        brand level conflict scan
data/cache/...                          persisted query cache
```

The external source folders are never modified. On every later run the program
first checks the cache, then the persisted files, and only then rebuilds.

### Ingredient parsing rules

For each `branded_food.csv` row, `fdc_id`, `brand_owner`, `brand_name` and
`ingredients` are read, then:

1. boiler-plate prefixes are removed: `INGREDIENTS:`, `MADE FROM:`,
   `CONTAINS LESS THAN 2% OF:`, `CONCENTRATED JUICES OF` (and configurable
   variants such as `CONTAINS 2% OR LESS OF:`);
2. the text is split on `,`, `;` and `&`;
3. a parenthesised or bracketed sub-list is expanded and the parent item is
   dropped (`MIREPOIX (CARROTS, CELERY, ONIONS)` → carrots, celery, onions);
4. percentage-only groups and qualifier phrases (`FROM CONCENTRATE`,
   `TO PROTECT`, `PROCESSING AID`, functional labels such as `PRESERVATIVES`)
   are discarded;
5. duplicates are removed while preserving order.

The resulting list is persisted as `nutrient_list`, and also mapped to canonical
nutrient keys through `ingredient_nutrient_map.csv` before pair matching.

## Configuration

All tunables are in `application.properties`; no runtime values are hard-coded.

| Key | Meaning |
|---|---|
| `dataset.root` | USDA CSV release folder (read only) |
| `dataset.ingest.include_measured` | also read `food_nutrient.csv` for quantities |
| `reference.source.dir` | reference PDF/image/CSV folder (read only) |
| `reference.dir` | writable work area under `data/reference` |
| `reference.file.*` | reference CSV names |
| `reference.crosscheck.file` | generated-vs-curated diff report |
| `store.dir`, `store.db.file`, `store.jsonl.file` | persisted store |
| `store.auto.build` | build automatically on first query |
| `store.warm.entries` | rows loaded from JSONL into the cache at startup |
| `cache.backend` | `memory`, `disk` or `redis` |
| `cache.l1.max_entries`, `cache.ttl.seconds` | memory cache limits |
| `cache.redis.*` | Redis connection (forward-looking, disabled) |
| `parse.*` | ingredient parsing rules and qualifier list |
| `analysis.min.dv.percent` | dose gate: both nutrients must reach this %DV |
| `analysis.quantity.unknown.activates` | treat presence as active when no amount exists |
| `conflicts.*` | brand scan, templates and result file names |
| `api.host`, `api.port`, `api.prefix` | HTTP API |
| `gui.host`, `gui.port`, `gui.api.base.url` | Fastify GUI |

Values may reference other keys or environment variables as `${NAME:default}`.

## CLI

```bash
uv run nutrient-conflicts --help

# first run
uv run nutrient-conflicts ingest

# reference data 1 (PDF) and 2 (image), merged into the knowledge base
uv run nutrient-conflicts reference build
uv run nutrient-conflicts reference crosscheck
uv run nutrient-conflicts reference status

# a single product
uv run nutrient-conflicts evaluate --fdc-id 1105905
uv run nutrient-conflicts evaluate --fdc-id 1105905 --json

# every product of a brand owner, paginated
uv run nutrient-conflicts evaluate --brand-owner "CAMPBELL SOUP COMPANY" --page 1 --size 10

# search and inspect
uv run nutrient-conflicts search --query "SWANSON BROTH"
uv run nutrient-conflicts show 1105905
uv run nutrient-conflicts brands --prefix CAMPBELL

# cache and store administration
uv run nutrient-conflicts cache stats
uv run nutrient-conflicts cache clear
uv run nutrient-conflicts store purge
uv run nutrient-conflicts status
```

Every command supports `-h` / `--help` and `--json`.

## API

```bash
uv run nutrient-conflicts serve
# http://127.0.0.1:8077/docs
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | health and reference/store statistics |
| GET | `/foods/search?q=&brand=&category=` | search branded foods |
| GET | `/foods/{fdc_id}` | identity, derived and measured nutrients |
| GET | `/foods/{fdc_id}/evaluation` | full product response (below) |
| GET | `/brands?prefix=` | brand owners and product counts |
| GET | `/brands/{brand_owner}/conflicts?page=&size=` | per-product rows + brand rollup |
| GET | `/interactions`, `/nutrients`, `/crosswalk` | reference data |
| GET | `/admin/store`, `/admin/cache`, `/admin/reference`, `/admin/conflicts` | status |
| POST/DELETE | `/admin/*` | rebuild or clear persisted data |

### Response shape

`brand` is the product brand name (`branded_food.brand_name`, falling back to the
owner); `brand_owner` is the manufacturer/brand owner. `nutrient_list` is the
derived ingredient list in order; `nutrient_keys` is the canonical nutrient
mapping used for pair matching.

`GET /foods/{fdc_id}/evaluation`:

```json
{
  "brand": "SWANSON",
  "fdc_id": 1105905,
  "description": "SWANSON BROTH BEEF",
  "food_category_id": "Herbs/Spices/Extracts",
  "data_type": "branded_food",
  "brand_owner": "CAMPBELL SOUP COMPANY",
  "nutrient_list": ["BEEF STOCK", "CARROTS", "CELERY", "ONIONS", "SALT"],
  "nutrient_keys": ["CALCIUM", "IRON", "SODIUM"],
  "synergistic": [
    {"pair": "CALCIUM + VITAMIN_K", "strength": "moderate", "evidence_level": "chart",
     "quantity_a": "12 MG (0.9% DV)", "quantity_b": "quantity not published", "...": "..."}
  ],
  "antagonistic": [
    {"pair": "CALCIUM + IRON", "highlighted": true, "strength": "strong",
     "quantity_a": "12 MG (0.9% DV)", "quantity_b": "0.8 MG (4.4% DV)", "...": "..."}
  ],
  "summary": {
    "conflicting": true,
    "statement": "1 antagonistic and 0 synergistic nutrient combination(s) identified ...",
    "counts": {"antagonistic": 1, "synergistic": 0, "context_dependent": 0}
  },
  "warning": {
    "required": true,
    "text": "... Check these results with a registered dietitian ...",
    "pairs": ["CALCIUM + IRON"],
    "quantities": ["CALCIUM + IRON: CALCIUM 12 MG (0.9% DV), IRON 0.8 MG (4.4% DV)"],
    "next_steps": ["Review the flagged pairs with a registered dietitian", "..."]
  }
}
```

`GET /brands/{brand_owner}/conflicts` returns `products[]` (the same shape,
paginated) plus `aggregate` (pairs recurring across the product line, product
counts and highest measured %DV) and a brand level `summary`/`warning`.

The first brand query also builds the brand level rollup scan over the whole
store (`data/results/Brand-Nutrient-Conflicts.csv`); later queries read the
cached scan. It is a one-time cost after an ingest or reference rebuild.

Antagonistic combinations are always highlighted (`"highlighted": true` and the
red row in the GUI). Context-dependent combinations are reported separately and
are **not** counted as synergistic or antagonistic.

## GUI

```bash
uv run nutrient-conflicts gui          # builds the React app if needed, starts API + Fastify
uv run nutrient-conflicts gui --rebuild
```

Open `http://127.0.0.1:5173`. Fastify serves `gui/dist` and proxies `/api/v1/*`
to the FastAPI port, so the browser uses one origin. For front-end development:

```bash
cd gui && npm install && npm run dev
```

## Testing and coverage

```bash
make test          # unittest + coverage, fails below 80%
make coverage      # same, then writes htmlcov/index.html
# Windows without make:
uv run coverage run --source=nutrient_conflicts -m unittest discover -s tests -t tests
uv run coverage report
```

The suite covers reference parsing, the ingredient rules, ingest and JSONL
export, the analyser, the service contract, the CLI, the API and the brand scan.
It uses a four-file fixture dataset; no large dataset or network access is
required.

## Caching and data lifecycle

1. First run: the store and reference data are built and persisted; query
   results are cached in memory and on disk.
2. Later runs: memory cache → disk cache → SQLite/JSONL. Missing values are
   recomputed and cached.
3. At startup the reference tables and the first `store.warm.entries` persisted
   food records are loaded into the process cache.
4. `store purge` deletes the SQLite store and JSONL export; `cache clear` empties
   both cache tiers; `reference clear` and `conflicts clear` remove their
   generated files. `make reset` removes everything generated.

## Mocking, assumptions and disclosures

- **The biological conflicts matrix image is not OCR'd.** It is a raster table
  with no text layer. The five rows are transcribed once into
  `reference/sources/biological_conflicts_matrix.csv`, and the program verifies
  that transcription against the image (size, pixel dimensions and SHA-256).
  If the image changes deliberately, review the transcription and run
  `reference build --accept-source-change`.
- The PDF chart **is** parsed from its text layer, including column geometry.
- Quantities come from `food_nutrient.csv` (USDA values per 100 g). When a
  serving size is in millilitres, the `analysis.serving.density.g.per.ml`
  assumption (default 1.0) is used to scale to a serving.
- Antinutrients such as phytate and oxalate are usually absent from branded
  labels; they are inferred from the ingredient list and clearly labelled
  accordingly.
- **No remote server, Docker container or security mock is used.** Redis is
  configured for future use but is inactive; `cache.backend=redis` falls back to
  disk when the package or server is unavailable.
- The chart applies to **high doses**; multivitamin-level amounts may not
  trigger the interactions. The program gates on `%DV` and reports the amounts.
- Results are informational only and are **not medical advice**. When an
  antagonistic combination is identified, the response includes a warning to
  review the result and the quantities with a registered dietitian.

## Project layout

```
application.properties      every runtime value
nutrient_conflicts/         shared Python package (core, CLI, API)
  refsources/               PDF and image ingestion
  ingredients.py            ingredient-list derivation
  ingest.py, store.py       first-run persistence and JSONL export
  analyzer.py               pair matching and %DV gating
  services.py               single service used by all interfaces
  cli.py, api.py            click CLI, FastAPI API
gui/                        React (Vite) SPA + Fastify proxy
reference/sources/          reviewed matrix transcription
tests/                      unittest suite and fixture dataset
data/                       generated store, reference and cache (gitignored)
```

## Troubleshooting

- **`store not built`** — run `uv run nutrient-conflicts ingest`, or set
  `store.auto.build=true` (default).
- **`could not find food.csv`** — check `dataset.root` in
  `application.properties`.
- **`the transcription does not match the image`** — the reference image was
  replaced; review the transcription and rebuild with
  `--accept-source-change`.
- **`npm not found`** — install Node.js 18+ or run only the CLI/API.
- **Slow first query** — the initial ingest scans ~2.5 GB of CSV; later queries
  read the SQLite store.

