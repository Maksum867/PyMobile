"""Regression tests for the Android shell, build, storage and HTTP audit fixes.

The Java renderer cannot run on the desktop, so its fixes are checked the same
way the rest of the suite checks Java: by the structure of the sources, and by
the prebuilt ``classes.dex`` actually containing the code that ships.
"""

from __future__ import annotations

import inspect
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from pymobile import App, Column, Label, Screen, TextInput
from pymobile.compiler import pipeline as pipeline_module
from pymobile.compiler.backends import native as native_module
from pymobile.compiler.backends.native import (
    DEBUG_KEYSTORE_NAME,
    KEYSTORE_DIR_ENV,
    NativeBackend,
    debug_keystore_path,
)
from pymobile.compiler.collector import SourceSet
from pymobile.compiler.manifest import ANDROID_NS, build_manifest
from pymobile.compiler.toolchain import Toolchain
from pymobile.core.config import ProjectConfig
from pymobile.core.ui.dialogs import AlertDialog, ConfirmDialog

ANDROID_DIR = Path(__file__).resolve().parents[1] / "resources" / "android"
JAVA_DIR = ANDROID_DIR / "java"
PREBUILT_DEX = ANDROID_DIR / "prebuilt" / "arm64-v8a" / "classes.dex"


def _attr(node: ET.Element, name: str) -> str | None:
    return node.get(f"{{{ANDROID_NS}}}{name}")


def _backend(tmp_path: Path, **kwargs: Any) -> NativeBackend:
    config = ProjectConfig(root=tmp_path, package="com.example.audit")
    toolchain = Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk")
    return NativeBackend(config, toolchain, tmp_path / "runtime", **kwargs)


# --------------------------------------------------------------- manifest
class TestManifest:
    def test_backup_is_off_by_default(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, package="com.example.a")
        application = ET.fromstring(build_manifest(config)).find("application")
        assert application is not None
        assert _attr(application, "allowBackup") == "false"

    def test_backup_can_be_enabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, package="com.example.a", allow_backup=True)
        application = ET.fromstring(build_manifest(config)).find("application")
        assert application is not None
        assert _attr(application, "allowBackup") == "true"

    def test_language_and_font_changes_do_not_recreate_the_activity(
        self, tmp_path: Path
    ) -> None:
        config = ProjectConfig(root=tmp_path, package="com.example.a")
        activity = ET.fromstring(build_manifest(config)).find(".//activity")
        assert activity is not None
        changes = set((_attr(activity, "configChanges") or "").split("|"))
        for change in ("locale", "layoutDirection", "fontScale", "density", "smallestScreenSize"):
            assert change in changes, change

    def test_predictive_back_is_enabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, package="com.example.a")
        application = ET.fromstring(build_manifest(config)).find("application")
        assert application is not None
        assert _attr(application, "enableOnBackInvokedCallback") == "true"


# ------------------------------------------------------------ signing key
class TestDebugKeystore:
    def test_lives_outside_the_build_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(KEYSTORE_DIR_ENV, raising=False)
        path = debug_keystore_path("com.example.a")
        assert path.parent == Path.home() / ".pymobile" / "keystores"
        assert path.name == "com.example.a-debug.jks"

    def test_directory_can_be_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KEYSTORE_DIR_ENV, str(tmp_path / "keys"))
        assert debug_keystore_path("com.example.a") == tmp_path / "keys" / "com.example.a-debug.jks"

    def test_a_key_from_build_is_adopted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing installs must keep accepting updates after the move."""
        monkeypatch.setenv(KEYSTORE_DIR_ENV, str(tmp_path / "keys"))
        backend = _backend(tmp_path)
        legacy = backend.config.output_path / DEBUG_KEYSTORE_NAME
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"old key")

        def no_keytool(*_: Any, **__: Any) -> str:
            raise AssertionError("a new key must not be generated")

        monkeypatch.setattr(native_module, "_run", no_keytool)
        keystore = backend._ensure_debug_keystore(tmp_path)
        assert keystore == tmp_path / "keys" / "com.example.audit-debug.jks"
        assert keystore.read_bytes() == b"old key"

    def test_survives_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil

        monkeypatch.setenv(KEYSTORE_DIR_ENV, str(tmp_path / "keys"))
        generated: list[Path] = []

        def fake_keytool(command: list[Any], **_: Any) -> str:
            target = Path(command[command.index("-keystore") + 1])
            target.write_bytes(b"key %d" % len(generated))
            generated.append(target)
            return ""

        monkeypatch.setattr(native_module, "_run", fake_keytool)
        first = _backend(tmp_path)._ensure_debug_keystore(tmp_path)
        shutil.rmtree(tmp_path / "build", ignore_errors=True)  # `pymobile clean`
        second = _backend(tmp_path)._ensure_debug_keystore(tmp_path)
        assert first == second
        assert len(generated) == 1


class TestSigningPasswords:
    def test_passwords_come_from_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYMOBILE_KS_PASS", "store-secret")
        monkeypatch.setenv("PYMOBILE_KEY_PASS", "key-secret")
        backend = _backend(tmp_path, keystore=tmp_path / "release.jks")
        assert backend._keystore_password_given is True
        assert backend.keystore_password == "store-secret"
        assert backend.key_password == "key-secret"

    def test_explicit_password_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYMOBILE_KS_PASS", "from-env")
        backend = _backend(tmp_path, keystore=tmp_path / "r.jks", keystore_password="given")
        assert backend.keystore_password == "given"

    def test_passwords_never_reach_the_command_line(self) -> None:
        source = inspect.getsource(NativeBackend.package)
        assert "pass:" not in source
        assert "env:PYMOBILE_SIGN_KS_PASS" in source

    def test_run_passes_extra_environment(self) -> None:
        output = native_module._run(
            [sys.executable, "-c", "import os; print(os.environ['PYMOBILE_T'])"],
            extra_env={"PYMOBILE_T": "visible"},
        )
        assert "visible" in output


class TestPipelinePassesSigningOptions:
    def test_release_keystore_reaches_the_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`build --native --keystore` used to produce a debug-signed APK."""
        seen: dict[str, Any] = {}

        class Stop(Exception):
            pass

        class FakeToolchain:
            def verify(self, **_: Any) -> None:
                return None

        def fake_backend(*_: Any, **kwargs: Any) -> None:
            seen.update(kwargs)
            raise Stop

        monkeypatch.setattr(pipeline_module, "find_toolchain", FakeToolchain)
        monkeypatch.setattr(pipeline_module, "ensure_runtime", lambda abi: tmp_path)
        monkeypatch.setattr(pipeline_module, "NativeBackend", fake_backend)
        config = ProjectConfig(root=tmp_path, package="com.example.a")
        build = pipeline_module.BuildPipeline(
            config,
            native=True,
            keystore=tmp_path / "release.jks",
            keystore_password="s1",
            key_alias="upload",
            key_password="s2",
        )
        main = tmp_path / "main.py"
        main.write_text("print('hi')\n", encoding="utf-8")
        sources = SourceSet(root=tmp_path, files=(main,), entrypoint=main)
        with pytest.raises(Stop):
            build._run_native(tmp_path, sources, [], None, tmp_path / "a.apk", 0.0)  # type: ignore[arg-type]
        assert seen["keystore"] == tmp_path / "release.jks"
        assert seen["keystore_password"] == "s1"
        assert seen["key_alias"] == "upload"
        assert seen["key_password"] == "s2"


# ------------------------------------------------------------- APK content
class TestFrameworkAssets:
    def test_desktop_tooling_is_not_shipped(self, tmp_path: Path) -> None:
        names = set(_backend(tmp_path)._framework_assets())
        prefix = "assets/app/pymobile/"
        for unwanted in (
            "cli.py",
            "__main__.py",
            "core/watcher.py",
            "core/ui/gui.py",
            "core/ui/web.py",
            "core/ui/preview.py",
        ):
            assert prefix + unwanted not in names, unwanted
        assert not any(n.startswith(prefix + "compiler/") for n in names)
        assert not any(n.startswith(prefix + "resources/") for n in names)

    def test_runtime_is_still_shipped(self, tmp_path: Path) -> None:
        names = set(_backend(tmp_path)._framework_assets())
        for needed in ("__init__.py", "log.py", "core/app.py", "core/bridge/android.py"):
            assert f"assets/app/pymobile/{needed}" in names, needed


class TestOptimizeForNative:
    def _compile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, device: tuple[int, int]
    ) -> tuple[list[tuple[str, Path]], list[str]]:
        from pymobile.compiler.collector import SourceSet

        main = tmp_path / "main.py"
        main.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(pipeline_module, "DEVICE_PYTHON", device)
        config = ProjectConfig(root=tmp_path, package="com.example.a", optimize=True)
        build = pipeline_module.BuildPipeline(config, native=True)
        work = tmp_path / "work"
        work.mkdir()
        entries = build._compile_sources(SourceSet(tmp_path, (main,), main), work)
        return entries, build.warnings

    def test_other_host_version_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries, warnings = self._compile(tmp_path, monkeypatch, (2, 7))
        assert [name for name, _ in entries] == ["main.py"]
        assert any("optimize" in warning for warning in warnings)

    def test_matching_host_version_compiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries, warnings = self._compile(tmp_path, monkeypatch, sys.version_info[:2])
        assert [name for name, _ in entries] == ["main.pyc"]
        assert not any("optimize" in warning for warning in warnings)


# ------------------------------------------------------------ log module
class TestLogModuleRename:
    def test_old_import_path_still_works(self) -> None:
        import pymobile
        import pymobile.log
        from pymobile.logging import get_logger  # noqa: F401 - the import is the test

        assert pymobile.logging is pymobile.log
        assert sys.modules["pymobile.logging"] is pymobile.log

    def test_no_module_shadows_the_standard_library(self) -> None:
        package = Path(__file__).resolve().parents[1]
        assert not (package / "logging.py").exists()


# --------------------------------------------------------- Python side of A
class _DialogScreen(Screen):
    def __init__(self, dialog: Any) -> None:
        super().__init__("Dialogs")
        self.dialog = dialog

    def build(self) -> Column:
        return Column(Label("body", id="body"), self.dialog)


class TestDialogDismiss:
    def test_back_on_a_confirm_dialog_means_cancel(self, bridge: Any) -> None:
        outcome: list[str] = []
        dialog = ConfirmDialog(
            "Delete?",
            on_confirm=lambda: outcome.append("yes"),
            on_cancel=lambda: outcome.append("no"),
            id="ask",
        )
        app = App("Demo", bridge=bridge)
        app.run(_DialogScreen(dialog))
        dialog.open()
        app.handle_ui_event("ask", "dismiss", "")
        assert outcome == ["no"]
        assert dialog.visible is False

    def test_back_on_an_alert_acknowledges_it(self, bridge: Any) -> None:
        seen: list[bool] = []
        dialog = AlertDialog("Saved", on_acknowledge=lambda: seen.append(True), id="note")
        app = App("Demo", bridge=bridge)
        app.run(_DialogScreen(dialog))
        dialog.open()
        app.handle_ui_event("note", "dismiss", "")
        assert seen == [True]
        assert dialog.visible is False


class TestTextInputRevision:
    def test_typing_does_not_bump_the_revision(self) -> None:
        field = TextInput()
        before = field.props()["revision"]
        field._ui_set_value("hel")
        assert field.value == "hel"
        assert field.props()["revision"] == before

    def test_programmatic_change_bumps_it(self) -> None:
        """clear() while the field has focus must reach the device."""
        field = TextInput()
        field._ui_set_value("hello")
        before = field.props()["revision"]
        field.value = ""
        assert field.props()["revision"] == before + 1

    def test_truncated_input_is_pushed_back(self) -> None:
        field = TextInput(max_length=3)
        before = field.props()["revision"]
        field._ui_set_value("abcd")
        assert field.value == "abc"
        assert field.props()["revision"] == before + 1


class _Plain(Screen):
    def build(self) -> Column:
        return Column(Label("x"))


class TestThemeAndLocale:
    def test_theme_reaches_a_renderer_that_accepts_it(self, bridge: Any) -> None:
        bridge.accepts_theme = True
        app = App("Demo", bridge=bridge, theme="dark")
        app.run(_Plain("Plain"))
        tree = bridge.last_tree
        assert tree["theme"]["dark"] is True
        assert tree["theme"]["PRIMARY"].startswith("#")

    def test_previews_get_the_plain_tree(self, bridge: Any) -> None:
        app = App("Demo", bridge=bridge)
        app.run(_Plain("Plain"))
        assert "theme" not in bridge.last_tree

    def test_language_change_becomes_an_app_event(self, bridge: Any) -> None:
        app = App("Demo", bridge=bridge)
        app.run(_Plain("Plain"))
        seen: list[Any] = []
        app.events.on("app:locale", lambda event: seen.append(event.source))
        app.handle_ui_event("", "locale", "uk-UA")
        assert seen == ["uk-UA"]


# ------------------------------------------------------ Java renderer (A)
class TestViewBuilderStructure:
    source = (JAVA_DIR / "ViewBuilder.java").read_text(encoding="utf-8")

    def _update_node(self) -> str:
        start = self.source.index("boolean updateNode(View view")
        return self.source[start:]

    def test_switch_is_matched_before_button(self) -> None:
        """Switch extends CompoundButton extends Button: order decides the branch.

        The Switch branch used to sit after `instanceof Button`, so it was dead
        code and a Switch's `checked` state never reached the device.
        """
        body = self._update_node()
        switch = body.index("if (view instanceof Switch)")
        assert switch < body.index("if (view instanceof CheckBox)")
        assert switch < body.index("if (view instanceof Button)")

    def test_composite_widgets_are_patched_before_the_generic_group(self) -> None:
        body = self._update_node()
        generic = body.index("        if (view instanceof ViewGroup) {")
        for kind in ("Dialog", "ListTile", "BottomNavigation", "Stepper", "ProgressText"):
            assert body.index(f'"{kind}"') < generic, kind

    def test_theme_is_applied(self) -> None:
        assert "boolean setTheme(JSONObject theme)" in self.source

    def test_text_input_honours_the_revision(self) -> None:
        assert '"revision"' in self.source


class TestPrebuiltDex:
    """The dex in the wheel is what runs on phones without a JDK/NDK.

    It had not been rebuilt since v0.3.0: every fix to ViewBuilder.java was
    invisible on a device and 20 widget types fell back to a plain view.
    """

    dex = PREBUILT_DEX.read_bytes()

    def test_every_widget_type_is_compiled_in(self) -> None:
        source = (JAVA_DIR / "ViewBuilder.java").read_text(encoding="utf-8")
        cases = sorted(set(re.findall(r'case "([A-Za-z]+)":', source)))
        assert cases
        missing = [kind for kind in cases if kind.encode() not in self.dex]
        assert not missing, f"prebuilt classes.dex is stale; rebuild it: {missing}"

    def test_renderer_fixes_are_compiled_in(self) -> None:
        for symbol in (b"setTheme", b"applyListTile", b"syncDialog", b"applySliderScale"):
            assert symbol in self.dex, symbol

    def test_old_prebuilt_bridge_still_finds_its_methods(self) -> None:
        """libpymobile.so was built against the short vibrate/notify signatures."""
        source = (JAVA_DIR / "Native.java").read_text(encoding="utf-8")
        assert "public static void vibrate(long milliseconds) {" in source
        short_notify = (
            "public static void notify(String title, String body, int id, boolean ongoing) {"
        )
        assert short_notify in source
