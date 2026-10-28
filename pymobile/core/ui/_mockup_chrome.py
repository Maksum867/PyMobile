"""Window chrome, dialogs, snackbars and loading adornments."""

from __future__ import annotations

from typing import Any

from ._mockup_colors import _mix
from ._mockup_context import _MockupContext
from ._mockup_paint import _LINE, _Box, _Painter
from .contract import text_value

_STATUS_BAR = 24
_APP_BAR = 56


class _ChromeLayout(_MockupContext):
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
