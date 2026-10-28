"""Rebuild and attest the bundled launcher, or check its source provenance.

Run ``python -m pymobile.compiler.rebuild_dex`` with an Android SDK/JDK, and
``python -m pymobile.compiler.rebuild_dex --check`` in SDK-free release checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from ..core.config import ProjectConfig
from ..errors import PyMobileError
from ..resources import resource_path
from .backends.native import NativeBackend
from .toolchain import find_toolchain


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_digest(path: Path) -> str:
    """Hash Java content independently of Git's LF/CRLF checkout conversion.

    Only physical CRLF line endings are normalised. Whitespace, source edits,
    lone CR bytes and the binary dex remain significant.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_bundled_dex() -> None:
    """Reject a stale source/dex pairing, without claiming device validation."""
    target = resource_path("android", "prebuilt", "arm64-v8a", "classes.dex")
    manifest = target.with_suffix(".sources.json")
    if not manifest.is_file():
        raise PyMobileError("Missing dex provenance; rebuild the bundled launcher.")
    report = json.loads(manifest.read_text(encoding="utf-8"))
    actual = {
        p.name: _source_digest(p) for p in sorted(resource_path("android", "java").glob("*.java"))
    }
    if report.get("schema") != 1 or report.get("sources") != actual:
        raise PyMobileError("Bundled dex sources changed; run the dex rebuild before release.")
    if report.get("dex_sha256") != _digest(target):
        raise PyMobileError("Bundled dex hash differs from its provenance manifest.")


def rebuild(output: Path | None = None) -> Path:
    """Compile every Java input and record CRLF-normalised source hashes."""
    toolchain = find_toolchain()
    toolchain.verify(require_javac=True)
    target = output or resource_path("android", "prebuilt", "arm64-v8a", "classes.dex")
    with tempfile.TemporaryDirectory(prefix="pymobile-dex-") as temporary:
        work = Path(temporary)
        project = work / "project"
        project.mkdir()
        (project / "main.py").write_text("# Compiler-only launcher rebuild\n", encoding="utf-8")
        config = ProjectConfig(root=project)
        backend = NativeBackend(config, toolchain, work / "unused-runtime")
        dex = backend._compile_java_from_source(work / "compile")
        sources = {
            p.name: _source_digest(p) for p in sorted((work / "compile/java").glob("*.java"))
        }
        expected = {p.name for p in resource_path("android", "java").glob("*.java")}
        if sources.keys() != expected:
            raise PyMobileError("Native compilation did not include every bundled Java source.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dex, target)
        report = {
            "schema": 1,
            "min_sdk": config.effective_min_sdk,
            "build_tools": toolchain.build_tools.name,
            "sources": sources,
            "dex_sha256": _digest(target),
        }
        target.with_suffix(".sources.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check bundled dex source provenance")
    parser.add_argument("--output", type=Path, help="write a separate dex and provenance manifest")
    args = parser.parse_args()
    if args.check and args.output:
        parser.error("--check and --output cannot be combined")
    try:
        if args.check:
            check_bundled_dex()
            print("Bundled dex matches its recorded source hashes.")
        else:
            print(f"Rebuilt {rebuild(args.output)}")
    except (PyMobileError, OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
