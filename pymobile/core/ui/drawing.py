"""Small, dependency-free vector drawings shared by the new renderers.

Coordinates are in a logical view box. Android Canvas, SVG, Tk Canvas and
mockups consume the same primitives; no fonts or downloaded icon packs are
needed. The primitive contract is intentionally private and JSON-only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from html import escape
from typing import Any

from .style import Color
from .widget import Widget

__all__ = ["ICON_NAMES"]

ICON_NAMES = (
    "add",
    "remove",
    "close",
    "check",
    "search",
    "menu",
    "back",
    "forward",
    "up",
    "down",
    "home",
    "settings",
    "delete",
    "edit",
    "heart",
    "star",
    "info",
    "refresh",
    "share",
    "calendar",
    "user",
    "download",
)


def finite_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number, not bool")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        raise TypeError(f"{name} must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def theme_color(widget: Widget, key: str) -> str:
    screen = widget.screen
    if screen is not None and screen.mounted:
        return screen.app.theme[key]
    return str(getattr(Color, key))


def line(points: list[tuple[float, float]], color: str, width: float = 2) -> dict[str, Any]:
    return {"type": "line", "points": [list(p) for p in points], "color": color, "width": width}


def icon_drawing(name: str, color: str = "#212121") -> dict[str, Any]:
    if name not in ICON_NAMES:
        raise ValueError(f"unknown icon {name!r}; choose from: {', '.join(ICON_NAMES)}")
    Color.validate(color)
    shapes: list[dict[str, Any]] = []

    def stroke(*points: tuple[float, float]) -> None:
        shapes.append(line(list(points), color))

    def circle(x: float, y: float, radius: float, *, filled: bool = False) -> None:
        shapes.append(
            {
                "type": "circle",
                "x": x,
                "y": y,
                "r": radius,
                "color": color,
                "fill": filled,
                "width": 2,
            }
        )

    if name == "add":
        stroke((5, 12), (19, 12))
        stroke((12, 5), (12, 19))
    elif name == "remove":
        stroke((5, 12), (19, 12))
    elif name == "close":
        stroke((6, 6), (18, 18))
        stroke((18, 6), (6, 18))
    elif name == "check":
        stroke((4, 12), (9, 17), (20, 6))
    elif name == "search":
        circle(10, 10, 6)
        stroke((15, 15), (21, 21))
    elif name == "menu":
        for y in (6, 12, 18):
            stroke((3, y), (21, y))
    elif name in ("back", "forward", "up", "down"):
        points = {
            "back": [(16, 4), (8, 12), (16, 20)],
            "forward": [(8, 4), (16, 12), (8, 20)],
            "up": [(4, 16), (12, 8), (20, 16)],
            "down": [(4, 8), (12, 16), (20, 8)],
        }[name]
        stroke(*points)
    elif name == "home":
        stroke((2, 11), (12, 3), (22, 11))
        stroke((5, 10), (5, 21), (19, 21), (19, 10))
        stroke((9, 21), (9, 14), (15, 14), (15, 21))
    elif name == "settings":
        circle(12, 12, 6)
        circle(12, 12, 2)
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            stroke(
                (12 + 7 * math.cos(a), 12 + 7 * math.sin(a)),
                (12 + 10 * math.cos(a), 12 + 10 * math.sin(a)),
            )
    elif name == "delete":
        stroke((4, 6), (20, 6))
        stroke((9, 3), (15, 3))
        stroke((6, 6), (7, 21), (17, 21), (18, 6))
        stroke((10, 10), (10, 17))
        stroke((14, 10), (14, 17))
    elif name == "edit":
        stroke((4, 16), (16, 4), (20, 8), (8, 20), (3, 21), (4, 16))
        stroke((14, 6), (18, 10))
    elif name == "heart":
        stroke(
            (12, 21),
            (3, 12),
            (2, 8),
            (4, 4),
            (8, 3),
            (12, 7),
            (16, 3),
            (20, 4),
            (22, 8),
            (21, 12),
            (12, 21),
        )
    elif name == "star":
        star: list[tuple[float, float]] = []
        for i in range(11):
            a = math.radians(-90 + i * 36)
            r = 10 if i % 2 == 0 else 4.5
            star.append((12 + r * math.cos(a), 12 + r * math.sin(a)))
        stroke(*star)
    elif name == "info":
        circle(12, 12, 9)
        circle(12, 7, 1, filled=True)
        stroke((12, 11), (12, 17))
    elif name == "refresh":
        stroke((20, 10), (18, 5), (12, 3), (6, 5), (3, 11), (5, 18), (12, 21), (19, 18))
        stroke((15, 10), (21, 10), (21, 4))
    elif name == "share":
        circle(5, 12, 2.5)
        circle(19, 5, 2.5)
        circle(19, 19, 2.5)
        stroke((7, 11), (17, 6))
        stroke((7, 13), (17, 18))
    elif name == "calendar":
        stroke((4, 5), (20, 5), (20, 21), (4, 21), (4, 5))
        stroke((4, 10), (20, 10))
        stroke((8, 2), (8, 7))
        stroke((16, 2), (16, 7))
    elif name == "user":
        circle(12, 7, 4)
        stroke((3, 21), (4, 17), (8, 14), (16, 14), (20, 17), (21, 21))
    elif name == "download":
        stroke((12, 3), (12, 16))
        stroke((6, 10), (12, 16), (18, 10))
        stroke((4, 17), (4, 21), (20, 21), (20, 17))
    return {"width": 24, "height": 24, "shapes": shapes}


def drawing_svg(drawing: Mapping[str, Any], *, label: str = "") -> str:
    """Render validated internal primitives without any external resources."""
    w, h = float(drawing["width"]), float(drawing["height"])
    items = []
    for s in drawing.get("shapes", ()):
        color = escape(str(s.get("color", "#212121")), quote=True)
        # Android #AARRGGBB is not SVG's #RRGGBBAA.
        if len(color) == 9 and color.startswith("#"):
            color = f"#{color[3:]}{color[1:3]}"
        t = s["type"]
        if t == "line":
            pts = " ".join(f"{float(x):g},{float(y):g}" for x, y in s["points"])
            items.append(
                f'<polyline points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="{float(s.get("width", 2)):g}" '
                'stroke-linecap="round" stroke-linejoin="round"/>'
            )
        elif t == "rect":
            items.append(
                f'<rect x="{float(s["x"]):g}" y="{float(s["y"]):g}" '
                f'width="{float(s["w"]):g}" height="{float(s["h"]):g}" fill="{color}"/>'
            )
        elif t == "circle":
            fill = color if s.get("fill", True) else "none"
            items.append(
                f'<circle cx="{float(s["x"]):g}" cy="{float(s["y"]):g}" '
                f'r="{float(s["r"]):g}" fill="{fill}" stroke="{color}" '
                f'stroke-width="{float(s.get("width", 1)):g}"/>'
            )
        elif t == "sector":
            cx, cy, r = float(s["x"]), float(s["y"]), float(s["r"])
            start, sweep = float(s["start"]), float(s["sweep"])
            if sweep >= 359.999:
                items.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{color}"/>')
            elif sweep > 0:
                a, b = math.radians(start), math.radians(start + sweep)
                x1, y1 = cx + r * math.cos(a), cy + r * math.sin(a)
                x2, y2 = cx + r * math.cos(b), cy + r * math.sin(b)
                path = (
                    f"M {cx:g},{cy:g} L {x1:g},{y1:g} A {r:g},{r:g} 0 "
                    f"{1 if sweep > 180 else 0} 1 {x2:g},{y2:g} Z"
                )
                items.append(f'<path d="{path}" fill="{color}"/>')
        elif t == "text":
            anchor = {"center": "middle", "end": "end"}.get(s.get("align"), "start")
            items.append(
                f'<text x="{float(s["x"]):g}" y="{float(s["y"]):g}" '
                f'fill="{color}" font-family="sans-serif" '
                f'font-size="{float(s.get("size", 12)):g}" text-anchor="{anchor}" '
                f'dominant-baseline="hanging">{escape(str(s["text"]))}</text>'
            )
    role = f'role="img" aria-label="{escape(label, quote=True)}"' if label else 'aria-hidden="true"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" '
        f'{role} style="display:block;width:100%;height:100%">' + "".join(items) + "</svg>"
    )
