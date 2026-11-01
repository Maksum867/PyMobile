"""Regression tests for the 2026-11 audit findings (PM-01…PM-09, DOC, AUDIO).

Each class pins one finding from the developer handoff:

* PM-01…PM-03, PM-08 — Storage shared state, load lifecycle, schema init,
  quarantine of invalid UTF-8;
* PM-05…PM-07 — HTTP charset parsing, unknown-codec policy, redirect URLs;
* PM-09 — the SDK installer making every launcher executable;
* DOC-01/DOC-02 — the Slider inclusive maximum and the DatePicker clamp
  contract;
* PM-12 / AUDIO-01 / AUDIO-02 — audio duration refresh, on_end without
  on_tick, and the watcher surviving an asynchronous prepare.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
import wave
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pymobile import App, Slider, Storage
from pymobile.core.api import audio as audio_module
from pymobile.core.bridge import StubBridge
from pymobile.core.net.http import HttpClient, Response, _charset_from_headers
from pymobile.core.ui.pickers import DatePicker
from pymobile.errors import PyMobileError, ResourceError


# --------------------------------------------------------------------------
# PM-01 — namespaces share one file state instead of overwriting each other
# --------------------------------------------------------------------------
class TestSharedNamespaceState:
    def test_clearing_one_namespace_keeps_the_other(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        auth = Storage(path, namespace="auth")
        cache = Storage(path, namespace="cache")
        auth["token"] = "first"
        cache["page"] = 2
        auth["token"] = "renewed"
        cache.clear()
        assert Storage(path, namespace="auth")["token"] == "renewed"
        assert Storage(path, namespace="cache").keys() == []

    def test_interleaved_writes_do_not_roll_back(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        auth = Storage(path, namespace="auth")
        cache = Storage(path, namespace="cache")
        auth["counter"] = 0
        cache["page0"] = 0
        auth["counter"] = 1
        cache["page1"] = 1
        assert cache.delete("page0") is True
        cache.clear()
        auth["counter"] = 2
        assert auth["counter"] == 2
        assert cache.keys() == []

    def test_two_owners_of_one_namespace_share_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        first, second = Storage(path), Storage(path)
        first["k"] = 1
        assert second["k"] == 1
        second["k"] = 2
        assert first["k"] == 2

    def test_a_transaction_covers_writes_from_another_owner(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        first, second = Storage(path), Storage(path)
        with first.transaction():
            first["a"] = 1
            second["b"] = 2
        assert Storage(path)["a"] == 1 and Storage(path)["b"] == 2

    def test_a_failed_transaction_rolls_back_both_owners(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        first, second = Storage(path), Storage(path)
        first["keep"] = "original"
        with pytest.raises(RuntimeError), first.transaction():
            first["keep"] = "changed"
            second["other"] = "x"
            raise RuntimeError("boom")
        assert first["keep"] == "original"
        assert "other" not in Storage(path)


# --------------------------------------------------------------------------
# PM-02 — a failed load/migration is sticky, never bypassed
# --------------------------------------------------------------------------
class TestStickyLoadFailure:
    def test_newer_schema_blocks_every_following_operation(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        Storage(path)["__schema_version__"] = 3
        store = Storage(path, schema_version=1, migrations={1: lambda data: data})
        with pytest.raises(PyMobileError):
            store.get("x")
        before = path.read_bytes()
        for attempt in (
            lambda: store.set("unexpected_write", True),
            lambda: store.__setitem__("k", 1),
            lambda: store.save(),
            lambda: store.update("k", lambda value: 1, default=0),
            lambda: store.delete("k"),
        ):
            with pytest.raises(PyMobileError):
                attempt()
        assert path.read_bytes() == before

    def test_a_failed_migration_never_serves_stale_data(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        Storage(path)["v"] = 1

        def explode(data: dict) -> dict:
            raise RuntimeError("migration exploded")

        store = Storage(path, schema_version=1, migrations={1: explode})
        with pytest.raises(RuntimeError, match="exploded"):
            store.get("v")
        with pytest.raises(RuntimeError, match="exploded"):
            store["v"] = 2
        with pytest.raises(RuntimeError, match="exploded"):
            store.get("v")


# --------------------------------------------------------------------------
# PM-03 — a fresh schema store initialises immediately, restarts cleanly
# --------------------------------------------------------------------------
class TestFreshStoreSchema:
    def test_restart_does_not_replay_the_migration_over_user_data(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.json"

        def migrate(data: dict) -> dict:
            return {**data, "theme": "dark"}

        store = Storage(path, schema_version=1, migrations={1: migrate})
        store["theme"] = "light"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["__schema_version__"] == 1
        reopened = Storage(path, schema_version=1, migrations={1: migrate})
        assert reopened["theme"] == "light"

    def test_the_initial_migration_runs_before_any_user_write(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = Storage(
            path, schema_version=1, migrations={1: lambda data: {**data, "theme": "dark"}}
        )
        assert store["theme"] == "dark"

    def test_namespaced_fresh_stores_initialise_too(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        migrate = lambda data: {**data, "theme": "dark"}  # noqa: E731
        first = Storage(path, namespace="a", schema_version=1, migrations={1: migrate})
        first["theme"] = "light"
        again = Storage(path, namespace="a", schema_version=1, migrations={1: migrate})
        assert again["theme"] == "light"

    def test_schema_zero_stores_still_create_nothing_on_read(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.json"
        store = Storage(path)
        assert store.keys() == []
        assert not path.exists()


# --------------------------------------------------------------------------
# PM-08 — invalid UTF-8 is quarantined like invalid JSON
# --------------------------------------------------------------------------
class TestCorruptionQuarantine:
    def test_invalid_utf8_is_moved_aside_with_its_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = b"\xff\xfe\x00"
        path.write_bytes(original)
        store = Storage(path)
        assert store.keys() == []
        backups = list(tmp_path.glob("state.json.corrupt-*"))
        assert backups and backups[0].read_bytes() == original

    def test_json_corruption_still_quarantines(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_bytes(b"{not json")
        assert Storage(path).keys() == []
        assert list(tmp_path.glob("state.json.corrupt-*"))

    def test_an_unreadable_file_is_a_hard_error_not_an_empty_store(
        self, tmp_path: Path
    ) -> None:
        if os.name == "nt" or (os.geteuid() == 0 if hasattr(os, "geteuid") else False):
            pytest.skip("permission bits are not enforced for this user")
        path = tmp_path / "state.json"
        path.write_text('{"a": 1}', encoding="utf-8")
        path.chmod(0)
        try:
            with pytest.raises(ResourceError):
                Storage(path).keys()
        finally:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


# --------------------------------------------------------------------------
# PM-05…PM-07 — charset parsing, codec policy, redirect URLs
# --------------------------------------------------------------------------
class TestCharsetHandling:
    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            ("text/plain; charset=utf-8", "utf-8"),
            ("text/plain; CHARSET=iso-8859-1", "iso-8859-1"),
            ("text/plain; CharSet = utf-8", "utf-8"),
            ('text/plain; charset="iso-8859-1"', "iso-8859-1"),
            ("text/plain; charset='us-ascii'", "us-ascii"),
            ("text/plain; boundary=----x; CHARSET=windows-1251", "windows-1251"),
            ("text/plain", ""),
            ("application/json", ""),
        ],
    )
    def test_charset_parameter_parsing(self, content_type: str, expected: str) -> None:
        assert _charset_from_headers({"content-type": content_type}) == expected

    def test_an_uppercase_charset_reaches_the_decoder(self) -> None:
        response = HttpClient._to_response(
            "http://example.invalid/",
            200,
            {"Content-Type": "text/plain; CHARSET=iso-8859-1"},
            b"caf\xe9",
        )
        assert response.encoding == "iso-8859-1"
        assert response.text == "café"

    def test_an_unknown_codec_falls_back_instead_of_leaking_lookuperror(self) -> None:
        response = Response(
            status=200,
            headers={},
            content=b"hello \xff",
            url="http://example.invalid/",
            encoding="not-a-real-codec",
        )
        assert response.encoding == "utf-8"
        assert "hello" in response.text  # replacement decoding, never LookupError


class TestRedirectUrls:
    @pytest.fixture
    def server(self) -> Iterator[str]:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/final")
                    self.end_headers()
                elif self.path == "/hop":
                    self.send_response(302)
                    self.send_header("Location", "/start")
                    self.end_headers()
                else:
                    body = b"missing"
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()

    def test_http_error_after_a_redirect_reports_the_final_url(self, server: str) -> None:
        response = HttpClient().get(f"{server}/start")
        assert response.status == 404
        assert response.url == f"{server}/final"

    def test_several_hops_still_report_the_final_url(self, server: str) -> None:
        response = HttpClient().get(f"{server}/hop")
        assert response.status == 404
        assert response.url == f"{server}/final"

    def test_a_direct_error_keeps_the_requested_url(self, server: str) -> None:
        response = HttpClient().get(f"{server}/final")
        assert response.status == 404
        assert response.url == f"{server}/final"


# --------------------------------------------------------------------------
# PM-09 — every cmdline launcher ends up executable
# --------------------------------------------------------------------------
class TestLauncherPermissions:
    def test_chmod_launchers_repairs_a_cache_without_the_exec_bit(
        self, tmp_path: Path
    ) -> None:
        from pymobile.compiler.sdk_installer import chmod_launchers

        if os.name == "nt":
            pytest.skip("Unix permission bits")
        tools = tmp_path / "bin"
        tools.mkdir()
        for name in ("sdkmanager", "avdmanager", "lint"):
            (tools / name).write_text("#!/bin/sh\n")
            (tools / name).chmod(0o644)
        (tools / "sdkmanager.bat").write_text("@echo off\r\n")
        chmod_launchers(tools)
        for name in ("sdkmanager", "avdmanager", "lint"):
            mode = (tools / name).stat().st_mode
            assert mode & stat.S_IXUSR, f"{name} is still not executable"
        # .bat files are untouched
        assert not ((tools / "sdkmanager.bat").stat().st_mode & stat.S_IXUSR)


# --------------------------------------------------------------------------
# DOC-01 / DOC-02 — documented component contracts
# --------------------------------------------------------------------------
class TestSliderContract:
    def test_an_off_grid_maximum_is_still_reachable(self) -> None:
        slider = Slider(0, minimum=0, maximum=10, step=3)
        slider.value = 10
        assert slider.value == 10
        assert slider.fraction == 1.0

    def test_off_grid_interior_values_still_snap(self) -> None:
        slider = Slider(0, minimum=0, maximum=10, step=3)
        slider.value = 9.9
        assert slider.value == 9

    def test_a_nonzero_minimum_keeps_the_endpoint(self) -> None:
        slider = Slider(2, minimum=2, maximum=10, step=3)
        slider.value = 10
        assert slider.value == 10


class TestDatePickerContract:
    def test_out_of_range_values_clamp_as_implemented(self) -> None:
        picker = DatePicker("2026-06-01", minimum="2026-01-01", maximum="2026-12-31")
        picker.set_value("2027-01-01")
        assert picker.value == "2026-12-31"

    def test_invalid_dates_still_raise(self) -> None:
        with pytest.raises(ValueError):
            DatePicker("19.09.2026")
        with pytest.raises(ValueError):
            DatePicker("2026-06-01").set_value("nope")

    def test_the_module_docstring_documents_the_clamp(self) -> None:
        from pymobile.core.ui import pickers

        assert "clamped" in (pickers.__doc__ or "")
        assert "ValueError" in (pickers.__doc__ or "")


# --------------------------------------------------------------------------
# PM-12 / AUDIO-01 / AUDIO-02 — the audio watcher lifecycle
# --------------------------------------------------------------------------
class _SlowHandle:
    """Android-like handle: frozen at 0 until ``prepare_after`` seconds pass."""

    def __init__(self, duration_ms: int = 400, prepare_after: float = 0.3, **options: object):
        self._duration = duration_ms
        self._t0 = time.monotonic()
        self._prepare_after = prepare_after
        self._want = False
        self._started_at: float | None = None
        self._offset = 0
        self.released = False

    def _prepared(self) -> bool:
        return time.monotonic() - self._t0 >= self._prepare_after

    def _maybe_start(self) -> None:
        if self._started_at is None and self._want and self._prepared():
            self._started_at = time.monotonic()

    def play(self) -> None:
        self._want = True
        self._maybe_start()

    def pause(self) -> None:
        self._offset = self.position()
        self._want = False
        self._started_at = None

    def stop(self) -> None:
        self._offset = 0
        self._want = False
        self._started_at = None

    def seek(self, position_ms: int) -> None:
        self._offset = max(0, int(position_ms))

    def position(self) -> int:
        self._maybe_start()
        if self._started_at is None:
            return self._offset
        return self._offset + int((time.monotonic() - self._started_at) * 1000)

    def duration(self) -> int:
        return self._duration if self._prepared() else 0

    def is_playing(self) -> bool:
        if self.released or not self._prepared():
            return False
        self._maybe_start()
        return self._started_at is not None and self.position() < self._duration

    def set_looping(self, looping: bool) -> None:
        pass

    def set_volume(self, level: float) -> None:
        pass

    def release(self) -> None:
        self.released = True


class _SlowBridge(StubBridge):
    def audio_player(self, source: str, **options: object) -> _SlowHandle:
        self.handle = _SlowHandle(source=source, **options)
        return self.handle


def _tone(path: Path, seconds: float = 0.25) -> str:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(8000 * seconds))
    return str(path)


def _wait_for(condition: object, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


class TestAudioWatcher:
    def test_on_end_fires_without_on_tick(self, tmp_path: Path) -> None:
        """AUDIO-01: on_end alone must get a watcher; the native bridge has no
        end callback, so the position polling *is* the completion signal."""
        app = App("T", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
        ended: list[int] = []
        player = app.audio(_tone(tmp_path / "beep.wav"), on_end=lambda: ended.append(1))
        player.play()
        _wait_for(lambda: ended, 3.0, "on_end without on_tick")
        player.release()
        assert ended == [1]

    def test_duration_refreshes_after_the_async_prepare(self, tmp_path: Path) -> None:
        """PM-12: the cached duration=0 of the preparing player must not
        swallow the end-of-file detection."""
        app = App("T", bridge=_SlowBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
        ended: list[int] = []
        player = app.audio("clip.wav", on_end=lambda: ended.append(1))
        player.play()
        assert player.duration == 0  # still preparing at play()
        _wait_for(lambda: player.duration == 400, 3.0, "duration refresh")
        _wait_for(lambda: ended, 4.0, "on_end after prepare")
        time.sleep(0.3)
        player.release()
        assert ended == [1]  # exactly once

    def test_the_watcher_survives_a_slow_prepare(self, tmp_path: Path) -> None:
        """AUDIO-02: a long prepareAsync used to stop the polling on its
        first not-playing tick, so the end of the file was never seen."""
        bridge = _SlowBridge(verbose=False)
        app = App("T", bridge=bridge, storage_path=str(tmp_path / "s.json"))
        ended: list[int] = []
        player = app.audio("slow.wav", on_end=lambda: ended.append(1))
        original = audio_module._PREPARE_TIMEOUT_S
        audio_module._PREPARE_TIMEOUT_S = 0.8  # the prepare below outlives several ticks
        try:
            player._require_handle()._prepare_after = 0.8  # type: ignore[attr-defined]
            player.play()
            _wait_for(lambda: ended, 5.0, "end after a slow prepare")
        finally:
            audio_module._PREPARE_TIMEOUT_S = original
            player.release()
        assert ended == [1]

    def test_pause_then_resume_reports_the_end_once(self, tmp_path: Path) -> None:
        app = App("T", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
        ended: list[int] = []
        player = app.audio(_tone(tmp_path / "beep.wav"), on_end=lambda: ended.append(1))
        player.play()
        time.sleep(0.05)
        player.pause()
        time.sleep(0.3)
        assert ended == []
        player.play()
        _wait_for(lambda: ended, 3.0, "end after resume")
        player.release()
        assert ended == [1]

    def test_a_replay_after_the_end_reports_the_end_again(self, tmp_path: Path) -> None:
        app = App("T", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
        ended: list[int] = []
        player = app.audio(_tone(tmp_path / "beep.wav"), on_end=lambda: ended.append(1))
        player.play()
        _wait_for(lambda: ended, 3.0, "first end")
        player.play()
        _wait_for(lambda: len(ended) == 2, 3.0, "second end")
        player.release()
        assert ended == [1, 1]
