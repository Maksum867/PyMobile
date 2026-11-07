"""Regression tests for the first audit block; kept outside the source repository."""

from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from PIL import Image, ImageDraw, ImageFont

import pymobile
from pymobile import Button, Canvas, Chip, Label, Polygon, Rect, Stack, Style
from pymobile.core.ui._mockup_colors import _palette
from pymobile.core.ui._mockup_fonts import _Fonts
from pymobile.core.ui._mockup_layout import _Layout
from pymobile.core.ui._mockup_paint import _Painter
from pymobile.core.ui.drawing import drawing_svg
from pymobile.core.ui.extras_preview import paint_tk
from pymobile.core.ui.web import render_html

JAVA = Path(pymobile.__file__).parent / "resources/android/java"


@pytest.fixture
def layout():
    return _Layout(_Fonts(ImageFont, 1), _palette(None), None, 1)


def paint(layout, widget, width=120):
    box = layout.layout(widget.to_dict(), width, fill=True)
    image = Image.new("RGBA", (width, round(box.h)), "white")
    painter = _Painter(ImageDraw, image, 1, layout.fonts)
    painter.paint(box, 0, 0)
    return image, box


@pytest.mark.parametrize("variant", ["primary", "outlined", "tonal", "danger"])
@pytest.mark.parametrize("radius", [0, 20])
def test_radius_retains_button_fill_or_outline(layout, variant, radius):
    image, _ = paint(layout, Button("", variant=variant, style=Style(corner_radius=radius)))
    if variant == "outlined":
        assert image.getpixel((60, 6)) == layout.p["PRIMARY"]
        assert image.getpixel((60, 24)) == (255, 255, 255, 255)
    else:
        assert image.getpixel((60, 24)) != (255, 255, 255, 255)
    if radius:
        assert image.getpixel((4, 6)) == (255, 255, 255, 255)


def test_explicit_button_background_wins(layout):
    image, _ = paint(layout, Button("", style=Style(background="#12AB34", corner_radius=20)))
    assert image.getpixel((60, 24)) == (18, 171, 52, 255)


@pytest.mark.parametrize("selected", [False, True])
def test_chip_pill_and_contrast(layout, selected):
    box = layout.layout(Chip("Chip", selected=selected).to_dict(), 120, fill=True)
    painter = Mock()
    box.paint(painter, 0, 0, box)
    assert painter.rect.call_args.kwargs["radius"] == 999
    colour = painter.lines.call_args.args[5]
    assert colour == (layout.p["ON_PRIMARY"] if selected else layout.p["BUTTON_TEXT"])


def test_segments_share_height_but_not_fill(layout):
    box = layout._segmented({"options": ["a", "b"], "value": "a"}, 240, True)
    active, inactive = [entry[2] for entry in box.kids]
    assert active.h == inactive.h == 48
    painter = Mock()
    inactive.paint(painter, 0, 0, inactive)
    assert painter.rect.call_args.args[4] is None
    assert painter.rect.call_args.kwargs["outline"] == layout.p["PRIMARY"]
    painter.shadow.assert_not_called()


@pytest.mark.parametrize("kind", ["SearchBar", "TextInput"])
@pytest.mark.parametrize("field", ["value", "placeholder"])
def test_single_line_clips_before_paint_even_after_shrink(layout, kind, field):
    box = layout._text_input(kind, {field: "Long text\n" * 30}, {}, 150, True)
    box.w = 60
    painter = Mock()
    box.paint(painter, 0, 0, box)
    _, _, width, lines, size, _ = painter.lines.call_args.args
    assert len(lines) == 1
    assert layout.fonts.width(lines[0], size) <= width
    assert lines[0].endswith("…")


def test_multiline_still_wraps(layout):
    box = layout._text_input(
        "TextInput", {"value": "long text " * 20, "multiline": True}, {}, 100, True
    )
    painter = Mock()
    box.paint(painter, 0, 0, box)
    assert len(painter.lines.call_args.args[3]) > 1


@pytest.mark.parametrize("width", [0, 1, 10, 40, 200])
def test_ellipsis_fits_tiny_width(layout, width):
    text = layout.fonts.ellipsize("long input " * 20, 18, False, width)
    assert layout.fonts.width(text, 18) <= width


def shape(kind, filled):
    kwargs = {"color": "#FF0000", "fill": filled, "width": 3}
    if kind == "polygon":
        return Polygon((10, 10), (90, 10), (50, 90), **kwargs)
    return Rect(10, 10, 80, 80, **kwargs)


@pytest.mark.parametrize("kind", ["polygon", "rect"])
@pytest.mark.parametrize("filled", [False, True])
def test_vector_contract_svg_png_and_tk(layout, kind, filled):
    canvas = Canvas(shape(kind, filled), width=100, height=100)
    drawing = canvas.drawing()
    primitive = drawing["shapes"][0]
    assert primitive["type"] == kind
    assert primitive["fill"] is filled
    assert primitive["width"] == 3
    svg = ET.fromstring(drawing_svg(drawing))
    node = next(n for n in svg if n.tag.endswith(kind))
    assert node.attrib["fill"] == ("#FF0000" if filled else "none")
    assert node.attrib["stroke"] == ("none" if filled else "#FF0000")
    image, _ = paint(layout, canvas, 100)
    assert image.getpixel((50, 40)) == ((255, 0, 0, 255) if filled else (255, 255, 255, 255))
    assert image.getpixel((50, 10)) == (255, 0, 0, 255)
    tk = Mock()
    paint_tk(tk, drawing, 200, 200)
    call = (tk.create_polygon if kind == "polygon" else tk.create_rectangle).call_args
    assert call.kwargs["fill"] == ("#FF0000" if filled else "")
    assert call.kwargs["outline"] == ("" if filled else "#FF0000")
    assert call.kwargs["width"] == 6


def test_stack_layers_and_preserves_tap():
    html = render_html(Stack(Label("base"), Button("overlay"), on_press=lambda: None).to_dict())
    assert html.startswith('<div class="stack"')
    assert 'role="button"' in html
    assert "base" in html and "overlay" in html
    source = Path(pymobile.__file__).parent / "core/ui/web.py"
    assert ".stack > * {{ grid-area: 1 / 1;" in source.read_text()


def test_android_radius_mutates_instead_of_erasing():
    source = (JAVA / "ViewStyles.java").read_text()
    assert "view.getBackground().mutate()" in source
    assert 'if (style.has("background"))' in source
    assert "&& view.getBackground() == null" in source


def test_android_labels_are_separate_from_keys():
    controls = (JAVA / "ViewControls.java").read_text()
    inputs = (JAVA / "ViewInputs.java").read_text()
    updates = (JAVA / "ViewUpdates.java").read_text()
    assert 'props.optJSONArray("labels")' in controls
    assert "tab.setText(optionLabel(props, i))" in controls
    assert "segment.setText(optionLabel(props, i))" in controls
    assert controls.count('Native.dispatchEvent(id, "change", String.valueOf(v.getTag()))') == 2
    assert "add(ViewControls.optionLabel(props, i))" in inputs
    assert "String[] entries = adapter.values" in inputs
    assert "ViewControls.optionLabel(props, i)" in updates
    assert "((ViewInputs.OptionAdapter) adapter).values[i]" in updates


def test_android_patch_dispatch_and_shared_styles():
    source = (JAVA / "ViewUpdates.java").read_text()
    controls = (JAVA / "ViewControls.java").read_text()
    assert source.index("if (view instanceof RadioGroup)") < source.index(
        "if (view instanceof ViewGroup)"
    )
    for method in ("applyButton", "applyChip", "applyBadge", "applyAvatar", "applySegment"):
        assert f"owner.controls.{method}(" in source
        assert controls.count(f"{method}(") >= 2
    assert source.index('"Chip".equals(type)') < source.index("if (view instanceof Button)")
    assert "child.setTag(key)" in source


def test_android_vector_closed_polygon_and_outline():
    source = (JAVA / "AdvancedViews.java").read_text()
    assert "if (polygon) path.close()" in source
    assert 'polygon && s.optBoolean("fill", true)' in source
    rect = source.split('else if ("rect".equals(type))')[1].split("else if")[0]
    assert "Paint.Style.FILL : Paint.Style.STROKE" in rect
