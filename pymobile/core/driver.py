"""Drive a running app the way a user does: open a screen, press, type, go back.

The same small engine serves two audiences:

* ``pymobile preview --screen ResultScreen`` / ``--navigate "Menu.start"`` render
  a screen that is not the first one, and
* :mod:`pymobile.testing` puts it behind pytest fixtures, so a test can walk an
  app to a screen and assert on it.

Everything goes through :meth:`App.handle_ui_event <pymobile.core.app.App.handle_ui_event>` —
the funnel the phone, the Tk window and the browser preview use — so a "press"
here runs exactly the code path of a tap. Mistakes (an unknown screen, a widget
id that is not on the screen, a step that lands somewhere else than expected)
are :class:`~pymobile.errors.PyMobileError`\\ s with a hint, not silent no-ops.

A step of a route is ``[Screen.]widget[=value]``::

    Menu.start                 press ``start`` while ``Menu`` is the current screen
    name=Ann                   type "Ann" into ``name`` (or pick / set a value)
    dark=true                  switch ``dark`` on
    <back>                     the hardware back button
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from ..errors import PyMobileError
from .ui.screen import Screen

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import App
    from .ui.widget import Widget

__all__ = ["Driver", "Step", "parse_steps", "screen_classes", "find_screen_class"]

#: Words a switch or checkbox is set with (``dark=true``); anything else is a value.
_ON = frozenset({"true", "on", "yes", "1"})
_OFF = frozenset({"false", "off", "no", "0"})
BACK = "<back>"


# --------------------------------------------------------------------------
# finding screens
# --------------------------------------------------------------------------
def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def screen_classes(*, project_only: bool = True) -> list[type[Screen]]:
    """Every :class:`Screen` subclass defined so far, one per qualified name.

    A hot reload (``pymobile watch``) defines the project's classes again; the
    newest definition of a name wins so the reloaded screen is the one found.
    ``project_only`` leaves out the classes of the framework itself.
    """
    found: dict[str, type[Screen]] = {}
    queue: list[type[Screen]] = list(Screen.__subclasses__())
    while queue:
        cls = queue.pop(0)
        queue.extend(cls.__subclasses__())
        found[_qualified(cls)] = cls
    classes = list(found.values())
    if project_only:
        classes = [cls for cls in classes if not cls.__module__.startswith("pymobile.")]
    return classes


def find_screen_class(
    name: str, candidates: Iterable[type[Screen]] | None = None
) -> type[Screen]:
    """The :class:`Screen` subclass called ``name`` (``Result``, ``screens.Result``).

    Raises :class:`PyMobileError` listing the screens that exist when there is
    no such class, or several with that name (say the module to pick one).
    """
    pool = list(candidates) if candidates is not None else screen_classes(project_only=False)
    exact = [cls for cls in pool if name in (_qualified(cls), cls.__name__)]
    if not exact:
        exact = [cls for cls in pool if _qualified(cls).endswith("." + name)]
    if len(exact) > 1 and candidates is None:
        # A screen of the framework (or a stale class that has not been garbage
        # collected yet) must not shadow the project's own screen of that name.
        own = [cls for cls in exact if not cls.__module__.startswith("pymobile.")]
        if len(own) == 1:
            return own[0]
    if len(exact) == 1:
        return exact[0]

    project = pool
    if candidates is None:  # tell the user about their screens, not the framework's own
        project = [cls for cls in pool if not cls.__module__.startswith("pymobile.")] or pool
    if len(exact) > 1:
        options = ", ".join(sorted(_qualified(cls) for cls in exact))
        raise PyMobileError(
            f"more than one screen is called {name!r}: {options}",
            hint="Use the module-qualified name, e.g. screens.ResultScreen.",
        )
    names = sorted({cls.__name__ for cls in project})
    close = get_close_matches(name, names, n=1, cutoff=0.5)
    suggestion = f" Did you mean {close[0]!r}?" if close else ""
    listing = ", ".join(names) if names else "none were found"
    raise PyMobileError(
        f"no screen called {name!r}.{suggestion}",
        hint=f"Screens in this project: {listing}.",
    )


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Step:
    """One step of a route: what to do, on which widget, and where."""

    text: str
    target: str  # widget id, or BACK
    screen: str | None = None  # the screen the step expects to be on
    value: str | None = None  # None: press; otherwise set this value


def parse_steps(route: str | Sequence[str], known_screens: Iterable[str] = ()) -> list[Step]:
    """Turn ``"Menu.start, name=Ann"`` (or a list of such strings) into steps.

    The part before the first dot names a screen only when a screen of that name
    exists (``known_screens``) — widget ids may contain dots themselves.
    """
    raw = re.split(r"[,\n]", route) if isinstance(route, str) else list(route)
    screens = set(known_screens)
    steps: list[Step] = []
    for item in raw:
        text = item.strip()
        if not text:
            continue
        if text == BACK:
            steps.append(Step(text, BACK))
            continue
        expression, sign, value = text.partition("=")
        screen: str | None = None
        head, dot, tail = expression.partition(".")
        if dot and head in screens and tail:
            screen, expression = head, tail
        target = expression.strip()
        if not target:
            raise PyMobileError(
                f"empty step in the route {route!r}",
                hint='A step is [Screen.]widget or [Screen.]widget=value, e.g. "Menu.start".',
            )
        steps.append(Step(text, target, screen, value if sign else None))
    if not steps:
        raise PyMobileError(
            f"the route {route!r} has no steps",
            hint='Give at least one step, e.g. --navigate "Menu.start".',
        )
    return steps


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------
class Driver:
    """Operate a running :class:`~pymobile.core.app.App` like a user would.

    ::

        driver = Driver(app)
        driver.open("ResultScreen", score=7)        # push a screen by name
        driver.navigate("Menu.start, Quiz.next")     # press start, then next
        driver.change("name", "Ann")                 # type / pick / set a value
        driver.press("submit")
        assert driver.screen.title == "Done"

    ``screens`` narrows which classes a screen *name* may refer to (by default:
    every :class:`Screen` subclass in the process).
    """

    def __init__(self, app: App, *, screens: Iterable[type[Screen]] | None = None) -> None:
        self.app = app
        self._screens = list(screens) if screens is not None else None

    @classmethod
    def attach(cls, *, screens: Iterable[type[Screen]] | None = None) -> Driver:
        """A driver for the app running in this process (``App.current()``).

        For code that builds its own ``App`` — call ``main()`` (or import the
        entry point), then ``Driver.attach()`` to walk the app it started.
        """
        from .app import App

        app = App.current()
        if app is None:
            raise PyMobileError(
                "no PyMobile app is running in this process",
                hint="Start one first (app.run(FirstScreen()), or call your main()).",
            )
        return cls(app, screens=screens)

    # -- what is on screen -----------------------------------------------------
    @property
    def screen(self) -> Screen:
        """The current screen; an error when the app is not running one."""
        current = self.app.screen
        if current is None:
            raise PyMobileError(
                "the app has no current screen",
                hint="Start it first: app.run(FirstScreen()).",
            )
        return current

    def find(self, widget_id: str) -> Widget:
        """The widget with this id on the current screen.

        Raises :class:`~pymobile.errors.WidgetNotFoundError` — with the closest
        ids that do exist — when there is none.
        """
        return self.screen.get(widget_id)

    def tree(self) -> dict[str, Any]:
        """The current screen as the renderer receives it (and draw it once)."""
        tree = self.app.render()
        return tree if tree is not None else self.screen.to_dict()

    # -- moving ----------------------------------------------------------------
    def open(self, target: str | type[Screen] | Screen, *args: Any, **kwargs: Any) -> Screen:
        """Push a screen — by class name, class or instance — and return it.

        Arguments go to the constructor: ``open("Result", score=7)``.
        """
        if isinstance(target, Screen):
            if args or kwargs:
                raise PyMobileError("arguments only apply when the screen is opened by class")
            instance = target
        else:
            cls = (
                find_screen_class(target, self._screens)
                if isinstance(target, str)
                else target
            )
            try:
                instance = cls(*args, **kwargs)
            except TypeError as error:
                raise PyMobileError(
                    f"cannot create {cls.__name__}: {error}",
                    hint=(
                        "Pass what its constructor needs — from the command line with "
                        "--set score=7 (repeatable, no quoting needed) or as JSON: "
                        '--args \'{"score": 7}\' (an object is keywords, a list is positional).'
                    ),
                ) from error
        if self.app.screen is None:
            raise PyMobileError(
                "the app is not running, so there is nothing to open a screen on",
                hint="Start it first: app.run(FirstScreen()).",
            )
        return self.app.push(instance)

    def back(self) -> None:
        """Press the hardware back button (pops a screen; on the root, stops the app)."""
        self.app.handle_ui_event("", "back", "")

    # -- interacting -----------------------------------------------------------
    def _usable(self, widget_id: str, action: str) -> Widget:
        widget = self.find(widget_id)
        if not widget.enabled:
            raise PyMobileError(
                f"cannot {action} {widget_id!r}: the widget is disabled",
                hint="A user cannot tap a disabled widget either; enable it first.",
            )
        return widget

    def press(self, widget_id: str) -> None:
        """Tap a widget (a button, a chip, a list row, a link…)."""
        widget = self._usable(widget_id, "press")
        if not hasattr(widget, "press"):
            raise PyMobileError(
                f"{widget_id!r} is a {type(widget).__name__}, which cannot be pressed",
                hint="Only widgets with a press() — Button, Chip, ListTile, Link… — take a tap.",
            )
        self.app.handle_ui_event(widget_id, "press", "")

    def long_press(self, widget_id: str) -> None:
        """Long-press a widget (list rows)."""
        widget = self._usable(widget_id, "long-press")
        if not hasattr(widget, "long_press"):
            raise PyMobileError(f"{widget_id!r} has no long-press action")
        self.app.handle_ui_event(widget_id, "long_press", "")

    def change(self, widget_id: str, value: object) -> None:
        """Type into, pick from or set a widget: TextInput, Dropdown, Slider, Switch…

        A Switch or Checkbox takes ``True``/``False`` (or ``"on"``/``"off"``…);
        everything else is sent as the text the front end would send.
        """
        widget = self._usable(widget_id, "change")
        text = str(value).strip().lower() if isinstance(value, (str, bool)) else None
        if hasattr(widget, "set_checked") and text in (_ON | _OFF):
            self.app.handle_ui_event(widget_id, "toggle", "true" if text in _ON else "false")
        elif isinstance(value, bool):
            self.app.handle_ui_event(widget_id, "change", "true" if value else "false")
        else:
            self.app.handle_ui_event(widget_id, "change", str(value))

    # -- routes ----------------------------------------------------------------
    def navigate(self, route: str | Sequence[str]) -> list[Screen]:
        """Run a route (``"Menu.start, Quiz.next"``); return the screen after each step.

        A ``Screen.`` prefix asserts where the step is taken, so a route that
        drifted (an earlier press went somewhere new) fails at the step where
        it matters instead of pressing the wrong widget three steps later.
        """
        known = {cls.__name__ for cls in (self._screens or screen_classes(project_only=False))}
        landed: list[Screen] = []
        for step in parse_steps(route, known):
            if step.target == BACK:
                self.back()
            else:
                if step.screen is not None:
                    self._expect_screen(step)
                if step.value is None:
                    self.press(step.target)
                else:
                    self.change(step.target, step.value)
            if not self.app.running:
                raise PyMobileError(
                    f"the app stopped at step {step.text!r}",
                    hint="<back> on the root screen exits the app; stop the route before it.",
                )
            landed.append(self.screen)
        return landed

    def _expect_screen(self, step: Step) -> None:
        current = self.screen
        names = {type(current).__name__, _qualified(type(current))}
        if step.screen not in names:
            raise PyMobileError(
                f"step {step.text!r} expects the screen {step.screen}, "
                f"but the current screen is {type(current).__name__}",
                hint="Steps run in order; an earlier step probably did not go where you expect.",
            )
