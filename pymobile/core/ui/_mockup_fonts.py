"""Font discovery, glyph fallback and text measurement for mockups."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...log import get_logger
from .preview import _SYMBOL_CANDIDATES, _has_glyph

_log = get_logger("ui.mockup")
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
