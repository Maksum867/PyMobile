"""Box model and Pillow drawing primitives; independent of widget layout."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

from ._mockup_colors import _Colour, _rgba
from ._mockup_fonts import _Fonts

_LINE = 1.25

_PaintFn = Callable[["_Painter", float, float, "_Box"], None]


def _pixel_access(image: Any) -> Any:
    """``image.load()`` with a non-optional type (Pillow types it as Optional)."""
    return image.load()


def _sample_stops(stops: list[tuple[float, _Colour]], t: float) -> _Colour:
    """Interpolate a colour at ``t`` (0..1) through a sorted stop list."""
    previous_offset, previous_colour = stops[0]
    if t <= previous_offset:
        return previous_colour
    for offset, colour in stops[1:]:
        if t <= offset:
            span = offset - previous_offset
            amount = 0.0 if span <= 0 else (t - previous_offset) / span
            return (
                round(previous_colour[0] + (colour[0] - previous_colour[0]) * amount),
                round(previous_colour[1] + (colour[1] - previous_colour[1]) * amount),
                round(previous_colour[2] + (colour[2] - previous_colour[2]) * amount),
                round(previous_colour[3] + (colour[3] - previous_colour[3]) * amount),
            )
        previous_offset, previous_colour = offset, colour
    return previous_colour


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
        """The width of the box including its outside margins."""
        return self.w + self.margin[0] + self.margin[2]

    @property
    def outer_h(self) -> float:
        """The height of the box including its outside margins."""
        return self.h + self.margin[1] + self.margin[3]

    def set_outer_h(self, value: float) -> None:
        """Set the outer height by adjusting the height, never going below zero."""
        self.h = max(0.0, value - self.margin[1] - self.margin[3])

    def set_outer_w(self, value: float) -> None:
        """Set the outer width by adjusting the width, never going below zero."""
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
        """Paint a rectangle, optionally rounded and outlined."""
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

    def gradient_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        stops: list[tuple[float, _Colour]],
        *,
        radius: float = 0,
        direction: str = "to_bottom",
    ) -> None:
        """Fill a rectangle with a linear ramp, interpolated in sRGB.

        No numpy here on purpose: the renderer is pure Pillow. The ramp is
        painted row by row (or column by column, or pixel by pixel on a
        diagonal) and then clipped to the rounded shape, so a corner radius and
        a gradient do not fight each other.
        """
        if w <= 0 or h <= 0 or len(stops) < 2:
            return
        from .style import GRADIENT_DIRECTIONS

        s = self.s
        width_px, height_px = max(1, round(w * s)), max(1, round(h * s))
        start_x, start_y, end_x, end_y = GRADIENT_DIRECTIONS.get(
            direction, GRADIENT_DIRECTIONS["to_bottom"]
        )
        vector_x = (end_x - start_x) * width_px
        vector_y = (end_y - start_y) * height_px
        length_sq = vector_x * vector_x + vector_y * vector_y
        if length_sq <= 0:
            return
        origin_x, origin_y = start_x * width_px, start_y * height_px
        ramp = Image.new("RGBA", (width_px, height_px))
        pixels = _pixel_access(ramp)

        def colour_at(t: float) -> _Colour:
            return _sample_stops(stops, min(1.0, max(0.0, t)))

        if vector_x == 0:  # vertical ramp: one colour per row
            for py in range(height_px):
                t = (py - origin_y) * vector_y / length_sq
                row_colour = colour_at(t)
                for px in range(width_px):
                    pixels[px, py] = row_colour
        elif vector_y == 0:  # horizontal ramp: one colour per column
            for px in range(width_px):
                t = (px - origin_x) * vector_x / length_sq
                column_colour = colour_at(t)
                for py in range(height_px):
                    pixels[px, py] = column_colour
        else:  # diagonal ramp: project each pixel onto the axis
            for py in range(height_px):
                base = (py - origin_y) * vector_y
                for px in range(width_px):
                    pixels[px, py] = colour_at((base + (px - origin_x) * vector_x) / length_sq)

        if radius > 0:
            shape = Image.new("L", (width_px, height_px), 0)
            ImageDraw.Draw(shape).rounded_rectangle(
                (0, 0, width_px - 1, height_px - 1),
                radius=min(round(radius * s), width_px / 2, height_px / 2),
                fill=255,
            )
            self.image.paste(ramp, (round(x * s), round(y * s)), shape)
        else:
            self.image.paste(ramp, (round(x * s), round(y * s)))

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
        """Paint a circle, optionally outlined."""
        s = self.s
        self.draw.ellipse(
            [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
            fill=self._c(fill),
            outline=self._c(outline),
            width=max(1, round(width * s)),
        )

    def line(self, points: list[tuple[float, float]], fill: _Colour, width: float = 1) -> None:
        """Paint a polyline through ``points``."""
        s = self.s
        self.draw.line(
            [(px * s, py * s) for px, py in points],
            fill=self._c(fill),
            width=max(1, round(width * s)),
            joint="curve",
        )

    def polygon(self, points: list[tuple[float, float]], fill: _Colour) -> None:
        """Paint a filled polygon through ``points``."""
        s = self.s
        self.draw.polygon([(px * s, py * s) for px, py in points], fill=self._c(fill))

    def arc(
        self, cx: float, cy: float, r: float, start: float, end: float, fill: _Colour, width: float
    ) -> None:
        """Paint an arc of the circle at ``cx, cy`` between two angles."""
        s = self.s
        self.draw.arc(
            [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
            start,
            end,
            fill=self._c(fill),
            width=max(1, round(width * s)),
        )

    def text(
        self,
        x: float,
        y: float,
        text: str,
        size: float,
        colour: _Colour,
        *,
        bold: bool = False,
        font_path: str | None = None,
        letter_spacing: float = 0.0,
        line_height: float = 0.0,
        underline: bool = False,
        strikethrough: bool = False,
    ) -> None:
        """Draw one line; ``y`` is the top of a ``size * _LINE`` high line box.

        ``font_path`` overrides the discovered sans face when a Style names a
        family; ``letter_spacing`` is in dp, like the Style field, and
        ``line_height`` shifts the baseline inside the taller line box the
        caller laid out.
        """
        s = self.s
        px = x * s
        spacing = letter_spacing * s
        line_box = (size * _LINE + line_height) * s
        for font, chunk in self.fonts.runs(text, size, bold, path=font_path):
            try:
                ascent, descent = font.getmetrics()
            except AttributeError:  # pragma: no cover - bitmap font
                ascent, descent = round(size * s * 0.8), round(size * s * 0.2)
            top = y * s + (line_box - (ascent + descent)) / 2
            if spacing:
                # Pillow cannot letter-space a string, so the chunks are drawn
                # glyph by glyph with the extra space between them.
                for character in chunk:
                    self.draw.text((px, top), character, font=font, fill=self._c(colour))
                    try:
                        px += float(font.getlength(character)) + spacing
                    except AttributeError:  # pragma: no cover
                        px += size * s * 0.6 + spacing
            else:
                self.draw.text((px, top), chunk, font=font, fill=self._c(colour))
                try:
                    px += float(font.getlength(chunk))
                except AttributeError:  # pragma: no cover
                    px += len(chunk) * size * s * 0.6
            if underline or strikethrough:
                end = px - spacing
                if underline:
                    self.rect(
                        x + 0,
                        y + (line_box - descent) / s - 1,
                        max(1.0, (end - x * s) / s),
                        1,
                        colour,
                    )
                if strikethrough:
                    self.rect(
                        x + 0,
                        y + (line_box / 2) / s,
                        max(1.0, (end - x * s) / s),
                        1,
                        colour,
                    )

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
        font_path: str | None = None,
        letter_spacing: float = 0.0,
        line_height: float = 0.0,
        underline: bool = False,
        strikethrough: bool = False,
    ) -> None:
        """Paint several lines of text, with the spacing and decorations asked for."""
        stride = size * _LINE + line_height
        for index, line in enumerate(lines):
            dx = 0.0
            if align in ("center", "end", "right"):
                spare = w - (
                    self.fonts.width(line, size, bold, path=font_path)
                    + max(0, len(line) - 1) * letter_spacing
                )
                dx = spare / 2 if align == "center" else spare
            self.text(
                x + max(0.0, dx),
                y + index * stride,
                line,
                size,
                colour,
                bold=bold,
                font_path=font_path,
                letter_spacing=letter_spacing,
                line_height=line_height,
                underline=underline,
                strikethrough=strikethrough,
            )

    # -- tree --------------------------------------------------------------
    def paint(self, box: _Box, x: float, y: float) -> None:
        """Paint one laid-out box: background, border, then its own content."""
        bx, by = x + box.margin[0], y + box.margin[1]
        saved = self.fade
        self.fade *= box.fade
        style = box.style
        radius = float(style.get("corner_radius") or 0)
        elevation = float(style.get("elevation") or 0)
        if elevation > 0:
            self.shadow(bx, by, box.w, box.h, radius=radius, elevation=elevation)
        background = style.get("background")
        gradient = style.get("gradient")
        if isinstance(gradient, dict) and gradient.get("stops"):
            # A gradient is the more specific instruction; a flat background
            # next to it is what the app falls back on where gradients cannot
            # be drawn, so the ramp is what the picture shows.
            self.gradient_rect(
                bx,
                by,
                box.w,
                box.h,
                [(float(offset), _rgba(colour)) for offset, colour in gradient["stops"]],
                radius=radius,
                direction=str(gradient.get("direction", "to_bottom")),
            )
        elif background is not None or radius or elevation:
            fill = _rgba(background, (0, 0, 0, 0)) if background is not None else None
            if fill is None and elevation:
                fill = style.get("_surface")
            if fill is not None:
                self.rect(bx, by, box.w, box.h, fill, radius=radius)
        border = style.get("border")
        if isinstance(border, dict) and border.get("sides"):
            colour = _rgba(border.get("color", "#E0E0E0"))
            thickness = float(border.get("width", 1))
            sides = set(border["sides"])
            if len(sides) == 4:
                self.rect(
                    bx, by, box.w, box.h, None, outline=colour, radius=radius, width=thickness
                )
            else:
                # One hairline per named side, drawn inside the box the way
                # Android's setStroke does it.
                if "top" in sides:
                    self.rect(bx, by, box.w, thickness, colour)
                if "bottom" in sides:
                    self.rect(bx, by + box.h - thickness, box.w, thickness, colour)
                if "left" in sides:
                    self.rect(bx, by, thickness, box.h, colour)
                if "right" in sides:
                    self.rect(bx + box.w - thickness, by, thickness, box.h, colour)
        if style.get("_tappable"):
            # A hairline in the accent colour, drawn under the children: it
            # shows that the whole surface takes a tap without inventing a
            # background the app did not ask for. The palette lives on the
            # layout, not on the painter.
            self.rect(
                bx,
                by,
                box.w,
                box.h,
                None,
                outline=_rgba(style.get("_tappable_colour"), (0, 0, 0, 80)),
                radius=radius,
                width=1,
            )
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
