"""RatingBar: stars must line up with the filled area on every front end."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import Column, RatingBar
from pymobile.core.ui.preview import render_ascii

JAVA = Path(__file__).resolve().parents[1] / "resources" / "android" / "java"


def test_text_preview_shows_filled_and_empty_stars() -> None:
    text = render_ascii(RatingBar(8, maximum=10))
    assert "★★★★★★★★☆☆" in text
    assert "8/10" in text


@pytest.mark.parametrize("rating", [0, 1, 7, 8, 10])
def test_mockup_fills_exactly_the_rated_stars(rating: int) -> None:
    """Ten stars on a 360 dp screen shrink to fit; star ``rating`` is the last
    coloured one and the next is grey — never a partly painted neighbour."""
    pytest.importorskip("PIL")
    from pymobile.core.ui.mockup import render_mockup

    image = render_mockup(
        Column(RatingBar(rating, maximum=10)), chrome=False, width=360, scale=1.0
    ).convert("RGB")
    cell = 360 / 10
    row = int(cell / 2)
    coloured = []
    for index in range(10):
        r, _g, b = image.getpixel((int(cell * index + cell / 2), row))
        coloured.append(b > r + 40)
    assert coloured == [i < rating for i in range(10)]


def test_android_uses_a_hand_drawn_star_view() -> None:
    """android.widget.RatingBar tiles stars while its clip follows the view's
    width, so a stretched or overflowing bar painted a fraction of the next
    star. The replacement draws every star itself."""
    builder = (JAVA / "ViewBuilder.java").read_text(encoding="utf-8")
    views = (JAVA / "AdvancedViews.java").read_text(encoding="utf-8")
    assert "class StarView" in views
    assert "new RatingBar(" not in builder
    assert "AdvancedViews.StarView" in builder
    # the old patch path removed the listener and never put it back
    assert "setOnRatingBarChangeListener(null)" not in builder


def test_android_star_view_is_wrapped_not_stretched() -> None:
    builder = (JAVA / "ViewBuilder.java").read_text(encoding="utf-8")
    assert '"RatingBar".equals(type) && vertical' in builder
