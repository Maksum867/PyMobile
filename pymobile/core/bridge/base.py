"""The contract between framework features and the Android platform.

Everything platform specific in PyMobile funnels through :class:`Bridge`.
Features (notifications, vibration, permissions, UI) never import ``jnius``
directly; they ask for a bridge. That single indirection is what makes the
framework testable on a desktop and portable to a different Android binding
later on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

__all__ = ["Bridge", "BridgeCall", "NotificationSpec"]


@dataclass(frozen=True, slots=True)
class BridgeCall:
    """A recorded platform call — the unit of assertion in tests."""

    name: str
    kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NotificationSpec:
    """Everything the platform needs to post one notification."""

    title: str
    body: str
    notification_id: int
    channel_id: str
    channel_name: str
    ongoing: bool = False
    small_icon: str | None = None


class Bridge(ABC):
    """Abstract Android platform surface.

    Implementations: :class:`~pymobile.core.bridge.jni.JNIBridge` on device and
    :class:`~pymobile.core.bridge.stub.StubBridge` everywhere else.
    """

    name: str = "abstract"

    #: Whether this bridge hands the serialised tree to the Java view layer
    #: (``ViewBuilder.java``). It is what lets :class:`~pymobile.core.app.App`
    #: warn about a widget type that renderer has no branch for: on the phone
    #: such a node is drawn as an empty view, with no error anywhere, while the
    #: desktop and browser previews print ``<BarChart>``. Previews leave this
    #: ``False`` — they can draw whatever they are handed.
    native_widgets: bool = False

    # -- lifecycle ---------------------------------------------------------
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this bridge can actually talk to Android right now."""

    # -- notifications -----------------------------------------------------
    @abstractmethod
    def ensure_channel(self, channel_id: str, channel_name: str, importance: int) -> None:
        """Create the notification channel (no-op below Android 8)."""

    @abstractmethod
    def notify(self, spec: NotificationSpec) -> None:
        """Post a notification."""

    @abstractmethod
    def cancel_notification(self, notification_id: int) -> None:
        """Dismiss a previously posted notification."""

    # -- vibration ---------------------------------------------------------
    @abstractmethod
    def vibrate(self, milliseconds: int, amplitude: int) -> None:
        """Vibrate once for ``milliseconds``."""

    @abstractmethod
    def vibrate_pattern(self, pattern: list[int], repeat: int) -> None:
        """Play an alternating off/on ``pattern`` in milliseconds."""

    @abstractmethod
    def cancel_vibration(self) -> None:
        """Stop any ongoing vibration."""

    # -- permissions -------------------------------------------------------
    @abstractmethod
    def has_permission(self, permission: str) -> bool:
        """Whether ``permission`` is currently granted."""

    @abstractmethod
    def request_permissions(self, permissions: list[str]) -> dict[str, bool]:
        """Show the runtime permission dialog and return the outcome."""

    # -- ui ----------------------------------------------------------------
    @abstractmethod
    def toast(self, message: str, long: bool = False) -> None:
        """Show a short platform toast."""

    @abstractmethod
    def render(self, tree: dict[str, Any]) -> None:
        """Hand a serialised widget tree to the native view layer."""

    # -- locale ------------------------------------------------------------
    def device_language(self) -> str:
        """The language tag the device is configured for, e.g. ``uk-UA``.

        Not abstract: a bridge that cannot answer returns an empty string and
        the framework falls back to the environment.
        """
        return ""

    # -- external ----------------------------------------------------------
    def open_url(self, url: str) -> bool:
        """Open ``url`` in an external viewer (browser).

        Returns ``True`` when handled; the default desktop implementation opens
        the system browser via ``webbrowser``.
        """
        import webbrowser

        webbrowser.open(url)
        return True
