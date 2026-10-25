"""Declarative vector drawing: ``Canvas``, ``Line``, ``Path``, ``Shape``.

The framework already knew how to draw vector primitives — icons and charts
produce them and every renderer consumes them (Android ``Canvas``, SVG in the
browser, a Tk canvas, the mockup picture). None of it was reachable from an
app, so a waveform had to be faked with forty ``Container`` strips of differing
width, which is one nested layout per sample.

``Canvas`` is the door. It accepts children — ``Line``, ``Path``, ``Shape``,
and ``Rect``/``Circle`` helpers — serialises them into the same primitive list
the renderers already understand, and draws them in a fixed logical view box::

    Canvas(
        Line((0, 40), (120, 40), color="#B0BEC5"),
        Path([(0, 40), (20, 10), (40, 55), (60, 25)], width=2, closed=False),
        Shape("rect", x=10, y=10, w=40, h=12, color=Color.PRIMARY),
        Circle(60, 20, 8, color=Color.SUCCESS),
        width=120,
        height=80,
    )

Everything is plain data (the node carries a ``drawing`` prop, exactly like
``Chart``), so it renders identically in the phone, the browser, the GUI
preview, the ASCII snapshot and the golden PNG — and it can be snapshotted.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contract import WidgetNode
from .drawing import finite_number, theme_color
from .style import Color, Style
from .widget import Widget

__all__ = ["Canvas", "Line", "Path", "Shape", "Rect", "Circle", "Polygon"]

#: Shapes ``Shape`` understands. Each maps to one primitive the renderers draw.
SHAPE_KINDS = ("rect", "circle", "polygon", "line", "text")


def _colour(value: str | None, attribute: str, element: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"{element}: {attribute} must be a colour string, got {type(value).__name__!r}"
        )
    return Color.validate(value)


def _number(value: Any, name: str, element: str) -> float:
    try:
        return finite_number(value, f"{element} {name}")
    except (TypeError, ValueError) as error:
        raise type(error)(f"{element}: {error}") from None


class _Element(Widget):
    """Base for everything a ``Canvas`` accepts.

    An element is a widget with no children and no hit area: it exists only to
    produce primitives. Building one outside a canvas is harmless (it renders
    as an empty node), which keeps the classes usable in tests.
    """

    type_name = "CanvasElement"
    __slots__ = ("color", "width", "opacity", "dash", "fill")

    def __init__(
        self,
        *,
        color: str | None = None,
        width: float = 2.0,
        opacity: float = 1.0,
        dash: str | bool = False,
        fill: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.color = _colour(color, "color", type(self).__name__)
        stroke = _number(width, "width", type(self).__name__)
        if stroke <= 0:
            raise ValueError(f"{type(self).__name__}: width must be positive, got {width!r}")
        self.width = stroke
        level = _number(opacity, "opacity", type(self).__name__)
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"{type(self).__name__}: opacity must be within 0.0 … 1.0")
        self.opacity = level
        self.dash = bool(dash)
        self.fill = bool(fill)

    # -- primitives --------------------------------------------------------
    def _paint(self, default: str) -> str:
        """The colour to draw with, with ``opacity`` folded into the alpha.

        Android colours are ``#AARRGGBB``; an element that asks for 50 % opacity
        must actually be half transparent in every renderer, so the alpha is
        baked into the colour string rather than kept as a separate knob the
        four renderers would each have to remember.
        """
        colour = (self.color or default).lstrip("#")
        if len(colour) == 3:
            colour = "".join(character * 2 for character in colour)
        alpha = 255
        if len(colour) == 8:
            alpha = int(colour[0:2], 16)
            colour = colour[2:]
        elif len(colour) != 6:
            colour = "212121"
        if self.opacity < 1.0:
            alpha = round(alpha * self.opacity)
        if alpha >= 255:
            return f"#{colour.upper()}"
        return f"#{alpha:02X}{colour.upper()}"

    def primitives(self, default: str) -> list[dict[str, Any]]:
        """The drawing primitives for this element (``[]`` when invisible)."""
        raise NotImplementedError

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "element": type(self).__name__,
            "color": self.color or "",
            "width": self.width,
            "opacity": self.opacity,
            "dash": self.dash,
        }


class Line(_Element):
    """A polyline through ``points``; a two-point line is a straight segment."""

    __slots__ = ("_points",)
    type_name = "CanvasLine"

    def __init__(self, *points: tuple[float, float], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        flat: list[tuple[float, float]] = []
        for index, point in enumerate(points):
            if not isinstance(point, (tuple, list)) or len(point) != 2:
                raise TypeError(
                    f"Line: point {index} must be an (x, y) pair, got {point!r}; "
                    "write Line((0, 10), (20, 30))"
                )
            flat.append(
                (
                    _number(point[0], "x", "Line"),
                    _number(point[1], "y", "Line"),
                )
            )
        if len(flat) < 2:
            raise ValueError(
                f"Line: at least two points are needed, got {len(flat)}; "
                "write Line((0, 10), (20, 30))"
            )
        self._points = tuple(flat)

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """The vertices, in order."""
        return self._points

    def primitives(self, default: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "line",
                "points": [[x, y] for x, y in self._points],
                "color": self._paint(default),
                "width": self.width,
            }
        ]


class Path(Line):
    """A polyline that may be closed back to its first point."""

    __slots__ = ("closed",)
    type_name = "CanvasPath"

    def __init__(
        self, points: Sequence[tuple[float, float]], *, closed: bool = False, **kwargs: Any
    ) -> None:
        if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
            raise TypeError(
                "Path: points must be a sequence of (x, y) pairs; "
                "write Path([(0, 10), (20, 30)])"
            )
        self.closed = bool(closed)
        super().__init__(*points, **kwargs)

    def primitives(self, default: str) -> list[dict[str, Any]]:
        points = list(self._points)
        if self.closed and len(points) > 2:
            points.append(points[0])
        return [
            {
                "type": "line",
                "points": [[x, y] for x, y in points],
                "color": self._paint(default),
                "width": self.width,
            }
        ]


class Shape(_Element):
    """One primitive by name: ``rect``, ``circle``, ``polygon``, ``line``, ``text``."""

    __slots__ = ("kind", "_geometry", "_points", "text", "font_size", "align")
    type_name = "CanvasShape"

    def __init__(
        self,
        kind: str = "rect",
        *,
        x: float = 0.0,
        y: float = 0.0,
        w: float = 0.0,
        h: float = 0.0,
        r: float = 0.0,
        points: Sequence[tuple[float, float]] | None = None,
        text: str = "",
        font_size: float = 12.0,
        align: str = "start",
        **kwargs: Any,
    ) -> None:
        if kind not in SHAPE_KINDS:
            raise ValueError(
                f"Shape: unknown kind {kind!r}; expected one of {', '.join(SHAPE_KINDS)} "
                "(or use the Line, Path, Rect, Circle and Polygon helpers)"
            )
        if kind == "line":
            raise ValueError(
                "Shape: use Line(...) for a line; Shape draws one filled primitive"
            )
        super().__init__(**kwargs)
        self.kind = kind
        self._points: list[tuple[float, float]] = []
        self._geometry: dict[str, float] = {
            "x": _number(x, "x", "Shape"),
            "y": _number(y, "y", "Shape"),
            "w": _number(w, "w", "Shape"),
            "h": _number(h, "h", "Shape"),
            "r": _number(r, "r", "Shape"),
        }
        if points is not None:
            self._points = [
                (_number(px, "x", "Shape"), _number(py, "y", "Shape")) for px, py in points
            ]
        if kind == "polygon" and len(self._points) < 3:
            raise ValueError("Shape: a polygon needs at least three points")
        if kind == "circle" and self._geometry["r"] <= 0:
            raise ValueError("Shape: a circle needs a positive r")
        if kind == "rect" and (self._geometry["w"] <= 0 or self._geometry["h"] <= 0):
            raise ValueError("Shape: a rect needs a positive w and h")
        if kind == "text" and not str(text):
            raise ValueError("Shape: a text shape needs text=")
        if align not in ("start", "center", "end"):
            raise ValueError("Shape: align must be start, center or end")
        self.text = str(text)
        self.font_size = _number(font_size, "font_size", "Shape")
        self.align = align

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "kind": self.kind,
            **self._geometry,
            "text": self.text,
            "font_size": self.font_size,
            "align": self.align,
        }

    def primitives(self, default: str) -> list[dict[str, Any]]:
        colour = self._paint(default)
        geometry = self._geometry
        if self.kind == "rect":
            return [
                {
                    "type": "rect",
                    "x": geometry["x"],
                    "y": geometry["y"],
                    "w": geometry["w"],
                    "h": geometry["h"],
                    "color": colour,
                }
            ]
        if self.kind == "circle":
            return [
                {
                    "type": "circle",
                    "x": geometry["x"],
                    "y": geometry["y"],
                    "r": geometry["r"],
                    "color": colour,
                    "fill": self.fill,
                    "width": self.width,
                }
            ]
        if self.kind == "polygon":
            # A closed path is what every backend turns into a filled polygon.
            points = list(self._points)
            return [
                {
                    "type": "line",
                    "points": [[x, y] for x, y in [*points, points[0]]],
                    "color": colour,
                    "width": self.width,
                }
            ]
        return [
            {
                "type": "text",
                "x": geometry["x"],
                "y": geometry["y"],
                "text": self.text,
                "color": colour,
                "size": self.font_size,
                "align": self.align,
            }
        ]


class Rect(Shape):
    """A filled rectangle in the canvas' own coordinates."""

    __slots__ = ()

    def __init__(
        self, x: float = 0.0, y: float = 0.0, w: float = 0.0, h: float = 0.0, **kwargs: Any
    ) -> None:
        super().__init__("rect", x=x, y=y, w=w, h=h, **kwargs)


class Circle(Shape):
    """A circle; ``fill=False`` draws just the ring."""

    __slots__ = ()

    def __init__(self, x: float = 0.0, y: float = 0.0, r: float = 0.0, **kwargs: Any) -> None:
        super().__init__("circle", x=x, y=y, r=r, **kwargs)


class Polygon(Shape):
    """A filled polygon through three or more points."""

    __slots__ = ()

    def __init__(self, *points: tuple[float, float], **kwargs: Any) -> None:
        super().__init__("polygon", points=list(points), **kwargs)


class Canvas(Widget):
    """A fixed-size drawing surface for :class:`Line`, :class:`Path` and friends."""

    __slots__ = ("width", "height", "background", "elements")
    type_name = "Canvas"

    def __init__(
        self,
        *elements: _Element,
        width: int = 320,
        height: int = 120,
        background: str | None = None,
        style: Style | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise TypeError(f"Canvas: width must be a positive int, got {width!r}")
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            raise TypeError(f"Canvas: height must be a positive int, got {height!r}")
        self.width = width
        self.height = height
        self.background = _colour(background, "background", "Canvas")
        for index, element in enumerate(elements):
            if not isinstance(element, _Element):
                raise TypeError(
                    f"Canvas: element {index} is {type(element).__name__!r}; only Line, "
                    "Path, Shape, Rect, Circle and Polygon can be drawn — a Widget "
                    "belongs in a layout, not on a canvas"
                )
        self.elements = tuple(elements)
        super().__init__(style=style, **kwargs)

    def add(self, *elements: _Element) -> None:
        """Add elements to the drawing and redraw."""
        for element in elements:
            if not isinstance(element, _Element):
                raise TypeError(
                    f"Canvas.add: {type(element).__name__!r} is not a drawing element"
                )
        self.elements = (*self.elements, *elements)
        self.invalidate()

    def clear(self) -> None:
        """Remove every element."""
        if self.elements:
            self.elements = ()
            self.invalidate()

    # -- the renderer contract --------------------------------------------
    def drawing(self, *, default_colour: str = "#212121") -> dict[str, Any]:
        """The serialised primitives, scaled into the canvas' logical box.

        ``Chart`` produces the same structure, so every renderer path is shared.
        """
        shapes: list[dict[str, Any]] = []
        for element in self.elements:
            shapes.extend(element.primitives(default_colour))
        return {"width": self.width, "height": self.height, "shapes": shapes}

    def props(self) -> dict[str, Any]:
        # Elements without a colour of their own follow the theme's text colour,
        # like a Label does; the resolution happens here so a theme change
        # repaints the drawing instead of freezing the colour it was born with.
        default_colour = theme_color(self, "TEXT")
        return {
            **super().props(),
            "width": self.width,
            "height": self.height,
            "background": self.background or "",
            "drawing": self.drawing(default_colour=default_colour),
            "label": f"drawing with {len(self.elements)} elements",
        }

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        # The renderers size the surface from the style/height like a Chart does.
        node["style"] = {"height": self.height, **node.get("style", {})}
        return node
