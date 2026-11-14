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
    # -- second batch: the everyday half of a Material icon set -------------
    "upload",
    "phone",
    "mail",
    "lock",
    "unlock",
    "eye",
    "eye_off",
    "camera",
    "image",
    "bell",
    "bookmark",
    "cart",
    "clock",
    "filter",
    "location",
    "play",
    "pause",
    "stop",
    "wifi",
    "cloud",
    "folder",
    "file",
    "copy",
    "undo",
    "redo",
    "plus_circle",
    "minus_circle",
    "check_circle",
    "close_circle",
    "warning",
    "help",
    "logout",
    "login",
    "grid",
    "list",
    "more",
    "drag",
    "sort",
    "sync",
    "print",
    "mic",
    "volume",
    "battery",
    "signal",
    "flag",
    "tag",
    "gift",
    "music",
    "video",
    "qr",
)


def finite_number(value: object, name: str) -> float:
    """``value`` as a float, rejecting bools, non-numbers and infinities."""
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
    """``value`` as an int of at least one, rejecting bools and smaller numbers."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def theme_color(widget: Widget, key: str) -> str:
    """The theme colour called ``key``, from the mounted screen or the default theme."""
    screen = widget.screen
    if screen is not None and screen.mounted:
        return screen.app.theme[key]
    return str(getattr(Color, key))


def line(points: list[tuple[float, float]], color: str, width: float = 2) -> dict[str, Any]:
    """A drawing made of a single line through ``points``."""
    return {"type": "line", "points": [list(p) for p in points], "color": color, "width": width}


def icon_drawing(name: str, color: str = "#212121") -> dict[str, Any]:
    """The built-in shapes for ``name`` drawn in ``color``."""
    if name not in ICON_NAMES:
        raise ValueError(f"unknown icon {name!r}; choose from: {', '.join(ICON_NAMES)}")
    Color.validate(color)
    shapes: list[dict[str, Any]] = []

    def stroke(*points: tuple[float, float]) -> None:
        shapes.append(line(list(points), color))

    def polygon(*points: tuple[float, float], filled: bool = False) -> None:
        shapes.append(
            {
                "type": "polygon",
                "points": [list(p) for p in points],
                "color": color,
                "fill": filled,
                "width": 2,
            }
        )

    def arc(x: float, y: float, radius: float, start: float, sweep: float) -> None:
        """A stroked arc, emitted as a polyline.

        A filled ``sector`` is the only arc every renderer already draws, and a
        sector is a wedge: the shackle of a lock and the waves of a Wi-Fi icon
        came out as black crescents. A polyline is stroke-only, so the same
        points draw a line everywhere.
        """
        steps = max(2, int(abs(sweep) / 12) + 2)
        points = [
            (
                x + radius * math.cos(math.radians(start + sweep * index / steps)),
                y + radius * math.sin(math.radians(start + sweep * index / steps)),
            )
            for index in range(steps + 1)
        ]
        stroke(*points)

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
    elif name == "upload":
        stroke((12, 16), (12, 3))
        stroke((6, 9), (12, 3), (18, 9))
        stroke((4, 17), (4, 21), (20, 21), (20, 17))
    elif name == "phone":
        stroke(
            (7, 2),
            (4, 5),
            (6, 12),
            (12, 18),
            (19, 20),
            (22, 17),
            (19, 13),
            (16, 15),
            (12, 10),
            (9, 8),
            (11, 5),
            (7, 2),
        )
    elif name == "mail":
        stroke((2, 5), (22, 5), (22, 19), (2, 19), (2, 5))
        stroke((2, 6), (12, 13), (22, 6))
    elif name == "lock":
        stroke((5, 11), (19, 11), (19, 21), (5, 21), (5, 11))
        arc(12, 10, 5, 180, 180)
        circle(12, 16, 1.5, filled=True)
    elif name == "unlock":
        stroke((5, 11), (19, 11), (19, 21), (5, 21), (5, 11))
        arc(12, 7, 5, 180, 180)
        circle(12, 16, 1.5, filled=True)
    elif name in ("eye", "eye_off"):
        stroke((2, 12), (6, 7), (12, 5), (18, 7), (22, 12))
        stroke((2, 12), (6, 17), (12, 19), (18, 17), (22, 12))
        circle(12, 12, 3.5)
        if name == "eye_off":
            stroke((4, 20), (20, 4))
    elif name == "camera":
        stroke((3, 7), (8, 7), (9, 4), (15, 4), (16, 7), (21, 7), (21, 20), (3, 20), (3, 7))
        circle(12, 13, 4)
    elif name == "image":
        stroke((3, 4), (21, 4), (21, 20), (3, 20), (3, 4))
        circle(8, 9, 1.8, filled=True)
        stroke((4, 18), (9, 12), (14, 17), (17, 13), (20, 17))
    elif name == "bell":
        stroke((5, 17), (6, 10), (9, 5), (12, 4), (15, 5), (18, 10), (19, 17), (5, 17))
        stroke((4, 17), (20, 17))
        stroke((10, 20), (12, 22), (14, 20))
    elif name == "bookmark":
        stroke((6, 3), (18, 3), (18, 21), (12, 16), (6, 21), (6, 3))
    elif name == "cart":
        circle(9, 20, 1.6, filled=True)
        circle(18, 20, 1.6, filled=True)
        stroke((2, 4), (5, 4), (8, 15), (19, 15), (22, 7), (6, 7))
    elif name == "clock":
        circle(12, 12, 9)
        stroke((12, 6), (12, 12), (16, 15))
    elif name == "filter":
        stroke((3, 5), (21, 5), (14, 13), (14, 20), (10, 18), (10, 13), (3, 5))
    elif name == "location":
        stroke((12, 22), (6, 13), (5, 9), (8, 4), (12, 3), (16, 4), (19, 9), (18, 13), (12, 22))
        circle(12, 10, 2.8)
    elif name == "play":
        polygon((8, 5), (20, 12), (8, 19), filled=True)
    elif name == "pause":
        stroke((9, 5), (9, 19))
        stroke((15, 5), (15, 19))
    elif name == "stop":
        stroke((6, 6), (18, 6), (18, 18), (6, 18), (6, 6))
    elif name == "wifi":
        arc(12, 20, 10, 200, 140)
        arc(12, 20, 6, 200, 140)
        circle(12, 19, 1.8, filled=True)
    elif name == "cloud":
        stroke((7, 18), (5, 14), (7, 10), (11, 9), (15, 10), (18, 13), (19, 18), (7, 18))
    elif name == "folder":
        stroke((2, 6), (9, 6), (11, 9), (22, 9), (22, 20), (2, 20), (2, 6))
    elif name == "file":
        stroke((5, 2), (14, 2), (19, 7), (19, 22), (5, 22), (5, 2))
        stroke((14, 2), (14, 7), (19, 7))
    elif name == "copy":
        stroke((8, 3), (20, 3), (20, 15), (8, 15), (8, 3))
        stroke((4, 7), (4, 21), (16, 21), (16, 17))
    elif name == "undo":
        arc(12, 12, 7, 0, 200)
        stroke((5, 8), (4, 13), (8, 16))
    elif name == "redo":
        arc(12, 12, 7, 340, 200)
        stroke((19, 8), (20, 13), (16, 16))
    elif name == "plus_circle":
        circle(12, 12, 9)
        stroke((12, 7), (12, 17))
        stroke((7, 12), (17, 12))
    elif name == "minus_circle":
        circle(12, 12, 9)
        stroke((7, 12), (17, 12))
    elif name == "check_circle":
        circle(12, 12, 9)
        stroke((7, 12), (11, 16), (17, 8))
    elif name == "close_circle":
        circle(12, 12, 9)
        stroke((9, 9), (15, 15))
        stroke((15, 9), (9, 15))
    elif name == "warning":
        stroke((12, 3), (22, 20), (2, 20), (12, 3))
        stroke((12, 9), (12, 14))
        circle(12, 17, 1.2, filled=True)
    elif name == "help":
        circle(12, 12, 9)
        stroke((9, 9), (10, 7), (13, 6), (15, 8), (14, 11), (12, 13), (12, 15))
        circle(12, 18, 1.2, filled=True)
    elif name in ("logout", "login"):
        stroke((10, 4), (4, 4), (4, 20), (10, 20))
        arrow = (3, 12) if name == "logout" else (21, 12)
        tip = (2, 12) if name == "logout" else (22, 12)
        stroke((3, 12), (21, 12))
        stroke((17, 8), (21, 12), (17, 16) if name == "logout" else (17, 8))
        del arrow, tip
    elif name == "grid":
        for x0, y0 in ((3, 3), (13, 3), (3, 13), (13, 13)):
            stroke((x0, y0), (x0 + 8, y0), (x0 + 8, y0 + 8), (x0, y0 + 8), (x0, y0))
    elif name == "list":
        for y in (6, 12, 18):
            circle(5, y, 1.4, filled=True)
            stroke((9, y), (21, y))
    elif name == "more":
        circle(5, 12, 1.8, filled=True)
        circle(12, 12, 1.8, filled=True)
        circle(19, 12, 1.8, filled=True)
    elif name == "drag":
        for x, y in ((9, 8), (15, 8), (9, 12), (15, 12), (9, 16), (15, 16)):
            circle(x, y, 1.3, filled=True)
    elif name == "sort":
        stroke((4, 7), (16, 7))
        stroke((4, 12), (12, 12))
        stroke((4, 17), (8, 17))
        stroke((18, 4), (18, 20))
        stroke((15, 17), (18, 20), (21, 17))
    elif name == "sync":
        arc(12, 12, 8, 300, 260)
        stroke((20, 4), (20, 9), (15, 9))
    elif name == "print":
        stroke((7, 3), (17, 3), (17, 8), (7, 8), (7, 3))
        stroke((3, 8), (21, 8), (21, 16), (17, 16), (17, 21), (7, 21), (7, 16), (3, 16), (3, 8))
        stroke((7, 16), (17, 16))
    elif name == "mic":
        stroke((9, 5), (9, 10), (12, 13), (15, 10), (15, 5), (12, 2), (9, 5))
        stroke((5, 11), (7, 15), (12, 17), (17, 15), (19, 11))
        stroke((12, 17), (12, 21))
        stroke((9, 21), (15, 21))
    elif name == "volume":
        polygon((4, 9), (8, 9), (13, 5), (13, 19), (8, 15), (4, 15), filled=True)
        arc(15, 12, 6, 320, 80)
        arc(15, 12, 9, 320, 80)
    elif name == "battery":
        stroke((2, 7), (19, 7), (19, 17), (2, 17), (2, 7))
        stroke((20, 10), (22, 10), (22, 14), (20, 14))
        stroke((5, 10), (9, 10), (9, 14), (5, 14), (5, 10))
    elif name == "signal":
        stroke((3, 20), (6, 15), (10, 18), (14, 9), (18, 12), (21, 4))
    elif name == "flag":
        stroke((5, 3), (5, 21))
        stroke((5, 4), (18, 4), (15, 8), (18, 12), (5, 12), (5, 4))
    elif name == "tag":
        stroke((3, 11), (11, 3), (21, 3), (21, 13), (13, 21), (3, 11))
        circle(17, 7, 1.5, filled=True)
    elif name == "gift":
        stroke((3, 8), (21, 8), (21, 12), (3, 12), (3, 8))
        stroke((5, 12), (5, 21), (19, 21), (19, 12))
        stroke((12, 8), (12, 21))
        stroke((12, 8), (8, 8), (7, 5), (10, 4), (12, 8))
        stroke((12, 8), (16, 8), (17, 5), (14, 4), (12, 8))
    elif name == "music":
        stroke((9, 18), (9, 5), (20, 3), (20, 16))
        circle(6.5, 18, 2.6, filled=True)
        circle(17.5, 16, 2.6, filled=True)
    elif name == "video":
        stroke((2, 6), (15, 6), (15, 18), (2, 18), (2, 6))
        polygon((15, 11), (22, 7), (22, 17), (15, 13), filled=True)
    elif name == "qr":
        stroke((3, 3), (9, 3), (9, 9), (3, 9), (3, 3))
        stroke((15, 3), (21, 3), (21, 9), (15, 9), (15, 3))
        stroke((3, 15), (9, 15), (9, 21), (3, 21), (3, 15))
        stroke((15, 15), (15, 21))
        stroke((18, 15), (21, 15), (21, 21), (18, 21), (18, 15))
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
        elif t == "polygon":
            pts = " ".join(f"{float(x):g},{float(y):g}" for x, y in s["points"])
            fill = color if s.get("fill", True) else "none"
            stroke = "none" if s.get("fill", True) else color
            items.append(
                f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="{float(s.get("width", 1)):g}" stroke-linejoin="round"/>'
            )
        elif t == "rect":
            fill = color if s.get("fill", True) else "none"
            stroke = "none" if s.get("fill", True) else color
            items.append(
                f'<rect x="{float(s["x"]):g}" y="{float(s["y"]):g}" '
                f'width="{float(s["w"]):g}" height="{float(s["h"]):g}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{float(s.get("width", 1)):g}"/>'
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
