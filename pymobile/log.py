"""Tiny logging helper.

PyMobile never configures the root logger on import: a library that hijacks
logging is painful to embed. :func:`configure` is called explicitly by the CLI,
and on-device by :meth:`pymobile.core.app.App.run`.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import ClassVar

__all__ = ["get_logger", "configure", "get_diagnostics", "LOGGER_NAME"]

LOGGER_NAME = "pymobile"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class _Formatter(logging.Formatter):
    """Compact, optionally coloured formatter."""

    _COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\033[2;37m",
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    _RESET = "\033[0m"

    def __init__(self, *, color: bool) -> None:
        super().__init__("%(message)s")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        """Format the record, redacting anything that looks like a secret."""
        message = super().format(record)
        prefix = record.levelname.lower()
        if record.levelno <= logging.INFO:
            prefix = "•"
        if self.color:
            tint = self._COLORS.get(record.levelno, "")
            return f"{tint}{prefix}{self._RESET} {message}"
        return f"{prefix} {message}"


class _FileFormatter(logging.Formatter):
    """Timestamped formatter for file loggers (no ANSI colour)."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")


def supports_color(stream: object = None) -> bool:
    """Return ``True`` when ANSI colours are safe to emit."""
    stream = stream or sys.stderr
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def configure(
    level: str | int = "info",
    *,
    color: bool | None = None,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Attach a stderr handler (and optionally a file handler) to ``pymobile``.

    When ``log_file`` is given, log records are also written there with
    timestamps, which is useful for diagnosing issues on a device where stderr
    is not easy to reach.
    """
    logger = logging.getLogger(LOGGER_NAME)
    resolved = _LEVELS.get(level, level) if isinstance(level, str) else level
    logger.setLevel(resolved)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(logging.StreamHandler(sys.stderr))
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(_FileFormatter())
        logger.addHandler(file_handler)
    return logger


def get_diagnostics() -> dict[str, object]:
    """Return a snapshot of runtime facts useful for support/debugging.

    Includes the Python version, platform, whether a file/console handler is
    attached and the configured level. Safe to call from anywhere.
    """
    logger = logging.getLogger(LOGGER_NAME)
    from . import __version__
    from .core.platform import current_platform

    level = logging.getLevelName(logger.level)
    return {
        "framework": "pymobile",
        "framework_version": __version__,
        "platform": str(current_platform()),
        "python": sys.version.split()[0],
        "level": level,
        "log_level": level.lower(),
        "handlers": [type(h).__name__ for h in logger.handlers],
    }


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced child of the framework logger."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
