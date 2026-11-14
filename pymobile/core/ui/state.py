"""Observable state: values a widget can follow instead of being rebuilt.

The framework's own story used to be "keep the state in a field of the screen
and call ``refresh()``", which rebuilds the whole tree for one changed number.
A :class:`Value` is the small piece that was missing: it holds a value, tells
whoever is interested when that value changes, and can push itself straight
into a widget::

    class Counter(Screen):
        def __init__(self) -> None:
            super().__init__()
            self.taps = Value(0)
            self.label = Label("0")
            self.taps.bind(self.label, "text", transform=lambda n: f"Taps: {n}")

        def build(self) -> Widget:
            return Column(self.label, Button("+1", on_press=self.increment))

        def increment(self) -> None:
            self.taps.value += 1        # the label redraws itself

:class:`Computed` derives a value from other values and stays correct without
anyone remembering to update it. Both are deliberately small: no widget
re-creation, no diffing, no global store — a value, its dependents, and a
subscription that returns its own unsubscribe call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from ...log import get_logger

__all__ = ["Value", "Computed"]

T = TypeVar("T")

_log = get_logger("ui.state")

#: The Computed whose function is running, so the Values it reads can be
#: recorded as its sources. One ContextVar keeps concurrent builds apart.
_tracking: ContextVar[list[_Trackable] | None] = ContextVar("pymobile_state_tracking", default=None)


class _Trackable:
    """Something that can be read by another value and notify it on change."""

    __slots__ = ("_dependents",)

    def __init__(self) -> None:
        self._dependents: list[Callable[[], None]] = []

    def _notify(self) -> None:
        for dependent in list(self._dependents):
            dependent()

    def _depend(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register ``callback``; the returned call removes it again.

        Membership is by identity: ``Value.__eq__`` compares the values inside,
        so two equal values would otherwise look like the same dependency.
        """
        if not any(existing is callback for existing in self._dependents):
            self._dependents.append(callback)

        def unsubscribe() -> None:
            with suppress(ValueError):  # already gone
                self._dependents.remove(callback)

        return unsubscribe


@contextmanager
def _collecting(sink: list[_Trackable]) -> Iterator[None]:
    """Record every Value read inside the block into ``sink``."""
    token = _tracking.set(sink)
    try:
        yield
    finally:
        _tracking.reset(token)


class Value(_Trackable, Generic[T]):
    """A value that notifies its dependents when it changes.

    ``Value(5)`` behaves like a box around ``5``: ``.value`` reads and writes it,
    ``+= 1`` works, and ``bind()`` writes it into a widget. Assigning the value
    it already holds notifies nobody, so a loop that recomputes ``0`` does not
    schedule a redraw.
    """

    __slots__ = ("_value", "_name")

    def __init__(self, value: T, *, name: str = "") -> None:
        super().__init__()
        self._value = value
        self._name = name

    # -- reading -----------------------------------------------------------
    @property
    def value(self) -> T:
        """The current value; reading it inside a :class:`Computed` links them."""
        tracked = _tracking.get()
        if tracked is not None and not any(item is self for item in tracked):
            tracked.append(self)
        return self._value

    @value.setter
    def value(self, value: T) -> None:
        """The current value; assigning it publishes the change."""
        self.set(value)

    def get(self) -> T:
        """The current value (the explicit spelling of ``.value``)."""
        return self.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        name = f" {self._name!r}" if self._name else ""
        return f"<Value{name}={self._value!r}>"

    # -- writing -----------------------------------------------------------
    def set(self, value: T) -> None:
        """Assign, notifying dependents only when the value really changed."""
        if _same(self._value, value):
            return
        self._value = value
        self._notify()

    def update(self, function: Callable[[T], T]) -> T:
        """Replace the value with ``function(current)``; return the new one."""
        new = function(self._value)
        self.set(new)
        return self._value

    def toggle(self) -> bool:
        """Flip a boolean value (``Value(False).toggle()`` → ``True``)."""
        if not isinstance(self._value, bool):
            raise TypeError("toggle() needs a bool value")
        self.set(not self._value)  # type: ignore[arg-type]
        return bool(self._value)

    def __add__(self, other: Any) -> Any:
        # ``self.taps += 1`` on a Value is a += on the *box*, so these two
        # operators make the arithmetic spellings work on the value inside.
        return self._value + other  # type: ignore[operator]

    def __radd__(self, other: Any) -> Any:
        return other + self._value  # type: ignore[operator]

    def __sub__(self, other: Any) -> Any:
        return self._value - other  # type: ignore[operator]

    def __iadd__(self, other: Any) -> Value[T]:
        self.set(self._value + other)  # type: ignore[operator]
        return self

    def __isub__(self, other: Any) -> Value[T]:
        self.set(self._value - other)  # type: ignore[operator]
        return self

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Value):
            return bool(self._value == other._value)
        return bool(self._value == other)

    def __bool__(self) -> bool:
        return bool(self._value)

    __hash__ = object.__hash__

    # -- binding -----------------------------------------------------------
    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        """Call ``callback(value)`` on every change; returns the unsubscribe."""

        def handler() -> None:
            callback(self._value)

        return self._depend(handler)

    def bind(
        self,
        widget: Any,
        attribute: str = "text",
        *,
        transform: Callable[[T], Any] | None = None,
    ) -> Value[T]:
        """Write this value into ``widget.attribute`` now and on every change.

        The widget redraws itself when its attribute is set (that is what
        :meth:`Widget.__setattr__` does), so binding one label to one value
        costs one property assignment per change — no ``refresh()``, no rebuilt
        tree, and no lost text in a neighbouring input::

            self.taps.bind(self.label, "text", transform=lambda n: f"Taps: {n}")
            self.visible.bind(self.spinner, "visible")

        Returns ``self`` so bindings can be chained.
        """

        def apply(value: T) -> None:
            try:
                setattr(widget, attribute, transform(value) if transform else value)
            except Exception:  # pragma: no cover - a bad binding must not crash a click
                _log.exception(
                    "binding %r to %s.%s failed",
                    self._name or "value",
                    type(widget).__name__,
                    attribute,
                )

        apply(self._value)
        self.subscribe(apply)
        return self

    def bind_get(
        self, widget: Any, attribute: str, *, transform: Callable[[Any], T] | None = None
    ) -> Value[T]:
        """Follow ``widget.attribute`` as a *source*: it becomes the value.

        The other direction of :meth:`bind`, for a field the user types into:
        ``self.query.bind_get(self.search, "value")`` keeps ``self.query`` in
        step with the input without a callback of its own. The widget is read
        once immediately, so the value starts out correct.
        """
        value = getattr(widget, attribute, None)
        if value is not None:
            self.set(transform(value) if transform else value)
        return self


class Computed(_Trackable, Generic[T]):
    """A value derived from others, recomputed when they change.

    The function runs once to find its sources, then again lazily whenever one
    of them changes::

        total = Computed(lambda: sum(item.price for item in cart.value))
        total.bind(total_label, "text", transform=lambda n: f"{n:.2f} грн")

    Reading a :class:`Value` (or another :class:`Computed`) inside the function
    is what links the two; nothing has to be declared. A source that was not
    read the first time is not watched — a derived value that branches on a
    condition re-runs whenever the condition itself changes, so the branch it
    takes is re-evaluated with it.
    """

    __slots__ = ("_compute", "_value", "_sources", "_dirty", "_recomputing")

    def __init__(self, compute: Callable[[], T]) -> None:
        super().__init__()
        self._compute = compute
        self._sources: list[tuple[_Trackable, Callable[[], None]]] = []
        self._dirty = True
        self._recomputing = False
        self._value: T = self._recompute()

    def _invalidate(self) -> None:
        """A source changed: recompute now, then tell our own dependents.

        Recomputing eagerly (rather than on the next read) is what makes a
        binding update the label the moment a source changes; a lazy recompute
        left the screen showing the previous number until something else asked
        for the value.
        """
        if self._recomputing:
            # A cycle (``a = Computed(lambda: b.value)``, ``b`` reading ``a``):
            # stop here rather than recursing forever.
            self._dirty = True
            return
        self._recomputing = True
        try:
            previous = self._value
            self._recompute()
            if not _same(previous, self._value):
                self._notify()
        finally:
            self._recomputing = False

    def _recompute(self) -> T:
        for _source, unsubscribe in self._sources:
            unsubscribe()
        collected: list[_Trackable] = []
        with _collecting(collected):
            value = self._compute()
        for source in collected:
            self._sources.append((source, source._depend(self._invalidate)))
        self._dirty = False
        self._value = value
        return value

    @property
    def value(self) -> T:
        """The derived value, recomputed first if a source changed."""
        if self._dirty and not self._recomputing:
            # A read from inside this value's own function (a genuine cycle)
            # sees the last value instead of recursing: stale beats a crash.
            self._recompute()
        tracked = _tracking.get()
        if tracked is not None and not any(item is self for item in tracked):
            tracked.append(self)
        return self._value

    def get(self) -> T:
        """The derived value (the explicit spelling of ``.value``)."""
        return self.value

    def map(self, function: Callable[[T], Any]) -> Computed[Any]:
        """A second derived value: ``total.map(lambda n: f"{n:.2f}")``."""
        return Computed(lambda: function(self.value))

    def bind(
        self,
        widget: Any,
        attribute: str = "text",
        *,
        transform: Callable[[T], Any] | None = None,
    ) -> Computed[T]:
        """Write the derived value into a widget now and on every recompute."""

        def apply() -> None:
            try:
                value = self.value
                setattr(widget, attribute, transform(value) if transform else value)
            except Exception:  # pragma: no cover - a bad binding must not crash a click
                _log.exception(
                    "binding a computed value to %s.%s failed",
                    type(widget).__name__,
                    attribute,
                )

        apply()
        self._depend(apply)
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Computed {self._value!r}>"


def _same(left: Any, right: Any) -> bool:
    """Whether two values count as unchanged (identity first, like a dict)."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:  # pragma: no cover - exotic __eq__
        return False
