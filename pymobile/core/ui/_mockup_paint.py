"""Box model and Pillow drawing primitives; independent of widget layout."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ._mockup_colors import _Colour, _rgba
from ._mockup_fonts import _Fonts

_LINE = 1.25

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
