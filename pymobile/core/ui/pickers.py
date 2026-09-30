"""Date and time pickers.

Values are plain ISO strings — ``"2026-09-19"`` and ``"14:30"`` — so they
serialise to JSON untouched and round-trip through storage as-is. The
browser preview renders real ``<input type="date">`` / ``<input type="time">``
controls (the OS picker comes for free); the device opens the native
``DatePickerDialog`` / ``TimePickerDialog``; invalid or out-of-range values
raise ``ValueError`` immediately, in the framework's fail-fast style.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from typing import Any

from .widget import Widget, callback_name, in_build_scope

__all__ = ["DatePicker", "TimePicker"]


def _parse_date(value: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from None


def _parse_time(value: str) -> _dt.time:
    text = value.strip()
    # Reject anything beyond the documented HH:MM shape up-front. ``fromisoformat``
    # accepts a permissive superset (including seconds, "T" separators, and
    # trailing "Z"), but the framework contract is "HH:MM" — anything else
    # would round-trip oddly through ``strftime("%H:%M")``. Accept both the
    # canonical "HH:MM" form and the short "H:MM" form which we normalise below.
    if not (len(text) == 4 or len(text) == 5):
        raise ValueError(f"{value!r} is not an ISO time (HH:MM)")
    expected_colon = 1 if len(text) == 4 else 2
    if text[expected_colon] != ":":
        raise ValueError(f"{value!r} is not an ISO time (HH:MM)")
    # Reject the ISO 8601 "end-of-day" sentinel ``24:xx`` *before* calling
    # ``fromisoformat`` — Python 3.14 silently coerces ``24:00`` to
    # ``00:00:00`` (hour=0), which would defeat the post-parse check below
    # on that interpreter. Python 3.13 still raises ``ValueError`` from
    # ``fromisoformat``, so the post-parse ``hour == 24`` branch is kept
    # as a defence-in-depth fallback for older interpreters.
    if text.startswith("24:"):
        raise ValueError(f"{value!r} is not a valid wall-clock time (hour 24)")
    if len(text) == 4 and text[1] == ":":      # "7:05" -> "07:05"
        text = f"0{text}"
    try:
        parsed = _dt.time.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{value!r} is not an ISO time (HH:MM)") from None
    # ``datetime.time.fromisoformat`` accepts the ISO 8601 "end-of-day"
    # sentinel 24:00 since Python 3.7 — it is technically valid but a wall
    # clock never reads 24:00, so reject it explicitly. ``strftime`` would
    # otherwise hand the value back unchanged and a UI field would show it.
    if parsed.hour == 24:
        raise ValueError(f"{value!r} is not a valid wall-clock time (hour 24)")
    return parsed


class DatePicker(Widget):
    """A calendar date field with optional ``minimum``/``maximum`` bounds."""

    type_name = "DatePicker"
    __slots__ = ("_value", "minimum", "maximum", "on_change")

    def __init__(
        self,
        value: str = "",
        *,
        minimum: str | None = None,
        maximum: str | None = None,
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.minimum = _parse_date(minimum) if minimum else None
        self.maximum = _parse_date(maximum) if maximum else None
        if self.minimum and self.maximum and self.minimum > self.maximum:
            raise ValueError("minimum must not be after maximum")
        self.on_change = on_change
        # Set directly: a listener observes user edits, not construction.
        self._value = self._normalise(value or _dt.date.today().isoformat())

    @property
    def value(self) -> str:
        """The selected date as ``YYYY-MM-DD``."""
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self.set_value(value)

    def _normalise(self, value: str) -> str:
        parsed = _parse_date(value)
        if self.minimum and parsed < self.minimum:
            parsed = self.minimum
        if self.maximum and parsed > self.maximum:
            parsed = self.maximum
        return parsed.isoformat()

    def set_value(self, value: str) -> None:
        """Validate, clamp to the bounds and notify listeners on change."""
        text = self._normalise(value)
        if text != self._value:
            self._value = text
            self.invalidate()
            # A value set while the tree is being built (build()) stays silent,
            # exactly like Slider/Switch/Checkbox/Dropdown: the handler would
            # otherwise run against widgets that do not exist yet (BUG-25).
            if self.on_change is not None and not in_build_scope():
                self.on_change(text)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "value": self._value,
            "minimum": self.minimum.isoformat() if self.minimum else "",
            "maximum": self.maximum.isoformat() if self.maximum else "",
            "on_change": callback_name(self.on_change),
        }


class TimePicker(Widget):
    """A wall-clock time field (``HH:MM``, minute resolution)."""

    type_name = "TimePicker"
    __slots__ = ("_value", "on_change")

    def __init__(
        self,
        value: str = "",
        *,
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.on_change = on_change
        # Set directly: a listener observes user edits, not construction.
        self._value = self._normalise(value or _dt.datetime.now().strftime("%H:%M"))

    @property
    def value(self) -> str:
        """The selected time as ``HH:MM``."""
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self.set_value(value)

    def _normalise(self, value: str) -> str:
        return _parse_time(value).strftime("%H:%M")

    def set_value(self, value: str) -> None:
        text = self._normalise(value)
        if text != self._value:
            self._value = text
            self.invalidate()
            # Same guard as DatePicker.set_value(): silent inside build().
            if self.on_change is not None and not in_build_scope():
                self.on_change(text)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "value": self._value,
            "on_change": callback_name(self.on_change),
        }
