"""The PNG preview canvas measurements and the glyph fallback chain."""

from __future__ import annotations

import os

import pytest

from pymobile import Label, Validator
from pymobile.core.ui import preview as preview_mod

pytest.importorskip("PIL")
from PIL import Image, ImageFont

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
needs_mono = pytest.mark.skipif(not os.path.exists(MONO), reason="DejaVuSansMono not installed")


class _LogSpy:
    """Minimal logger stand-in: the renderer only ever warns."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def __getattr__(self, name: str) -> object:
        return lambda *a, **k: None


@pytest.fixture(autouse=True)
def _no_font_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYMOBILE_PREVIEW_FONT", raising=False)


@needs_mono
def test_png_canvas_uses_real_glyph_advances(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_mod, "_FONT_CANDIDATES", (MONO,))
    monkeypatch.setattr(preview_mod, "_SYMBOL_CANDIDATES", ())
    line = "Зарплата — вересень — 19.09 15:15  +12 000.00 ₴"
    out = str(tmp_path / "ui.png")  # type: ignore[arg-type]
    preview_mod.render_png(Label(line), out)

    width, _ = Image.open(out).size
    font = ImageFont.truetype(MONO, 14)  # _preview_font loads the face at scale + 2
    assert width == int(font.getlength(line)) + 24
    # the pre-fix formula allotted 7 px per character and clipped this line
    assert width > len(line) * (12 // 2 + 1) + 24


@needs_mono
def test_png_warns_with_hint_when_glyphs_uncovered(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_mod, "_FONT_CANDIDATES", (MONO,))
    monkeypatch.setattr(preview_mod, "_SYMBOL_CANDIDATES", ())
    spy = _LogSpy()
    monkeypatch.setattr(preview_mod, "_log", spy)

    preview_mod.render_png(Label("pizza \U0001f354"), str(tmp_path / "ui.png"))  # type: ignore[arg-type]

    assert len(spy.messages) == 1
    assert "no preview font covers" in spy.messages[0]
    assert "PYMOBILE_PREVIEW_FONT" in spy.messages[0]


@needs_mono
def test_png_fallback_face_patches_holes_silently(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no candidates the ASCII-only bundled default face becomes primary
    # and lacks Cyrillic and box drawing; MONO as the symbol fallback patches
    # every hole, so nothing may be logged.
    monkeypatch.setattr(preview_mod, "_FONT_CANDIDATES", ())
    monkeypatch.setattr(preview_mod, "_SYMBOL_CANDIDATES", (MONO,))
    spy = _LogSpy()
    monkeypatch.setattr(preview_mod, "_log", spy)

    out = str(tmp_path / "ui.png")  # type: ignore[arg-type]
    preview_mod.render_png(Label("Привіт ┌───"), out)

    assert spy.messages == []
    assert Image.open(out).size[0] > 24

    # the same tree with no fallback available must warn exactly once
    monkeypatch.setattr(preview_mod, "_SYMBOL_CANDIDATES", ())
    preview_mod.render_png(Label("Привіт ┌───┐"), str(tmp_path / "ui2.png"))  # type: ignore[arg-type]
    assert len(spy.messages) == 1


def test_bare_argument_rule_names_the_fix() -> None:
    with pytest.raises(ValueError, match="requires an argument"):
        Validator({"name": ["min_length"]})
    with pytest.raises(ValueError, match="one-key mapping"):
        Validator({"age": ["between"]})
    with pytest.raises(ValueError) as exc:
        Validator({"x": ["max"]})
    assert "bare strings only work for" in str(exc.value)


def test_truly_unknown_rule_message_unchanged() -> None:
    with pytest.raises(ValueError, match="unknown validation rule"):
        Validator({"x": ["telepathy"]})
