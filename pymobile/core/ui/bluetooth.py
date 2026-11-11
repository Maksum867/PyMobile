"""Widgets for Bluetooth data: signal strength, battery level, a proximity radar
and connection status.

They draw numbers the app already has. None of them scans or connects by
itself: the app reads its Bluetooth data and passes the values in, then calls
the matching ``set_*`` method to update the widget in place.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .canvas import Canvas, Circle, Rect, Shape
from .components import Label
from .layout import Row
from .style import Color, Style

__all__ = ["SignalStrength", "BatteryLevel", "Radar", "ConnectionStatus"]

RSSI_MIN = -127
RSSI_MAX = 20
#: Received power at 1 m, a common default for BLE beacons, in dBm.
TX_POWER_1M = -59
#: Path-loss exponent: 2 is free space, indoors is usually 2.5 to 3.
PATH_LOSS_EXPONENT = 2.0
RADAR_MAX_DEVICES = 12
_BAR_OFF = "#D6D6D6"
_RING = "#D6D6D6"
_CONNECTION_STATES: dict[str, tuple[str, str]] = {
    "connected": (Color.SUCCESS, "Connected"),
    "connecting": (Color.WARNING, "Connecting…"),
    "disconnected": (Color.TEXT_MUTED, "Disconnected"),
    "error": (Color.ERROR, "Connection failed"),
}


def _check_rssi(value: Any, owner: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not RSSI_MIN <= value <= RSSI_MAX:
        raise ValueError(
            f"{owner}: rssi must be an int from {RSSI_MIN} to {RSSI_MAX} dBm, got {value!r}"
        )
    return value


def _check_optional_rssi(value: Any, owner: str) -> int | None:
    return None if value is None else _check_rssi(value, owner)


def _signal_level(rssi: int | None) -> int:
    """Bars lit, 0 to 4. Thresholds follow the usual phone-style scale."""
    if rssi is None:
        return 0
    if rssi >= -60:
        return 4
    if rssi >= -70:
        return 3
    if rssi >= -80:
        return 2
    if rssi >= -90:
        return 1
    return 0


def _signal_elements(rssi: int | None) -> list[Any]:
    level = _signal_level(rssi)
    if level >= 3:
        lit = Color.SUCCESS
    elif level == 2:
        lit = Color.WARNING
    elif level == 1:
        lit = Color.ERROR
    else:
        lit = Color.TEXT_MUTED
    base = 44
    elements: list[Any] = []
    for index, height in enumerate((14, 22, 30, 38)):
        colour = lit if index < level else _BAR_OFF
        elements.append(Rect(4 + index * 12, base - height, 8, height, color=colour))
    label = "No signal" if rssi is None else f"{rssi} dBm"
    elements.append(Shape("text", x=62, y=30, text=label, font_size=14, color=Color.TEXT))
    return elements


class SignalStrength(Canvas):
    """Four signal bars and the RSSI in dBm, for one device.

    ``rssi`` is the received signal strength in dBm (for example ``-67``), or
    ``None`` when the device is not in range. Stronger signals are closer to
    zero, so ``-50`` is four bars and ``-95`` is none.
    """

    def __init__(
        self, rssi: int | None = None, *, width: int = 160, height: int = 48, **kwargs: Any
    ) -> None:
        checked = _check_optional_rssi(rssi, "SignalStrength")
        super().__init__(*_signal_elements(checked), width=width, height=height, **kwargs)
        self.rssi = checked

    def set_rssi(self, rssi: int | None) -> None:
        """Show a new reading (or ``None`` for no signal) and redraw."""
        checked = _check_optional_rssi(rssi, "SignalStrength")
        self.rssi = checked
        self.clear()
        self.add(*_signal_elements(checked))


def _battery_colour(percent: int) -> str:
    if percent >= 50:
        return Color.SUCCESS
    if percent >= 20:
        return Color.WARNING
    return Color.ERROR


def _battery_elements(percent: int | None, charging: bool) -> list[Any]:
    elements: list[Any] = [
        Rect(2, 6, 80, 28, fill=False, color=Color.TEXT_MUTED, width=2),
        Rect(82, 16, 6, 10, color=Color.TEXT_MUTED),
    ]
    if percent is None:
        text = "--"
    else:
        if percent > 0:
            elements.append(Rect(6, 10, 72 * percent / 100, 20, color=_battery_colour(percent)))
        text = f"{percent} %"
    if charging:
        text += " charging"
    elements.append(Shape("text", x=96, y=28, text=text, font_size=14, color=Color.TEXT))
    return elements


class BatteryLevel(Canvas):
    """A battery icon with its charge in percent.

    ``percent`` is 0 to 100, or ``None`` when the level is unknown. The fill
    turns amber below 50 % and red below 20 %. ``charging=True`` adds the word
    "charging" after the figure.
    """

    def __init__(
        self, percent: int | None = None, *, charging: bool = False, **kwargs: Any
    ) -> None:
        checked = self._check_percent(percent)
        super().__init__(*_battery_elements(checked, charging), width=220, height=40, **kwargs)
        self.percent = checked
        self.charging = bool(charging)

    @staticmethod
    def _check_percent(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError(
                f"BatteryLevel: percent must be an int from 0 to 100 or None, got {value!r}"
            )
        return value

    def set_level(self, percent: int | None, *, charging: bool = False) -> None:
        """Show a new charge level and redraw."""
        checked = self._check_percent(percent)
        self.percent = checked
        self.charging = bool(charging)
        self.clear()
        self.add(*_battery_elements(checked, self.charging))


def _distance_m(rssi: int) -> float:
    """A rough distance from RSSI, from the log-distance path-loss model."""
    return 10 ** ((TX_POWER_1M - rssi) / (10 * PATH_LOSS_EXPONENT))


class Radar(Canvas):
    """A top-down view of nearby devices, with distance from signal strength.

    ``devices`` is a sequence of ``(name, rssi)`` pairs. The centre is the
    phone; each device sits on a ring at its estimated distance, up to
    ``range_m`` metres, and devices further away are drawn on the outer ring.

    RSSI tells distance but not direction, so devices are spread around the
    circle in order of signal strength, strongest first. Their angles do not
    mean anything. Distances are rough: walls, bodies and the phone's own
    antenna change them a lot.
    """

    def __init__(
        self,
        devices: Iterable[tuple[str, int]] = (),
        *,
        range_m: float = 10.0,
        size: int = 240,
        **kwargs: Any,
    ) -> None:
        if isinstance(range_m, bool) or not isinstance(range_m, (int, float)) or range_m <= 0:
            raise ValueError(f"Radar: range_m must be a positive number, got {range_m!r}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 120:
            raise ValueError(f"Radar: size must be an int of at least 120, got {size!r}")
        self.range_m = float(range_m)
        checked = self._check_devices(devices)
        super().__init__(
            *self._elements(checked, self.range_m, size), width=size, height=size, **kwargs
        )
        self.size = size
        self.devices = checked

    @staticmethod
    def _check_devices(devices: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
        checked: list[tuple[str, int]] = []
        for entry in devices:
            try:
                name, rssi = entry
            except (TypeError, ValueError):
                raise ValueError(
                    f"Radar: each device is a (name, rssi) pair, got {entry!r}"
                ) from None
            if not isinstance(name, str) or not name:
                raise ValueError(f"Radar: device names must be non-empty strings, got {name!r}")
            checked.append((name, _check_rssi(rssi, "Radar")))
        if len(checked) > RADAR_MAX_DEVICES:
            raise ValueError(f"Radar: at most {RADAR_MAX_DEVICES} devices, got {len(checked)}")
        return checked

    @staticmethod
    def _elements(devices: list[tuple[str, int]], range_m: float, size: int) -> list[Any]:
        centre = size / 2
        outer = centre - 22
        elements: list[Any] = [
            Circle(centre, centre, outer * step / 3, fill=False, color=_RING, width=1)
            for step in (1, 2, 3)
        ]
        elements.append(Circle(centre, centre, 5, color=Color.PRIMARY))
        ordered = sorted(devices, key=lambda device: device[1], reverse=True)
        for index, (name, rssi) in enumerate(ordered):
            distance = min(_distance_m(rssi), range_m)
            radius = outer * distance / range_m
            angle = -math.pi / 2 + index * 2 * math.pi / max(len(ordered), 1)
            x = centre + radius * math.cos(angle)
            y = centre + radius * math.sin(angle)
            elements.append(Circle(x, y, 7, color=Color.ACCENT))
            elements.append(
                Shape("text", x=x + 10, y=y + 4, text=name[:16], font_size=12, color=Color.TEXT)
            )
        return elements

    def set_devices(self, devices: Iterable[tuple[str, int]]) -> None:
        """Replace the devices and redraw."""
        checked = self._check_devices(devices)
        self.devices = checked
        self.clear()
        self.add(*self._elements(checked, self.range_m, self.size))


class ConnectionStatus(Row):
    """A coloured dot and a word for the link state.

    ``state`` is ``"disconnected"`` (default), ``"connecting"``, ``"connected"``
    or ``"error"``. Use :meth:`set_state` to change it.
    """

    def __init__(self, state: str = "disconnected", **kwargs: Any) -> None:
        colour, text = self._lookup(state)
        self._dot = Canvas(Circle(6, 6, 6, color=colour), width=12, height=12)
        self._label = Label(text, style=Style(font_size=14, color=colour))
        super().__init__(self._dot, self._label, spacing=8, **kwargs)
        self.state = state

    @staticmethod
    def _lookup(state: Any) -> tuple[str, str]:
        if state not in _CONNECTION_STATES:
            allowed = ", ".join(_CONNECTION_STATES)
            raise ValueError(f"ConnectionStatus: state must be one of {allowed}, got {state!r}")
        return _CONNECTION_STATES[state]

    def set_state(self, state: str) -> None:
        """Change the state; the dot and the label update together."""
        colour, text = self._lookup(state)
        self.state = state
        self._dot.clear()
        self._dot.add(Circle(6, 6, 6, color=colour))
        self._label.text = text
        self._label.style = Style(font_size=14, color=colour)
