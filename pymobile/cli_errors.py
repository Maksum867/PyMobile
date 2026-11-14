"""Turning an exception from the app's own code into a message with a location.

The CLI runs the developer's entry point, so the most useful part of a
traceback is the frame that belongs to *their* file. Keeping this separate from
the command dispatch also keeps :mod:`pymobile.cli` small enough for the
file-size guard in the test suite.
"""

from __future__ import annotations

from pathlib import Path

from .errors import PyMobileError


def _user_code_location(exc: BaseException, entry: Path) -> str | None:
    """``main.py:12`` for the innermost frame that belongs to the app.

    A traceback from user code spends most of its frames inside the framework;
    the one that answers "where?" is the deepest frame whose file is the app's
    own. Syntax errors carry their own position.
    """
    if isinstance(exc, SyntaxError) and exc.filename:
        line = exc.lineno or 0
        return f"{Path(exc.filename).name}:{line}" if line else Path(exc.filename).name
    location: str | None = None
    traceback_ = exc.__traceback__
    while traceback_ is not None:
        frame = traceback_.tb_frame
        filename = frame.f_code.co_filename
        if not filename.startswith("<"):
            path = Path(filename)
            if entry.parent in path.parents or path == entry:
                location = f"{path.name}:{traceback_.tb_lineno}"
        traceback_ = traceback_.tb_next
    return location


def _describe_user_error(exc: BaseException, entry: Path) -> PyMobileError:
    """Turn a raw exception from the app's code into a message with a location."""
    location = _user_code_location(exc, entry)
    where = f" ({location})" if location else ""
    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    hint = (
        "This is a bug in your code, not in the framework: read the line named "
        "above, or re-run with --verbose for the full traceback."
    )
    if isinstance(exc, NameError) and getattr(exc, "name", None):
        hint = (
            f"{exc.name!r} is not defined at that point: check the spelling, and "
            "that the import or assignment runs before this line."
        )
    elif isinstance(exc, AttributeError):
        hint = (
            "That attribute does not exist. A common cause is a missing "
            "super().__init__() in the screen's __init__."
        )
    elif isinstance(exc, SyntaxError):
        hint = "Python could not parse the file; the caret in --verbose points at it."
    error = PyMobileError(f"{detail}{where}", hint=hint)
    # The line the message already names, kept as an attribute so a caller that
    # formats findings of its own (``pymobile check``) does not have to parse
    # the text back out.
    error.line = int(location.rsplit(":", 1)[-1]) if location else None  # type: ignore[attr-defined]
    return error
