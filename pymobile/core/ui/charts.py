"""Read-only vector charts, rendered identically without plotting dependencies."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .contract import WidgetNode
from .drawing import finite_number, line, positive_int, theme_color
from .style import Color
from .widget import Widget

__all__ = ["BarChart", "LineChart", "PieChart"]

_PALETTE = ("#3F51B5", "#26A69A", "#FFB300", "#EF5350", "#AB47BC", "#42A5F5")
Data = Mapping[str, float] | Sequence[float]


class _Chart(Widget):
    # One native drawing implementation; the public classes select the geometry.
    type_name = "Chart"
    __slots__ = (
        "_data",
        "_labels",
        "title",
        "height",
        "color",
        "_colors",
        "show_values",
        "empty_text",
    )
    chart_kind = "bar"

    def __init__(
        self,
        data: Data = (),
        *,
        labels: Sequence[str] | None = None,
        title: str = "",
        height: int = 220,
        color: str | None = None,
        colors: Sequence[str] | None = None,
        show_values: bool = True,
        empty_text: str = "No data",
        **kwargs: Any,
    ) -> None:
        positive_int(height, "height")
        if height < 80:
            raise ValueError("chart height must be at least 80 dp")
        if color is not None:
            Color.validate(color)
        if isinstance(colors, (str, bytes)):
            raise TypeError("colors must be a sequence, not one color string")
        self._colors = tuple(colors or _PALETTE)
        for c in self._colors:
            Color.validate(c)
        self._data, self._labels = self._validate(data, labels)
        self.title, self.height, self.color = str(title), height, color
        self.show_values, self.empty_text = bool(show_values), str(empty_text)
        super().__init__(**kwargs)

    def _validate(
        self, data: Data, labels: Sequence[str] | None
    ) -> tuple[tuple[float, ...], tuple[str, ...]]:
        if isinstance(data, Mapping):
            if labels is not None:
                raise ValueError("mapping data already supplies labels")
            names = tuple(str(k) for k in data)
            values = tuple(finite_number(v, "chart value") for v in data.values())
        else:
            if isinstance(data, (str, bytes)):
                raise TypeError("chart data must be numbers or a label/value mapping")
            values = tuple(finite_number(v, "chart value") for v in data)
            if isinstance(labels, (str, bytes)):
                raise TypeError("labels must be a sequence of strings")
            names = (
                tuple(str(x) for x in labels)
                if labels is not None
                else tuple(str(i + 1) for i in range(len(values)))
            )
            if len(names) != len(values):
                raise ValueError("labels and data must have the same length")
        if self.chart_kind == "pie" and any(value < 0 for value in values):
            raise ValueError("pie chart values must not be negative")
        # A finite input can still overflow the sum or range used by geometry.
        if values and (
            not math.isfinite(max(values) - min(values))
            or (self.chart_kind == "pie" and not math.isfinite(sum(values)))
        ):
            raise ValueError("chart values span too large a range")
        return values, names

    @property
    def data(self) -> tuple[float, ...]:
        return self._data

    @data.setter
    def data(self, value: Data) -> None:
        labels = (
            None
            if isinstance(value, Mapping)
            else (self.labels if len(value) == len(self.data) else None)
        )
        self.set_data(value, labels=labels)

    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    def set_data(self, data: Data, *, labels: Sequence[str] | None = None) -> None:
        values, names = self._validate(data, labels)
        if (values, names) != (self._data, self._labels):
            self._data, self._labels = values, names
            self.invalidate()

    @staticmethod
    def _text(
        x: float, y: float, text: str, color: str, *, size: int = 11, align: str = "start"
    ) -> dict[str, Any]:
        return {
            "type": "text",
            "x": x,
            "y": y,
            "text": text,
            "color": color,
            "size": size,
            "align": align,
        }

    @staticmethod
    def _fit_text(text: str, width: float, size: int) -> str:
        """Conservative font-independent fit in the shared logical view box.

        Budget one em per character (combining marks take no extra space),
        rather than assuming that every locale has narrow Latin glyphs.
        """
        text = " ".join(text.splitlines())

        def measure(value: str) -> float:
            return sum(0 if unicodedata.combining(ch) else size for ch in value)

        if measure(text) <= width:
            return text
        if width < size:
            return ""
        result = ""
        for ch in text:
            if measure(result + ch + "…") > width:
                break
            result += ch
        return result + "…"

    def _drawing(self) -> dict[str, Any]:
        width, height = 320, self.height
        text = theme_color(self, "TEXT")
        muted = theme_color(self, "TEXT_MUTED")
        primary = self.color or self.style.color or theme_color(self, "PRIMARY")
        Color.validate(primary)
        shapes: list[dict[str, Any]] = []
        top = 30 if self.title else 12
        if self.title:
            shapes.append(
                self._text(12, 5, self._fit_text(self.title, width - 24, 15), text, size=15)
            )
        if not self.data or (self.chart_kind == "pie" and sum(self.data) == 0):
            shapes.append(self._text(160, height / 2, self.empty_text, muted, align="center"))
            return {"width": width, "height": height, "shapes": shapes}
        if self.chart_kind == "pie":
            radius = max(8.0, min((height - top - 20) / 2, 76))
            cx, cy = 94.0, top + radius + 6
            angle, total = -90.0, sum(self.data)
            slots = max(1, (height - top - 2) // 20)
            visible = slots if len(self.data) <= slots else slots - 1
            for i, (name, value) in enumerate(zip(self.labels, self.data, strict=True)):
                color = self._colors[i % len(self._colors)]
                sweep = value / total * 360
                shapes.append(
                    {
                        "type": "sector",
                        "x": cx,
                        "y": cy,
                        "r": radius,
                        "start": angle,
                        "sweep": sweep,
                        "color": color,
                    }
                )
                angle += sweep
                # Keep a legible legend inside the view; the accessible summary
                # and ASCII preview retain every entry when a legend is too long.
                if i < visible:
                    shapes.append(
                        {
                            "type": "rect",
                            "x": 185,
                            "y": top + i * 20,
                            "w": 8,
                            "h": 8,
                            "color": color,
                        }
                    )
                    suffix = f" {value / total:.0%}" if self.show_values else ""
                    label = self._fit_text(name + suffix, width - 199 - 8, 11)
                    shapes.append(self._text(199, top + i * 20 - 1, label, text))
            if len(self.data) > visible:
                shapes.append(
                    self._text(185, top + visible * 20, f"+{len(self.data) - visible} more", muted)
                )
        else:
            left, right, bottom = 42.0, 306.0, height - 30.0
            low, high = min(0.0, min(self.data)), max(0.0, max(self.data))
            if low == high:
                high = low + 1
            span = high - low
            # Keep value annotations outside both signed extremes, not inside
            # filled bars or on the zero baseline. Tiny charts omit values.
            annotate = self.show_values and len(self.data) <= 12 and bottom - top >= 48
            data_top = top + 16 if annotate else top
            data_bottom = bottom - 16 if annotate else bottom

            def y(value: float) -> float:
                return data_bottom - (value - low) / span * (data_bottom - data_top)

            zero = y(0)
            shapes.append(line([(left, top), (left, bottom), (right, bottom)], muted, 1))
            for value in (low, (low + high) / 2, high):
                shapes.append(
                    self._text(left - 5, y(value) - 5, f"{value:g}"[:9], muted, align="end")
                )
            shapes.append(line([(left, zero), (right, zero)], muted, 1))
            count = len(self.data)
            pitch = (right - left) / count
            longest = max((min(len(name), 8) for name in self.labels), default=1)
            skip = max(1, math.ceil(count / 8), math.ceil((longest * 11 + 8) / pitch))
            points = []
            for i, (name, value) in enumerate(zip(self.labels, self.data, strict=True)):
                x = left + (i + 0.5) * pitch
                if self.chart_kind == "bar":
                    bw = max(0.1, pitch * 0.72)
                    shapes.append(
                        {
                            "type": "rect",
                            "x": x - bw / 2,
                            "y": min(zero, y(value)),
                            "w": bw,
                            "h": max(0.5, abs(zero - y(value))),
                            "color": primary,
                        }
                    )
                else:
                    points.append((x, y(value)))
                    if count < 40:
                        shapes.append(
                            {"type": "circle", "x": x, "y": y(value), "r": 3, "color": primary}
                        )
                if i % skip == 0:
                    label_width = min(pitch * skip - 8, 2 * (x - 4), 2 * (width - 4 - x))
                    label = self._fit_text(name, label_width, 11)
                    shapes.append(self._text(x, bottom + 8, label, muted, align="center"))
                    if annotate:
                        label_y = y(value) + 3 if value < 0 else y(value) - 14
                        value_label = self._fit_text(f"{value:g}", label_width, 11)
                        shapes.append(self._text(x, label_y, value_label, text, align="center"))
            if points:
                shapes.append(line(points, primary, 2))
        return {"width": width, "height": height, "shapes": shapes}

    def props(self) -> dict[str, Any]:
        if positive_int(self.height, "height") < 80:
            raise ValueError("chart height must be at least 80 dp")
        label = self.title or f"{self.chart_kind} chart"
        summary = "; ".join(f"{k}: {v:g}" for k, v in zip(self.labels, self.data, strict=True))
        return {
            **super().props(),
            "kind": self.chart_kind,
            "title": self.title,
            "data": list(self.data),
            "labels": list(self.labels),
            "height": self.height,
            "label": f"{label}. {summary}" if summary else label,
            "drawing": self._drawing(),
        }

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        node["style"] = {"height": self.height, **node.get("style", {})}
        return node


class BarChart(_Chart):
    """Signed vertical bars with a zero baseline, labels and optional values."""

    __slots__ = ()
    chart_kind = "bar"


class LineChart(_Chart):
    """A single equally spaced series; negative/constant values are supported."""

    __slots__ = ()
    chart_kind = "line"


class PieChart(_Chart):
    """Non-negative slices and a compact legend; all-zero data shows empty_text."""

    __slots__ = ()
    chart_kind = "pie"
