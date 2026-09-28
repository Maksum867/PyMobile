"""On-device implementation backed by ``pyjnius``.

Imports of ``jnius`` are deliberately lazy and wrapped: the module must remain
importable on a developer machine where no JVM exists. Java classes are
resolved once and cached, because ``autoclass`` lookups are not free.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import cached_property
from typing import Any, ClassVar

from ...errors import BridgeError
from ...log import get_logger
from .base import Bridge, NotificationSpec

__all__ = ["JNIBridge"]

_log = get_logger("bridge.jni")

# android.app.NotificationManager.IMPORTANCE_DEFAULT
IMPORTANCE_DEFAULT = 3
# android.os.VibrationEffect.DEFAULT_AMPLITUDE
DEFAULT_AMPLITUDE = -1


class JNIBridge(Bridge):
    """Talks to the Android runtime through JNI."""

    name = "jni"

    #: The tree is destined for the same Java renderer as the compiled bridge,
    #: so an unknown widget type is reported here too.
    native_widgets = True

    def __init__(self) -> None:
        self._classes: dict[str, Any] = {}
        # Java owns a Runnable only after its run() method begins. Keep a
        # strong Python reference until then: otherwise pyjnius may expose a
        # freed PythonJavaClass to the Android main queue.
        self._ui_runnables: dict[int, Any] = {}
        self._ui_runnables_lock = threading.Lock()
        # Android's Activity.requestPermissions() is asynchronous: the dialog
        # opens and control returns immediately, so the result arrives later
        # via onRequestPermissionsResult on a Java callback. We bridge that
        # back to a Python Future that the framework awaits, otherwise every
        # permission request would synchronously report "not granted" because
        # the user hasn't had time to tap anything yet.
        self._pending_permissions: dict[int, PermissionFuture] = {}
        self._pending_permissions_lock = threading.Lock()
        self._permission_callback_attached = False

    # -- plumbing ----------------------------------------------------------
    def _autoclass(self, path: str) -> Any:
        """Resolve and cache a Java class."""
        cached = self._classes.get(path)
        if cached is not None:
            return cached
        try:
            from jnius import autoclass  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - device-only path
            raise BridgeError(
                "pyjnius is not available in this runtime",
                hint="Install the 'android' extra, or run on a real device/emulator.",
            ) from exc
        klass = autoclass(path)
        self._classes[path] = klass
        return klass

    @cached_property
    def _activity(self) -> Any:  # pragma: no cover - device-only path
        """The current Android Activity."""
        try:
            return self._autoclass("org.kivy.android.PythonActivity").mActivity
        except Exception:
            return self._autoclass("org.kivy.android.PythonService").mService

    @cached_property
    def _context(self) -> Any:  # pragma: no cover - device-only path
        return self._activity.getApplicationContext()

    def is_available(self) -> bool:
        try:
            import jnius  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False
        return True

    # -- notifications -----------------------------------------------------
    def ensure_channel(  # pragma: no cover - device-only path
        self, channel_id: str, channel_name: str, importance: int = IMPORTANCE_DEFAULT
    ) -> None:
        build_version = self._autoclass("android.os.Build$VERSION")
        if build_version.SDK_INT < 26:  # channels landed in Oreo
            return
        channel_cls = self._autoclass("android.app.NotificationChannel")
        manager = self._context.getSystemService(
            self._autoclass("android.content.Context").NOTIFICATION_SERVICE
        )
        channel = channel_cls(channel_id, channel_name, importance)
        manager.createNotificationChannel(channel)

    def notify(self, spec: NotificationSpec) -> None:  # pragma: no cover - device-only path
        self.ensure_channel(spec.channel_id, spec.channel_name, IMPORTANCE_DEFAULT)
        builder_cls = self._autoclass("android.app.Notification$Builder")
        build_version = self._autoclass("android.os.Build$VERSION")
        context = self._context
        builder = (
            builder_cls(context, spec.channel_id)
            if build_version.SDK_INT >= 26
            else builder_cls(context)
        )
        builder.setContentTitle(spec.title)
        builder.setContentText(spec.body)
        builder.setOngoing(spec.ongoing)
        builder.setAutoCancel(not spec.ongoing)
        builder.setSmallIcon(self._icon_id(spec.small_icon))
        manager = context.getSystemService(
            self._autoclass("android.content.Context").NOTIFICATION_SERVICE
        )
        manager.notify(spec.notification_id, builder.build())

    def _icon_id(self, name: str | None) -> int:  # pragma: no cover - device-only path
        """Resolve a drawable name to a resource id, falling back to the app icon."""
        resources = self._context.getResources()
        package = self._context.getPackageName()
        for candidate, kind in ((name, "drawable"), ("icon", "mipmap"), ("icon", "drawable")):
            if not candidate:
                continue
            found = resources.getIdentifier(candidate, kind, package)
            if found:
                return int(found)
        return int(self._autoclass("android.R$drawable").ic_dialog_info)

    def cancel_notification(self, notification_id: int) -> None:  # pragma: no cover
        manager = self._context.getSystemService(
            self._autoclass("android.content.Context").NOTIFICATION_SERVICE
        )
        manager.cancel(notification_id)

    # -- vibration ---------------------------------------------------------
    @cached_property
    def _vibrator(self) -> Any:  # pragma: no cover - device-only path
        context_cls = self._autoclass("android.content.Context")
        build_version = self._autoclass("android.os.Build$VERSION")
        if build_version.SDK_INT >= 31:
            manager = self._context.getSystemService(context_cls.VIBRATOR_MANAGER_SERVICE)
            return manager.getDefaultVibrator()
        return self._context.getSystemService(context_cls.VIBRATOR_SERVICE)

    def vibrate(  # pragma: no cover - device-only path
        self, milliseconds: int, amplitude: int = DEFAULT_AMPLITUDE
    ) -> None:
        build_version = self._autoclass("android.os.Build$VERSION")
        if build_version.SDK_INT >= 26:
            effect_cls = self._autoclass("android.os.VibrationEffect")
            self._vibrator.vibrate(effect_cls.createOneShot(milliseconds, amplitude))
        else:
            self._vibrator.vibrate(milliseconds)

    def vibrate_pattern(  # pragma: no cover - device-only path
        self, pattern: list[int], repeat: int = -1
    ) -> None:
        build_version = self._autoclass("android.os.Build$VERSION")
        if build_version.SDK_INT >= 26:
            effect_cls = self._autoclass("android.os.VibrationEffect")
            self._vibrator.vibrate(effect_cls.createWaveform(pattern, repeat))
        else:
            self._vibrator.vibrate(pattern, repeat)

    def cancel_vibration(self) -> None:  # pragma: no cover - device-only path
        self._vibrator.cancel()

    # -- permissions -------------------------------------------------------
    def has_permission(self, permission: str) -> bool:  # pragma: no cover - device-only path
        package_manager = self._autoclass("android.content.pm.PackageManager")
        result = self._context.checkSelfPermission(permission)
        return int(result) == int(package_manager.PERMISSION_GRANTED)

    def request_permissions(  # pragma: no cover - device-only path
        self, permissions: list[str]
    ) -> dict[str, bool]:
        """Request permissions and wait for the user's answer.

        Android shows the system dialog asynchronously and reports the outcome
        through ``Activity.onRequestPermissionsResult``. We register a Python
        callback via pyjnius, fire the dialog and block until every requested
        permission has been answered — so the framework (and tests) observe the
        real result instead of the "not granted" snapshot we'd otherwise take
        a millisecond after the dialog opened.
        """
        if not permissions:
            return {}
        already_granted: dict[str, bool] = {}
        to_request: list[str] = []
        for name in permissions:
            if self.has_permission(name):
                already_granted[name] = True
            else:
                to_request.append(name)
        if not to_request:
            return already_granted

        future: PermissionFuture = self._request_permissions_async(to_request)
        future.wait()  # safe on a UI thread because the OS dialog is modal
        result = future.result
        already_granted.update(result)
        return already_granted

    def _request_permissions_async(  # pragma: no cover - device-only path
        self, permissions: list[str]
    ) -> PermissionFuture:
        """Open the system dialog and return a :class:`PermissionFuture`.

        Exposed so a screen that wants to keep its UI responsive while the
        dialog is up can attach a ``then`` callback instead of blocking.
        """
        build_version = self._autoclass("android.os.Build$VERSION")
        future = PermissionFuture(permissions)
        if build_version.SDK_INT < 23:
            # Pre-Marshmallow: permissions are install-time; everything not
            # already granted is denied silently. Skip the dialog and resolve
            # immediately so callers still observe a concrete outcome.
            future._complete(
                {name: self.has_permission(name) for name in permissions}, timed_out=True
            )
            return future

        with self._pending_permissions_lock:
            self._pending_permissions[future.request_code] = future
        self._ensure_permission_callback()
        self._activity.requestPermissions(permissions, future.request_code)
        return future

    def _ensure_permission_callback(self) -> None:
        """Attach the Java → Python permission callback exactly once.

        Attaching twice would shadow the first one. The activity is an Android
        singleton so the callback survives across screens for the lifetime of
        the process, which is exactly what we want.
        """
        if self._permission_callback_attached:
            return
        self._permission_callback_attached = True
        try:
            from jnius import PythonJavaClass, java_method  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - device-only path
            return

        bridge = self

        class _PermissionListener(PythonJavaClass):  # type: ignore[misc]
            __javainterfaces__: ClassVar[list[str]] = [
                "org/pymobile/app/PermissionListener"
            ]
            __javacontext__: ClassVar[str] = "app"

            @java_method(  # type: ignore[untyped-decorator]
                "(I[Ljava/lang/String;[IZ)V"
            )
            def onResult(
                self,
                request_code: int,
                permissions_j: Any,
                grant_results_j: Any,
                _rationale_asked: bool,
            ) -> None:
                names = list(permissions_j) if permissions_j is not None else []
                grants = list(grant_results_j) if grant_results_j is not None else []
                # PackageManager.PERMISSION_GRANTED == 0; anything else is denied.
                outcome = {
                    name: (int(grants[i]) == 0 if i < len(grants) else False)
                    for i, name in enumerate(names)
                }
                with bridge._pending_permissions_lock:
                    pending = bridge._pending_permissions.pop(request_code, None)
                if pending is not None:
                    pending._complete(outcome, timed_out=False)

        self._permission_listener = _PermissionListener()  # keep alive

    # -- ui ----------------------------------------------------------------
    def toast(self, message: str, long: bool = False) -> None:  # pragma: no cover
        toast_cls = self._autoclass("android.widget.Toast")
        duration = toast_cls.LENGTH_LONG if long else toast_cls.LENGTH_SHORT
        self._run_on_ui(lambda: toast_cls.makeText(self._context, message, duration).show())

    def render(self, tree: dict[str, Any]) -> None:  # pragma: no cover - device-only path
        """Send the widget tree to the native renderer.

        The native side is layered on top of this call; until it ships the tree
        is logged so the app still boots on device.
        """
        _log.debug("render tree: %s", tree.get("type"))

    def _run_on_ui(self, action: Any) -> None:  # pragma: no cover - device-only path
        from jnius import PythonJavaClass, java_method  # type: ignore[import-not-found]

        bridge = self

        class _Runnable(PythonJavaClass):  # type: ignore[misc]
            # pyjnius reads these class attributes; ClassVar keeps linters happy.
            __javainterfaces__: ClassVar[list[str]] = ["java/lang/Runnable"]
            __javacontext__: ClassVar[str] = "app"

            @java_method("()V")  # type: ignore[untyped-decorator]
            def run(self) -> None:
                try:
                    action()
                finally:
                    # Release exactly after Java invokes us, even when the UI
                    # callback raises. This prevents both premature GC and a
                    # permanent Python-side Runnable leak.
                    with bridge._ui_runnables_lock:
                        bridge._ui_runnables.pop(id(self), None)

        runnable = _Runnable()
        with self._ui_runnables_lock:
            self._ui_runnables[id(runnable)] = runnable
        try:
            self._activity.runOnUiThread(runnable)
        except Exception:
            with self._ui_runnables_lock:
                self._ui_runnables.pop(id(runnable), None)
            raise


class PermissionFuture:
    """Awaits the user's answer to a runtime-permission dialog.

    Mirrors :class:`HttpFuture` and :class:`JobHandle`: thread-safe, callback-
    based, and ``wait()``-able. Every pending request gets a unique
    ``request_code`` so the Android callback (which fires once per dialog)
    knows which future to resolve.
    """

    __slots__ = ("_permissions", "_result", "_done", "_lock", "_callbacks", "request_code")

    _next_code = 1000
    _code_lock = threading.Lock()

    def __init__(self, permissions: list[str]) -> None:
        self._permissions = list(permissions)
        self._result: dict[str, bool] = {}
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[dict[str, bool]], None]] = []
        with PermissionFuture._code_lock:
            # request_code must not collide with anything the host app uses.
            # Start at 1000 to stay clear of common activity result codes.
            self.request_code = PermissionFuture._next_code
            PermissionFuture._next_code += 1

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def result(self) -> dict[str, bool]:
        return dict(self._result)

    def wait(self, timeout: float | None = None) -> dict[str, bool]:
        """Block until the system dialog returns or ``timeout`` elapses.

        A timeout returns an empty result rather than raising, because the
        caller is usually UI code that should fall back to "denied" instead of
        crashing. ``None`` waits forever.
        """
        finished = self._done.wait(timeout)
        if not finished:
            return {}
        return self.result

    def then(
        self,
        on_done: Callable[[dict[str, bool]], None],
    ) -> PermissionFuture:
        """Register a callback fired on the calling thread when done."""
        with self._lock:
            if not self._done.is_set():
                self._callbacks.append(on_done)
                return self
        on_done(self.result)
        return self

    def _complete(self, outcome: dict[str, bool], *, timed_out: bool) -> None:
        # Merging with what we already have keeps a partial answer
        # (e.g. user answered one permission, then dismissed) useful: the
        # missing one is reported as denied.
        with self._lock:
            self._result = {name: outcome.get(name, False) for name in self._permissions}
            self._done.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for cb in callbacks:
            try:
                cb(self.result)
            except Exception:  # never let a callback break the dialog flow
                _log.exception("permission future callback failed")
