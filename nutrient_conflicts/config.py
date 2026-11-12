#!/usr/bin/env python3
# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Configuration loader for NutrientConflicts.

Every runtime tunable is read from ``application.properties`` (and an optional
``application-<profile>.properties`` overlay). No magic values are hard coded in
the rest of the package.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

_INTERPOLATION = re.compile(r"\$\{([A-Za-z0-9_.]+)(?::([^}]*))?\}")


class ConfigError(RuntimeError):
    """Raised when a required configuration key is missing or malformed."""


@dataclass
class Config:
    """Parsed properties with typed accessors and project relative paths."""

    root: Path
    profile: str | None
    values: dict[str, str] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    # -- loading ---------------------------------------------------------
    @classmethod
    def load(cls, root: Path | str | None = None, profile: str | None = None) -> "Config":
        project_root = Path(root) if root else default_project_root()
        profile = profile or os.environ.get("NutrientConflicts_PROFILE") or None
        cfg = cls(root=project_root, profile=profile)
        cfg._read(project_root / "application.properties", required=True)
        if profile:
            cfg._read(project_root / f"application-{profile}.properties", required=False)
        cfg._interpolate()
        return cfg

    def _read(self, path: Path, required: bool) -> None:
        if not path.is_file():
            if required:
                raise ConfigError(f"configuration file not found: {path}")
            return
        text = path.read_text(encoding="utf-8")
        self.values.update(parse_properties(text))
        self.sources.append(path)

    def _interpolate(self) -> None:
        for _ in range(10):
            changed = False
            for key, value in list(self.values.items()):
                if "${" not in value:
                    continue

                def replace(match: re.Match[str]) -> str:
                    name, default = match.group(1), match.group(2)
                    if name in os.environ:
                        return os.environ[name]
                    if name in self.values:
                        return self.values[name]
                    return default if default is not None else ""

                new_value = _INTERPOLATION.sub(replace, value)
                if new_value != value:
                    self.values[key] = new_value
                    changed = True
            if not changed:
                break

    # -- accessors -------------------------------------------------------
    def has(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str, default: str | None = None) -> str:
        if key in self.values:
            return self.values[key]
        if default is None:
            raise ConfigError(f"missing required configuration key: {key}")
        return default

    def get_int(self, key: str, default: int | None = None) -> int:
        raw = self.get(key, None if default is None else str(default))
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"configuration key {key} must be an integer, got {raw!r}") from exc

    def get_float(self, key: str, default: float | None = None) -> float:
        raw = self.get(key, None if default is None else str(default))
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"configuration key {key} must be a number, got {raw!r}") from exc

    def get_bool(self, key: str, default: bool | None = None) -> bool:
        raw = self.get(key, None if default is None else str(default))
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def get_list(self, key: str, default: list[str] | None = None, separator: str = ",") -> list[str]:
        if key not in self.values:
            return list(default or [])
        return [part.strip() for part in self.values[key].split(separator) if part.strip()]

    def get_map(self, key: str, default: dict[str, str] | None = None) -> dict[str, str]:
        if key not in self.values:
            return dict(default or {})
        result: dict[str, str] = {}
        for item in self.values[key].split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                name, value = item.split(":", 1)
                result[name.strip()] = value.strip()
            else:
                result[item] = ""
        return result

    def path(self, key: str) -> Path:
        raw = self.get(key)
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else self.root / candidate

    def dir_path(self, key: str) -> Path:
        path = self.path(key)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- testing helpers -------------------------------------------------
    def with_overrides(self, overrides: dict[str, str]) -> "Config":
        clone = Config(root=self.root, profile=self.profile, values=dict(self.values), sources=list(self.sources))
        clone.values.update({key: str(value) for key, value in overrides.items()})
        return clone

    def describe(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "profile": self.profile or "",
            "sources": ",".join(str(path) for path in self.sources),
        }


def default_project_root() -> Path:
    env = os.environ.get("NutrientConflicts_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def ensure_reference_workspace(config: "Config") -> Path:
    """Copy the read-only reference CSVs into the writable work area once.

    The external reference folder is never modified. The merged knowledge base, the
    parsed document results and the cross-check report are all written under
    ``reference.dir`` instead.
    """
    source = config.path("reference.source.dir")
    work = config.dir_path("reference.dir")
    for key in ("reference.file.aliases", "reference.file.dv", "reference.file.crosswalk",
                "reference.name.map", "reference.file.interactions"):
        name = Path(config.get(key)).name
        target = work / name
        if not target.is_file():
            origin = source / name
            if origin.is_file():
                shutil.copy2(origin, target)
    seed_name = config.get("reference.file.interactions.seed", "nutrient_interactions.seed.csv")
    seed = work / seed_name
    if not seed.is_file():
        origin = source / config.get("reference.file.interactions")
        if origin.is_file():
            shutil.copy2(origin, seed)
    return work


def parse_properties(text: str) -> dict[str, str]:
    """Parse a Java style ``.properties`` document."""
    values: dict[str, str] = {}
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not pending and (not line or line.startswith("#") or line.startswith("!")):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending += line[:-1]
            continue
        line = pending + line
        pending = ""
        if not line:
            continue
        separator_index = _first_separator(line)
        if separator_index < 0:
            values[line.strip()] = ""
        else:
            key = line[:separator_index].strip()
            value = line[separator_index + 1:].strip()
            if key:
                values[key] = value
    return values


def _first_separator(line: str) -> int:
    for index, char in enumerate(line):
        if char in "=:":
            return index
    return -1
