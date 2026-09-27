"""A lazily built list for many rows without building them all at once.

A plain ``Column`` materialises every child, which is fine for a dozen rows but
wastes memory and slows the renderer for hundreds or thousands. :class:`List`
is a data-driven container: you give it a number of items and a ``builder``
callback that produces the widget for each index, and it builds the rows in
pages of ``visible_count``.

The first page is built up front. When the last built row scrolls into view
the device renderer sends a ``load_more`` event and the next page is appended
in place (the scroll position is kept); the Tk and browser previews show a
"Load more" button instead. Rows that were built stay built — this is
incremental loading, not recycling — so a list of 10 000 rows costs only what
the user actually scrolled through.

Structural changes (a new ``item_count`` or ``builder``) need
:meth:`List.refresh`.

Gestures
--------
* **Pull to refresh** — ``List(..., on_refresh=self.reload)``. Pulling the
  list down from its top shows a spinner and calls ``on_refresh``. The
  spinner stays while :attr:`List.refreshing` is true: it is cleared when the
  handler returns, or — if the handler returns a
  :class:`~pymobile.core.jobs.JobHandle` or
  :class:`~pymobile.core.net.http.HttpFuture` — when that work finishes.
* **Swipe a row** — ``ListTile(..., on_swipe_left=self.delete)``. Dragging a
  row sideways past a third of its width slides it away and calls the
  handler; a shorter drag springs back. ``on_swipe_right`` is the other
  direction.
* **Scroll to a row** — :meth:`List.scroll_to` builds the rows up to the
  index and scrolls the enclosing ``ScrollView`` so the row is visible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...log import get_logger
from .style import Color
from .widget import Container, Widget, callback_name

_log = get_logger("ui.list")

__all__ = ["List", "ListTile"]


class List(Container):
    """A list of ``item_count`` rows built by ``builder``, one page at a time.

    ``builder(index) -> Widget`` returns the widget for row ``index``. ``spacing``
    separates rows. ``visible_count`` is the page size: that many rows are
    built at first and each :meth:`load_more` adds another page. It used to be
    a hard cap — ``List(10_000, ...)`` showed 20 rows and nothing else.
    """

    type_name = "List"
    __slots__ = (
        "_builder",
        "item_count",
        "spacing",
        "visible_count",
        "_window",
        "on_refresh",
        "_refreshing",
        "_scroll_target",
        "_scroll_serial",
        "_scroll_animated",
    )

    def __init__(
        self,
        item_count: int = 0,
        *,
        builder: Callable[[int], Widget] | None = None,
        spacing: int = 0,
        visible_count: int = 20,
        on_refresh: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if item_count < 0:
            raise ValueError("item_count must not be negative")
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        if visible_count < 1:
            raise ValueError("visible_count must be >= 1")
        self._builder = builder
        self.item_count = item_count
        self.spacing = spacing
        self.visible_count = visible_count
        #: Number of rows built so far.
        self._window = min(item_count, visible_count)
        #: Called when the user pulls the list down from its top.
        self.on_refresh = on_refresh
        self._refreshing = False
        #: Last scroll_to() request: row index, a serial so the same index can
        #: be requested twice, and whether to animate.
        self._scroll_target = -1
        self._scroll_serial = 0
        self._scroll_animated = True
        self._build_window()

    def _build_window(self) -> None:
        """(Re)build rows ``0 .. window`` from scratch."""
        super().clear()
        self._window = min(self._window, self.item_count)
        for index in range(self._window):
            super().add(self._row_for(index))

    def _row_for(self, index: int) -> Widget:
        if self._builder is None:
            return ListTile(f"Item {index + 1}")
        return self._builder(index)

    @property
    def loaded(self) -> int:
        """How many rows are built (and shown)."""
        return self._window

    @property
    def has_more(self) -> bool:
        """Whether rows beyond the built ones exist."""
        return self._window < self.item_count

    def load_more(self, count: int | None = None) -> int:
        """Build the next ``count`` rows (default: one page); returns how many.

        Existing rows are kept as they are — only new ones are appended, so
        their state (a half-typed field, a toggled switch) survives.
        """
        step = self.visible_count if count is None else count
        if step < 1:
            raise ValueError("count must be >= 1")
        target = min(self.item_count, self._window + step)
        added = target - self._window
        for index in range(self._window, target):
            super().add(self._row_for(index))
        self._window = target
        if added:
            self.invalidate()
        return added

    def _ui_load_more(self, value: str = "") -> None:
        """``load_more`` event from a front end; ``value`` is the row count it saw.

        A stale request (the rows it saw are no longer the last ones, e.g. the
        user scrolled while a page was being added) is ignored so one scroll
        never loads two pages.
        """
        try:
            seen = int(value)
        except (TypeError, ValueError):
            seen = self._window
        if seen >= self._window:
            self.load_more()

    # Override add/clear so users cannot disturb the lazily built children.
    def add(self, child: Widget) -> Widget:  # pragma: no cover - defensive
        raise ValueError("List builds its rows itself; set item_count and call refresh()")

    def clear(self) -> None:  # pragma: no cover - defensive
        raise ValueError("List builds its rows itself; set item_count and call refresh()")

    def refresh(self) -> None:
        """Rebuild the built rows after ``item_count``/``builder`` changed.

        At least one page is shown; rows already loaded stay loaded (as far as
        the new ``item_count`` allows).
        """
        self._window = max(self._window, min(self.item_count, self.visible_count))
        self._build_window()
        self.invalidate()

    def scroll_to(self, index: int, *, animated: bool = True) -> None:
        """Scroll the enclosing ``ScrollView`` so row ``index`` is visible.

        Rows up to ``index`` are built first (a page at a time, as usual).
        On the device the scroll is smooth unless ``animated=False``; the
        browser preview scrolls the row into view. Calling it again with the
        same index scrolls again (the user may have scrolled away).
        """
        if not 0 <= index < self.item_count:
            raise IndexError(index)
        if index >= self._window:
            self.load_more(index + 1 - self._window)
        self._scroll_target = index
        self._scroll_serial += 1
        self._scroll_animated = animated
        self.invalidate()

    @property
    def scroll_target(self) -> int | None:
        """The row the last :meth:`scroll_to` asked for, or ``None``."""
        return self._scroll_target if self._scroll_target >= 0 else None

    # -- pull to refresh -----------------------------------------------------
    @property
    def refreshing(self) -> bool:
        """Whether the pull-to-refresh spinner is shown.

        Set it to ``True`` to show the spinner from code (a refresh button,
        a first load) and to ``False`` when the data has arrived.
        """
        return self._refreshing

    @refreshing.setter
    def refreshing(self, value: bool) -> None:
        value = bool(value)
        if value != self._refreshing:
            self._refreshing = value
            self.invalidate()

    def pull_to_refresh(self) -> None:
        """Do what the pull gesture does: show the spinner and call ``on_refresh``.

        Ignored while a refresh is already running or the list is disabled,
        so an impatient second pull does not start a second download.
        """
        if self.on_refresh is None or self._refreshing or not self.enabled:
            return
        self.refreshing = True
        try:
            result = self.on_refresh()
        except Exception:
            self.refreshing = False
            raise
        then = getattr(result, "then", None)
        if callable(then):
            # A background job / HTTP request: keep spinning until it ends,
            # successfully or not.
            def finished(_value: Any = None) -> None:
                self.refreshing = False

            try:
                then(finished, finished)
                return
            except Exception:  # an object with an unrelated then()
                _log.debug("on_refresh result has an incompatible then()", exc_info=True)
        self.refreshing = False

    def _ui_refresh(self, value: str = "") -> None:
        """``refresh`` event from a front end (the pull gesture)."""
        if self.on_refresh is None:
            # The spinner was shown by the device; make it go away again.
            self.invalidate()
            return
        if self._refreshing:
            return
        self.pull_to_refresh()

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "item_count": self.item_count,
            "spacing": self.spacing,
            "visible_count": self.visible_count,
            "loaded": self._window,
            "has_more": self.has_more,
            "refreshable": self.on_refresh is not None,
            "refreshing": self._refreshing,
            "on_refresh": callback_name(self.on_refresh),
            "scroll_to": self._scroll_target,
            "scroll_serial": self._scroll_serial,
            "scroll_animated": self._scroll_animated,
        }


class ListTile(Widget):
    """A simple row with a title and optional subtitle/trailing text.

    A convenience building block for :class:`List` (and usable standalone).

    ``on_long_press`` gives a row a second action — delete, archive, "edit
    this one" — which a list otherwise cannot offer without a permanent button
    on every row::

        ListTile(title=item.name, on_press=self.toggle, on_long_press=self.delete)

    The device vibrates on the long press, as Android users expect.

    ``on_swipe_left`` / ``on_swipe_right`` react to dragging the row sideways
    — the familiar "swipe to delete / archive"::

        ListTile(item.name, on_swipe_left=lambda: self.delete(item),
                 on_swipe_right=lambda: self.archive(item))

    While the row is dragged it is tinted with ``swipe_left_color`` /
    ``swipe_right_color``. The handler usually removes the item; if it does
    not, the row slides back into place.
    """

    type_name = "ListTile"
    __slots__ = (
        "_title",
        "subtitle",
        "trailing",
        "on_press",
        "on_long_press",
        "on_swipe_left",
        "on_swipe_right",
        "swipe_left_color",
        "swipe_right_color",
    )

    def __init__(
        self,
        title: str = "",
        *,
        subtitle: str = "",
        trailing: str = "",
        on_press: Callable[[], object] | None = None,
        on_long_press: Callable[[], None] | None = None,
        on_swipe_left: Callable[[], None] | None = None,
        on_swipe_right: Callable[[], None] | None = None,
        swipe_left_color: str = "#E53935",
        swipe_right_color: str = "#43A047",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self.subtitle = subtitle
        self.trailing = trailing
        self.on_press = on_press
        self.on_long_press = on_long_press
        self.on_swipe_left = on_swipe_left
        self.on_swipe_right = on_swipe_right
        self.swipe_left_color = Color.validate(swipe_left_color)
        self.swipe_right_color = Color.validate(swipe_right_color)

    @property
    def title(self) -> str:
        """The row's title; assigning to it schedules a redraw."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        if value != self._title:
            self._title = value
            self.invalidate()

    def set_title(self, value: str) -> None:
        """Replace the title."""
        self.title = value

    def press(self) -> None:
        """Simulate a tap; ignored while disabled."""
        if self.enabled and self.on_press is not None:
            self.on_press()

    def long_press(self) -> None:
        """Simulate a long press; ignored while disabled."""
        if self.enabled and self.on_long_press is not None:
            self.on_long_press()

    def swipe(self, direction: str) -> None:
        """Simulate a swipe: ``"left"`` (finger moves left) or ``"right"``.

        Ignored while disabled or when that direction has no handler.
        """
        if direction not in ("left", "right"):
            raise ValueError(f"direction must be 'left' or 'right', got {direction!r}")
        handler = self.on_swipe_left if direction == "left" else self.on_swipe_right
        if self.enabled and handler is not None:
            handler()

    def _ui_swipe(self, value: str) -> None:
        """``swipe`` event from a front end; ``value`` is the direction."""
        if value in ("left", "right"):
            self.swipe(value)
        else:
            raise ValueError(f"unknown swipe direction {value!r}")

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "title": self._title,
            "subtitle": self.subtitle,
            "trailing": self.trailing,
            "on_press": callback_name(self.on_press),
            "on_long_press": callback_name(self.on_long_press),
            "long_pressable": self.on_long_press is not None,
            "swipe_left": self.on_swipe_left is not None,
            "swipe_right": self.on_swipe_right is not None,
            "swipe_left_color": self.swipe_left_color,
            "swipe_right_color": self.swipe_right_color,
        }
