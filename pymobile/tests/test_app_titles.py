"""The App name wins for previews and becomes the installed Android title."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import App, Label, Screen
from pymobile.cli import main
from pymobile.compiler.backends.native import NativeBackend
from pymobile.compiler.manifest import build_manifest
from pymobile.compiler.scaffold import create_project
from pymobile.compiler.toolchain import Toolchain
from pymobile.core.bridge.stub import StubBridge
from pymobile.core.config import ProjectConfig


class Home(Screen):
    def build(self) -> Label:
        return Label("hello")


class NativeRecorder(StubBridge):
    native_widgets = True
    accepts_theme = True


def test_native_render_payload_carries_the_runtime_app_name(tmp_path: Path) -> None:
    bridge = NativeRecorder(verbose=False)
    app = App("Notes", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    app.run(Home())
    try:
        assert bridge.last_tree is not None
        assert bridge.last_tree["app_title"] == "Notes"
    finally:
        app.stop()


def test_desktop_run_uses_app_name_instead_of_toml_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_project(tmp_path, "TOML Name")
    (tmp_path / "main.py").write_text(
        "from pymobile import App, Label, Screen\n"
        "class Home(Screen):\n"
        "    def build(self): return Label('hello')\n"
        "App('Notes').run(Home())\n",
        encoding="utf-8",
    )

    assert main(["-c", str(tmp_path), "run"]) == 0
    output = capsys.readouterr().out
    assert "Notes" in output
    assert "TOML Name" not in output


def test_literal_app_name_overrides_toml_name_in_android_labels_and_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "from pymobile import App, Label, Screen\n"
        "class Home(Screen):\n"
        "    def build(self): return Label('hello')\n"
        "App('Notes').run(Home())\n",
        encoding="utf-8",
    )
    config = ProjectConfig(root=tmp_path, name="TOML Name", package="com.example.notes")
    toolchain = Toolchain(
        sdk=tmp_path,
        build_tools=tmp_path / "build-tools",
        platform_jar=tmp_path / "android.jar",
        java_home=tmp_path / "jdk",
    )
    backend = NativeBackend(config, toolchain, tmp_path / "runtime")

    assert backend.app_name == "Notes"
    manifest = build_manifest(
        config,
        activity="org.pymobile.app.MainActivity",
        app_name=backend.app_name,
    )
    assert 'android:label="Notes"' in manifest
    backend.set_entrypoint("main.py")
    assert b"name=Notes\n" in backend._asset_metadata()


def test_dynamic_app_name_keeps_toml_as_launcher_label_fallback(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from pymobile import App\nAPP_NAME = 'Notes'\nApp(APP_NAME).run()\n",
        encoding="utf-8",
    )
    backend = NativeBackend(
        ProjectConfig(root=tmp_path, name="TOML Name"),
        Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "build-tools",
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
        ),
        tmp_path / "runtime",
    )
    assert backend.app_name == "TOML Name"
