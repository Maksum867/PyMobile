"""A tiny, dependency-free key/value store backed by a JSON file.

Designed for small mobile apps: settings, user prefs, a high score, a small
cache. Values are any JSON-serialisable object. Every write persists to disk
atomically (write-to-temp, fsync, then rename), so a crash mid-write cannot
corrupt the store.

Values are *copied* on the way in and on the way out: what you get back is
exactly what a restart would load from disk, and mutating it in place never
changes the store behind your back — write it back with :meth:`Storage.set`.

A file that cannot be parsed is never silently overwritten: it is moved aside
to ``<name>.corrupt-<timestamp>`` and a warning is logged before the store
starts empty.

The store is meant to be owned by one process (the app). Writes from another
process are detected and logged, not merged.

The file lives in the app's data directory — on Android inside the app's
private storage, on the desktop in a per-app folder under the user's data dir
overridable for tests.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from ...errors import PyMobileError, ResourceError
from ...log import get_logger

__all__ = ["Storage", "default_storage_path"]

_log = get_logger("api.storage")

#: Sentinel for "the caller did not pass a default" (``None`` itself is a
#: perfectly good default, so it cannot double as the marker).
_UNSET: Any = object()


DEFAULT_STORE_FILENAME = "pymobile_store.json"


def default_storage_path(filename: str = DEFAULT_STORE_FILENAME) -> Path:
    """Return a sensible default location for the store file.

    * Android: the app's private files dir (set via ``PYMOBILE_STORAGE_DIR``
      by the runtime bootstrap).
    * Desktop: ``~/.pymobile/<filename>`` (Linux/macOS) or the user's
      ``AppData`` (Windows). ``PYMOBILE_STORAGE_DIR`` overrides it everywhere
      so tests can isolate their store.
    """
    override = os.environ.get("PYMOBILE_STORAGE_DIR")
    if override:
        return Path(override) / filename
    platform = os.environ.get("ANDROID_APP_PATH")
    if platform:
        return Path(platform) / ".." / ".." / "files" / filename
    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(home))) / "pymobile"
    else:
        base = home / ".pymobile"
    return base / filename


def _resolve_store_path(path: str | Path | None) -> Path:
    """Turn a user-supplied location into the path of the store *file*.

    ``storage_path`` is routinely read as "the folder to keep my data in" —
    the environment override ``PYMOBILE_STORAGE_DIR`` really is a directory —
    so a directory is accepted here and gets the default filename appended
    instead of failing with ``IsADirectoryError`` from inside :meth:`save`.
    """
    if path is None:
        return default_storage_path()
    text = str(path)
    resolved = Path(text)
    looks_like_dir = text.endswith(("/", os.sep)) or (os.altsep and text.endswith(os.altsep))
    if resolved.is_dir() or looks_like_dir:
        return resolved / DEFAULT_STORE_FILENAME
    return resolved


class Storage:
    """A JSON-backed key/value store.

    Example::

        store = Storage()
        store.increment("taps")          # atomic; safe from jobs and timers

    ``path`` may be either the store **file** or a **directory** to keep it in;
    a directory (existing, or a path ending in a separator) gets the default
    filename appended, so both spellings below do the same thing::

        Storage("/tmp/my-app/store.json")
        Storage("/tmp/my-app")

    Keys must be non-empty strings. ``__getitem__``/``__setitem__`` map to
    :meth:`get`/:meth:`set`; ``in``/``del`` work as expected.

    Individual operations are atomic. A read-modify-write **sequence** written
    by hand is not — use :meth:`update`, :meth:`increment` or the
    :meth:`transaction` context manager when several steps must be one unit.
    A transaction is all-or-nothing: if the block raises, every change made
    inside it is rolled back and nothing reaches the disk.
    """

    __slots__ = ("_path", "_data", "_loaded", "_lock", "_tx_depth", "_tx_dirty", "_disk_stamp")

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = _resolve_store_path(path)
        self._data: dict[str, Any] = {}
        self._loaded = False
        # A re-entrant lock makes every single operation atomic, and backs the
        # explicit `transaction()` block for multi-step sequences.
        self._lock = threading.RLock()
        # Transactions defer the disk write to the end of the outermost block.
        self._tx_depth = 0
        self._tx_dirty = False
        # (mtime_ns, size) of the file as we last read or wrote it — used to
        # notice another process writing the same store.
        self._disk_stamp: tuple[int, int] | None = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def path(self) -> Path:
        """Filesystem location of the store."""
        return self._path

    def _stamp(self) -> tuple[int, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            try:
                text = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._loaded = True
                return
            except OSError as exc:
                # Unreadable (permissions, I/O error …) is NOT "empty": starting
                # fresh here would make the next write destroy the user's data.
                raise ResourceError(
                    f"Could not read the store {self._path}: {exc}",
                    hint="Check the file permissions; the store was left untouched.",
                ) from exc
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError(f"top-level value is {type(data).__name__}, not an object")
            except ValueError as exc:
                self._quarantine(exc)
                data = {}
            self._data.update(data)
            self._loaded = True
            self._disk_stamp = self._stamp()

    def _quarantine(self, reason: Exception) -> None:
        """Move an unparsable store aside instead of letting a write replace it."""
        backup = self._path.with_name(
            f"{self._path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        try:
            os.replace(self._path, backup)
        except OSError as exc:
            raise ResourceError(
                f"The store {self._path} is corrupt ({reason}) and could not be moved aside: {exc}",
                hint="Fix or remove the file by hand; nothing was overwritten.",
            ) from exc
        _log.warning(
            "store %s could not be parsed (%s); it was moved to %s and the store starts empty",
            self._path, reason, backup,
        )

    def save(self) -> None:
        """Persist the store to disk atomically and under its process lock.

        Inside a :meth:`transaction` block the write is **deferred** to the end
        of the outermost block, exactly like every other mutation: writing
        straight to disk there used to push half-finished state out even when
        the block later raised, which broke the all-or-nothing promise (a
        reader saw the rolled-back value while memory had the original one).
        """
        with self._lock:
            self._load()
            self._persist()

    def _persist(self) -> None:
        """Write now, or at the end of the enclosing transaction; caller holds ``_lock``."""
        if self._tx_depth:
            self._tx_dirty = True
        else:
            self._write_locked()

    def _save_locked(self) -> None:
        """Backward-compatible name used by subclasses/tests; caller holds ``_lock``."""
        self._persist()

    def _write_locked(self) -> None:
        """Atomically and durably persist the in-memory store; caller holds ``_lock``."""
        # Serialise first: a bad value must fail before any file is touched.
        try:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            raise ResourceError(
                f"Storage value is not JSON serializable: {exc}",
                hint="Use only JSON types: str, int, float, bool, None, list, dict. "
                "Convert sets, tuples, or custom objects to list/dict first.",
            ) from exc
        stamp = self._stamp()
        if self._disk_stamp is not None and stamp is not None and stamp != self._disk_stamp:
            _log.warning(
                "store %s was modified by another process since it was loaded; "
                "those changes are being overwritten", self._path,
            )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp", prefix=self._path.name
            )
        except OSError as exc:
            raise ResourceError(
                f"Could not open the storage directory {self._path.parent}: {exc}",
                hint="Check that the path is writable and is not a file.",
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
        except IsADirectoryError as exc:
            with suppress(OSError):
                os.unlink(temp_name)
            raise ResourceError(
                f"The storage path {self._path} is a directory, not a file.",
                hint=(
                    "Pass a file such as "
                    f"{self._path / DEFAULT_STORE_FILENAME}, or a directory that "
                    "does not yet exist; PYMOBILE_STORAGE_DIR takes the directory."
                ),
            ) from exc
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise
        _fsync_dir(self._path.parent)
        self._disk_stamp = self._stamp()

    # -- mapping API -------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key``, or ``default`` when absent."""
        with self._lock:
            self._load()
            if key not in self._data:
                return default
            return _copy(self._data[key])

    def set(self, key: str, value: Any) -> Any:
        """Set ``key`` atomically within this process and persist it.

        The write is all-or-nothing: when persisting fails (a read-only
        directory, a full disk, a path that is a file) the in-memory value is
        rolled back, so memory and disk never disagree and a later successful
        write cannot silently commit the value that once failed.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("storage key must be a non-empty string")
        stored = _checked_copy(key, value)
        with self._lock:
            self._load()
            self._apply_locked(key, stored)
            return value

    def _apply_locked(self, key: str, value: Any) -> None:
        """Set ``key`` and persist, restoring the previous state on failure.

        Caller holds ``_lock``. The rollback matters because :meth:`set` used
        to keep the new value in ``_data`` after the disk write had already
        raised: the store then *looked* updated, the file was not, and the next
        unrelated write committed the failed change along with it.
        """
        had = key in self._data
        previous = self._data.get(key)
        self._data[key] = value
        try:
            self._persist()
        except BaseException:
            if had:
                self._data[key] = previous
            else:
                self._data.pop(key, None)
            raise

    def delete(self, key: str) -> bool:
        """Remove ``key`` and return whether it existed.

        Like :meth:`set`, the disk is written before the change is kept: a
        failed write restores the key instead of leaving memory ahead of disk.
        """
        with self._lock:
            self._load()
            if key not in self._data:
                return False
            previous = self._data.pop(key)
            try:
                self._persist()
            except BaseException:
                self._data[key] = previous
                raise
            return True

    def contains(self, key: str) -> bool:
        """Whether ``key`` is present."""
        with self._lock:
            self._load()
            return key in self._data

    def exists(self, key: str) -> bool:
        """Whether ``key`` is present (alias of :meth:`contains`).

        Added for discoverability — ``storage.exists(\"key\")`` reads more
        naturally than ``\"key\" in storage`` for newcomers, though both work::

            if storage.exists(\"token\"):
                ...

            if \"token\" in storage:
                ...
        """
        return self.contains(key)

    def clear(self) -> None:
        """Remove every entry and persist the empty store (all-or-nothing)."""
        with self._lock:
            self._load()
            previous = self._data
            self._data = {}
            try:
                self._persist()
            except BaseException:
                self._data = previous
                raise

    # -- atomic sequences --------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[Storage]:
        """Hold the store lock for a multi-step read-modify-write.

        Single operations are already atomic; this is for sequences that must
        not interleave with a job, a timer or an HTTP callback::

            with app.storage.transaction() as store:
                cart = store.get("cart", [])
                cart.append(item)
                store["cart"] = cart

        The block is serialised against other threads in this process and is
        all-or-nothing: the changes are written to disk once, when the
        outermost block finishes; if it raises, every change made inside it is
        rolled back in memory and the file is not touched.
        """
        with self._lock:
            self._load()
            snapshot = _copy(self._data)
            outer_dirty = self._tx_dirty
            self._tx_depth += 1
            try:
                yield self
            except BaseException:
                self._data = snapshot
                self._tx_dirty = outer_dirty
                raise
            finally:
                self._tx_depth -= 1
            if self._tx_depth == 0 and self._tx_dirty:
                self._tx_dirty = False
                try:
                    self._write_locked()
                except BaseException:
                    self._data = snapshot
                    raise

    def update(self, key: str, function: Callable[[Any], Any], default: Any = _UNSET) -> Any:
        """Atomically replace ``key`` with ``function(current_value)``.

        ``store.update("cart", lambda items: [*items, new], default=[])`` is
        race-free where ``store["cart"] = store.get("cart", []) + [new]`` is
        not: the read, the transformation and the persist happen under one
        lock without re-entry, so two concurrent updates cannot lose one
        another's change.

        Without ``default``, a missing key is an error: the function used to
        receive ``None`` and raise whatever it raised (``TypeError: unsupported
        operand type(s) for +: 'NoneType' and 'int'``) from deep inside the
        caller's own lambda. Pass ``default=None`` explicitly to keep the old
        behaviour.
        """
        with self._lock:
            self._load()
            if key in self._data:
                current = _copy(self._data[key])
            elif default is _UNSET:
                raise PyMobileError(
                    f"Storage key {key!r} does not exist",
                    hint=(
                        "pass default=… to update a missing key, e.g. "
                        'store.update("cart", add_item, default=[])'
                    ),
                )
            else:
                current = _copy(default)
            new_value = function(current)
            # Validate before touching memory: a non-JSON result must not end
            # up in the store and break every later write of any key.
            stored = _checked_copy(key, new_value)
            self._apply_locked(key, stored)
            return new_value

    def increment(self, key: str, amount: float = 1) -> float:
        """Atomically add ``amount`` to a numeric entry and return the result.

        Missing entries start from zero. A value that looks like a number is
        used as one: ``store.set("n", "5"); store.increment("n")`` is ``6``.
        Anything else non-numeric is treated as zero *and the old value is
        replaced* — the docstring always said "non-numeric entries start from
        zero", but silently turning the string ``"5"`` into ``1`` lost data that
        came back from JSON, so numeric strings are parsed first.
        """
        def bump(current: Any) -> float:
            if isinstance(current, bool):
                return amount
            if isinstance(current, (int, float)):
                return current + amount
            try:
                return float(str(current).strip()) + amount
            except (TypeError, ValueError):
                _log.warning(
                    "increment: %r held %r, which is not a number; counting from zero",
                    key,
                    current,
                )
                return amount

        return float(self.update(key, bump, default=0))

    def setdefault(self, key: str, default: Any) -> Any:
        """Return ``key``, storing and returning ``default`` when it is absent."""
        stored = _checked_copy(key, default)
        with self._lock:
            self._load()
            if key in self._data:
                return _copy(self._data[key])
            self._apply_locked(key, stored)
            return _copy(stored)

    def keys(self) -> list[str]:
        """All stored keys."""
        with self._lock:
            self._load()
            return list(self._data.keys())

    def items(self) -> list[tuple[str, Any]]:
        """All ``(key, value)`` pairs."""
        with self._lock:
            self._load()
            return [(key, _copy(value)) for key, value in self._data.items()]

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            self._load()
            if key not in self._data:
                raise KeyError(key)
            return _copy(self._data[key])

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: str) -> None:
        if not self.delete(key):
            # Keep KeyError to satisfy Mapping protocol and existing tests,
            # but include a helpful hint in the message.
            raise KeyError(
                f"storage key {key!r} not found — "
                "use storage.delete(key) to silently ignore when missing, "
                "or check 'key in storage' before deleting"
            )

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.contains(key)

    def __len__(self) -> int:
        with self._lock:
            self._load()
            return len(self._data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Storage path={self._path}>"


_IMMUTABLE = (str, int, float, bool, type(None))


def _copy(value: Any) -> Any:
    """Deep copy of a JSON value (cheap for the common scalar case)."""
    if isinstance(value, _IMMUTABLE):
        return value
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(v) for v in value]
    return json.loads(json.dumps(value))


def _checked_copy(key: str, value: Any) -> Any:
    """Validate ``value`` as JSON and return the form a restart would load."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        from ...errors import PyMobileError

        raise PyMobileError(
            f"Storage value for {key!r} is not JSON serializable: {exc}",
            hint="Use JSON types: str, int, float, bool, None, list, dict. "
            "E.g. list(my_set) instead of my_set.",
        ) from exc
    return json.loads(text)


def _fsync_dir(directory: Path) -> None:
    """Make the rename itself durable (POSIX; a no-op where unsupported)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
