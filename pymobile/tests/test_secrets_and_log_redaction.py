"""Secrets and links: what may leave the app, and how a secret is kept.

Platform calls that take a URL from app data need a scheme check (S3), and a
credential must not be written to a JSON file or to a log line (K3, S4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymobile import App
from pymobile.core.bridge import StubBridge
from pymobile.core.bridge.base import OPENABLE_SCHEMES
from pymobile.core.net.http import SECRET_QUERY_PARAMS, redact_url
from pymobile.errors import NetworkError


class _NoSecrets(StubBridge):
    """A bridge without keystore support, like a desktop preview."""

    def store_secret(self, name: str, value: str) -> bool:
        raise NotImplementedError("no keystore here")

    def load_secret(self, name: str) -> str | None:
        raise NotImplementedError("no keystore here")

    def delete_secret(self, name: str) -> bool:
        raise NotImplementedError("no keystore here")


class TestSecrets:
    def test_a_secret_survives_a_round_trip(self, tmp_path: Path) -> None:
        app = App("T", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
        try:
            assert app.save_secret("api", "sk-live-123") is True
            assert app.load_secret("api") == "sk-live-123"
            assert app.delete_secret("api") is True
            assert app.load_secret("api") is None
            assert app.delete_secret("api") is False
        finally:
            app.stop()

    def test_a_secret_never_reaches_the_storage_file(self, tmp_path: Path) -> None:
        store = tmp_path / "s.json"
        app = App("T", bridge=StubBridge(verbose=False), storage_path=str(store))
        try:
            app.save_secret("api", "sk-live-123")
            app.storage.set("other", 1)  # force a write
            app.storage.save()
        finally:
            app.stop()
        assert "sk-live-123" not in store.read_text()

    def test_the_call_log_does_not_quote_the_secret(self) -> None:
        bridge = StubBridge(verbose=False)
        app = App("T", bridge=bridge)
        try:
            app.save_secret("api", "sk-live-123")
        finally:
            app.stop()
        recorded = json.dumps([call.kwargs for call in bridge.calls])
        assert "sk-live-123" not in recorded

    def test_a_bridge_without_a_keystore_says_so(self) -> None:
        app = App("T", bridge=_NoSecrets(verbose=False))
        try:
            with pytest.raises(NotImplementedError, match="keystore"):
                app.save_secret("api", "x")
        finally:
            app.stop()

    def test_the_arguments_are_validated(self) -> None:
        app = App("T", bridge=StubBridge(verbose=False))
        try:
            with pytest.raises(ValueError, match="secret name"):
                app.save_secret("", "x")
            with pytest.raises(TypeError, match="secret value"):
                app.save_secret("api", 1)  # type: ignore[arg-type]
        finally:
            app.stop()

    def test_the_android_bridge_uses_the_native_module(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        class Native:
            def __init__(self) -> None:
                self.vault: dict[str, str] = {}

            def put_secret(self, name: str, value: str) -> bool:
                self.vault[name] = value
                return True

            def get_secret(self, name: str) -> str | None:
                return self.vault.get(name)

            def delete_secret(self, name: str) -> bool:
                return self.vault.pop(name, None) is not None

        bridge = AndroidBridge.with_native(Native())
        assert bridge.store_secret("api", "value") is True
        assert bridge.load_secret("api") == "value"
        assert bridge.delete_secret("api") is True

    def test_an_old_runtime_is_told_to_rebuild(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        bridge = AndroidBridge.with_native(object())
        with pytest.raises(NotImplementedError, match="rebuild the APK"):
            bridge.store_secret("api", "value")


class TestOpenableLinks:
    @pytest.mark.parametrize(
        "url", ["https://example.com/a", "http://example.com", "mailto:a@b.c", "tel:+380501112233"]
    )
    def test_supported_schemes_are_opened(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", lambda target: opened.append(target) or True)
        assert StubBridge(verbose=False).open_url(url) is True
        assert opened == [url]

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "javascript:alert(1)", "intent://scan", "content://x", "/help"],
    )
    def test_other_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(ValueError, match="allowed schemes"):
            StubBridge(verbose=False).open_url(url)

    def test_the_allowed_set_is_small_and_named(self) -> None:
        assert frozenset({"http", "https", "mailto", "tel"}) == OPENABLE_SCHEMES

    def test_the_android_bridge_checks_before_calling_java(self) -> None:
        from pymobile.core.bridge.android import AndroidBridge

        class Native:
            called = False

            def open_url(self, url: str) -> bool:
                Native.called = True
                return True

        with pytest.raises(ValueError, match="refusing to open"):
            AndroidBridge.with_native(Native()).open_url("file:///data/local/tmp/x")
        assert Native.called is False


class TestRedactedLogs:
    def test_a_token_in_the_query_string_is_masked(self) -> None:
        masked = redact_url("https://api.example.com/x?access_token=abc&page=2")
        assert "abc" not in masked
        assert "access_token=***" in masked
        assert "page=2" in masked

    def test_a_url_without_credentials_is_untouched(self) -> None:
        url = "https://api.example.com/x?page=2&sort=name"
        assert redact_url(url) == url

    def test_every_named_parameter_is_masked(self) -> None:
        for name in SECRET_QUERY_PARAMS:
            masked = redact_url(f"https://example.com/?{name}=leaked")
            assert "leaked" not in masked

    def test_a_failed_request_does_not_log_its_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from pymobile.core.net.http import HttpClient

        client = HttpClient(headers={"X-Trace": "on"})
        with caplog.at_level("DEBUG", logger="pymobile"), pytest.raises(NetworkError):
            # Nothing listens on port 1: the failure is logged with the URL.
            client.get("http://127.0.0.1:1/state?token=leaked", timeout=0.3)
        client.close()
        assert "leaked" not in caplog.text
