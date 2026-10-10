"""Framework-wide error hierarchy.

Every exception raised by PyMobile inherits from :class:`PyMobileError`, so an
application (or a host tool) can catch the whole framework with one clause.
Errors carry an optional ``hint`` describing how to fix the problem — the CLI
prints it right under the message.
"""

from __future__ import annotations

__all__ = [
    "PyMobileError",
    "ConfigError",
    "BridgeError",
    "PlatformError",
    "PermissionError_",
    "NetworkError",
    "ResourceError",
    "ModelStoreError",
    "WidgetNotFoundError",
    "WidgetTypeError",
    "WidgetParentError",
]


class PyMobileError(Exception):
    """Base class for all PyMobile errors."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(PyMobileError):
    """The project configuration is missing or invalid."""


class BridgeError(PyMobileError):
    """A call into the Android platform layer failed."""


class PlatformError(PyMobileError):
    """The requested feature is not available on the current platform."""


class PermissionError_(PyMobileError):
    """A runtime Android permission was denied by the user."""

    def __init__(self, permission: str, *, hint: str | None = None) -> None:
        super().__init__(f"Permission denied: {permission}", hint=hint)
        self.permission = permission


class NetworkError(PyMobileError):
    """An HTTP request could not be completed."""


class ResourceError(PyMobileError):
    """A packaged resource (template, icon) could not be read."""


class ModelStoreError(PyMobileError):
    """Typed model data could not be read, migrated or reconstructed safely."""


class WidgetNotFoundError(PyMobileError, LookupError):
    """``get()`` found no widget with the requested id.

    Also a :class:`LookupError`, so ``except LookupError`` catches it.
    """

    def __init__(self, widget_id: str, *, hint: str | None = None) -> None:
        super().__init__(f"no widget with id {widget_id!r}", hint=hint)
        self.widget_id = widget_id


class WidgetTypeError(PyMobileError, TypeError):
    """``find()``/``get()`` found the id, but the widget has another type.

    Also a :class:`TypeError`, so ``except TypeError`` catches it.
    """

    def __init__(self, widget_id: str, expected: type, actual: type) -> None:
        super().__init__(
            f"widget {widget_id!r} is a {actual.__name__}, not a {expected.__name__}",
            hint=(
                f"Pass the class the widget really is (find({widget_id!r}, {actual.__name__})), "
                "or give the widget you meant a different id."
            ),
        )
        self.widget_id = widget_id
        self.expected = expected
        self.actual = actual


class WidgetParentError(PyMobileError, ValueError):
    """``Container.add()`` was given a widget that already has a parent.

    Also a :class:`ValueError`, so ``except ValueError`` catches it — like the
    other misuse errors of ``add()`` (a container inside itself, a cycle) that
    have always been ``ValueError`` — while ``except PyMobileError`` still gets
    the ``hint`` that explains how a widget usually ends up in two places.
    """

    def __init__(self, widget_id: str, *, hint: str | None = None) -> None:
        super().__init__(f"widget {widget_id!r} already has a parent", hint=hint)
        self.widget_id = widget_id
