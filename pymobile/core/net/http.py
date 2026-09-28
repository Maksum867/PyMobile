"""HTTP client built on the standard library.

Why not ``requests``? Every dependency bundled into an APK costs download size
and build time, and ``urllib`` already covers GET/POST/PUT/DELETE. The client
adds the parts that are genuinely missing: JSON handling, timeouts, retries
with backoff, a base URL and default headers.
"""

from __future__ import annotations

import functools
import hashlib
import http.client
import json as jsonlib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from ...errors import NetworkError
from ...log import get_logger
from .cache import HttpCache

__all__ = ["HttpClient", "HttpSecurityPolicy", "Response", "HttpFuture", "DEFAULT_TIMEOUT"]

_log = get_logger("http")

DEFAULT_TIMEOUT = 15.0
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
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

    def validate(self, url: str) -> None:
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
#: another origin (urllib forwards every header by default).
_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization")


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
        if new is not None and _origin(newurl) != _origin(req.full_url):
            for name in list(new.headers):
                if name.lower() in _CREDENTIAL_HEADERS:
                    del new.headers[name]
            for name in list(new.unredirected_hdrs):
                if name.lower() in _CREDENTIAL_HEADERS:
                    del new.unredirected_hdrs[name]
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

    @property
    def ok(self) -> bool:
        """``True`` for 2xx status codes."""
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        """Body decoded as text (invalid bytes are replaced, never raised)."""
        return self.content.decode(self.encoding, errors="replace")

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
        """
        callback: Callable[[], None] | None = None
        with self._lock:
            if self._cancelled:
                return self

            def callback() -> None:
                self._fire(on_success, on_error)

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
    user_agent: str = f"PyMobile/{__import__('pymobile').__version__}"
    cache: HttpCache | None = field(default=None, repr=False)
    security: HttpSecurityPolicy = field(default_factory=HttpSecurityPolicy)
    #: Runs ``HttpFuture`` callbacks. ``app.http`` gets one that runs them on
    #: the UI side; None (the default) runs them on the request thread.
    deliver: Callable[[Callable[[], None]], None] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # Validation for common mistakes (BUG-27, BUG-28) + new checks
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
        if not isinstance(self.backoff, (int, float)):
            raise TypeError(f"backoff must be a number, got {type(self.backoff).__name__!r}")
        if self.backoff < 0:
            raise ValueError(f"backoff must be >= 0, got {self.backoff}")
        if self.base_url is not None and not isinstance(self.base_url, str):
            raise TypeError(
                f"base_url must be a string, got {type(self.base_url).__name__!r}"
            )
        if isinstance(self.base_url, str) and self.base_url.strip() == "" and self.base_url != "":
            raise ValueError(
                "base_url must not be only whitespace; "
                "pass \"\" for no base URL or a URL like https://api.example.com"
            )
        # Docs historically showed ``HttpClient(cache=app.storage)``. Accept a
        # Storage (or any object with a ``path``) and wrap it in HttpCache.
        cache = self.cache
        if cache is None or isinstance(cache, HttpCache):
            return
        path = getattr(cache, "path", cache)
        self.cache = HttpCache(path)

    # -- synchronous verbs -------------------------------------------------
    def get(self, url: str, *, params: Params | None = None, **kwargs: Any) -> Response:
        """Perform a GET request."""
        return self.request("GET", url, params=params, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        """Perform a POST request."""
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        """Perform a PUT request."""
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        """Perform a DELETE request."""
        return self.request("DELETE", url, **kwargs)

    # -- asynchronous verbs ------------------------------------------------
    def get_async(self, url: str, *, params: Params | None = None, **kwargs: Any) -> HttpFuture:
        """Start a GET request on a background thread; returns an :class:`HttpFuture`."""
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

        threading.Thread(target=run, name=f"pymobile-http-{method}", daemon=True).start()
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
        final_url = self._build_url(url, params)
        # Responses fetched with different credentials are different entries:
        # otherwise one account's data would be served to the next.
        variant = _credential_variant(self._merge_headers(kwargs.get("headers"), None))
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
                final_url, response.status, response.headers, response.content, variant=variant
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
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        retry_safe: bool | None = None,
    ) -> Response:
        """Send a request and return a :class:`Response`.

        Raises :class:`~pymobile.errors.NetworkError` on transport failures;
        HTTP error statuses are returned, not raised (use
        :meth:`Response.raise_for_status`).

        ``retry_safe=True`` permits retries for non-idempotent verbs; the
        default retries only GET/HEAD/OPTIONS.
        """
        if json is not None and data is not None:
            raise ValueError("pass either json= or data=, not both")

        final_url = self._build_url(url, params)
        body, content_type = self._encode_body(json, data)
        request_headers = self._merge_headers(headers, content_type)
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

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                response = self._send(method.upper(), final_url, body, request_headers, timeout)
            except NetworkError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                self._sleep(attempt)
                _log.debug("retrying %s %s (%s)", method, final_url, exc)
                continue

            elapsed = time.monotonic() - started
            response = Response(
                status=response.status,
                headers=response.headers,
                content=response.content,
                url=response.url,
                elapsed=elapsed,
                encoding=response.encoding,
            )
            if response.status in _RETRY_STATUSES and attempt < attempts:
                self._sleep(attempt)
                _log.debug("retrying %s %s after HTTP %s", method, final_url, response.status)
                continue
            _log.debug("%s %s -> %s in %.0f ms", method, final_url, response.status, elapsed * 1000)
            return response

        raise NetworkError(str(last_error) if last_error else "Request failed")

    def _send(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float | None,
    ) -> Response:
        """One transport attempt."""
        request = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()),
            _PolicyRedirectHandler(self.security),
        )
        try:
            with opener.open(
                request,
                timeout=timeout if timeout is not None else self.timeout,
            ) as raw:
                return self._to_response(raw.geturl(), raw.status, dict(raw.headers), raw.read())
        except urllib.error.HTTPError as exc:  # 4xx/5xx are valid responses here
            payload = exc.read() if hasattr(exc, "read") else b""
            return self._to_response(url, int(exc.code), dict(exc.headers or {}), payload)
        except urllib.error.URLError as exc:
            raise NetworkError(
                f"Could not reach {url}: {exc.reason}",
                hint="Check connectivity and the INTERNET permission in your project config.",
            ) from exc
        except TimeoutError as exc:
            raise NetworkError(f"Request to {url} timed out") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise NetworkError(
                f"Could not read {url}: {exc}",
                hint="Check connectivity and the INTERNET permission in your project config.",
            ) from exc

    @staticmethod
    def _to_response(url: str, status: int, headers: Mapping[str, str], content: bytes) -> Response:
        """Build a Response, honouring the charset from ``Content-Type``."""
        normalized = {key.lower(): value for key, value in headers.items()}
        encoding = "utf-8"
        content_type = normalized.get("content-type", "")
        if "charset=" in content_type:
            encoding = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        return Response(
            status=status, headers=normalized, content=content, url=url, encoding=encoding
        )

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


def _credential_variant(headers: Mapping[str, str]) -> str:
    """Fingerprint of the credentials a request carries ('' when it has none)."""
    parts = sorted(
        f"{key.lower()}:{value}" for key, value in headers.items()
        if key.lower() in _CREDENTIAL_HEADERS or key.lower() == "x-api-key"
    )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """TLS context that prefers the packaged certifi bundle when available.

    Built once: loading the CA bundle on every request costs tens of ms.
    """
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


def _entry_to_response(entry: dict[str, Any], url: str, *, from_cache: bool = True) -> Response:
    """Rebuild a :class:`Response` from a cached entry, marking it as cached."""
    content = _decode_cached_body(entry.get("content", b""))
    encoding = entry.get("encoding", "utf-8")
    if encoding == "base64":
        encoding = "utf-8"
    return Response(
        status=int(entry.get("status", 0)),
        headers=dict(entry.get("headers", {})),
        content=content,
        url=url,
        encoding=encoding,
        from_cache=from_cache,
    )
