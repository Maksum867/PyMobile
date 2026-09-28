"""On-device bridge backed by the ``_pymobile_android`` built-in module.

The module is injected by the JNI layer when the app runs inside an APK, so it
is imported lazily and its absence simply means "not on a device".

Compared with :class:`~pymobile.core.bridge.jni.JNIBridge` (which drives the
platform through pyjnius) this bridge speaks to purpose-built C functions, so
there is no reflection cost on the hot render path.
"""

from __future__ import annotations

import json
from typing import Any

from ...log import get_logger
from .base import Bridge, NotificationSpec

__all__ = ["AndroidBridge", "native_module"]

_log = get_logger("bridge.android")


def native_module() -> Any | None:
    """Return the injected native module, or ``None`` off-device."""
    try:
        import _pymobile_android  # type: ignore[import-not-found]
    except ImportError:
        return None
    return _pymobile_android


class AndroidBridge(Bridge):
    """Talks to Android through the compiled PyMobile runtime."""

    name = "android"

    #: The device renderer draws its default colours from the app theme.
    accepts_theme = True

    #: The tree goes to ``ViewBuilder.java``, which has one branch per built-in
    #: type and drops anything else. Apps are warned about unknown types (see
    #: ``App._check_widget_types``).
    native_widgets = True

    def __init__(self) -> None:
        self._native = native_module()
        self._granted: set[str] = set()

    def is_available(self) -> bool:
        return self._native is not None

    # -- ui ----------------------------------------------------------------
    def render(self, tree: dict[str, Any]) -> None:
        """Serialise the widget tree and hand it to the Java renderer."""
        if self._native is None:
            return
        self._native.render(json.dumps(tree, ensure_ascii=False))

    #: Must match ``Native.WAKE`` in Native.java.
    WAKE_PAYLOAD = '{"__wake__":true}'

    def wake(self) -> None:
        """Make the blocked event loop return a ``__wake__`` event.

        Called from background threads by :meth:`App.dispatch` so that the
        dispatched callbacks run on the event-loop thread.
        """
        if self._native is None:
            return
        native_wake = getattr(self._native, "wake", None)
        if native_wake is not None:
            native_wake()  # pushes "__wake__" straight into the event queue
        else:
            # Bridges built before 0.8.0: Native.render() turns this payload
            # into a "__wake__" event instead of drawing it.
            self._native.render(self.WAKE_PAYLOAD)

    def toast(self, message: str, long: bool = False) -> None:
        if self._native is not None:
            self._native.toast(message, bool(long))

    # -- notifications -----------------------------------------------------
    def ensure_channel(self, channel_id: str, channel_name: str, importance: int) -> None:
        """Create the notification channel with the exact configured values.

        The prebuilt JNI bridge may not expose ``ensure_channel`` (older
        ``libpymobile.so``). Falling back silently is safe: ``DeviceServices``
        creates the channel on demand inside ``notify`` anyway.
        """
        if self._native is not None and hasattr(self._native, "ensure_channel"):
            self._native.ensure_channel(channel_id, channel_name, int(importance))

    def notify(self, spec: NotificationSpec) -> None:
        if self._native is None:
            return
        native = self._native
        try:
            # New bridge: full signature (channel id/name, small icon).
            native.notify(
                spec.title,
                spec.body,
                spec.notification_id,
                spec.ongoing,
                spec.channel_id,
                spec.channel_name,
                spec.small_icon,
            )
        except TypeError:
            # Older libpymobile.so only accepts (title, body, id, ongoing).
            native.notify(
                spec.title,
                spec.body,
                spec.notification_id,
                spec.ongoing,
            )
        except Exception:
            # Never let a bridge hiccup break the app.
            _log.exception("notify failed")

    def cancel_notification(self, notification_id: int) -> None:
        if self._native is not None:
            self._native.cancel_notification(notification_id)

    # -- vibration ---------------------------------------------------------
    def vibrate(self, milliseconds: int, amplitude: int = -1) -> None:
        if self._native is None:
            return
        try:
            self._native.vibrate(int(milliseconds), int(amplitude))
        except TypeError:
            # Older libpymobile.so only accepts the duration.
            self._native.vibrate(int(milliseconds))

    def vibrate_pattern(self, pattern: list[int], repeat: int = -1) -> None:
        if self._native is not None:
            self._native.vibrate_pattern([int(v) for v in pattern], int(repeat))

    def cancel_vibration(self) -> None:
        if self._native is not None:
            self._native.cancel_vibration()

    # -- permissions -------------------------------------------------------
    def has_permission(self, permission: str) -> bool:
        if self._native is None:
            return False
        return bool(self._native.has_permission(permission))

    def request_permissions(self, permissions: list[str]) -> dict[str, bool]:
        """Show the system dialog for each permission and report the outcome.

        ``request_permission`` blocks until the user answers, so its return
        value is authoritative — re-reading ``has_permission`` afterwards used
        to race with the system and report a false "denied".
        """
        if self._native is None:
            return dict.fromkeys(permissions, False)
        results: dict[str, bool] = {}
        for permission in permissions:
            if self._native.has_permission(permission):
                results[permission] = True
                continue
            granted = bool(self._native.request_permission(permission))
            # Trust the dialog result, but fall back to a direct check in case
            # an older runtime returns None.
            results[permission] = granted or bool(self._native.has_permission(permission))
        return results

    # -- locale ------------------------------------------------------------
    def device_language(self) -> str:
        """Ask Android for the current locale, e.g. ``uk-UA``."""
        if self._native is None:
            return ""
        getter = getattr(self._native, "device_language", None)
        if getter is None:  # runtime older than the current framework
            return ""
        return str(getter() or "")

    # -- external ----------------------------------------------------------
    def open_url(self, url: str) -> bool:
        """Open ``url`` in the system browser on Android.

        Falls back to the native module when it exposes ``open_url``; otherwise
        returns ``False`` (no crash, the app keeps running).
        """
        if self._native is not None and hasattr(self._native, "open_url"):
            try:
                return bool(self._native.open_url(url))
            except Exception:
                return False
        return False

    # -- lifecycle ---------------------------------------------------------
    def finish_app(self) -> bool:
        """Ask the Activity to finish (root back button).

        Returns True when the platform acknowledged the request; preview
        bridges return False so the app stops instead.
        """
        native = self._native
        if native is not None and hasattr(native, "finish_app"):
            try:
                native.finish_app()
                return True
            except Exception:
                _log.exception("finish_app failed")
        return False

    # -- events ------------------------------------------------------------
    def next_event(self, timeout_ms: int = -1) -> tuple[str, str, str] | None:
        """Block until the UI thread reports an interaction."""
        if self._native is None:
            return None
        result = self._native.next_event(timeout_ms)
        if result is None:
            return None
        widget_id, kind, value = result
        return str(widget_id), str(kind), str(value)
