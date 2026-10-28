"""Colour parsing and Android-compatible theme palettes for mockup rendering."""

from __future__ import annotations

from typing import Any

_Colour = tuple[int, int, int, int]


def _blend(base: _Colour, over: _Colour, amount: float) -> _Colour:
    """Mix ``over`` into ``base``; ``amount`` is how much of ``over`` survives."""
    keep = max(0.0, min(1.0, amount))
    return (
        round(base[0] * (1 - keep) + over[0] * keep),
        round(base[1] * (1 - keep) + over[1] * keep),
        round(base[2] * (1 - keep) + over[2] * keep),
        255,
    )


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
        # Semantic accents, so a danger button or a success badge is the same
        # colour as on the phone (P1: the palette only knew PRIMARY).
        "ACCENT": _rgba(colours.get("ACCENT"), (0xFF, 0x40, 0x81, 255)),
        "SUCCESS": _rgba(colours.get("SUCCESS"), (0x2E, 0x7D, 0x32, 255)),
        "WARNING": _rgba(colours.get("WARNING"), (0xF9, 0xA8, 0x25, 255)),
        "ERROR": _rgba(colours.get("ERROR"), (0xC6, 0x28, 0x28, 255)),
        # Material widget colours of Theme.Material(.Light).
        "BUTTON": (0x5A, 0x59, 0x5B, 255) if dark else (0xD6, 0xD7, 0xD7, 255),
        "BUTTON_TEXT": white if dark else (0, 0, 0, 0xDE),
        "CONTROL": (255, 255, 255, 0xB3) if dark else (0, 0, 0, 0x8A),
        "TRACK_OFF": (255, 255, 255, 0x4D) if dark else (0, 0, 0, 0x61),
        "THUMB_OFF": (0xBD, 0xBD, 0xBD, 255) if dark else (0xFA, 0xFA, 0xFA, 255),
        "DIVIDER": (0, 0, 0, 0x1F),
    }
    return palette
