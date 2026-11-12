# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Shared helpers for the test-suite.

The fixtures are a tiny four-file FDC release. The reference documents are read
from the external project folder, while every generated artefact goes to a
throw-away work directory so the repository never accumulates test data.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.config import Config, ensure_reference_workspace
from nutrient_conflicts.services import NutrientConflictsService

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dataset"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def reference_source_dir(config: Config) -> Path:
    return config.path("reference.source.dir")


def fixture_config(workdir: Path) -> Config:
    """Test configuration with store, cache and generated output redirected."""
    config = Config.load()
    reference = workdir / "reference"
    sources = reference_source_dir(config)
    outputs = {
        "dataset.root": str(FIXTURES),
        "store.dir": str(workdir / "store"),
        "cache.dir": str(workdir / "cache"),
        "log.level": "WARNING",
        "reference.dir": str(reference),
        "reference.name.map": str(reference / "nutrient_name_map.csv"),
        "reference.file.interactions": str(reference / "nutrient_interactions.csv"),
        "reference.source.dir": str(sources),
        "reference.source.vitamin_mineral.pdf": str(
            sources / "MET2557-Vitamin-Mineral-Interactions-Chart_IPAD.pdf"),
        "reference.source.biological.image": str(sources / "BiologicalConflicts-Matrix-AvsB.png"),
        "reference.source.biological.source_file": str(
            PROJECT_ROOT / "reference" / "sources" / "biological_conflicts_matrix.csv"),
        "reference.source.vitamin_mineral.source_file": str(
            reference / "sources" / "vitamin_mineral_interactions.csv"),
        "reference.source.vitamin_mineral.output_file": str(
            reference / "derived" / "Vitamin-Mineral-Interactions.csv"),
        "reference.source.vitamin_mineral.report_file": str(
            reference / "derived" / "Vitamin-Mineral-Interactions.md"),
        "reference.source.biological.output_file": str(
            reference / "derived" / "BiologicalConflicts-Matrix.csv"),
        "reference.source.biological.report_file": str(
            reference / "derived" / "BiologicalConflicts-Matrix.md"),
        "reference.manifest.file": str(reference / "derived" / "reference-manifest.json"),
        "reference.crosscheck.file": str(reference / "derived" / "reference-crosscheck.json"),
        "conflicts.output.dir": str(workdir / "results"),
    }
    reference.mkdir(parents=True, exist_ok=True)
    config = config.with_overrides(outputs)
    ensure_reference_workspace(config)
    return config


class StoreTestCase(unittest.TestCase):
    """Builds one ingest for the whole test class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workdir = Path(tempfile.mkdtemp(prefix="NutrientConflicts-test-"))
        cls.config = fixture_config(cls.workdir)
        cls.service = NutrientConflictsService(cls.config)
        cls.service.ingest(force=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workdir, ignore_errors=True)
