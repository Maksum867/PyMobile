"""Non-installing, target-specific wheel checks for app dependencies."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .runtime import PYTHON_VERSION

_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib", ".a", ".exe")


@dataclass(frozen=True)
class DependencyCheck:
    requirement: str
    compatible: bool
    detail: str


def check_dependencies(requirements: list[str]) -> list[DependencyCheck]:
    """Download universal wheels only, inspecting the entire dependency tree.

    A failed resolution does not prove a dependency is native: it might also be
    unavailable, have incompatible metadata, or be unreachable over the network.
    No project files are modified and no package code is executed/installed.
    """
    results: list[DependencyCheck] = []
    for requirement in requirements:
        with tempfile.TemporaryDirectory(prefix="pymobile-deps-check-") as directory:
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--platform",
                "any",
                "--implementation",
                "py",
                "--python-version",
                PYTHON_VERSION.rsplit(".", 1)[0],
                "--abi",
                "none",
                "--dest",
                directory,
                requirement,
            ]
            try:
                completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired) as error:
                results.append(DependencyCheck(requirement, False, str(error)))
                continue
            if completed.returncode:
                results.append(
                    DependencyCheck(
                        requirement,
                        False,
                        "No compatible universal wheel (or index/network error): "
                        + (completed.stderr or completed.stdout).strip()[-500:],
                    )
                )
                continue
            wheels = list(Path(directory).glob("*.whl"))
            if not wheels:
                results.append(DependencyCheck(requirement, False, "pip returned no wheels"))
                continue
            try:
                for wheel in wheels:
                    with zipfile.ZipFile(wheel) as archive:
                        names = archive.namelist()
                        if any(name.lower().endswith(_NATIVE_SUFFIXES) for name in names):
                            raise ValueError(f"{wheel.name} contains a binary extension")
                        metadata = [name for name in names if name.endswith(".dist-info/WHEEL")]
                        if len(metadata) != 1 or b"Root-Is-Purelib: true" not in archive.read(
                            metadata[0]
                        ):
                            raise ValueError(f"{wheel.name} is not a pure-Python wheel")
            except (OSError, zipfile.BadZipFile, ValueError, KeyError) as error:
                results.append(DependencyCheck(requirement, False, str(error)))
                continue
            results.append(
                DependencyCheck(
                    requirement,
                    True,
                    "universal wheels: " + ", ".join(sorted(w.name for w in wheels)),
                )
            )
    return results
