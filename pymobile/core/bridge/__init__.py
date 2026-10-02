"""Platform bridge selection.

``get_bridge()`` returns the right implementation for the current runtime and
caches it. Tests and previews override it with :func:`set_bridge`.
"""

from __future__ import annotations

from ...log import get_logger
from ..platform import is_android
from .android import AndroidBridge
from .base import Bridge, BridgeCall, NotificationSpec
from .gui import GuiBridge, WebBridge
from .jni import JNIBridge
from .stub import StubBridge

__all__ = [
    "Bridge",
    "AndroidBridge",
    "BridgeCall",
    "NotificationSpec",
    "JNIBridge",
    "StubBridge",
    "GuiBridge",
    "WebBridge",
    "get_bridge",
    "active_bridge",
    "set_bridge",
    "reset_bridge",
]

_log = get_logger("bridge")
_active: Bridge | None = None


def get_bridge() -> Bridge:
    """Return the active bridge, creating the platform default on first use."""
    global _active
    if _active is None:
        if is_android():
            # Preferred: the runtime compiled into the APK. pyjnius is only a
            # fallback for apps embedded in a python-for-android bootstrap.
            candidate: Bridge = AndroidBridge()
            if not candidate.is_available():  # pragma: no cover - device-only path
                candidate = JNIBridge()
            if not candidate.is_available():  # pragma: no cover - device-only path
                _log.warning("Android detected but no native bridge is available; using the stub")
                candidate = StubBridge()
        else:
            candidate = StubBridge()
        _active = candidate
        _log.debug("bridge selected: %s", _active.name)
    return _active


def active_bridge() -> Bridge | None:
    """The bridge installed right now, or ``None`` when none was selected yet.

    Unlike :func:`get_bridge` this never *creates* one, so tooling and tests
    can save and restore the selection without a side effect.
    """
    return _active


def set_bridge(bridge: Bridge | None) -> None:
    """Install a specific bridge (pass ``None`` to fall back to detection)."""
    global _active
    _active = bridge


def reset_bridge() -> None:
    """Drop the cached bridge so the next call re-detects the platform."""
    set_bridge(None)
