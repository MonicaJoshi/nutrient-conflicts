# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Cache tests: memory layer, persisted layer, expiry and clearing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nutrient_conflicts.cache import Cache
from nutrient_conflicts.config import Config

BASE = Config.load(profile="test")


def cache_for(directory: Path, **overrides) -> Cache:
    config = BASE.with_overrides({"cache.dir": str(directory), **overrides})
    return Cache(config)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.cache = cache_for(self.directory)

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_and_get_from_memory(self):
        self.cache.set("ns", "key", {"value": 1})
        self.assertEqual(self.cache.get("ns", "key"), {"value": 1})
        self.assertEqual(self.cache.stats()["l1_hits"], 1)

    def test_persisted_layer_survives_a_restart(self):
        self.cache.set("ns", "key", {"value": 2})
        restarted = cache_for(self.directory)
        self.assertEqual(restarted.get("ns", "key"), {"value": 2})
        self.assertEqual(restarted.stats()["l2_hits"], 1)

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("ns", "absent"))
        self.assertEqual(self.cache.stats()["misses"], 1)

    def test_expired_entries_are_dropped(self):
        short = cache_for(self.directory / "short", **{"cache.ttl.seconds": "0"})
        short.set("ns", "key", {"value": 3})
        self.assertIsNone(short.get("ns", "key"))

    def test_clear_removes_memory_and_disk(self):
        self.cache.set("ns", "key", {"value": 4})
        removed = self.cache.clear()
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(self.cache.get("ns", "key"))

    def test_disabled_cache_is_a_no_op(self):
        disabled = cache_for(self.directory / "off", **{"cache.enabled": "false"})
        disabled.set("ns", "key", {"value": 5})
        self.assertIsNone(disabled.get("ns", "key"))
        self.assertFalse(disabled.stats()["enabled"])

    def test_lru_evicts_oldest_entry(self):
        small = cache_for(self.directory / "lru", **{"cache.l1.max_entries": "2", "cache.backend": "memory"})
        small.set("ns", "a", 1)
        small.set("ns", "b", 2)
        small.set("ns", "c", 3)
        self.assertEqual(small.stats()["l1_entries"], 2)
        self.assertIsNone(small.get("ns", "a"))

    def test_key_is_stable_and_namespaced(self):
        first = Cache.key_for("evaluation", "123")
        second = Cache.key_for("evaluation", "123")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("evaluation:"))
        self.assertNotEqual(first, Cache.key_for("food", "123"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
