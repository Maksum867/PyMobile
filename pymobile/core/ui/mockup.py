"""A picture of the screen as the phone draws it: :func:`render_mockup`.

:func:`~pymobile.core.ui.preview.render_png` photographs the *text* preview —
handy in a terminal, but it does not show what a user will see. This module
lays the widget tree out with the same rules as the Android renderer
(``ViewBuilder.java``: vertical containers stretch their children across,
rows wrap them, ``Expanded`` takes a weighted share of the free space, a
``Stack`` overlays full-size children…) and paints it with Material-style
widgets in the app's theme colours: grey raised buttons, underlined text
fields, switches, list rows, dialogs and the snackbar.

It is an approximation, not a screenshot. Fonts differ (the phone uses
Roboto; the mockup uses whatever sans-serif face the machine has), and
system colours such as the action bar and the tint of switches vary between
Android versions and vendors — here they follow the app's ``PRIMARY`` colour.
Sizes are in dp, exactly as in a ``Style``, so spacing and proportions match.

Pillow is the only requirement (``pip install Pillow``).
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...log import get_logger
from .contract import text_value
from .flow import flow_lines, line_offset
from .preview import _SYMBOL_CANDIDATES, _as_node, _has_glyph

__all__ = ["render_mockup"]

_log = get_logger("ui.mockup")

#: Screen size of a typical phone, in dp.
_DEFAULT_WIDTH = 360
_DEFAULT_HEIGHT = 640
_STATUS_BAR = 24
_APP_BAR = 56
#: Line height as a multiple of the font size (Roboto's is ~1.17; the common
#: fallback faces are a little taller).
_LINE = 1.25

_SANS_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Medium.ttf",
    ),
    (
        "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/roboto/Roboto-Medium.ttf",
    ),
    ("/usr/share/fonts/TTF/Roboto-Regular.ttf", "/usr/share/fonts/TTF/Roboto-Medium.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    (
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
)

#: Faces consulted glyph by glyph for what the main face lacks (CJK, symbols).
_FALLBACK_CANDIDATES = (
    *_SYMBOL_CANDIDATES,
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)

_Colour = tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def render_mockup(
    widget_or_tree: Any,
    path: str | os.PathLike[str] | None = None,
    *,
    width: int = _DEFAULT_WIDTH,
    height: int | None = None,
    scale: float = 2.0,
    theme: Any = None,
    title: str = "",
    chrome: bool = True,
    assets: str | os.PathLike[str] | None = None,
) -> Any:
    """Draw the widget tree the way the phone would and save it as an image.

    ``widget_or_tree`` is a live widget, a screen's root, or a rendered tree
    (``StubBridge.last_tree``: its ``snackbar`` and ``theme`` entries are
    honoured). Sizes are in dp; ``scale`` is the pixel density of the image
    (2 → a 360 dp wide screen becomes 720 px).

    ``height=None`` grows the picture until the whole tree fits, which is the
    most useful view of a long, scrolling screen; pass ``height=640`` to see
    exactly the first screenful of a typical phone. ``theme`` is a
    :class:`~pymobile.core.ui.theme.Theme`, ``"light"``/``"dark"`` or a
    colour dict; by default the tree's own theme (or the light one) is used.
    ``title`` is shown in the action bar (the app name on a device);
    ``chrome=False`` leaves out the status and action bars. ``assets`` is the
    directory ``Image`` sources are resolved against.

    Returns ``path`` when one is given, otherwise the ``PIL.Image``.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError(
            "The PNG mockup needs Pillow; install it with `pip install Pillow`."
        ) from exc

    tree = widget_or_tree if isinstance(widget_or_tree, dict) else None
    node = _as_node(widget_or_tree)
    if width < 120:
        raise ValueError("width must be at least 120 dp")
    if scale <= 0:
        raise ValueError("scale must be positive")

    palette = _palette(theme if theme is not None else (tree or {}).get("theme"))
    fonts = _Fonts(ImageFont, scale)
    layout = _Layout(fonts, palette, Path(assets) if assets else None, scale)

    top = (_STATUS_BAR + _APP_BAR) if chrome else 0
    viewport = (height if height is not None else _DEFAULT_HEIGHT) - top
    root = layout.layout(node, float(width), fill=True, force_h=None)
    if root is not None and root.outer_h < viewport:
        # The root view is MATCH_PARENT in both directions on the device, so
        # a short tree still gets the whole screen (Expanded, end alignment).
        root = layout.layout(node, float(width), fill=True, force_h=float(viewport))
    content_h = root.outer_h if root is not None else 0.0
    total_h = height if height is not None else top + max(viewport, math.ceil(content_h))

    image = Image.new(
        "RGB", (round(width * scale), round(total_h * scale)), palette["BACKGROUND"][:3]
    )
    painter = _Painter(ImageDraw, image, scale, fonts)
    if root is not None:
        painter.paint(root, 0.0, float(top))
    screen = (0.0, float(top), float(width), float(total_h - top))
    # Stacking order of the device: content, snackbar (part of the content
    # view), action bar; a dialog is a window of its own that dims the whole
    # activity — action bar included, status bar not.
    snackbar = (tree or {}).get("snackbar")
    if isinstance(snackbar, dict) and snackbar.get("message") is not None:
        layout.paint_snackbar(painter, snackbar, screen)
    if chrome:
        layout.paint_chrome(painter, float(width), title or str((tree or {}).get("screen") or ""))
    window = (
        0.0,
        float(_STATUS_BAR if chrome else 0),
        float(width),
        float(total_h - (_STATUS_BAR if chrome else 0)),
    )
    for dialog in layout.dialogs:
        layout.paint_dialog(painter, dialog, window)

    if path is None:
        return image
    image.save(os.fspath(path))
    return os.fspath(path)


# ---------------------------------------------------------------------------
# colours
# ---------------------------------------------------------------------------
def _rgba(value: Any, fallback: _Colour = (0, 0, 0, 255)) -> _Colour:
    """``#RGB``/``#RRGGBB``/``#AARRGGBB`` (Android order) → RGBA."""
    if isinstance(value, tuple) and len(value) == 4:
        return value
    text = str(value or "").strip()
    if not text.startswith("#"):
        return fallback
    digits = text[1:]
    try:
        if len(digits) == 3:
            r, g, b = (int(ch * 2, 16) for ch in digits)
            return (r, g, b, 255)
        if len(digits) == 6:
            return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 255)
        if len(digits) == 8:
            a = int(digits[0:2], 16)
            return (int(digits[2:4], 16), int(digits[4:6], 16), int(digits[6:8], 16), a)
    except ValueError:
        pass
    return fallback


def _with_alpha(colour: _Colour, alpha: float) -> _Colour:
    return (colour[0], colour[1], colour[2], round(colour[3] * alpha))


def _mix(colour: _Colour, other: _Colour, amount: float) -> _Colour:
    return (
        round(colour[0] + (other[0] - colour[0]) * amount),
        round(colour[1] + (other[1] - colour[1]) * amount),
        round(colour[2] + (other[2] - colour[2]) * amount),
        255,
    )


def _palette(theme: Any) -> dict[str, Any]:
    """Resolve the colours the Java renderer would use (``ViewBuilder.setTheme``)."""
    colours: dict[str, str] = {}
    dark = False
    if isinstance(theme, str):
        from .theme import Theme

        theme = Theme.dark() if theme.strip().lower() == "dark" else Theme.light()
    if theme is not None and hasattr(theme, "as_dict"):
        colours = {str(k).upper(): str(v) for k, v in theme.as_dict().items()}
        dark = bool(getattr(theme, "is_dark", False))
    elif isinstance(theme, dict):
        colours = {str(k).upper(): str(v) for k, v in theme.items() if isinstance(v, str)}
        dark = bool(theme.get("dark", False))
    white = (255, 255, 255, 255)
    palette: dict[str, Any] = {
        "dark": dark,
        "PRIMARY": _rgba(colours.get("PRIMARY"), (0x3F, 0x51, 0xB5, 255)),
        "BACKGROUND": _rgba(colours.get("BACKGROUND"), (18, 18, 18, 255) if dark else white),
        # Like the device: the light preset's #F5F5F5 surface is replaced by white.
        "SURFACE": _rgba(colours.get("SURFACE"), (30, 30, 30, 255)) if dark else white,
        "TEXT": _rgba(
            colours.get("TEXT"), (0xEE, 0xEE, 0xEE, 255) if dark else (0x21, 0x21, 0x21, 255)
        ),
        "MUTED": _rgba(
            colours.get("TEXT_MUTED"), (0x9E, 0x9E, 0x9E, 255) if dark else (0x75, 0x75, 0x75, 255)
        ),
        "ON_PRIMARY": white,
        # Material widget colours of Theme.Material(.Light).
        "BUTTON": (0x5A, 0x59, 0x5B, 255) if dark else (0xD6, 0xD7, 0xD7, 255),
        "BUTTON_TEXT": white if dark else (0, 0, 0, 0xDE),
        "CONTROL": (255, 255, 255, 0xB3) if dark else (0, 0, 0, 0x8A),
        "TRACK_OFF": (255, 255, 255, 0x4D) if dark else (0, 0, 0, 0x61),
        "THUMB_OFF": (0xBD, 0xBD, 0xBD, 255) if dark else (0xFA, 0xFA, 0xFA, 255),
        "DIVIDER": (0, 0, 0, 0x1F),
    }
    return palette


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------
class _Fonts:
    """Sans-serif faces at dp sizes, with per-glyph fallback."""

    def __init__(self, image_font: Any, scale: float) -> None:
        self._image_font = image_font
        self._scale = scale
        self._cache: dict[tuple[str, float], Any] = {}
        self._glyph: dict[tuple[str, str], bool] = {}
        self.regular, self.bold = self._pick_sans()
        self._fallbacks = [p for p in _FALLBACK_CANDIDATES if Path(p).is_file()]
        self._warned: set[str] = set()

    @staticmethod
    def _pick_sans() -> tuple[str, str]:
        override = os.environ.get("PYMOBILE_PREVIEW_FONT", "")
        if override and Path(override).is_file():
            bold = os.environ.get("PYMOBILE_PREVIEW_FONT_BOLD", "")
            return override, bold if bold and Path(bold).is_file() else override
        for regular, bold in _SANS_CANDIDATES:
            if Path(regular).is_file():
                return regular, bold if Path(bold).is_file() else regular
        return "", ""

    def font(self, path: str, size: float) -> Any:
        key = (path, size)
        font = self._cache.get(key)
        if font is None:
            pixels = max(6, round(size * self._scale))
            try:
                font = self._image_font.truetype(path, pixels) if path else None
            except OSError:
                font = None
            if font is None:
                try:
                    font = self._image_font.load_default(pixels)
                except TypeError:  # pragma: no cover - Pillow < 10.1
                    font = self._image_font.load_default()
            self._cache[key] = font
        return font

    def _covers(self, path: str, font: Any, ch: str) -> bool:
        key = (path, ch)
        known = self._glyph.get(key)
        if known is None:
            known = self._glyph[key] = _has_glyph(font, ch)
        return known

    def runs(self, text: str, size: float, bold: bool = False) -> list[tuple[Any, str]]:
        """Split ``text`` into (font, chunk) runs, patching missing glyphs."""
        main_path = self.bold if bold else self.regular
        main = self.font(main_path, size)
        runs: list[tuple[Any, str]] = []
        for ch in text:
            chosen = main
            if not self._covers(main_path, main, ch):
                for path in self._fallbacks:
                    candidate = self.font(path, size)
                    if self._covers(path, candidate, ch):
                        chosen = candidate
                        break
                else:
                    if ch not in self._warned and len(self._warned) < 16:
                        self._warned.add(ch)
                        _log.debug("no mockup font covers %r", ch)
            if runs and runs[-1][0] is chosen:
                runs[-1] = (chosen, runs[-1][1] + ch)
            else:
                runs.append((chosen, ch))
        return runs

    def width(self, text: str, size: float, bold: bool = False) -> float:
        total = 0.0
        for font, chunk in self.runs(text, size, bold):
            try:
                total += float(font.getlength(chunk))
            except AttributeError:  # pragma: no cover - bitmap font
                total += len(chunk) * size * self._scale * 0.6
        return total / self._scale

    def wrap(self, text: str, size: float, bold: bool, max_w: float) -> list[str]:
        """Greedy word wrap; words longer than a line are broken."""
        lines: list[str] = []
        for paragraph in str(text).split("\n"):
            current = ""
            for word in paragraph.split(" "):
                candidate = f"{current} {word}" if current else word
                if self.width(candidate, size, bold) <= max_w or not candidate.strip():
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = ""
                while self.width(word, size, bold) > max_w and len(word) > 1:
                    cut = len(word) - 1
                    while cut > 1 and self.width(word[:cut], size, bold) > max_w:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                current = word
            lines.append(current)
        return lines or [""]


# ---------------------------------------------------------------------------
# boxes and painting
# ---------------------------------------------------------------------------
_PaintFn = Callable[["_Painter", float, float, "_Box"], None]


class _Box:
    """A laid-out view: border-box size, margins, own painting and children."""

    __slots__ = ("w", "h", "margin", "paint", "kids", "style", "fade", "clip")

    def __init__(self, w: float, h: float, paint: _PaintFn | None = None) -> None:
        self.w = w
        self.h = h
        self.margin = (0.0, 0.0, 0.0, 0.0)
        self.paint = paint
        self.kids: list[tuple[float, float, _Box]] = []
        self.style: dict[str, Any] = {}
        self.fade = 1.0
        self.clip = False

    @property
    def outer_w(self) -> float:
        return self.w + self.margin[0] + self.margin[2]

    @property
    def outer_h(self) -> float:
        return self.h + self.margin[1] + self.margin[3]

    def set_outer_h(self, value: float) -> None:
        self.h = max(0.0, value - self.margin[1] - self.margin[3])

    def set_outer_w(self, value: float) -> None:
        self.w = max(0.0, value - self.margin[0] - self.margin[2])


class _Painter:
    def __init__(self, image_draw: Any, image: Any, scale: float, fonts: _Fonts) -> None:
        self._image_draw = image_draw
        self.image = image
        self.draw = image_draw.Draw(image, "RGBA")
        self.s = scale
        self.fonts = fonts
        self.fade = 1.0

    def _c(self, colour: _Colour | None) -> _Colour | None:
        if colour is None:
            return None
        return (colour[0], colour[1], colour[2], round(colour[3] * self.fade))

    # -- primitives --------------------------------------------------------
    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: _Colour | None = None,
        *,
        radius: float = 0,
        outline: _Colour | None = None,
        width: float = 1,
    ) -> None:
        if w <= 0 or h <= 0:
            return
        s = self.s
        box = [x * s, y * s, (x + w) * s - 1, (y + h) * s - 1]
        if box[2] < box[0] or box[3] < box[1]:
            return
        stroke = max(1, round(width * s))
        radius_px = min(radius * s, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
        if radius_px > 0.5:
            self.draw.rounded_rectangle(
                box, radius=radius_px, fill=self._c(fill), outline=self._c(outline), width=stroke
            )
        else:
            self.draw.rectangle(box, fill=self._c(fill), outline=self._c(outline), width=stroke)

    def shadow(
        self, x: float, y: float, w: float, h: float, *, radius: float, elevation: float
    ) -> None:
        """Soft drop shadow built from a few translucent layers (no blur pass)."""
        if elevation <= 0:
            return
        steps = 4
        for i in range(steps, 0, -1):
            spread = elevation * i / steps * 0.6
            self.rect(
                x - spread * 0.5,
                y + elevation * 0.35 - spread * 0.2,
                w + spread,
                h + spread,
                (0, 0, 0, round(40 / steps)),
                radius=radius + spread,
            )

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        fill: _Colour | None = None,
        outline: _Colour | None = None,
        width: float = 1,
    ) -> None:
        s = self.s
        self.draw.ellipse(
            [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
            fill=self._c(fill),
            outline=self._c(outline),
            width=max(1, round(width * s)),
        )

    def line(self, points: list[tuple[float, float]], fill: _Colour, width: float = 1) -> None:
        s = self.s
        self.draw.line(
            [(px * s, py * s) for px, py in points],
            fill=self._c(fill),
            width=max(1, round(width * s)),
            joint="curve",
        )

    def polygon(self, points: list[tuple[float, float]], fill: _Colour) -> None:
        s = self.s
        self.draw.polygon([(px * s, py * s) for px, py in points], fill=self._c(fill))

    def arc(
        self, cx: float, cy: float, r: float, start: float, end: float, fill: _Colour, width: float
    ) -> None:
        s = self.s
        self.draw.arc(
            [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
            start,
            end,
            fill=self._c(fill),
            width=max(1, round(width * s)),
        )

    def text(
        self, x: float, y: float, text: str, size: float, colour: _Colour, *, bold: bool = False
    ) -> None:
        """Draw one line; ``y`` is the top of a ``size * _LINE`` high line box."""
        s = self.s
        px = x * s
        for font, chunk in self.fonts.runs(text, size, bold):
            try:
                ascent, descent = font.getmetrics()
            except AttributeError:  # pragma: no cover - bitmap font
                ascent, descent = round(size * s * 0.8), round(size * s * 0.2)
            top = y * s + (size * _LINE * s - (ascent + descent)) / 2
            self.draw.text((px, top), chunk, font=font, fill=self._c(colour))
            try:
                px += float(font.getlength(chunk))
            except AttributeError:  # pragma: no cover
                px += len(chunk) * size * s * 0.6

    def lines(
        self,
        x: float,
        y: float,
        w: float,
        lines: list[str],
        size: float,
        colour: _Colour,
        *,
        bold: bool = False,
        align: str = "",
    ) -> None:
        for index, line in enumerate(lines):
            dx = 0.0
            if align in ("center", "end", "right"):
                spare = w - self.fonts.width(line, size, bold)
                dx = spare / 2 if align == "center" else spare
            self.text(x + max(0.0, dx), y + index * size * _LINE, line, size, colour, bold=bold)

    # -- tree --------------------------------------------------------------
    def paint(self, box: _Box, x: float, y: float) -> None:
        bx, by = x + box.margin[0], y + box.margin[1]
        saved = self.fade
        self.fade *= box.fade
        style = box.style
        radius = float(style.get("corner_radius") or 0)
        elevation = float(style.get("elevation") or 0)
        if elevation > 0:
            self.shadow(bx, by, box.w, box.h, radius=radius, elevation=elevation)
        background = style.get("background")
        if background is not None or radius or elevation:
            fill = _rgba(background, (0, 0, 0, 0)) if background is not None else None
            if fill is None and elevation:
                fill = style.get("_surface")
            if fill is not None:
                self.rect(bx, by, box.w, box.h, fill, radius=radius)
        if box.paint is not None:
            box.paint(self, bx, by, box)
        if box.clip:
            kids = list(box.kids)

            def draw_kids(p: _Painter) -> None:
                for dx, dy, kid in kids:
                    p.paint(kid, bx + dx, by + dy)

            self._clip_paint(bx, by, box.w, box.h, draw_kids)
        else:
            for dx, dy, kid in box.kids:
                self.paint(kid, bx + dx, by + dy)
        self.fade = saved

    def _clip_paint(
        self, x: float, y: float, w: float, h: float, draw: Callable[[_Painter], None]
    ) -> None:
        # Paint into a copy, then paste back only the clip rectangle.
        s = self.s
        box = (
            max(0, round(x * s)),
            max(0, round(y * s)),
            min(self.image.width, round((x + w) * s)),
            min(self.image.height, round((y + h) * s)),
        )
        backup = self.image.copy()
        draw(self)
        if box[2] > box[0] and box[3] > box[1]:
            inside = self.image.crop(box)
            self.image.paste(backup)
            self.image.paste(inside, box)
        else:
            self.image.paste(backup)


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------
_VERTICAL = ("Column", "Container", "SafeArea", "RadioGroup", "List")
_BUTTON_LIKE = ("Button", "Chip", "DatePicker", "TimePicker")


def _insets(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            left, top, right, bottom = (float(v) for v in value)
        except (TypeError, ValueError):
            return None
        return (left, top, right, bottom)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _flex_of(node: dict[str, Any]) -> float:
    kind = node.get("type")
    if kind in ("Expanded", "Flexible"):
        try:
            return max(1.0, float(node.get("props", {}).get("flex", 1) or 1))
        except (TypeError, ValueError):
            return 1.0
    weight = _number((node.get("style") or {}).get("weight"))
    return weight if weight and weight > 0 else 0.0


class _Layout:
    def __init__(
        self, fonts: _Fonts, palette: dict[str, Any], assets: Path | None, scale: float
    ) -> None:
        self.fonts = fonts
        self.p = palette
        self.assets = assets
        self.scale = scale
        self._dialogs: dict[str, dict[str, Any]] = {}

    @property
    def dialogs(self) -> list[dict[str, Any]]:
        """Visible dialogs, once each however often the tree was measured."""
        return list(self._dialogs.values())

    # -- entry -------------------------------------------------------------
    def layout(
        self,
        node: dict[str, Any],
        max_w: float,
        *,
        fill: bool,
        force_h: float | None = None,
        axis: str = "v",
    ) -> _Box | None:
        """Lay ``node`` out in at most ``max_w`` dp (margins included)."""
        if not node.get("visible", True):
            return None
        kind = str(node.get("type", ""))
        if kind == "Dialog":
            self._dialogs[str(node.get("id") or len(self._dialogs))] = node
            return _Box(0, 0)
        style = dict(node.get("style") or {})
        margin = _insets(style.get("margin")) or (0.0, 0.0, 0.0, 0.0)
        avail = max(0.0, max_w - margin[0] - margin[2])

        fixed_w = _number(style.get("width"))
        wanted = str(style.get("width") or "").lower()
        if fixed_w is not None:
            width: float | None = min(fixed_w, avail) if avail else fixed_w
        elif wanted in ("fill", "match", "match_parent"):
            width = avail
        elif wanted in ("wrap", "wrap_content"):
            width = None
        else:
            width = avail if fill else None
        max_width = _number(style.get("max_width"))
        if max_width is not None:
            avail = min(avail, max_width)
            if width is not None:
                width = min(width, max_width)

        fixed_h = _number(style.get("height"))
        inner_force = (
            fixed_h
            if fixed_h is not None
            else (None if force_h is None else max(0.0, force_h - margin[1] - margin[3]))
        )

        box = self._layout_kind(
            kind,
            node,
            style,
            avail if width is None else width,
            width is not None,
            inner_force,
            axis,
        )
        # Width constraints.
        min_w = _number(style.get("min_width"))
        if width is not None:
            box.w = width
        if min_w is not None:
            box.w = max(box.w, min(min_w, max_w))
        if max_width is not None:
            box.w = min(box.w, max_width)
        # Height constraints.
        if inner_force is not None:
            box.h = inner_force
        min_h = _number(style.get("min_height"))
        if min_h is not None:
            box.h = max(box.h, min_h)
        max_h = _number(style.get("max_height"))
        if max_h is not None:
            box.h = min(box.h, max_h)
        ratio = _number(style.get("aspect_ratio"))
        if ratio and ratio > 0 and fixed_h is None:
            box.h = box.w / ratio

        box.margin = margin
        box.style = style
        if style.get("elevation") and style.get("background") is None:
            style["_surface"] = self.p["SURFACE"]
        if not node.get("enabled", True):
            box.fade = 0.45
        return box

    def _padding(
        self, style: dict[str, Any], default: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return _insets(style.get("padding")) or default

    # -- dispatch ----------------------------------------------------------
    def _layout_kind(
        self,
        kind: str,
        node: dict[str, Any],
        style: dict[str, Any],
        w: float,
        fill: bool,
        force_h: float | None,
        axis: str,
    ) -> _Box:
        props = node.get("props") or {}
        children = [c for c in node.get("children", ()) if isinstance(c, dict)]
        if kind in _VERTICAL or (kind == "ScrollView" and not props.get("horizontal")):
            return self._vertical(kind, node, props, style, children, w, fill, force_h)
        if kind == "Row":
            return self._row(props, style, children, w, fill, force_h)
        if kind == "ScrollView":
            return self._hscroll(props, style, children, w, force_h)
        if kind == "Grid":
            return self._grid(props, style, children, w, force_h)
        if kind == "Wrap":
            return self._wrap(props, style, children, w, fill)
        if kind == "Stack":
            return self._stack(style, children, w, fill, force_h)
        if kind == "PageView":
            return self._vertical("Column", node, props, style, children[:1], w, True, force_h)
        if kind in ("Expanded", "Flexible"):
            pad = self._padding(style, (0, 0, 0, 0))
            inner = (
                self.layout(
                    children[0],
                    w - pad[0] - pad[2],
                    fill=True,
                    force_h=None if force_h is None else force_h - pad[1] - pad[3],
                )
                if children
                else None
            )
            box = _Box(
                w if fill else (inner.outer_w if inner else 0) + pad[0] + pad[2],
                (inner.outer_h if inner else 0) + pad[1] + pad[3],
            )
            if inner is not None:
                box.kids.append((pad[0], pad[1], inner))
            return box
        return self._leaf(kind, node, props, style, w, fill, axis)

    # -- containers --------------------------------------------------------
    def _vertical(
        self,
        kind: str,
        node: dict[str, Any],
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        default_pad = (0.0, 0.0, 0.0, 0.0)
        if kind == "SafeArea":
            m = float(props.get("minimum", 0) or 0)
            default_pad = (m, m, m, m)
        pad = self._padding(style, default_pad)
        cw = max(0.0, w - pad[0] - pad[2])
        spacing = float(props.get("spacing", 0) or 0)
        cross = (
            ""
            if kind in ("ScrollView", "SafeArea", "RadioGroup")
            else text_value(props.get("cross_align") or "")
        )
        align = text_value(props.get("align") or "") if kind == "Column" else ""
        stretch = cross in ("", "stretch")
        if not fill and children:
            # WRAP_CONTENT: LinearLayout measures the children by their
            # content first; MATCH_PARENT children then get that width.
            widths = [
                self._child_v(child, cw, False).outer_w
                for child in children
                if child.get("visible", True) and child.get("type") not in ("Spacer", "Divider")
            ]
            cw = min(cw, max([*widths, 0.0]))
            fill = True

        head: _Box | None = None
        if kind == "List" and props.get("refreshable") and props.get("refreshing"):
            head = _Box(cw, 56, self._paint_refresh_header)

        entries: list[tuple[dict[str, Any], _Box | None, float]] = []
        for child in children:
            if not child.get("visible", True):
                continue
            flex = _flex_of(child)
            if flex and force_h is not None:
                entries.append((child, None, flex))
                continue
            entries.append((child, self._child_v(child, cw, stretch), 0.0))

        used = sum(b.outer_h for _, b, _ in entries if b is not None)
        used += spacing * max(0, len(entries) - 1) + (head.h if head else 0)
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        free = 0.0 if inner_h is None else max(0.0, inner_h - used)
        total_flex = sum(f for _, b, f in entries if b is None)
        resolved: list[_Box] = []
        for child, laid, flex in entries:
            if laid is None:
                share = free * flex / total_flex if total_flex else 0.0
                loose = child.get("props", {}).get("fit") == "loose"
                laid = self.layout(child, cw, fill=True, force_h=None if loose else share) or _Box(
                    0, 0
                )
                if loose:
                    laid.set_outer_h(min(laid.outer_h, share))
            resolved.append(laid)

        content_w = (
            cw if fill else max([b.outer_w for b in resolved] + [head.w if head else 0.0, 0.0])
        )
        box = _Box(content_w + pad[0] + pad[2], 0)
        y = pad[1]
        if head is not None:
            head.w = content_w
            box.kids.append((pad[0], y, head))
            y += head.h
        total = sum(b.outer_h for b in resolved) + spacing * max(0, len(resolved) - 1)
        gap = spacing
        if inner_h is not None and not total_flex:
            leftover = max(0.0, inner_h - total - (head.h if head else 0))
            if align == "center":
                y += leftover / 2
            elif align == "end":
                y += leftover
            elif align == "space_between" and len(resolved) > 1:
                gap += leftover / (len(resolved) - 1)
        for index, laid in enumerate(resolved):
            if index:
                y += gap
            dx = 0.0
            if not stretch and laid.outer_w < content_w:
                if cross == "center":
                    dx = (content_w - laid.outer_w) / 2
                elif cross == "end":
                    dx = content_w - laid.outer_w
            box.kids.append((pad[0] + dx, y, laid))
            y += laid.outer_h
        box.h = y + pad[3]
        if kind == "List" and props.get("has_more"):
            footer = _Box(content_w, 48, self._paint_load_more)
            box.kids.append((pad[0], box.h - pad[3], footer))
            box.h += 48
        return box

    def _child_v(self, child: dict[str, Any], cw: float, stretch: bool) -> _Box:
        kind = child.get("type")
        props = child.get("props") or {}
        if kind == "Spacer":
            size = float(props.get("size", 8) or 0)
            return _Box(cw if stretch else size, size)
        if kind == "Divider":
            return self._divider(props, cw, vertical_parent=True)
        return self.layout(child, cw, fill=stretch, axis="v") or _Box(0, 0)

    def _divider(self, props: dict[str, Any], length: float, *, vertical_parent: bool) -> _Box:
        thickness = max(1.0, float(props.get("thickness", 1) or 1))
        colour = _rgba(props.get("color"), self.p["DIVIDER"])
        inset = float(props.get("inset", 0) or 0)
        upright = bool(props.get("vertical"))

        def paint(p: _Painter, x: float, y: float, b: _Box) -> None:
            p.rect(x, y, b.w, b.h, colour)

        if upright:
            box = _Box(thickness, 24 if vertical_parent else 0, paint)
            box.margin = (0.0, inset, 0.0, inset)
        else:
            box = _Box(max(0.0, length - 2 * inset) if vertical_parent else 24, thickness, paint)
            box.margin = (inset, 0.0, inset, 0.0)
        return box

    def _row(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        spacing = float(props.get("spacing", 0) or 0)
        cross = text_value(props.get("cross_align") or "")
        align = text_value(props.get("align") or "")
        visible = [c for c in children if c.get("visible", True)]
        remaining = cw - spacing * max(0, len(visible) - 1)
        laid: list[_Box | None] = []
        flexes: list[float] = []
        upright_dividers: list[_Box] = []
        for child in visible:
            flex = _flex_of(child)
            flexes.append(flex)
            if flex:
                laid.append(None)
                continue
            kind = child.get("type")
            cprops = child.get("props") or {}
            if kind == "Spacer":
                size = float(cprops.get("size", 8) or 0)
                box = _Box(size, size)
            elif kind == "Divider":
                box = self._divider(cprops, 0, vertical_parent=False)
                if cprops.get("vertical"):
                    upright_dividers.append(box)
            else:
                box = self.layout(child, max(0.0, remaining), fill=False, axis="h") or _Box(0, 0)
            remaining -= box.outer_w
            laid.append(box)
        total_flex = sum(flexes)
        free = max(0.0, remaining)
        for index, child in enumerate(visible):
            if laid[index] is None:
                share = free * flexes[index] / total_flex if total_flex else 0.0
                loose = child.get("props", {}).get("fit") == "loose"
                box = self.layout(child, share, fill=not loose, axis="h") or _Box(0, 0)
                laid[index] = box
        boxes = [b for b in laid if b is not None]
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        row_h = (
            inner_h
            if inner_h is not None
            else max([b.outer_h for b in boxes if b not in upright_dividers] + [0.0])
        )
        for divider in upright_dividers:
            divider.set_outer_h(row_h)
        used = sum(b.outer_w for b in boxes) + spacing * max(0, len(boxes) - 1)
        content_w = cw if fill else min(cw, used)
        x = pad[0]
        gap = spacing
        leftover = max(0.0, content_w - used)
        if not total_flex:
            if align == "center":
                x += leftover / 2
            elif align == "end":
                x += leftover
            elif align == "space_between" and len(boxes) > 1:
                gap += leftover / (len(boxes) - 1)
        box = _Box(content_w + pad[0] + pad[2], row_h + pad[1] + pad[3])
        for index, child_box in enumerate(boxes):
            if index:
                x += gap
            if cross == "stretch":
                child_box.set_outer_h(row_h)
                dy = 0.0
            elif cross == "start":
                dy = 0.0
            elif cross == "end":
                dy = row_h - child_box.outer_h
            else:
                dy = (row_h - child_box.outer_h) / 2
            box.kids.append((x, pad[1] + dy, child_box))
            x += child_box.outer_w
        return box

    def _hscroll(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        spacing = float(props.get("spacing", 0) or 0)
        x = pad[0]
        kids: list[tuple[float, _Box]] = []
        for child in children:
            box = self.layout(child, 100000.0, fill=False, axis="h")
            if box is None:
                continue
            if kids:
                x += spacing
            kids.append((x, box))
            x += box.outer_w
        h = max([b.outer_h for _, b in kids] + [0.0])
        box = _Box(w, h + pad[1] + pad[3])
        box.kids = [(kx, pad[1] + (h - b.outer_h) / 2, b) for kx, b in kids]
        box.clip = True
        return box

    def _grid(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        columns = max(1, int(props.get("columns", 2) or 2))
        row_gap = float(props.get("row_spacing", 0) or 0)
        col_gap = float(props.get("column_spacing", 0) or 0)
        cell_w = max(0.0, (cw - col_gap * (columns - 1)) / columns)
        box = _Box(w, 0)
        y = pad[1]
        # Hidden cells keep their slot, as on the device (GONE inside a weighted row).
        for start in range(0, len(children), columns):
            row = children[start : start + columns]
            laid = [self.layout(child, cell_w, fill=True) for child in row]
            height = max([b.outer_h for b in laid if b is not None] + [0.0])
            if start:
                y += row_gap
            for column, cell in enumerate(laid):
                if cell is None:
                    continue
                box.kids.append((pad[0] + column * (cell_w + col_gap), y, cell))
            y += height
        box.h = y + pad[3]
        return box

    def _flow(
        self, boxes: list[_Box], width: float, gap: float, run_gap: float, align: str
    ) -> tuple[list[tuple[float, float, _Box]], float, float]:
        """Place boxes in lines, as ``ViewBuilder.FlowLayout`` does.

        Returns the positioned boxes, the height used and the widest line.
        """
        placed: list[tuple[float, float, _Box]] = []
        y = 0.0
        widest = 0.0
        for line in flow_lines([b.outer_w for b in boxes], width, gap):
            used = sum(boxes[i].outer_w for i in line) + gap * (len(line) - 1)
            x = line_offset(align, width, used)
            for index in line:
                placed.append((x, y, boxes[index]))
                x += boxes[index].outer_w + gap
            y += max(boxes[i].outer_h for i in line) + run_gap
            widest = max(widest, used)
        return placed, max(0.0, y - run_gap) if placed else 0.0, widest

    def _wrap(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
    ) -> _Box:
        """Wrap: children in natural size, a new line when the row is full."""
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        gap = float(props.get("spacing", 0) or 0)
        run_value = props.get("run_spacing")
        run_gap = gap if run_value is None else float(run_value or 0)
        align = text_value(props.get("align") or "start")
        visible = [c for c in children if c.get("visible", True)]
        boxes = [self.layout(child, cw, fill=False, axis="h") or _Box(0, 0) for child in visible]
        placed, height, widest = self._flow(boxes, cw, gap, run_gap, align)
        if not fill:
            # A wrap-content box is as wide as its longest line, and the shorter
            # lines are aligned inside that width.
            placed, height, widest = self._flow(boxes, widest, gap, run_gap, align)
        box = _Box((cw if fill else widest) + pad[0] + pad[2], height + pad[1] + pad[3])
        box.kids = [(pad[0] + x, pad[1] + y, child) for x, y, child in placed]
        return box

    def _stack(
        self,
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        if not fill and children:
            measured = [self.layout(child, cw, fill=False) for child in children]
            cw = min(cw, max([b.outer_w for b in measured if b is not None] + [0.0]))
            fill = True
        # FrameLayout's default params are MATCH_PARENT: every child fills.
        laid = [
            b for b in (self.layout(child, cw, fill=True) for child in children) if b is not None
        ]
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        height = inner_h if inner_h is not None else max([b.outer_h for b in laid] + [0.0])
        for child_box in laid:
            child_box.set_outer_h(height)
        box = _Box(cw + pad[0] + pad[2], height + pad[1] + pad[3])
        box.kids = [(pad[0], pad[1], b) for b in laid]
        return box

    # -- leaves ------------------------------------------------------------
    def _text_box(
        self,
        text: str,
        size: float,
        colour: _Colour,
        style: dict[str, Any],
        w: float,
        fill: bool,
        *,
        pad: tuple[float, float, float, float] = (0, 0, 0, 0),
        bold: bool = False,
        min_h: float = 0.0,
        min_w: float = 0.0,
        align: str = "",
        background: Callable[[_Painter, float, float, _Box], None] | None = None,
    ) -> _Box:
        size = float(style.get("font_size") or size)
        colour = _rgba(style.get("color"), colour) if style.get("color") else colour
        bold = bool(style.get("bold")) or bold
        align = str(style.get("align") or align)
        pad = self._padding(style, pad)
        inner = max(1.0, w - pad[0] - pad[2])
        lines = self.fonts.wrap(text, size, bold, inner)
        text_w = max((self.fonts.width(line, size, bold) for line in lines), default=0.0)
        text_h = len(lines) * size * _LINE
        box_w = w if fill else min(w, max(min_w, text_w + pad[0] + pad[2]))
        box_h = max(min_h, text_h + pad[1] + pad[3])

        def paint(p: _Painter, x: float, y: float, b: _Box) -> None:
            if background is not None:
                background(p, x, y, b)
            inner_w = b.w - pad[0] - pad[2]
            wrapped = (
                lines
                if inner_w >= inner - 0.5
                else self.fonts.wrap(text, size, bold, max(1.0, inner_w))
            )
            height = len(wrapped) * size * _LINE
            top = (
                y + pad[1] + max(0.0, (b.h - pad[1] - pad[3] - height) / 2) if min_h else y + pad[1]
            )
            p.lines(x + pad[0], top, inner_w, wrapped, size, colour, bold=bold, align=align)

        return _Box(box_w, box_h, paint)

    def _button(
        self,
        text: str,
        style: dict[str, Any],
        w: float,
        fill: bool,
        *,
        fill_colour: _Colour | None = None,
        text_colour: _Colour | None = None,
        flat: bool = False,
    ) -> _Box:
        custom = style.get("background") is not None or style.get("corner_radius") is not None
        bg = fill_colour or self.p["BUTTON"]
        inset_x, inset_y = (0.0, 0.0) if (custom or flat) else (4.0, 6.0)

        def background(p: _Painter, x: float, y: float, b: _Box) -> None:
            if custom:
                return  # painted from the style (GradientDrawable) by the painter
            if flat:
                p.rect(x, y, b.w, b.h, bg)
                return
            p.shadow(
                x + inset_x,
                y + inset_y,
                b.w - 2 * inset_x,
                b.h - 2 * inset_y,
                radius=2,
                elevation=2,
            )
            p.rect(x + inset_x, y + inset_y, b.w - 2 * inset_x, b.h - 2 * inset_y, bg, radius=2)

        return self._text_box(
            text,
            14,
            text_colour or self.p["BUTTON_TEXT"],
            style,
            w,
            fill,
            pad=(16 + inset_x, 10, 16 + inset_x, 10),
            min_h=48,
            min_w=88 if style.get("min_width") is None else 0,
            align="center",
            background=background,
        )

    def _leaf(
        self,
        kind: str,
        node: dict[str, Any],
        props: dict[str, Any],
        style: dict[str, Any],
        w: float,
        fill: bool,
        axis: str,
    ) -> _Box:
        p = self.p
        text = text_value(props.get("text", ""))
        if kind in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "Chart"):
            from .extras_preview import extra_mockup

            drawn: _Box | None = extra_mockup(self, kind, node, w, fill)
            if drawn is not None:
                return drawn
        if kind == "Label":
            return self._text_box(text, 16, p["TEXT"], style, w, fill)
        if kind == "Link":
            return self._text_box(text, 16, p["PRIMARY"], style, w, fill)
        if kind == "Button":
            return self._button(text, style, w, fill)
        if kind == "Chip":
            if props.get("selected"):
                return self._button(
                    text,
                    style,
                    w,
                    fill,
                    flat=True,
                    fill_colour=_rgba(props.get("selectedColor"), p["PRIMARY"]),
                    text_colour=(255, 255, 255, 255),
                )
            return self._button(text, style, w, fill)
        if kind in ("DatePicker", "TimePicker"):
            value = str(
                props.get("value") or ("Pick date" if kind == "DatePicker" else "Pick time")
            )
            return self._button(value, style, w, fill)
        if kind in ("TextInput", "SearchBar"):
            return self._text_input(kind, props, style, w, fill)
        if kind == "Switch":
            return self._switch(bool(props.get("checked")), w)
        if kind == "Checkbox":
            return self._checkbox(
                bool(props.get("checked")), text_value(props.get("text", "")), style, w, fill
            )
        if kind == "RadioButton":
            return self._radio(bool(props.get("selected")), text, style, w, fill)
        if kind == "ProgressBar":
            return self._progress(props, w, fill)
        if kind == "ProgressText":
            return self._progress_text(props, style, w, fill)
        if kind == "Slider":
            return self._slider(props, w, fill)
        if kind == "RatingBar":
            return self._rating(props, w)
        if kind == "Dropdown":
            return self._dropdown(props, style, w, fill)
        if kind == "Badge":
            bg = _rgba(props.get("background"), p["PRIMARY"])

            def badge_bg(pt: _Painter, x: float, y: float, b: _Box) -> None:
                if style.get("background") is None:
                    pt.rect(x, y, b.w, b.h, bg)

            return self._text_box(
                text,
                14,
                _rgba(props.get("color"), (255, 255, 255, 255)),
                style,
                w,
                fill,
                pad=(6, 2, 6, 2),
                align="center",
                background=badge_bg,
            )
        if kind == "Avatar":
            size = float(props.get("size", 48) or 48)
            bg = _rgba(props.get("background"), p["PRIMARY"])
            label = text[:2].upper()

            def avatar_bg(pt: _Painter, x: float, y: float, b: _Box) -> None:
                if style.get("background") is None:
                    pt.rect(x, y, b.w, b.h, bg)

            return self._text_box(
                label,
                14,
                _rgba(props.get("color"), (255, 255, 255, 255)),
                style,
                w,
                fill,
                min_w=size,
                min_h=size,
                align="center",
                background=avatar_bg,
            )
        if kind == "Stepper":
            return self._stepper(props, w, fill)
        if kind == "SegmentedButtons":
            return self._segmented(props, w, fill)
        if kind == "BottomNavigation":
            return self._bottom_nav(props, w)
        if kind == "DataTable":
            return self._table(props, w, fill)
        if kind == "ListTile":
            return self._list_tile(props, style, w)
        if kind == "Image":
            return self._image(props, style, w, fill)
        if kind == "Spacer":
            size = float(props.get("size", 8) or 0)
            return _Box(size, size)
        if kind == "Divider":
            return self._divider(props, w, vertical_parent=axis == "v")

        # Unknown (plugin) widget: a labelled outline, so it is not silently lost.
        def unknown(pt: _Painter, x: float, y: float, b: _Box) -> None:
            pt.rect(x, y, b.w, b.h, None, radius=4, outline=p["MUTED"])

        return self._text_box(
            f"<{kind}>", 13, p["MUTED"], style, w, fill, pad=(8, 8, 8, 8), background=unknown
        )

    def _text_input(
        self, kind: str, props: dict[str, Any], style: dict[str, Any], w: float, fill: bool
    ) -> _Box:
        p = self.p
        value = text_value(props.get("value", ""))
        default_hint = "Search" if kind == "SearchBar" else ""
        placeholder = text_value(props.get("placeholder", "") or default_hint)
        if props.get("password") and value:
            value = "•" * len(value)
        shown, colour = (value, p["TEXT"]) if value else (placeholder, p["MUTED"])
        lines_min = 3 if props.get("multiline") else 1
        size = float(style.get("font_size") or 18)
        line_h = size * _LINE

        def underline(pt: _Painter, x: float, y: float, b: _Box) -> None:
            pt.rect(x + 4, y + b.h - 9, b.w - 8, 1, p["CONTROL"])

        box = self._text_box(
            shown or " ",
            18,
            colour,
            {**style, "color": None} if not value else style,
            w,
            fill,
            pad=(8, 12, 8, 14),
            min_w=64,
            background=underline,
        )
        box.h = max(box.h, lines_min * line_h + 26)
        if not props.get("multiline"):
            box.h = line_h + 26
        return box

    def _switch(self, checked: bool, w: float) -> _Box:
        p = self.p

        def paint(pt: _Painter, x: float, y: float, b: _Box) -> None:
            cy = y + b.h / 2
            tx = x + b.w - 44
            track = _with_alpha(p["PRIMARY"], 0.5) if checked else p["TRACK_OFF"]
            pt.rect(tx, cy - 7, 34, 14, track, radius=7)
            cx = tx + (24 if checked else 10)
            pt.circle(cx, cy + 1, 10.5, (0, 0, 0, 40))
            pt.circle(cx, cy, 10, p["PRIMARY"] if checked else p["THUMB_OFF"])

        return _Box(min(w, 52), 40, paint)

    def _checkbox(
        self, checked: bool, label: str, style: dict[str, Any], w: float, fill: bool
    ) -> _Box:
        p = self.p

        def mark(pt: _Painter, x: float, y: float, b: _Box) -> None:
            cy = y + b.h / 2
            if checked:
                pt.rect(x + 7, cy - 9, 18, 18, p["PRIMARY"], radius=2)
                pt.line(
                    [(x + 10.5, cy), (x + 14.5, cy + 4), (x + 21.5, cy - 4)],
                    (255, 255, 255, 255),
                    2,
                )
            else:
                pt.rect(x + 7, cy - 9, 18, 18, None, radius=2, outline=p["CONTROL"], width=2)

        if not label:
            return _Box(32, 32, mark)
        return self._text_box(
            label, 14, p["TEXT"], style, w, fill, pad=(36, 0, 0, 0), min_h=32, background=mark
        )

    def _radio(
        self, selected: bool, label: str, style: dict[str, Any], w: float, fill: bool
    ) -> _Box:
        p = self.p

        def mark(pt: _Painter, x: float, y: float, b: _Box) -> None:
            cy = y + b.h / 2
            ring = p["PRIMARY"] if selected else p["CONTROL"]
            pt.circle(x + 16, cy, 9, None, ring, 2)
            if selected:
                pt.circle(x + 16, cy, 5, p["PRIMARY"])

        return self._text_box(
            label, 14, p["TEXT"], style, w, fill, pad=(36, 0, 0, 0), min_h=40, background=mark
        )

    def _fraction(self, props: dict[str, Any]) -> float:
        try:
            maximum = float(props.get("maximum", 100) or 100)
            value = float(props.get("value", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, value / maximum if maximum > 0 else 0.0))

    def _bar(self, fraction: float) -> _PaintFn:
        p = self.p

        def paint(pt: _Painter, x: float, y: float, b: _Box) -> None:
            cy = y + b.h / 2
            pt.rect(x, cy - 2, b.w, 4, _with_alpha(p["PRIMARY"], 0.3))
            pt.rect(x, cy - 2, b.w * fraction, 4, p["PRIMARY"])

        return paint

    def _progress(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        p = self.p
        if props.get("indeterminate"):

            def spinner(pt: _Painter, x: float, y: float, b: _Box) -> None:
                pt.arc(x + 24, y + b.h / 2, 18, -90, 190, p["PRIMARY"], 4)

            return _Box(48, 48, spinner)
        return _Box(w, 16, self._bar(self._fraction(props)))

    def _progress_text(
        self, props: dict[str, Any], style: dict[str, Any], w: float, fill: bool
    ) -> _Box:
        box = _Box(w, 16)
        bar = _Box(w, 16, self._bar(self._fraction(props)))
        box.kids.append((0.0, 0.0, bar))
        label = text_value(props.get("text", ""))
        if label:
            text = self._text_box(label, 13, self.p["MUTED"], style, w, False)
            box.kids.append((0.0, 16.0, text))
            box.h += text.h
        return box

    def _slider(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        p = self.p
        try:
            low = float(props.get("minimum", 0) or 0)
            high = float(props.get("maximum", 100) or 100)
            value = float(props.get("value", low) or low)
            fraction = max(0.0, min(1.0, (value - low) / (high - low))) if high > low else 0.0
        except (TypeError, ValueError):
            fraction = 0.0

        def paint(pt: _Painter, x: float, y: float, b: _Box) -> None:
            cy = y + b.h / 2
            left, right = x + 16, x + b.w - 16
            pt.rect(left, cy - 1, right - left, 2, p["TRACK_OFF"])
            tx = left + (right - left) * fraction
            pt.rect(left, cy - 1, tx - left, 2, p["PRIMARY"])
            pt.circle(tx, cy, 8, p["PRIMARY"])

        return _Box(w, 32, paint)

    def _rating(self, props: dict[str, Any], w: float) -> _Box:
        p = self.p
        count = max(1, int(props.get("maximum", 5) or 5))
        try:
            rating = float(props.get("rating", 0) or 0)
        except (TypeError, ValueError):
            rating = 0.0
        # As on the phone: stars shrink to fit rather than run off the screen.
        size = max(1.0, min(40.0, w / count))

        def star(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
            points = []
            for i in range(10):
                angle = math.pi / 2 + i * math.pi / 5
                radius = r if i % 2 == 0 else r * 0.45
                points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
            return points

        def paint(pt: _Painter, x: float, y: float, b: _Box) -> None:
            for i in range(count):
                colour = p["PRIMARY"] if i < round(rating) else p["TRACK_OFF"]
                pt.polygon(star(x + size * i + size / 2, y + b.h / 2, size * 0.42), colour)

        return _Box(min(w, size * count), size, paint)

    def _dropdown(self, props: dict[str, Any], style: dict[str, Any], w: float, fill: bool) -> _Box:
        p = self.p
        options = [str(o) for o in props.get("options", ()) or ()]
        value = text_value(props.get("value") or (options[0] if options else ""))

        def arrow(pt: _Painter, x: float, y: float, b: _Box) -> None:
            ax, cy = x + b.w - 20, y + b.h / 2 + 2
            pt.polygon([(ax - 5, cy - 3), (ax + 5, cy - 3), (ax, cy + 3)], p["CONTROL"])

        return self._text_box(
            value, 16, p["TEXT"], style, w, fill, pad=(8, 12, 40, 12), min_h=48, background=arrow
        )

    def _stepper(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        minus = self._button("\u2212", {}, 88, False)  # the minus sign the device shows
        plus = self._button("+", {}, 88, False)
        value = self._text_box(
            text_value(props.get("value", 0)),
            18,
            self.p["TEXT"],
            {},
            200,
            False,
            pad=(12, 0, 12, 0),
        )
        used = minus.w + value.w + plus.w
        box = _Box(w if fill else used, 48)
        x = (box.w - used) / 2
        box.kids = [
            (x, 0.0, minus),
            (x + minus.w, (48 - value.h) / 2, value),
            (x + minus.w + value.w, 0.0, plus),
        ]
        return box

    def _segmented(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        """A row of segments, each as wide as its text.

        The row scrolls sideways on the phone when the segments do not fit, so the
        picture shows what the screen shows — the segments that fit, the next one
        cut at the edge — rather than squeezed words. ``wrap`` flows them onto
        more lines instead.
        """
        p = self.p
        options = [str(o) for o in props.get("options", ()) or ()]
        selected = text_value(props.get("value", ""))
        wrap = bool(props.get("wrap"))
        # A scrolling row measures its segments without a limit; a wrapping one
        # never offers a segment more than the width of a line.
        offer = w if wrap else 100000.0
        segments: list[_Box] = []
        for option in options:
            if option == selected:
                segments.append(
                    self._button(
                        option,
                        {},
                        offer,
                        False,
                        flat=True,
                        fill_colour=p["PRIMARY"],
                        text_colour=p["ON_PRIMARY"],
                    )
                )
            else:
                segments.append(self._button(option, {}, offer, False, text_colour=p["TEXT"]))
        if wrap:
            placed, height, widest = self._flow(segments, w, 0.0, 0.0, "start")
            box = _Box(w if fill else min(w, widest), height)
            box.kids = placed
            return box
        box = _Box(0, 48)
        x = 0.0
        for seg in segments:
            box.kids.append((x, 0.0, seg))
            x += seg.w
        box.w = w if fill else min(w, x)
        box.clip = x > w
        return box

    def _bottom_nav(self, props: dict[str, Any], w: float) -> _Box:
        p = self.p
        options = [str(o) for o in props.get("options", ()) or ()]
        selected = text_value(props.get("value", ""))
        box = _Box(w, 48)
        if not options:
            return box
        tab_w = w / len(options)
        for index, option in enumerate(options):
            on = option == selected
            tab = self._button(
                option,
                {},
                tab_w,
                True,
                flat=True,
                fill_colour=p["PRIMARY"] if on else p["SURFACE"],
                text_colour=p["ON_PRIMARY"] if on else p["TEXT"],
            )
            box.kids.append((index * tab_w, 0.0, tab))
        return box

    def _table(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        # Like the device: every row is its own wrapping LinearLayout, so the
        # columns are only aligned when the cells happen to be equally wide.
        box = _Box(w if fill else 0, 0)
        y = 0.0
        rows: list[tuple[list[Any], bool]] = []
        if props.get("headers"):
            rows.append((list(props["headers"]), True))
        rows += [(list(r or ()), False) for r in props.get("rows", ()) or ()]
        widest = 0.0
        for cells, bold in rows:
            x = 0.0
            height = 0.0
            for cell in cells:
                cell_box = self._text_box(
                    str(cell),
                    14,
                    self.p["TEXT"],
                    {},
                    max(1.0, w - x),
                    False,
                    pad=(8, 4, 8, 4),
                    bold=bold,
                )
                box.kids.append((x, y, cell_box))
                x += cell_box.w
                height = max(height, cell_box.h)
            widest = max(widest, x)
            y += height
        box.h = y
        if not fill:
            box.w = min(w, widest)
        return box

    def _list_tile(self, props: dict[str, Any], style: dict[str, Any], w: float) -> _Box:
        p = self.p
        pad = self._padding(style, (12, 10, 12, 10))
        inner = max(1.0, w - pad[0] - pad[2])
        trailing = text_value(props.get("trailing", "") or "")
        trailing_box = (
            self._text_box(trailing, 18, p["MUTED"], {}, inner / 2, False) if trailing else None
        )
        text_w = inner - (trailing_box.w if trailing_box else 0)
        title = self._text_box(text_value(props.get("title", "")), 16, p["TEXT"], {}, text_w, True)
        subtitle_text = text_value(props.get("subtitle", "") or "")
        subtitle = (
            self._text_box(subtitle_text, 13, p["MUTED"], {}, text_w, True)
            if subtitle_text
            else None
        )
        texts_h = title.h + (subtitle.h if subtitle else 0)
        content_h = max(texts_h, trailing_box.h if trailing_box else 0)
        box = _Box(w, content_h + pad[1] + pad[3])
        top = pad[1] + (content_h - texts_h) / 2
        box.kids.append((pad[0], top, title))
        if subtitle:
            box.kids.append((pad[0], top + title.h, subtitle))
        if trailing_box:
            box.kids.append(
                (pad[0] + text_w, pad[1] + (content_h - trailing_box.h) / 2, trailing_box)
            )
        return box

    def _image(self, props: dict[str, Any], style: dict[str, Any], w: float, fill: bool) -> _Box:
        p = self.p
        source = text_value(props.get("source", "") or "")
        picture = self._load_image(source)
        cover = props.get("fit") == "cover"
        if picture is not None:
            natural_w = picture.width / self.scale
            natural_h = picture.height / self.scale
            box_w = w if fill else min(w, natural_w)
            box_h = natural_h * (box_w / natural_w) if natural_w else natural_h
        else:
            box_w = w if fill else min(w, 160.0)
            box_h = box_w * 9 / 16

        def paint(pt: _Painter, x: float, y: float, b: _Box) -> None:
            if picture is None:
                pt.rect(x, y, b.w, b.h, _with_alpha(p["MUTED"], 0.18))
                cx, cy = x + b.w / 2, y + b.h / 2
                pt.polygon(
                    [
                        (cx - 22, cy + 14),
                        (cx - 6, cy - 6),
                        (cx + 4, cy + 6),
                        (cx + 10, cy),
                        (cx + 22, cy + 14),
                    ],
                    _with_alpha(p["MUTED"], 0.6),
                )
                pt.circle(cx + 10, cy - 12, 4, _with_alpha(p["MUTED"], 0.6))
                return
            self._paste_image(pt, picture, x, y, b.w, b.h, cover)

        return _Box(box_w, box_h, paint)

    def _load_image(self, source: str) -> Any:
        if not source or source.startswith(("http://", "https://", "data:")):
            return None
        candidates = [Path(source)]
        if self.assets is not None:
            candidates += [self.assets / source, self.assets / "assets" / source]
        for candidate in candidates:
            if candidate.is_file():
                try:
                    from PIL import Image  # type: ignore

                    with Image.open(candidate) as opened:
                        return opened.convert("RGBA")
                except OSError:
                    return None
        return None

    def _paste_image(
        self, pt: _Painter, picture: Any, x: float, y: float, w: float, h: float, cover: bool
    ) -> None:
        s = pt.s
        target_w, target_h = max(1, round(w * s)), max(1, round(h * s))
        ratio = (max if cover else min)(target_w / picture.width, target_h / picture.height)
        size = (max(1, round(picture.width * ratio)), max(1, round(picture.height * ratio)))
        resized = picture.resize(size)
        if cover:
            left = (size[0] - target_w) // 2
            top = (size[1] - target_h) // 2
            resized = resized.crop((left, top, left + target_w, top + target_h))
            offset = (round(x * s), round(y * s))
        else:
            offset = (
                round(x * s) + (target_w - size[0]) // 2,
                round(y * s) + (target_h - size[1]) // 2,
            )
        pt.image.paste(resized, offset, resized)

    # -- decorations -------------------------------------------------------
    def _paint_refresh_header(self, pt: _Painter, x: float, y: float, b: _Box) -> None:
        pt.arc(x + b.w / 2, y + b.h / 2, 12, -90, 200, self.p["PRIMARY"], 3)

    def _paint_load_more(self, pt: _Painter, x: float, y: float, b: _Box) -> None:
        pt.arc(x + b.w / 2, y + b.h / 2, 10, -90, 200, self.p["PRIMARY"], 3)

    def paint_chrome(self, pt: _Painter, width: float, title: str) -> None:
        primary = self.p["PRIMARY"]
        white = (255, 255, 255, 255)
        pt.rect(0, 0, width, _STATUS_BAR, _mix(primary, (0, 0, 0, 255), 0.2))
        pt.text(16, (_STATUS_BAR - 13 * _LINE) / 2, "12:00", 13, white)
        # battery and signal
        pt.rect(width - 26, 7, 9, 12, white, radius=1)
        pt.rect(width - 23.5, 5.5, 4, 2, white)
        pt.polygon([(width - 46, 19), (width - 34, 19), (width - 34, 7)], white)
        pt.rect(0, _STATUS_BAR, width, _APP_BAR, primary)
        for i in range(4):
            pt.rect(0, _STATUS_BAR + _APP_BAR + i, width, 1, (0, 0, 0, 36 - i * 9))
        if title:
            text = title
            while text and self.fonts.width(text, 20, True) > width - 32:
                text = text[:-2] + "…" if len(text) > 2 else ""
            pt.text(16, _STATUS_BAR + (_APP_BAR - 20 * _LINE) / 2, text, 20, white, bold=True)

    def paint_dialog(
        self, pt: _Painter, node: dict[str, Any], screen: tuple[float, float, float, float]
    ) -> None:
        sx, sy, sw, sh = screen
        props = node.get("props") or {}
        sheet = bool(props.get("sheet"))
        box_w = sw if sheet else round(sw * 0.88)
        inner_w = box_w - 32
        title = text_value(props.get("title", "") or "")
        column = {"type": "Column", "children": node.get("children", []), "props": {}}
        content = self.layout(column, inner_w, fill=True)
        head = (
            self._text_box(
                title, 18, self.p["TEXT"], {}, inner_w, True, bold=True, pad=(0, 0, 0, 8)
            )
            if title
            else None
        )
        box_h = 32 + (head.h if head else 0) + (content.outer_h if content else 0)
        pt.rect(sx, sy, sw, sh, (0, 0, 0, 153))
        x = sx + (sw - box_w) / 2
        y = sy + sh - box_h if sheet else sy + max(16.0, (sh - box_h) / 2)
        r = 16 if sheet else 12
        pt.shadow(x, y, box_w, box_h, radius=r, elevation=12)
        pt.rect(x, y, box_w, box_h, self.p["SURFACE"], radius=r)
        if sheet:
            pt.rect(x, y + r, box_w, box_h - r, self.p["SURFACE"])
        top = y + 16
        if head is not None:
            pt.paint(head, x + 16, top)
            top += head.h
        if content is not None:
            pt.paint(content, x + 16, top)

    def paint_snackbar(
        self, pt: _Painter, data: dict[str, Any], screen: tuple[float, float, float, float]
    ) -> None:
        sx, sy, sw, sh = screen
        dark = self.p["dark"]
        bg = (0xE6, 0xE6, 0xE6, 255) if dark else (0x32, 0x32, 0x32, 255)
        fg = (0x21, 0x21, 0x21, 255) if dark else (255, 255, 255, 255)
        action_colour = (
            self.p["PRIMARY"] if dark else _mix(self.p["PRIMARY"], (255, 255, 255, 255), 0.45)
        )
        action = str(data.get("action") or "")
        box_w = sw - 24
        action_w = self.fonts.width(action, 14, True) + 32 if action else 0.0
        message = self._text_box(
            str(data.get("message", "")),
            14,
            fg,
            {},
            box_w - action_w,
            True,
            pad=(16, 14, 8, 14),
            min_h=48,
        )
        box_h = max(48.0, message.h)
        x, y = sx + 12, sy + sh - 12 - box_h
        pt.shadow(x, y, box_w, box_h, radius=4, elevation=6)
        pt.rect(x, y, box_w, box_h, bg, radius=4)
        message.h = box_h
        pt.paint(message, x, y + (box_h - message.h) / 2)
        if action:
            pt.text(
                x + box_w - action_w + 16,
                y + (box_h - 14 * _LINE) / 2,
                action,
                14,
                action_colour,
                bold=True,
            )
