"""Android App Bundle (``.aab``) output.

Google Play stopped accepting APKs for new apps and updates: it wants an
``.aab`` and generates per-device APKs from it. PyMobile built APKs only, so a
finished, signed, installable app could not be uploaded at all.

An app bundle is a ZIP with a fixed layout::

    BundleConfig.pb            protobuf settings (bundletool validates the rest)
    base/manifest/AndroidManifest.xml
    base/dex/classes.dex
    base/assets/…              every asset the APK carries under assets/
    base/lib/<abi>/…           native libraries
    base/res/…                 compiled resources (the aapt2 output)
    base/resources.pb          resource table
    BUNDLE-METADATA/…          optional

The files themselves are the ones a native build already produces; this module
only arranges them in that layout, writes the ``BundleConfig.pb`` aapt2 does not
emit by itself, and then runs ``bundletool build-apks --mode=universal`` to prove
the bundle is valid and installable — the only check that matters to a store
upload.

The protobuf for ``BundleConfig`` is encoded by hand (a handful of fields, no
runtime dependency on ``protobuf``); ``bundletool validate`` is what proves the
encoding is right, and the test suite runs it when a JDK is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import PyMobileError
from ..log import get_logger

__all__ = ["BundleResult", "build_bundle", "write_bundle_config", "COMPRESSION_SUFFIXES"]

_log = get_logger("compiler.bundle")

#: Suffixes bundletool requires to stay uncompressed in the bundle, because the
#: platform maps them and reads them directly (native libraries and the dex).
COMPRESSION_SUFFIXES = (".so", ".dex")


@dataclass(frozen=True, slots=True)
class BundleResult:
    """The written ``.aab`` and what went into it."""

    path: Path
    size: int
    entries: int
    universal_apk: Path | None = None


def _varint(value: int) -> bytes:
    """Protocol-buffers base-128 varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_bytes(number: int, payload: bytes) -> bytes:
    """A length-delimited field (wire type 2)."""
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _field_varint(number: int, value: int) -> bytes:
    """A varint field (wire type 0)."""
    return _varint(number << 3) + _varint(value)


def write_bundle_config(*, version_code: int = 1) -> bytes:
    """Encode ``BundleConfig.pb``.

    ``BundleConfig`` field 1 is ``bundletool`` (a message holding the version
    that wrote the bundle), field 2 ``optimizations`` (empty: no splits are
    configured), and ``compression`` (field 3) lists the suffixes that must stay
    uncompressed. ``version_code`` is not part of ``BundleConfig`` — it lives in
    the manifest — and is accepted so a caller cannot silently drop it.
    """
    del version_code  # the manifest carries it; kept for a stable signature
    bundletool = _field_bytes(1, _field_bytes(1, b"1.18.1")) + _field_bytes(2, b"0.15.1")
    compression = b"".join(
        _field_bytes(1, suffix.encode("ascii")) for suffix in COMPRESSION_SUFFIXES
    )
    # field 4 is `uncompress_native_libraries`; 5 `master_resources`; both are
    # left unset: the defaults are what a store expects.
    body = _field_bytes(1, bundletool) + _field_bytes(3, compression) + _field_varint(6, 1)
    return body


def _copy_tree(source: Path, target: Path) -> int:
    """Copy every file under ``source`` into ``target``; return the count."""
    if not source.is_dir():
        return 0
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        count += 1
    return count


def _run_bundletool(java: Path, bundletool: Path, *args: str) -> str:
    """Run a bundletool command and return its stdout."""
    result = subprocess.run(
        [str(java), "-jar", str(bundletool), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        raise PyMobileError(
            "bundletool rejected the app bundle",
            hint="\n".join(detail) or "run with --verbose for the whole output",
        )
    return result.stdout


def build_bundle(
    output: Path,
    *,
    manifest: Path,
    dex: Path,
    assets: Path,
    libraries: Path | None = None,
    resources: Path | None = None,
    resource_table: Path | None = None,
) -> BundleResult:
    """Arrange a native build's artifacts into an ``.aab``.

    ``manifest`` is the *binary* manifest aapt2 produced (not the XML source):
    a bundle stores the same compiled form an APK does.
    """
    import zipfile

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    entries = 0
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:

            def write(name: str, data: bytes, *, store: bool = False) -> None:
                nonlocal entries
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)
                entries += 1

            write("BundleConfig.pb", write_bundle_config(), store=False)
            write("base/manifest/AndroidManifest.xml", manifest.read_bytes())
            write("base/dex/classes.dex", dex.read_bytes(), store=True)

            if resource_table is not None and resource_table.is_file():
                write("base/resources.pb", resource_table.read_bytes())

            if assets.is_dir():
                for path in sorted(assets.rglob("*")):
                    if not path.is_file():
                        continue
                    name = "base/assets/" + path.relative_to(assets).as_posix()
                    store = path.suffix.lower() in COMPRESSION_SUFFIXES
                    write(name, path.read_bytes(), store=store)

            if libraries is not None and libraries.is_dir():
                for path in sorted(libraries.rglob("*")):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(libraries)
                    # A bundle keeps native code in base/lib/<abi>/, exactly
                    # where the platform loads it from inside the APK.
                    write(f"base/lib/{relative.as_posix()}", path.read_bytes(), store=True)

            if resources is not None and resources.is_dir():
                for path in sorted(resources.rglob("*")):
                    if not path.is_file():
                        continue
                    write(
                        "base/" + path.relative_to(resources).as_posix(),
                        path.read_bytes(),
                        store=path.suffix.lower() in COMPRESSION_SUFFIXES,
                    )

        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    size = output.stat().st_size
    _log.debug("wrote %s (%d entries, %d bytes)", output.name, entries, size)
    return BundleResult(path=output, size=size, entries=entries)


def validate_bundle(
    bundle: Path, *, java: Path, bundletool: Path, universal: Path | None = None
) -> Path | None:
    """Ask bundletool whether the bundle is acceptable, and build a universal APK.

    Returns the universal APK when requested. ``bundletool build-apks`` reads
    the whole bundle — the protobuf, the manifest, every split — so a bundle
    that satisfies it is one Play can accept; a plain ``zipfile`` check proves
    only that the ZIP is not corrupt.
    """
    _run_bundletool(java, bundletool, "validate", "--bundle", str(bundle))
    if universal is None:
        return None
    universal.parent.mkdir(parents=True, exist_ok=True)
    _run_bundletool(
        java,
        bundletool,
        "build-apks",
        "--bundle",
        str(bundle),
        "--output",
        str(universal),
        "--mode=universal",
        "--overwrite",
    )
    return universal


def bundletool_path(candidates: list[Path]) -> Path | None:
    """The first ``bundletool*.jar`` that exists among ``candidates``."""
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def describe(bundle: BundleResult) -> str:
    """One-line summary for the CLI."""
    return json.dumps(
        {"aab": str(bundle.path), "size": bundle.size, "entries": bundle.entries},
        ensure_ascii=False,
    )
