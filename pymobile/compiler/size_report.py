"""Compressed APK byte attribution without building or extracting an archive."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..errors import PyMobileError


@dataclass(frozen=True)
class SizeReport:
    apk: Path
    total: int
    categories: dict[str, int]
    dependencies: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "apk": str(self.apk),
            "total": self.total,
            "categories": self.categories,
            "dependencies": self.dependencies,
        }


def analyze_apk(apk: Path) -> SizeReport:
    """Use ZIP compressed sizes; the residual includes ZIP/signing overhead.

    Installed wheel RECORD entries identify distribution files including data.
    When RECORD is absent, at least the dist-info directory is attributed.
    """
    if not apk.is_file():
        raise PyMobileError(
            f"APK not found: {apk}", hint="Build one with `pymobile build --native`."
        )
    try:
        with zipfile.ZipFile(apk) as archive:
            files = {info.filename: info for info in archive.infolist() if not info.is_dir()}
            ownership: dict[str, str] = {}
            for name in files:
                if not (name.startswith("assets/app/") and name.endswith(".dist-info/RECORD")):
                    continue
                distribution = name.removeprefix("assets/app/").split("/", 1)[0]
                for row in csv.reader(io.StringIO(archive.read(name).decode("utf-8"))):
                    if not row:
                        continue
                    path = PurePosixPath(row[0])
                    if path.is_absolute() or ".." in path.parts:
                        continue
                    ownership["assets/app/" + path.as_posix()] = distribution
            categories: dict[str, int] = defaultdict(int)
            dependencies: dict[str, int] = defaultdict(int)
            for name, info in files.items():
                if name.startswith("assets/python/") or name.startswith("lib/"):
                    group = "runtime"
                elif name.startswith("assets/app/pymobile/"):
                    group = "framework"
                elif name.startswith("assets/app/"):
                    owner = ownership.get(name)
                    if owner is None:
                        prefix = name.removeprefix("assets/app/").split("/", 1)[0]
                        if prefix.endswith(".dist-info"):
                            owner = prefix
                    group = "dependencies" if owner else "app"
                    if owner:
                        dependencies[owner] += info.compress_size
                else:
                    group = "resources"
                categories[group] += info.compress_size
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise PyMobileError(f"Could not read APK {apk}: {error}") from error
    total = apk.stat().st_size
    categories["archive/signing overhead"] = max(0, total - sum(categories.values()))
    return SizeReport(apk, total, dict(categories), dict(sorted(dependencies.items())))
