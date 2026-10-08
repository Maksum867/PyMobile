"""App-shell navigation: the bottom tab bar.

``BottomNavigation`` is the primary navigation of an application — the
Android bottom-navigation pattern. It differs from ``SegmentedButtons``
(a content filter) in how renderers place it: pinned to the bottom edge in
the browser preview, an equal-width tab row in the Tk window and a
horizontal bar of tabs on the device.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .components import _deprecated_on_change, _parse_options
from .widget import Widget, callback_name, in_build_scope

__all__ = ["BottomNavigation"]


class BottomNavigation(Widget):
    """A persistent bar of mutually exclusive tabs.

    ``options`` are the tab labels; ``value`` is the selected one.
    ``on_select`` fires when the user picks another tab, exactly like
    :class:`~pymobile.Dropdown` (``on_change`` is a deprecated alias), so the
    usual pattern is::

        self.tabs = BottomNavigation(
            ["Home", "Stats", "Settings"],
            on_select=self.show_tab,
        )

    Options may also be ``(value, label)`` pairs for stable identifiers with
    localised labels (П-11).
    """

    type_name = "BottomNavigation"
    __slots__ = ("options", "_labels", "_value", "on_select")

    def __init__(
        self,
        options: Sequence[Any],
        *,
        value: str | None = None,
        on_select: Callable[[str], None] | None = None,
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        _deprecated_on_change(on_select, on_change, exclusive=False)
        values, labels = _parse_options(options)
        if value is not None and value not in values:
            raise ValueError(f"value {value!r} is not one of the options {values!r}")
        self.options = values
        self._labels = labels
        self.on_select = on_select or on_change
        self._value = value if value is not None else values[0]

    @property
    def value(self) -> str:
        """The selected tab's value; assigning to it schedules a redraw."""
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self.select(value)

    def select(self, value: str) -> None:
        """Choose ``value``, notifying listeners only on a real change."""
        if value not in self.options:
            raise ValueError(f"{value!r} is not one of the options")
        if value != self._value:
            self._value = value
            self.invalidate()
            # Picking the initial tab while build() assembles the shell must not
            # call the handler: it is the trap BUG-25 removed everywhere else.
            if self.on_select is not None and not in_build_scope():
                self.on_select(value)

    def set_value(self, value: str) -> None:
        """Generic setter used by the app's ``change`` event handler."""
        self.select(value)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "options": list(self.options),
            "labels": list(self._labels),
            "value": self._value,
            "on_select": callback_name(self.on_select),
        }
