"""Leaf/control widget measurements and paint functions."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from ._mockup_colors import _blend, _Colour, _rgba, _with_alpha
from ._mockup_context import _MockupContext
from ._mockup_paint import _LINE, _Box, _Painter, _PaintFn
from .contract import text_value
from .theme import _readable_on


class _WidgetLayout(_MockupContext):
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
        single_line: bool = False,
    ) -> _Box:
        size = float(style.get("font_size") or size)
        colour = _rgba(style.get("color"), colour) if style.get("color") else colour
        bold = bool(style.get("bold")) or bold
        align = str(style.get("align") or align)
        pad = self._padding(style, pad)
        inner = max(1.0, w - pad[0] - pad[2])

        def wrap(width: float) -> list[str]:
            if single_line:
                return [self.fonts.ellipsize(text, size, bold, width)]
            return self.fonts.wrap(text, size, bold, width)

        lines = wrap(inner)
        text_w = max((self.fonts.width(line, size, bold) for line in lines), default=0.0)
        text_h = len(lines) * size * _LINE
        box_w = w if fill else min(w, max(min_w, text_w + pad[0] + pad[2]))
        box_h = max(min_h, text_h + pad[1] + pad[3])

        def paint(p: _Painter, x: float, y: float, b: _Box) -> None:
            if background is not None:
                background(p, x, y, b)
            inner_w = b.w - pad[0] - pad[2]
            wrapped = lines if inner_w >= inner - 0.5 else wrap(max(1.0, inner_w))
            height = len(wrapped) * size * _LINE
            top = (
                y + pad[1] + max(0.0, (b.h - pad[1] - pad[3] - height) / 2) if min_h else y + pad[1]
            )
            p.lines(x + pad[0], top, inner_w, wrapped, size, colour, bold=bold, align=align)

        return _Box(box_w, box_h, paint)

    #: Fill and text colour of each ButtonVariant in the picture (P1).
    _VARIANTS: ClassVar[dict[str, tuple[str | None, str | _Colour | None]]] = {
        "primary": ("PRIMARY", "ON_PRIMARY"),
        "tonal": ("_TONAL", None),
        "outlined": (None, "PRIMARY"),
        "text": (None, "PRIMARY"),
        "danger": ("ERROR", None),
    }

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
        variant: str = "primary",
    ) -> _Box:
        # An explicit Style(background=…) always wins; without one the variant
        # decides. Before this, ``custom`` made the painter skip the button
        # background entirely, which is why every button came out grey.
        custom = style.get("background") is not None
        radius = float(style["corner_radius"]) if style.get("corner_radius") is not None else 2.0
        fill_key, text_key = self._VARIANTS.get(variant, self._VARIANTS["primary"])
        transparent = variant == "text"
        if fill_colour is None and fill_key is not None and not custom:
            fill_colour = (
                _blend(self.p["PRIMARY"], self.p["SURFACE"], 0.82)
                if fill_key == "_TONAL"
                else self.p[fill_key]
            )
        if text_colour is None and text_key is not None and not custom:
            text_colour = self.p[text_key] if isinstance(text_key, str) else text_key
        if variant == "tonal" and not custom and text_colour is None:
            assert fill_colour is not None
            text_colour = _rgba(_readable_on("#{:02X}{:02X}{:02X}".format(*fill_colour[:3])))
        elif text_colour is None and fill_key is not None and not custom and fill_colour:
            text_colour = _rgba(_readable_on("#{:02X}{:02X}{:02X}".format(*fill_colour[:3])))
        bg = fill_colour or self.p["BUTTON"]
        outline = None
        if variant == "outlined" and not custom:
            outline = self.p["PRIMARY"]
        inset_x, inset_y = (0.0, 0.0) if (custom or flat) else (4.0, 6.0)

        def background(p: _Painter, x: float, y: float, b: _Box) -> None:
            if custom:
                return  # painted from the style (GradientDrawable) by the painter
            if transparent:
                return  # a text button is just its label
            if flat:
                p.rect(x, y, b.w, b.h, None if outline else bg, outline=outline, radius=radius)
                return
            if outline is not None:
                # An outlined button is a hairline, not a raised surface.
                p.rect(
                    x + inset_x,
                    y + inset_y,
                    b.w - 2 * inset_x,
                    b.h - 2 * inset_y,
                    None,
                    outline=outline,
                    radius=radius,
                )
                return
            p.shadow(
                x + inset_x,
                y + inset_y,
                b.w - 2 * inset_x,
                b.h - 2 * inset_y,
                radius=radius,
                elevation=2,
            )
            p.rect(
                x + inset_x, y + inset_y, b.w - 2 * inset_x, b.h - 2 * inset_y, bg, radius=radius
            )

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
        if kind in (
            "Icon",
            "IconButton",
            "AutoComplete",
            "RangeSlider",
            "Chart",
            "Canvas",
        ):
            from .extras_preview import extra_mockup

            drawn: _Box | None = extra_mockup(self, kind, node, w, fill)
            if drawn is not None:
                return drawn
        if kind == "Label":
            return self._text_box(text, 16, p["TEXT"], style, w, fill)
        if kind == "Link":
            return self._text_box(text, 16, p["PRIMARY"], style, w, fill)
        if kind == "Button":
            variant = text_value(props.get("variant")) or "primary"
            return self._button(text, style, w, fill, variant=variant)
        if kind == "Chip":
            style = {"corner_radius": 999, **style}
            if props.get("selected"):
                return self._button(
                    text,
                    style,
                    w,
                    fill,
                    flat=True,
                    fill_colour=_rgba(props.get("selectedColor"), p["PRIMARY"]),
                    text_colour=p["ON_PRIMARY"],
                )
            # P1: a Chip used to be drawn as a plain grey pill whatever the app
            # asked for — its Style(background=…) and Style(color=…) were
            # dropped, so a palette of coloured chips came out uniformly grey.
            own_background = _rgba(style.get("background"), (0, 0, 0, 0))
            own_text = style.get("color")
            return self._button(
                text,
                style,
                w,
                fill,
                flat=True,
                fill_colour=own_background if own_background[3] else self.p["BUTTON"],
                text_colour=_rgba(own_text, p["BUTTON_TEXT"]) if own_text else p["BUTTON_TEXT"],
            )
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
                    pt.rect(x, y, b.w, b.h, bg, radius=b.h / 2)

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
            picture = self._load_image(text_value(props.get("source")))

            def avatar_bg(pt: _Painter, x: float, y: float, b: _Box) -> None:
                if style.get("background") is None:
                    pt.rect(x, y, b.w, b.h, bg, radius=min(b.w, b.h) / 2)
                if picture is not None:
                    from PIL import Image, ImageDraw, ImageOps

                    dimensions = (max(1, round(b.w * pt.s)), max(1, round(b.h * pt.s)))
                    photo = ImageOps.fit(picture, dimensions)
                    mask = Image.new("L", dimensions)
                    ImageDraw.Draw(mask).ellipse(
                        (0, 0, dimensions[0] - 1, dimensions[1] - 1), fill=255
                    )
                    from PIL import ImageChops

                    mask = ImageChops.multiply(mask, photo.getchannel("A"))
                    pt.image.paste(photo, (round(x * pt.s), round(y * pt.s)), mask)

            return self._text_box(
                "" if picture is not None else label,
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
            single_line=not props.get("multiline", False),
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

    @staticmethod
    def _pairs(props: dict[str, Any]) -> list[tuple[str, str]]:
        """П-11: zip option values with their (possibly localised) labels."""
        option_values = [str(o) for o in (props.get("options") or [])]
        option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
        while len(option_labels) < len(option_values):
            option_labels.append(option_values[len(option_labels)])
        return list(zip(option_values, option_labels, strict=False))

    def _dropdown(self, props: dict[str, Any], style: dict[str, Any], w: float, fill: bool) -> _Box:
        p = self.p
        pairs = self._pairs(props)
        value = text_value(props.get("value") or (pairs[0][0] if pairs else ""))
        label = value
        for v, lbl in pairs:
            if v == value:
                label = lbl
                break

        def arrow(pt: _Painter, x: float, y: float, b: _Box) -> None:
            ax, cy = x + b.w - 20, y + b.h / 2 + 2
            pt.polygon([(ax - 5, cy - 3), (ax + 5, cy - 3), (ax, cy + 3)], p["CONTROL"])

        return self._text_box(
            label, 16, p["TEXT"], style, w, fill, pad=(8, 12, 40, 12), min_h=48, background=arrow
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
        pairs = self._pairs(props)
        selected = text_value(props.get("value", ""))
        wrap = bool(props.get("wrap"))
        # A scrolling row measures its segments without a limit; a wrapping one
        # never offers a segment more than the width of a line.
        offer = w if wrap else 100000.0
        segments: list[_Box] = []
        for value, label in pairs:
            if value == selected:
                segments.append(
                    self._button(
                        label,
                        {},
                        offer,
                        False,
                        flat=True,
                        fill_colour=p["PRIMARY"],
                        text_colour=p["ON_PRIMARY"],
                    )
                )
            else:
                segments.append(
                    self._button(
                        label,
                        {},
                        offer,
                        False,
                        flat=True,
                        variant="outlined",
                        text_colour=p["TEXT"],
                    )
                )
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
        pairs = self._pairs(props)
        selected = text_value(props.get("value", ""))
        box = _Box(w, 48)
        if not pairs:
            return box
        tab_w = w / len(pairs)
        for index, (value, label) in enumerate(pairs):
            on = value == selected
            tab = self._button(
                self.fonts.ellipsize(label, 13, False, max(0, tab_w - 12)),
                {"font_size": 13, "padding": [6, 10, 6, 10], "min_width": 0},
                tab_w,
                True,
                flat=True,
                fill_colour=p["PRIMARY"] if on else p["SURFACE"],
                text_colour=p["ON_PRIMARY"] if on else p["TEXT"],
            )
            box.kids.append((index * tab_w, 0.0, tab))
        return box

    def _table(self, props: dict[str, Any], w: float, fill: bool) -> _Box:
        rows: list[tuple[list[Any], bool]] = []
        if props.get("headers"):
            rows.append((list(props["headers"]), True))
        rows += [(list(r or ()), False) for r in props.get("rows", ()) or ()]
        columns = max((len(cells) for cells, _ in rows), default=0)
        if not columns:
            return _Box(w if fill else 0, 0)
        widths = [16.0] * columns
        for cells, bold in rows:
            for index, cell in enumerate(cells):
                widths[index] = max(widths[index], self.fonts.width(str(cell), 14, bold) + 16)
        natural = sum(widths)
        if natural > w:
            widths = [width * w / natural for width in widths]
        elif fill:
            widths = [width + (w - natural) / columns for width in widths]
        box = _Box(sum(widths), 0)
        for cells, bold in rows:
            row = [
                self._text_box(
                    str(cells[index]) if index < len(cells) else "",
                    14,
                    self.p["TEXT"],
                    {},
                    width,
                    True,
                    pad=(8, 4, 8, 4),
                    bold=bold,
                )
                for index, width in enumerate(widths)
            ]
            height = max(cell.h for cell in row)
            x = 0.0
            for cell in row:
                cell.h = height
                box.kids.append((x, box.h, cell))
                x += cell.w
            box.h += height
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
        fit = text_value(props.get("fit", "contain"))
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
            self._paste_image(pt, picture, x, y, b.w, b.h, fit)

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
        self, pt: _Painter, picture: Any, x: float, y: float, w: float, h: float, fit: str
    ) -> None:
        from PIL import Image

        if w <= 0 or h <= 0:
            return
        s = pt.s
        target_w, target_h = max(1, round(w * s)), max(1, round(h * s))
        if fit == "none":
            resized = picture
        elif fit == "fill":
            resized = picture.resize((target_w, target_h))
        else:
            ratio = (max if fit == "cover" else min)(
                target_w / picture.width, target_h / picture.height
            )
            size = (max(1, round(picture.width * ratio)), max(1, round(picture.height * ratio)))
            resized = picture.resize(size)
        # Composite into a bounded tile, including for an oversized fit=none
        # image. Pasting straight into the screen would paint across neighbours.
        tile = Image.new("RGBA", (target_w, target_h))
        offset = ((target_w - resized.width) // 2, (target_h - resized.height) // 2)
        tile.paste(resized, offset)
        destination = (round(x * s), round(y * s))
        if pt.image.mode == "RGBA":
            pt.image.alpha_composite(tile, dest=destination)
        else:
            pt.image.paste(tile, destination, tile)
