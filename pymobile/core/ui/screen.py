"""Screens and navigation.

A :class:`Screen` builds a widget tree and receives lifecycle callbacks. The
:class:`Navigator` keeps a stack of screens and drives those callbacks, which
keeps navigation logic out of the app object.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ...errors import PyMobileError
from ...log import get_logger
from ..events import Event, Subscription
from .widget import W, Widget, widget_scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..app import App

__all__ = ["Screen", "Navigator"]

_log = get_logger("ui.screen")

#: Navigation preserves the concrete screen type, so `app.push(Details())`
#: is still a Details for the type checker and for editor completion.
ScreenT = TypeVar("ScreenT", bound="Screen")


class Screen:
    """One full-window view.

    Subclasses override :meth:`build` to return the widget tree. Lifecycle
    hooks (:meth:`on_mount`, :meth:`on_show`, :meth:`on_hide`,
    :meth:`on_unmount`) are optional.
    """

    #: Title shown in the action bar; defaults to the class name.
    title: str = ""

    def __init__(self, title: str | None = None) -> None:
        self.title = title or self.title or type(self).__name__
        self._app: App | None = None
        self._root: Widget | None = None
        self._mounted = False
        #: Whether this screen was ever on a navigator stack. A screen that
        #: never was is not what app.render() serialises, which is what makes
        #: a refresh() on it a no-op worth reporting.
        self._ever_mounted = False
        self._warned_manual_render = False
        self._warned_offstage_refresh = False
        self._subscriptions: list[Subscription] = []

    @property
    def app(self) -> App:
        """The running application this screen is attached to.

        The navigator clears the link when the screen leaves the stack, so
        a callback that outlives the screen must grab the reference first::

            def leave(self):
                app = self.app   # still valid during the current dispatch
                app.pop()

        After ``pop()`` this raises :class:`~pymobile.errors.PyMobileError`
        instead of returning ``None`` — a stale callback reaching for the
        application of a screen that no longer exists is a bug that used to
        fail three lines later with a cryptic ``AttributeError`` on ``None``.
        """
        if self._app is None:
            raise PyMobileError(
                f"screen {self.title!r} is no longer on the stack",
                hint="grab app = self.app before calling pop()",
            )
        return self._app

    # -- construction ------------------------------------------------------
    def build(self) -> Widget:
        """Return the widget tree for this screen."""
        raise NotImplementedError(f"{type(self).__name__} must implement build()")

    @property
    def root(self) -> Widget:
        """The built widget tree, constructed on first access."""
        if self._root is None:
            with widget_scope(self):
                root = self.build()
            if root is None:
                raise PyMobileError(
                    f"{type(self).__name__}.build() returned None",
                    hint="build() must return a Widget, e.g. Label('Hello').",
                )
            if not isinstance(root, Widget):
                raise PyMobileError(
                    f"{type(self).__name__}.build() must return Widget, "
                    f"got {type(root).__name__!r}",
                    hint="Return a Widget from build(), e.g. Label, Column.",
                )
            # The screen link lives on the root only; Widget.screen walks up
            # to find it, so every widget in the tree can reach us.
            root._screen = self
            self._root = root
            self._name_widgets()
        return self._root

    def _name_widgets(self) -> None:
        """Give widgets stored on ``self`` an id derived from the attribute.

        ``self.counter = Label("0")`` becomes ``counter`` instead of
        ``label-7``. Ids that shift whenever a widget is added above make
        logs, toasts and ``find()`` calls unreadable, and they are what the
        native side uses to patch views in place.

        Only attributes assigned during ``build()`` are considered, and an
        explicit ``id=`` always wins.
        """
        assert self._root is not None
        owned = {id(widget): widget for widget in self._root.walk()}
        for name, value in vars(self).items():
            if name.startswith("_") or not isinstance(value, Widget):
                continue
            if id(value) not in owned or value._explicit_id:
                continue
            value.id = name
        self._check_unique_ids()

    def _check_unique_ids(self) -> None:
        """Verify every widget in the tree has a unique id.

        The native renderer patches views by id, so two widgets sharing the
        same id silently clobber each other on every redraw. Detecting the
        clash at build time turns a hard-to-debug visual glitch into a loud,
        actionable error.
        """
        assert self._root is not None
        seen: dict[str, Widget] = {}
        duplicates: list[str] = []
        for widget in self._root.walk():
            existing = seen.get(widget.id)
            if existing is None:
                seen[widget.id] = widget
            elif widget.id not in duplicates:
                duplicates.append(widget.id)
        if duplicates:
            raise PyMobileError(
                f"duplicate widget id(s) in {type(self).__name__}: "
                + ", ".join(repr(wid) for wid in duplicates),
                hint="Give each widget a unique `id=` (or assign conflicting "
                "widgets to different attributes so the auto-naming does not "
                "collide).",
            )

    def invalidate(self) -> None:
        """Ask the application to redraw this screen.

        Called automatically whenever a widget in the tree changes, so
        application code rarely needs it. Redraws are coalesced by the app: a
        loop that updates ten labels still results in a single render.
        """
        app = self._app
        if app is None or app.navigator.current is not self:
            return
        if app.auto_render:
            app.schedule_render()
            return
        # auto_render was switched off deliberately, so the redraw is the
        # application's job — but a silent no-op is exactly the trap this
        # release removes, so say so once per screen.
        if not self._warned_manual_render:
            self._warned_manual_render = True
            _log.warning(
                "%s changed while auto_render is off; call app.render() to show it",
                type(self).__name__,
            )

    @property
    def mounted(self) -> bool:
        """Whether the screen is currently attached to a navigator."""
        return self._mounted

    def refresh(self) -> None:
        """Rebuild the tree from ``build()`` and re-render immediately.

        Use it when the *structure* changed (a list grew, a section appeared).
        Changing the text or state of an existing widget needs no call at all:
        the widget schedules its own redraw.

        The frame goes out only when this screen is the one the navigator
        shows — ``app.render()`` serialises ``navigator.current`` and nothing
        else. A screen stacked *below* the current one keeps its rebuilt tree
        and displays it the next time it becomes current. A screen that is not
        on the stack at all (a tab, a panel embedded in another screen's tree)
        has no frame of its own to repaint: the screen that builds it is the
        one to rebuild. That first case is reported, once, instead of quietly
        doing nothing.
        """
        if self._root is not None:
            self._root._screen = None
            for node in self._root.walk():
                node._parent = None
        self._root = None
        app = self._app
        if app is None:
            if not self._ever_mounted:
                self._warn_offstage_refresh(
                    f"{type(self).__name__} is not on a navigator stack, so no frame "
                    "contains this tree. A screen embedded in another screen (tabs, "
                    "panels) is rendered by the one that builds it: rebuild the shell "
                    "instead — see 'Screens as tabs' in the README."
                )
            return
        if app.navigator.current is self:
            app.render()

    def _warn_offstage_refresh(self, message: str) -> None:
        """Explain a refresh() that cannot repaint anything — once per screen."""
        if self._warned_offstage_refresh:
            return
        self._warned_offstage_refresh = True
        _log.warning("refresh() rebuilt the tree but nothing displays it: %s", message)

    @overload
    def find(self, widget_id: str) -> Widget | None: ...

    @overload
    def find(self, widget_id: str, kind: type[W]) -> W | None: ...

    def find(self, widget_id: str, kind: type[Widget] | None = None) -> Widget | None:
        """Look up a widget by id inside this screen.

        ``self.find("title", Label)`` is typed ``Label | None``; see
        :meth:`Widget.find <pymobile.core.ui.widget.Widget.find>`.
        """
        return self.root.find(widget_id) if kind is None else self.root.find(widget_id, kind)

    @overload
    def get(self, widget_id: str) -> Widget: ...

    @overload
    def get(self, widget_id: str, kind: type[W]) -> W: ...

    def get(self, widget_id: str, kind: type[Widget] | None = None) -> Widget:
        """Look up a widget that must exist: ``self.get("title", Label).text = "Hi"``.

        Raises :class:`~pymobile.errors.WidgetNotFoundError` (with the closest
        existing ids) instead of returning ``None``.
        """
        return self.root.get(widget_id) if kind is None else self.root.get(widget_id, kind)

    def find_all(self, kind: type[W]) -> list[W]:
        """Every widget of this screen that is an instance of ``kind``."""
        return self.root.find_all(kind)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the screen (title + widget tree)."""
        return {"screen": self.title, **self.root.to_dict()}

    # -- events ------------------------------------------------------------
    def on(self, event: str, handler: Callable[[Event], None]) -> Subscription:
        """Subscribe to an application event for as long as this screen lives.

        The subscription is cancelled automatically in :meth:`on_unmount`,
        which is the difference that matters::

            class TimerScreen(Screen):
                def on_mount(self):
                    self.on("pomodoro:tick", self.on_tick)

        With ``app.on()`` the handler of a popped screen stays registered, so
        pushing the screen a second time runs the callback twice and keeps the
        old instance alive. Here that cannot happen.

        The returned :class:`~pymobile.core.events.Subscription` can still be
        cancelled by hand for a one-shot listener.
        """
        if self._app is None:
            raise PyMobileError(
                f"{type(self).__name__}.on() needs a running app",
                hint="Subscribe from on_mount() or later, not from __init__().",
            )
        subscription = self._app.events.on(event, handler)
        self._subscriptions.append(subscription)
        return subscription

    def _cancel_subscriptions(self) -> None:
        """Drop every subscription made through :meth:`on`."""
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    # -- lifecycle ---------------------------------------------------------
    def on_mount(self) -> None:
        """Called once when the screen is pushed onto the navigator."""

    def on_show(self) -> None:
        """Called every time the screen becomes visible."""

    def on_hide(self) -> None:
        """Called when another screen covers this one."""

    def on_unmount(self) -> None:
        """Called once when the screen is popped."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Screen {self.title!r}>"


class Navigator:
    """A stack of screens with lifecycle dispatch."""

    __slots__ = ("_stack", "_app", "_on_change")

    def __init__(
        self, app: App | None = None, on_change: Callable[[Screen | None], None] | None = None
    ) -> None:
        self._stack: list[Screen] = []
        self._app = app
        self._on_change = on_change

    @property
    def current(self) -> Screen | None:
        """The visible screen, or ``None`` when the stack is empty."""
        return self._stack[-1] if self._stack else None

    @property
    def stack(self) -> Sequence[Screen]:
        """Immutable view of the screen stack, bottom first."""
        return tuple(self._stack)

    @property
    def depth(self) -> int:
        """Number of screens on the stack."""
        return len(self._stack)

    def _prepare(self, screen: ScreenT) -> None:
        """Validate ``screen`` and build its widget tree without touching the stack.

        Every navigation builds the new screen *first*: if ``build()`` raises,
        the current screen is still mounted and visible and the stack is
        unchanged. (``replace()``/``reset()`` used to unmount the old screen
        before building the new one, leaving an empty stack on failure.)
        """
        if not isinstance(screen, Screen):
            raise TypeError(
                f"push() expects a Screen instance, got {type(screen).__name__!r}; "
                f"write app.push(MyScreen()) not app.push({screen!r})"
            )
        if screen in self._stack:
            raise PyMobileError(
                f"screen {screen.title!r} is already on the stack",
                hint="Create new instance: app.push(SettingsScreen())",
            )
        screen._app = self._app
        # Build the widget tree BEFORE lifecycle hooks so on_mount/on_show
        # can safely access widgets created in build() (e.g. self.label).
        try:
            screen.root  # noqa: B018 - builds and validates the tree
        except BaseException:
            screen._app = None
            raise

    def _show_new(self, screen: ScreenT) -> ScreenT:
        """Put an already prepared screen on top and run its lifecycle hooks."""
        self._stack.append(screen)
        if not screen._mounted:
            screen._mounted = True
            screen._ever_mounted = True
            screen.on_mount()
        screen.on_show()
        _log.debug("show %s (depth=%d)", screen.title, self.depth)
        self._notify()
        return screen

    @staticmethod
    def _discard(screen: Screen) -> None:
        """Unmount a screen that has already been removed from the stack."""
        screen._mounted = False
        screen.on_unmount()
        screen._cancel_subscriptions()
        screen._app = None

    def push(self, screen: ScreenT) -> ScreenT:
        """Show ``screen`` on top of the stack."""
        self._prepare(screen)
        previous = self.current
        if previous is not None:
            previous.on_hide()
        return self._show_new(screen)

    def pop(self) -> Screen | None:
        """Remove the top screen and reveal the one below it.

        The root screen is never popped — ``pop()`` returns ``None`` when
        already at the root. It is safe to call ``pop()`` multiple times:
        extra calls on the root are no-ops returning ``None``, not errors.

        Example::

            app.pop()  # goes back
            app.pop()  # safe even if already at root, returns None
        """
        if len(self._stack) <= 1:
            return None
        screen = self._stack.pop()
        screen.on_hide()
        screen._mounted = False
        # Run the user hook first: it is the documented place for farewell
        # work, and it must still see screen-owned subscriptions alive so
        # ``screen.on(...)`` callbacks fired from the hook are delivered.
        # Cancelling the subscriptions afterwards is safe because no later
        # hook will run on this screen.
        screen.on_unmount()
        screen._cancel_subscriptions()
        screen._app = None
        current = self.current
        if current is not None:
            current.on_show()
        _log.debug("pop %s (depth=%d)", screen.title, self.depth)
        self._notify()
        return screen

    def replace(self, screen: ScreenT) -> ScreenT:
        """Swap the top screen for ``screen``.

        Unlike ``push()``, this does not keep the current screen in the stack —
        useful for login → home transitions where you don't want back to go to login.

        Example::

            # After login, replace login screen with home
            app.navigator.replace(HomeScreen())

            # Or via App shortcut
            app.replace(HomeScreen())
        """
        self._prepare(screen)
        if self._stack:
            top = self._stack.pop()
            top.on_hide()
            self._discard(top)
        return self._show_new(screen)

    def reset(self, screen: ScreenT) -> ScreenT:
        """Clear the stack and start again from ``screen``.

        Useful for logout or switching user accounts — all screens are unmounted
        and the stack starts fresh.

        Example::

            # On logout, reset to login screen
            app.navigator.reset(LoginScreen())
        """
        self._prepare(screen)
        self.dispose()
        return self._show_new(screen)

    def dispose(self) -> None:
        """Hide and unmount every screen, releasing lifecycle resources.

        The visible screen is hidden once, then the stack is unmounted from top
        to bottom. App shutdown uses the same path as reset so subscriptions
        and screen-owned resources cannot survive either operation.
        """
        current = self.current
        if current is not None:
            current.on_hide()
        while self._stack:
            self._discard(self._stack.pop())

    def _notify(self) -> None:
        """Tell the app that the visible screen changed."""
        if self._on_change is not None:
            self._on_change(self.current)
