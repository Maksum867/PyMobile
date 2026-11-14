"""The style vocabulary: borders, gradients, opacity and typography.

Every field is checked twice - once on the object itself and once in the
renderers that have to show it (CSS, the PNG mockup and the HTML preview).
"""

from __future__ import annotations

import tempfile
from typing import Any

import pytest

from pymobile import App, Border, Column, Gradient, Label, Screen, Style, Widget
from pymobile.core.ui.mockup import render_mockup
from pymobile.core.ui.style import GRADIENT_DIRECTIONS
from pymobile.core.ui.web import _style_css, render_html
from pymobile.testing import StubBridge


def _render(app: App) -> dict[str, Any]:
    tree = app.render()
    assert tree is not None
    return tree


class TestStyleSurface:
    def test_the_audit_counted_eighteen_fields_and_there_are_more_now(self) -> None:
        fields = set(Style.__dataclass_fields__)
        assert len(fields) >= 29
        for name in (
            "opacity",
            "border",
            "gradient",
            "line_height",
            "letter_spacing",
            "font_family",
            "text_transform",
            "text_overflow",
            "max_lines",
            "underline",
            "strikethrough",
        ):
            assert name in fields

    def test_a_border_serialises_with_its_sides(self) -> None:
        style = Style(border=Border(width=2, color="#C62828", sides="bottom"))
        assert style.to_dict()["border"] == {
            "width": 2,
            "color": "#C62828",
            "sides": ["bottom"],
        }

    def test_a_zero_width_border_is_dropped(self) -> None:
        assert "border" not in Style(border=Border(width=0, color="#000000")).to_dict()

    def test_an_unknown_border_side_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown border side"):
            Border(color="#000000", sides="top diagonal").side_set

    def test_a_gradient_holds_two_or_more_stops(self) -> None:
        gradient = Gradient("#FFFFFF", "#3F51B5", direction="to_bottom_right")
        serialised = gradient.to_dict()
        assert serialised["direction"] == "to_bottom_right"
        assert serialised["stops"] == [[0.0, "#FFFFFF"], [1.0, "#3F51B5"]]

    def test_a_three_colour_gradient_keeps_its_middle(self) -> None:
        ramp = Gradient("#FFFFFF", "#3F51B5", "#E8EAF6").ramp()
        assert [offset for offset, _ in ramp] == [0.0, 0.5, 1.0]

    def test_gradient_stops_must_cover_the_whole_ramp(self) -> None:
        with pytest.raises(ValueError, match="must start at 0 and end at 1"):
            Gradient(stops=((0.2, "#FFFFFF"), (1.0, "#000000")))

    def test_every_gradient_direction_is_named(self) -> None:
        for direction in GRADIENT_DIRECTIONS:
            assert Gradient("#FFFFFF", "#000000", direction=direction).to_dict()

    def test_an_unknown_gradient_direction_is_refused(self) -> None:
        with pytest.raises(ValueError, match="invalid gradient direction"):
            Gradient("#FFFFFF", "#000000", direction="diagonally")

    def test_opacity_is_validated(self) -> None:
        with pytest.raises(ValueError, match="opacity must be from 0"):
            Style(opacity=1.5)
        with pytest.raises(TypeError, match="opacity must be a number"):
            Style(opacity="half")  # type: ignore[arg-type]

    def test_font_family_accepts_a_list_and_refuses_punctuation(self) -> None:
        assert Style(font_family="Georgia, serif").to_dict()["font_family"] == "Georgia, serif"
        with pytest.raises(ValueError, match="invalid font_family"):
            Style(font_family="bad; font }")

    def test_text_transform_is_normalised(self) -> None:
        assert Style(text_transform="Uppercase").to_dict()["text_transform"] == "uppercase"
        with pytest.raises(ValueError, match="invalid text_transform"):
            Style(text_transform="shout")

    def test_max_lines_implies_an_ellipsis(self) -> None:
        serialised = Style(max_lines=2).to_dict()
        assert serialised["max_lines"] == 2
        assert serialised["text_overflow"] == "ellipsis"

    def test_merge_and_derive_keep_the_rest_of_the_style(self) -> None:
        base = Style(background="#FFFFFF", padding=8, corner_radius=4)
        derived = base.derive(background="#000000")
        assert derived.background == "#000000"
        assert derived.padding == base.padding
        assert derived.corner_radius == 4


class TestStyleReachesThePreviews:
    def test_css_carries_the_new_fields(self) -> None:
        css = _style_css(
            Style(
                border=Border(2, "#C62828", sides="bottom"),
                opacity=0.5,
                line_height=20,
                letter_spacing=0.5,
                font_family="Georgia, serif",
                text_transform="uppercase",
                underline=True,
            ).to_dict()
        )
        for fragment in (
            "opacity:0.5",
            "border-bottom:2px solid #C62828",
            "line-height:20px",
            "letter-spacing:0.5px",
            "font-family:Georgia,serif",
            "text-transform:uppercase",
            "text-decoration-line:underline",
        ):
            assert fragment in css

    def test_a_gradient_becomes_a_linear_gradient(self) -> None:
        css = _style_css(
            Style(gradient=Gradient("#FFFFFF", "#000000", direction="to_right")).to_dict()
        )
        assert "linear-gradient(90deg" in css
        assert "#FFFFFF 0%" in css and "#000000 100%" in css

    def test_the_web_preview_renders_a_bordered_label(self) -> None:
        html = render_html(Label("x", style=Style(border=Border(1, "#333333"))).to_dict())
        assert "border:1px solid #333333" in html

    def test_the_png_mockup_paints_something_for_every_new_field(self) -> None:
        from PIL import Image

        class Styled(Screen):
            def build(self) -> Widget:
                return Column(
                    Label(
                        "gradient",
                        style=Style(
                            gradient=Gradient("#3F51B5", "#1A237E", direction="to_right"),
                            color="#FFFFFF",
                            padding=8,
                        ),
                    ),
                    Label("border", style=Style(border=Border(2, "#C62828"))),
                    Label("ghost", style=Style(background="#000000", opacity=0.5, padding=8)),
                )

        screen = Styled()
        app = App(screen, bridge=StubBridge(verbose=False))
        app.run(screen)
        try:
            with tempfile.TemporaryDirectory() as directory:
                tree = _render(app)
                # The renderer strips the generation prefix from ids; the tree
                # is valid input either way.
                path = render_mockup(
                    {key: value for key, value in tree.items() if key != "generation"},
                    f"{directory}/style.png",
                    width=320,
                )
                image = Image.open(path).convert("RGB")
                colours = {colour for _, colour in image.getcolors(maxcolors=1 << 20) or []}
                # A gradient is a ramp: many distinct blues, not one flat fill.
                blues = [
                    colour for colour in colours if colour[2] > colour[0] + 40 and colour[2] > 60
                ]
                assert len(blues) > 10
        finally:
            app.stop()
