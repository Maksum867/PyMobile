"""Android runtime permissions.

Two responsibilities, kept apart on purpose:

* :class:`Permission` — the catalogue of well-known permission strings, so app
  code never hand-types ``"android.permission.VIBRATE"``;
* :class:`PermissionManager` — request/check logic on top of a bridge.

Permissions declared by a project also end up in the generated manifest; see
:mod:`pymobile.compiler.manifest`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from ...errors import PermissionError_
from ...log import get_logger
from ..bridge import Bridge, get_bridge

__all__ = ["Permission", "PermissionManager", "normalize"]

_log = get_logger("permissions")

_PREFIX = "android.permission."


class Permission(str, Enum):
    """Frequently used Android permissions."""

    INTERNET = "android.permission.INTERNET"
    ACCESS_NETWORK_STATE = "android.permission.ACCESS_NETWORK_STATE"
    VIBRATE = "android.permission.VIBRATE"
    POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"
    CAMERA = "android.permission.CAMERA"
    RECORD_AUDIO = "android.permission.RECORD_AUDIO"
    ACCESS_FINE_LOCATION = "android.permission.ACCESS_FINE_LOCATION"
    ACCESS_COARSE_LOCATION = "android.permission.ACCESS_COARSE_LOCATION"
    BLUETOOTH = "android.permission.BLUETOOTH"
    BLUETOOTH_ADMIN = "android.permission.BLUETOOTH_ADMIN"
    BLUETOOTH_SCAN = "android.permission.BLUETOOTH_SCAN"
    BLUETOOTH_CONNECT = "android.permission.BLUETOOTH_CONNECT"
    READ_EXTERNAL_STORAGE = "android.permission.READ_EXTERNAL_STORAGE"
    WRITE_EXTERNAL_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE"
    READ_MEDIA_IMAGES = "android.permission.READ_MEDIA_IMAGES"
    WAKE_LOCK = "android.permission.WAKE_LOCK"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def short(self) -> str:
        """Name without the ``android.permission.`` prefix."""
        return self.value.removeprefix(_PREFIX)

    @property
    def runtime(self) -> bool:
        """Whether Android asks the user at runtime (vs. install-time grant)."""
        return self in _RUNTIME_PERMISSIONS


_RUNTIME_PERMISSIONS = frozenset(
    {
        Permission.POST_NOTIFICATIONS,
        Permission.CAMERA,
        Permission.RECORD_AUDIO,
        Permission.ACCESS_FINE_LOCATION,
        Permission.ACCESS_COARSE_LOCATION,
        Permission.BLUETOOTH_SCAN,
        Permission.BLUETOOTH_CONNECT,
        Permission.READ_EXTERNAL_STORAGE,
        Permission.WRITE_EXTERNAL_STORAGE,
        Permission.READ_MEDIA_IMAGES,
    }
)


def normalize(permission: str | Permission) -> str:
    """Accept ``"CAMERA"``, ``"android.permission.CAMERA"`` or the enum member."""
    if isinstance(permission, Permission):
        return permission.value
    if not isinstance(permission, str):
        raise TypeError(
            f"permission must be a string or Permission enum, got {type(permission).__name__!r}; "
            f"write permissions.has('CAMERA') or permissions.has(Permission.CAMERA)"
        )
    text = permission.strip()
    if not text:
        raise ValueError("permission name must not be empty")
    if "." in text:
        return text
    return f"{_PREFIX}{text.upper()}"


class PermissionManager:
    """Check and request runtime permissions."""

    __slots__ = ("_bridge",)

    def __init__(self, bridge: Bridge | None = None) -> None:
        self._bridge = bridge or get_bridge()

    def has(self, permission: str | Permission) -> bool:
        """Whether a single permission is granted."""
        return self._bridge.has_permission(normalize(permission))

    def missing(self, permissions: Iterable[str | Permission]) -> list[str]:
        """Subset of ``permissions`` that is not granted yet."""
        return [normalize(p) for p in permissions if not self.has(p)]

    def request(self, *permissions: str | Permission) -> dict[str, bool]:
        """Request permissions, skipping the ones already granted.

        A permission missing from ``AndroidManifest.xml`` is refused by the
        system without even showing a dialog, which looks like a silent
        failure, so that case is logged explicitly.
        """
        # Allow both request("CAMERA", "LOCATION") and request(["CAMERA", "LOCATION"])
        if len(permissions) == 1 and isinstance(permissions[0], (list, tuple, set)):
            permissions = tuple(permissions[0])
        wanted = [normalize(p) for p in permissions]
        if not wanted:
            return {}
        pending = [name for name in wanted if not self._bridge.has_permission(name)]
        results = {name: True for name in wanted if name not in pending}
        if pending:
            results.update(self._bridge.request_permissions(pending))
        denied = [name for name, ok in results.items() if not ok]
        if denied:
            _log.warning(
                "denied: %s — if no dialog appeared, add them to `permissions` "
                "in pymobile.toml and rebuild",
                ", ".join(denied),
            )
        return results

    def require(self, *permissions: str | Permission) -> None:
        """Request permissions and raise :class:`PermissionError_` if any is denied."""
        results = self.request(*permissions)
        for name, granted in results.items():
            if not granted:
                raise PermissionError_(
                    name,
                    hint="Declare it in the project config and grant it in the system dialog.",
                )

    @staticmethod
    def manifest_entries(permissions: Sequence[str | Permission]) -> list[str]:
        """De-duplicated, sorted, fully qualified names for the manifest."""
        return sorted({normalize(p) for p in permissions})
