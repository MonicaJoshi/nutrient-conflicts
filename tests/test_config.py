# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Configuration parsing tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.config import Config, ConfigError, parse_properties


class PropertiesTests(unittest.TestCase):
    def test_parses_comments_blank_lines_and_separators(self):
        values = parse_properties(
            "# comment\n! also comment\n\napp.name=NutrientConflicts\napp.version:1.2.3\nflag\n"
        )
        self.assertEqual(values["app.name"], "NutrientConflicts")
        self.assertEqual(values["app.version"], "1.2.3")
        self.assertEqual(values["flag"], "")

    def test_joins_continuation_lines(self):
        values = parse_properties("parse.ignore.prefixes=INGREDIENTS:,\\\n  MADE FROM:\n")
        self.assertEqual(values["parse.ignore.prefixes"], "INGREDIENTS:,MADE FROM:")

    def test_interpolation_uses_env_then_default(self):
        os.environ["NUTRIENTCONFLICTS_TEST_VALUE"] = "env-value"
        try:
            values = parse_properties(
                "a=${NUTRIENTCONFLICTS_TEST_VALUE}\n"
                "b=${NUTRIENTCONFLICTS_MISSING:fallback}\n"
                "c=${app.name}-x\napp.name=NutrientConflicts\n"
            )
            config = Config(root=Path("."), profile=None, values=values)
            config._interpolate()
        finally:
            del os.environ["NUTRIENTCONFLICTS_TEST_VALUE"]
        self.assertEqual(config.get("a"), "env-value")
        self.assertEqual(config.get("b"), "fallback")
        self.assertEqual(config.get("c"), "NutrientConflicts-x")


class ConfigAccessorTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            root=Path("."),
            profile="test",
            values={
                "app.name": "NutrientConflicts",
                "answer": "42",
                "ratio": "0.75",
                "enabled": "TRUE",
                "disabled": "no",
                "items": "a, b ,c",
                "weights": "strong:1.0,weak:0.3",
                "relative.path": "data/store",
            },
        )

    def test_typed_accessors(self):
        self.assertEqual(self.config.get_int("answer"), 42)
        self.assertAlmostEqual(self.config.get_float("ratio"), 0.75)
        self.assertTrue(self.config.get_bool("enabled"))
        self.assertFalse(self.config.get_bool("disabled"))
        self.assertEqual(self.config.get_list("items"), ["a", "b", "c"])
        self.assertEqual(self.config.get_map("weights"), {"strong": "1.0", "weak": "0.3"})

    def test_missing_key_raises(self):
        with self.assertRaises(ConfigError):
            self.config.get("nope")

    def test_bad_integer_raises(self):
        with self.assertRaises(ConfigError):
            self.config.get_int("app.name")

    def test_with_overrides_is_a_copy(self):
        clone = self.config.with_overrides({"answer": "7"})
        self.assertEqual(clone.get_int("answer"), 7)
        self.assertEqual(self.config.get_int("answer"), 42)

    def test_dir_path_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(root=Path(tmp), profile=None, values={"cache.dir": "cache"})
            path = config.dir_path("cache.dir")
            self.assertTrue(path.is_dir())
            self.assertEqual(path, Path(tmp) / "cache")

    def test_load_real_project_properties(self):
        config = Config.load()
        self.assertEqual(config.get("app.name"), "NutrientConflicts")
        self.assertGreater(config.get_float("analysis.min.dv.percent"), 0)
        described = config.describe()
        self.assertIn("root", described)
        self.assertTrue(described["sources"].endswith("application.properties"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

