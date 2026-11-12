# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Two level cache: in-process LRU over a persisted backend.

The first run of any command writes its results to ``cache.dir``; later runs read
the memory layer first, then the persisted layer, and repopulate memory. A Redis
backend can be selected in ``application.properties`` for a shared deployment; it
is imported lazily so the project runs without the dependency installed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)

_MISS = object()


class Cache:
    """LRU memory cache in front of a json-on-disk (or Redis) cache."""

    def __init__(self, config: Config) -> None:
        self.enabled = config.get_bool("cache.enabled", True)
        self.backend = config.get("cache.backend", "disk").strip().lower()
        self.ttl_seconds = config.get_int("cache.ttl.seconds", 86400)
        self.max_entries = config.get_int("cache.l1.max_entries", 2048)
        self.directory = config.path("cache.dir")
        self.redis_prefix = config.get("cache.redis.prefix", "NutrientConflicts:")
        self._redis = None
        self._lock = threading.RLock()
        self._l1: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._stats = {"l1_hits": 0, "l2_hits": 0, "misses": 0, "sets": 0, "clears": 0}
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)
        if self.backend == "redis":
            self._redis = self._connect_redis(config)

    # -- public API ------------------------------------------------------
    def get(self, namespace: str, key: str) -> Any | None:
        if not self.enabled:
            return None
        cache_key = self.key_for(namespace, key)
        now = time.time()
        with self._lock:
            entry = self._l1.get(cache_key)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    self._l1.move_to_end(cache_key)
                    self._stats["l1_hits"] += 1
                    return value
                self._l1.pop(cache_key, None)
        value = self._read_backend(cache_key, namespace, now)
        if value is _MISS:
            self._stats["misses"] += 1
            return None
        self._stats["l2_hits"] += 1
        self._store_l1(cache_key, value, now)
        return value

    def set(self, namespace: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        cache_key = self.key_for(namespace, key)
        now = time.time()
        self._store_l1(cache_key, value, now)
        self._stats["sets"] += 1
        if self.backend == "memory":
            return
        if self.backend == "redis" and self._redis is not None:
            try:
                self._redis.setex(cache_key, self.ttl_seconds, json.dumps(value, default=str))
                return
            except Exception as exc:  # pragma: no cover - depends on a live server
                logger.warning("redis cache write failed, falling back to disk: %s", exc)
                self.backend = "disk"
        self._write_disk(cache_key, namespace, value)

    def clear(self) -> int:
        with self._lock:
            removed = len(self._l1)
            self._l1.clear()
            self._stats["clears"] += 1
        if self._redis is not None:
            try:
                for key in self._redis.scan_iter(f"{self.redis_prefix}*"):
                    self._redis.delete(key)
            except Exception as exc:  # pragma: no cover - depends on a live server
                logger.warning("redis cache clear failed: %s", exc)
        if self.directory.exists():
            for path in self.directory.glob("*/**/*.json"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:  # pragma: no cover - file locking on Windows
                    logger.debug("could not delete cache file %s", path)
        return removed

    def stats(self) -> dict[str, Any]:
        files = sum(1 for _ in self.directory.glob("*/**/*.json")) if self.directory.exists() else 0
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "directory": str(self.directory),
            "l1_entries": len(self._l1),
            "l1_max_entries": self.max_entries,
            "l2_entries": files,
            "ttl_seconds": self.ttl_seconds,
            **self._stats,
        }

    @staticmethod
    def key_for(namespace: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return f"{namespace}:{digest}"

    # -- internals -------------------------------------------------------
    def _store_l1(self, cache_key: str, value: Any, now: float) -> None:
        with self._lock:
            self._l1[cache_key] = (now + self.ttl_seconds, value)
            self._l1.move_to_end(cache_key)
            while len(self._l1) > self.max_entries:
                self._l1.popitem(last=False)

    def _read_backend(self, cache_key: str, namespace: str, now: float) -> Any:
        if self.backend == "memory":
            return _MISS
        if self.backend == "redis" and self._redis is not None:
            try:
                raw = self._redis.get(cache_key)
                if raw:
                    return json.loads(raw)
                return _MISS
            except Exception as exc:  # pragma: no cover - depends on a live server
                logger.warning("redis cache read failed, falling back to disk: %s", exc)
                self.backend = "disk"
        path = self._disk_path(cache_key, namespace)
        if not path.is_file():
            return _MISS
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _MISS
        if payload.get("expires_at", 0) <= now:
            path.unlink(missing_ok=True)
            return _MISS
        return payload.get("value")

    def _write_disk(self, cache_key: str, namespace: str, value: Any) -> None:
        path = self._disk_path(cache_key, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"expires_at": time.time() + self.ttl_seconds, "value": value}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(temporary, path)

    def _disk_path(self, cache_key: str, namespace: str) -> Path:
        _, _, digest = cache_key.partition(":")
        return self.directory / namespace / f"{digest}.json"

    def _connect_redis(self, config: Config):  # pragma: no cover - optional dependency
        try:
            import redis  # type: ignore
        except ImportError:
            logger.warning("cache.backend=redis but the redis package is not installed; using disk")
            self.backend = "disk"
            return None
        try:
            client = redis.Redis(
                host=config.get("cache.redis.host", "localhost"),
                port=config.get_int("cache.redis.port", 6379),
                db=config.get_int("cache.redis.db", 0),
                password=config.get("cache.redis.password", "") or None,
                socket_timeout=config.get_int("api.request.timeout.seconds", 30),
            )
            client.ping()
            logger.info("redis cache connected")
            return client
        except Exception as exc:
            logger.warning("redis unavailable (%s); using disk cache", exc)
            self.backend = "disk"
            return None
