"""Final P2/P6 gates, including optional real native SDK builds (no emulator)."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from pymobile import cli
from pymobile.compiler.backends.native import framework_asset_files
from pymobile.compiler.cache import BuildCache
from pymobile.compiler.pipeline import BuildPipeline, build_apk
from pymobile.compiler.rebuild_dex import check_bundled_dex
from pymobile.compiler.toolchain import find_toolchain
from pymobile.errors import PyMobileError

APP = """from pymobile import App, Label, Screen
class Home(Screen):
    def build(self):
        return Label("Native smoke")
App("Native smoke").run(Home())
"""
ROOT = Path(__file__).resolve().parents[1]


def test_plain_native_build_rejects_empty_startup_before_output(project):
    pipeline = BuildPipeline(project, native=True)
    assert pipeline.check_startup is True
    with pytest.raises(PyMobileError, match="rendered nothing"):
        pipeline.run()
    assert not project.output_path.exists()


def test_convenience_api_also_checks_native_startup(project):
    with pytest.raises(PyMobileError, match="rendered nothing"):
        build_apk(project, native=True)


def test_native_cache_cannot_bypass_startup_validation(project):
    pipeline = BuildPipeline(project, native=True)
    artifact = project.output_path / project.apk_name
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"synthetic old cache")
    sources = pipeline._collect()
    BuildCache(project.output_path).save(
        pipeline._fingerprint(sources),
        artifact,
        mode=pipeline.build_mode(),
        signer=pipeline.signer_identity(),
        icon="1",
    )
    with pytest.raises(PyMobileError, match="rendered nothing"):
        pipeline.run()
    assert artifact.read_bytes() == b"synthetic old cache"


def test_cli_native_defaults_to_checking(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load", lambda args: project)
    assert cli.main(["build", "--native"]) == 1
    assert "rendered nothing" in capsys.readouterr().err


def test_skip_is_explicit_mutually_exclusive_and_warned(project):
    parser = cli.build_parser()
    assert parser.parse_args(["build", "--native"]).check_startup is None
    assert parser.parse_args(["build", "--native", "--skip-startup-check"]).check_startup is False
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--check-startup", "--skip-startup-check"])
    pipeline = BuildPipeline(project, native=True, check_startup=False)
    pipeline._validate()
    assert any("explicitly skipped" in warning for warning in pipeline.warnings)


def test_structural_packaging_stays_non_executing_by_default(project):
    result = BuildPipeline(project, use_cache=False).run()
    assert not result.native
    assert result.apk.is_file()


def test_split_renderer_files_have_size_guards():
    paths = [
        ROOT / "cli.py",
        ROOT / "cli_parser.py",
        ROOT / "cli_preview.py",
        ROOT / "cli_device.py",
    ]
    paths += list((ROOT / "core/ui").glob("_mockup*.py"))
    paths += list((ROOT / "resources/android/java").glob("View*.java"))
    for path in paths:
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 1000, path.name


def test_bundled_dex_provenance_and_collaborators():
    check_bundled_dex()
    dex = (ROOT / "resources/android/prebuilt/arm64-v8a/classes.dex").read_bytes()
    for name in (
        "ViewStyles",
        "ViewContainers",
        "ViewInputs",
        "ViewControls",
        "ViewLists",
        "ViewUpdates",
        "ViewOverlays",
        "ViewGestures",
        "FlowViews",
    ):
        assert name.encode() in dex


def test_provenance_detects_stale_sources(tmp_path, monkeypatch):
    from pymobile.compiler import rebuild_dex

    java = tmp_path / "java"
    java.mkdir()
    (java / "A.java").write_text("class A {}")
    target = tmp_path / "classes.dex"
    target.write_bytes(b"fake")
    target.with_suffix(".sources.json").write_text(json.dumps({"schema": 1, "sources": {}}))
    monkeypatch.setattr(
        rebuild_dex, "resource_path", lambda *parts: java if parts[-1] == "java" else target
    )
    with pytest.raises(PyMobileError, match="sources changed"):
        rebuild_dex.check_bundled_dex()


def test_new_desktop_modules_are_not_shipped_as_android_python():
    shipped = {path.relative_to(ROOT).as_posix() for path in framework_asset_files()}
    assert not {"cli.py", "cli_preview.py", "cli_device.py", "cli_parser.py"} & shipped
    assert not any("mockup" in path for path in shipped)


@pytest.mark.skipif(
    not os.environ.get("PYMOBILE_NATIVE_TESTS"), reason="opt-in real Android SDK build"
)
def test_real_native_source_and_prebuilt_builds(project, monkeypatch):
    project.entrypoint_path.write_text(APP)
    toolchain = find_toolchain()
    toolchain.verify(require_javac=True)
    monkeypatch.setenv("PYMOBILE_BUILD_JAVA", "1")
    result = BuildPipeline(project, native=True, use_cache=False).run()
    assert result.native and not result.cached
    with zipfile.ZipFile(result.apk) as apk:
        dex = apk.read("classes.dex")
        for name in (b"ViewGestures", b"ViewStyles", b"FlowViews"):
            assert name in dex
        assert "AndroidManifest.xml" in apk.namelist()
        assert "lib/arm64-v8a/libpymobile.so" in apk.namelist()
    signature = subprocess.run(
        [str(toolchain.apksigner), "verify", "--verbose", str(result.apk)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Verified using v2 scheme (APK Signature Scheme v2): true" in signature.stdout
    assert "Verified using v3 scheme (APK Signature Scheme v3): true" in signature.stdout
    cached = BuildPipeline(project, native=True).run()
    assert cached.cached and cached.native
    monkeypatch.setenv("PYMOBILE_BUILD_JAVA", "0")
    fallback = BuildPipeline(project, native=True, use_cache=False).run()
    with zipfile.ZipFile(fallback.apk) as apk:
        assert (
            apk.read("classes.dex")
            == (ROOT / "resources/android/prebuilt/arm64-v8a/classes.dex").read_bytes()
        )


def test_bundled_provenance_accepts_crlf_but_rejects_source_edits(tmp_path, monkeypatch):
    from pymobile.compiler import rebuild_dex

    java = tmp_path / "java"
    java.mkdir()
    for source in (ROOT / "resources/android/java").glob("*.java"):
        data = source.read_bytes().replace(b"\r\n", b"\n")
        (java / source.name).write_bytes(data.replace(b"\n", b"\r\n"))
    original = ROOT / "resources/android/prebuilt/arm64-v8a/classes.dex"
    target = tmp_path / "classes.dex"
    target.write_bytes(original.read_bytes())
    target.with_suffix(".sources.json").write_bytes(
        original.with_suffix(".sources.json").read_bytes()
    )
    monkeypatch.setattr(
        rebuild_dex, "resource_path", lambda *parts: java if parts[-1] == "java" else target
    )
    rebuild_dex.check_bundled_dex()
    changed = next(java.glob("*.java"))
    changed.write_bytes(changed.read_bytes() + b"\r\n// real source edit\r\n")
    with pytest.raises(PyMobileError, match="sources changed"):
        rebuild_dex.check_bundled_dex()


def test_provenance_dex_hash_remains_byte_exact(tmp_path, monkeypatch):
    import hashlib

    from pymobile.compiler import rebuild_dex

    java = tmp_path / "java"
    java.mkdir()
    source = b"class A {}\n"
    (java / "A.java").write_bytes(source.replace(b"\n", b"\r\n"))
    target = tmp_path / "classes.dex"
    binary = b"dex\r\n\x00binary"
    target.write_bytes(binary)
    target.with_suffix(".sources.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "sources": {"A.java": hashlib.sha256(source).hexdigest()},
                "dex_sha256": hashlib.sha256(binary).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(
        rebuild_dex, "resource_path", lambda *parts: java if parts[-1] == "java" else target
    )
    rebuild_dex.check_bundled_dex()
    target.write_bytes(binary.replace(b"\r\n", b"\n"))
    with pytest.raises(PyMobileError, match="dex hash differs"):
        rebuild_dex.check_bundled_dex()


def test_rebuild_records_lf_hashes_for_crlf_compile_inputs(tmp_path, monkeypatch):
    import hashlib
    from unittest.mock import Mock

    from pymobile.compiler import rebuild_dex

    java = tmp_path / "java"
    java.mkdir()
    source = b"class A {}\n"
    (java / "A.java").write_bytes(source)
    target = tmp_path / "output/classes.dex"
    binary = b"dex\r\n\x00binary"
    toolchain = Mock(build_tools=Path("35.0.0"))

    def compile_java(work):
        (work / "java").mkdir(parents=True)
        (work / "java/A.java").write_bytes(source.replace(b"\n", b"\r\n"))
        dex = work / "classes.dex"
        dex.write_bytes(binary)
        return dex

    backend = Mock()
    backend._compile_java_from_source.side_effect = compile_java
    monkeypatch.setattr(rebuild_dex, "NativeBackend", Mock(return_value=backend))
    monkeypatch.setattr(rebuild_dex, "find_toolchain", lambda: toolchain)
    monkeypatch.setattr(
        rebuild_dex, "resource_path", lambda *parts: java if parts[-1] == "java" else target
    )
    assert rebuild_dex.rebuild(target) == target
    report = json.loads(target.with_suffix(".sources.json").read_text())
    assert report["sources"] == {"A.java": hashlib.sha256(source).hexdigest()}
    assert report["dex_sha256"] == hashlib.sha256(binary).hexdigest()
    rebuild_dex.check_bundled_dex()
