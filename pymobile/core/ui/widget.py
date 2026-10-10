"""Widget foundation.

A widget is a declarative description of a piece of UI, not a native view.
Trees are serialised with :meth:`Widget.to_dict` and handed to the bridge,
which owns the native rendering. Keeping widgets pure data makes the UI layer
trivially testable and lets the native renderer evolve independently.

Mutating a widget that is on screen schedules a re-render by itself: every
setter funnels through :meth:`Widget.invalidate`, which walks up to the screen
and asks the application to redraw. Forgetting ``app.render()`` is therefore no
longer a class of bug — see :mod:`pymobile.core.app` for how the redraws are
coalesced.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import get_close_matches
from itertools import count
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ...errors import WidgetNotFoundError, WidgetParentError, WidgetTypeError
from ...log import get_logger
from .contract import SerializedValue, WidgetNode, WidgetProps
from .registry import widget_types
from .style import Style

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .screen import Screen

__all__ = ["Widget", "Container", "auto_id", "reset_id_counter", "widget_scope"]

#: A concrete widget class, for the typed lookups (``find(id, Label) -> Label``).
W = TypeVar("W", bound="Widget")

_log = get_logger("ui.widget")


def _error_node(widget_id: str, type_name: str, exc: BaseException) -> WidgetNode:
    """A red placeholder standing in for a widget that failed to render."""
    return {
        "type": "Label",
        "id": widget_id or "error",
        "visible": True,
        "enabled": False,
        "props": {"text": f"⚠ {type_name}: {type(exc).__name__}: {exc}"},
        "style": {"color": "#F44336"},
    }

#: Fallback counter, used when no screen scope is active.
_ids = count(1)

#: ``(widget class, prop name)`` pairs already reported as unknown props, so a
#: typo is mentioned once instead of on every frame.
_warned_stray_props: set[tuple[str, str]] = set()

#: Per-screen counters, so ids do not shift when an unrelated screen changes.
#: A ContextVar keeps concurrent builds (threads, tests) from sharing state.
_scope: ContextVar[dict[str, count[int]] | None] = ContextVar("pymobile_widget_scope", default=None)


def reset_id_counter() -> None:
    """Restart the anonymous-widget counter (used by tests)."""
    global _ids
    _ids = count(1)


def auto_id(prefix: str) -> str:
    """Return a unique id of the form ``prefix-N``.

    Inside a :func:`widget_scope` the number is local to that screen and
    restarts per widget type, so adding a Label at the top of one screen no
    longer renumbers every widget in the application.
    """
    counters = _scope.get()
    if counters is None:
        # Outside build() (a widget made in Screen.__init__, a helper, a
        # test) the id gets its own namespace. It used to be ``label-1`` too,
        # colliding with the first Label built inside the screen: "duplicate
        # widget id 'label-1'".
        return f"{prefix}-g{next(_ids)}"
    counter = counters.get(prefix)
    if counter is None:
        counter = count(1)
        counters[prefix] = counter
    return f"{prefix}-{next(counter)}"


@contextmanager
def widget_scope(owner: object = None) -> Iterator[None]:
    """Number widgets built inside the block per screen instead of globally."""
    token = _scope.set({})
    try:
        yield
    finally:
        _scope.reset(token)


def _serialise_value(value: object) -> SerializedValue:
    """Validate extension props at the public renderer boundary.

    The returned value is always a **deep copy**: callers can mutate it
    without surprising the widget that produced it.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _serialise_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise_value(item) for item in value]
    raise TypeError(
        f"widget extension props must contain only JSON-like values (got {type(value).__name__})"
    )


def _copy_props(props: Mapping[str, object]) -> WidgetProps:
    """Return a deep copy of the internal ``_props`` mapping.

    ``Widget.props()`` used to hand out a shallow ``dict(self._props)``,
    which leaked references to nested dict/list extension props. A native
    renderer that mutated them in place could corrupt widget state. Now the
    nested values are cloned so the renderer can do whatever it likes with
    the returned mapping.
    """
    return {key: _serialise_value(value) for key, value in props.items()}


_SETTERS: dict[type, frozenset[str]] = {}


def _setter_names(cls: type) -> frozenset[str]:
    """Names of properties on ``cls`` (they handle redraws themselves)."""
    names = _SETTERS.get(cls)
    if names is None:
        names = frozenset(
            name
            for klass in cls.__mro__
            for name, member in vars(klass).items()
            if isinstance(member, property)
        )
        _SETTERS[cls] = names
    return names


class Widget:
    """Base class for every UI component."""

    #: Type name used in the serialised tree; defaults to the class name.
    type_name: str = "Widget"

    __slots__ = (
        "id",
        "style",
        "_visible",
        "_enabled",
        "_parent",
        "_props",
        "_screen",
        "_explicit_id",
        "_animate_visibility",
        "_animation_duration_ms",
        "_accessibility_label",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        style: Style | None = None,
        visible: bool = True,
        enabled: bool = True,
        animate_visibility: bool = False,
        animation_duration_ms: int = 220,
        accessibility_label: str | None = None,
        **props: Any,
    ) -> None:
        if accessibility_label is not None and not isinstance(accessibility_label, str):
            raise TypeError("accessibility_label must be a string or None")
        self._explicit_id = id is not None
        self.id = id or auto_id(type(self).__name__.lower())
        self.style = style or Style()
        if not isinstance(animate_visibility, bool):
            raise TypeError("animate_visibility must be a bool")
        if (
            isinstance(animation_duration_ms, bool)
            or not isinstance(animation_duration_ms, int)
            or not 0 <= animation_duration_ms <= 5000
        ):
            raise ValueError("animation_duration_ms must be an int from 0 to 5000")
        self._visible = visible
        self._enabled = enabled
        self._animate_visibility = animate_visibility
        self._animation_duration_ms = animation_duration_ms
        self._accessibility_label = accessibility_label
        self._parent: Widget | None = None
        self._screen: Screen | None = None
        self._props: WidgetProps = {}
        for name, value in props.items():
            self.set_prop(name, value, invalidate=False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Redraw when a public attribute changes.

        Stateful values with their own property setter (``text``, ``value``,
        ``visible`` …) already schedule a redraw. Plain attributes —
        ``placeholder``, ``ListTile.subtitle``, ``Badge.color``, a new ``style``
        object, ``ProgressText.label`` … — used to change silently until
        something else triggered a render. Mutating a mutable value in place
        (``widget.style.bold = True``) still needs :meth:`invalidate`.
        """
        if name[0] == "_" or name in _setter_names(type(self)):
            object.__setattr__(self, name, value)
            return
        try:
            old = object.__getattribute__(self, name)
        except AttributeError:  # first assignment, from __init__
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)
        if old is value:
            return
        try:
            changed = bool(old != value)
        except Exception:  # exotic __eq__: assume it changed
            changed = True
        if changed:
            self.invalidate()

    # -- tree --------------------------------------------------------------
    @property
    def parent(self) -> Widget | None:
        """The widget this one is attached to, if any."""
        return self._parent

    @property
    def children(self) -> Sequence[Widget]:
        """Child widgets; leaf widgets return an empty tuple."""
        return ()

    def walk(self) -> Iterator[Widget]:
        """Depth-first iteration over this widget and its descendants."""
        yield self
        for child in self.children:
            yield from child.walk()

    @overload
    def find(self, widget_id: str) -> Widget | None: ...

    @overload
    def find(self, widget_id: str, kind: type[W]) -> W | None: ...

    def find(self, widget_id: str, kind: type[Widget] | None = None) -> Widget | None:
        """Find a descendant (or self) by id; ``None`` when there is none.

        Pass the widget class as ``kind`` and the result is typed as that
        class, so editors and type checkers know its methods without a cast::

            title = self.find("title", Label)     # Label | None
            if title is not None:
                title.text = "Hi"

        A widget with that id but another type raises
        :class:`~pymobile.errors.WidgetTypeError` instead of handing back the
        wrong object.
        """
        widget = next((node for node in self.walk() if node.id == widget_id), None)
        if widget is not None and kind is not None:
            _check_kind(widget, widget_id, kind)
        return widget

    @overload
    def get(self, widget_id: str) -> Widget: ...

    @overload
    def get(self, widget_id: str, kind: type[W]) -> W: ...

    def get(self, widget_id: str, kind: type[Widget] | None = None) -> Widget:
        """Like :meth:`find`, but the widget must exist — never ``None``.

        ``self.get("title", Label).text = "Hi"`` needs no ``None`` check. A
        missing id raises :class:`~pymobile.errors.WidgetNotFoundError` naming
        the closest ids that do exist (a typo is the usual cause).
        """
        widget = self.find(widget_id) if kind is None else self.find(widget_id, kind)
        if widget is None:
            raise WidgetNotFoundError(widget_id, hint=_not_found_hint(self, widget_id))
        return widget

    def find_all(self, kind: type[W]) -> list[W]:
        """Every descendant (and self) that is an instance of ``kind``, in tree order.

        ``self.find_all(TextInput)`` — all inputs of a form, typed as such.
        """
        return [node for node in self.walk() if isinstance(node, kind)]

    # -- reactivity --------------------------------------------------------
    @property
    def screen(self) -> Screen | None:
        """The screen this widget belongs to, if it is attached to one.

        The link is stored on the root widget only, so this walks up the
        parent chain — a handful of pointer hops on trees of realistic depth.
        """
        node: Widget = self
        while node._parent is not None:
            node = node._parent
        return node._screen

    def invalidate(self) -> None:
        """Mark this widget as changed and schedule a redraw.

        Safe to call at any time: while the widget is detached, during
        ``build()``, or before the app is running. In those cases there is
        nothing on screen yet and the call does nothing.
        """
        try:
            screen = self.screen
        except AttributeError:  # still inside __init__
            return
        if screen is not None:
            screen.invalidate()

    # -- properties --------------------------------------------------------
    @property
    def visible(self) -> bool:
        """Whether the widget is drawn; hidden widgets keep their place free."""
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        if value != self._visible:
            self._visible = value
            self.invalidate()

    @property
    def animate_visibility(self) -> bool:
        """Whether visibility changes fade/scale on Android (default: false)."""
        return self._animate_visibility

    @animate_visibility.setter
    def animate_visibility(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("animate_visibility must be a bool")
        if value != self._animate_visibility:
            self._animate_visibility = value
            self.invalidate()

    @property
    def animation_duration_ms(self) -> int:
        """Duration of this widget's visibility animation, from 0 to 5000 ms."""
        return self._animation_duration_ms

    @animation_duration_ms.setter
    def animation_duration_ms(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5000:
            raise ValueError("animation_duration_ms must be an int from 0 to 5000")
        if value != self._animation_duration_ms:
            self._animation_duration_ms = value
            self.invalidate()

    @property
    def enabled(self) -> bool:
        """Whether the widget reacts to input."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if value != self._enabled:
            self._enabled = value
            self.invalidate()

    @property
    def accessibility_label(self) -> str | None:
        """A concise screen-reader name for this widget, when its text is not enough."""
        return self._accessibility_label

    @accessibility_label.setter
    def accessibility_label(self, value: str | None) -> None:
        if value is not None and not isinstance(value, str):
            raise TypeError("accessibility_label must be a string or None")
        if value != self._accessibility_label:
            self._accessibility_label = value
            self.invalidate()

    def set_prop(self, name: str, value: object, *, invalidate: bool = True) -> None:
        """Set a validated extension prop for a custom widget/renderer.

        Extension props are deliberately JSON-like, so every renderer receives
        the same portable data rather than a Python callback or host object.
        """
        if not name or not name.isidentifier() or name.startswith("_"):
            raise ValueError("widget extension prop names must be public identifiers")
        try:
            serialised = _serialise_value(value)
        except TypeError as error:
            # The usual cause is a misspelled argument (`Button("b",
            # on_pres=…)`): the key is not a widget prop, so it is stored as an
            # extension prop, and a function is not one.
            raise TypeError(
                f"{error}. {name!r} is not a prop of {type(self).__name__}: extra keyword "
                "arguments become extension props and must be JSON-like — check the "
                "spelling of the argument name."
            ) from error
        if self._props.get(name) != serialised:
            self._props[name] = serialised
            if invalidate:
                self.invalidate()

    def props(self) -> WidgetProps:
        """Serialisable properties of this widget; subclasses extend this.

        The returned mapping is a deep copy so renderers can mutate nested
        dict/list extension props without leaking back into the widget.
        """
        result = _copy_props(self._props)
        if self._accessibility_label is not None:
            result["accessibility_label"] = self._accessibility_label
        return result

    def to_dict(self) -> WidgetNode:
        """Serialise the widget subtree into the public renderer contract.

        A widget that raises while serialising is replaced by red error text
        naming the failure, and its siblings keep rendering. The device
        renderer already behaves this way; doing it here means the desktop
        preview, the browser preview and the snapshot tests agree with the
        phone instead of losing the whole screen to one broken widget.
        """
        try:
            props = self.props()
        except Exception as exc:
            _log.exception("widget %s (%s) failed to serialise", self.id, self.type_name)
            return _error_node(self.id, self.type_name, exc)
        self._warn_stray_props(props)
        node: WidgetNode = {
            "type": self.type_name,
            "id": self.id,
            "visible": self._visible,
            "enabled": self._enabled,
            "props": props,
        }
        try:
            style = self.style.to_dict()
        except Exception:  # pragma: no cover - defensive
            style = {}
        if style:
            node["style"] = style
        if self._animate_visibility:
            node["animation"] = {
                "visibility": "fade_scale",
                "duration_ms": self._animation_duration_ms,
            }
        children = []
        for child in self.children:
            try:
                children.append(child.to_dict())
            except Exception as exc:  # a child that cannot even report its own failure
                _log.exception("child of %s failed to serialise", self.id)
                children.append(_error_node(getattr(child, "id", "?"), "Widget", exc))
        if children:
            node["children"] = children
        return node

    def _warn_stray_props(self, rendered: Mapping[str, object]) -> None:
        """Name keyword arguments this built-in widget does not have.

        Extra keywords are stored as *extension props* for custom renderers, so
        they are accepted silently — but on a built-in widget they are almost
        always a typo: ``Label("hi", colr="red")`` rendered a black label and
        said nothing, because the renderer had no idea what ``colr`` was. Custom
        widget types are exempt: extension props are their mechanism.
        """
        if not self._props or self.type_name not in widget_types():
            return
        native = set(rendered) - set(self._props)
        try:
            parameters = set(inspect.signature(type(self).__init__).parameters)
        except (TypeError, ValueError):  # pragma: no cover - builtins / C classes
            parameters = set()
        candidates = sorted(native | parameters)
        for name in sorted(self._props):
            marker = (type(self).__name__, name)
            if marker in _warned_stray_props:
                continue
            _warned_stray_props.add(marker)
            close = get_close_matches(name, candidates, n=1, cutoff=0.7)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            _log.warning(
                "%s has no prop %r: it was stored as an extension prop, which the "
                "renderers ignore.%s",
                type(self).__name__,
                name,
                hint,
            )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} id={self.id!r}>"


class Container(Widget):
    """A widget that owns and lays out children."""

    type_name = "Container"

    __slots__ = ("_children",)

    def __init__(self, *children: Widget, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._children: list[Widget] = []
        self.extend(children)

    @property
    def children(self) -> Sequence[Widget]:
        """Immutable view over the child list."""
        return tuple(self._children)

    def add(self, child: Widget) -> Widget:
        """Append a child and return it (so calls can be chained/assigned)."""
        if child is None:
            raise TypeError(
                f"{type(self).__name__} received None as a child; "
                "every child must be a Widget instance. "
                "Check for conditional expressions like "
                "'Label(\'x\') if condition else None' \u2014 use 'visible' instead."
            )
        if child is self:
            raise ValueError("a container cannot contain itself")
        # Reject attaching an ancestor below its own descendant. Without this,
        # ``root.add(child); child.add(root)`` forms a cycle and recursion in
        # walk()/to_dict() never terminates.
        ancestor: Widget | None = self
        while ancestor is not None:
            if ancestor is child:
                raise ValueError("a container cannot contain one of its ancestors")
            ancestor = ancestor._parent
        if child._parent is not None:
            # A widget arriving in the tree twice is almost always a side
            # effect of how the framework owns the tree (Screen.build() /
            # Screen.refresh() attaching widgets you also stashed on ``self``),
            # so the error carries a hint. It is also a ValueError, like every
            # other rejection in this method, so existing ``except ValueError``
            # clauses keep working.
            raise WidgetParentError(
                child.id,
                hint=(
                    "A widget can only live in one place. Build the tree from scratch in "
                    "Screen.build(); widgets stored on the screen are detached for you by "
                    "Screen.refresh(). Use parent.remove(child) to detach one by hand."
                ),
            )
        # A live add() is checked against the screen's current ids: the
        # initial-build check cannot see widgets added later, and duplicates
        # silently clobber each other in the native renderer. Nothing is
        # attached when the check fails, so a rejected add() leaves the tree
        # exactly as it was.
        screen = self.screen
        if screen is not None:
            screen._check_new_ids(child)
        child._parent = self
        self._children.append(child)
        self.invalidate()
        return child

    def extend(self, children: Sequence[Widget]) -> None:
        """Append several children."""
        for child in children:
            self.add(child)

    def remove(self, child: Widget) -> None:
        """Detach a child (no error if it is not present)."""
        if child in self._children:
            self._children.remove(child)
            child._parent = None
            self.invalidate()

    def clear(self) -> None:
        """Detach every child."""
        if not self._children:
            return
        for child in self._children:
            child._parent = None
        self._children.clear()
        self.invalidate()

    def __len__(self) -> int:
        return len(self._children)

    def __iter__(self) -> Iterator[Widget]:
        return iter(self._children)


def _check_kind(widget: Widget, widget_id: str, kind: type[Widget]) -> None:
    if not isinstance(kind, type):
        raise TypeError(f"kind must be a widget class, got {kind!r}")
    if not isinstance(widget, kind):
        raise WidgetTypeError(widget_id, kind, type(widget))


def _not_found_hint(root: Widget, widget_id: str) -> str:
    """Suggest the ids closest to a missing one."""
    ids = [node.id for node in root.walk()]
    close = get_close_matches(widget_id, ids, n=3, cutoff=0.5)
    if close:
        return "Did you mean " + ", ".join(repr(name) for name in close) + "?"
    return (
        "Give the widget an explicit id (Label('…', id=\"title\")); anonymous "
        "widgets get generated ids such as 'label-1'."
    )


def in_build_scope() -> bool:
    """Whether we are currently inside a Screen.build() call.

    Used to suppress on_change/on_select callbacks during build() (BUG-25):
    firing them while the tree is still being constructed would let a handler
    access widgets that haven't been created yet.
    Outside build() (standalone widgets, tests, user interactions) callbacks
    fire normally.
    """
    return _scope.get() is not None


def callback_name(handler: Callable[..., Any] | None) -> str | None:
    """Stable identifier for a callback, used when serialising handlers."""
    if handler is None:
        return None
    return getattr(handler, "__name__", repr(handler))
