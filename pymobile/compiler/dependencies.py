"""Resolve pure-Python PyPI wheels for the embedded Android CPython 3.14.

Only platform-independent wheels are accepted. Host site-packages and host
binary extensions must never be copied to an Android APK.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core.config import ProjectConfig
from ..errors import PyMobileError
from .runtime import PYTHON_VERSION

_SKIP = {"__pycache__", "bin"}
_NATIVE = {".so", ".pyd", ".dll", ".dylib", ".a", ".exe", ".pyc", ".pyo"}


def _files(directory: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if relative.as_posix() == ".pymobile-requirements.json" or any(
            part in _SKIP for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise PyMobileError(f"Dependency contains a symlink: {relative}")
        if not path.is_file():
            continue
        if path.suffix.lower() in _NATIVE:
            raise PyMobileError(
                f"Dependency contains a native/binary file: {relative}",
                hint="Only pure-Python wheels are supported; Android extensions "
                "need an Android build.",
            )
        entries.append((relative.as_posix(), path))
    if not entries or not any(name.endswith(".dist-info/WHEEL") for name, _ in entries):
        raise PyMobileError("Dependency install is empty or missing wheel metadata")
    for name, path in entries:
        if name.endswith(".dist-info/WHEEL"):
            content = path.read_text(encoding="utf-8")
            if "Root-Is-Purelib: true" not in content:
                raise PyMobileError(f"Dependency is not pure Python: {name}")
    return entries


def prepare_dependencies(config: ProjectConfig) -> list[tuple[str, Path]]:
    """Install declared distributions into an excluded project-local cache.

    The result is a flat set of files mapped to the *app root* where the JNI
    launcher already adds them to sys.path. Reusing the cache keeps rebuilds
    fast; removing the directory forces a fresh resolution of unpinned specs.
    """
    if not config.dependencies:
        return []
    cache = config.root / ".pymobile" / "dependencies"
    key = hashlib.sha256(json.dumps([PYTHON_VERSION, config.dependencies]).encode()).hexdigest()
    marker = cache / ".pymobile-requirements.json"
    if marker.is_file():
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == key:
                return _files(cache)
        except (OSError, ValueError, PyMobileError):
            pass  # a corrupted cache must be reinstalled, not shipped

    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pymobile-deps-", dir=cache.parent) as tmp:
        target = Path(tmp) / "packages"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-compile",
            "--only-binary=:all:",
            "--platform",
            "any",
            "--implementation",
            "py",
            "--python-version",
            PYTHON_VERSION.rsplit(".", 1)[0],
            "--abi",
            "none",
            "--target",
            str(target),
            *config.dependencies,
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=180, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PyMobileError(f"Could not install app dependencies: {exc}") from exc
        if completed.returncode:
            raise PyMobileError(
                "Could not install pure-Python dependencies for Android CPython "
                f"{PYTHON_VERSION}: {(completed.stderr or completed.stdout)[-1200:]}",
                hint="Pin versions with universal wheels (py3-none-any); "
                "native extensions are not supported.",
            )
        _files(target)  # reject nonportable contents before replacing the cache
        marker_path = target / marker.name
        marker_path.write_text(json.dumps(key), encoding="utf-8")
        if cache.exists():
            shutil.rmtree(cache)
        os.replace(target, cache)
    return _files(cache)
