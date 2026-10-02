"""A tiny disk-backed HTTP response cache (offline support).

Caches the body, status and headers of ``GET`` responses keyed by URL so an app
can render the last-known-good data while offline, or avoid refetching data
that rarely changes. Built on the same JSON store the framework uses for local
storage, so it adds no dependency and lives in the app's data directory.

The cache is deliberately simple: keys are URLs (with query strings) plus a
fingerprint of the request's credentials, so responses fetched for one account
are never served to another; a ``ttl`` bounds freshness, stale entries are
still returned so callers can show something rather than nothing, and the
oldest entries are evicted beyond ``max_entries``. Use :class:`HttpCache` directly or hand it to
:class:`~pymobile.core.net.http.HttpClient` via ``cache=``.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...log import get_logger
from ..api.storage import Storage

__all__ = ["HttpCache"]

_log = get_logger("net.cache")


def _key_for(url: str, variant: str = "") -> str:
    """A stable, filesystem-safe cache key for a URL (and credential variant)."""
    raw = url if not variant else f"{url}\n{variant}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


DEFAULT_MAX_ENTRIES = 200


class HttpCache:
    """A disk-backed cache of HTTP responses keyed by URL.

    Each entry stores the status, headers, body (as bytes) and a timestamp.
    ``get`` returns ``None`` when the URL is not cached; ``set`` stores it.
    ``ttl`` seconds bound freshness but a stale entry is still returned by
    ``get_stale`` so an offline app can show the last-known data.

    Mutations and ``clear()`` are serialised through an internal lock so
    concurrent jobs and HTTP callbacks cannot race; readers take the same
    lock so ``clear()`` never exposes a half-wiped keyspace.


    ``variant`` separates responses to the same URL fetched with different
    credentials (``HttpClient`` passes a fingerprint of ``Authorization``/
    ``Cookie``/``X-API-Key``). ``max_entries`` bounds the cache: the oldest
    entries are evicted first (``None`` disables the limit).
    """

    __slots__ = ("_storage", "_prefix", "_lock", "_max_entries")

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        prefix: str = "http:",
        max_entries: int | None = DEFAULT_MAX_ENTRIES,
        storage: Storage | None = None,
    ) -> None:
        if max_entries is not None and max_entries < 1:
            raise ValueError(f"max_entries must be >= 1 or None, got {max_entries}")
        if storage is not None and path is not None:
            raise ValueError("pass either path or storage=, not both")
        # Defaults to the shared app data store (keys namespaced by prefix).
        #
        # ``storage=`` adopts an existing :class:`Storage` *instance* instead of
        # opening a second one on the same file: two owners of one JSON file
        # each keep their own snapshot and lock, so the next write of either
        # silently discards the other's changes. Handing over the instance is
        # what makes ``HttpClient(cache=app.storage)`` safe.
        if storage is not None:
            self._storage = storage
        else:
            self._storage = Storage(path) if path is not None else Storage()
        self._prefix = prefix
        self._lock = threading.Lock()
        self._max_entries = max_entries

    @property
    def storage(self) -> Storage:
        """The store this cache writes into (never a second handle on the file)."""
        return self._storage

    @classmethod
    def at(cls, path: str | Path) -> HttpCache:
        """Create a cache backed by a specific file (useful for tests)."""
        return cls(path)

    def _full_key(self, url: str, variant: str = "") -> str:
        return self._prefix + _key_for(url, variant)

    def get(self, url: str, *, variant: str = "") -> dict[str, Any] | None:
        """Return the cached entry ``{status, headers, content, fetched_at}`` or ``None``."""
        with self._lock:
            entry = self._storage.get(self._full_key(url, variant))
        return entry if isinstance(entry, dict) else None

    def get_stale(self, url: str, ttl: float, *, variant: str = "") -> dict[str, Any] | None:
        """Return a cached entry even if it is older than ``ttl``, or ``None``."""
        return self.get(url, variant=variant)

    def is_fresh(self, url: str, ttl: float, *, variant: str = "") -> bool:
        """Whether a cached entry exists and is newer than ``ttl`` seconds."""
        entry = self.get(url, variant=variant)
        if entry is None:
            return False
        return (time.time() - float(entry.get("fetched_at", 0))) < ttl

    def set(
        self,
        url: str,
        status: int,
        headers: Mapping[str, str],
        content: bytes,
        *,
        variant: str = "",
        charset: str = "utf-8",
    ) -> None:
        """Store a response for ``url``.

        The body is stored as base64 rather than a JSON array of bytes, which
        used to inflate both disk and CPU for large payloads. ``Set-Cookie``
        is not stored: a session cookie has no business sitting in a cache
        file. Beyond ``max_entries`` the oldest entries are evicted.

        ``charset`` records the encoding the response body was decoded with
        (from its ``Content-Type``). Without it a cached ``iso-8859-1`` body
        came back as ``caf?`` while the same bytes freshly fetched read
        ``café``: the ``encoding`` field held the *payload* encoding (base64)
        and the response decoder fell back to UTF-8.
        """
        kept_headers = {k: v for k, v in dict(headers).items() if k.lower() != "set-cookie"}
        with self._lock, self._storage.transaction() as store:
            store.set(
                self._full_key(url, variant),
                {
                    "status": status,
                    "headers": kept_headers,
                    "content": base64.b64encode(content).decode("ascii"),
                    # How the body is stored in this JSON document.
                    "encoding": "base64",
                    # How the body should be decoded as text (HTTP charset).
                    "charset": str(charset or "utf-8"),
                    "fetched_at": time.time(),
                },
            )
            self._evict_locked()

    def _evict_locked(self) -> None:
        if self._max_entries is None:
            return
        entries = [
            (float(value.get("fetched_at", 0)) if isinstance(value, dict) else 0.0, key)
            for key, value in self._storage.items()
            if key.startswith(self._prefix)
        ]
        overflow = len(entries) - self._max_entries
        if overflow <= 0:
            return
        entries.sort()
        for _, key in entries[:overflow]:
            self._storage.delete(key)
        _log.debug("evicted %d old HTTP cache entries", overflow)

    def delete(self, url: str, *, variant: str = "") -> bool:
        """Remove a cached entry; returns whether it existed."""
        with self._lock:
            return self._storage.delete(self._full_key(url, variant))

    def clear(self) -> None:
        """Drop every cached entry."""
        # Take the snapshot of matching keys and delete them under one lock so
        # a writer that arrives mid-clear cannot land an entry that survives.
        # ruff cannot see that ``Storage`` has no ``__iter__`` and refuses to
        # drop the explicit ``.keys()`` call automatically.
        with self._lock:
            keys = [k for k in self._storage.keys() if k.startswith(self._prefix)]  # noqa: SIM118
            with self._storage.transaction() as store:
                for key in keys:
                    store.delete(key)

    def __contains__(self, url: object) -> bool:
        return isinstance(url, str) and self.get(url) is not None

    def __len__(self) -> int:
        # Storage exposes keys() but deliberately is not a Mapping/iterable.
        with self._lock:
            return sum(
                1 for key in self._storage.keys() if key.startswith(self._prefix)  # noqa: SIM118
            )
