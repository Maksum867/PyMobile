"""Local notifications.

Thin, predictable wrapper over the bridge: channel handling and id allocation
live here so application code just calls :meth:`Notifications.notify`.
"""

from __future__ import annotations

from itertools import count

from ...log import get_logger
from ..bridge import Bridge, NotificationSpec, get_bridge

__all__ = ["Notifications", "IMPORTANCE_DEFAULT", "IMPORTANCE_HIGH", "IMPORTANCE_LOW"]

_log = get_logger("notifications")

IMPORTANCE_LOW = 2
IMPORTANCE_DEFAULT = 3
IMPORTANCE_HIGH = 4


class Notifications:
    """Post and cancel local notifications."""

    __slots__ = ("_bridge", "_channel_id", "_channel_name", "_importance", "_ids", "_ready")

    def __init__(
        self,
        bridge: Bridge | None = None,
        *,
        channel_id: str = "pymobile.default",
        channel_name: str = "General",
        importance: int = IMPORTANCE_DEFAULT,
    ) -> None:
        self._bridge = bridge or get_bridge()
        self._channel_id = channel_id
        self._channel_name = channel_name
        self._importance = importance
        self._ids = count(1)
        self._ready = False

    @property
    def channel_id(self) -> str:
        """Identifier of the channel notifications are posted to."""
        return self._channel_id

    def _ensure_channel(self) -> None:
        """Create the channel once per process."""
        if not self._ready:
            self._bridge.ensure_channel(self._channel_id, self._channel_name, self._importance)
            self._ready = True

    def notify(
        self,
        title: str,
        body: str = "",
        *,
        notification_id: int | None = None,
        ongoing: bool = False,
        icon: str | None = None,
    ) -> int:
        """Post a notification and return its id (usable with :meth:`cancel`).

        ``title`` is required (Android needs it for the notification header),
        ``body`` may be empty — a title-only notification is valid.
        """
        if not isinstance(title, str):
            raise TypeError(
                f"notification title must be a string, got {type(title).__name__!r}; "
                f'write notify("Title", "Body") not notify({title!r}, ...)'
            )
        if not title:
            raise ValueError(
                "notification title must not be empty; "
                'pass e.g. notify("Update", "Download complete")'
            )
        if not isinstance(body, str):
            raise TypeError(f"notification body must be a string, got {type(body).__name__!r}")
        if notification_id is not None and not isinstance(notification_id, int):
            raise TypeError(
                f"notification_id must be an int, got {type(notification_id).__name__!r}; "
                f"pass e.g. notification_id=1"
            )
        self._ensure_channel()
        resolved_id = notification_id if notification_id is not None else next(self._ids)
        self._bridge.notify(
            NotificationSpec(
                title=title,
                body=body,
                notification_id=resolved_id,
                channel_id=self._channel_id,
                channel_name=self._channel_name,
                ongoing=ongoing,
                small_icon=icon,
            )
        )
        _log.debug("posted notification %s: %s", resolved_id, title)
        return resolved_id

    def cancel(self, notification_id: int) -> None:
        """Dismiss a notification by id."""
        self._bridge.cancel_notification(notification_id)
