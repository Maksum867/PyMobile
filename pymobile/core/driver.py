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
    @Stats                     pick the "Stats" tab of the bottom navigation
    Shell.@Stats               …and assert that the step is taken on Shell
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import PyMobileError
from ..log import get_logger
from .ui.screen import Screen

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import App
    from .ui.widget import Widget

__all__ = [
    "Driver",
    "Step",
    "parse_steps",
    "screen_classes",
    "find_screen_class",
    "discover_screens",
]

#: Words a switch or checkbox is set with (``dark=true``); anything else is a value.
_ON = frozenset({"true", "on", "yes", "1"})
_OFF = frozenset({"false", "off", "no", "0"})
BACK = "<back>"

_log = get_logger("driver")


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


#: Directories never searched for screen modules: build output, caches and the
#: Java/asset folders have nothing a ``Screen`` subclass could live in.
_SKIPPED_PARTS = frozenset({"__pycache__", "build", "dist", "java", "jni", "prebuilt"})


def _defines_a_screen(tree: ast.AST) -> bool:
    """Whether a module syntactically defines a ``…Screen`` class.

    The check is deliberately syntactic: importing every project module to find
    out would run their top-level code, and the reason a screen is missing is
    usually that the module holding it was never imported at all.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
            if isinstance(name, str) and name.endswith("Screen"):
                return True
    return False


def discover_screens(
    paths: Iterable[str | Path], *, exclude: Iterable[str | Path] = ()
) -> list[type[Screen]]:
    """Import project modules that define a ``Screen`` and return all screens.

    ``pymobile preview --screen ResultScreen`` used to see only the screens the
    entry point had already imported — a screen imported lazily (or, commonly,
    in a module the entry point never touches) was reported as "no screen
    called 'ResultScreen'". Every ``.py`` under ``paths`` that syntactically
    defines a ``…Screen`` class is imported, so the class exists by the time
    the name is looked up.

    Returns the screen classes that were not visible before the call. Modules
    that fail to import are skipped (their error is logged at debug level): a
    broken helper in an unrelated folder must not break the preview.
    """
    before = {id(cls) for cls in screen_classes(project_only=False)}
    # The entry point has already been executed as ``__main__``: importing the
    # same file again would define every screen a second time and make names
    # ambiguous. Callers pass it in ``exclude``.
    skipped = {
        str(Path(item).expanduser().resolve()) for item in exclude
    }
    roots: list[Path] = []
    for raw in paths:
        root = Path(raw).expanduser().resolve()
        if root.is_file():
            root = root.parent
        if root.is_dir() and root not in roots:
            roots.append(root)
    for root in roots:
        candidates = sorted(root.rglob("*.py"))
        for path in candidates:
            relative = path.relative_to(root)
            if any(part in _SKIPPED_PARTS or part.startswith(".") for part in relative.parts):
                continue
            if str(path.resolve()) in skipped:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            if not _defines_a_screen(tree):
                continue
            _import_project_module(root, path)
    return [cls for cls in screen_classes(project_only=False) if id(cls) not in before]


def _import_project_module(root: Path, path: Path) -> None:
    """Import one file as a module of the project rooted at ``root``."""
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return
    name = ".".join(parts)
    # ``import_module`` caches by *name*, and sys.path keeps every root we have
    # ever been asked about. Two projects (or two test directories) with a file
    # of the same name therefore collided: the second lookup returned the first
    # project's module, so its screens were never discovered and the preview
    # said "no screen called ..." for a class that plainly exists.
    same_file = _module_is(path)

    def owner_is_this_file() -> bool:
        existing = sys.modules.get(name)
        return existing is not None and same_file(existing)

    if owner_is_this_file():
        return  # this very file is already imported
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        importlib.invalidate_caches()
        if name in sys.modules:
            # A different file already owns this name: import this one under an
            # alias instead of silently reusing the other project's module.
            alias = f"{name}__{_path_token(path)}"
            spec = importlib.util.spec_from_file_location(alias, path)
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[alias] = module
            spec.loader.exec_module(module)
            return
        importlib.import_module(name)
    except Exception as error:  # a broken module must not break the preview
        _log.debug("could not import %s while looking for screens: %s", path, error)


def _module_is(path: Path) -> Callable[[object], bool]:
    """A predicate telling whether a module object was loaded from ``path``."""
    wanted = path.resolve()

    def matches(module: object) -> bool:
        origin = getattr(module, "__file__", "") or ""
        try:
            return bool(origin) and Path(origin).resolve() == wanted
        except OSError:  # pragma: no cover - a synthetic module
            return False

    return matches


def _path_token(path: Path) -> str:
    """A short, stable suffix that makes an aliased module name unique."""
    import hashlib

    return hashlib.blake2s(str(path.resolve()).encode("utf-8"), digest_size=4).hexdigest()


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
    target: str  # widget id, a tab name, or BACK
    screen: str | None = None  # the screen the step expects to be on
    value: str | None = None  # None: press; otherwise set this value
    #: ``True`` for ``@Stats``: pick that tab in the bottom navigation. A tab
    #: bar has no stable id in an inline tree, so it is addressed by the label
    #: or value the user sees instead.
    tab: bool = False


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
        tab = target.startswith("@")
        if tab:
            target = target[1:].strip()
            if sign:
                raise PyMobileError(
                    f"a tab step takes no value: {text!r}",
                    hint='Write "@Stats" to pick the Stats tab; tabs are chosen by '
                    "label or value, not set with =.",
                )
        if not target:
            raise PyMobileError(
                f"empty step in the route {route!r}",
                hint='A step is [Screen.]widget or [Screen.]widget=value, e.g. "Menu.start".',
            )
        steps.append(Step(text, target, screen, value if sign else None, tab))
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
        resolved = self.app._resolve_wire_id(widget_id)
        if resolved is None:
            from ..errors import WidgetNotFoundError

            raise WidgetNotFoundError(
                widget_id, hint="Stale frame; read tree() again for the current widget id."
            )
        return self.screen.get(resolved)

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
        """Tap a widget (a button, a chip, a list row, a link, a tappable card…)."""
        widget = self._usable(widget_id, "press")
        if not getattr(widget, "pressable", True) or not hasattr(widget, "press"):
            raise PyMobileError(
                f"{widget_id!r} is a {type(widget).__name__}, which cannot be pressed",
                hint=(
                    "Only widgets with a press() — Button, Chip, ListTile, Link, or a "
                    "Row/Column/Container/Card/Dialog/Stack given on_press= — take a tap."
                ),
            )
        self.app.handle_ui_event(widget_id, "press", "")

    def tab(self, target: str | int) -> Screen:
        """Pick a tab in the screen's bottom navigation; return the screen.

        The top-level tabs of an app are usually a ``BottomNavigation`` built
        inline in ``build()``, so they have no id a route could name. This
        selects them the way the user does — by the label or value shown on the
        bar, or by position — and runs the screen's own ``on_select``::

            driver.tab("Stats")     # --navigate "@Stats" on the command line
            driver.tab(2)           # the third tab

        The first matching bar is used when a screen has more than one.
        """
        screen = self.screen
        bars = [
            widget
            for widget in screen.root.walk()
            if getattr(widget, "type_name", "") == "BottomNavigation"
        ]
        if not bars:
            raise PyMobileError(
                f"{type(screen).__name__} has no BottomNavigation to pick a tab in",
                hint=(
                    "Add a BottomNavigation to the screen, or press the widget that "
                    "switches tabs with press(widget_id)."
                ),
            )
        chosen: tuple[Any, str] | None = None
        for bar in bars:
            values = [str(value) for value in getattr(bar, "options", ())]
            labels = [str(label) for label in getattr(bar, "labels", values)]
            if isinstance(target, int):
                if 0 <= target < len(values):
                    chosen = (bar, values[target])
                    break
                continue
            wanted = str(target)
            if wanted in values:
                chosen = (bar, wanted)
                break
            if wanted in labels:
                chosen = (bar, values[labels.index(wanted)])
                break
        if chosen is None:
            described = "; ".join(
                ", ".join(
                    str(label)
                    for label in getattr(bar, "labels", getattr(bar, "options", ()))
                )
                for bar in bars
            )
            raise PyMobileError(
                f"no tab called {target!r} in this screen's bottom navigation",
                hint=f"The tabs are: {described or 'none'}.",
            )
        bar, value = chosen
        self.app.handle_ui_event(bar.id, "change", value)
        return self.screen

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
        # П-09: mirror the guard in press() — a Button/Chip/Link that only
        # supports a tap must not silently swallow `id=value` routes.
        accepts_value = (
            hasattr(widget, "set_checked")  # Switch / Checkbox
            or hasattr(widget, "set_value")  # Slider, Dropdown, TextInput, …
            or hasattr(widget, "value")  # any input-style widget
            or isinstance(getattr(widget, "editable", None), bool)
        )
        if not accepts_value:
            raise PyMobileError(
                f"{widget_id!r} is a {type(widget).__name__}, which takes no value",
                hint=f"Use press({widget_id!r}) to tap it; change() is for inputs "
                "(TextInput, Dropdown, Slider, Switch, Checkbox).",
            )
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
        screen_pool = self._screens or screen_classes(project_only=False)
        known = {cls.__name__ for cls in screen_pool}
        landed: list[Screen] = []
        for step in parse_steps(route, known):
            if step.target == BACK:
                self.back()
            else:
                if step.screen is not None:
                    self._expect_screen(step)
                try:
                    if step.tab:
                        self.tab(step.target)
                    elif step.value is None:
                        self.press(step.target)
                    else:
                        self.change(step.target, step.value)
                except PyMobileError as exc:
                    # П-17: if the user wrote "Meniu.start" (typo in a screen
                    # name) the parser treated "Meniu.start" as a widget id
                    # because "Meniu" is not a known screen. Surface a screen
                    # hint in addition to whatever the widget hint says.
                    head = step.text.split(".", 1)[0] if "." in step.text else step.text
                    if step.screen is None and head not in known:
                        names = sorted({cls.__name__ for cls in screen_pool})
                        close_screen = get_close_matches(head, names, n=1, cutoff=0.6)
                        if close_screen and close_screen[0] != head:
                            tail = step.text.split(".", 1)[-1] if "." in step.text else "widget"
                            hint = (
                                (exc.hint + " ") if exc.hint else ""
                            ) + (
                                f"Did you mean the screen {close_screen[0]!r}? "
                                f"Write it as {close_screen[0]}.{tail}."
                            )
                            exc.hint = hint
                    raise
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
