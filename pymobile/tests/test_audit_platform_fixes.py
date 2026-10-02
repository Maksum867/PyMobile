"""Regression tests for the platform audit: cache identity, assets, launcher.

The Android layer cannot run on the desktop, so — as everywhere else in this
suite — Java and C fixes are checked through the sources and the prebuilt
binaries that actually ship (``test_jni_utf8.py`` compiles and runs the string
conversion itself). The Python-side fixes are checked by behaviour.

Each test names the defect it pins down, because several of them describe
contracts that are invisible until they break: a cache that hands back an APK
signed with the wrong key, a disabled field that accepts input, a launcher that
runs a file the APK does not contain.
"""

from __future__ import annotations

import io
import json
import socket
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pymobile import App, Column, Label, Screen, TextInput, Widget
from pymobile.compiler.backends.native import NativeBackend
from pymobile.compiler.cache import BuildCache
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.toolchain import Toolchain, normalise_java_home
from pymobile.core.bridge.stub import StubBridge
from pymobile.core.config import ProjectConfig

PACKAGE = Path(__file__).resolve().parents[1]
ANDROID_DIR = PACKAGE / "resources" / "android"
PREBUILT = ANDROID_DIR / "prebuilt"
JNI_SOURCE = (ANDROID_DIR / "jni" / "pymobile_jni.c").read_text(encoding="utf-8")


def _project(tmp_path: Path, **flags: Any) -> ProjectConfig:
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return ProjectConfig(root=tmp_path, package="com.example.a", **flags)


def _backend(tmp_path: Path, **flags: Any) -> NativeBackend:
    runtime = tmp_path / "runtime"
    (runtime / "lib" / "python3.14").mkdir(parents=True, exist_ok=True)
    config = ProjectConfig(root=tmp_path, package="com.example.a", **flags)
    return NativeBackend(
        config, Toolchain(tmp_path, tmp_path, tmp_path / "j.jar", tmp_path / "jdk"), runtime
    )


def _elf_load_alignments(path: Path) -> list[int]:
    """``p_align`` of every ``PT_LOAD`` segment, read without readelf."""
    data = path.read_bytes()
    assert data[:4] == b"\x7fELF"
    is64 = data[4] == 2
    endian = "<" if data[5] == 1 else ">"
    if is64:
        phoff = struct.unpack_from(endian + "Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", data, 54)
        aligns = []
        for index in range(phnum):
            offset = phoff + index * phentsize
            p_type = struct.unpack_from(endian + "I", data, offset)[0]
            if p_type == 1:  # PT_LOAD
                aligns.append(struct.unpack_from(endian + "Q", data, offset + 48)[0])
        return aligns
    phoff, phentsize, phnum = struct.unpack_from(endian + "III", data, 28)
    aligns = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        p_type = struct.unpack_from(endian + "I", data, offset)[0]
        if p_type == 1:
            aligns.append(struct.unpack_from(endian + "I", data, offset + 28)[0])
    return aligns


# --------------------------------------------------------------------------
# Disabled widgets (PM-15)
# --------------------------------------------------------------------------
class TestDisabledInput:
    def test_a_disabled_text_input_refuses_device_input(self) -> None:
        seen: list[str] = []
        field = TextInput("old", enabled=False, on_change=seen.append)
        field._ui_set_value("new")
        assert field.value == "old"
        assert seen == []

    def test_a_disabled_text_input_still_accepts_programmatic_values(self) -> None:
        """``enabled`` gates the user, not the program."""
        field = TextInput("old", enabled=False)
        field.value = "from code"
        assert field.value == "from code"

    def test_reenabled_input_accepts_input_again(self) -> None:
        seen: list[str] = []
        field = TextInput("old", on_change=seen.append)
        field.enabled = False
        field._ui_set_value("ignored")
        field.enabled = True
        field._ui_set_value("typed")
        assert field.value == "typed"
        assert seen == ["typed"]

    def test_the_dispatcher_drops_events_for_a_disabled_widget(self) -> None:
        """Queued events and front ends that ignore ``enabled`` are covered too."""
        seen: list[str] = []

        class Form(Screen):
            def build(self) -> Widget:
                self.field = TextInput("old", enabled=False, on_change=seen.append, id="field")
                return Column(self.field)

        screen = Form()
        app = App(screen, bridge=StubBridge(verbose=False))
        app.run(screen)
        try:
            app.handle_ui_event("field", "change", "pressed by someone")
            assert screen.field.value == "old"  # type: ignore[attr-defined]
            assert seen == []
        finally:
            app.stop()


# --------------------------------------------------------------------------
# Cache identity (PM-17, PM-18) and build metadata (PM-24, PM-29)
# --------------------------------------------------------------------------
class TestCacheIdentity:
    def _fingerprint(self, config: ProjectConfig, **kwargs: Any) -> str:
        build = BuildPipeline(config, native=True, use_cache=False, **kwargs)
        return build._fingerprint(build._collect())

    def test_the_signer_is_part_of_the_fingerprint(self, tmp_path: Path) -> None:
        """A release build must not reuse a debug-signed APK (the audit repro)."""
        config = _project(tmp_path)
        debug = self._fingerprint(config)
        release = self._fingerprint(
            config, keystore=tmp_path / "release.jks", key_alias="upload"
        )
        other = self._fingerprint(
            config, keystore=tmp_path / "release.jks", key_alias="another"
        )
        assert debug != release
        assert release != other

    def test_passwords_are_not_part_of_the_fingerprint(self, tmp_path: Path) -> None:
        """Only the identity is hashed; a secret must not reach cache metadata."""
        config = _project(tmp_path)
        first = self._fingerprint(
            config,
            keystore=tmp_path / "release.jks",
            key_alias="upload",
            keystore_password="one",
            key_password="two",
        )
        second = self._fingerprint(
            config,
            keystore=tmp_path / "release.jks",
            key_alias="upload",
            keystore_password="three",
            key_password="four",
        )
        assert first == second

    def test_a_cached_native_build_is_reported_as_native(self, tmp_path: Path) -> None:
        """The cached result used to take ``native=False`` from the dataclass."""
        config = _project(tmp_path)
        build = BuildPipeline(config, native=True, use_cache=True)
        sources = build._collect()
        artifact = config.output_path / config.apk_name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"apk")
        cache = BuildCache(config.output_path)
        cache.save(
            build._fingerprint(sources),
            artifact,
            mode=build.build_mode(),
            signer=build.signer_identity(),
            icon="1",
        )
        result = build.run()
        assert result.cached and result.native
        assert result.apk == artifact

    def test_a_cache_entry_from_another_mode_is_ignored(self, tmp_path: Path) -> None:
        config = _project(tmp_path)
        build = BuildPipeline(config, native=True, use_cache=True)
        sources = build._collect()
        artifact = config.output_path / config.apk_name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"apk")
        cache = BuildCache(config.output_path)
        cache.save(
            build._fingerprint(sources),
            artifact,
            mode="structural",  # an artifact of the other kind
            signer=build.signer_identity(),
            icon="0",
        )
        hit = cache.entry(build._fingerprint(sources))
        assert hit is not None
        assert build._cache_hit_is_usable(hit) is False


class TestBuildMetadata:
    def test_the_properties_file_names_the_packaged_entry_point(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        backend.set_entrypoint("startup.py", "pyc")
        backend.set_payload_digest("abc123def456" * 4)
        metadata = backend._asset_metadata().decode("utf-8")
        assert "entrypoint=startup.py" in metadata
        assert "entrypoint_type=pyc" in metadata
        # The launcher compares this stamp with the installed one; without it
        # an `edit → build → adb install -r` kept the old Python on disk.
        assert "payload=abc123def456abc123def456abc123de" in metadata

    def test_default_entrypoint_is_main_py(self, tmp_path: Path) -> None:
        metadata = _backend(tmp_path)._asset_metadata().decode("utf-8")
        assert "entrypoint=main.py" in metadata and "entrypoint_type=py" in metadata


# --------------------------------------------------------------------------
# Assets (PM-20)
# --------------------------------------------------------------------------
class TestAssetSuffixes:
    def test_extra_suffixes_travel_into_the_package(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "seed.csv").write_text("a,b\n", encoding="utf-8")
        config = _project(tmp_path, asset_suffixes=[".csv"])
        sources = BuildPipeline(config, use_cache=False)._collect()
        assert any(path.name == "seed.csv" for path in sources.files)

    def test_a_leading_dot_is_required(self, tmp_path: Path) -> None:
        from pymobile.errors import ConfigError

        (tmp_path / "main.py").write_text("print('x')\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid asset suffix"):
            ProjectConfig(root=tmp_path, package="com.example.a", asset_suffixes=["csv"])

    def test_left_out_files_are_reported(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "seed.csv").write_text("a,b\n", encoding="utf-8")
        build = BuildPipeline(_project(tmp_path), use_cache=False)
        build._collect()
        assert any(".csv" in warning for warning in build.warnings)


# --------------------------------------------------------------------------
# Launcher sources: entry point, status, extraction stamp (PM-24/27/29/30)
# --------------------------------------------------------------------------
class TestLauncherSources:
    activity = (ANDROID_DIR / "java" / "MainActivity.java").read_text(encoding="utf-8")
    runtime = (ANDROID_DIR / "java" / "PythonRuntime.java").read_text(encoding="utf-8")

    def test_the_entry_point_is_not_hard_coded(self) -> None:
        assert 'PythonRuntime.run(getApplicationContext(), "main.py")' not in self.activity
        assert "entrypointFromAssets" in self.activity
        assert "entrypoint_type" in self.runtime

    def test_the_runner_falls_back_to_bytecode(self) -> None:
        assert "resolve_entry" in JNI_SOURCE
        assert '".pyc"' in JNI_SOURCE or "pyc" in JNI_SOURCE

    def test_a_startup_failure_is_not_reported_as_success(self) -> None:
        # The old runner printed the traceback and returned 0, so the Java side
        # showed the "Starting Python…" placeholder forever.
        assert "PyExc_SystemExit" in JNI_SOURCE
        assert "PyErr_Display" in JNI_SOURCE
        assert "except SystemExit" not in JNI_SOURCE

    def test_the_extraction_stamp_includes_the_payload(self) -> None:
        assert 'values.get("payload")' in self.runtime or "payload" in self.runtime
        assert "versionCodeOrUnknown" in self.runtime

    def test_a_failed_preparation_can_be_retried(self) -> None:
        # ``started = true`` was set before extraction, so one IOException meant
        # "already running" for the rest of the process's life.
        assert "boolean started = false" not in self.runtime
        assert "State.PREPARING" in self.runtime and "State.FAILED" in self.runtime


# --------------------------------------------------------------------------
# JNI sources: text conversion, queue discipline (PM-25 and the S-risks)
# --------------------------------------------------------------------------
class TestJniSources:
    def test_application_text_is_not_modified_utf8(self) -> None:
        # GetStringUTFChars/NewStringUTF speak Modified UTF-8: an emoji arrives
        # as CESU-8 and CPython's strict UTF-8 decoder rejects it.
        assert "GetStringUTFChars(env" not in JNI_SOURCE
        assert "NewStringUTF(env" not in JNI_SOURCE
        assert "GetStringChars" in JNI_SOURCE and "NewString(" in JNI_SOURCE
        assert "jstring_to_utf8" in JNI_SOURCE and "utf8_to_jstring" in JNI_SOURCE

    def test_events_carry_their_length(self) -> None:
        # A payload may contain U+0000, which no C string can hold.
        assert '"(s#s#s#)"' in JNI_SOURCE
        assert "text_copy" in JNI_SOURCE

    def test_the_event_wait_rechecks_its_predicate(self) -> None:
        assert "while (!q_head && !q_stopped)" in JNI_SOURCE
        assert "ETIMEDOUT" in JNI_SOURCE

    def test_the_event_queue_is_bounded(self) -> None:
        assert "MAX_PENDING_EVENTS" in JNI_SOURCE
        assert "dropping the oldest event" in JNI_SOURCE


# --------------------------------------------------------------------------
# Shipped artifacts (PM-26)
# --------------------------------------------------------------------------
class TestShippedArtifacts:
    @pytest.mark.parametrize("abi", ["arm64-v8a", "x86_64"])
    def test_the_bridges_are_aligned_for_16kb_pages(self, abi: str) -> None:
        """A 4 KB-aligned ``PT_LOAD`` cannot be mapped on a 16 KB-page device."""
        library = PREBUILT / abi / "libpymobile.so"
        alignments = _elf_load_alignments(library)
        assert alignments, f"{library} has no PT_LOAD segments"
        assert set(alignments) == {0x4000}, alignments

    def test_the_launcher_dex_understands_the_build_metadata(self) -> None:
        dex = (PREBUILT / "arm64-v8a" / "classes.dex").read_bytes()
        assert b"pymobile.properties" in dex
        assert b"entrypoint_type" in dex

    def test_the_java_sources_are_the_packaged_ones(self) -> None:
        """The dex must be built from these sources, not an older revision."""
        dex = (PREBUILT / "arm64-v8a" / "classes.dex").read_bytes()
        for marker in (
            b"entrypointFromAssets",
            b"versionCodeOrUnknown",
            b"readBuildProperties",
        ):
            assert marker in dex, f"{marker.decode()} missing from the launcher dex"


# --------------------------------------------------------------------------
# Web preview (PM-28)
# --------------------------------------------------------------------------
class _Home(Screen):
    title = "Home"

    def __init__(self) -> None:
        super().__init__()
        self.taps = 0

    def build(self) -> Widget:
        return Column(Label("Hello"), Label(f"taps: {self.taps}", id="counter"))


def _preview(host: str) -> tuple[App, Any, int]:
    from pymobile.core.ui.web import WebPreview

    app = App("Demo", bridge=StubBridge(verbose=False))
    app.run(_Home())
    preview = WebPreview(app, host=host, port=0)
    return app, preview, preview.start_background()


def _open(url: str, **kwargs: Any) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5, **kwargs) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


class TestWebPreviewSecurity:
    def test_a_loopback_preview_needs_no_token(self) -> None:
        app, preview, port = _preview("127.0.0.1")
        try:
            assert preview.exposed is False and preview.token == ""
            status, page = _open(f"http://127.0.0.1:{port}/")
            assert status == 200 and "Home" in page
            assert "<script" in page
        finally:
            preview.stop()
            app.stop()

    def test_an_exposed_preview_refuses_requests_without_the_token(self) -> None:
        app, preview, port = _preview("0.0.0.0")
        try:
            assert preview.exposed is True and preview.token
            status, body = _open(f"http://127.0.0.1:{port}/")
            assert status == 403 and "token" in body
            status, _ = _open(f"http://127.0.0.1:{port}/state")
            assert status == 403
        finally:
            preview.stop()
            app.stop()

    def test_the_token_opens_the_page_and_the_state(self) -> None:
        app, preview, port = _preview("0.0.0.0")
        try:
            status, page = _open(f"http://127.0.0.1:{port}/?t={preview.token}")
            assert status == 200 and "Home" in page
            state = urllib.request.Request(
                f"http://127.0.0.1:{port}/state",
                headers={"X-PMB-Token": preview.token},
            )
            status, body = _open(state)
            assert status == 200
            # The token in the header is accepted exactly like ``?t=…``.
            assert json.loads(body)["title"] == "Demo"
        finally:
            preview.stop()
            app.stop()

    def test_unknown_routes_are_refused(self) -> None:
        app, preview, port = _preview("127.0.0.1")
        try:
            assert _open(f"http://127.0.0.1:{port}/anything")[0] == 404
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/unexpected-path",
                method="POST",
                data=json.dumps({"id": "counter", "kind": "press", "value": ""}).encode(),
                headers={"Content-Type": "application/json"},
            )
            assert _open(request)[0] == 404
        finally:
            preview.stop()
            app.stop()

    def test_events_must_be_json_and_small(self) -> None:
        app, preview, port = _preview("127.0.0.1")
        try:
            plain = urllib.request.Request(
                f"http://127.0.0.1:{port}/event",
                method="POST",
                data=b"id=counter",
                headers={"Content-Type": "text/plain"},
            )
            assert _open(plain)[0] == 415
            huge = urllib.request.Request(
                f"http://127.0.0.1:{port}/event",
                method="POST",
                data=b"x" * (128 * 1024),
                headers={"Content-Type": "application/json"},
            )
            assert _open(huge)[0] == 413
        finally:
            preview.stop()
            app.stop()

    def test_a_foreign_origin_is_refused(self) -> None:
        """A page on another site must not be able to drive the preview."""
        app, preview, port = _preview("0.0.0.0")
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/event",
                method="POST",
                data=json.dumps({"id": "counter", "kind": "press", "value": ""}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-PMB-Token": preview.token,
                    "Origin": "https://evil.example",
                },
            )
            assert _open(request)[0] == 403
        finally:
            preview.stop()
            app.stop()

    def test_the_page_cannot_be_framed(self) -> None:
        app, preview, port = _preview("127.0.0.1")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                assert response.headers["X-Frame-Options"] == "DENY"
        finally:
            preview.stop()
            app.stop()


# --------------------------------------------------------------------------
# JDK discovery (PM-19)
# --------------------------------------------------------------------------

class TestRejectedBodiesAreDrained:
    """A rejected request must not leave its body in the socket.

    The preview answers 413/415/403 without using the body. If those bytes are
    still unread when the handler closes, the client — which is often still
    sending — sees the connection reset instead of the status line (on Windows
    ``ConnectionAbortedError: [WinError 10053]``, and the 413 assertion in the
    security test above failed with exactly that). The fix reads the body
    first, bounded.
    """

    def test_the_body_is_consumed(self) -> None:
        from pymobile.core.ui.web import _drain_body

        body = b"x" * 4096
        stream = io.BytesIO(body)
        assert _drain_body(stream, len(body)) == len(body)
        assert stream.read() == b""

    def test_a_lying_content_length_cannot_read_forever(self) -> None:
        from pymobile.core.ui.web import DRAIN_LIMIT, _drain_body

        stream = io.BytesIO(b"x" * 32)
        # A client claiming a gigabyte must not make the preview read that far:
        # end of file stops it, and the limit caps a body that really is huge.
        assert _drain_body(stream, 10**9) == 32
        big = io.BytesIO(b"x" * (DRAIN_LIMIT + 1024))
        assert _drain_body(big, DRAIN_LIMIT + 1024) == DRAIN_LIMIT

    def test_an_oversized_event_still_answers_and_closes_cleanly(self) -> None:
        """The Windows symptom, checked at the socket level.

        A raw connection sends the oversized body and then reads until the
        server closes. With the body left unread the reset wins and this read
        fails (``ConnectionResetError``) or comes back empty, which is what
        ``urllib`` reported on Windows as ``WinError 10053``.
        """
        _app, preview, port = _preview("127.0.0.1")
        try:
            body = b"x" * (128 * 1024)
            request = (
                f"POST /event?t={preview.token} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode() + body
            connection = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                connection.sendall(request)
                chunks: list[bytes] = []
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:  # the server closed: FIN, not a reset
                        break
                    chunks.append(chunk)
            except ConnectionResetError as error:  # pragma: no cover - the bug
                pytest.fail(f"the connection was reset instead of closed: {error}")
            finally:
                connection.close()
            response = b"".join(chunks)
            head, _, answer = response.partition(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 413"), response[:60]
            declared = [
                line for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")
            ]
            assert declared, head
            assert len(answer) == int(declared[0].split(b":")[1])
            assert b"event body larger than" in answer
        finally:
            preview.stop()


class TestJdkDiscovery:
    def test_a_macos_bundle_is_normalised(self, tmp_path: Path) -> None:
        outer = tmp_path / "jdk-17.0.13+11"
        (outer / "Contents" / "Home" / "bin").mkdir(parents=True)
        (outer / "Contents" / "Home" / "bin" / "javac").write_text("", encoding="utf-8")
        assert normalise_java_home(outer) == outer / "Contents" / "Home"

    def test_a_directory_without_javac_is_not_a_home(self, tmp_path: Path) -> None:
        empty = tmp_path / "jdk-17-not-a-jdk"
        empty.mkdir()
        assert normalise_java_home(empty) is None
        assert normalise_java_home(None) is None

    def test_the_bundled_install_is_normalised_too(self, tmp_path: Path) -> None:
        from pymobile.compiler.sdk_installer import _first_jdk_home

        home = tmp_path / "jdk"
        bundled = home / "jdk-17.0.13+11"
        (bundled / "Contents" / "Home" / "bin").mkdir(parents=True)
        (bundled / "Contents" / "Home" / "bin" / "javac").write_text("", encoding="utf-8")
        assert _first_jdk_home(home) == bundled / "Contents" / "Home"


# --------------------------------------------------------------------------
# Documentation that disagreed with the code (PM-21, PM-22)
# --------------------------------------------------------------------------
class TestDocumentation:
    readme = (PACKAGE.parent / "README.md").read_text(encoding="utf-8")

    def test_the_counter_example_keeps_its_state_out_of_build(self) -> None:
        section = self.readme.split("class Home(Screen):", 1)[1].split("```", 1)[0]
        assert "def __init__" in section
        assert "self.taps = 0" in section.split("def build", 1)[0]
        assert "self.taps = 0" not in section.split("def build", 1)[1]

    def test_build_running_again_does_not_reset_state(self) -> None:
        """The pattern the README teaches, checked by behaviour."""

        class Counter(Screen):
            def __init__(self) -> None:
                super().__init__()
                self.taps = 0

            def build(self) -> Widget:
                self.counter = Label(f"Taps: {self.taps}")
                return Column(self.counter)

        screen = Counter()
        app = App(screen, bridge=StubBridge(verbose=False))
        app.run(screen)
        try:
            screen.taps = 3  # type: ignore[attr-defined]
            screen.refresh()  # a theme switch rebuilds exactly like this
            tree = app.render()
            assert tree is not None
            assert "Taps: 3" in json.dumps(tree)
        finally:
            app.stop()

    def test_the_style_recipe_is_executable(self) -> None:
        assert "from dataclasses import replace" in self.readme
        assert "FrozenInstanceError" in self.readme
