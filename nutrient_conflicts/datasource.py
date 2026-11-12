# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Locate and stream the USDA FoodData Central CSV files.

The release ships as a folder of CSVs *and* as zip archives. The extracted
folders in this repository are incomplete, so the resolver falls back to reading
the CSV straight out of the zip without unpacking 2 GB to disk.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO

from .config import Config


class DatasetError(RuntimeError):
    """Raised when a dataset file cannot be located."""


@dataclass(frozen=True)
class LocatedFile:
    """A CSV file that may live on disk or inside a zip archive."""

    filename: str
    display: str
    size: int
    modified: float
    disk_path: Path | None = None
    zip_path: Path | None = None
    member: str | None = None

    @contextmanager
    def open_text(self, encoding: str = "utf-8") -> Iterator[TextIO]:
        if self.disk_path is not None:
            with self.disk_path.open("r", encoding=encoding, newline="") as handle:
                yield handle
            return
        if self.zip_path is None or self.member is None:
            raise DatasetError(f"{self.filename} has no readable location")
        with zipfile.ZipFile(self.zip_path) as archive:
            with archive.open(self.member) as raw:
                with io.TextIOWrapper(raw, encoding=encoding, newline="") as handle:
                    yield handle

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "location": self.display,
            "size": self.size,
            "modified": self.modified,
        }


class DatasetSource:
    """Resolve dataset CSV names to a readable location."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = config.path("dataset.root")
        self.encoding = config.get("dataset.csv.encoding", "utf-8")
        self.zip_pattern = config.get("dataset.zip.pattern", "*.zip")
        self.preferred_zip = config.get("dataset.preferred.zip", "")
        self._located: dict[str, LocatedFile] = {}

    # -- public API ------------------------------------------------------
    def locate(self, filename: str) -> LocatedFile:
        if filename in self._located:
            return self._located[filename]
        found = self._locate_on_disk(filename) or self._locate_in_zip(filename)
        if found is None:
            raise DatasetError(
                f"could not find {filename} under {self.root}; "
                "set dataset.root in application.properties"
            )
        self._located[filename] = found
        return found

    def has(self, filename: str) -> bool:
        try:
            self.locate(filename)
            return True
        except DatasetError:
            return False

    def rows(self, filename: str, columns: list[str]) -> Iterator[dict[str, str]]:
        """Yield csv rows as dictionaries with only the requested columns."""
        located = self.locate(filename)
        with located.open_text(self.encoding) as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return
            positions = {name: header.index(name) for name in columns if name in header}
            missing = [name for name in columns if name not in positions]
            if missing:
                raise DatasetError(f"{filename} is missing columns: {', '.join(missing)}")
            for row in reader:
                try:
                    yield {name: row[index] for name, index in positions.items()}
                except IndexError:
                    continue

    def describe(self) -> dict:
        files = {}
        for key in ("dataset.file.branded_food", "dataset.file.food",
                    "dataset.file.food_nutrient", "dataset.file.nutrient"):
            filename = self.config.get(key)
            try:
                files[filename] = self.locate(filename).as_dict()
            except DatasetError:
                files[filename] = {"filename": filename, "location": "", "size": 0, "modified": 0}
        total = sum(item["size"] for item in files.values())
        return {"root": str(self.root), "files": files, "total_bytes": total}

    # -- internals -------------------------------------------------------
    def _locate_on_disk(self, filename: str) -> LocatedFile | None:
        if not self.root.exists():
            return None
        candidates = [path for path in self.root.rglob(filename) if path.is_file()]
        if not candidates:
            return None
        path = max(candidates, key=lambda item: item.stat().st_size)
        stat = path.stat()
        return LocatedFile(
            filename=filename,
            display=str(path),
            size=stat.st_size,
            modified=stat.st_mtime,
            disk_path=path,
        )

    def _locate_in_zip(self, filename: str) -> LocatedFile | None:
        if not self.root.exists():
            return None
        archives = [path for path in self.root.rglob("*") if path.is_file() and fnmatch.fnmatch(path.name, self.zip_pattern)]
        archives.sort(key=lambda item: (item.name != self.preferred_zip, item.name))
        for archive in archives:
            try:
                with zipfile.ZipFile(archive) as handle:
                    for info in handle.infolist():
                        if info.is_dir():
                            continue
                        if Path(info.filename).name != filename:
                            continue
                        modified = datetime(*info.date_time).timestamp()
                        return LocatedFile(
                            filename=filename,
                            display=f"{archive}::{info.filename}",
                            size=info.file_size,
                            modified=modified,
                            zip_path=archive,
                            member=info.filename,
                        )
            except zipfile.BadZipFile:
                continue
        return None
