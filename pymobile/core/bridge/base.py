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
from pathlib import Path
from typing import Any

__all__ = [
    "Bridge",
    "BridgeCall",
    "NotificationSpec",
    "PickedFile",
    "OPENABLE_SCHEMES",
    "pick_filters",
]


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


@dataclass(frozen=True, slots=True)
class PickedFile:
    """One file the user chose in the system picker, or picked with the camera.

    ``path`` is an absolute path the app can open and read; ``name`` is the
    file's display name, ``size`` its length in bytes (``-1`` when the platform
    could not report it) and ``mime`` its content type, which may be guessed
    from the extension when the provider does not send one.
    """

    path: str
    name: str = ""
    size: int = -1
    mime: str = ""

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError(
                "PickedFile needs a path; check pick_file() for None before reading one"
            )
        # A path alone is enough: the rest is derived, so a stub bridge (and a
        # caller building one by hand) does not have to repeat what the path
        # already says.
        if not self.name:
            object.__setattr__(self, "name", Path(self.path).name)
        if not self.mime:
            import mimetypes

            guessed, _ = mimetypes.guess_type(self.name)
            if guessed:
                object.__setattr__(self, "mime", guessed)


#: Android intent categories, spelled as the platform does.
_CATEGORY_OPENABLE = "android.intent.category.OPENABLE"
_INTENT_GET_CONTENT = "android.intent.action.GET_CONTENT"
_TYPE_ANY = "*/*"


def pick_filters(accept: str) -> list[str]:
    """Turn ``"audio/*"``, ``"image/png,image/jpeg"`` or ``".wav"`` into MIME types.

    Android filters by MIME type, not by extension, and ``GET_CONTENT`` takes
    either one type or the wildcard. Accepting an extension the way a browser
    does is friendlier than forcing the caller to know the mapping, so an
    extension is translated here (and an unknown one becomes ``*/*`` rather
    than silently matching nothing).
    """
    if not isinstance(accept, str):
        raise TypeError("accept must be a string such as 'audio/*'")
    text = accept.strip()
    if not text:
        return [_TYPE_ANY]
    extension_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }
    kinds: list[str] = []
    for part in text.replace(";", ",").split(","):
        item = part.strip().lower()
        if not item:
            continue
        if item.startswith("."):
            mapped = extension_map.get(item)
            if mapped is None:
                _log_hint = (
                    f"unknown extension {item!r} in accept; use a MIME type (e.g. 'audio/*')"
                )
                raise ValueError(_log_hint)
            kinds.append(mapped)
        elif "/" in item:
            kinds.append(item)
        else:
            raise ValueError(
                f"invalid accept {accept!r}; use MIME types ('audio/*', 'image/png') "
                "or extensions ('.wav')"
            )
    if _TYPE_ANY in kinds:
        return [_TYPE_ANY]
    # ``accept`` may be several types; Android matches them with EXTRA_MIME_TYPES
    # while the intent's own type stays the wildcard that covers them all.
    return kinds or [_TYPE_ANY]


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

    # -- files -------------------------------------------------------------
    def pick_file(self, accept: str = "*/*", *, multiple: bool = False) -> list[PickedFile]:
        """Let the user choose one or more files; empty when they cancel.

        Not abstract: a bridge with no picker (a desktop preview) returns an
        empty list, so a caller writes the same code on every platform and
        simply gets "the user picked nothing".
        """
        return []

    def pick_image(self) -> list[PickedFile]:
        """Let the user photograph something or pick a picture; empty on cancel."""
        return []

    # -- clipboard ---------------------------------------------------------
    def copy_to_clipboard(self, text: str) -> bool:
        """Put ``text`` on the system clipboard; ``False`` when unsupported."""
        return False

    def clipboard_text(self) -> str:
        """Whatever the clipboard holds, or an empty string."""
        return ""

    # -- share sheet -------------------------------------------------------
    def share(self, text: str = "", url: str = "", subject: str = "") -> bool:
        """Open the system share sheet; ``False`` when nothing could share it."""
        return False

    # -- secrets -----------------------------------------------------------
    def store_secret(self, name: str, value: str) -> bool:
        """Store ``value`` under ``name`` behind the platform keystore.

        Not abstract: a bridge with no keystore (a desktop preview) returns
        ``False`` and the caller decides what to do. On Android the value is
        encrypted with a key the keystore holds, so it is not readable from a
        copy of the app's data directory.
        """
        raise NotImplementedError(
            f"the {self.name} bridge cannot store secrets; "
            "use app.save_secret() on a device or pass a custom bridge"
        )

    def load_secret(self, name: str) -> str | None:
        """The secret stored under ``name``, or ``None`` when there is none."""
        raise NotImplementedError(
            f"the {self.name} bridge cannot read secrets; "
            "use app.load_secret() on a device or pass a custom bridge"
        )

    def delete_secret(self, name: str) -> bool:
        """Forget ``name``; ``False`` when it did not exist or is unsupported."""
        raise NotImplementedError(
            f"the {self.name} bridge cannot delete secrets; "
            "use app.delete_secret() on a device or pass a custom bridge"
        )

    # -- external ----------------------------------------------------------
    def open_url(self, url: str) -> bool:
        """Open ``url`` in an external viewer (browser).

        Returns ``True`` when handled; the default desktop implementation opens
        the system browser via ``webbrowser``. Only ``http``, ``https``,
        ``mailto`` and ``tel`` are opened: the URL often comes from server data,
        and a ``file:`` or ``intent:`` link there must not reach the platform.
        """
        _check_openable_url(url)
        import webbrowser

        webbrowser.open(url)
        return True


#: Schemes a link from app data may open. ``tel:`` and ``sms:`` dial or compose;
#: everything else (``file:``, ``intent:``, ``javascript:``, custom schemes) is
#: refused, because ``Intent.parseUri`` on a hostile string can reach parts of
#: the system the app never meant to expose.
OPENABLE_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel"})


def _check_openable_url(url: str) -> str:
    """Validate ``url`` for :meth:`Bridge.open_url`; returns the stripped URL."""
    if not isinstance(url, str):
        raise TypeError("url must be a string")
    text = url.strip()
    if not text:
        raise ValueError("url must not be empty")
    scheme = text.split(":", 1)[0].strip().casefold() if ":" in text else ""
    # A relative path has no scheme and cannot open anything: refuse it here
    # rather than letting the platform guess.
    if scheme not in OPENABLE_SCHEMES:
        raise ValueError(
            f"refusing to open {text!r}: allowed schemes are {', '.join(sorted(OPENABLE_SCHEMES))}"
        )
    return text
