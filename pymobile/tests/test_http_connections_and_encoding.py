"""HTTP transport: keep-alive, compression, deadlines and streaming.

Each test here pins a property the client is expected to keep: one connection
per host instead of one per call, a body that arrives compressed but is handed
over decoded, a timeout that bounds the whole call, and a streaming download
that never holds the whole payload in memory.
"""

from __future__ import annotations

import gzip
import threading
import time
import zlib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from pymobile import Storage
from pymobile.core.net.http import ACCEPT_ENCODING, HttpClient
from pymobile.errors import NetworkError

BIG = b"payload-" * 200


class _Handler(BaseHTTPRequestHandler):
    """A server that can answer plain, gzip, slow and multipart requests."""

    protocol_version = "HTTP/1.1"
    hits = 0
    last_body = b""
    last_headers: ClassVar[dict[str, str]] = {}

    def log_message(self, *args: object) -> None:
        pass

    def _respond(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).hits += 1
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        if self.path == "/gzip":
            compressed = gzip.compress(BIG)
            self._respond(
                200, compressed, {"Content-Encoding": "gzip", "Content-Type": "text/plain"}
            )
        elif self.path == "/deflate":
            self._respond(200, zlib.compress(BIG), {"Content-Encoding": "deflate"})
        elif self.path == "/broken-gzip":
            self._respond(200, b"not really gzip", {"Content-Encoding": "gzip"})
        elif self.path == "/slow":
            # Two seconds of silence: a 0.3 s deadline must win.
            time.sleep(2)
            self._respond(200, b"late")
        elif self.path == "/stream":
            self._respond(200, BIG, {"Content-Type": "text/plain"})
        elif self.path == "/redirect":
            self._respond(302, b"", {"Location": "/stream"})
        else:
            self._respond(200, b"ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_body = self.rfile.read(length)
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        self._respond(200, b"received")


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.hits = 0
    _Handler.last_body = b""
    _Handler.last_headers = {}
    # Threaded on purpose: the client keeps connections alive, so a
    # single-threaded server would serve one caller and park the rest until the
    # idle socket timed out - which is the server's limitation, not the
    # client's.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestKeepAliveConnections:
    def test_two_requests_reuse_one_socket(self, server: str) -> None:
        client = HttpClient()
        try:
            first = client.get(f"{server}/plain")
            second = client.get(f"{server}/plain")
            assert first.content == second.content == b"ok"
            # One idle connection is held for the next call rather than a new
            # socket per request (and a new TLS handshake over HTTPS).
            assert len(client._pool) == 1
        finally:
            client.close()

    def test_closing_the_client_drops_the_idle_sockets(self, server: str) -> None:
        client = HttpClient()
        client.get(f"{server}/plain")
        assert len(client._pool) == 1
        client.close()
        assert len(client._pool) == 0

    def test_the_client_is_a_context_manager(self, server: str) -> None:
        with HttpClient() as client:
            assert client.get(f"{server}/plain").ok
        assert len(client._pool) == 0

    def test_the_default_encoding_header_is_advertised(self, server: str) -> None:
        with HttpClient() as client:
            client.get(f"{server}/plain")
        assert _Handler.last_headers["accept-encoding"] == ACCEPT_ENCODING


class TestCompressedResponses:
    def test_a_gzip_body_is_decoded(self, server: str) -> None:
        with HttpClient() as client:
            response = client.get(f"{server}/gzip")
        assert response.content == BIG
        assert response.text.startswith("payload-")
        # The header cannot stay: what the caller holds is no longer gzip.
        assert "content-encoding" not in response.headers

    def test_a_deflate_body_is_decoded(self, server: str) -> None:
        with HttpClient() as client:
            assert client.get(f"{server}/deflate").content == BIG

    def test_a_body_that_is_not_really_gzip_is_returned_as_is(self, server: str) -> None:
        with HttpClient() as client:
            response = client.get(f"{server}/broken-gzip")
        assert response.content == b"not really gzip"


class TestRequestBodies:
    def test_multipart_carries_fields_and_files(self, server: str) -> None:
        with HttpClient() as client:
            response = client.post(
                f"{server}/upload",
                data={"title": "holiday"},
                files={"photo": ("p.png", b"\x89PNG\r\n", "image/png")},
            )
        assert response.text == "received"
        assert _Handler.last_headers["content-type"].startswith("multipart/form-data; boundary=")
        body = _Handler.last_body
        assert b'name="title"' in body and b"holiday" in body
        assert b'filename="p.png"' in body and b"Content-Type: image/png" in body
        # Every part is terminated, including the last one.
        assert body.endswith(b"--\r\n")

    def test_a_large_body_can_be_gzipped(self, server: str) -> None:
        with HttpClient() as client:
            payload = {"blob": "x" * 5000}
            client.post(f"{server}/upload", json=payload, compress=True)
        assert _Handler.last_headers["content-encoding"] == "gzip"
        assert gzip.decompress(_Handler.last_body)

    def test_a_small_body_is_not_compressed(self, server: str) -> None:
        with HttpClient() as client:
            client.post(f"{server}/upload", json={"tiny": 1}, compress=True)
        assert "content-encoding" not in _Handler.last_headers

    def test_files_and_json_cannot_be_mixed(self, server: str) -> None:
        with HttpClient() as client, pytest.raises(ValueError, match="files= with data="):
            client.post(f"{server}/upload", json={"a": 1}, files={"f": b"x"})


class TestStreamingResponses:
    def test_a_streamed_body_is_read_in_chunks(self, server: str) -> None:
        with HttpClient() as client:
            response = client.get(f"{server}/stream", stream=True)
            assert response.stream is True
            assert response.content == b""  # nothing buffered up front
            chunks = list(response.iter_content(1024))
            assert sum(len(chunk) for chunk in chunks) == len(BIG)
            assert len(chunks) > 1
            assert len(client._pool) == 1  # the connection went back to the pool

    def test_closing_early_releases_the_connection(self, server: str) -> None:
        with HttpClient() as client:
            with client.get(f"{server}/stream", stream=True) as response:
                next(response.iter_content(16))
            assert len(client._pool) == 1

    def test_a_plain_response_iterates_too(self, server: str) -> None:
        with HttpClient() as client:
            response = client.get(f"{server}/stream")
        assert b"".join(response.iter_content(4096)) == BIG

    def test_a_stream_follows_a_redirect_first(self, server: str) -> None:
        with HttpClient() as client:
            response = client.get(f"{server}/redirect", stream=True)
            assert response.url.endswith("/stream")
            assert b"".join(response.iter_content(2048)) == BIG


class TestDeadlines:
    def test_a_timeout_is_a_network_error(self, server: str) -> None:
        with HttpClient() as client, pytest.raises(NetworkError, match="timed out"):
            client.get(f"{server}/slow", timeout=0.3)

    def test_the_budget_bounds_retries(self, server: str) -> None:
        with HttpClient(retries=3, backoff=0, timeout=0.4, timeout_budget=0.5) as client:
            started = time.monotonic()
            with pytest.raises(NetworkError):
                client.get(f"{server}/slow")
            # Four attempts of 0.4 s plus backoff used to take 1.6 s or more.
            assert time.monotonic() - started < 1.5

    def test_the_budget_is_validated(self) -> None:
        with pytest.raises(ValueError, match="timeout_budget must be positive"):
            HttpClient(timeout_budget=0)
        with pytest.raises(TypeError, match="timeout_budget must be a number"):
            HttpClient(timeout_budget="2")  # type: ignore[arg-type]


class TestAsyncWorkers:
    def test_async_requests_run_on_a_fixed_pool(self, server: str) -> None:
        with HttpClient() as client:
            futures = [client.get_async(f"{server}/plain") for _ in range(12)]
            for future in futures:
                assert future.get(timeout=10).ok
            assert 0 < len(client._workers) <= 32

    def test_the_pool_is_created_on_first_use(self, server: str) -> None:
        with HttpClient() as client:
            # A synchronous app never pays for worker threads it never uses.
            assert len(client._workers) == 0
            client.get_async(f"{server}/plain").get(timeout=10)
            assert len(client._workers) > 0


class TestCacheWithDecodedBodies:
    def test_the_cache_stores_what_the_caller_saw(self, server: str, tmp_path) -> None:
        store = Storage(tmp_path / "store.json")
        with HttpClient(cache=store) as client:
            assert client.get_cached(f"{server}/gzip", ttl=60).content == BIG
            cached = client.get_cached(f"{server}/gzip", ttl=60)
        assert cached.from_cache is True
        assert cached.content == BIG
