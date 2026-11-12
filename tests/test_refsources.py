# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Tests for the reference source extractors (reference-data-1 and -2)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.refsources.builder import ReferenceBuilder
from nutrient_conflicts.refsources.chart import ChartError, extract_chart
from nutrient_conflicts.refsources.matrix import (SourceChangedError, image_fingerprint, load_matrix,
                                          refresh_fingerprint)
from nutrient_conflicts.refsources.merge import (Claim, MergeError, diff_against, guard, merge_claims)
from nutrient_conflicts.refsources.names import NutrientNameMap, UnknownNutrientError, normalise_name
from support import PROJECT_ROOT, fixture_config

SOURCE_DIR = PROJECT_ROOT / "referenceData"
EVIDENCE = {"inferred": 0, "chart": 1, "clinical": 2}
STRENGTH = {"weak": 0, "moderate": 1, "strong": 2}


class NameMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = fixture_config(Path(tempfile.mkdtemp(prefix="ni-names-")))
        self.names = NutrientNameMap.load(self.config.path("reference.name.map"))

    def test_spellings_resolve_to_the_same_key(self):
        for name in ("Vitamin B-12", "vitamin b12", "Cobalamin", "VITAMIN B 12"):
            self.assertEqual(self.names.resolve(name), "VITAMIN_B12", name)

    def test_parenthetical_qualifiers_are_ignored(self):
        self.assertEqual(self.names.resolve("Vitamin B1 (Thiamin)"), "VITAMIN_B1")
        self.assertEqual(normalise_name("  Vitamin   D  "), "VITAMIN D")

    def test_unknown_name_is_explicit(self):
        with self.assertRaises(UnknownNutrientError) as context:
            self.names.resolve("Unobtainium")
        self.assertIn("nutrient_name_map.csv", str(context.exception))

    def test_cells_expand_multi_nutrient_values(self):
        self.assertEqual(self.names.resolve_cell("Phytates / Oxalates"),
                         ["PHYTATE", "OXALATE"])
        self.assertEqual(self.names.resolve_cell("Calcium / Zinc / Iron"),
                         ["CALCIUM", "ZINC", "IRON"])


class ChartExtractionTests(unittest.TestCase):
    """The chart is parsed from the real PDF in the repository."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workdir = Path(tempfile.mkdtemp(prefix="ni-chart-"))
        cls.config = fixture_config(cls.workdir)
        cls.names = NutrientNameMap.load(cls.config.path("reference.name.map"))
        cls.document = extract_chart(cls.config.path("reference.source.vitamin_mineral.pdf"),
                                     cls.config, cls.names)
        cls.rows = {row.nutrient_key: row for row in cls.document.rows}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def test_every_nutrient_row_is_recognised(self):
        self.assertEqual(len(self.document.rows), 26)
        self.assertIn("VITAMIN_A", self.rows)
        self.assertIn("SULFUR", self.rows)

    def test_columns_stay_aligned_when_cells_wrap(self):
        vitamin_a = self.rows["VITAMIN_A"]
        self.assertEqual(vitamin_a.synergistic, ("IODINE", "IRON", "ZINC"))
        self.assertEqual(vitamin_a.varies, ("VITAMIN_E",))
        self.assertEqual(vitamin_a.antagonistic, ("VITAMIN_K", "VITAMIN_D"))

    def test_split_labels_and_wrapped_cells_are_joined(self):
        self.assertEqual(self.rows["VITAMIN_B6"].antagonistic, ("VITAMIN_B1", "VITAMIN_B9", "ZINC"))
        self.assertIn("MANGANESE", self.rows["CALCIUM"].antagonistic)
        self.assertEqual(self.rows["SULFUR"].synergistic, ("MOLYBDENUM",))

    def test_superscript_reference_numbers_are_ignored(self):
        for row in self.document.rows:
            for key in (*row.synergistic, *row.varies, *row.antagonistic):
                self.assertRegex(key, r"^[A-Z][A-Z0-9_]+$")

    def test_dose_note_is_lifted_from_the_pdf(self):
        self.assertIn("high doses", self.document.dose_note.lower())

    def test_wrong_column_boundaries_fail_loudly(self):
        broken = self.config.with_overrides({"reference.pdf.column.boundaries": "0.9,0.94,0.97"})
        with self.assertRaises(ChartError):
            extract_chart(self.config.path("reference.source.vitamin_mineral.pdf"), broken, self.names)


class MatrixTranscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="ni-matrix-"))
        self.config = fixture_config(self.workdir)
        self.names = NutrientNameMap.load(self.config.path("reference.name.map"))
        self.transcription = self.config.path("reference.source.biological.source_file")
        self.image = self.config.path("reference.source.biological.image")

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_transcription_matches_the_image(self):
        document = load_matrix(self.transcription, self.image, self.config, self.names)
        self.assertEqual(document.declared_rows, 5)
        self.assertEqual(len(document.rows), 5)
        self.assertEqual(document.rows[0].nutrients_a, ("CALCIUM",))
        self.assertEqual(document.rows[0].relation, "ANTAGONISTIC")
        self.assertIn("divalent metal", document.rows[0].mechanism)

    def test_multi_nutrient_cells_expand(self):
        document = load_matrix(self.transcription, self.image, self.config, self.names)
        last = document.rows[-1]
        self.assertEqual(set(last.nutrients_a), {"PHYTATE", "OXALATE"})
        self.assertEqual(set(last.nutrients_b), {"CALCIUM", "ZINC", "IRON"})

    def test_changed_image_is_refused(self):
        copy = self.workdir / "changed" / self.transcription.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        text = self.transcription.read_text(encoding="utf-8")
        copy.write_text(text.replace("image.sha256=", "image.sha256=dead"), encoding="utf-8")
        with self.assertRaises(SourceChangedError) as context:
            load_matrix(copy, self.image, self.config, self.names)
        self.assertIn("--accept-source-change", str(context.exception))

    def test_accepting_a_changed_image_refreshes_the_fingerprint(self):
        copy = self.workdir / "changed" / self.transcription.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        text = self.transcription.read_text(encoding="utf-8")
        copy.write_text(text.replace("image.sha256=", "image.sha256=dead"), encoding="utf-8")
        document = load_matrix(copy, self.image, self.config, self.names, accept_source_change=True)
        self.assertEqual(document.fingerprint["image.sha256"], image_fingerprint(self.image)["image.sha256"])
        refresh_fingerprint(copy, self.image)
        self.assertNotIn("image.sha256=dead", copy.read_text(encoding="utf-8"))
        load_matrix(copy, self.image, self.config, self.names)

    def test_declared_row_count_is_enforced(self):
        copy = self.workdir / "rows" / self.transcription.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        text = self.transcription.read_text(encoding="utf-8").replace("image.rows=5", "image.rows=6")
        copy.write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError) as context:
            load_matrix(copy, self.image, self.config, self.names)
        self.assertIn("declares 6", str(context.exception))


class MergeTests(unittest.TestCase):
    def claims(self):
        return [
            Claim("IRON", "VITAMIN_C", "SYNERGISTIC", "chart", "chart", "moderate", "chart text"),
            Claim("IRON", "VITAMIN_C", "SYNERGISTIC", "matrix", "clinical", "strong", "matrix text"),
            Claim("CALCIUM", "IRON", "ANTAGONISTIC", "chart", "chart", "moderate", "chart text"),
            Claim("CALCIUM", "IRON", "SYNERGISTIC", "other", "chart", "moderate", "other text"),
            Claim("VITAMIN_A", "VITAMIN_E", "CONTEXT_DEPENDENT", "chart", "chart", "weak", "dose"),
            Claim("VITAMIN_A", "VITAMIN_E", "SYNERGISTIC", "matrix", "clinical", "strong", "syn"),
        ]

    def test_opposing_directions_become_context_dependent(self):
        merged = {pair.pair: pair for pair in merge_claims(self.claims(), EVIDENCE, STRENGTH)}
        pair = merged[("CALCIUM", "IRON")]
        self.assertEqual(pair.relation, "CONTEXT_DEPENDENT")
        self.assertEqual(pair.conflicting_claims, "ANTAGONISTIC/SYNERGISTIC")

    def test_dose_direction_is_kept_when_one_source_names_it(self):
        merged = {pair.pair: pair for pair in merge_claims(self.claims(), EVIDENCE, STRENGTH)}
        pair = merged[("VITAMIN_A", "VITAMIN_E")]
        self.assertEqual(pair.relation, "SYNERGISTIC")
        self.assertEqual(pair.conflicting_claims, "CONTEXT_DEPENDENT/SYNERGISTIC")

    def test_strongest_evidence_and_mechanism_win(self):
        merged = {pair.pair: pair for pair in merge_claims(self.claims(), EVIDENCE, STRENGTH)}
        pair = merged[("IRON", "VITAMIN_C")]
        self.assertEqual((pair.strength, pair.evidence_level), ("strong", "clinical"))
        self.assertEqual(pair.mechanism, "matrix text")

    def test_guard_blocks_removals_and_relation_changes(self):
        merged = merge_claims(self.claims(), EVIDENCE, STRENGTH)
        existing = [{"nutrient_a": "ZINC", "nutrient_b": "COPPER", "relation": "ANTAGONISTIC",
                     "strength": "strong", "evidence_level": "clinical"},
                    {"nutrient_a": "CALCIUM", "nutrient_b": "IRON", "relation": "ANTAGONISTIC",
                     "strength": "strong", "evidence_level": "clinical"}]
        diff = diff_against(existing, merged)
        self.assertEqual(diff["removed"], [("COPPER", "ZINC")])
        self.assertEqual(len(diff["relation_changed"]), 1)
        with self.assertRaises(MergeError):
            guard(diff, allow_removals=False)
        guard(diff, allow_removals=True)


class BuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="ni-builder-"))
        self.config = fixture_config(self.workdir)
        self.builder = ReferenceBuilder(self.config)

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_build_persists_both_sources_and_the_merged_kb(self):
        summary = self.builder.build()
        self.assertEqual(summary["merged"]["pairs"], 58)
        self.assertEqual(summary["merged"]["relations"]["ANTAGONISTIC"], 32)
        self.assertEqual(summary["merged"]["relations"]["SYNERGISTIC"], 21)
        self.assertEqual(summary["merged"]["relations"]["CONTEXT_DEPENDENT"], 5)
        for name in ("chart_csv", "chart_report", "matrix_csv", "matrix_report"):
            self.assertTrue(Path(summary["outputs"][name]).is_file(), name)
        self.assertTrue(self.builder.merged_file().is_file())

    def test_human_readable_outputs_are_populated(self):
        self.builder.build()
        chart_csv = self.builder.chart_output_file().read_text(encoding="utf-8")
        self.assertIn("Vitamin A,VITAMIN_A,SYNERGISTIC,Iodine,IODINE", chart_csv)
        chart_md = self.builder.chart_report_file().read_text(encoding="utf-8")
        self.assertIn("## Antagonistic", chart_md)
        matrix_md = self.builder.matrix_report_file().read_text(encoding="utf-8")
        self.assertIn("Scientific mechanism", matrix_md)
        self.assertIn("metallothionein", matrix_md)

    def test_status_round_trip_and_clear(self):
        self.builder.build()
        status = self.builder.status()
        self.assertFalse(status["stale"], status["reasons"])
        self.assertEqual(status["merged"]["pairs"], 58)

        self.builder.chart_source_file().write_text("nutrient_key\n", encoding="utf-8")
        self.assertTrue(self.builder.status()["stale"])

        removed = self.builder.clear()
        self.assertTrue(removed["removed"])
        self.assertFalse(self.builder.merged_file().is_file())
        self.assertTrue(self.builder.status()["stale"])

    def test_clear_can_keep_the_merged_kb(self):
        self.builder.build()
        self.builder.clear(keep_kb=True)
        self.assertTrue(self.builder.merged_file().is_file())
        self.assertFalse(self.builder.chart_output_file().is_file())

    def test_crosscheck_report_is_written(self):
        summary = self.builder.build()
        self.assertTrue(Path(summary["crosscheck"]["file"]).is_file())
        self.assertEqual(summary["crosscheck"]["removed"], 0)

    def test_single_source_build_still_merges_both(self):
        summary = self.builder.build(sources=["vitamin-mineral"])
        self.assertEqual(summary["sources"], ["vitamin-mineral"])
        self.assertEqual(summary["merged"]["pairs"], 58)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.builder.build(sources=["nothing"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
