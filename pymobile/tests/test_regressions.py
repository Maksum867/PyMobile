"""Regression tests for public API contracts fixed after the 0.6.0 audit."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import pymobile
from pymobile import (
    App,
    Column,
    Dropdown,
    Label,
    PyMobileDeprecationWarning,
    RatingBar,
    Row,
    Screen,
    SegmentedButtons,
    Validator,
    Widget,
    get_diagnostics,
    t,
    translations,
)
from pymobile.core.api.storage import Storage
from pymobile.core.jobs import JobHandle, JobManager
from pymobile.core.net.cache import HttpCache
from pymobile.core.net.http import HttpClient, HttpFuture, Response
from pymobile.core.ui.components import Image
from pymobile.errors import NetworkError, PyMobileError


def test_documented_validator_mapping_dsl() -> None:
    validator = Validator(
        {
            "email": ["required", "email"],
            "age": ["optional", "integer", {"between": [1, 120]}],
        }
    )
    assert validator.validate({"email": "a@example.com", "age": "42"}) == {}
    assert validator.validate({"email": "invalid", "age": 130}) == {
        "email": "must be a valid email address",
        "age": "must be between 1.0 and 120.0",
    }


def test_selection_on_change_is_a_deprecated_alias_that_still_works() -> None:
    changed: list[str] = []
    with pytest.warns(PyMobileDeprecationWarning, match="use on_select= instead"):
        Dropdown(["one", "two"], on_change=changed.append).set_value("two")
    with pytest.warns(PyMobileDeprecationWarning, match="use on_select= instead"):
        SegmentedButtons(["light", "dark"], on_change=changed.append).set_value("dark")
    assert changed == ["two", "dark"]


def test_rating_value_alias_and_conflicting_aliases() -> None:
    assert RatingBar(value=3).value == 3
    with pytest.raises(ValueError, match="either rating or value"):
        RatingBar(2, value=3)


def test_image_accepts_local_file_url(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"not decoded by the widget")
    assert Image(image.as_uri()).source == image.as_uri()


def test_storage_rejects_non_string_keys(tmp_path: Path) -> None:
    store = Storage(tmp_path / "store.json")
    with pytest.raises(ValueError, match="non-empty string"):
        store.set(1, "bad")  # type: ignore[arg-type]


def test_completed_job_callback_can_cancel_without_deadlock() -> None:
    handle = JobHandle("job", lambda: None)
    handle._complete("done", None)
    handle.then(lambda _: handle.cancel())
    assert handle.cancelled


def test_completed_http_callback_can_cancel_without_deadlock() -> None:
    future = HttpFuture(HttpClient(), "GET", "https://example.test", {})
    future._complete(Response(200, {}, b"ok", "https://example.test"), None)
    future.then(lambda _: future.cancel())
    assert future.cancelled


def test_job_wait_times_out() -> None:
    handle = JobHandle("slow", lambda: None)
    with pytest.raises(TimeoutError, match="did not complete"):
        handle.wait(timeout=0.01)


def test_failed_repeating_job_completes_handle_with_error() -> None:
    manager = JobManager()

    def fail() -> None:
        raise RuntimeError("boom")

    handle = manager.every(1, fail)
    for _ in range(50):
        if handle.done:
            break
        time.sleep(0.01)
    assert handle.done
    with pytest.raises(RuntimeError, match="boom"):
        handle.wait()
    manager.shutdown()



# ---------------------------------------------------------------------------
# 0.6.4 audit: language/theme propagation, diagnostics, widget reuse
# ---------------------------------------------------------------------------
def _texts(tree: dict[str, Any] | None) -> list[str]:
    """Every label text in a serialised widget tree, in order."""
    if not tree:
        return []
    found: list[str] = []
    if tree.get("type") == "Label":
        text = (tree.get("props") or {}).get("text")
        if text is not None:
            found.append(str(text))
    for child in tree.get("children") or []:
        found.extend(_texts(child))
    return found


class _Greeting(Screen):
    def build(self) -> Widget:
        return Column(Label(t("greeting")))


class _Settings(Screen):
    def build(self) -> Widget:
        return Column(Label("settings"))


@pytest.fixture
def catalogue() -> Iterator[Any]:
    """A tiny uk/en catalogue; the previous language is restored afterwards."""
    before = translations.language
    translations.load({"greeting": "Hello"}, language="en")
    translations.load({"greeting": "Привіт"}, language="uk")
    translations.use("uk")
    yield translations
    translations.use(before)


def test_language_change_reaches_screens_under_the_top(bridge, catalogue) -> None:  # type: ignore[no-untyped-def]
    """Regression: only ``navigator.current`` was rebuilt, so the screen below
    the top kept the previous language until it was rebuilt by hand."""
    app = App("Demo", bridge=bridge)
    app.run(_Greeting())
    app.push(_Settings())

    catalogue.use("en")
    app.pop()

    assert _texts(bridge.last_tree) == ["Hello"]


def test_theme_change_invalidates_screens_under_the_top(bridge) -> None:  # type: ignore[no-untyped-def]
    """``set_theme`` refreshed only the visible screen, so a hidden one kept the
    tree it had built under the old theme."""
    app = App("Demo", bridge=bridge)
    home = _Greeting()
    app.run(home)
    app.push(_Settings())
    stale = home.root

    app.set_theme("dark")
    app.pop()

    assert home.root is not stale


def test_get_diagnostics_reports_documented_keys() -> None:
    """The README example reads ``framework_version`` and ``log_level``."""
    info = get_diagnostics()
    assert info["framework_version"] == pymobile.__version__
    assert info["log_level"] == str(info["level"]).lower()
    for key in ("framework", "platform", "python", "handlers"):
        assert key in info


def test_stored_widget_survives_a_refresh(bridge) -> None:  # type: ignore[no-untyped-def]
    """The documented ``self.counter = Label("0")`` pattern must keep working
    across the rebuild that a language or theme change triggers."""

    class Counter(Screen):
        def __init__(self) -> None:
            super().__init__()
            self.counter = Label("0")

        def build(self) -> Widget:
            return Column(self.counter)

    app = App("Demo", bridge=bridge)
    screen = Counter()
    app.run(screen)
    screen.refresh()  # used to raise: widget 'counter' already has a parent
    assert _texts(bridge.last_tree) == ["0"]


def test_double_parent_error_carries_a_hint() -> None:
    child = Label("x")
    Column(child)
    with pytest.raises(PyMobileError, match="already has a parent") as caught:
        Row(child)
    assert caught.value.hint



# ---------------------------------------------------------------------------
# 0.6.4 audit: HTTP transport and caching
# ---------------------------------------------------------------------------
class _LoopbackServer:
    """A loopback HTTP server that records the paths it was asked for.

    ``truncate_first`` makes the first N replies send a Content-Length longer
    than the body and then close, which is the truncated-read case that used to
    escape as ``http.client.IncompleteRead``.
    """

    def __init__(self, body: bytes = b'{"ok": true}', truncate_first: int = 0) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        outer = self
        self.paths: list[str] = []
        self._truncate_first = truncate_first

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.paths.append(self.path)
                if len(outer.paths) <= outer._truncate_first:
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(body[:10])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def __enter__(self) -> _LoopbackServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def test_get_cached_sends_params_exactly_once(tmp_path: Path) -> None:
    """Regression: ``final_url`` already has the query string, so forwarding
    ``params`` to ``get()`` appended every parameter a second time."""
    with _LoopbackServer() as server:
        client = HttpClient(
            base_url=server.base_url,
            cache=HttpCache(tmp_path / "cache.json"),
            retries=0,
        )
        client.get_cached("/items", ttl=60, params={"page": 2, "q": "kyiv"})
        assert server.paths == ["/items?page=2&q=kyiv"]


def test_get_cached_and_get_share_one_cache_key(tmp_path: Path) -> None:
    """The duplicated query string also produced a key that a plain
    ``client.get(...)`` with the same params could never match."""
    cache_file = tmp_path / "cache.json"
    with _LoopbackServer() as server:
        first = HttpClient(base_url=server.base_url, cache=HttpCache(cache_file), retries=0)
        first.get_cached("/items", ttl=60, params={"page": 2})

        second = HttpClient(base_url=server.base_url, cache=HttpCache(cache_file), retries=0)
        cached = second.get_cached("/items", ttl=60, params={"page": 2})

        assert cached.from_cache is True
        assert server.paths == ["/items?page=2"], "the second client hit the network"


def test_truncated_response_is_a_network_error() -> None:
    """Regression: a short read escaped as ``IncompleteRead`` instead of
    ``NetworkError``, so callers could not handle it at all."""
    with _LoopbackServer(truncate_first=1) as server:
        client = HttpClient(base_url=server.base_url, timeout=2, retries=0)
        with pytest.raises(NetworkError, match="Could not read"):
            client.get("/items")


def test_retries_cover_truncated_responses() -> None:
    """``retries`` only catches NetworkError — which a reset never was."""
    with _LoopbackServer(truncate_first=1) as server:
        client = HttpClient(base_url=server.base_url, timeout=2, retries=2, backoff=0.01)
        response = client.get("/items")
        assert response.ok
        assert len(server.paths) == 2, "повтор спроби не відбувся"


def test_stale_cache_survives_a_broken_connection(tmp_path: Path) -> None:
    """The documented offline mode: a stale copy is served when the network
    fails. It never triggered, because the failure was not a NetworkError."""
    cache = HttpCache(tmp_path / "cache.json")
    with _LoopbackServer(truncate_first=10) as server:
        url = f"{server.base_url}/items"
        cache.set(url, 200, {"content-type": "application/json"}, b'{"items": [1]}')

        client = HttpClient(
            base_url=server.base_url, cache=cache, timeout=2, retries=1, backoff=0.01
        )
        response = client.get_cached("/items", ttl=0.0)

        assert response.from_cache is True
        assert response.json() == {"items": [1]}



# ---------------------------------------------------------------------------
# 0.6.4 audit: plugin re-activation, stopped-app navigation, missing images
# ---------------------------------------------------------------------------
def test_plugin_activates_once_per_app(bridge) -> None:  # type: ignore[no-untyped-def]
    """Regression: ``_activated`` was a set of names that was never reset, so a
    second App created in the same process never got an ``activate`` call."""
    from pymobile.core.plugins import Plugin, PluginRegistry

    class Recorder(Plugin):
        name = "recorder"

        def __init__(self) -> None:
            self.seen: list[str] = []

        def activate(self, app: App) -> None:
            self.seen.append(app.name)

    registry = PluginRegistry()
    plugin = Recorder()
    registry.register(plugin)

    first = App("First", bridge=bridge)
    second = App("Second", bridge=bridge)

    registry.activate_all(first)
    assert plugin.seen == ["First"]

    registry.activate_all(first)  # same app: must not run twice
    assert plugin.seen == ["First"]

    registry.activate_all(second)  # a later app: must run again
    assert plugin.seen == ["First", "Second"]


def test_unregistered_plugin_forgets_its_activation(bridge) -> None:  # type: ignore[no-untyped-def]
    from pymobile.core.plugins import Plugin, PluginRegistry

    class P(Plugin):
        name = "p"

        def __init__(self) -> None:
            self.calls = 0

        def activate(self, app: App) -> None:
            self.calls += 1

    registry = PluginRegistry()
    first = P()
    registry.register(first)
    app = App("Demo", bridge=bridge)
    registry.activate_all(app)

    registry.unregister("p")
    second = P()
    registry.register(second)
    registry.activate_all(app)

    assert (first.calls, second.calls) == (1, 1)


def test_navigation_after_stop_is_reported_as_stopped(bridge) -> None:  # type: ignore[no-untyped-def]
    """Regression: the error blamed ``App.run()`` even though it had been called
    and the app was merely stopped, which sent callers down the wrong path."""
    app = App("Demo", bridge=bridge)
    app.run(_Greeting())
    app.stop()

    with pytest.raises(PyMobileError, match="stopped") as caught:
        app.push(_Settings())
    assert "before App.run()" not in str(caught.value)


def test_navigation_before_run_keeps_its_message(bridge) -> None:  # type: ignore[no-untyped-def]
    """The original diagnosis must still fire when run() was never called."""
    app = App("Demo", bridge=bridge)
    with pytest.raises(PyMobileError, match=r"before App\.run"):
        app.push(_Settings())


def _capture_logs(logger_name: str) -> tuple[logging.Handler, list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(logger_name)
    handler = Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    return handler, records


def test_missing_relative_image_warns() -> None:
    """Regression: a relative path that exists nowhere was accepted in complete
    silence, so a typo showed up as a blank box with no diagnostic."""
    handler, records = _capture_logs("pymobile.ui.components")
    try:
        Image("definitely-missing-image.png")
    finally:
        logging.getLogger("pymobile.ui.components").removeHandler(handler)

    messages = [record.getMessage() for record in records]
    assert any("definitely-missing-image.png" in message for message in messages)
    assert all(record.levelno >= logging.WARNING for record in records)


def test_existing_image_does_not_warn(tmp_path: Path) -> None:
    image = tmp_path / "logo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    handler, records = _capture_logs("pymobile.ui.components")
    try:
        Image(str(image))
    finally:
        logging.getLogger("pymobile.ui.components").removeHandler(handler)

    assert records == []

def test_windows_drive_path_is_not_read_as_a_url_scheme() -> None:
    """Regression: ``urlparse("C:\\photos\\me.png")`` reports the drive letter as
    a URL scheme, so on Windows the most natural form of a local path was
    rejected outright with "unsupported image URL scheme: 'c'"."""
    with pytest.raises(FileNotFoundError):  # a missing file, not a malformed URL
        Image(r"C:\definitely\missing\photo.png")