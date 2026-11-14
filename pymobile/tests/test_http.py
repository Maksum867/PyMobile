"""Tests for the HTTP client.

A real loopback HTTP server is used instead of mocks: it exercises the actual
urllib code path, including headers, bodies, status codes and timeouts.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pymobile.core.net.http import HttpClient, Response
from pymobile.errors import NetworkError


class _Handler(BaseHTTPRequestHandler):
    """Echoes request details back as JSON."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def _echo(self, method: str) -> None:
        if self.path.startswith("/status/"):
            code = int(self.path.rsplit("/", 1)[1])
            self.send_response(code)
            body = b'{"error":"expected"}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/notjson":
            body = b"plain text"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/empty":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b""
        response = json.dumps(
            {
                "method": method,
                "path": self.path,
                "body": payload.decode("utf-8", "replace"),
                "content_type": self.headers.get("Content-Type", ""),
                "custom": self.headers.get("X-Custom", ""),
                "user_agent": self.headers.get("User-Agent", ""),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self) -> None:
        self._echo("GET")

    def do_POST(self) -> None:
        self._echo("POST")

    def do_PUT(self) -> None:
        self._echo("PUT")

    def do_DELETE(self) -> None:
        self._echo("DELETE")


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    """A background HTTP server; yields its base URL."""
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestVerbs:
    def test_get(self, server: str) -> None:
        response = HttpClient().get(f"{server}/hello")
        assert response.ok
        assert response.json()["method"] == "GET"

    def test_get_with_params(self, server: str) -> None:
        response = HttpClient().get(f"{server}/search", params={"q": "py", "n": 2, "skip": None})
        path = response.json()["path"]
        assert "q=py" in path and "n=2" in path and "skip" not in path

    def test_post_json(self, server: str) -> None:
        response = HttpClient().post(f"{server}/items", json={"a": 1})
        data = response.json()
        assert data["method"] == "POST"
        assert json.loads(data["body"]) == {"a": 1}
        assert data["content_type"] == "application/json"

    def test_post_form(self, server: str) -> None:
        response = HttpClient().post(f"{server}/form", data={"x": "1"})
        data = response.json()
        assert data["body"] == "x=1"
        assert "form-urlencoded" in data["content_type"]

    def test_post_raw_bytes(self, server: str) -> None:
        assert HttpClient().post(f"{server}/raw", data=b"raw").json()["body"] == "raw"

    def test_post_text(self, server: str) -> None:
        assert HttpClient().post(f"{server}/text", data="hi").json()["body"] == "hi"

    def test_put(self, server: str) -> None:
        assert HttpClient().put(f"{server}/x", json={"b": 2}).json()["method"] == "PUT"

    def test_delete(self, server: str) -> None:
        assert HttpClient().delete(f"{server}/x").json()["method"] == "DELETE"


class TestClientBehaviour:
    def test_base_url_join(self, server: str) -> None:
        client = HttpClient(base_url=server)
        assert client.get("/joined").json()["path"] == "/joined"

    def test_absolute_url_ignores_base(self, server: str) -> None:
        client = HttpClient(base_url="http://never.invalid")
        assert client.get(f"{server}/abs").ok

    def test_default_headers_merged(self, server: str) -> None:
        client = HttpClient(headers={"X-Custom": "yes"})
        assert client.get(f"{server}/h").json()["custom"] == "yes"

    def test_per_request_headers_win(self, server: str) -> None:
        client = HttpClient(headers={"X-Custom": "no"})
        data = client.get(f"{server}/h", headers={"X-Custom": "yes"}).json()
        assert data["custom"] == "yes"

    def test_user_agent(self, server: str) -> None:
        assert "PyMobile" in HttpClient().get(f"{server}/ua").json()["user_agent"]

    def test_json_and_data_conflict(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            HttpClient().request("POST", "http://x.test", json={}, data=b"x")

    def test_bad_scheme_rejected(self) -> None:
        with pytest.raises(NetworkError, match="scheme"):
            HttpClient().get("ftp://example.com/file")

    def test_unreachable_host(self) -> None:
        # Port 1 on loopback is normally refused at once ("Could not reach"),
        # but a runner that drops it instead turns the same call into a
        # timeout. Both are the same failure for the caller, so accept either
        # rather than fail on which of the two the network stack chose.
        with pytest.raises(NetworkError, match=r"Could not reach|timed out"):
            HttpClient(timeout=2).get("http://127.0.0.1:1/nothing")

    def test_retry_then_fail(self) -> None:
        client = HttpClient(timeout=1, retries=2, backoff=0)
        with pytest.raises(NetworkError):
            client.get("http://127.0.0.1:1/nothing")

    def test_elapsed_recorded(self, server: str) -> None:
        assert HttpClient().get(f"{server}/x").elapsed >= 0


class TestResponse:
    def test_error_status_not_raised_by_default(self, server: str) -> None:
        response = HttpClient().get(f"{server}/status/404")
        assert response.status == 404
        assert not response.ok

    def test_raise_for_status(self, server: str) -> None:
        response = HttpClient().get(f"{server}/status/500", timeout=5)
        with pytest.raises(NetworkError, match="HTTP 500"):
            response.raise_for_status()

    def test_raise_for_status_returns_self(self, server: str) -> None:
        response = HttpClient().get(f"{server}/x")
        assert response.raise_for_status() is response

    def test_invalid_json(self, server: str) -> None:
        with pytest.raises(NetworkError, match="not valid JSON"):
            HttpClient().get(f"{server}/notjson").json()

    def test_empty_body_json(self, server: str) -> None:
        with pytest.raises(NetworkError, match="empty"):
            HttpClient().get(f"{server}/empty").json()

    def test_headers_lowercased(self, server: str) -> None:
        assert "content-type" in HttpClient().get(f"{server}/x").headers

    def test_text_decoding_is_lenient(self) -> None:
        response = Response(200, {}, b"\xff\xfe", "http://x.test")
        assert isinstance(response.text, str)
