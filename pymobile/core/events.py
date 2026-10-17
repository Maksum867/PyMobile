"""A minimal synchronous event bus.

Used to decouple UI widgets from application logic: widgets emit events, the
app subscribes. No threading, no async — Android delivers UI callbacks on the
main thread and the bus keeps that contract simple and predictable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger

__all__ = ["Event", "EventBus", "Subscription"]

_log = get_logger("events")

Handler = Callable[["Event"], None]


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable message flowing through the bus."""

    name: str
    source: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Read a payload field."""
        return self.data.get(key, default)


class _Registration:
    """One ``on()`` call. Identity matters: the same function subscribed twice
    is two registrations, and each Subscription cancels only its own."""

    __slots__ = ("handler", "internal")

    def __init__(self, handler: Handler, *, internal: bool = False) -> None:
        self.handler = handler
        #: Framework plumbing (``data:changed``) rather than an application
        #: listener: still delivered, not counted by ``len(bus)``.
        self.internal = internal


@dataclass(slots=True)
class Subscription:
    """Handle returned by :meth:`EventBus.on`; call :meth:`cancel` to detach."""

    _bus: EventBus
    _name: str
    _handler: Handler
    active: bool = True
    _registration: _Registration | None = None

    def cancel(self) -> None:
        """Remove THIS subscription from the bus (idempotent).

        Other subscriptions of the same handler — made by another screen, say —
        stay attached.
        """
        if self.active:
            if self._registration is not None:
                self._bus._remove(self._name, self._registration)
            else:
                self._bus.off(self._name, self._handler)
            self.active = False


class EventBus:
    """Named channels of callbacks, dispatched in registration order."""

    __slots__ = ("_handlers", "_lock")

    def __init__(self) -> None:
        self._handlers: dict[str, list[_Registration]] = {}
        self._lock = threading.Lock()

    def on(self, name: str, handler: Handler, *, internal: bool = False) -> Subscription:
        """Register ``handler`` for events called ``name``.

        ``internal=True`` marks framework plumbing: it is delivered like any
        other handler but not counted by ``len(bus)``, so a screen asserting
        "only my subscription is left" does not count the app's own listener.
        """
        if not callable(handler):
            raise TypeError(
                f"handler must be callable, got {type(handler).__name__!r}; "
                f"write app.on({name!r}, my_handler) where my_handler is a function"
            )
        registration = _Registration(handler, internal=internal)
        with self._lock:
            self._handlers.setdefault(name, []).append(registration)
        return Subscription(self, name, handler, _registration=registration)

    def _remove(self, name: str, registration: _Registration) -> None:
        with self._lock:
            handlers = self._handlers.get(name)
            if handlers and registration in handlers:
                handlers.remove(registration)
                if not handlers:
                    del self._handlers[name]

    def off(self, name: str, handler: Handler | None = None) -> int:
        """Detach a handler, or all handlers for ``name`` when ``handler`` is None.

        ``off(\"event\")`` removes every subscriber for that event — useful when
        you want to reset a channel without tracking each :class:`Subscription`::

            app.events.off(\"my:event\")  # all handlers for my:event are gone

        ``off(\"event\", handler)`` removes every registration of that handler.
        Handlers are compared with ``==``, so ``off("event", self.on_event)``
        works: each ``self.on_event`` is a new bound-method object, but equal
        ones wrap the same function and instance.
        Returns number of handlers removed.
        """
        with self._lock:
            if handler is None:
                handlers = self._handlers.pop(name, None)
                return len(handlers) if handlers else 0
            handlers = self._handlers.get(name)
            if not handlers:
                return 0
            before = len(handlers)
            with_removed = [r for r in handlers if r.handler != handler]
            if with_removed:
                self._handlers[name] = with_removed
            else:
                del self._handlers[name]
            return before - len(with_removed)

    def off_all(self, name: str) -> int:
        """Remove all handlers for ``name`` and return how many were removed."""
        with self._lock:
            handlers = self._handlers.pop(name, None)
        return len(handlers) if handlers else 0

    def emit(self, name: str, *, source: str | None = None, **data: Any) -> Event:
        """Build an :class:`Event` and deliver it to every subscriber."""
        event = Event(name=name, source=source, data=data)
        self.dispatch(event)
        return event

    def dispatch(self, event: Event) -> None:
        """Deliver a pre-built event; handler errors are logged, never raised."""
        with self._lock:
            handlers = tuple(r.handler for r in self._handlers.get(event.name, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                _log.exception("handler for %r failed", event.name)

    def clear(self) -> None:
        """Drop every subscription."""
        with self._lock:
            self._handlers.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._handlers

    def __iter__(self) -> Iterator[str]:
        return iter(self._handlers)

    def __len__(self) -> int:
        """Number of application subscriptions (framework-internal ones excluded)."""
        with self._lock:
            return sum(
                1
                for registrations in self._handlers.values()
                for registration in registrations
                if not registration.internal
            )
