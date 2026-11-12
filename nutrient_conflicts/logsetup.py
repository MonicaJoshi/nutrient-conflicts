# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Logging setup driven by ``application.properties``.

The handler resolves ``sys.stderr`` at write time instead of binding it once, so a
caller that temporarily redirects stderr (a test runner, a report writer, a GUI
console) never ends up with a closed or stale stream.
"""

from __future__ import annotations

import logging
import sys

from .config import Config

_CONFIGURED = False


class _DynamicStderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr``."""

    def __init__(self) -> None:
        super().__init__(stream=sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:  # type: ignore[override]
        pass


def configure_logging(config: Config, stream=None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, config.get("log.level", "INFO").upper(), logging.INFO)
    handler = _DynamicStderrHandler() if stream is None else logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(
        config.get("log.format", "%(asctime)s %(levelname)-7s %(name)s: %(message)s")))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("nutrient_conflicts").setLevel(level)
    _CONFIGURED = True


def reset_logging() -> None:
    """Remove handlers; used by the test-suite."""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    _CONFIGURED = False
