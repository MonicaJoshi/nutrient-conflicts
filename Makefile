# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
#
# Nutrient Conflicts Evaluation developer tasks. Run `make help` for the list.
# Every runtime value lives in application.properties; this file only wires
# developer workflows together.

PYTHON ?= uv run python
VENV   ?= .venv
TESTS  ?= tests

.PHONY: help venv install test coverage ingest reference crosscheck status api gui gui-install gui-build purge reset clean

help:
	@echo "make venv        create $(VENV) with uv (falls back to python -m venv)"
	@echo "make install     install the project and its test dependencies"
	@echo "make test        run the unit suite under coverage (fails below 80%)"
	@echo "make coverage    same as test, then write the HTML report"
	@echo "make ingest      build the persisted store from the USDA release"
	@echo "make reference   read the PDF chart + matrix image and rebuild the knowledge base"
	@echo "make crosscheck  show the generated-vs-curated reference diff"
	@echo "make status      show store, reference data, results and cache status"
	@echo "make api         serve the FastAPI application (docs at /docs)"
	@echo "make gui-install install the React/Fastify GUI dependencies"
	@echo "make gui-build   build the React production bundle"
	@echo "make gui         build (if needed) and serve the GUI through Fastify"
	@echo "make purge       delete the store, exports and cache"
	@echo "make reset       delete every generated artefact"
	@echo "make clean       remove coverage data, caches and build artefacts"

venv:
	uv venv $(VENV) || $(PYTHON) -m venv $(VENV)

install:
	uv pip install -e ".[test]" || $(PYTHON) -m pip install -e ".[test]"

test:
	$(PYTHON) -m coverage run --source=nutrient_conflicts -m unittest discover -s $(TESTS) -t $(TESTS)
	$(PYTHON) -m coverage report

coverage: test
	$(PYTHON) -m coverage html
	@echo "HTML report: htmlcov/index.html"

ingest:
	uv run nutrient-conflicts ingest

reference:
	uv run nutrient-conflicts reference build

crosscheck:
	uv run nutrient-conflicts reference crosscheck

status:
	uv run nutrient-conflicts status

api:
	uv run nutrient-conflicts serve

gui-install:
	cd gui && npm install

gui-build:
	cd gui && npm run build

gui:
	uv run nutrient-conflicts gui

purge:
	uv run nutrient-conflicts store purge
	uv run nutrient-conflicts cache clear

reset:
	uv run nutrient-conflicts store purge
	uv run nutrient-conflicts conflicts clear
	uv run nutrient-conflicts reference clear
	uv run nutrient-conflicts cache clear

clean:
	-$(PYTHON) -m coverage erase
	-rm -rf htmlcov build dist *.egg-info .pytest_cache gui/dist
	-find . -name "__pycache__" -type d -prune -exec rm -rf {} +
