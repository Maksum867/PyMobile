"""Deprecation warnings: one category, one helper, the dates in one place.

Removing something from a public API is a promise kept in two steps (see
*Versioning and deprecation policy* in the README): first the old spelling keeps
working and says so, with the name to use instead and the release that will drop
it; only later is it removed. Everything that announces such a step goes
through :func:`warn_deprecated`, so the wording is uniform and applications can
filter the whole family with one category::

    import warnings
    from pymobile import PyMobileDeprecationWarning

    warnings.filterwarnings("error", category=PyMobileDeprecationWarning)  # CI: no old names

The category derives from :class:`DeprecationWarning`, which Python (and
pytest) show only for code you run yourself — run tests with ``-W error`` or
``-W default::DeprecationWarning`` to see them all.
"""

from __future__ import annotations

import warnings

__all__ = [
    "ALIASES_DEPRECATED_IN",
    "ALIASES_REMOVED_IN",
    "PyMobileDeprecationWarning",
    "warn_deprecated",
]

#: Release that first warns about the duplicate keyword names (``max=`` for
#: ``maximum=``, ``on_change=`` for ``on_select=`` …).
ALIASES_DEPRECATED_IN = "0.9.0"
#: Release that drops them. The 1.0 API is frozen, so an alias that is still
#: there at 1.0 would stay for good; at least one minor release warns first.
ALIASES_REMOVED_IN = "1.0.0"


class PyMobileDeprecationWarning(DeprecationWarning):
    """A PyMobile API that still works but is scheduled for removal."""


def warn_deprecated(
    old: str,
    new: str | None = None,
    *,
    since: str,
    removal: str,
    stacklevel: int = 1,
) -> None:
    """Warn that ``old`` is deprecated in favour of ``new``.

    ``stacklevel`` counts from the *caller* of this function: ``1`` blames the
    line that called :func:`warn_deprecated`, ``2`` the caller of that, and so
    on — pick the level that lands on the application's own code, which is what
    Python's default filters key on.
    """
    message = f"{old} is deprecated since PyMobile {since} and will be removed in {removal}"
    if new:
        message += f"; use {new} instead"
    warnings.warn(message + ".", PyMobileDeprecationWarning, stacklevel=stacklevel + 1)
