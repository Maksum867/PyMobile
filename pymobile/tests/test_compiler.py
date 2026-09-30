"""Tests for the compiler: manifest, collector, icons, packager and pipeline."""

from __future__ import annotations

import hashlib
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pymobile.compiler.cache import BuildCache, fingerprint_files
from pymobile.compiler.collector import collect_sources
from pymobile.compiler.icon import DENSITIES, prepare_icons
from pymobile.compiler.manifest import ANDROID_NS, build_manifest
from pymobile.compiler.packager import ApkPackager
from pymobile.compiler.pipeline import BuildPipeline, build_apk
from pymobile.compiler.scaffold import create_project, default_package, render, slugify
from pymobile.core.config import ProjectConfig
from pymobile.errors import ConfigError, ResourceError

ANDROID = f"{{{ANDROID_NS}}}"


class TestManifest:
    def test_is_well_formed(self, project: ProjectConfig) -> None:
        root = ET.fromstring(build_manifest(project))
        assert root.tag == "manifest"
        assert root.get("package") == "com.example.demo"

    def test_version_attributes(self, project: ProjectConfig) -> None:
        root = ET.fromstring(build_manifest(project))
        assert root.get(f"{ANDROID}versionName") == "0.1.0"
        assert root.get(f"{ANDROID}versionCode") == "1"

    def test_sdk_levels(self, project: ProjectConfig) -> None:
        sdk = ET.fromstring(build_manifest(project)).find("uses-sdk")
        assert sdk is not None
        assert sdk.get(f"{ANDROID}minSdkVersion") == "24"
        assert sdk.get(f"{ANDROID}targetSdkVersion") == "35"

    def test_permissions_deduplicated(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            root=tmp_path,
            permissions=[
                "android.permission.INTERNET",
                "android.permission.INTERNET",
                "android.permission.VIBRATE",
            ],
        )
        nodes = ET.fromstring(build_manifest(config)).findall("uses-permission")
        assert len(nodes) == 2

    def test_short_permission_names_expanded(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, permissions=["CAMERA"])
        node = ET.fromstring(build_manifest(config)).find("uses-permission")
        assert node is not None
        assert node.get(f"{ANDROID}name") == "android.permission.CAMERA"

    def test_launcher_intent_filter(self, project: ProjectConfig) -> None:
        root = ET.fromstring(build_manifest(project))
        category = root.find(".//intent-filter/category")
        assert category is not None
        assert category.get(f"{ANDROID}name") == "android.intent.category.LAUNCHER"

    def test_orientation_applied(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, orientation="landscape")
        activity = ET.fromstring(build_manifest(config)).find(".//activity")
        assert activity is not None
        assert activity.get(f"{ANDROID}screenOrientation") == "landscape"

    def test_special_characters_escaped(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, name='Tom & "Jerry" <app>')
        xml = build_manifest(config)
        assert "&amp;" in xml
        ET.fromstring(xml)  # must still parse


class TestCollector:
    def test_collects_python_files(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "helper.py").write_text("y = 2", encoding="utf-8")
        result = collect_sources(tmp_path, tmp_path / "main.py")
        assert result.count == 2
        assert result.total_bytes > 0

    def test_skips_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "main.pyc").write_bytes(b"\x00")
        assert collect_sources(tmp_path, tmp_path / "main.py").count == 1

    def test_exclude_globs(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text("x = 1", encoding="utf-8")
        result = collect_sources(tmp_path, tmp_path / "main.py", exclude=["tests/**"])
        assert result.count == 1

    def test_exclude_glob_semantics(self) -> None:
        """Regression: globs must follow gitignore semantics."""
        from pymobile.compiler.collector import _is_excluded

        # A pattern with no slash matches at any depth.
        assert _is_excluded(Path("pkg/test_foo.py"), ["test_*.py"])
        assert not _is_excluded(Path("pkg/helper.py"), ["test_*.py"])
        # '**/dir/**' matches root-level and nested __pycache__.
        assert _is_excluded(Path("__pycache__/x.pyc"), ["**/__pycache__/**"])
        assert _is_excluded(Path("sub/__pycache__/x.pyc"), ["**/__pycache__/**"])
        # A pattern with a slash is anchored to the root.
        assert _is_excluded(Path("build/x.py"), ["build/**"])
        assert not _is_excluded(Path("sub/build/x.py"), ["build/**"])
        assert _is_excluded(Path("sub/build/x.py"), ["**/build/**"])

    def test_assets_included(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.md").write_text("ignored", encoding="utf-8")
        names = {p.name for p in collect_sources(tmp_path, tmp_path / "main.py").files}
        assert names == {"main.py", "data.json"}

    def test_entrypoint_always_included(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        result = collect_sources(tmp_path, tmp_path / "main.py", exclude=["**/*.py"])
        assert result.entrypoint in result.files

    def test_missing_source_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Source directory"):
            collect_sources(tmp_path / "nope", tmp_path / "main.py")

    def test_missing_entrypoint(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Entry point"):
            collect_sources(tmp_path, tmp_path / "missing.py")

    def test_relative_paths(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        result = collect_sources(tmp_path, tmp_path / "main.py")
        assert list(result.relative()) == [Path("main.py")]


class TestIcons:
    def test_default_icon_used(self, tmp_path: Path) -> None:
        icons = prepare_icons(None, tmp_path)
        assert icons.is_default
        assert set(icons.densities) == set(DENSITIES)
        assert all(path.exists() for path in icons.files.values())

    def test_custom_icon(self, tmp_path: Path) -> None:
        source = tmp_path / "logo.png"
        _write_png(source)
        icons = prepare_icons(source, tmp_path / "res")
        assert not icons.is_default
        assert icons.source == source

    def test_missing_icon(self, tmp_path: Path) -> None:
        with pytest.raises(ResourceError, match="not found"):
            prepare_icons(tmp_path / "missing.png", tmp_path / "res")

    def test_unsupported_format(self, tmp_path: Path) -> None:
        bad = tmp_path / "logo.bmp"
        bad.write_bytes(b"BM123")
        with pytest.raises(ResourceError, match="Unsupported icon format"):
            prepare_icons(bad, tmp_path / "res")

    def test_empty_icon(self, tmp_path: Path) -> None:
        empty = tmp_path / "logo.png"
        empty.write_bytes(b"")
        with pytest.raises(ResourceError, match="empty"):
            prepare_icons(empty, tmp_path / "res")

    def test_sizes_match_densities(self, tmp_path: Path) -> None:
        pytest.importorskip("PIL")
        from PIL import Image as PILImage

        icons = prepare_icons(None, tmp_path)
        for density, path in icons.files.items():
            with PILImage.open(path) as image:
                assert image.size == (DENSITIES[density], DENSITIES[density])


class TestPackager:
    def test_creates_valid_zip(self, tmp_path: Path) -> None:
        source = tmp_path / "main.py"
        source.write_text("x = 1", encoding="utf-8")
        output = tmp_path / "out.apk"
        result = ApkPackager().build(output, manifest="<manifest/>", sources=[("main.py", source)])
        assert result.path.exists()
        with zipfile.ZipFile(output) as archive:
            assert archive.testzip() is None
            assert "AndroidManifest.xml" in archive.namelist()
            assert "assets/app/main.py" in archive.namelist()

    def test_deterministic_output(self, tmp_path: Path) -> None:
        source = tmp_path / "main.py"
        source.write_text("x = 1", encoding="utf-8")
        first = tmp_path / "a.apk"
        second = tmp_path / "b.apk"
        for output in (first, second):
            ApkPackager().build(output, manifest="<m/>", sources=[("main.py", source)])
        assert first.read_bytes() == second.read_bytes()

    def test_extra_entries(self, tmp_path: Path) -> None:
        output = tmp_path / "out.apk"
        ApkPackager().build(
            output, manifest="<m/>", sources=[], extra={"assets/meta.txt": b"hello"}
        )
        with zipfile.ZipFile(output) as archive:
            assert archive.read("assets/meta.txt") == b"hello"

    def test_images_are_stored_not_deflated(self, tmp_path: Path) -> None:
        image = tmp_path / "icon.png"
        _write_png(image)
        output = tmp_path / "out.apk"
        ApkPackager().build(output, manifest="<m/>", sources=[], resources={"res/icon.png": image})
        with zipfile.ZipFile(output) as archive:
            assert archive.getinfo("res/icon.png").compress_type == zipfile.ZIP_STORED

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        output = tmp_path / "out.apk"
        ApkPackager().build(output, manifest="<m/>", sources=[])
        assert not (tmp_path / "out.apk.tmp").exists()


class TestCache:
    def test_fingerprint_changes_with_content(self, tmp_path: Path) -> None:
        path = tmp_path / "a.py"
        path.write_text("x = 1", encoding="utf-8")
        first = fingerprint_files([path])
        path.write_text("x = 22222", encoding="utf-8")
        assert fingerprint_files([path]) != first

    def test_fingerprint_catches_same_second_same_size_edit(self, tmp_path: Path) -> None:
        """Regression: a whole-second mtime with an unchanged size used to hide
        an edit made twice within one clock second, returning a stale APK."""
        import os
        import time

        path = tmp_path / "a.py"
        path.write_text("AAAAA", encoding="utf-8")
        ts = int(time.time())
        os.utime(path, (ts, ts))
        first = fingerprint_files([path])
        # Same path, same byte size, same whole-second mtime — different content.
        path.write_text("BBBBB", encoding="utf-8")
        os.utime(path, (ts, ts))
        assert fingerprint_files([path]) != first

    def test_fingerprint_is_stable(self, tmp_path: Path) -> None:
        path = tmp_path / "a.py"
        path.write_text("x = 1", encoding="utf-8")
        assert fingerprint_files([path]) == fingerprint_files([path])

    def test_missing_files_ignored(self, tmp_path: Path) -> None:
        assert isinstance(fingerprint_files([tmp_path / "ghost.py"]), str)

    def test_save_and_check(self, tmp_path: Path) -> None:
        artifact = tmp_path / "app.apk"
        artifact.write_bytes(b"apk")
        cache = BuildCache(tmp_path)
        cache.save("abc", artifact)
        assert cache.is_fresh("abc") == artifact
        assert cache.is_fresh("other") is None

    def test_stale_when_artifact_deleted(self, tmp_path: Path) -> None:
        artifact = tmp_path / "app.apk"
        artifact.write_bytes(b"apk")
        cache = BuildCache(tmp_path)
        cache.save("abc", artifact)
        artifact.unlink()
        assert cache.is_fresh("abc") is None

    def test_corrupt_cache_ignored(self, tmp_path: Path) -> None:
        cache = BuildCache(tmp_path)
        cache.path.write_text("not json", encoding="utf-8")
        assert cache.load() == {}

    def test_clear(self, tmp_path: Path) -> None:
        cache = BuildCache(tmp_path)
        cache.save("abc", tmp_path / "x.apk")
        cache.clear()
        assert not cache.path.exists()


class TestPipeline:
    def test_build_produces_apk(self, project: ProjectConfig) -> None:
        result = build_apk(project)
        assert result.apk.exists()
        assert result.apk.name == "demo-app-0.1.0.apk"
        assert not result.cached
        assert result.size > 0

    def test_apk_contains_expected_entries(self, project: ProjectConfig) -> None:
        result = build_apk(project)
        with zipfile.ZipFile(result.apk) as archive:
            names = archive.namelist()
        assert "AndroidManifest.xml" in names
        assert "assets/pymobile.properties" in names
        assert any(name.startswith("res/mipmap-") for name in names)

    def test_optimize_ships_bytecode(self, project: ProjectConfig) -> None:
        project.optimize = True
        result = build_apk(project)
        with zipfile.ZipFile(result.apk) as archive:
            names = archive.namelist()
        assert "assets/app/main.pyc" in names
        assert "assets/app/main.py" not in names

    def test_no_optimize_ships_sources(self, project: ProjectConfig) -> None:
        project.optimize = False
        result = build_apk(project)
        with zipfile.ZipFile(result.apk) as archive:
            assert "assets/app/main.py" in archive.namelist()

    def test_second_build_is_cached(self, project: ProjectConfig) -> None:
        build_apk(project)
        second = build_apk(project)
        assert second.cached

    def test_cache_can_be_disabled(self, project: ProjectConfig) -> None:
        build_apk(project)
        assert not build_apk(project, use_cache=False).cached

    def test_build_is_reproducible(self, project: ProjectConfig) -> None:
        """Regression: two identical builds produced different APKs.

        The staged copy lives in a fresh ``pymobile-build-XXXX`` temp directory,
        so the bytecode recorded that absolute path, and the default TIMESTAMP
        invalidation also baked the copy's mtime into the ``.pyc`` header.
        """
        project.optimize = True
        first = hashlib.sha256(build_apk(project, use_cache=False).apk.read_bytes()).hexdigest()
        time.sleep(1.1)  # a later second → a different source mtime
        second = hashlib.sha256(build_apk(project, use_cache=False).apk.read_bytes()).hexdigest()
        assert first == second, "дві однакові збірки дали різні APK"

    def test_bytecode_leaks_no_build_paths(self, project: ProjectConfig) -> None:
        """A shipped ``.pyc`` must not name the machine that built it."""
        project.optimize = True
        result = build_apk(project, use_cache=False)
        with zipfile.ZipFile(result.apk) as archive:
            payload = archive.read("assets/app/main.pyc")
        assert b"pymobile-build-" not in payload
        assert b"/tmp/" not in payload
        assert b"app/main.py" in payload, "очікували відносний шлях app/main.py"

    def test_fingerprint_is_stable_across_processes(self, project: ProjectConfig) -> None:
        """Regression: PYTHONHASHSEED randomisation must not defeat the cache."""
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(
            f"""
            from pymobile.core.config import ProjectConfig
            from pymobile.compiler.pipeline import BuildPipeline

            config = ProjectConfig(
                name="Demo App", package="com.example.demo", root={str(project.root)!r}
            )
            pipeline = BuildPipeline(config)
            print(pipeline._fingerprint(pipeline._collect()))
            """
        )
        seen = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            ).stdout.strip()
            for seed in ("1", "2")
        }
        assert len(seen) == 1

    def test_cache_survives_a_new_process(self, project: ProjectConfig) -> None:
        """A rebuild from a fresh interpreter must hit the cache."""
        import subprocess
        import sys
        import textwrap

        build_apk(project)
        script = textwrap.dedent(
            f"""
            from pymobile.core.config import ProjectConfig
            from pymobile.compiler.pipeline import build_apk

            config = ProjectConfig(
                name="Demo App", package="com.example.demo", root={str(project.root)!r}
            )
            print(build_apk(config).cached)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert completed.stdout.strip() == "True"

    def test_cache_invalidated_on_change(self, project: ProjectConfig) -> None:
        build_apk(project)
        (project.root / "main.py").write_text("print('changed')\n", encoding="utf-8")
        assert not build_apk(project).cached

    def test_syntax_error_still_packages_source(self, project: ProjectConfig) -> None:
        # The warning only fires on the bytecode-compile path, which requires
        # optimize=True (the default is to ship sources directly).
        project.optimize = True
        (project.root / "broken.py").write_text("def (:\n", encoding="utf-8")
        result = build_apk(project)
        assert any("broken.py" in warning for warning in result.warnings)
        with zipfile.ZipFile(result.apk) as archive:
            assert "assets/app/broken.py" in archive.namelist()

    def test_warns_about_missing_internet(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "from pymobile import HttpClient\nclient = HttpClient()\n", encoding="utf-8"
        )
        config = ProjectConfig(root=tmp_path, permissions=[])
        result = build_apk(config)
        assert any("INTERNET" in warning for warning in result.warnings)

    def test_no_internet_warning_without_http_usage(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        config = ProjectConfig(root=tmp_path, permissions=[])
        result = build_apk(config)
        assert not any("INTERNET" in warning for warning in result.warnings)

    def test_warns_about_notifications_on_new_sdk(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "def done(app):\n    app.notify('Done')\n", encoding="utf-8"
        )
        config = ProjectConfig(root=tmp_path, target_sdk=34)
        result = build_apk(config)
        assert any("POST_NOTIFICATIONS" in warning for warning in result.warnings)

    def test_no_notifications_warning_for_an_app_that_never_notifies(
        self, tmp_path: Path
    ) -> None:
        # The stock config targets SDK 35 without POST_NOTIFICATIONS; that is only
        # a problem for code that posts a notification.
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        config = ProjectConfig(root=tmp_path, target_sdk=34)
        result = build_apk(config)
        assert not any("POST_NOTIFICATIONS" in warning for warning in result.warnings)

    def test_custom_icon_flagged(self, project: ProjectConfig) -> None:
        icon = project.root / "logo.png"
        _write_png(icon)
        project.icon = "logo.png"
        assert not build_apk(project).icon_is_default

    def test_missing_entrypoint_error(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path)
        with pytest.raises(ConfigError, match="Entry point"):
            build_apk(config)

    def test_stage_callback_and_timings(self, project: ProjectConfig) -> None:
        stages: list[str] = []
        result = BuildPipeline(project, on_stage=stages.append).run()
        assert stages == ["validate", "collect", "compile", "icons", "manifest", "package"]
        assert len(result.timings) == 6

    def test_summary_text(self, project: ProjectConfig) -> None:
        assert "KB" in build_apk(project).summary()


class TestScaffold:
    def test_creates_runnable_project(self, tmp_path: Path) -> None:
        target = tmp_path / "myapp"
        result = create_project(target, "My App")
        names = {path.name for path in result.files}
        assert names == {"main.py", "pymobile.toml", "README.md", ".gitignore"}

    def test_placeholders_substituted(self, tmp_path: Path) -> None:
        create_project(tmp_path / "app", "Cool App", package="com.cool.app")
        text = (tmp_path / "app" / "pymobile.toml").read_text(encoding="utf-8")
        assert 'name = "Cool App"' in text
        assert 'package = "com.cool.app"' in text
        assert "{{" not in text

    def test_generated_project_builds(self, tmp_path: Path) -> None:
        from pymobile.core.config import load_config

        target = tmp_path / "app"
        create_project(target, "Generated App")
        result = build_apk(load_config(target))
        assert result.apk.exists()

    def test_generated_main_is_valid_python(self, tmp_path: Path) -> None:
        create_project(tmp_path / "app", "App")
        source = (tmp_path / "app" / "main.py").read_text(encoding="utf-8")
        compile(source, "main.py", "exec")

    def test_non_empty_directory_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "existing.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ConfigError, match="not empty"):
            create_project(tmp_path, "App")

    def test_force_overrides(self, tmp_path: Path) -> None:
        (tmp_path / "existing.txt").write_text("x", encoding="utf-8")
        assert create_project(tmp_path, "App", force=True).files

    def test_slugify_and_package(self) -> None:
        assert slugify("My Cool App!") == "mycoolapp"
        assert slugify("!!!") == "app"
        assert slugify("Скарбничка") == "skarbnychka"
        assert default_package("My App") == "org.pymobile.myapp"

    def test_render_keeps_unknown_placeholders(self) -> None:
        assert render("{{a}}-{{b}}", {"a": "1"}) == "1-{{b}}"


def _write_png(path: Path) -> None:
    """Write a small valid PNG (Pillow when available, raw bytes otherwise)."""
    try:
        from PIL import Image

        Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(path)
    except ImportError:  # pragma: no cover - Pillow is in the dev extra
        path.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001080600000"
                "01f15c4890000000a49444154789c6300010000050001"
                "0d0a2db40000000049454e44ae426082"
            )
        )
