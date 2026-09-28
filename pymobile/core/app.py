"""The application object.

:class:`App` is the single entry point an application author touches. It wires
together the platform bridge, the feature APIs, the event bus and navigation —
but it owns none of their logic, so each part stays independently testable.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import sysconfig
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from ..errors import PyMobileError
from ..log import configure, get_logger
from .api.notifications import Notifications
from .api.permissions import Permission, PermissionManager
from .api.storage import Storage, default_storage_path
from .api.vibration import Vibration
from .bridge import Bridge, get_bridge
from .dispatcher import UiDispatcher
from .events import EventBus, Subscription
from .i18n import translations
from .jobs import JobHandle, JobManager
from .net.http import HttpClient
from .platform import current_platform
from .plugins import plugins as _plugin_registry
from .scheduler import Scheduler, TimerHandle
from .ui.registry import unknown_types
from .ui.screen import Navigator, Screen, ScreenT
from .ui.snackbar import DEFAULT_ACTION_DURATION, DEFAULT_DURATION, SNACKBAR_ID, Snackbar
from .ui.theme import Theme

#: Event kind the device bridge uses to wake the event loop for UI calls.
WAKE_EVENT = "__wake__"

#: How many frames in a row may be requested while a frame is being built.
#: A handful is normal (a widget updated inside build()); a long run means a
#: feedback loop, and the loop is stopped and reported instead of spinning.
_MAX_DEFERRED_FRAMES = 8

__all__ = ["App"]

_log = get_logger("app")


def _noop() -> None:
    return None

#: The application started most recently, or None. Developer tooling (the
#: interactive preview, the reloader) needs a handle on the running app
#: without dictating where the author keeps theirs.
_current: App | None = None


def _app_store_path(package: str) -> Path:
    """Where this application keeps its store when nothing was configured.

    On a device each app already owns a private directory, but on a desktop
    every PyMobile project used to share a single ``pymobile_store.json`` —
    two projects open at once would overwrite each other's settings while you
    developed them. The file is named after the application id instead.

    A store written by an older version — or placed by hand under the
    package's dotted name, ``com.example.notes.json`` — is adopted once, so
    upgrading does not look like the user's data disappeared.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", package.lower()).strip("-") or "app"
    path = default_storage_path(f"{slug}.json")
    if not path.exists():
        legacies = [default_storage_path()]
        # The store is named with dashes (com-example-notes.json). A file
        # named after the package verbatim, dots and all, is the obvious
        # guess when seeding or restoring one by hand; it is specific to this
        # app, so it is adopted everywhere, before any generic legacy name.
        for dotted in dict.fromkeys((f"{package}.json", f"{package.lower()}.json")):
            legacies.insert(0, default_storage_path(dotted))
        # Before the package was read from pymobile.toml an app without an
        # explicit ``package=`` wrote org-pymobile-app.json. On a device the
        # directory is private to this app, so adopting it is safe there (on a
        # desktop it would be shared by every project, so it is not adopted).
        if current_platform().value == "android":
            legacies.insert(0, default_storage_path("org-pymobile-app.json"))
        for legacy in legacies:
            if legacy.exists() and legacy != path:
                with suppress(OSError):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(legacy, path)
                    _log.debug("adopted the store %s for %s", legacy, package)
                break
    return path


def _package_from_project() -> str:
    """The ``package`` declared in the project's ``pymobile.toml``, if any.

    ``App(...)`` without ``package=`` used to fall back to ``org.pymobile.app``
    even inside a configured project, so on the desktop every project shared
    one store file. The project is looked up next to the ``__main__`` script
    (and its parents), then in the working directory. ``PYMOBILE_PACKAGE``
    overrides the lookup. Any problem simply means "not found".
    """
    override = os.environ.get("PYMOBILE_PACKAGE", "").strip()
    if override:
        return override
    starts: list[Path] = []
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if main_file:
        starts.append(Path(main_file).resolve().parent)
    with suppress(OSError):
        starts.append(Path.cwd())
    seen: set[Path] = set()
    for start in starts:
        for folder in (start, *list(start.parents)[:3]):
            if folder in seen:
                continue
            seen.add(folder)
            if not (folder / "pymobile.toml").is_file():
                continue
            try:
                from .config import load_config

                return load_config(folder).package
            except Exception as exc:  # a broken toml must not stop the app
                # ...but it must not go unnoticed either: the app would fall
                # back to the shared org.pymobile.app store.
                _log.warning(
                    "ignoring %s/pymobile.toml (%s); using package %r — pass "
                    "App(package=...) or fix the file",
                    folder,
                    exc,
                    "org.pymobile.app",
                )
                return ""
    return ""


_PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
_STDLIB_DIRS = tuple(
    {
        str(Path(path).resolve())
        for path in (sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"))
        if path
    }
)


def _raised_by_user_code(error: BaseException) -> bool:
    """Whether ``error`` came out of application code rather than the framework.

    A ``TypeError``/``ValueError`` from a widget's value conversion (a front end
    sending ``"abc"`` to a Stepper) is bad input; the same exception raised
    inside the app's own ``on_press`` is a bug in the app and needs a traceback.
    """
    tb = error.__traceback__
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        if filename and not filename.startswith("<"):
            resolved = str(Path(filename).resolve())
            if not resolved.startswith(_PACKAGE_DIR) and not resolved.startswith(_STDLIB_DIRS):
                return True
        tb = tb.tb_next
    return False


def _weak_language_listener(app: App) -> Callable[[str], None]:
    """A translations listener that does not keep ``app`` alive."""
    ref = weakref.ref(app)

    def listener(language: str) -> None:
        target = ref()
        if target is not None:
            target._on_language_change(language)

    return listener


class App:
    """Root object of a PyMobile application.

    Example::

        app = App("Demo")
        app.run(HomeScreen())
    """

    def __init__(
        self,
        name: str = "PyMobile App",
        *,
        bridge: Bridge | None = None,
        base_url: str = "",
        log_level: str = "info",
        auto_render: bool = True,
        storage_path: str | None = None,
        theme: str | Theme | None = None,
        version: str = "",
        package: str = "",
        log_file: str | None = None,
    ) -> None:
        self.name = name
        self.version = version or "0.1.0"
        self.package = package or _package_from_project() or "org.pymobile.app"
        self.bridge: Bridge = bridge or get_bridge()
        self.events = EventBus()
        #: Thread-safe hand-off for state changes produced by jobs/HTTP/timers.
        self.dispatcher = UiDispatcher()
        self.navigator = Navigator(self, on_change=self._on_screen_change)
        self.notifications = Notifications(self.bridge, channel_name=name)
        self.vibration = Vibration(self.bridge)
        self.permissions = PermissionManager(self.bridge)
        self.http = HttpClient(base_url=base_url, deliver=self._deliver)
        self.storage = (
            Storage(storage_path)
            if storage_path is not None
            else Storage(_app_store_path(self.package))
        )
        self._theme = self._resolve_theme(theme)
        self._scheduler = Scheduler()
        self.jobs = JobManager(deliver=self._deliver)
        self._log_level = log_level
        self._log_file = log_file
        self._running = False
        self._ever_started = False
        self.auto_render = auto_render
        self._render_scheduled = False
        self._render_depth = 0
        self._render_lock = threading.RLock()
        #: True while a frame is being serialised; a re-entrant render() request
        #: is deferred instead of recursing into build() (see render()).
        self._rendering = False
        self._warned_render_loop = False
        #: Widget types already reported as having no native renderer.
        self._warned_unknown_types: set[str] = set()
        #: Serialises everything that touches widget state outside the device
        #: UI thread: event handlers, dispatched callbacks and rendering.
        self._ui_lock = threading.RLock()
        #: Thread running the device event loop (None on desktop / previews).
        self._loop_thread: int | None = None
        self._ui_calls: list[Callable[[], None]] = []
        self._ui_calls_lock = threading.Lock()
        self._wake_pending = False
        # Rebuild the visible screen when the language changes: t() is called
        # inside build(), so the old text is already baked into the widgets.
        # Subscribed in run() — an App that never runs has nothing to rebuild,
        # and 50 test apps used to leave 54 listeners on the global catalogue.
        self._unsubscribe_language: Callable[[], None] = _noop
        #: The snackbar on screen, if any (see snackbar()).
        self._snackbar: Snackbar | None = None
        self._snackbar_serial = 0

    @staticmethod
    def current() -> App | None:
        """The application currently running in this process, if any."""
        return _current

    # -- properties --------------------------------------------------------
    @property
    def platform(self) -> str:
        """Name of the runtime platform (``"android"`` or ``"desktop"``)."""
        return str(current_platform())

    @property
    def running(self) -> bool:
        """Whether :meth:`run` has been called and :meth:`stop` has not."""
        return self._running

    @property
    def screen(self) -> Screen | None:
        """The currently visible screen."""
        return self.navigator.current

    @property
    def theme(self) -> Theme:
        """The active application theme."""
        return self._theme

    @property
    def info(self) -> dict[str, str]:
        """Application metadata: name, version, package, platform."""
        return {
            "name": self.name,
            "version": self.version,
            "package": self.package,
            "platform": self.platform,
        }

    def set_theme(self, theme: str | Theme) -> None:
        """Switch the theme and redraw the visible screen (like a language change)."""
        resolved = self._resolve_theme(theme)
        if resolved.name == self._theme.name and resolved.as_dict() == self._theme.as_dict():
            return
        self._theme = resolved
        self.events.emit("app:theme", source=resolved.name)
        self._refresh_screens()

    @staticmethod
    def _resolve_theme(theme: str | Theme | None) -> Theme:
        """Turn a name/Theme/None into a :class:`Theme` (defaults to light)."""
        if theme is None:
            return Theme.light()
        if isinstance(theme, Theme):
            return theme
        lowered = theme.strip().lower()
        if lowered in ("dark", "dark_mode"):
            return Theme.dark()
        if lowered in ("light", "light_mode", ""):
            return Theme.light()
        raise ValueError(f"unknown theme {theme!r}; use 'light', 'dark' or a Theme object")

    # -- lifecycle ---------------------------------------------------------
    def run(self, screen: Screen) -> None:
        """Start the app with ``screen`` as the initial view.

        On a device this blocks in the UI event loop until the activity is
        destroyed; on a desktop it returns once the first screen is rendered,
        which keeps previews and tests non-blocking.
        """
        global _current
        if self._ever_started and not self._running:
            # stop() shuts the dispatcher, the job manager and the event
            # subscriptions down for good. Running again used to work just
            # enough to look alive: navigation and timers fine, the first
            # run_job() raising "JobManager has been shut down".
            raise PyMobileError(
                "The application has already been stopped",
                hint=(
                    "stop() releases the job manager, the dispatcher and every event "
                    "subscription; create a new App if the application should run again."
                ),
            )
        configure(self._log_level, log_file=self._log_file)
        _log.info("starting %s on %s (bridge=%s)", self.name, self.platform, self.bridge.name)
        self._running = True
        self._ever_started = True
        _current = self
        self._unsubscribe_language()
        unsubscribe = translations.subscribe(_weak_language_listener(self))
        self._unsubscribe_language = unsubscribe
        # An app that is dropped without stop() must not stay subscribed.
        weakref.finalize(self, unsubscribe)
        _plugin_registry.activate_all(self)
        _plugin_registry.on_app_start(self)
        self.events.emit("app:start", source=self.name)
        self.navigator.reset(screen)

        if hasattr(self.bridge, "next_event"):
            self._event_loop()

    def _event_loop(self) -> None:
        """Dispatch UI events until the platform asks us to stop.

        Widgets are looked up by id in the current screen, so a callback always
        acts on the widget the user actually touched.
        """
        next_event = self.bridge.next_event  # type: ignore[attr-defined]
        _log.info("entering the UI event loop")
        self._loop_thread = threading.get_ident()
        try:
            while self._running:
                event = next_event(-1)
                if event is None:
                    break
                widget_id, kind, value = event
                try:
                    if kind == WAKE_EVENT:
                        self._run_ui_calls()
                    else:
                        self.handle_ui_event(widget_id, kind, value)
                except Exception:
                    _log.exception("error handling %s on %s", kind, widget_id)
        finally:
            self._loop_thread = None
        _log.info("event loop finished")

    # -- running code on the UI thread ------------------------------------
    def _call_on_ui(self, function: Callable[[], None]) -> None:
        """Run ``function`` where the UI state lives, from any thread.

        * device: on the event-loop thread — a background thread wakes the loop
          through the bridge and the call runs between two UI events;
        * Tk preview: on the Tk thread (``bridge.call_soon``);
        * anywhere else (tests, the browser preview, whose HTTP handlers run on
          several threads): right here, under the UI lock that event handlers
          and rendering also hold, so it never interleaves with them.
        """
        if self._loop_thread is not None:
            if threading.get_ident() == self._loop_thread:
                function()
                return
            wake = getattr(self.bridge, "wake", None)
            if callable(wake):
                with self._ui_calls_lock:
                    self._ui_calls.append(function)
                    first = not self._wake_pending
                    self._wake_pending = True
                if first:
                    wake()
                return
        call_soon = getattr(self.bridge, "call_soon", None)
        if callable(call_soon) and call_soon(lambda: self._locked(function)):
            return
        self._locked(function)

    def _locked(self, function: Callable[[], None]) -> None:
        with self._ui_lock:
            function()

    def _run_ui_calls(self) -> None:
        """Run the calls queued by background threads (device event loop)."""
        with self._ui_calls_lock:
            calls = self._ui_calls
            self._ui_calls = []
            self._wake_pending = False
        for call in calls:
            try:
                self._locked(call)
            except Exception:
                _log.exception("error in a call scheduled on the UI thread")

    def handle_ui_event(self, widget_id: str, kind: str, value: str) -> None:
        """Apply one UI interaction and draw a single frame for it.

        Every front end — the device event loop, the Tk window, the browser
        preview — funnels through here, so the batching lives inside rather
        than being something each caller has to remember to wrap.
        """
        with self._ui_lock, self.batch():
            self._handle_ui_event(widget_id, kind, value)

    def _handle_ui_event(self, widget_id: str, kind: str, value: str) -> None:
        """Apply one UI event to the widget it belongs to."""
        if kind == "back":
            if self.navigator.depth > 1:
                self.pop()
            else:
                self._finish_or_stop()
            return
        if widget_id == SNACKBAR_ID:
            self._snackbar_event(kind, value)
            return
        if kind == "locale":
            # The system language changed while the app was running (the
            # activity is no longer recreated for it). Apps that follow the
            # device language react with: app.on("app:locale", ...).
            self.events.emit("app:locale", source=value)
            return

        screen = self.navigator.current
        if screen is None:
            return
        widget = screen.find(widget_id)
        if widget is None:
            _log.debug("event for unknown widget %r", widget_id)
            return

        _log.debug(
            "ui event %s on %s (type=%s) value=%r", kind, widget_id, type(widget).__name__, value
        )

        # A handler that raises must not take the application down with it, and
        # a malformed value from a front end (Stepper ← "abc") must not either.
        # Event-bus handlers and timer callbacks are already protected this way;
        # widget callbacks now match, so the contract holds everywhere.
        try:
            if kind == "press" and hasattr(widget, "press"):
                widget.press()
            elif kind == "long_press" and hasattr(widget, "long_press"):
                widget.long_press()
            elif kind == "change" and hasattr(widget, "_ui_set_value"):
                widget._ui_set_value(value)
            elif kind == "change" and hasattr(widget, "set_value"):
                widget.set_value(value)
            elif kind == "dismiss" and hasattr(widget, "dismiss"):
                widget.dismiss()
            elif kind == "toggle" and hasattr(widget, "set_checked"):
                widget.set_checked(value == "true")
            elif kind == "increment" and hasattr(widget, "increment"):
                widget.increment()
            elif kind == "decrement" and hasattr(widget, "decrement"):
                widget.decrement()
            elif kind == "search" and hasattr(widget, "submit"):
                widget.submit()
            elif kind == "load_more" and hasattr(widget, "_ui_load_more"):
                widget._ui_load_more(value)
            elif kind == "swipe" and hasattr(widget, "_ui_swipe"):
                widget._ui_swipe(value)
            elif kind == "refresh" and hasattr(widget, "_ui_refresh"):
                widget._ui_refresh(value)
        except (TypeError, ValueError) as exc:
            if _raised_by_user_code(exc):
                # A bug in the app's handler, not bad input: it used to be
                # reported as "value '' is not valid for a Button" without a
                # traceback, which sent people looking in the wrong place.
                _log.exception(
                    "the %s handler of %s (%s) raised", kind, widget_id, type(widget).__name__
                )
            else:
                _log.warning(
                    "ignoring %s event for %s: value %r is not valid for a %s (%s)",
                    kind,
                    widget_id,
                    value,
                    type(widget).__name__,
                    exc,
                )
        except Exception:
            _log.exception(
                "the %s handler of %s (%s) raised", kind, widget_id, type(widget).__name__
            )
        self.events.emit(f"ui:{kind}", source=widget_id, value=value)

    def _finish_or_stop(self) -> None:
        """Handle the root hardware-back button.

        On device this asks the platform to finish the Activity — previously
        only the app stopped while the window stayed on screen, which looked
        like a freeze. Preview bridges keep stopping the app as before.
        """
        finish = getattr(self.bridge, "finish_app", None)
        if callable(finish):
            try:
                if finish():
                    return
            except Exception:
                _log.exception("finish_app failed")
        self.stop()

    def stop(self) -> None:
        """Shut the app down and release subscriptions.

        Pending timers are always cancelled, even when ``run`` was never
        called, so a partially initialised app cannot leak background work.
        """
        global _current
        self._scheduler.cancel_all()
        if self._snackbar is not None:
            self._snackbar._retire()
            self._snackbar = None
        self.dispatcher.close()
        if not self._running:
            return
        self._running = False
        self._unsubscribe_language()
        self.navigator.dispose()
        self.jobs.shutdown()
        _plugin_registry.on_app_stop(self)
        if _current is self:
            _current = None
        self.events.emit("app:stop", source=self.name)
        self.events.clear()
        _log.info("stopped %s", self.name)

    # -- ui ----------------------------------------------------------------
    def render(self) -> dict[str, Any] | None:
        """Serialise the visible screen and hand it to the bridge.

        Calling this by hand is no longer required — widgets schedule their own
        redraws — but it stays available for the rare case that needs a frame
        pushed out right now.

        Rendering is not re-entrant. ``render()`` reached *while a frame is
        being built* — a shell that rebuilds itself from ``build()``, or from a
        lifecycle hook running during that frame — records the request and
        returns ``None``; the frame that is already in flight is drawn again
        when it finishes. Without that, ``build()`` ran inside itself until the
        interpreter raised ``RecursionError``.
        """
        with self._ui_lock:
            if self._rendering:
                with self._render_lock:
                    self._render_scheduled = True
                _log.debug("render() re-entered while a frame was being built; deferring")
                return None
            self._rendering = True
            try:
                tree = self._render_locked()
                for _ in range(_MAX_DEFERRED_FRAMES):
                    with self._render_lock:
                        if not self._render_scheduled:
                            break
                    tree = self._render_locked()
                else:
                    # Something redraws on every frame: stop, rather than spin.
                    with self._render_lock:
                        self._render_scheduled = False
                    if not self._warned_render_loop:
                        self._warned_render_loop = True
                        _log.warning(
                            "widgets changed on every frame %d times in a row; stopping "
                            "the redraw loop — a callback probably rebuilds the tree "
                            "during build()",
                            _MAX_DEFERRED_FRAMES,
                        )
                return tree
            finally:
                self._rendering = False

    def _render_locked(self) -> dict[str, Any] | None:
        # Drain updates queued by background work before serialising the tree.
        # Renderer bridges own the final platform-thread hand-off.
        self.dispatcher.drain()
        screen = self.navigator.current
        if screen is None:
            return None
        # Hold the lock for the full serialise-and-render so a second thread
        # observing ``_render_scheduled`` cannot decide to render concurrently.
        # The bridge call itself is fast (JSON serialisation + a function call),
        # so the critical section stays short.
        with self._render_lock:
            self._render_scheduled = False
            tree = screen.to_dict()
            bar = self._snackbar
            if bar is not None and bar.visible:
                tree["snackbar"] = bar.to_dict()
            self._check_widget_types(tree)
            payload = tree
            if getattr(self.bridge, "accepts_theme", False):
                # The device renderer paints its own defaults (text, surfaces,
                # selected tabs …) from the palette; previews ignore it.
                payload = {**tree, "theme": {**self._theme.as_dict(), "dark": self._theme.is_dark}}
            self.bridge.render(payload)
        self.events.emit("app:render", source=screen.title, tree=tree)
        return tree

    def _check_widget_types(self, tree: dict[str, Any]) -> None:
        """Warn once about widget types the native renderer cannot draw.

        ``ViewBuilder.java`` has one branch per built-in type; a custom
        ``type_name`` without one becomes an *empty view* on the phone — no
        exception, no placeholder — while the desktop previews print
        ``<BarChart>`` and look fine. The walk runs only for bridges that feed
        that renderer, so a preview never sees the warning, and each type is
        reported once per app rather than once per frame.
        """
        if not getattr(self.bridge, "native_widgets", False):
            return
        for name in sorted(unknown_types(tree, renderer="android")):
            if name in self._warned_unknown_types:
                continue
            self._warned_unknown_types.add(name)
            _log.warning(
                "widget type %r has no branch in the native renderer (ViewBuilder.java): "
                "on the phone it draws as an empty view, silently. Add the case there and "
                "declare it with register_widget_type(%r) so this warning stops — or "
                "compose the widget from existing ones (Row/Column/ProgressBar).",
                name,
                name,
            )

    def schedule_render(self) -> None:
        """Request a redraw of the visible screen, coalescing repeats.

        This is what a widget calls when it changes. Inside a
        :meth:`batch` block — and while a UI callback is running — the redraws
        pile up into a single frame, so a handler that updates six labels
        still renders once.
        """
        if not self.auto_render or not self._running:
            return
        with self._render_lock:
            if self._render_depth > 0:
                self._render_scheduled = True
                return
            # Re-check inside the lock: another thread may have already
            # started a render, in which case we piggy-back on its scheduled
            # follow-up instead of issuing a second concurrent render.
            if self._render_scheduled:
                return
            self._render_scheduled = True
        # Render is called outside the lock to avoid holding it across the
        # bridge call — but ``_render_scheduled`` is cleared inside ``render``
        # itself, under the same lock, so a concurrent caller will see the
        # in-flight render and queue exactly one follow-up frame.
        self.render()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group many widget updates into a single redraw.

        ::

            with app.batch():
                for label, value in zip(labels, values):
                    label.text = value
            # one render happens here

        Nesting is allowed; only the outermost block flushes.
        """
        with self._render_lock:
            self._render_depth += 1
        try:
            yield
        finally:
            with self._render_lock:
                self._render_depth -= 1
                flush = self._render_depth == 0 and self._render_scheduled
            if flush:
                self.render()

    def push(self, screen: ScreenT) -> ScreenT:
        """Navigate to a new screen."""
        return self.navigator.push(screen)

    def pop(self) -> Screen | None:
        """Go back one screen; ``None`` when already at the root.

        Safe to call multiple times — extra pops on the root return ``None``.
        """
        return self.navigator.pop()

    def replace(self, screen: ScreenT) -> ScreenT:
        """Replace the top screen with ``screen`` (no back to previous).

        Example::

            app.replace(HomeScreen())  # after login, no back to login
        """
        return self.navigator.replace(screen)

    def reset(self, screen: ScreenT) -> ScreenT:
        """Clear stack and start from ``screen`` (e.g. on logout).

        Example::

            app.reset(LoginScreen())
        """
        return self.navigator.reset(screen)

    # -- UI dispatch -------------------------------------------------------
    def dispatch(self, callback: Callable[..., None], *args: Any, **kwargs: Any) -> bool:
        """Hand a state update produced by background work to the UI.

        Use this instead of mutating widgets directly in a job, timer or HTTP
        completion callback::

            app.run_job(load).then(lambda cards: app.dispatch(show, cards))

        ``callback(*args, **kwargs)`` runs on the UI side — on a device, the
        event-loop thread; in the Tk preview, the Tk thread; elsewhere under the
        lock that event handlers and rendering hold — and one frame is drawn
        after it. Returns ``False`` once the app has stopped.
        """
        accepted = self.dispatcher.post(callback, *args, **kwargs)
        if accepted and self._running:
            self._call_on_ui(self._flush_dispatched)
        return accepted

    def _flush_dispatched(self) -> None:
        """Run queued dispatch callbacks (render drains them) and draw a frame."""
        if self.dispatcher.pending:
            self.render()

    # -- timers ------------------------------------------------------------
    def set_interval(
        self,
        interval_ms: int,
        callback: Callable[[], None],
        *,
        drift_correction: bool = True,
        background: bool = False,
    ) -> TimerHandle:
        """Run ``callback`` every ``interval_ms`` until the handle is cancelled.

        The callback runs on the UI side, like a button handler (see
        :meth:`dispatch`), so a clock can update a label directly and one frame
        is drawn per tick. Keep it short; for blocking work — a network poll —
        pass ``background=True`` and hand the result over with
        :meth:`dispatch`. All timers are cancelled automatically by :meth:`stop`.

        Ticks are aligned to a fixed timeline, so the time spent inside the
        callback does not accumulate into a visible lag — a one-second timer
        is still on the second after an hour. Pass ``drift_correction=False``
        to wait a fixed pause between runs instead.
        """
        return self._scheduler.set_interval(
            interval_ms,
            callback if background else self._on_ui(callback),
            drift_correction=drift_correction,
        )

    def set_timeout(
        self, delay_ms: int, callback: Callable[[], None], *, background: bool = False
    ) -> TimerHandle:
        """Run ``callback`` once after ``delay_ms`` milliseconds (UI side, see
        :meth:`set_interval`; ``background=True`` keeps it on the timer thread)."""
        return self._scheduler.set_timeout(
            delay_ms, callback if background else self._on_ui(callback)
        )

    def _deliver(self, callback: Callable[[], None]) -> None:
        """Run a job / HTTP completion callback on the UI side, as one frame."""
        self._on_ui(callback)()

    def _on_ui(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Wrap a timer callback so it runs where UI state lives, as one frame."""
        if not callable(callback):
            raise TypeError(f"callback must be callable, got {type(callback).__name__!r}")

        def run_batched() -> None:
            with self.batch():
                callback()

        def fire() -> None:
            if self._running:
                self._call_on_ui(run_batched)
            else:
                callback()  # not started (or stopped): nothing to protect

        return fire

    # -- background jobs ---------------------------------------------------
    def run_job(self, fn: Callable[[], Any], *, name: str | None = None) -> JobHandle:
        """Run ``fn`` once on a background thread; returns a :class:`JobHandle`.

        The result/error is captured on the handle and delivered to ``then``
        callbacks, so the UI is never blocked and errors do not crash the app.
        """
        return self.jobs.enqueue(fn, name=name)

    def repeat_job(
        self, interval_ms: int, fn: Callable[[], Any], *, name: str | None = None
    ) -> JobHandle:
        """Run ``fn`` every ``interval_ms`` until cancelled; returns a handle."""
        return self.jobs.every(interval_ms, fn, name=name)

    def toast(self, message: str, *, long: bool = False) -> None:
        """Show a short platform message."""
        self.bridge.toast(message, long)

    def snackbar(
        self,
        message: str,
        *,
        action: str | None = None,
        on_action: Callable[[], Any] | None = None,
        duration_ms: int | None = None,
    ) -> Snackbar:
        """Show a message at the bottom of the screen, optionally with one action.

        ::

            app.snackbar("Note deleted", action="Undo", on_action=restore)

        It hides itself after ``duration_ms`` (4 s, or 7 s with an action;
        ``0`` keeps it until :meth:`Snackbar.dismiss` or the action is
        tapped). A new snackbar replaces the one on screen. Tapping the action
        runs ``on_action`` on the UI side, like a button handler, and hides
        the snackbar.

        Unlike :meth:`toast`, a snackbar is drawn by the framework as part of
        the frame, so previews and tests see it: ``app.current_snackbar``.
        """
        if action is not None and not str(action).strip():
            raise ValueError("action must be a non-empty label (or None)")
        if on_action is not None and action is None:
            raise ValueError("on_action needs an action label to tap: action='Undo'")
        if on_action is not None and not callable(on_action):
            raise TypeError("on_action must be callable")
        if duration_ms is None:
            duration_ms = DEFAULT_ACTION_DURATION if action else DEFAULT_DURATION
        if duration_ms < 0:
            raise ValueError("duration_ms must not be negative")
        with self._ui_lock:
            previous = self._snackbar
            if previous is not None:
                previous._retire()
            self._snackbar_serial += 1
            bar = Snackbar(
                self,
                str(message),
                action=None if action is None else str(action),
                on_action=on_action,
                duration_ms=duration_ms,
                token=self._snackbar_serial,
            )
            self._snackbar = bar
            if duration_ms > 0:
                bar._timer = self.set_timeout(duration_ms, bar.dismiss)
        self.schedule_render()
        return bar

    @property
    def current_snackbar(self) -> Snackbar | None:
        """The snackbar on screen, or ``None``."""
        bar = self._snackbar
        return bar if bar is not None and bar.visible else None

    def _snackbar_closed(self, bar: Snackbar) -> None:
        if self._snackbar is bar:
            self._snackbar = None
            self.schedule_render()

    def _snackbar_event(self, kind: str, value: str) -> None:
        """A tap on the action / a swipe-away from a front end."""
        bar = self._snackbar
        if bar is None or not bar.visible:
            return
        if value and value != str(bar.token):
            _log.debug("ignoring %s for an old snackbar (%s)", kind, value)
            return
        try:
            if kind == "press":
                bar.press()
            elif kind == "dismiss":
                bar.dismiss()
        except Exception:
            _log.exception("the snackbar action raised")
        self.events.emit(f"ui:{kind}", source=SNACKBAR_ID, value=value)

    # -- convenience -------------------------------------------------------
    def notify(self, title: str, body: str = "", **kwargs: Any) -> int:
        """Post a local notification; returns its id."""
        return self.notifications.notify(title, body, **kwargs)

    def vibrate(self, milliseconds: int = 100) -> None:
        """Vibrate the device once."""
        self.vibration.vibrate(milliseconds)

    def require_permissions(self, *permissions: str | Permission) -> None:
        """Request permissions and fail loudly if any is denied."""
        self.permissions.require(*permissions)

    def on(
        self,
        event: str,
        handler: Callable[[Any], None],
        *,
        screen: Screen | None = None,
    ) -> Subscription:
        """Subscribe to an application event.

        Pass ``screen=`` to tie the subscription to a screen's lifetime: it is
        cancelled when that screen unmounts, which prevents the handler of a
        popped screen from firing (and from keeping the screen alive). Inside
        a screen, ``self.on(...)`` does the same thing with less typing.
        """
        subscription = self.events.on(event, handler)
        if screen is not None:
            screen._subscriptions.append(subscription)
        return subscription

    def off(self, event: str, handler: Callable[[Any], None] | None = None) -> int:
        """Unsubscribe ``handler`` (or every handler) from an application event.

        Bound methods work: ``app.off("tick", self.on_tick)``. Returns how many
        handlers were removed. To drop one specific subscription, keep the
        :class:`Subscription` from :meth:`on` and call ``cancel()`` on it.
        """
        return self.events.off(event, handler)

    # -- internals ---------------------------------------------------------
    def _on_language_change(self, language: str) -> None:
        if self.navigator.current is None or not self._running:
            return
        self.events.emit("app:language", source=language)
        self._refresh_screens()

    def _refresh_screens(self) -> None:
        if not self._running:
            return
        for screen in self.navigator.stack:
            screen.refresh()

    def _on_screen_change(self, screen: Screen | None) -> None:
        """Re-render whenever the navigator changes the visible screen."""
        if screen is None:
            return
        if not self._running:
            if self._ever_started:
                raise PyMobileError(
                    "The application has already been stopped",
                    hint=(
                        "Navigation is not available after app.stop(). Create a new App if "
                        "the application should run again."
                    ),
                )
            raise PyMobileError(
                "Navigation happened before App.run()",
                hint="Call app.run(FirstScreen()) to start the application.",
            )
        self.events.emit("screen:change", source=screen.title)
        self.render()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<App {self.name!r} platform={self.platform}>"
