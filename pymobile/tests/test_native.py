"""Tests for the native APK backend, toolchain discovery and runtime cache.

The heavy end-to-end build needs the Android SDK/NDK, so it is skipped unless a
real toolchain is present. Everything that can be checked without it — path
resolution, error messages, asset selection — is tested unconditionally.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pymobile.compiler.runtime import ABI_TRIPLETS, runtime_cache_dir
from pymobile.compiler.toolchain import Toolchain, ToolchainError, find_toolchain
from pymobile.core.config import ProjectConfig
from pymobile.errors import PyMobileError


def _toolchain_available() -> bool:
    """Whether a usable SDK + NDK is installed on this machine."""
    try:
        toolchain = find_toolchain()
        toolchain.verify(require_ndk=True)
    except (ToolchainError, PyMobileError):
        return False
    return True


requires_toolchain = pytest.mark.skipif(
    not _toolchain_available(), reason="Android SDK/NDK not installed"
)


def _prebuilt_so_present() -> bool:
    """Whether the packaged prebuilt JNI bridge exists.

    ``*.so`` is git-ignored, so a fresh source checkout lacks the artifact —
    it only ships inside built wheels. Tests that depend on it are skipped
    rather than failing in that case.
    """
    try:
        from pymobile.resources import resource_path

        return resource_path("android", "prebuilt", "arm64-v8a", "libpymobile.so").exists()
    except Exception:
        return False


requires_prebuilt_so = pytest.mark.skipif(
    not _prebuilt_so_present(),
    reason="prebuilt libpymobile.so is absent from a source checkout (*.so is git-ignored)",
)


def _method_body(source: str, signature: str) -> str:
    """Return one Java method body, matched by brace balance.

    Slicing until the next comment breaks whenever a method is moved, so the
    body is delimited properly instead.
    """
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


class TestToolchainDiscovery:
    def test_missing_sdk_reports_hint(self, tmp_path: Path) -> None:
        with pytest.raises(ToolchainError) as info:
            find_toolchain(tmp_path / "no-such-sdk")
        assert info.value.hint

    import sys

    def test_tool_paths_derive_from_build_tools(self, tmp_path: Path) -> None:
        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "build-tools" / "34.0.0",
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
        )
        expected_aapt2 = "aapt2.exe" if sys.platform == "win32" else "aapt2"
        expected_javac = "javac.exe" if sys.platform == "win32" else "javac"

        assert toolchain.aapt2.name == expected_aapt2
        assert toolchain.d8.parent == toolchain.build_tools
        assert toolchain.javac.parts[-2:] == ("bin", expected_javac)

    def test_verify_lists_every_missing_tool(self, tmp_path: Path) -> None:
        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
        )
        with pytest.raises(ToolchainError) as info:
            toolchain.verify()
        message = str(info.value)
        # d8/javac are optional now: prebuilt artifacts cover them.
        for tool in ("aapt2", "zipalign", "apksigner"):
            assert tool in message

    def test_no_ndk_means_no_clang(self, tmp_path: Path) -> None:
        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
            ndk=None,
        )
        assert toolchain.clang is None
        assert not toolchain.has_ndk

    def test_describe_is_single_line(self, tmp_path: Path) -> None:
        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "jdk",
        )
        assert "\n" not in toolchain.describe()


class TestRuntime:
    def test_known_abis(self) -> None:
        assert ABI_TRIPLETS["arm64-v8a"] == "aarch64-linux-android"

    def test_unsupported_abi(self) -> None:
        from pymobile.compiler.runtime import ensure_runtime

        with pytest.raises(PyMobileError, match="No official CPython build"):
            ensure_runtime("armeabi-v7a")

    def test_cache_dir_honours_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYMOBILE_CACHE", str(tmp_path))
        assert runtime_cache_dir() == tmp_path


class TestAssetSelection:
    def test_framework_is_bundled(self, tmp_path: Path) -> None:
        """The APK must carry the pymobile package itself."""
        from pymobile.compiler.backends.native import NativeBackend

        config = ProjectConfig(root=tmp_path, package="com.example.a")
        backend = NativeBackend(
            config,
            Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk"),
            tmp_path / "runtime",
        )
        assets = backend._framework_assets()
        names = set(assets)
        assert any(n.endswith("pymobile/__init__.py") for n in names)
        assert any("core/ui/widget.py" in n for n in names)
        assert not any("__pycache__" in n for n in names)
        assert not any("/tests/" in n for n in names)

    def test_release_keystore_requires_password(self, tmp_path: Path) -> None:
        from pymobile.compiler.backends.native import NativeBackend

        config = ProjectConfig(root=tmp_path, package="com.example.a")
        toolchain = Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk")
        backend = NativeBackend(
            config,
            toolchain,
            tmp_path / "runtime",
            keystore=tmp_path / "release.jks",
        )
        assert backend._release_keystore is True
        assert backend._keystore_password_given is False

    def test_stdlib_excludes_are_applied(self) -> None:
        from pymobile.compiler.backends.native import STDLIB_EXCLUDES

        for unwanted in ("test", "idlelib", "tkinter", "ensurepip"):
            assert unwanted in STDLIB_EXCLUDES


class TestApkSize:
    """Everything that decides how large the APK gets."""

    def _backend(self, tmp_path: Path, **flags: object) -> object:
        from pymobile.compiler.backends.native import NativeBackend

        runtime = tmp_path / "runtime"
        (runtime / "lib" / "python3.14").mkdir(parents=True, exist_ok=True)
        config = ProjectConfig(root=tmp_path, package="com.example.a", **flags)  # type: ignore[arg-type]
        return NativeBackend(
            config,
            Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk"),
            runtime,
        )

    # -- duplicate native libraries ---------------------------------------
    def _fake_libs(self, tmp_path: Path) -> Path:
        """A lib dir shaped like the official CPython Android release."""
        libdir = tmp_path / "runtime" / "lib"
        libdir.mkdir(parents=True, exist_ok=True)
        for name in (
            "libpython3.14.so",
            "libcrypto.so",
            "libcrypto_python.so",
            "libssl.so",
            "libssl_python.so",
            "libsqlite3.so",
            "libsqlite3_python.so",
        ):
            (libdir / name).write_bytes(b"\x7fELF" + name.encode())
        return libdir

    def test_duplicate_support_libraries_are_dropped(self, tmp_path: Path) -> None:
        """libcrypto.so and libcrypto_python.so are byte-identical copies.

        Only the _python names appear in the extension modules' DT_NEEDED
        entries, so shipping both wasted ~5 MB of uncompressed, page-aligned
        payload in every APK.
        """
        backend = self._backend(tmp_path)
        libdir = self._fake_libs(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        backend._copy_runtime_libraries(libdir, out)  # type: ignore[attr-defined]

        shipped = {path.name for path in out.glob("*.so")}
        assert shipped == {
            "libpython3.14.so",
            "libcrypto_python.so",
            "libssl_python.so",
            "libsqlite3_python.so",
        }

    def test_unpaired_library_is_still_shipped(self, tmp_path: Path) -> None:
        """A runtime without the _python variant must keep the plain one."""
        backend = self._backend(tmp_path)
        libdir = tmp_path / "runtime" / "lib"
        libdir.mkdir(parents=True, exist_ok=True)
        (libdir / "libpython3.14.so").write_bytes(b"\x7fELF")
        (libdir / "libsqlite3.so").write_bytes(b"\x7fELF")
        out = tmp_path / "out"
        out.mkdir()
        backend._copy_runtime_libraries(libdir, out)  # type: ignore[attr-defined]
        assert (out / "libsqlite3.so").exists()

    def test_no_ssl_leaves_openssl_out(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, no_ssl=True)
        libdir = self._fake_libs(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        backend._copy_runtime_libraries(libdir, out)  # type: ignore[attr-defined]

        shipped = {path.name for path in out.glob("*.so")}
        assert shipped == {"libpython3.14.so", "libsqlite3_python.so"}

    # -- stdlib pruning ----------------------------------------------------
    def test_build_config_headers_are_excluded(self, tmp_path: Path) -> None:
        """config-3.14 never matched config-3.14-aarch64-linux-android."""
        backend = self._backend(tmp_path)
        assert backend._is_excluded(  # type: ignore[attr-defined]
            Path("config-3.14-aarch64-linux-android/Makefile")
        )

    def test_minimal_stdlib_drops_desktop_packages(self, tmp_path: Path) -> None:
        default = self._backend(tmp_path)
        minimal = self._backend(tmp_path, minimal_stdlib=True)
        for name in ("pydoc_data/topics.py", "unittest/case.py", "venv/__init__.py"):
            assert not default._is_excluded(Path(name))  # type: ignore[attr-defined]
            assert minimal._is_excluded(Path(name))  # type: ignore[attr-defined]

    def test_minimal_stdlib_keeps_what_apps_use(self, tmp_path: Path) -> None:
        minimal = self._backend(tmp_path, minimal_stdlib=True)
        for name in ("json/__init__.py", "http/client.py", "urllib/request.py", "re/__init__.py"):
            assert not minimal._is_excluded(Path(name))  # type: ignore[attr-defined]

    def test_no_ssl_drops_the_ssl_module_and_extensions(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, no_ssl=True)
        assert backend._is_excluded(Path("ssl.py"))  # type: ignore[attr-defined]
        assert backend._is_excluded(  # type: ignore[attr-defined]
            Path("lib-dynload/_ssl.cpython-314-aarch64-linux-android.so")
        )
        assert not backend._is_excluded(Path("json/__init__.py"))  # type: ignore[attr-defined]

    def test_no_ssl_skips_the_ca_bundle(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, no_ssl=True)
        assets = backend.collect_assets([])  # type: ignore[attr-defined]
        assert "assets/python/etc/ssl/cert.pem" not in assets

    def test_ssl_is_shipped_by_default(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path)
        assert not backend._is_excluded(Path("ssl.py"))  # type: ignore[attr-defined]
        assert "assets/python/etc/ssl/cert.pem" in backend.collect_assets([])  # type: ignore[attr-defined]

    def test_flags_are_off_by_default(self) -> None:
        config = ProjectConfig(package="com.example.a")
        assert config.minimal_stdlib is False
        assert config.no_ssl is False


@requires_toolchain
class TestNativeBuild:
    """End-to-end: produce a signed APK and verify it."""

    @pytest.fixture(scope="class")
    @classmethod
    def built(cls, tmp_path_factory: pytest.TempPathFactory) -> Path:
        from pymobile.compiler.pipeline import build_apk
        from pymobile.compiler.scaffold import create_project
        from pymobile.core.config import load_config

        project = tmp_path_factory.mktemp("native")
        create_project(project, "Native Test", package="com.example.nativetest")
        result = build_apk(load_config(project), native=True)
        return result.apk

    def test_apk_exists_and_is_a_valid_zip(self, built: Path) -> None:
        assert built.exists()
        with zipfile.ZipFile(built) as archive:
            assert archive.testzip() is None

    def test_contains_every_required_component(self, built: Path) -> None:
        with zipfile.ZipFile(built) as archive:
            names = archive.namelist()
        assert "AndroidManifest.xml" in names
        assert "classes.dex" in names
        assert "resources.arsc" in names
        assert any(n.startswith("META-INF/") for n in names)
        assert any(n.endswith("libpymobile.so") for n in names)
        assert any(n.endswith("libpython3.14.so") for n in names)

    def test_manifest_is_binary(self, built: Path) -> None:
        """aapt2 must have converted the manifest to binary AXML."""
        with zipfile.ZipFile(built) as archive:
            head = archive.read("AndroidManifest.xml")[:4]
        assert not head.startswith(b"<?xml")

    def test_native_libraries_are_stored_uncompressed(self, built: Path) -> None:
        """Only lib/ must be uncompressed; assets/ may be deflated."""
        with zipfile.ZipFile(built) as archive:
            for info in archive.infolist():
                if info.filename.startswith("lib/") and info.filename.endswith(".so"):
                    assert info.compress_type == zipfile.ZIP_STORED

    def test_app_ships_sources_not_host_bytecode(self, built: Path) -> None:
        """Host .pyc would be unreadable by the device interpreter."""
        with zipfile.ZipFile(built) as archive:
            names = archive.namelist()
        assert "assets/app/main.py" in names
        assert "assets/app/main.pyc" not in names

    def test_signature_verifies(self, built: Path) -> None:
        from pymobile.compiler.backends.native import NativeBackend

        toolchain = find_toolchain()
        backend = NativeBackend(
            ProjectConfig(root=built.parent, package="com.example.nativetest"),
            toolchain,
            Path(os.environ.get("PYMOBILE_RUNTIME", "/nonexistent")),
        )
        assert backend.verify(built)


class _FakeNative:
    """Stands in for the injected ``_pymobile_android`` module."""

    def __init__(self, events: list[tuple[str, str, str]] | None = None) -> None:
        self.rendered: list[str] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._events = list(events or [])
        self.granted: set[str] = set()

    def render(self, payload: str) -> None:
        self.rendered.append(payload)

    def toast(self, message: str, longer: bool = False) -> None:
        self.calls.append(("toast", (message, longer)))

    def vibrate(self, milliseconds: int, amplitude: int = -1) -> None:
        self.calls.append(("vibrate", (milliseconds, amplitude)))

    def vibrate_pattern(self, pattern: list[int], repeat: int = -1) -> None:
        self.calls.append(("vibrate_pattern", (tuple(pattern), repeat)))

    def cancel_vibration(self) -> None:
        self.calls.append(("cancel_vibration", ()))

    def notify(
        self,
        title: str,
        body: str,
        identifier: int,
        ongoing: bool = False,
        channel_id: str = "",
        channel_name: str = "",
        small_icon: str = "",
    ) -> None:
        self.calls.append(
            ("notify", (title, body, identifier, ongoing, channel_id, channel_name, small_icon))
        )

    def ensure_channel(self, channel_id: str, channel_name: str, importance: int) -> None:
        self.calls.append(("ensure_channel", (channel_id, channel_name, importance)))

    def cancel_notification(self, identifier: int) -> None:
        self.calls.append(("cancel_notification", (identifier,)))

    def has_permission(self, permission: str) -> bool:
        return permission in self.granted

    def request_permission(self, permission: str) -> None:
        self.granted.add(permission)

    def next_event(self, timeout_ms: int = -1) -> tuple[str, str, str] | None:
        return self._events.pop(0) if self._events else None


class TestAndroidBridge:
    """The device bridge, exercised through a fake native module."""

    def _bridge(self, events: list[tuple[str, str, str]] | None = None) -> Any:
        from pymobile.core.bridge.android import AndroidBridge

        bridge = AndroidBridge()
        bridge._native = _FakeNative(events)
        return bridge

    def test_unavailable_without_native_module(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        bridge = AndroidBridge()
        bridge._native = None
        assert not bridge.is_available()

    def test_render_emits_json(self) -> None:
        import json as jsonlib

        bridge = self._bridge()
        bridge.render({"type": "Column", "id": "root", "children": []})
        payload = jsonlib.loads(bridge._native.rendered[0])
        assert payload["type"] == "Column"

    def test_render_keeps_unicode_readable(self) -> None:
        bridge = self._bridge()
        bridge.render({"type": "Label", "props": {"text": "Привіт"}})
        assert "Привіт" in bridge._native.rendered[0]

    def test_notification_forwarded(self) -> None:
        from pymobile.core.bridge.base import NotificationSpec

        bridge = self._bridge()
        bridge.notify(NotificationSpec("T", "B", 7, "c", "C", ongoing=True))
        assert ("notify", ("T", "B", 7, True, "c", "C", None)) in bridge._native.calls

    def test_permission_round_trip(self) -> None:
        bridge = self._bridge()
        assert not bridge.has_permission("android.permission.CAMERA")
        assert bridge.request_permissions(["android.permission.CAMERA"]) == {
            "android.permission.CAMERA": True
        }

    def test_calls_are_safe_without_native(self) -> None:
        """Off-device the bridge must degrade quietly, not explode."""
        from pymobile.core.bridge.android import AndroidBridge

        bridge = AndroidBridge()
        bridge._native = None
        bridge.render({"type": "Label"})
        bridge.vibrate(10)
        bridge.toast("hi")
        assert bridge.next_event() is None


class TestEventLoop:
    """App.run must consume UI events and route them to the right widget."""

    def _app_with(self, events: list[tuple[str, str, str]]) -> Any:
        from pymobile import App, Button, Column, Label, Screen, Widget
        from pymobile.core.bridge.android import AndroidBridge

        class Home(Screen):
            title = "Home"

            def __init__(self) -> None:
                super().__init__()
                self.taps = 0
                self.counter = Label("0", id="counter")

            def build(self) -> Widget:
                return Column(
                    self.counter,
                    Button("tap", on_press=self.on_tap, id="btn"),
                )

            def on_tap(self) -> None:
                self.taps += 1
                self.counter.set_text(str(self.taps))

        bridge = AndroidBridge()
        bridge._native = _FakeNative(events)
        return App("Test", bridge=bridge), Home()

    def test_press_reaches_the_widget(self) -> None:
        app, home = self._app_with([("btn", "press", ""), ("btn", "press", "")])
        app.run(home)
        assert home.taps == 2
        assert home.counter.text == "2"

    def test_loop_exit_shuts_the_app_down(self) -> None:
        """A finished event loop means the platform closed the app (PM-10).

        The loop returns when the device asks it to stop (activity destroyed);
        it used to leave ``running`` True, ``App.current()`` pointing at the
        dead app and every screen mounted. Here the fake queue is empty, which
        ends the loop exactly like ``Native.stopEventLoop`` does on a device.
        """
        app, home = self._app_with([])
        app.run(home)  # must return rather than hang
        assert not app.running
        assert app.current() is None
        assert not home.mounted

    def test_unknown_widget_is_ignored(self) -> None:
        app, home = self._app_with([("ghost", "press", "")])
        app.run(home)
        assert home.taps == 0

    def test_back_pops_or_stops(self) -> None:
        app, home = self._app_with([("", "back", "")])
        app.run(home)
        assert not app.running  # at the root, back exits the app

    def test_handler_error_does_not_break_the_loop(self) -> None:
        from pymobile import Button, Column, Screen, Widget

        fired: list[int] = []

        def boom() -> None:
            fired.append(1)
            raise RuntimeError("bad handler")

        class Broken(Screen):
            def build(self) -> Widget:
                return Column(Button("x", on_press=boom, id="b"))

        app, _ = self._app_with([("b", "press", ""), ("b", "press", "")])
        app.run(Broken())
        # Both queued presses were dispatched even though the handler raised
        # every time: one failing callback does not end the loop.
        assert len(fired) == 2


class TestWindowsToolPaths:
    """Tool resolution must adapt to Windows filename conventions."""

    def _toolchain(self, tmp_path: Path) -> Toolchain:
        bt = tmp_path / "bt"
        bt.mkdir(exist_ok=True)
        jdk = tmp_path / "jdk" / "bin"
        jdk.mkdir(parents=True, exist_ok=True)

        (tmp_path / "android.jar").write_text("", encoding="utf-8")

        return Toolchain(
            sdk=tmp_path,
            build_tools=bt,
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
        )

    def test_posix_uses_bare_names(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        toolchain = self._toolchain(tmp_path)
        assert toolchain.aapt2.name == "aapt2"
        assert toolchain.javac.name == "javac"

    def test_windows_prefers_exe_for_binaries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        toolchain = self._toolchain(tmp_path)
        assert toolchain.aapt2.name == "aapt2.exe"
        assert toolchain.zipalign.name == "zipalign.exe"
        assert toolchain.javac.name == "javac.exe"

    def test_windows_prefers_bat_for_wrappers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """d8 and apksigner ship as .bat scripts in the Android SDK."""
        monkeypatch.setattr("platform.system", lambda: "Windows")
        toolchain = self._toolchain(tmp_path)
        assert toolchain.d8.name == "d8.bat"
        assert toolchain.apksigner.name == "apksigner.bat"

    def test_existing_file_wins_over_default_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        build_tools = tmp_path / "bt"
        build_tools.mkdir()
        (build_tools / "aapt2.bat").write_text("", encoding="utf-8")
        toolchain = self._toolchain(tmp_path)
        # .exe is preferred, but when only .bat exists it must be picked up.
        assert toolchain.aapt2.name == "aapt2.bat"

    def test_windows_clang_uses_cmd_wrapper(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        ndk = tmp_path / "ndk" / "27.0.0"
        clang_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "windows-x86_64" / "bin"
        clang_dir.mkdir(parents=True)
        (clang_dir / "aarch64-linux-android21-clang.cmd").write_text("", encoding="utf-8")
        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "jdk",
            ndk=ndk,
        )
        assert toolchain.clang is not None
        assert toolchain.clang.name.endswith(".cmd")


class TestJdkArchives:
    """Every supported host must have an automatic JDK download."""

    def test_all_hosts_covered(self) -> None:
        from pymobile.compiler.sdk_installer import _JDK_ARCHIVES

        assert set(_JDK_ARCHIVES) == {"Linux", "Windows", "Darwin"}

    def test_darwin_cmdline_tools_are_pinned(self) -> None:
        from pymobile.compiler.sdk_installer import _CMDLINE_TOOLS, _CMDLINE_TOOLS_ARM

        assert "Darwin" in _CMDLINE_TOOLS
        assert "Darwin" in _CMDLINE_TOOLS_ARM
        assert "Linux" in _CMDLINE_TOOLS_ARM

    def test_windows_archive_is_a_zip(self) -> None:
        from pymobile.compiler.sdk_installer import _JDK_ARCHIVES

        filename, is_zip, checksum = _JDK_ARCHIVES["Windows"]

        assert filename.endswith(".zip")
        assert is_zip is True
        assert len(checksum) == 64
        assert all(character in "0123456789abcdef" for character in checksum)

    def test_unsupported_host_reports_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pymobile.compiler import sdk_installer

        monkeypatch.setattr("platform.system", lambda: "FreeBSD")
        monkeypatch.delenv("JAVA_HOME", raising=False)
        with pytest.raises(PyMobileError, match="No automatic JDK download"):
            sdk_installer._ensure_jdk(Path("/tmp/does-not-exist-xyz"))


class TestPrebuiltArtifacts:
    """The packaged prebuilts let users build APKs without the NDK or a JDK."""

    @requires_prebuilt_so
    def test_prebuilts_are_packaged(self) -> None:
        from pymobile.resources import resource_path

        for name in ("libpymobile.so", "classes.dex"):
            path = resource_path("android", "prebuilt", "arm64-v8a", name)
            assert path.exists()
            assert path.stat().st_size > 1024

    def test_dex_contains_the_launcher_classes(self) -> None:
        from pymobile.resources import resource_path

        payload = resource_path("android", "prebuilt", "arm64-v8a", "classes.dex").read_bytes()
        for symbol in (b"MainActivity", b"ViewBuilder", b"Native", b"DeviceServices"):
            assert symbol in payload

    @requires_prebuilt_so
    def test_prebuilts_carry_no_project_identity(self) -> None:
        """They must be reusable across apps, so no package id may be baked in."""
        from pymobile.resources import resource_path

        for name in ("libpymobile.so", "classes.dex"):
            payload = resource_path("android", "prebuilt", "arm64-v8a", name).read_bytes()
            assert b"com.example" not in payload

    @requires_prebuilt_so
    def test_jni_falls_back_without_ndk(self, tmp_path: Path) -> None:
        from pymobile.compiler.backends.native import NativeBackend

        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "jdk",
            ndk=None,
        )
        runtime = tmp_path / "runtime"
        (runtime / "lib").mkdir(parents=True)
        backend = NativeBackend(
            ProjectConfig(root=tmp_path, package="com.example.a"), toolchain, runtime
        )
        output = backend.compile_jni(tmp_path / "work")
        assert (output / "libpymobile.so").exists()

    def test_dex_falls_back_without_jdk(self, tmp_path: Path) -> None:
        from pymobile.compiler.backends.native import NativeBackend

        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=tmp_path / "bt",
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "missing-jdk",
        )
        backend = NativeBackend(
            ProjectConfig(root=tmp_path, package="com.example.a"), toolchain, tmp_path
        )
        dex = backend.compile_java(tmp_path / "work")
        assert dex.exists()
        assert dex.name == "classes.dex"


class TestToolchainRequirements:
    """The NDK and javac must be optional, everything else mandatory."""

    def _toolchain(self, tmp_path: Path) -> Toolchain:
        bt = tmp_path / "bt"
        bt.mkdir(exist_ok=True)
        jdk = tmp_path / "jdk" / "bin"
        jdk.mkdir(parents=True, exist_ok=True)

        ext = ".exe" if sys.platform == "win32" else ""
        bat = ".bat" if sys.platform == "win32" else ""

        (bt / f"aapt2{ext}").write_text("", encoding="utf-8")
        (bt / f"zipalign{ext}").write_text("", encoding="utf-8")
        (bt / f"apksigner{bat if sys.platform == 'win32' else ext}").write_text(
            "", encoding="utf-8"
        )
        (jdk / f"keytool{ext}").write_text("", encoding="utf-8")
        (tmp_path / "android.jar").write_text("", encoding="utf-8")

        return Toolchain(
            sdk=tmp_path,
            build_tools=bt,
            platform_jar=tmp_path / "android.jar",
            java_home=tmp_path / "jdk",
        )

    def test_passes_without_ndk_or_javac(self, tmp_path: Path) -> None:
        self._toolchain(tmp_path).verify()

    def test_ndk_can_be_demanded(self, tmp_path: Path) -> None:
        with pytest.raises(ToolchainError, match="NDK"):
            self._toolchain(tmp_path).verify(require_ndk=True)

    def test_javac_can_be_demanded(self, tmp_path: Path) -> None:
        with pytest.raises(ToolchainError, match="javac"):
            self._toolchain(tmp_path).verify(require_javac=True)

    def test_missing_keytool_is_fatal(self, tmp_path: Path) -> None:
        """Signing always needs a JRE, so keytool is never optional."""
        toolchain = self._toolchain(tmp_path)

        keytool = tmp_path / "jdk" / "bin" / "keytool"
        if sys.platform == "win32" and not keytool.exists():
            keytool = tmp_path / "jdk" / "bin" / "keytool.exe"

        keytool.unlink()

        with pytest.raises(ToolchainError) as exc_info:
            toolchain.verify()

        assert "keytool" in str(exc_info.value)


class TestSdkPackages:
    def test_minimal_excludes_the_ndk(self) -> None:
        from pymobile.compiler.sdk_installer import MINIMAL_PACKAGES

        assert not any("ndk" in package for package in MINIMAL_PACKAGES)

    def test_full_set_adds_the_ndk(self) -> None:
        from pymobile.compiler.sdk_installer import MINIMAL_PACKAGES, REQUIRED_PACKAGES

        assert set(MINIMAL_PACKAGES) < set(REQUIRED_PACKAGES)
        assert any("ndk" in package for package in REQUIRED_PACKAGES)


class TestBuildRobustness:
    """Regression: a failing d8/javac must never break the build.

    Reported on Windows as `d8 failed (exit 1) ... NullPointerException` while
    dexing an anonymous inner class. Since the launcher dex is identical for
    every app, the prebuilt one is now used by default.
    """

    import sys
    from typing import Any

    def _backend(self, tmp_path: Path, *, javac_exists: bool = True) -> Any:
        from pymobile.compiler.backends.native import NativeBackend

        jdk_bin = tmp_path / "jdk" / "bin"
        jdk_bin.mkdir(parents=True, exist_ok=True)

        exts = ["", ".exe"] if sys.platform == "win32" else [""]

        if javac_exists:
            for ext in exts:
                (jdk_bin / f"javac{ext}").write_text("", encoding="utf-8")

        for ext in exts:
            (jdk_bin / f"keytool{ext}").write_text("", encoding="utf-8")

        bt = tmp_path / "bt"
        bt.mkdir(exist_ok=True)
        bt_exts = ["", ".exe", ".bat"] if sys.platform == "win32" else [""]
        for ext in bt_exts:
            (bt / f"aapt2{ext}").write_text("", encoding="utf-8")
            (bt / f"zipalign{ext}").write_text("", encoding="utf-8")
            (bt / f"apksigner{ext}").write_text("", encoding="utf-8")

        (tmp_path / "j.jar").write_text("", encoding="utf-8")

        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=bt,
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "jdk",
        )
        return NativeBackend(
            ProjectConfig(root=tmp_path, package="com.example.a"), toolchain, tmp_path
        )

    def test_prebuilt_used_even_when_javac_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an opt-in, the toolchain must not be invoked at all."""
        monkeypatch.delenv("PYMOBILE_BUILD_JAVA", raising=False)

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("javac/d8 must not run by default")

        monkeypatch.setattr("pymobile.compiler.backends.native._run", explode)
        dex = self._backend(tmp_path).compile_java(tmp_path / "work")
        assert dex.exists()

    def test_source_build_failure_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An opt-in build that fails must degrade, not abort."""
        monkeypatch.setenv("PYMOBILE_BUILD_JAVA", "1")

        def fail(*args: object, **kwargs: object) -> None:
            raise PyMobileError("d8 failed (exit 1)")

        monkeypatch.setattr("pymobile.compiler.backends.native._run", fail)
        backend = self._backend(tmp_path)
        dex = backend.compile_java(tmp_path / "work")
        assert dex.exists()
        assert any("prebuilt dex" in warning for warning in backend.warnings)

    @requires_prebuilt_so
    def test_jni_falls_back_when_clang_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYMOBILE_BUILD_JNI", "1")

        # Create a fake prebuilt .so where NativeBackend expects its package resource.
        def mock_run(*args: object, **kwargs: object) -> None:
            raise PyMobileError("clang exploded")

        monkeypatch.setattr("pymobile.compiler.backends.native._run", mock_run)

        ndk = tmp_path / "ndk"
        hosts = ["linux-x86_64", "windows-x86_64", "windows"]
        clang_names = [
            "aarch64-linux-android21-clang",
            "aarch64-linux-android21-clang.exe",
            "aarch64-linux-android21-clang.cmd",
            "clang",
            "clang.exe",
        ]

        for host in hosts:
            clang_dir = ndk / "toolchains" / "llvm" / "prebuilt" / host / "bin"
            clang_dir.mkdir(parents=True, exist_ok=True)
            for name in clang_names:
                (clang_dir / name).write_text("", encoding="utf-8")

        jdk_bin = tmp_path / "jdk" / "bin"
        jdk_bin.mkdir(parents=True, exist_ok=True)
        bt = tmp_path / "bt"
        bt.mkdir(parents=True, exist_ok=True)

        ext = ".exe" if sys.platform == "win32" else ""
        bat = ".bat" if sys.platform == "win32" else ""

        (jdk_bin / f"keytool{ext}").write_text("", encoding="utf-8")
        (bt / f"aapt2{ext}").write_text("", encoding="utf-8")
        (bt / f"zipalign{ext}").write_text("", encoding="utf-8")
        (bt / f"apksigner{bat if sys.platform == 'win32' else ext}").write_text(
            "", encoding="utf-8"
        )
        (tmp_path / "j.jar").write_text("", encoding="utf-8")

        from pymobile.compiler.backends.native import NativeBackend

        runtime = tmp_path / "runtime"
        (runtime / "lib").mkdir(parents=True, exist_ok=True)
        # Ensure the prebuilt library is present for the fallback path.
        (runtime / "lib" / "libpymobile.so").write_text("", encoding="utf-8")

        toolchain = Toolchain(
            sdk=tmp_path,
            build_tools=bt,
            platform_jar=tmp_path / "j.jar",
            java_home=tmp_path / "jdk",
            ndk=ndk,
        )
        backend = NativeBackend(
            ProjectConfig(root=tmp_path, package="com.example.a"), toolchain, runtime
        )

        output = backend.compile_jni(tmp_path / "work")

        assert (output / "libpymobile.so").exists()
        assert any("prebuilt JNI bridge" in warning for warning in backend.warnings), (
            f"Expected warning about prebuilt JNI bridge, got: {backend.warnings}"
        )

    def test_dex_is_passed_a_single_jar(self, tmp_path: Path) -> None:
        """d8 must receive one archive, not hundreds of .class paths."""
        native = Path(__file__).resolve().parents[1] / "compiler" / "backends" / "native.py"
        source = native.read_text(encoding="utf-8")
        assert '"jar", "cf", archive' in source
        assert "*sorted(classes.rglob" not in source


class TestRendererContract:
    """Static checks on the Java renderer.

    The renderer cannot be unit-tested without a device, so these guard the
    invariants that actually broke in the field.
    """

    def _source(self, name: str) -> str:
        from pymobile.resources import resource_path

        return resource_path("android", "java", name).read_text(encoding="utf-8")

    def test_leaf_widgets_do_not_set_layout_params(self) -> None:
        """Regression: a Spacer setting ViewGroup.LayoutParams made
        LinearLayout raise ClassCastException, so every sibling after it
        vanished from the screen. Only containers may assign params."""
        source = self._source("ViewBuilder.java")
        builder = source[source.index("private View buildSpacer") :]
        builder = builder[: builder.index("\n    }")]
        # Strip comments first: the method documents why it must not call this.
        code = "\n".join(line for line in builder.splitlines() if not line.strip().startswith("//"))
        assert "setLayoutParams" not in code

    def test_children_are_built_defensively(self) -> None:
        """One failing widget must not blank the whole screen."""
        source = self._source("ViewBuilder.java")
        assert "private View buildChild(" in source
        # every container adds children through the guarded helper
        for call in ("content.addView(buildChild(", "frame.addView(buildChild("):
            assert call in source
        assert "layout.addView(child, params)" in source

    def test_spacer_gets_an_explicit_size_from_its_parent(self) -> None:
        """The parent container, not the Spacer, assigns the size.

        The sizing lives in childParams(), which is also where flex shares and
        cross-axis alignment are resolved for every child of a Row/Column.
        """
        source = self._source("ViewBuilder.java")
        params = _method_body(source, "private LinearLayout.LayoutParams childParams")
        assert '"Spacer".equals(type)' in params
        assert 'childProps.optInt("size", 8)' in params

    def test_every_widget_type_is_handled(self) -> None:
        """The Python side must not emit a type the renderer ignores."""
        from pymobile.core.ui import components, layout

        source = self._source("ViewBuilder.java")
        for module in (components, layout):
            for name in module.__all__:
                widget = getattr(module, name)
                type_name = getattr(widget, "type_name", name)
                assert f'case "{type_name}"' in source or type_name == "Label", (
                    f"{type_name} has no branch in ViewBuilder.java"
                )

    def test_prebuilt_dex_matches_the_current_java(self) -> None:
        """The shipped dex must contain the latest renderer symbols.

        The dex is what actually runs on a phone; if it lags behind
        ViewBuilder.java the new widgets render as blank views with no error.
        """
        from pymobile.resources import resource_path

        payload = resource_path("android", "prebuilt", "arm64-v8a", "classes.dex").read_bytes()
        for symbol in (
            b"buildChild",
            b"buildGrid",
            b"buildSafeArea",
            b"buildFlex",
            b"buildDivider",
            b"childParams",
            b"applyConstraints",
        ):
            assert symbol in payload, f"{symbol.decode()} missing from the prebuilt dex"

    def test_layout_primitives_are_rendered_natively(self) -> None:
        """Grid/SafeArea/Expanded/Divider need real branches, not a fallback."""
        source = self._source("ViewBuilder.java")
        for type_name in ("Grid", "SafeArea", "Expanded", "Flexible", "Divider"):
            assert f'case "{type_name}"' in source

    def test_grid_columns_share_width_by_weight(self) -> None:
        """Equal columns are what Row(weight=1) could not guarantee."""
        source = self._source("ViewBuilder.java")
        grid = _method_body(source, "private View buildGrid")
        assert "new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)" in grid

    def test_safe_area_uses_real_window_insets(self) -> None:
        source = self._source("ViewBuilder.java")
        assert "setOnApplyWindowInsetsListener" in source
        assert "getSystemWindowInsetTop" in source

    def test_constraints_are_applied(self) -> None:
        source = self._source("ViewBuilder.java")
        for marker in ("max_width", "max_height", "aspect_ratio", "setMinimumWidth"):
            assert marker in source

    def test_margin_is_applied_where_the_params_exist(self) -> None:
        """Regression: Style(margin=...) was silently dropped.

        applyStyle() runs while the view is still detached, so
        getLayoutParams() returns null and writing margins there did nothing.
        They belong on the params built by childParams().
        """
        source = self._source("ViewBuilder.java")
        assert "private void applyMargin(" in source
        assert "applyBoxStyle(params, style)" in source

        # ...and the old, ineffective path must not come back.
        assert "getLayoutParams()" not in _method_body(source, "private void applyStyle(")

    def test_margin_adds_to_spacing_instead_of_replacing_it(self) -> None:
        """A container's spacing and a widget's margin must both survive."""
        source = self._source("ViewBuilder.java")
        margin = _method_body(source, "private void applyMargin(")
        for field in ("leftMargin", "topMargin", "rightMargin", "bottomMargin"):
            assert f"params.{field} +=" in margin

    def test_style_width_and_height_reach_the_layout(self) -> None:
        """Style(width=..., height=...) had no effect on device at all."""
        source = self._source("ViewBuilder.java")
        assert 'dimension(style, "width")' in source
        assert 'dimension(style, "height")' in source
        assert "MATCH_PARENT" in source[source.index("private int dimension(") :]

    def test_elevation_is_applied(self) -> None:
        source = self._source("ViewBuilder.java")
        assert "setElevation" in source

    def test_scrollview_children_use_the_shared_sizing_rules(self) -> None:
        """A scrolled child gets the same margins and flex as a Column child."""
        source = self._source("ViewBuilder.java")
        scroll = _method_body(source, "private View buildScroll")
        assert "childParams(childNode, orientation" in scroll

    def test_grid_cells_keep_their_margin(self) -> None:
        source = self._source("ViewBuilder.java")
        assert "applyMargin(params" in _method_body(source, "private View buildGrid")


class TestDeviceFixes:
    """Regressions reported from a real phone."""

    def _java(self, name: str) -> str:
        from pymobile.resources import resource_path

        return resource_path("android", "java", name).read_text(encoding="utf-8")

    def _jni(self) -> str:
        from pymobile.resources import resource_path

        return resource_path("android", "jni", "pymobile_jni.c").read_text(encoding="utf-8")

    # -- HTTPS ------------------------------------------------------------
    def test_ca_bundle_is_discoverable(self) -> None:
        """Without a CA bundle every HTTPS request fails on device."""
        from pymobile.compiler.backends.native import _find_ca_bundle

        bundle = _find_ca_bundle()
        assert bundle is not None and bundle.exists()

    def test_ca_bundle_is_packaged(self, tmp_path: Path) -> None:
        from pymobile.compiler.backends.native import NativeBackend

        runtime = tmp_path / "runtime"
        (runtime / "lib" / "python3.14").mkdir(parents=True)
        backend = NativeBackend(
            ProjectConfig(root=tmp_path, package="com.example.a"),
            Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk"),
            runtime,
        )
        assets = backend.collect_assets([])
        assert "assets/python/etc/ssl/cert.pem" in assets

    def test_jni_points_openssl_at_the_bundle(self) -> None:
        source = self._jni()
        assert "SSL_CERT_FILE" in source
        assert "etc', 'ssl', 'cert.pem'" in source

    def test_python_runtime_reextracts_on_version_change(self) -> None:
        source = self._java("PythonRuntime.java")
        assert "currentVersionStamp" in source
        assert "getLongVersionCode" in source
        assert "readStamp" in source

    # -- scroll / keyboard -------------------------------------------------
    def test_renderer_updates_in_place(self) -> None:
        """Rebuilding every render reset scroll and closed the keyboard."""
        assert "boolean update(View view" in self._java("ViewBuilder.java")
        assert "builder.update(existing, root)" in self._java("MainActivity.java")

    def test_focused_input_is_never_overwritten(self) -> None:
        source = self._java("ViewBuilder.java")
        assert "!input.hasFocus()" in source

    def test_views_are_tagged_with_their_id(self) -> None:
        assert "view.setTag(id)" in self._java("ViewBuilder.java")

    def test_new_widgets_have_java_renderers(self) -> None:
        """Every new component must have a Java branch, or it silently renders
        as a plain label on a real device (untappable)."""
        source = self._java("ViewBuilder.java")
        for widget in (
            "ListTile",
            "List",
            "Slider",
            "Checkbox",
            "RatingBar",
            "Dropdown",
            "Chip",
            "Badge",
            "Stepper",
            "RadioButton",
            "RadioGroup",
            "SegmentedButtons",
            "Link",
            "DataTable",
            "Avatar",
        ):
            assert f'case "{widget}":' in source, f"missing Java renderer for {widget}"

    # -- permissions -------------------------------------------------------
    def test_permission_request_blocks_for_the_answer(self) -> None:
        """The old code returned before the user tapped, so it read 'denied'."""
        activity = self._java("MainActivity.java")
        assert "requestPermissionBlocking" in activity
        assert "onRequestPermissionsResult" in activity
        assert "CountDownLatch" in activity

    def test_permission_result_reaches_python(self) -> None:
        assert "(Ljava/lang/String;)Z" in self._jni()
        assert "public static boolean requestPermission" in self._java("Native.java")

    # -- vibration ---------------------------------------------------------
    def test_single_pulse_uses_a_waveform(self) -> None:
        """createOneShot is silently ignored on several devices."""
        source = self._java("DeviceServices.java")
        block = source[source.index("static void vibrate(Context") :]
        block = block[: block.index("\n    }")]
        assert "vibratePattern(" in block
        assert "createOneShot" not in block

    def test_waveforms_specify_amplitudes(self) -> None:
        assert "createWaveform(pattern, amplitudes, repeat)" in self._java("DeviceServices.java")

    def test_notify_creates_the_channel_without_a_jni_rebuild(self) -> None:
        """Java notify() must open the channel itself.

        That is what lets a default `pymobile build --native` post
        notifications without `PYMOBILE_BUILD_JNI=1`.
        """
        notify = _method_body(self._java("DeviceServices.java"), "static void notify(Context")
        assert "ensureChannel(" in notify
        jni = self._jni()
        assert '{"notify", py_notify' in jni
        assert '{"ensure_channel", py_ensure_channel' in jni

    def test_short_presets_are_perceptible(self) -> None:
        from pymobile.core.api.vibration import PRESETS

        for name, pattern in PRESETS.items():
            longest = max(pattern[1::2]) if len(pattern) > 1 else 0
            assert longest >= 50, f"preset {name} is too short to be felt"


class TestCaBundleDiscovery:
    """Finding root certificates must work on every host OS.

    Reported from Windows: `no CA bundle found; HTTPS requests will fail on
    device`. Windows keeps its roots in a registry-backed store, so the
    file-path probing that works on Linux finds nothing.
    """

    def test_certifi_is_preferred(self) -> None:
        from pymobile.compiler.backends.native import _find_ca_bundle

        bundle = _find_ca_bundle()
        assert bundle is not None
        assert bundle.read_text(encoding="ascii", errors="ignore").count("BEGIN CERTIFICATE") > 10

    def test_windows_store_export(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The registry store is turned into a PEM file."""
        import ssl

        from pymobile.compiler.backends.native import _export_windows_trust_store

        sample = b"0\x82\x01\x00"  # not a real certificate, only the DER->PEM wrapper is exercised
        monkeypatch.setattr(
            ssl, "enum_certificates", lambda store: [(sample, "x509_asn", True)], raising=False
        )
        bundle = _export_windows_trust_store(tmp_path)
        assert bundle is not None
        assert "BEGIN CERTIFICATE" in bundle.read_text(encoding="ascii")

    def test_certificates_without_tls_trust_are_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ssl

        from pymobile.compiler.backends.native import _export_windows_trust_store

        monkeypatch.setattr(
            ssl,
            "enum_certificates",
            lambda store: [(b"0\x82", "x509_asn", {"1.3.6.1.5.5.7.3.4"})],
            raising=False,
        )
        assert _export_windows_trust_store(tmp_path) is None

    def test_tls_purpose_is_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import ssl

        from pymobile.compiler.backends.native import _export_windows_trust_store

        monkeypatch.setattr(
            ssl,
            "enum_certificates",
            lambda store: [(b"0\x82", "x509_asn", {"1.3.6.1.5.5.7.3.1"})],
            raising=False,
        )
        assert _export_windows_trust_store(tmp_path) is not None

    def test_absent_store_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On POSIX ssl.enum_certificates does not exist at all."""
        import ssl

        from pymobile.compiler.backends.native import _export_windows_trust_store

        monkeypatch.delattr(ssl, "enum_certificates", raising=False)
        assert _export_windows_trust_store(tmp_path) is None

    def test_warning_tells_the_user_what_to_do(self) -> None:
        native = Path(__file__).resolve().parents[1] / "compiler" / "backends" / "native.py"
        source = native.read_text(encoding="utf-8")
        assert "pip install certifi" in source


class TestPermissionFlow:
    """Runtime permissions reported as broken on a real device.

    Two independent causes: the bridge threw away the dialog result, and the
    demo asked for permissions that were never declared in the manifest — which
    Android denies silently, without showing anything to the user.
    """

    def test_bridge_trusts_the_dialog_result(self) -> None:
        """A granted permission must be reported even if the follow-up
        has_permission() call has not caught up yet."""
        from pymobile.core.bridge.android import AndroidBridge

        class Native:
            def __init__(self) -> None:
                self.asked: list[str] = []

            def has_permission(self, permission: str) -> bool:
                return False  # system state lags behind the dialog

            def request_permission(self, permission: str) -> bool:
                self.asked.append(permission)
                return True  # the user tapped "Allow"

        bridge = AndroidBridge()
        bridge._native = Native()
        result = bridge.request_permissions(["android.permission.CAMERA"])
        assert result == {"android.permission.CAMERA": True}

    def test_denied_permission_is_reported(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        class Native:
            def has_permission(self, permission: str) -> bool:
                return False

            def request_permission(self, permission: str) -> bool:
                return False

        bridge = AndroidBridge()
        bridge._native = Native()
        assert bridge.request_permissions(["android.permission.CAMERA"]) == {
            "android.permission.CAMERA": False
        }

    def test_already_granted_skips_the_dialog(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        class Native:
            def __init__(self) -> None:
                self.asked: list[str] = []

            def has_permission(self, permission: str) -> bool:
                return True

            def request_permission(self, permission: str) -> bool:
                self.asked.append(permission)
                return True

        bridge = AndroidBridge()
        bridge._native = Native()
        bridge.request_permissions(["android.permission.CAMERA"])
        assert bridge._native.asked == []

    def test_build_warns_about_undeclared_permissions(self, tmp_path: Path) -> None:
        """The silent-denial trap must be caught at build time."""
        from pymobile.compiler.pipeline import BuildPipeline

        (tmp_path / "main.py").write_text(
            "from pymobile import Permission\n"
            "app.permissions.request(Permission.CAMERA, Permission.RECORD_AUDIO)\n",
            encoding="utf-8",
        )
        config = ProjectConfig(
            root=tmp_path,
            package="com.example.a",
            permissions=["android.permission.INTERNET"],
        )
        pipeline = BuildPipeline(config)
        pipeline._check_requested_permissions(pipeline._collect())
        joined = " ".join(pipeline.warnings)
        assert "CAMERA" in joined
        assert "RECORD_AUDIO" in joined

    def test_no_warning_when_everything_is_declared(self, tmp_path: Path) -> None:
        from pymobile.compiler.pipeline import BuildPipeline

        (tmp_path / "main.py").write_text(
            "from pymobile import Permission\napp.permissions.request(Permission.CAMERA)\n",
            encoding="utf-8",
        )
        config = ProjectConfig(
            root=tmp_path,
            package="com.example.a",
            permissions=["android.permission.INTERNET", "android.permission.CAMERA"],
        )
        pipeline = BuildPipeline(config)
        pipeline._check_requested_permissions(pipeline._collect())
        assert not any("CAMERA" in warning for warning in pipeline.warnings)


class TestPermissionDialogTiming:
    """Third cause of the permission failures seen on a real device.

    Python starts from onCreate, so a permission requested immediately reaches
    the system before the activity window exists. Android drops such a request
    without showing anything, which is indistinguishable from a denial.
    """

    def _activity(self) -> str:
        from pymobile.resources import resource_path

        return resource_path("android", "java", "MainActivity.java").read_text(encoding="utf-8")

    def test_dialog_waits_for_the_resumed_state(self) -> None:
        source = self._activity()
        assert "resumedLatch" in source
        assert "resumedLatch.await" in source

    def test_resume_releases_the_gate(self) -> None:
        source = self._activity()
        assert "protected void onResume()" in source
        assert "resumedLatch.countDown()" in source

    def test_wait_is_bounded(self) -> None:
        """A missing onResume must not hang the interpreter forever."""
        source = self._activity()
        gate = source[source.index("resumedLatch.await") :][:120]
        assert "TimeUnit.SECONDS" in gate

    def test_gate_precedes_the_request(self) -> None:
        source = self._activity()
        block = source[source.index("static boolean requestPermissionBlocking") :]
        block = block[: block.index("return request.granted")]
        assert block.index("resumedLatch.await") < block.index("requestPermissions(")


class TestNoSslWarning:
    """--no-ssl silently breaks HTTPS unless the build says so."""

    def _project(self, tmp_path: Path, source: str) -> ProjectConfig:
        (tmp_path / "main.py").write_text(source, encoding="utf-8")
        return ProjectConfig(root=tmp_path, package="com.example.a", no_ssl=True)

    def test_warns_when_the_app_uses_http(self, tmp_path: Path) -> None:
        from pymobile.compiler.pipeline import BuildPipeline

        config = self._project(tmp_path, "from pymobile import HttpClient\nHttpClient()\n")
        pipeline = BuildPipeline(config)
        pipeline._validate()
        assert any("--no-ssl" in warning for warning in pipeline.warnings)

    def test_silent_when_the_app_never_goes_online(self, tmp_path: Path) -> None:
        from pymobile.compiler.pipeline import BuildPipeline

        config = self._project(tmp_path, "print('offline')\n")
        pipeline = BuildPipeline(config)
        pipeline._validate()
        assert not any("--no-ssl" in warning for warning in pipeline.warnings)

    def test_no_warning_without_the_flag(self, tmp_path: Path) -> None:
        from pymobile.compiler.pipeline import BuildPipeline

        (tmp_path / "main.py").write_text("from pymobile import HttpClient\n", encoding="utf-8")
        config = ProjectConfig(root=tmp_path, package="com.example.a")
        pipeline = BuildPipeline(config)
        pipeline._validate()
        assert not any("--no-ssl" in warning for warning in pipeline.warnings)
