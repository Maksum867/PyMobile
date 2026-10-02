"""Source collection.

The fastest build is the one that copies the fewest files, so collection is a
first-class stage: it applies exclude globs, skips caches and reports exactly
what will be packaged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError
from ..log import get_logger

__all__ = ["SourceSet", "collect_sources"]

_log = get_logger("compiler.collector")

_ALWAYS_EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)


@dataclass(frozen=True, slots=True)
class SourceSet:
    """The files that will go into the APK."""

    root: Path
    files: tuple[Path, ...]
    entrypoint: Path

    @property
    def total_bytes(self) -> int:
        """Combined size of all collected files."""
        return sum(path.stat().st_size for path in self.files)

    @property
    def count(self) -> int:
        """Number of collected files."""
        return len(self.files)

    def relative(self) -> Iterator[Path]:
        """Paths relative to the source root."""
        for path in self.files:
            yield path.relative_to(self.root)


def _glob_to_regex(glob: str) -> str:
    """Turn a gitignore glob body into regex source (without anchors).

    ``*`` matches within a path segment, ``?`` one character, ``[...]`` a
    character class and ``**`` matches across directories.
    """
    out: list[str] = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            while j < n and glob[j] != "]":
                j += 1
            if j < n:
                out.append(glob[i : j + 1])
                i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _is_excluded(relative: Path, patterns: Sequence[str]) -> bool:
    """Whether ``relative`` matches any gitignore-style exclude glob.

    Semantics follow gitignore so project-level patterns behave the way users
    expect:

    * a leading ``/`` anchors the pattern to the source root;
    * a pattern with a ``/`` in the middle is anchored to the source root
      (``build/**`` only excludes a top-level ``build/``);
    * a pattern with no ``/`` matches the file name (or any path suffix) at
      any depth, so ``test_*.py`` catches ``pkg/test_foo.py`` too;
    * a leading ``**/`` unanchors the remainder, so ``**/__pycache__/**``
      matches a root-level ``__pycache__/`` as well as nested ones;
    * a trailing ``/`` means "this directory and everything under it".
    """
    text = relative.as_posix()
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        dir_only = pattern.endswith("/")
        body = pattern.rstrip("/")
        if not body:
            continue

        anywhere = body.startswith("**/")
        anchored = body.startswith("/")
        if anywhere:
            body = body[3:]
        elif anchored:
            body = body[1:]

        core = _glob_to_regex(body)
        if not core:
            continue
        if dir_only:
            core += "(?:/.*)?"

        if anywhere:
            # The remainder may begin at any path segment.
            rx = re.compile(rf"(?:^|/){core}")
        elif "/" in body or anchored:
            # Anchored to the source root.
            rx = re.compile(rf"^{core}$")
        else:
            # No slash: match the file name (or a suffix) at any depth.
            rx = re.compile(rf"(?:^|/){core}$")
        if rx.search(text) is not None:
            return True
    return False


def collect_sources(
    source_dir: Path,
    entrypoint: Path,
    *,
    exclude: Sequence[str] = (),
    include_suffixes: Iterable[str] | None = None,
) -> SourceSet:
    """Walk ``source_dir`` and return the set of files to package.

    ``include_suffixes`` defaults to Python sources plus common asset types.
    """
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ConfigError(
            f"Source directory does not exist: {source_dir}",
            hint="Check `source_dir` in your project configuration.",
        )
    if not entrypoint.exists():
        raise ConfigError(
            f"Entry point not found: {entrypoint}",
            hint="Create the file or point `entrypoint` at an existing module.",
        )

    suffixes = (
        {s.lower() for s in include_suffixes}
        if include_suffixes is not None
        else {".py", ".json", ".txt", ".toml", ".png", ".jpg", ".jpeg", ".webp", ".ttf", ".otf"}
    )

    collected: list[Path] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        if any(part in _ALWAYS_EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if _is_excluded(relative, exclude):
            continue
        collected.append(path)

    if entrypoint not in collected:
        collected.insert(0, entrypoint)

    result = SourceSet(root=source_dir, files=tuple(collected), entrypoint=entrypoint)
    _log.debug("collected %d files (%d bytes)", result.count, result.total_bytes)
    return result
