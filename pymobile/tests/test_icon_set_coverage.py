"""Every icon in the set draws its shapes and has a text-preview glyph."""

from __future__ import annotations

from pymobile.core.ui import drawing
from pymobile.core.ui.extras_preview import _ICON_GLYPHS


class TestIcons:
    def test_the_set_more_than_tripled(self) -> None:
        assert len(drawing.ICON_NAMES) > 60

    def test_every_icon_has_shapes(self) -> None:
        empty = [name for name in drawing.ICON_NAMES if not drawing.icon_drawing(name)["shapes"]]
        assert empty == []

    def test_every_icon_has_a_text_preview_glyph(self) -> None:
        missing = [name for name in drawing.ICON_NAMES if name not in _ICON_GLYPHS]
        assert missing == []
