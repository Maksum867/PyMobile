"""Incremental build cache.

Compilation speed comes mostly from *not* redoing work. The cache stores a
fingerprint of the inputs (config + every source file + icon) next to the build
output; when nothing changed and the artifact still exists, the build is
skipped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger

__all__ = ["BuildCache", "fingerprint_files"]

_log = get_logger("compiler.cache")

CACHE_FILENAME = ".pymobile-cache.json"
_CACHE_VERSION = 1

#: Files up to this size are fingerprinted by content hash; anything larger is
#: fingerprinted by size + mtime so the cache stays fast on big binaries.
#: 8 MB comfortably covers every source file and icon in a PyMobile project.
_HASH_LIMIT = 8 * 1024 * 1024


def fingerprint_files(paths: Iterable[Path]) -> str:
    """Hash file paths, sizes and enough of each file's identity to catch edits.

    Files below :data:`_HASH_LIMIT` are hashed by **content**, so editing a file
    twice within the same clock second can no longer produce a stale "up to
    date" build. Larger files fall back to size + nanosecond mtime to avoid
    reading megabytes into memory on every build.
    """
    digest = hashlib.blake2b(digest_size=16)
    for path in sorted(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        if stat.st_size <= _HASH_LIMIT:
            try:
                digest.update(hashlib.blake2b(path.read_bytes(), digest_size=16).digest())
            except OSError:  # vanished between stat and read — use mtime
                digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
        else:
            digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
    return digest.hexdigest()


@dataclass(slots=True)
class BuildCache:
    """Reads and writes the build fingerprint file.

    Besides the fingerprint, an entry records **metadata** about the artifact —
    the build mode and the signing identity. Without it a cache hit could only
    guess: a reused native APK was reported as ``native=False`` (``BuildResult``
    default), so a release command that hit the cache described its output as a
    structural preview package.
    """

    directory: Path

    @property
    def path(self) -> Path:
        """Location of the cache file."""
        return self.directory / CACHE_FILENAME

    def load(self) -> dict[str, str]:
        """Return the stored entry, or an empty dict when absent/invalid."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            return {}
        return {str(k): str(v) for k, v in data.items() if k != "version"}

    def save(self, fingerprint: str, artifact: Path, **metadata: str) -> None:
        """Record the fingerprint and metadata of a successful build."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": _CACHE_VERSION,  # int on purpose: it is a schema number
            "fingerprint": fingerprint,
            "artifact": str(artifact),
            **{key: str(value) for key, value in metadata.items()},
        }
        try:
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk issues
            _log.debug("could not write build cache: %s", exc)

    def entry(self, fingerprint: str) -> dict[str, str] | None:
        """The stored entry when it matches ``fingerprint`` and the file is there."""
        data = self.load()
        if data.get("fingerprint") != fingerprint:
            return None
        artifact = Path(data.get("artifact", ""))
        if not artifact.exists():
            return None
        return {**data, "artifact": str(artifact)}

    def is_fresh(self, fingerprint: str) -> Path | None:
        """Return the cached artifact when it matches ``fingerprint``."""
        found = self.entry(fingerprint)
        return Path(found["artifact"]) if found is not None else None

    def clear(self) -> None:
        """Remove the cache file."""
        self.path.unlink(missing_ok=True)
