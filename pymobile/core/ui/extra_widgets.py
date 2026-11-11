"""Widgets added in 0.14: QR codes, tag entry, countdowns, Markdown text, signature pad.

``QRCode``, ``TagInput``, ``Countdown`` and ``MarkdownText`` are built from
existing widgets or a :class:`Canvas`, so every renderer already draws them and
no Java is compiled for them. ``SignaturePad`` needs touch events, which a
Canvas cannot receive, so it has a native renderer
(``org.pymobile.app.widgets.SignaturePadRenderer``) that sends its strokes back
to Python as a ``change`` event.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from ...errors import PyMobileError
from .canvas import Canvas, Rect
from .components import Button, Chip, Label, TextInput
from .contract import WidgetProps
from .layout import Column, Expanded, Row, Wrap
from .registry import register_preview_renderer, register_widget_type
from .style import Style
from .widget import Widget

__all__ = ["QRCode", "TagInput", "Countdown", "MarkdownText", "SignaturePad"]

_HEX_COLOUR = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
_ECC_LEVELS = ("L", "M", "Q", "H")


def _check_colour(value: str, prop: str, owner: str) -> str:
    if not isinstance(value, str) or not _HEX_COLOUR.match(value):
        raise ValueError(f"{owner}: {prop} must be #RRGGBB or #AARRGGBB, got {value!r}")
    return value


def _check_positive_int(value: int, prop: str, owner: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TypeError(f"{owner}: {prop} must be a positive int, got {value!r}")
    return value


# -- QRCode -------------------------------------------------------------------


def _load_segno() -> Any:
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PyMobileError(
            "QRCode needs the segno package",
            hint=(
                "Install it for desktop runs with `pip install segno`. For the APK, "
                'declare it in pymobile.toml: dependencies = ["segno"] under [app].'
            ),
        ) from exc
    return segno


class QRCode(Canvas):
    """A QR code for ``text``, drawn as dark rectangles on a light background.

    The matrix comes from the pure-Python ``segno`` package, which the APK
    bundles when it is listed in ``dependencies``. ``ecc`` is the error
    correction level (``"L"``, ``"M"``, ``"Q"`` or ``"H"``); a higher level
    survives more damage and holds less text. ``quiet_zone`` is the blank border
    in modules that scanners need around the code.

    ``size`` is the largest size the code can take: the module is a whole
    number of units, so the drawn code can be a few units smaller than
    ``size``. The real side length is the widget's ``width``/``height``, and
    ``module_size`` is the size of one module.

    A Canvas cannot change its drawing after it is built, so create a new
    ``QRCode`` when the text changes::

        QRCode("https://example.com", size=220)
    """

    def __init__(
        self,
        text: str,
        *,
        size: int = 240,
        ecc: str = "M",
        dark: str = "#000000",
        light: str = "#FFFFFF",
        quiet_zone: int = 4,
        **kwargs: Any,
    ) -> None:
        if not isinstance(text, str) or not text:
            raise TypeError("QRCode: text must be a non-empty string")
        if ecc not in _ECC_LEVELS:
            raise ValueError(f"QRCode: ecc must be one of L, M, Q, H; got {ecc!r}")
        _check_positive_int(size, "size", "QRCode")
        if size < 32:
            raise ValueError("QRCode: size must be at least 32")
        if isinstance(quiet_zone, bool) or not isinstance(quiet_zone, int) or quiet_zone < 0:
            raise TypeError(f"QRCode: quiet_zone must be a non-negative int, got {quiet_zone!r}")
        _check_colour(dark, "dark", "QRCode")
        _check_colour(light, "light", "QRCode")

        segno = _load_segno()
        try:
            code = segno.make(text, error=ecc, micro=False)
        except Exception as exc:  # segno raises its own error types
            raise ValueError(
                f"QRCode: the text does not fit in a QR code at ecc {ecc!r} ({exc})"
            ) from exc

        rows = [list(row) for row in code.matrix]
        modules = len(rows)
        # Whole-pixel modules keep every edge on the grid. Fractional modules
        # leave hairline gaps between neighbouring squares, which scanners miss.
        module = max(1, size // (modules + 2 * quiet_zone))
        side = (modules + 2 * quiet_zone) * module
        rects: list[Rect] = []
        for y, row in enumerate(rows):
            x = 0
            while x < modules:
                if not row[x]:
                    x += 1
                    continue
                start = x
                while x < modules and row[x]:
                    x += 1
                # One rectangle per run of dark modules keeps the drawing small.
                rects.append(
                    Rect(
                        x=(start + quiet_zone) * module,
                        y=(y + quiet_zone) * module,
                        w=(x - start) * module,
                        h=module,
                        color=dark,
                    )
                )
        super().__init__(*rects, width=side, height=side, background=light, **kwargs)
        self.text = text
        self.ecc = ecc
        self.modules = modules
        self.module_size = module


# -- TagInput -----------------------------------------------------------------


class TagInput(Column):
    """A text field whose entries become removable tags.

    Type a tag and press **Add**; tap a tag to remove it. Duplicate and empty
    tags are ignored, and ``max_tags`` caps how many there can be.
    ``on_change`` receives the full list after every add or remove. Tags given
    to the constructor do not trigger it.
    """

    def __init__(
        self,
        tags: Iterable[str] = (),
        *,
        placeholder: str = "Add a tag",
        max_tags: int | None = None,
        on_change: Callable[[list[str]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if max_tags is not None:
            _check_positive_int(max_tags, "max_tags", "TagInput")
        self.max_tags = max_tags
        self.on_change = on_change
        self._chips: list[tuple[str, Chip]] = []
        self._input = TextInput(placeholder=placeholder, accessibility_label="Tag text")
        self._chip_row = Wrap(spacing=8, run_spacing=8)
        entry = Row(
            Expanded(self._input),
            Button("Add", variant="tonal", on_press=self._add_from_input),
            spacing=8,
        )
        super().__init__(entry, self._chip_row, spacing=8, **kwargs)
        for tag in tags:
            text = _normalise_tag(tag)
            if text and text not in self.tags and (max_tags is None or len(self._chips) < max_tags):
                self._insert(text)

    @property
    def tags(self) -> tuple[str, ...]:
        """The current tags, in the order they were added."""
        return tuple(name for name, _ in self._chips)

    def add_tag(self, tag: str) -> bool:
        """Add ``tag``. Returns False when it was empty, a duplicate or over the limit."""
        text = _normalise_tag(tag)
        if not text or text in self.tags:
            return False
        if self.max_tags is not None and len(self._chips) >= self.max_tags:
            return False
        self._insert(text)
        self._changed()
        return True

    def remove_tag(self, tag: str) -> bool:
        """Remove ``tag``. Returns False when it is not in the list."""
        for index, (name, chip) in enumerate(self._chips):
            if name == tag:
                del self._chips[index]
                self._chip_row.remove(chip)
                self._changed()
                return True
        return False

    def clear_tags(self) -> None:
        """Remove every tag."""
        if not self._chips:
            return
        for _, chip in self._chips:
            self._chip_row.remove(chip)
        self._chips.clear()
        self._changed()

    def _insert(self, text: str) -> None:
        chip = Chip(f"{text} x", on_press=partial(self.remove_tag, text))
        self._chips.append((text, chip))
        self._chip_row.add(chip)

    def _add_from_input(self) -> None:
        if self.add_tag(self._input.value):
            self._input.value = ""

    def _changed(self) -> None:
        self.invalidate()
        if self.on_change is not None:
            self.on_change(list(self.tags))


def _normalise_tag(tag: str) -> str:
    return " ".join(str(tag).split())


# -- Countdown ----------------------------------------------------------------


def _format_countdown(seconds: float) -> str:
    whole = max(0, math.ceil(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class Countdown(Label):
    """Shows the time left, counting down to zero.

    Call :meth:`start` from a handler (a button press, for example). Ticks run on
    a background timer and update the label on the UI thread. :meth:`stop`
    pauses it and :meth:`reset` returns it to the full time. ``on_finish`` runs
    once when the time runs out. Stop the countdown in ``on_hide`` if the screen
    can be left while it runs.
    """

    def __init__(
        self,
        seconds: float,
        *,
        on_finish: Callable[[], object] | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            raise ValueError(f"Countdown: seconds must be a positive number, got {seconds!r}")
        self._total = float(seconds)
        self._remaining = float(seconds)
        self._deadline: float | None = None
        self._job: Any = None
        self.on_finish = on_finish
        super().__init__(_format_countdown(self._remaining), **kwargs)

    @property
    def remaining(self) -> float:
        """Seconds left, including the time that passed since :meth:`start`."""
        if self._deadline is None:
            return self._remaining
        return max(0.0, self._deadline - time.monotonic())

    @property
    def running(self) -> bool:
        return self._job is not None

    def start(self) -> None:
        """Start (or resume) the countdown. Needs the widget to be on a screen."""
        if self._job is not None:
            return
        screen = self.screen
        if screen is None:
            raise PyMobileError(
                "Countdown.start() was called before the widget was on a screen",
                hint="Call start() from a handler, or after the screen has been shown.",
            )
        app = screen.app
        self._deadline = time.monotonic() + self._remaining
        # The job thread only hands ticks to the UI thread; it never touches widgets.
        self._job = app.repeat_job(250, lambda: app.dispatch(self._tick))

    def stop(self) -> None:
        """Pause the countdown; :meth:`start` resumes it from the same point."""
        if self._job is None:
            return
        self._remaining = self.remaining
        self._deadline = None
        self._job.cancel()
        self._job = None

    def reset(self) -> None:
        """Stop the countdown and return it to the full time."""
        self.stop()
        self._remaining = self._total
        self.text = _format_countdown(self._total)

    def _tick(self) -> None:
        if self._job is None:
            return  # stopped after the tick was queued
        left = self.remaining
        if left > 0:
            self.text = _format_countdown(left)
            return
        self.stop()
        self._remaining = 0.0
        self.text = _format_countdown(0)
        if self.on_finish is not None:
            self.on_finish()


# -- MarkdownText -------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKUP = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]*)`")


def _inline(text: str) -> str:
    """Drop inline markers, keeping their text: bold, code and link labels."""
    text = _LINK.sub(r"\1", text)
    return _MARKUP.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", text)


def _list_item(marker: str, body: str, font_size: int) -> Widget:
    return Row(
        Label(marker, style=Style(font_size=font_size)),
        Expanded(Label(_inline(body), style=Style(font_size=font_size))),
        spacing=8,
    )


def _markdown_blocks(text: str, font_size: int) -> list[Widget]:
    blocks: list[Widget] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            body = _inline(" ".join(paragraph))
            blocks.append(Label(body, style=Style(font_size=font_size)))
            paragraph.clear()

    code: list[str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                blocks.append(Label("\n".join(code), style=Style(font_size=font_size - 2)))
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if not line.strip():
            flush()
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            size = {1: font_size + 8, 2: font_size + 4, 3: font_size + 2}[level]
            label = _inline(heading.group(2))
            blocks.append(Label(label, style=Style(font_size=size, bold=True)))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            blocks.append(_list_item("•", bullet.group(1), font_size))
            continue
        numbered = _NUMBERED.match(line)
        if numbered:
            flush()
            blocks.append(_list_item(f"{numbered.group(1)}.", numbered.group(2), font_size))
            continue
        paragraph.append(line.strip())
    flush()
    if code is not None:  # an unclosed fence shows its lines rather than vanishing
        blocks.append(Label("\n".join(code), style=Style(font_size=font_size - 2)))
    return blocks


class MarkdownText(Column):
    """Renders a small Markdown subset as native text.

    Supported: ``#``, ``##`` and ``###`` headings, paragraphs, ``-``/``*`` bullet
    lists, ``1.`` numbered lists and fenced code blocks. Inline ``**bold**``,
    ``__bold__``, `` `code` `` and ``[links](url)`` keep their text but lose their
    styling, because a Label has one style. Images, tables and nested lists are
    not supported.
    """

    def __init__(self, text: str, *, font_size: int = 16, **kwargs: Any) -> None:
        if not isinstance(text, str):
            raise TypeError("MarkdownText: text must be a string")
        _check_positive_int(font_size, "font_size", "MarkdownText")
        self.source = text
        super().__init__(*_markdown_blocks(text, font_size), spacing=8, **kwargs)


# -- SignaturePad -------------------------------------------------------------

Stroke = list[tuple[float, float]]


def _parse_strokes(raw: object) -> list[Stroke]:
    if not isinstance(raw, list):
        raise ValueError("SignaturePad: strokes must be a list")
    strokes: list[Stroke] = []
    for stroke in raw:
        if not isinstance(stroke, list):
            raise ValueError("SignaturePad: each stroke must be a list of points")
        points: Stroke = []
        for point in stroke:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)
            ):
                raise ValueError("SignaturePad: a point must be [x, y]")
            points.append(
                (min(1.0, max(0.0, float(point[0]))), min(1.0, max(0.0, float(point[1]))))
            )
        strokes.append(points)
    return strokes


class SignaturePad(Widget):
    """A box where the user draws with a finger, for signatures and sketches.

    Strokes are reported with **0..1 coordinates** relative to the pad, so the
    same data works at any size. ``on_change`` receives the full list of strokes
    after each stroke the user finishes. Changing the program's view of the pad
    does not call it. Use :meth:`clear` to erase the drawing from Python.

    On the desktop preview, the pad shows as a placeholder: drawing needs touch
    input, which only the phone provides.
    """

    type_name = "SignaturePad"

    def __init__(
        self,
        *,
        height: int = 200,
        stroke_color: str = "#000000",
        stroke_width: float = 4.0,
        background: str = "#FFFFFF",
        on_change: Callable[[list[Stroke]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        _check_positive_int(height, "height", "SignaturePad")
        if isinstance(stroke_width, bool) or not isinstance(stroke_width, (int, float)):
            raise TypeError("SignaturePad: stroke_width must be a number")
        if stroke_width <= 0:
            raise ValueError("SignaturePad: stroke_width must be positive")
        self.pad_height = height
        self.stroke_color = _check_colour(stroke_color, "stroke_color", "SignaturePad")
        self.stroke_width = float(stroke_width)
        self.background = _check_colour(background, "background", "SignaturePad")
        self.on_change = on_change
        self._strokes: list[Stroke] = []
        self._revision = 0
        super().__init__(**kwargs)

    def props(self) -> WidgetProps:
        return {
            **super().props(),
            "height": self.pad_height,
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "background": self.background,
            "revision": self._revision,
        }

    @property
    def strokes(self) -> list[Stroke]:
        """A copy of the drawn strokes, each a list of ``(x, y)`` in 0..1."""
        return [list(stroke) for stroke in self._strokes]

    @property
    def is_empty(self) -> bool:
        return not self._strokes

    def clear(self) -> None:
        """Erase the drawing. The renderer clears on its next update."""
        self._strokes = []
        self._revision += 1
        self.invalidate()

    def _ui_set_value(self, value: str) -> None:
        """Receive the strokes the native pad sends after each finished stroke."""
        try:
            raw = json.loads(value)
        except ValueError as exc:
            raise ValueError("SignaturePad: stroke data is not JSON") from exc
        self._strokes = _parse_strokes(raw)
        if self.on_change is not None:
            self.on_change(self.strokes)


register_widget_type("SignaturePad", android=True, web=False, gui=False, ascii=True)
register_preview_renderer("SignaturePad", "ascii", lambda node: ["[signature pad]"])
