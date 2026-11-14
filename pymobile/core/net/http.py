"""HTTP client built on the standard library.

Why not ``requests``? Every dependency bundled into an APK costs download size
and build time, and ``urllib`` already covers GET/POST/PUT/DELETE. The client
adds the parts that are genuinely missing: JSON handling, timeouts, retries
with backoff, a base URL and default headers.
"""

from __future__ import annotations

import codecs
import contextlib
import functools
import hashlib
import http.client
import json as jsonlib
import queue
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from ...errors import NetworkError
from ...log import get_logger
from ..api.storage import Storage
from .cache import HttpCache

if TYPE_CHECKING:  # pragma: no cover - typing only; `ssl` is imported lazily
    import ssl

__all__ = [
    "HttpClient",
    "HttpSecurityPolicy",
    "Response",
    "HttpFuture",
    "DEFAULT_TIMEOUT",
    "redact_url",
]

_log = get_logger("http")

DEFAULT_TIMEOUT = 15.0
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: Methods a 301/302/303 turn into a GET, as browsers do; 307/308 keep them.
_SEE_OTHER_STATUSES = frozenset({301, 302, 303})
Params = Mapping[str, str | int | float | bool | None]


@dataclass(frozen=True, slots=True)
class HttpSecurityPolicy:
    """Optional production constraints for outbound HTTP requests.

    Defaults preserve the framework's existing local-development behaviour.
    Set ``require_https=True`` in production; optionally restrict requests to
    an allow-list of hostnames.

    The policy is enforced on the initial URL **and on every redirect hop**,
    so a server (or an attacker controlling one) cannot bounce a request to a
    blocked host or downgrade it to plain HTTP.
    """

    require_https: bool = False
    allowed_hosts: frozenset[str] | None = None
    #: Extra header names (lower-case, e.g. ``"x-auth-token"``) treated as
    #: credentials: dropped on a cross-origin redirect and mixed into the
    #: cache key. The defaults (Authorization, Cookie, Proxy-Authorization,
    #: X-API-Key) are always included.
    credential_headers: frozenset[str] = frozenset()
    #: Hosts that may keep their credentials across a redirect *to another
    #: origin*. Empty by default: forwarding a key to a host the client did
    #: not address itself has to be an explicit, written-down decision.
    forward_credentials_hosts: frozenset[str] = frozenset()

    #: Ports used when a URL does not state one, by scheme.
    _DEFAULT_PORTS: ClassVar[Mapping[str, int]] = MappingProxyType({"http": 80, "https": 443})

    def __post_init__(self) -> None:
        # Hosts are compared case-insensitively; accept any iterable, so
        # allowed_hosts=["API.example.com"] works as written.
        if self.allowed_hosts is not None:
            if isinstance(self.allowed_hosts, str):
                raise TypeError("allowed_hosts must be a collection of host names, not a str")
            hosts = frozenset(h.strip().casefold() for h in self.allowed_hosts if h.strip())
            empty = not hosts
            object.__setattr__(self, "allowed_hosts", hosts)
            if empty:
                raise ValueError(
                    "allowed_hosts must not be empty; pass host names such as "
                    "['api.example.com'] or leave it as None to allow every host"
                )
        object.__setattr__(
            self,
            "credential_headers",
            _normalise_header_names(self.credential_headers),
        )
        object.__setattr__(
            self,
            "forward_credentials_hosts",
            frozenset(
                host.strip().casefold() for host in self.forward_credentials_hosts if host.strip()
            ),
        )

    @property
    def credential_header_names(self) -> frozenset[str]:
        """Every header name that must not cross an origin boundary."""
        return _DEFAULT_CREDENTIAL_HEADERS | self.credential_headers

    def validate(self, url: str) -> None:
        """Raise when ``url`` breaks the policy: scheme, host and redirect rules."""
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").casefold()
        if self.require_https and parsed.scheme != "https":
            raise NetworkError("Insecure HTTP is blocked by the security policy")
        if self.allowed_hosts is None:
            return
        if host in self.allowed_hosts:
            return
        # An entry may name a port ("api.example.com:8443"). The port used to be
        # compared as part of the string against a hostname that never carries
        # one, so such an entry blocked the very host it allowed.
        port = parsed.port
        if port is None:
            port = self._DEFAULT_PORTS.get(parsed.scheme, 0)
        candidates = {f"{host}:{port}", f"[{host}]:{port}"}
        if candidates & self.allowed_hosts:
            return
        raise NetworkError(
            f"Host {host!r} is blocked by the security policy",
            hint=(
                "allowed_hosts accepts a bare host or host:port; currently allowed: "
                + ", ".join(sorted(self.allowed_hosts))
            ),
        )


#: Request headers that carry credentials and must not follow a redirect to
#: another origin (urllib forwards every header by default). ``x-api-key``
#: belongs here: the cache already treated it as a credential when separating
#: entries, but the redirect handler *forwarded* it, so a key handed to one
#: host could be sent to another one the first host redirected to.
_DEFAULT_CREDENTIAL_HEADERS: frozenset[str] = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key"}
)


#: Request bodies at least this large are worth compressing when asked to.
COMPRESS_THRESHOLD = 1024

FileValue = "bytes | str | Path | tuple[str, Any] | tuple[str, Any, str]"


def _read_file_value(name: str, value: Any) -> tuple[str, bytes, str]:
    """Normalise one ``files=`` entry into ``(filename, content, content_type)``."""
    if isinstance(value, (bytes, bytearray)):
        return name, bytes(value), "application/octet-stream"
    if isinstance(value, tuple):
        if len(value) == 2:
            filename, content = value
            content_type = "application/octet-stream"
        elif len(value) == 3:
            filename, content, content_type = value
        else:
            raise ValueError(
                f"files[{name!r}] must be (filename, content) or (filename, content, content_type)"
            )
        return str(filename), _coerce_upload(content), str(content_type)
    # A path is read here rather than by the caller: streaming a file upload
    # from disk is the common case, and reading it lazily would mean holding
    # the request open across a retry.
    path = Path(value)
    return path.name, path.read_bytes(), "application/octet-stream"


def _coerce_upload(content: Any) -> bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, Path):
        return content.read_bytes()
    raise TypeError(f"file content must be bytes, str or a Path, got {type(content).__name__!r}")


def _encode_multipart(
    fields: Mapping[str, Any] | None, files: Mapping[str, Any]
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body.

    Uploads used to mean hand-rolling the boundary, the ``Content-Length`` and
    the CRLF rules; a missing CRLF between parts is the classic bug, and it
    fails silently on the server side. This does it once, correctly.
    """
    boundary = f"----PyMobileBoundary{secrets.token_hex(16)}"
    lines: list[bytes] = []
    for name, value in (fields or {}).items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    for name, entry in files.items():
        filename, content, content_type = _read_file_value(name, entry)
        lines.append(f"--{boundary}".encode())
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        lines.append(disposition.encode())
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


#: Query parameters whose value is a credential, masked before a URL reaches
#: the log. A debug log that carries ``?access_token=…`` ends up in logcat,
#: in bug reports and in a support ticket.
SECRET_QUERY_PARAMS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "code",
        "id_token",
        "key",
        "password",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)


def redact_url(url: str) -> str:
    """``url`` with credential-looking query values replaced by ``***``.

    Kept as a small function of its own so a caller can log the same
    redaction, and so the behaviour is obvious in a test.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:  # pragma: no cover - impossible for a str we built
        return url
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(name.casefold() in SECRET_QUERY_PARAMS for name, _ in pairs):
        return url
    masked = [
        (name, "***" if name.casefold() in SECRET_QUERY_PARAMS else value) for name, value in pairs
    ]
    # ``safe="*"`` keeps the mask readable in the log instead of %2A%2A%2A.
    query = urllib.parse.urlencode(masked, safe="*")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _header_value(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup; ``""`` when the header is absent."""
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return ""


def _normalise_header_names(names: frozenset[str] | tuple[str, ...] | list[str]) -> frozenset[str]:
    """Lower-case header names, rejecting a bare string ("x-api-key" is iterable)."""
    if isinstance(names, str):
        raise TypeError("credential_headers must be a collection of header names, not a str")
    return frozenset(str(name).strip().casefold() for name in names if str(name).strip())


#: Encodings the client understands, in the order it advertises them. A server
#: that honours ``Accept-Encoding`` gets a smaller body on the wire; one that
#: compresses without being asked is still decoded (see :func:`_decompress`).
ACCEPT_ENCODING = "gzip, deflate"

#: Enough for a phone: a pool entry is one idle socket, and these are cheap.
MAX_IDLE_CONNECTIONS = 4
#: Servers close idle sockets after ~5-60 s; dropping ours sooner avoids
#: writing a request into a socket the peer has already shut down.
IDLE_CONNECTION_TIMEOUT = 20.0


class _ConnectionPool:
    """Idle keep-alive connections, keyed by scheme/host/port.

    Every request used to open a fresh TCP connection (and, over HTTPS, a fresh
    TLS handshake). Reusing the socket turn is what makes a page of twenty API
    calls bearable on a phone. ``http.client`` connections are not thread safe,
    so a checked-out connection belongs to one caller until it is returned;
    connections handed back after an error are dropped rather than reused.
    """

    def __init__(
        self,
        *,
        max_idle: int = MAX_IDLE_CONNECTIONS,
        idle_timeout: float = IDLE_CONNECTION_TIMEOUT,
    ) -> None:
        self._idle: dict[
            tuple[str, str, int | None], list[tuple[http.client.HTTPConnection, float]]
        ] = {}
        self._lock = threading.Lock()
        self._max_idle = max_idle
        self._idle_timeout = idle_timeout

    def acquire(
        self, scheme: str, host: str, port: int | None, timeout: float
    ) -> http.client.HTTPConnection:
        """A reusable connection for the host, reusing an idle one when there is any."""
        key = (scheme, host, port)
        with self._lock:
            entries = self._idle.get(key, [])
            while entries:
                connection, returned_at = entries.pop()
                if time.monotonic() - returned_at <= self._idle_timeout:
                    connection.timeout = timeout
                    if connection.sock is not None:
                        return connection
                    connection.close()
        return self._new(scheme, host, port, timeout)

    @staticmethod
    def _new(
        scheme: str, host: str, port: int | None, timeout: float
    ) -> http.client.HTTPConnection:
        if scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=_ssl_context())
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def release(
        self, scheme: str, host: str, port: int | None, connection: http.client.HTTPConnection
    ) -> None:
        """Return a healthy connection; anything else is closed instead."""
        key = (scheme, host, port)
        with self._lock:
            entries = self._idle.setdefault(key, [])
            if len(entries) >= self._max_idle:
                connection.close()
                return
            entries.append((connection, time.monotonic()))

    def discard(self, connection: http.client.HTTPConnection) -> None:
        """Close and forget a connection that cannot be reused."""
        with self._lock:
            for entries in self._idle.values():
                entries[:] = [item for item in entries if item[0] is not connection]
        with contextlib.suppress(Exception):
            connection.close()

    def close(self) -> None:
        """Close every idle socket (called by ``App.stop`` and by tests)."""
        with self._lock:
            entries = [item for items in self._idle.values() for item in items]
            self._idle.clear()
        for connection, _ in entries:
            with contextlib.suppress(Exception):
                connection.close()

    def __len__(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._idle.values())


def _http_worker_count(max_workers: int | None) -> int:
    """How many worker threads serve ``*_async`` requests."""
    if max_workers is None:
        import os

        return max(2, min(32, (os.cpu_count() or 2) * 2))
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise TypeError("max_workers must be an int or None")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    return max_workers


class _RequestPool:
    """Fixed daemon workers for ``get_async`` and friends.

    One thread per request does not scale: a screen that fires twenty async
    loads, or a retry storm, used to create a thread each. A queued request
    waits here instead, and ``close()`` drains the queue.
    """

    def __init__(self, size: int) -> None:
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._closed = False
        self._lock = threading.Lock()
        self._size = size
        # Threads are started with the first async request, not with the client:
        # every ``HttpClient()`` used to spawn a worker pool on creation, and so
        # did every ``App`` and every test that built one, for requests a
        # synchronous app never makes.
        self._spawned = False

    def _ensure_started(self) -> None:
        if self._spawned:
            return
        with self._lock:
            if self._spawned:
                return
            for index in range(self._size):
                thread = threading.Thread(
                    target=self._loop, name=f"pymobile-http-worker-{index}", daemon=True
                )
                thread.start()
                self._threads.append(thread)
            self._spawned = True

    def submit(self, task: Callable[[], None]) -> None:
        """Run ``task`` on one of the request threads, starting them on first use."""
        if self._closed:
            raise NetworkError("the HTTP client has been closed")
        self._ensure_started()
        self._queue.put(task)

    def _loop(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            try:
                task()
            except BaseException:  # pragma: no cover - futures capture their own errors
                _log.exception("an HTTP worker raised")
            finally:
                self._queue.task_done()

    def shutdown(self, wait: bool = False) -> None:
        """Stop the request threads once their current task is done."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for _ in self._threads:
            self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join(timeout=5)

    def __len__(self) -> int:
        return len(self._threads)


def _gzip_bytes(body: bytes) -> bytes:
    """gzip a request body (``zlib.compress`` cannot ask for the gzip format)."""
    import gzip

    return gzip.compress(body, 6)


def _decompress(body: bytes, encoding: str) -> bytes:
    """Undo ``Content-Encoding`` on a body that was compressed anyway.

    The client advertises ``Accept-Encoding: gzip, deflate`` and also sends
    ``Accept-Encoding: identity`` when the caller sets the header by hand. A
    server that compresses regardless used to hand the caller gzip bytes as
    ``response.text``; this decodes them, and falls back to the raw body when
    the stream is truncated or otherwise unreadable (a partial body beats an
    exception the app cannot act on).
    """
    name = encoding.strip().casefold()
    if not name or name == "identity":
        return body
    if not body:
        return body
    try:
        if name in ("gzip", "x-gzip"):
            return zlib.decompress(body, 16 + zlib.MAX_WBITS)
        if name == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except zlib.error as exc:
        _log.warning("could not decode a %s response body (%s); returning it as-is", name, exc)
        return body
    _log.debug("unknown Content-Encoding %r; returning the body as-is", encoding)
    return body


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme, (parsed.hostname or "").casefold(), parsed.port)


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check the security policy on every hop and drop credentials cross-origin."""

    def __init__(self, policy: HttpSecurityPolicy) -> None:
        super().__init__()
        self._policy = policy

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Follow a redirect only when the policy allows the destination."""
        scheme = urllib.parse.urlsplit(newurl).scheme
        if scheme not in ("http", "https"):
            raise NetworkError(f"Redirect to unsupported URL scheme blocked: {newurl!r}")
        try:
            self._policy.validate(newurl)
        except NetworkError as exc:
            raise NetworkError(
                f"Redirect from {req.full_url} to {newurl} blocked: {exc}",
                hint="Add the target host to HttpSecurityPolicy.allowed_hosts if it is trusted.",
            ) from exc
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None or _origin(newurl) == _origin(req.full_url):
            return new
        host = (urllib.parse.urlsplit(newurl).hostname or "").casefold()
        if host in self._policy.forward_credentials_hosts:
            # Explicitly trusted: the request may keep its credentials even
            # though the origin changed. Written down on purpose.
            _log.debug("forwarding credentials to allow-listed host %s", host)
            return new
        names = self._policy.credential_header_names
        dropped: list[str] = []
        for store in (new.headers, new.unredirected_hdrs):
            for name in list(store):
                if name.casefold() in names:
                    del store[name]
                    dropped.append(name)
        if dropped:
            _log.debug(
                "dropped credential headers %s on a cross-origin redirect to %s",
                ", ".join(sorted(dropped)),
                host,
            )
        return new


@dataclass(frozen=True, slots=True)
class Response:
    """An immutable HTTP response."""

    status: int
    headers: dict[str, str]
    content: bytes
    url: str
    elapsed: float = 0.0
    encoding: str = "utf-8"
    from_cache: bool = False
    #: ``True`` when the body was **not** read: use ``iter_content()`` and call
    #: ``close()`` (or use the response as a context manager) when done.
    stream: bool = False
    #: The reader behind a streaming response; other responses leave it None.
    _reader: Callable[[int], bytes] | None = field(default=None, repr=False, compare=False)
    _closer: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # A server may send any junk in ``charset=…``. Decoding with a codec
        # Python does not have used to leak ``LookupError`` out of ``text``/
        # ``json()``; the contract is replacement decoding, so an unusable
        # charset falls back to UTF-8 here, once, where every Response —
        # live or served from the HTTP cache — passes through.
        try:
            codecs.lookup(self.encoding)
        except (LookupError, TypeError):
            _log.warning(
                "unknown charset %r for %s; decoding the body as utf-8",
                self.encoding,
                self.url,
            )
            object.__setattr__(self, "encoding", "utf-8")

    @property
    def ok(self) -> bool:
        """``True`` for 2xx status codes."""
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        """Body decoded as text (invalid bytes are replaced, never raised)."""
        try:
            return self.content.decode(self.encoding, errors="replace")
        except LookupError:  # pragma: no cover - construction already validates
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse the body as JSON."""
        if not self.content:
            raise NetworkError("Response body is empty; nothing to decode as JSON")
        try:
            return jsonlib.loads(self.text)
        except ValueError as exc:
            preview = self.text[:120]
            raise NetworkError(
                f"Response from {self.url} is not valid JSON: {exc}",
                hint=f"First bytes of the body: {preview!r}",
            ) from exc

    def iter_content(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield the body in chunks.

        For ``stream=True`` responses the bytes are read from the socket as
        they arrive and the connection is held until the iterator is done, so
        a 30 MB download no longer sits in memory three times over. A response
        that was fully read (the default) simply yields its content in slices.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self._reader is None:
            for start in range(0, len(self.content), chunk_size):
                yield self.content[start : start + chunk_size]
            return
        try:
            while True:
                chunk = self._reader(chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        """Release the connection behind a streaming response (idempotent)."""
        closer = self._closer
        object.__setattr__(self, "_closer", None)
        if closer is not None:
            closer()

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def raise_for_status(self) -> Response:
        """Return ``self``, or raise :class:`NetworkError` for 4xx/5xx."""
        if not self.ok:
            raise NetworkError(
                f"HTTP {self.status} for {self.url}",
                hint=self.text[:200] or None,
            )
        return self


class HttpFuture:
    """A handle to an in-flight background HTTP request.

    Returned by the ``*_async`` verbs. The request runs on a daemon thread so
    the UI never blocks. Attach a callback with :meth:`then` or await the
    result with :meth:`get`. Cancelling prevents the callbacks from firing
    (the request itself continues to completion on its thread).

    .. note::
        With ``app.http`` (or any client given ``deliver=``) callbacks run on
        the UI side, like a button handler, and may update widgets directly.
        A standalone ``HttpClient()`` runs them on the **background thread**
        that performed the request (``pymobile-http-<METHOD>``) — or on the
        caller's thread when the request had already finished; hand UI work
        over with ``app.dispatch(...)`` there.

    An exception raised by a callback is logged with its traceback; it never
    replaces the request's result (``get()`` still returns the response).
    """

    __slots__ = (
        "_client",
        "_method",
        "_url",
        "_kwargs",
        "_done",
        "_result",
        "_error",
        "_callbacks",
        "_lock",
        "_cancelled",
    )

    def __init__(
        self,
        client: HttpClient,
        method: str,
        url: str,
        kwargs: dict[str, Any],
    ) -> None:
        self._client = client
        self._method = method
        self._url = url
        self._kwargs = kwargs
        self._done = threading.Event()
        self._result: Response | None = None
        self._error: BaseException | None = None
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._cancelled = False

    # -- results -----------------------------------------------------------
    @property
    def done(self) -> bool:
        """Whether the request has finished."""
        return self._done.is_set()

    @property
    def cancelled(self) -> bool:
        """Whether :meth:`cancel` was called."""
        return self._cancelled

    def cancel(self) -> None:
        """Detach callbacks from this request.

        The network exchange itself still runs to completion on its daemon
        thread (``urllib`` cannot be interrupted portably) and ``get()`` keeps
        returning its result; only the registered callbacks are suppressed.
        """
        with self._lock:
            self._cancelled = True
            self._callbacks.clear()

    def get(self, timeout: float | None = None) -> Response:
        """Block until the request finishes and return the :class:`Response`.

        Raises :class:`TimeoutError` when ``timeout`` seconds elapse before the
        request completes. Raises :class:`NetworkError` if the request failed.
        ``timeout`` of ``None`` waits forever.

        .. warning::
            This blocks the calling thread. Never call it on the UI thread
            (e.g. inside ``on_show`` or a button handler) — it will freeze
            the screen. Use ``then(on_success=...)`` instead, or call ``get()``
            from a background job via ``app.run_job()``.
        """
        finished = self._done.wait(timeout)
        if not finished:
            raise TimeoutError(
                f"Request to {self._url} did not complete"
                + (f" within {timeout} seconds" if timeout is not None else "")
            )
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise NetworkError("Request did not complete")
        return self._result

    def then(
        self,
        on_success: Callable[[Response], None],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> HttpFuture:
        """Register callbacks to run once the request completes.

        ``on_success`` is called with the :class:`Response`; ``on_error`` (if
        given) with the exception. With ``app.http`` (a client given
        ``deliver=``) the callback runs on the UI side. A standalone client
        runs it on the request's background thread — or, if the request has
        already finished, immediately on the calling thread.

        An exception raised by the callback is logged either way: a callback
        that throws after the request has already completed used to raise out
        of ``then()`` into the caller, so the same broken callback was a
        traceback or a log line depending on whether the response had arrived.
        """
        callback: Callable[[], None] | None = None
        with self._lock:
            if self._cancelled:
                return self

            def callback() -> None:
                # Re-check at delivery time. ``cancel()`` can run after this
                # wrapper was queued (the request finished and its callbacks
                # were handed to the delivery queue) — the documented promise
                # is that cancelling suppresses the callbacks, so a wrapper
                # that is already in flight must honour it too.
                if self._cancelled:
                    return
                try:
                    self._fire(on_success, on_error)
                except Exception:
                    _log.exception(
                        "unhandled exception in an HttpFuture callback for %s", self._url
                    )

            if not self._done.is_set():
                self._callbacks.append(callback)
                return self
        # Never call user code while holding _lock: callbacks are allowed to
        # cancel this future or attach another callback.
        assert callback is not None
        self._run(callback)
        return self

    def _run(self, callback: Callable[[], None]) -> None:
        deliver = self._client.deliver
        if deliver is None:
            callback()
        else:
            deliver(callback)

    def _fire(
        self,
        on_success: Callable[[Response], None],
        on_error: Callable[[BaseException], None] | None,
    ) -> None:
        if self._error is not None:
            if on_error is not None:
                on_error(self._error)
        elif self._result is not None:
            on_success(self._result)

    def _complete(self, result: Response | None, error: BaseException | None) -> None:
        with self._lock:
            self._result = result
            self._error = error
            self._done.set()
            callbacks = [] if self._cancelled else list(self._callbacks)
            self._callbacks.clear()
        # User callbacks intentionally run after releasing _lock. A failing
        # callback must neither hide the others nor rewrite the result.
        for cb in callbacks:
            try:
                self._run(cb)
            except Exception:
                _log.exception("unhandled exception in an HttpFuture callback for %s", self._url)


#: Per-request options accepted by the verbs (they are forwarded to ``request``).
_REQUEST_OPTIONS = frozenset(
    {"json", "data", "files", "compress", "headers", "timeout", "retry_safe", "stream"}
)
#: Options that exist, but on ``HttpClient(...)`` instead of on each request.
_CLIENT_OPTIONS = frozenset({"retries", "backoff", "cache", "security", "user_agent", "base_url"})


def _check_request_options(verb: str, kwargs: Mapping[str, Any]) -> None:
    """Fail with a message that names the real problem, not ``request()``."""
    for key in kwargs:
        if key in _REQUEST_OPTIONS:
            continue
        if key in _CLIENT_OPTIONS:
            raise TypeError(
                f"{verb}() got an unexpected keyword argument {key!r}: "
                f"{key} is set on HttpClient({key}=...), not per request"
            )
        raise TypeError(
            f"{verb}() got an unexpected keyword argument {key!r}; "
            f"accepted options are: {', '.join(sorted(_REQUEST_OPTIONS))}"
        )


@dataclass(slots=True)
class HttpClient:
    """A small HTTP client with both synchronous and async (background) verbs.

    ``retries`` applies only to idempotent requests (GET, HEAD, OPTIONS):
    connection errors and retryable status codes (408/425/429 and 5xx) are
    retried with exponential backoff. POST/PUT/DELETE are never replayed
    automatically — a retry could perform a side effect twice — pass
    ``retry_safe=True`` to opt in. The synchronous ``get``/
    ``post``/``put``/``delete`` return a :class:`Response` directly; the
    ``*_async`` variants run on a background thread and return an
    :class:`HttpFuture` so the UI thread is never blocked.
    """

    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    retries: int = 0
    backoff: float = 0.5
    #: A deadline for the whole call -- every retry and every backoff sleep
    #: included. ``timeout`` bounds one attempt: with ``timeout=2, retries=3``
    #: a single ``get()`` used to take 8.4 s. ``None`` keeps the old behaviour.
    timeout_budget: float | None = None
    #: Redirect hops followed before the response is returned as-is.
    max_redirects: int = 10
    #: Worker threads for the ``*_async`` verbs (default: twice the CPU count).
    max_workers: int | None = None
    user_agent: str = f"PyMobile/{__import__('pymobile').__version__}"
    cache: HttpCache | None = field(default=None, repr=False)
    security: HttpSecurityPolicy = field(default_factory=HttpSecurityPolicy)
    #: Runs ``HttpFuture`` callbacks. ``app.http`` gets one that runs them on
    #: the UI side; None (the default) runs them on the request thread.
    deliver: Callable[[Callable[[], None]], None] | None = field(
        default=None, repr=False, compare=False
    )
    #: Idle keep-alive sockets, one per scheme/host/port (see _ConnectionPool).
    _pool: _ConnectionPool = field(
        default_factory=lambda: _ConnectionPool(), repr=False, compare=False, init=False
    )
    _budget_started: float = field(default=0.0, repr=False, compare=False, init=False)
    _workers: _RequestPool = field(
        default_factory=lambda: _RequestPool(32), repr=False, compare=False, init=False
    )

    def __post_init__(self) -> None:
        # Validation for the common mistakes + the options that used to slip through
        if not isinstance(self.timeout, (int, float)):
            raise TypeError(f"timeout must be a number, got {type(self.timeout).__name__!r}")
        if self.timeout <= 0:
            raise ValueError(
                f"timeout must be positive, got {self.timeout}; "
                "pass e.g. timeout=15.0 for 15 seconds"
            )
        if not isinstance(self.retries, int) or isinstance(self.retries, bool):
            raise TypeError(
                f"retries must be an int, got {type(self.retries).__name__!r}; "
                f"pass e.g. retries=0 or retries=2"
            )
        if self.retries < 0:
            raise ValueError(
                f"retries must be >= 0, got {self.retries}; "
                "pass 0 for no retries, 2 for two retries"
            )
        if self.timeout_budget is not None:
            if not isinstance(self.timeout_budget, (int, float)) or isinstance(
                self.timeout_budget, bool
            ):
                raise TypeError(
                    f"timeout_budget must be a number or None, got "
                    f"{type(self.timeout_budget).__name__!r}"
                )
            if self.timeout_budget <= 0:
                raise ValueError(f"timeout_budget must be positive, got {self.timeout_budget}")
        if isinstance(self.max_redirects, bool) or not isinstance(self.max_redirects, int):
            raise TypeError("max_redirects must be an int")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be >= 0")
        if not isinstance(self.backoff, (int, float)):
            raise TypeError(f"backoff must be a number, got {type(self.backoff).__name__!r}")
        if self.backoff < 0:
            raise ValueError(f"backoff must be >= 0, got {self.backoff}")
        if self.base_url is not None and not isinstance(self.base_url, str):
            raise TypeError(f"base_url must be a string, got {type(self.base_url).__name__!r}")
        if isinstance(self.base_url, str) and self.base_url.strip() == "" and self.base_url != "":
            raise ValueError(
                "base_url must not be only whitespace; "
                'pass "" for no base URL or a URL like https://api.example.com'
            )
        # Docs historically showed ``HttpClient(cache=app.storage)``. A
        # ``Storage`` is adopted **as the same instance** (see HttpCache): the
        # old code built a second Storage on the same file, and both owners
        # kept their own snapshot, so the next persist of either one wiped the
        # other's keys ("settings change loses the cache, cache write loses
        # the settings"). Anything else with a ``path`` is a location, not an
        # owner, and gets its own file-backed cache.
        self._workers = _RequestPool(_http_worker_count(self.max_workers))
        cache = self.cache
        if cache is None or isinstance(cache, HttpCache):
            return
        if isinstance(cache, Storage):
            self.cache = HttpCache(storage=cache)
            return
        path = getattr(cache, "path", cache)
        self.cache = HttpCache(path)

    # -- synchronous verbs -------------------------------------------------
    def get(self, url: str, *, params: Params | None = None, **kwargs: Any) -> Response:
        """Perform a GET request."""
        _check_request_options("get", kwargs)
        return self.request("GET", url, params=params, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        """Perform a POST request."""
        _check_request_options("post", kwargs)
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        """Perform a PUT request."""
        _check_request_options("put", kwargs)
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        """Perform a DELETE request."""
        _check_request_options("delete", kwargs)
        return self.request("DELETE", url, **kwargs)

    # -- asynchronous verbs ------------------------------------------------
    def get_async(self, url: str, *, params: Params | None = None, **kwargs: Any) -> HttpFuture:
        """Start a GET request on a background thread; returns an :class:`HttpFuture`."""
        _check_request_options("get_async", kwargs)
        kwargs.setdefault("params", params)
        return self._async("GET", url, kwargs)

    def post_async(self, url: str, **kwargs: Any) -> HttpFuture:
        """Start a POST request on a background thread; returns an :class:`HttpFuture`."""
        return self._async("POST", url, kwargs)

    def put_async(self, url: str, **kwargs: Any) -> HttpFuture:
        """Start a PUT request on a background thread; returns an :class:`HttpFuture`."""
        return self._async("PUT", url, kwargs)

    def delete_async(self, url: str, **kwargs: Any) -> HttpFuture:
        """Start a DELETE request on a background thread; returns an :class:`HttpFuture`."""
        return self._async("DELETE", url, kwargs)

    def _async(self, method: str, url: str, kwargs: dict[str, Any]) -> HttpFuture:
        future = HttpFuture(self, method, url, dict(kwargs))

        def run() -> None:
            try:
                result = self.request(method, url, **kwargs)
            except BaseException as error:
                future._complete(None, error)
            else:
                future._complete(result, None)

        self._workers.submit(run)
        return future

    # -- cached GET --------------------------------------------------------
    def get_cached(
        self,
        url: str,
        *,
        ttl: float = 300.0,
        params: Params | None = None,
        **kwargs: Any,
    ) -> Response:
        """Perform a GET, serving a cached copy when fresh and storing on success.

        Returns a fresh cached response without hitting the network when one is
        available and newer than ``ttl`` seconds. Otherwise performs the request
        and stores the successful (2xx) result in the cache. On a network
        failure, a stale cached response (any age) is returned so the app can
        keep working offline; if there is no cache at all the ``NetworkError``
        propagates.

        Requires ``self.cache``; raises ``ValueError`` when it is unset.
        """
        if self.cache is None:
            raise ValueError("get_cached() needs a cache; pass HttpClient(cache=HttpCache())")
        _check_request_options("get_cached", kwargs)
        final_url = self._build_url(url, params)
        # Responses fetched with different credentials are different entries:
        # otherwise one account's data would be served to the next.
        variant = _credential_variant(
            self._merge_headers(kwargs.get("headers"), None),
            self.security.credential_headers,
        )
        if self.cache.is_fresh(final_url, ttl, variant=variant):
            entry = self.cache.get(final_url, variant=variant)
            if entry is not None:
                return _entry_to_response(entry, final_url, from_cache=True)
        try:
            response = self.get(final_url, **kwargs)
        except NetworkError:
            stale = self.cache.get_stale(final_url, ttl, variant=variant)
            if stale is not None:
                return _entry_to_response(stale, final_url, from_cache=True)
            raise
        if response.ok and self.cache is not None:
            self.cache.set(
                final_url,
                response.status,
                response.headers,
                response.content,
                variant=variant,
                charset=response.encoding,
            )
        return response

    # -- core --------------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Params | None = None,
        json: Any = None,
        data: bytes | str | Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        compress: bool = False,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        retry_safe: bool | None = None,
        stream: bool = False,
    ) -> Response:
        (
            """Send a request and return a :class:`Response`.

        Raises :class:`~pymobile.errors.NetworkError` on transport failures;
        HTTP error statuses are returned, not raised (use
        :meth:`Response.raise_for_status`).

        ``retry_safe=True`` permits retries for non-idempotent verbs; the
        default retries only GET/HEAD/OPTIONS.

        ``stream=True`` returns as soon as the headers are in, with the body
        left on the socket: read it with ``response.iter_content()``, which
        also releases the connection.
        """
            ""
        )
        if json is not None and data is not None:
            raise ValueError("pass either json= or data=, not both")
        if files is not None and json is not None:
            raise ValueError("pass files= with data=, not with json=")

        final_url = self._build_url(url, params)
        body: bytes | None
        content_type: str | None
        if files is not None:
            body, content_type = _encode_multipart(
                data if isinstance(data, Mapping) else None, files
            )
        else:
            body, content_type = self._encode_body(json, data)
        request_headers = self._merge_headers(headers, content_type)
        if compress and body is not None and len(body) >= COMPRESS_THRESHOLD:
            # gzip the *request*: a 30 MB JSON payload on a phone uplink is
            # mostly repetition. Skipped for tiny bodies, where the header and
            # the gzip framing cost more than they save.
            body = _gzip_bytes(body)
            request_headers["Content-Encoding"] = "gzip"
        attempts = max(0, self.retries) + 1
        # Never replay a non-idempotent verb implicitly: a retried POST could
        # perform its side effect twice. Callers opt in per request.
        if retry_safe is not None:
            allowed = retry_safe
        elif method.upper() in ("GET", "HEAD", "OPTIONS"):
            allowed = True
        else:
            allowed = False
        if not allowed:
            attempts = 1
        last_error: Exception | None = None
        self._budget_started = time.monotonic()

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                response = self._send(
                    method.upper(), final_url, body, request_headers, timeout, stream=stream
                )
            except NetworkError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                self._sleep(attempt)
                _log.debug("retrying %s %s (%s)", method, redact_url(final_url), exc)
                continue

            elapsed = time.monotonic() - started
            response = Response(
                status=response.status,
                headers=response.headers,
                content=response.content,
                url=response.url,
                elapsed=elapsed,
                encoding=response.encoding,
                stream=response.stream,
                _reader=response._reader,
                _closer=response._closer,
            )
            if response.status in _RETRY_STATUSES and attempt < attempts:
                self._sleep(attempt)
                _log.debug(
                    "retrying %s %s after HTTP %s",
                    method,
                    redact_url(final_url),
                    response.status,
                )
                continue
            _log.debug(
                "%s %s -> %s in %.0f ms",
                method,
                redact_url(final_url),
                response.status,
                elapsed * 1000,
            )
            return response

        raise NetworkError(str(last_error) if last_error else "Request failed")

    def _send(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float | None,
        *,
        stream: bool = False,
    ) -> Response:
        """One transport attempt, on a pooled keep-alive connection.

        ``timeout`` is a **deadline for the whole attempt**, not for each read:
        a server that dribbles one byte a second used to keep a request alive
        far past the timeout the caller asked for. It is enforced on the
        socket and, for the body, by checking the elapsed time while reading.
        """
        if self.timeout_budget is not None:
            remaining_budget = self.timeout_budget - (time.monotonic() - self._budget_started)
            if remaining_budget <= 0:
                raise NetworkError(
                    f"Request to {url} ran past the {self.timeout_budget:g}s budget "
                    f"({self.retries} retr{'y' if self.retries == 1 else 'ies'} included)",
                    hint=(
                        "Raise HttpClient(timeout_budget=...) or lower `retries`; "
                        "the budget covers retries and backoff, `timeout` covers one attempt."
                    ),
                )
            effective = remaining_budget if timeout is None else min(timeout, remaining_budget)
        else:
            effective = timeout if timeout is not None else self.timeout
        deadline = time.monotonic() + effective

        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme
        host = parsed.hostname or ""
        port = parsed.port
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        pool_key = (scheme, host, port)
        connection = self._pool.acquire(scheme, host, port, effective)
        request_headers = dict(headers)
        request_headers.setdefault("Host", parsed.netloc)
        request_headers.setdefault("Accept-Encoding", ACCEPT_ENCODING)
        try:
            connection.request(method, target, body=body, headers=request_headers)
            raw = connection.getresponse()
            status = raw.status
            response_headers = {key: value for key, value in raw.getheaders()}
            # A redirect we are about to follow: its body is not part of the
            # answer, and an unframed one (``HTTP/1.1`` without
            # ``Content-Length``) would block the read until the server closes.
            # Dropping the socket is what lets the hop happen at all.
            following = (
                status in _REDIRECT_STATUSES
                and bool(_header_value(response_headers, "location"))
                and self.max_redirects > 0
            )
            if following:
                payload = b""
                # Close the response before the socket: the file object holds
                # its own reference, and a socket closed while the file is open
                # leaves the server waiting on a connection we think is gone.
                raw.close()
                self._pool.discard(connection)
            elif stream:
                # Leave the body on the socket; iter_content() reads it and
                # hands the connection back. Nothing may return early here.
                payload = b""
                reader = raw.read
                closer = functools.partial(self._release_stream, pool_key, connection, raw)
            else:
                payload = raw.read()
                if time.monotonic() > deadline:
                    raise TimeoutError(f"body not received within {effective:g}s")
                if raw.will_close:
                    self._pool.discard(connection)
                else:
                    self._pool.release(*pool_key, connection)
            final_url = url
        except TimeoutError as exc:
            self._pool.discard(connection)
            raise NetworkError(
                f"Request to {url} timed out after {effective:g}s",
                hint=(
                    "Raise HttpClient(timeout=...) for slow servers; "
                    "the timeout covers the whole request."
                ),
            ) from exc
        except http.client.IncompleteRead as exc:
            # The socket worked and the body stopped early: reporting "could
            # not reach" there was misleading, and leaking IncompleteRead made
            # the failure impossible for an app to handle.
            self._pool.discard(connection)
            raise NetworkError(f"Could not read {url}: {exc}") from exc
        except (http.client.HTTPException, OSError) as exc:
            self._pool.discard(connection)
            raise NetworkError(
                f"Could not reach {url}: {exc}",
                hint="Check connectivity and the INTERNET permission in your project config.",
            ) from exc

        redirects = 0
        while (
            status in _REDIRECT_STATUSES
            and (location := _header_value(response_headers, "location"))
            and redirects < self.max_redirects
        ):
            final_url = urllib.parse.urljoin(final_url, location)
            self._check_redirect(final_url, url)
            redirects += 1
            if status in _SEE_OTHER_STATUSES:
                # A browser turns a 301/302/303 into a GET and drops the body;
                # replaying a POST to the new location would post twice.
                method, body = "GET", None
                request_headers.pop("Content-Type", None)
            response_headers, status, payload = self._follow(
                method,
                final_url,
                body,
                request_headers,
                deadline,
                effective,
                hop=redirects,
                max_redirects=self.max_redirects,
            )

        lowered = {key.lower(): value for key, value in response_headers.items()}
        if stream and not following:
            # A compressed stream would have to be inflated chunk by chunk to be
            # useful, and a truncated one is not detectable here; ask for the
            # plain body instead and hand back what the socket delivered.
            return Response(
                status=status,
                headers=lowered,
                content=b"",
                url=final_url,
                encoding=_charset_from_headers(lowered) or "utf-8",
                stream=True,
                _reader=reader,
                _closer=closer,
            )
        encoding = _header_value(response_headers, "content-encoding")
        if encoding:
            payload = _decompress(payload, encoding)
            # The bytes are no longer in that encoding: leaving the header in
            # place would make a caller that re-serves the body wrong, and
            # ``Content-Length`` no longer describes what we hold.
            lowered.pop("content-encoding", None)
            lowered.pop("content-length", None)
        charset = _charset_from_headers(lowered) or "utf-8"
        return Response(
            status=status, headers=lowered, content=payload, url=final_url, encoding=charset
        )

    def _follow(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        deadline: float,
        effective: float,
        hop: int = 0,
        max_redirects: int = 0,
    ) -> tuple[dict[str, str], int, bytes]:
        """Send one redirect hop, keeping the method and body for 307/308."""
        parsed = urllib.parse.urlsplit(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NetworkError(f"Request to {url} timed out after {effective:g}s")
        connection = self._pool.acquire(
            parsed.scheme, parsed.hostname or "", parsed.port, remaining
        )
        hop_headers = dict(headers)
        hop_headers["Host"] = parsed.netloc
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        # No ``Connection`` header: a 302 whose body is omitted (so the socket
        # stays usable) and one that closes the socket are both legal, and the
        # server decides. ``will_close`` below records which one happened.
        hop_headers.pop("Connection", None)
        try:
            connection.request(method, target, body=body, headers=hop_headers)
            raw = connection.getresponse()
            status = raw.status
            response_headers = {key: value for key, value in raw.getheaders()}
            following = (
                status in _REDIRECT_STATUSES
                and bool(_header_value(response_headers, "location"))
                and hop < max_redirects
            )
            if following:
                payload = b""
                raw.close()
                self._pool.discard(connection)
                return response_headers, status, payload
            payload = raw.read()
        except (http.client.HTTPException, OSError, TimeoutError) as exc:
            self._pool.discard(connection)
            raise NetworkError(f"Could not follow redirect to {url}: {exc}") from exc
        if raw.will_close:
            self._pool.discard(connection)
        else:
            self._pool.release(parsed.scheme, parsed.hostname or "", parsed.port, connection)
        return response_headers, status, payload

    def _release_stream(
        self,
        pool_key: tuple[str, str, int | None],
        connection: http.client.HTTPConnection,
        raw: http.client.HTTPResponse,
    ) -> None:
        """Close a streamed response and return or drop its connection."""
        try:
            raw.close()
        finally:
            self._pool.release(*pool_key, connection)

    def _check_redirect(self, target: str, source: str) -> None:
        """Re-check the security policy on every hop and drop credentials cross-origin.

        Ported from the ``urllib`` redirect handler: a server (or whoever
        controls it) must not be able to bounce a request to a blocked host,
        downgrade it to plain HTTP, or carry its credentials to another origin.
        """
        scheme = urllib.parse.urlsplit(target).scheme
        if scheme not in ("http", "https"):
            raise NetworkError(f"Redirect to unsupported URL scheme blocked: {target!r}")
        try:
            self.security.validate(target)
        except NetworkError as exc:
            raise NetworkError(
                f"Redirect from {source} to {target} blocked: {exc}",
                hint="Add the target host to HttpSecurityPolicy.allowed_hosts if it is trusted.",
            ) from exc

    @staticmethod
    def _to_response(url: str, status: int, headers: Mapping[str, str], content: bytes) -> Response:
        """Build a Response, honouring the charset from ``Content-Type``."""
        normalized = {key.lower(): value for key, value in headers.items()}
        encoding = _charset_from_headers(normalized) or "utf-8"
        return Response(
            status=status, headers=normalized, content=content, url=url, encoding=encoding
        )

    def close(self) -> None:
        """Close idle sockets and stop the async workers."""
        self._pool.close()
        self._workers.shutdown()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- helpers -----------------------------------------------------------
    def _build_url(self, url: str, params: Params | None) -> str:
        """Join the base URL and append the query string."""
        full = url
        if self.base_url and not urllib.parse.urlparse(url).scheme:
            full = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"
        scheme = urllib.parse.urlparse(full).scheme
        if scheme not in ("http", "https"):
            raise NetworkError(
                f"Unsupported URL scheme in {full!r}",
                hint="Only http:// and https:// URLs are allowed.",
            )
        if params:
            filtered = {k: str(v) for k, v in params.items() if v is not None}
            if filtered:
                # A fragment is client-side only. Query parameters must be
                # inserted before ``#fragment`` or servers never receive them.
                parsed = urllib.parse.urlsplit(full)
                query = parsed.query
                encoded = urllib.parse.urlencode(filtered)
                query = f"{query}&{encoded}" if query else encoded
                full = urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
                )
        self.security.validate(full)
        return full

    @staticmethod
    def _encode_body(
        json: Any, data: bytes | str | Mapping[str, Any] | None
    ) -> tuple[bytes | None, str | None]:
        """Serialise the request body and infer its content type."""
        if json is not None:
            return jsonlib.dumps(json).encode("utf-8"), "application/json"
        if data is None:
            return None, None
        if isinstance(data, bytes):
            return data, None
        if isinstance(data, str):
            return data.encode("utf-8"), "text/plain; charset=utf-8"
        return (
            urllib.parse.urlencode(dict(data)).encode("utf-8"),
            "application/x-www-form-urlencoded",
        )

    def _merge_headers(
        self, headers: Mapping[str, str] | None, content_type: str | None
    ) -> dict[str, str]:
        """Combine defaults, per-request headers and the inferred content type."""
        merged = {"User-Agent": self.user_agent, "Accept": "*/*"}
        merged.update(self.headers)
        if content_type:
            merged["Content-Type"] = content_type
        if headers:
            merged.update(headers)
        return merged

    def _sleep(self, attempt: int) -> None:
        """Exponential backoff between retries."""
        if self.backoff > 0:
            time.sleep(self.backoff * (2 ** (attempt - 1)))


def _credential_variant(headers: Mapping[str, str], extra: frozenset[str] = frozenset()) -> str:
    """Fingerprint of the credentials a request carries ('' when it has none).

    ``extra`` are the policy's additional credential header names, so the
    cache splits (and the redirect handler strips) exactly the same set.
    """
    names = _DEFAULT_CREDENTIAL_HEADERS | extra
    parts = sorted(
        f"{key.lower()}:{value}" for key, value in headers.items() if key.lower() in names
    )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """TLS context that prefers the packaged certifi bundle when available.

    Built once: loading the CA bundle on every request costs tens of ms.

    ``ssl`` is imported lazily — see :meth:`HttpClient._send`. When the module
    is absent (a ``--no-ssl`` build) the failure is reported as a targeted
    :class:`NetworkError` at the moment an HTTPS request is attempted, rather
    than as an ``ImportError`` while importing the framework.
    """
    try:
        import ssl
    except ImportError as exc:
        raise NetworkError(
            "This build was packaged with --no-ssl: HTTPS is unavailable",
            hint=(
                "Rebuild without `no_ssl` (pymobile.toml) / `--no-ssl` so the TLS "
                "libraries are packaged, or make the app work offline."
            ),
        ) from exc
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _decode_cached_body(raw: Any) -> bytes:
    """Accept base64 (current) and the legacy JSON-array-of-bytes format."""
    import base64

    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw)
        except (ValueError, TypeError):
            return raw.encode("utf-8")
    if isinstance(raw, list):
        return bytes(raw)
    return bytes(raw)


def _charset_from_headers(headers: Mapping[str, str]) -> str:
    """The charset named by a ``Content-Type`` header, or ``""``.

    MIME parameter names are case-insensitive — ``CHARSET=iso-8859-1`` is as
    valid as ``charset=…`` — values may be quoted, and spaces are allowed
    around the ``=``. The old implementation *checked* the lower-cased header
    but then split the original spelling, so any upper-case parameter raised
    ``IndexError`` out of an otherwise perfectly valid response.
    """
    for key, value in headers.items():
        if key.lower() != "content-type":
            continue
        for parameter in str(value).split(";"):
            name, separator, raw = parameter.partition("=")
            if separator and name.strip().casefold() == "charset":
                charset = raw.strip().strip("\"'")
                if charset:
                    return charset
    return ""


def _entry_to_response(entry: dict[str, Any], url: str, *, from_cache: bool = True) -> Response:
    """Rebuild a :class:`Response` from a cached entry, marking it as cached.

    The charset is read from the entry's ``charset`` field and falls back to
    the stored ``Content-Type`` header. It used to fall back to UTF-8 (while
    ``encoding`` held the *payload* format, ``"base64"``), so a cached
    ``iso-8859-1`` body decoded as ``caf\ufffd`` while the freshly fetched one
    read ``café``.
    """
    content = _decode_cached_body(entry.get("content", b""))
    headers = dict(entry.get("headers", {}))
    encoding = str(entry.get("charset") or "") or _charset_from_headers(headers)
    if not encoding:
        # Entry written by an older version: ``encoding`` was the payload
        # format, never the HTTP charset.
        legacy = entry.get("encoding", "")
        encoding = legacy if legacy and legacy != "base64" else "utf-8"
    return Response(
        status=int(entry.get("status", 0)),
        headers=headers,
        content=content,
        url=url,
        encoding=encoding,
        from_cache=from_cache,
    )
