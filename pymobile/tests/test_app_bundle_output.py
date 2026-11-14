"""Google Play takes an ``.aab``: the bundle a native build now also writes.

The audit's A10 was blunt — an app built with PyMobile could not be uploaded at
all. These tests pin the layout Play expects, the ``BundleConfig.pb`` encoding
bundletool has to accept, and the refusal to pretend a structural package can
become a bundle. The end-to-end test runs bundletool itself when a JDK, an SDK
and bundletool.jar are present, because "it is a ZIP with the right names" is
not the same claim as "Play accepts it".
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from pymobile.compiler.bundle import (
    COMPRESSION_SUFFIXES,
    build_bundle,
    bundletool_path,
    validate_bundle,
    write_bundle_config,
)
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.scaffold import create_project
from pymobile.compiler.sdk_installer import SDK_PLATFORM
from pymobile.core.config import ProjectConfig
from pymobile.errors import PyMobileError

REPO = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path, name: str = "Bundled") -> ProjectConfig:
    create_project(tmp_path, name)
    return ProjectConfig(root=tmp_path, name=name.lower(), package=f"com.example.{name.lower()}")


def _fixture_bundle(tmp_path: Path) -> Path:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_bytes(b"\x08\x01")
    dex = tmp_path / "classes.dex"
    dex.write_bytes(b"dex\n035\x00")
    assets = tmp_path / "assets"
    (assets / "app").mkdir(parents=True)
    (assets / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    libraries = tmp_path / "lib" / "arm64-v8a"
    libraries.mkdir(parents=True)
    (libraries / "libpymobile.so").write_bytes(b"\x7fELF")
    return build_bundle(
        tmp_path / "out.aab",
        manifest=manifest,
        dex=dex,
        assets=assets,
        libraries=tmp_path / "lib",
    )


# -- the bundle Play expects ---------------------------------------------------
def test_the_bundle_has_the_module_layout_play_reads(tmp_path: Path) -> None:
    bundle = _fixture_bundle(tmp_path)
    with zipfile.ZipFile(bundle.path) as archive:
        names = set(archive.namelist())
    assert "BundleConfig.pb" in names
    assert "base/manifest/AndroidManifest.xml" in names
    assert "base/dex/classes.dex" in names
    assert "base/assets/app/main.py" in names
    assert "base/lib/arm64-v8a/libpymobile.so" in names


def test_native_code_and_dex_are_stored_uncompressed(tmp_path: Path) -> None:
    # bundletool (and the platform) require these to stay uncompressed.
    bundle = _fixture_bundle(tmp_path)
    with zipfile.ZipFile(bundle.path) as archive:
        for name in ("base/dex/classes.dex", "base/lib/arm64-v8a/libpymobile.so"):
            assert archive.getinfo(name).compress_type == zipfile.ZIP_STORED


def test_the_bundle_is_byte_identical_between_runs(tmp_path: Path) -> None:
    first = _fixture_bundle(tmp_path / "one" if (tmp_path / "one").mkdir() is None else tmp_path)
    second = _fixture_bundle(tmp_path / "two" if (tmp_path / "two").mkdir() is None else tmp_path)
    assert first.path.read_bytes() == second.path.read_bytes()


def test_bundle_config_is_a_well_formed_protobuf() -> None:
    """Decode the hand-encoded message with a bare varint reader."""

    def read_varint(data: bytes, position: int) -> tuple[int, int]:
        value = 0
        shift = 0
        while True:
            byte = data[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, position
            shift += 7

    data = write_bundle_config()
    fields: dict[int, bytes | int] = {}
    position = 0
    while position < len(data):
        key, position = read_varint(data, position)
        number, wire = key >> 3, key & 0x7
        if wire == 2:
            length, position = read_varint(data, position)
            fields[number] = data[position : position + length]
            position += length
        elif wire == 0:
            value, position = read_varint(data, position)
            fields[number] = value
        else:  # a wire type BundleConfig never uses
            raise AssertionError(f"unexpected wire type {wire} in BundleConfig.pb")
    assert 1 in fields and 3 in fields, sorted(fields)
    compression = fields[3]
    assert isinstance(compression, bytes)
    assert b".dex" in compression and b".so" in compression


def test_the_compression_list_covers_dex_and_native_libraries() -> None:
    assert ".dex" in COMPRESSION_SUFFIXES
    assert ".so" in COMPRESSION_SUFFIXES


# -- wiring into a build ------------------------------------------------------
def test_a_structural_build_cannot_be_a_bundle(tmp_path: Path) -> None:
    config = _project(tmp_path)
    with pytest.raises(PyMobileError) as caught:
        BuildPipeline(config, use_cache=False, bundle=True).run()
    assert "needs a native build" in str(caught.value)


def test_bundle_is_its_own_cache_mode(tmp_path: Path) -> None:
    config = _project(tmp_path)
    plain = BuildPipeline(config, use_cache=False, native=True).build_mode()
    with_bundle = BuildPipeline(config, use_cache=False, native=True, bundle=True).build_mode()
    assert plain != with_bundle
    assert "+bundle" in with_bundle


def test_the_default_target_sdk_is_what_play_requires(tmp_path: Path) -> None:
    config = ProjectConfig(root=tmp_path)
    assert config.target_sdk == SDK_PLATFORM == 36


def test_the_build_compiles_against_the_installed_platform(tmp_path: Path) -> None:
    """The toolchain compiles against SDK_PLATFORM, not against target_sdk."""
    from pymobile.compiler.toolchain import find_toolchain

    try:
        toolchain = find_toolchain()
    except Exception:  # no SDK on this machine: nothing to compare
        pytest.skip("no Android SDK available")
    assert toolchain.platform_jar.parent.name == f"android-{SDK_PLATFORM}"


# -- end to end, when the tools exist -----------------------------------------
def _bundletool() -> Path | None:
    candidates = []
    override = os.environ.get("PYMOBILE_BUNDLETOOL")
    if override:
        candidates.append(Path(override))
    candidates += [
        Path.home() / ".andro" / "bundletool.jar",
        Path.home() / ".pymobile" / "bundletool.jar",
        REPO / "tools" / "bundletool.jar",
    ]
    return bundletool_path(candidates)


def test_bundletool_accepts_a_real_build(tmp_path: Path) -> None:
    """A native ``--bundle`` build, validated by bundletool itself.

    Skipped without an SDK, a JDK and bundletool.jar — the three things a
    release machine has and a laptop may not.
    """
    jar = _bundletool()
    if jar is None:
        pytest.skip("bundletool.jar not found")
    java = shutil.which("java")
    if java is None:
        pytest.skip("no java on PATH")
    try:
        from pymobile.compiler.toolchain import find_toolchain

        toolchain = find_toolchain()
        toolchain.verify(require_javac=True)
    except Exception:
        pytest.skip("no Android SDK/JDK available")

    config = _project(tmp_path, "Realbundle")
    result = BuildPipeline(config, use_cache=False, native=True, bundle=True).run()
    bundle = result.apk.with_suffix(".aab")
    assert bundle.is_file(), "the build did not write an .aab next to the APK"

    universal = tmp_path / "universal.apks"
    validate_bundle(bundle, java=Path(java), bundletool=jar, universal=universal)
    assert universal.is_file() and universal.stat().st_size > 0


# -- A17: more than one ABI in a single APK ------------------------------------
def test_a_multi_abi_build_packages_every_architecture(tmp_path: Path) -> None:
    """``abis = ["arm64-v8a", "x86_64"]`` used to ship arm64 only.

    Silently ignoring the second ABI meant the same APK could not start on an
    emulator, and nothing said so. Both architectures now land in one file.
    """
    import os
    import shutil

    if shutil.which("java") is None and not os.environ.get("JAVA_HOME"):
        pytest.skip("no java on PATH")
    try:
        from pymobile.compiler.toolchain import find_toolchain

        toolchain = find_toolchain()
        toolchain.verify(require_javac=True)
        from pymobile.compiler.runtime import ensure_runtime

        ensure_runtime("arm64-v8a")
        ensure_runtime("x86_64")
    except Exception:
        pytest.skip("no Android SDK or runtime for both ABIs")

    config = _project(tmp_path, "Twoabis")
    configure = tmp_path / "pymobile.toml"
    text = configure.read_text(encoding="utf-8")
    configure.write_text(
        text.replace('abis = ["arm64-v8a"]', 'abis = ["arm64-v8a", "x86_64"]'), encoding="utf-8"
    )
    config = ProjectConfig(
        root=tmp_path, name="twoabis", package="com.example.twoabis", abis=["arm64-v8a", "x86_64"]
    )
    result = BuildPipeline(config, use_cache=False, native=True).run()
    with zipfile.ZipFile(result.apk) as archive:
        names = archive.namelist()
    assert any(name.startswith("lib/arm64-v8a/") for name in names)
    assert any(name.startswith("lib/x86_64/") for name in names)
    # Both ABIs sit in one file, which is worth saying out loud.
    assert any("arm64-v8a, x86_64" in warning for warning in result.warnings)
