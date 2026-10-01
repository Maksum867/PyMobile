"""Vector icons, autocomplete, a two-thumb range slider and swipeable pages."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any

from .components import Button, TextInput
from .contract import WidgetNode
from .drawing import finite_number, icon_drawing, positive_int, theme_color
from .style import Color
from .widget import Container, Widget, callback_name, in_build_scope

__all__ = [
    "Icon",
    "IconButton",
    "FloatingActionButton",
    "AutoComplete",
    "RangeSlider",
    "PageView",
]


class Icon(Widget):
    """A bundled vector icon. See ``pymobile.ICON_NAMES`` for supported names."""

    type_name = "Icon"
    __slots__ = ("_name", "size", "color", "label")

    def __init__(
        self, name: str, *, size: int = 24, color: str | None = None, label: str = "", **kwargs: Any
    ) -> None:
        icon_drawing(name)
        positive_int(size, "size")
        if color is not None:
            Color.validate(color)
        super().__init__(**kwargs)
        self._name, self.size, self.color, self.label = name, size, color, str(label)

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        icon_drawing(value)
        if value != self._name:
            self._name = value
            self.invalidate()

    def props(self) -> dict[str, Any]:
        positive_int(self.size, "size")
        color = self.color or self.style.color or theme_color(self, "TEXT")
        return {
            **super().props(),
            "name": self.name,
            "size": self.size,
            "label": self.label,
            "drawing": icon_drawing(self.name, color),
        }

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        if node["type"] == self.type_name:
            node["style"] = {"width": self.size, "height": self.size, **node.get("style", {})}
        return node


class IconButton(Button):
    """A real icon-only button with a 48-dp touch target and accessible label."""

    type_name = "IconButton"
    __slots__ = ("_name", "size", "color", "label", "background")

    def __init__(
        self,
        name: str,
        *,
        label: str | None = None,
        size: int = 48,
        color: str | None = None,
        background: str | None = None,
        on_press: Callable[[], object] | None = None,
        **kwargs: Any,
    ) -> None:
        icon_drawing(name)
        positive_int(size, "size")
        if size < 48:
            raise ValueError("IconButton size must be at least 48 dp")
        for c in (color, background):
            if c is not None:
                Color.validate(c)
        super().__init__("", on_press=on_press, **kwargs)
        self._name = name
        self.label = str(label) if label is not None else name.replace("_", " ")
        if not self.label.strip():
            raise ValueError("an icon button needs a non-empty accessible label")
        self.size, self.color, self.background = size, color, background

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        icon_drawing(value)
        if value != self._name:
            self._name = value
            self.invalidate()

    def props(self) -> dict[str, Any]:
        if positive_int(self.size, "size") < 48:
            raise ValueError("IconButton size must be at least 48 dp")
        if not self.label.strip():
            raise ValueError("an icon button needs an accessible label")
        color = self.color or self.style.color or theme_color(self, "TEXT")
        return {
            **super().props(),
            "name": self.name,
            "size": self.size,
            "label": self.label,
            "drawing": icon_drawing(self.name, color),
            "background": self.background or "#00000000",
        }

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        if node["type"] == self.type_name:
            node["style"] = {
                "width": self.size,
                "height": self.size,
                "corner_radius": self.size // 2,
                "background": node.get("props", {}).get("background", "#00000000"),
                **node.get("style", {}),
            }
        return node


class FloatingActionButton(IconButton):
    """An elevated circular icon button; place it in a layout/Stack yourself."""

    __slots__ = ()

    def __init__(self, name: str = "add", *, size: int = 56, **kwargs: Any) -> None:
        super().__init__(name, size=size, **kwargs)

    def props(self) -> dict[str, Any]:
        props = super().props()
        props["background"] = self.background or theme_color(self, "PRIMARY")
        props["drawing"] = icon_drawing(self.name, self.color or "#FFFFFF")
        return props

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        node.setdefault("style", {}).setdefault("elevation", 6)
        return node


class AutoComplete(TextInput):
    """Text input with local suggestions and separate change/selection callbacks.

    Replace ``options`` with :meth:`set_options` when remote results arrive.
    Fetching/debouncing network suggestions belongs to the application's job
    or HTTP layer, not to a blocking renderer callback.
    """

    type_name = "AutoComplete"
    __slots__ = ("_options", "threshold", "on_select")

    def __init__(
        self,
        options: Sequence[str] = (),
        value: str = "",
        *,
        threshold: int = 1,
        on_select: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        positive_int(threshold, "threshold")
        self._options = self._validate_options(options)
        self.threshold, self.on_select = threshold, on_select
        super().__init__(value, **kwargs)

    @staticmethod
    def _validate_options(options: Sequence[str]) -> tuple[str, ...]:
        if isinstance(options, (str, bytes)):
            raise TypeError("options must be a sequence of strings, not one string")
        if any(not isinstance(option, str) for option in options):
            raise TypeError("every suggestion must be a string")
        return tuple(dict.fromkeys(options))

    @property
    def options(self) -> tuple[str, ...]:
        return self._options

    @options.setter
    def options(self, value: Sequence[str]) -> None:
        self.set_options(value)

    def set_options(self, options: Sequence[str]) -> None:
        values = self._validate_options(options)
        if values != self._options:
            self._options = values
            self.invalidate()

    def suggestions(self, query: str | None = None) -> tuple[str, ...]:
        text = self.value if query is None else query
        if len(text) < self.threshold:
            return ()
        return tuple(o for o in self.options if text.casefold() in o.casefold())

    def select(self, value: str) -> None:
        if value not in self.options:
            raise ValueError(f"{value!r} is not an autocomplete option")
        self.set_value(value)
        if self.on_select is not None and not in_build_scope():
            self.on_select(value)

    def _ui_select(self, value: str) -> None:
        if self.enabled:
            self.select(value)

    def _ui_set_value(self, value: str) -> None:
        if self.enabled:
            super()._ui_set_value(value)

    def props(self) -> dict[str, Any]:
        positive_int(self.threshold, "threshold")
        return {
            **super().props(),
            "options": list(self.options),
            "threshold": self.threshold,
            "on_select": callback_name(self.on_select),
        }


class RangeSlider(Widget):
    """A single track with two draggable thumbs. ``value`` is ``(low, high)``.

    Values are clamped and snapped relative to ``minimum``; reversed ranges
    and non-finite numbers are rejected. The native renderer commits on release
    rather than rebuilding the entire Python tree on every animation frame.
    """

    type_name = "RangeSlider"
    __slots__ = ("_value", "_minimum", "_maximum", "_step", "on_change", "label")

    def __init__(
        self,
        low: float = 0,
        high: float = 100,
        *,
        minimum: float = 0,
        maximum: float = 100,
        step: float = 1,
        label: str = "Range",
        on_change: Callable[[tuple[float, float]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._minimum = finite_number(minimum, "minimum")
        self._maximum = finite_number(maximum, "maximum")
        self._step = finite_number(step, "step")
        if self._maximum <= self._minimum:
            raise ValueError("maximum must exceed minimum")
        if self._step <= 0:
            raise ValueError("step must be positive")
        self.label, self.on_change = str(label), on_change
        self._value = self._normalise((low, high))

    @property
    def minimum(self) -> float:
        return self._minimum

    @property
    def maximum(self) -> float:
        return self._maximum

    @property
    def step(self) -> float:
        return self._step

    def _snap(self, value: float) -> float:
        value = max(self.minimum, min(value, self.maximum))
        if value == self.maximum:
            return value
        ticks = math.floor((value - self.minimum) / self.step + 0.5)
        return max(self.minimum, min(round(self.minimum + ticks * self.step, 12), self.maximum))

    def _normalise(self, value: Sequence[float]) -> tuple[float, float]:
        if isinstance(value, (str, bytes)) or len(value) != 2:
            raise ValueError("a range needs exactly two numbers")
        low, high = finite_number(value[0], "low"), finite_number(value[1], "high")
        if low > high:
            raise ValueError("low must not exceed high")
        return self._snap(low), self._snap(high)

    @property
    def value(self) -> tuple[float, float]:
        return self._value

    @value.setter
    def value(self, value: Sequence[float]) -> None:
        self.set_value(value)

    @property
    def low(self) -> float:
        return self._value[0]

    @property
    def high(self) -> float:
        return self._value[1]

    def set_value(self, value: Sequence[float]) -> None:
        new = self._normalise(value)
        if new != self._value:
            self._value = new
            self.invalidate()
            if self.on_change is not None and not in_build_scope():
                self.on_change(new)

    def _ui_set_value(self, value: str) -> None:
        if not self.enabled:
            return
        decoded = json.loads(value)
        if not isinstance(decoded, list):
            raise ValueError("range UI values must be a JSON array")
        self.set_value(decoded)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "low": self.low,
            "high": self.high,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "label": self.label,
            "on_change": callback_name(self.on_change),
        }

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        node["style"] = {"height": 64, **node.get("style", {})}
        return node


class PageView(Container):
    """A swipeable pager, rendering only the selected page.

    Pass page widgets, or ``item_count`` plus ``builder(index) -> Widget`` for
    lazily built pages. Keep this widget (or its selected index) outside
    ``Screen.build()`` when selection should survive screen.refresh().
    """

    type_name = "PageView"
    _gates_events = True
    __slots__ = ("_pages", "_builder", "_count", "_value", "loop", "on_select", "height")

    def __init__(
        self,
        *pages: Widget,
        item_count: int | None = None,
        builder: Callable[[int], Widget] | None = None,
        value: int = 0,
        loop: bool = False,
        height: int = 180,
        on_select: Callable[[int], None] | None = None,
        **kwargs: Any,
    ) -> None:
        positive_int(height, "height")
        if builder is not None:
            if pages:
                raise ValueError("pass pages or a builder, not both")
            if item_count is None:
                raise ValueError("a page builder needs item_count")
            positive_int(item_count, "item_count")
            count = item_count
        else:
            if item_count is not None:
                raise ValueError("item_count is only used with a builder")
            if not pages:
                raise ValueError("PageView needs at least one page")
            if any(not isinstance(page, Widget) for page in pages):
                raise TypeError("every page must be a Widget")
            if len({id(page) for page in pages}) != len(pages):
                raise ValueError("a page widget cannot be used twice")
            if any(page.parent is not None for page in pages):
                raise ValueError("pages must not already belong to another container")
            count = len(pages)
        super().__init__(**kwargs)
        self._pages, self._builder, self._count = tuple(pages), builder, count
        self._value = self._validate_index(value)
        self.loop, self.height, self.on_select = bool(loop), height, on_select
        Container.add(self, self._page(self._value))

    @property
    def item_count(self) -> int:
        return self._count

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value: int) -> None:
        self.select(value)

    def _validate_index(self, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("page index must be an int")
        if not 0 <= value < self.item_count:
            raise IndexError("page index is out of range")
        return value

    def _page(self, index: int) -> Widget:
        page = self._builder(index) if self._builder is not None else self._pages[index]
        if not isinstance(page, Widget):
            raise TypeError("page builder must return a Widget")
        return page

    def select(self, index: int) -> None:
        index = self._validate_index(index)
        if index == self._value:
            return
        # Build before replacing anything: a bad builder leaves the old page intact.
        page = self._page(index)
        if page.parent is not None and page.parent is not self:
            raise ValueError("page already belongs to another container")
        for old in self._children:
            old._parent = None
        self._children = [page]
        page._parent = self
        self._value = index
        self.invalidate()
        if self.on_select is not None and not in_build_scope():
            self.on_select(index)

    def set_value(self, value: int) -> None:
        self.select(value)

    def next(self) -> None:
        if self.value < self.item_count - 1:
            self.select(self.value + 1)
        elif self.loop:
            self.select(0)

    def previous(self) -> None:
        if self.value > 0:
            self.select(self.value - 1)
        elif self.loop:
            self.select(self.item_count - 1)

    def _ui_set_value(self, value: str) -> None:
        if self.enabled:
            try:
                self.select(int(value))
            except IndexError:
                raise ValueError("page index is out of range") from None

    def add(self, child: Widget) -> Widget:
        raise ValueError("PageView owns its pages; construct a new pager to replace the page set")

    def clear(self) -> None:
        raise ValueError("PageView needs a page; use select()")

    def props(self) -> dict[str, Any]:
        positive_int(self.height, "height")
        return {
            **super().props(),
            "value": self.value,
            "item_count": self.item_count,
            "loop": self.loop,
            "height": self.height,
            "on_select": callback_name(self.on_select),
        }

    def to_dict(self) -> WidgetNode:
        # Screen.refresh() detaches the old tree. Restore ownership of a kept pager.
        for node in self.walk():
            for child in node.children:
                child._parent = node
        tree = super().to_dict()
        tree["style"] = {"height": self.height, **tree.get("style", {})}
        return tree
