"""Audit block 3: chart geometry, patterns and authenticated local-image preview."""

import base64

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from pymobile import (
    App,
    Avatar,
    Badge,
    BarChart,
    BottomNavigation,
    Button,
    Chip,
    ColorPicker,
    Column,
    Dialog,
    Divider,
    Label,
    LineChart,
    Pagination,
    PieChart,
    SegmentedButtons,
    StubBridge,
    Theme,
)
from pymobile.core.ui._mockup_colors import _palette
from pymobile.core.ui._mockup_fonts import _Fonts
from pymobile.core.ui._mockup_layout import _Layout
from pymobile.core.ui._mockup_paint import _Painter
from pymobile.core.ui.web import WebPreview, render_html


@pytest.fixture
def layout():
    return _Layout(_Fonts(ImageFont, 1), _palette(None), None, 1)


@pytest.mark.parametrize("chart_type", [BarChart, LineChart])
@pytest.mark.parametrize("count", [5, 6, 7, 8, 40])
def test_chart_axis_labels_fit_and_do_not_overlap(chart_type, count, layout):
    names = ["WWWWWWW" + str(i) for i in range(count)]
    chart = chart_type(list(range(1, count + 1)), labels=names)
    drawing = chart.props()["drawing"]
    labels = [s for s in drawing["shapes"] if s["type"] == "text" and s["y"] == chart.height - 22]
    assert 0 < len(labels) < count
    previous_right = 0
    for s in labels:
        width = layout.fonts.width(s["text"], s["size"])
        left, right = s["x"] - width / 2, s["x"] + width / 2
        assert 0 <= left < right <= 320
        assert left >= previous_right + 2
        previous_right = right
    assert chart.props()["labels"] == names  # accessible/raw data never truncated


@pytest.mark.parametrize("data", [[-120, 340, -45, 510], [-3, -100], [0, 1, 999], [0, 0]])
def test_bar_value_annotations_stay_outside_bars_and_bounds(data):
    shapes = BarChart(data, show_values=True).props()["drawing"]["shapes"]
    bars = [s for s in shapes if s["type"] == "rect"]
    annotations = [s for s in shapes if s["type"] == "text" and s["x"] >= 42 and s["y"] < 190]
    assert len(annotations) == len(data)
    for value, bar, annotation in zip(data, bars, annotations, strict=True):
        assert 0 <= annotation["y"] <= 190 - annotation["size"]
        if value < 0:
            assert annotation["y"] > bar["y"] + bar["h"]
        else:
            assert annotation["y"] + annotation["size"] < bar["y"]


def test_short_chart_omits_values_instead_of_overlapping_title():
    chart = BarChart([-10, 20], title="Small", height=80)
    shapes = chart.props()["drawing"]["shapes"]
    annotations = [s for s in shapes if s["type"] == "text" and s["x"] >= 42 and s["y"] < 50]
    assert not annotations


@pytest.mark.parametrize("chart_type", [BarChart, LineChart, PieChart])
def test_long_titles_and_pie_legend_have_visible_truncation(chart_type, layout):
    title = "Very long WWW title " * 20
    chart = chart_type({f"category {i}": i + 1 for i in range(20)}, title=title)
    props = chart.props()
    shapes = props["drawing"]["shapes"]
    heading = shapes[0]
    assert heading["text"].endswith("…")
    assert heading["x"] + layout.fonts.width(heading["text"], 15) <= 308
    assert props["title"] == title and "category 19" in props["label"]
    if chart_type is PieChart:
        more = [s for s in shapes if s["type"] == "text" and s["text"].endswith(" more")]
        assert len(more) == 1 and more[0]["y"] + 11 <= 220
        sectors = [s for s in shapes if s["type"] == "sector"]
        assert len(sectors) == 20 and sum(s["sweep"] for s in sectors) == pytest.approx(360)
        visible = len([s for s in shapes if s["type"] == "rect"])
        assert more[0]["text"] == f"+{20 - visible} more"


def test_color_picker_ring_visible_in_png_and_button_retains_hit_target(layout):
    picker = ColorPicker(["#FFFFFF", "#121212"])
    ring = picker.children[0].children[0]
    button = ring.children[0]
    assert button.style.width == button.style.height == 48
    assert ring.style.width == ring.style.height == 50
    box = layout.layout(ring.to_dict(), 50, fill=True)
    image = PILImage.new("RGBA", (50, 50), "white")
    _Painter(ImageDraw, image, 1, layout.fonts).paint(box, 0, 0)
    assert image.getpixel((25, 0)) != (255, 255, 255, 255)
    assert image.getpixel((25, 3)) == (255, 255, 255, 255)
    button.on_press()
    assert picker.value == "#FFFFFF"


def test_pagination_is_compact_and_gaps_centered(layout):
    pager = Pagination(total_pages=30, current=15)
    buttons = [n for n in pager.walk() if isinstance(n, Button)]
    gaps = [n for n in pager.walk() if isinstance(n, Label)]
    assert buttons and gaps
    for button in buttons:
        box = layout.layout(button.to_dict(), 360, fill=False)
        assert 40 <= box.w < 88 and box.h >= 48
    for gap in gaps:
        box = layout.layout(gap.to_dict(), 360, fill=False)
        assert box.h == 48
        assert gap.style.padding.top == gap.style.padding.bottom == 14


@pytest.mark.parametrize("variant", ["primary", "tonal", "outlined", "text"])
def test_web_button_variants_use_theme_variables(variant):
    html = render_html(Button("Go", variant=variant).to_dict())
    assert "var(--primary)" in html and "#3F51B5" not in html


@pytest.mark.parametrize(
    "widget",
    [
        Chip("Choice", selected=True),
        SegmentedButtons(["a", "b"], value="a"),
        BottomNavigation(["a", "b"], value="a"),
    ],
)
def test_web_selection_changes_fill_and_text(widget):
    html = render_html(widget.to_dict())
    assert "background:var(--primary)" in html
    assert "color:var(--on-primary)" in html


def test_badge_avatar_default_background_follows_app_theme():
    app = App(
        "palette", bridge=StubBridge(verbose=False), theme=Theme("mint", {"PRIMARY": "#007755"})
    )
    badge, avatar, explicit = Badge("1"), Avatar("AB"), Badge("2", background="#112233")
    try:
        app.run(Column(badge, avatar, explicit))
        assert badge.props()["background"] == avatar.props()["background"] == "#007755"
        assert explicit.props()["background"] == "#112233"
        app.set_theme(Theme("custom", {"PRIMARY": "#AA0055"}))
        assert badge.props()["background"] == avatar.props()["background"] == "#AA0055"
    finally:
        app.stop()


def test_web_on_primary_can_be_overridden_and_short_colours_work():
    app = App("palette", bridge=StubBridge(verbose=False), theme=Theme("dark", {"PRIMARY": "#fff"}))
    try:
        assert WebPreview(app)._theme_vars()["on_primary"] == "#000000"
        app.set_theme(Theme("custom", {"PRIMARY": "#FFFFFF", "ON_PRIMARY": "#123456"}))
        assert WebPreview(app)._theme_vars()["on_primary"] == "#123456"
    finally:
        app.stop()


@pytest.mark.parametrize("vertical", [False, True])
def test_web_dividers_use_tint_thickness_and_inset(vertical):
    html = render_html(
        Divider(vertical=vertical, thickness=3, inset=7, color="#80123456").to_dict()
    )
    assert "#12345680" in html and "7px" in html and "3px" in html
    assert "var(--line)" in render_html(Divider(vertical=vertical).to_dict())


def test_web_dialog_is_overlay_and_hidden_dialog_renders_nothing():
    dialog = Dialog(Label("Content"), title="<Title>", sheet=True)
    assert render_html(dialog.to_dict()) == ""
    dialog.open()
    html = render_html(dialog.to_dict())
    assert 'class="modal-overlay sheet"' in html and 'aria-modal="true"' in html
    assert "&lt;Title&gt;" in html and "'dismiss'" in html


@pytest.fixture
def assets_preview(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    PILImage.new("RGB", (4, 3), "red").save(assets / "logo.png")
    app = App("assets", bridge=StubBridge(verbose=False))
    preview = WebPreview(app, assets=assets)
    try:
        yield preview, assets
    finally:
        app.stop()


def test_web_local_assets_embed_without_mutating_original_tree(assets_preview):
    preview, assets = assets_preview
    node = {"type": "Image", "props": {"source": "assets/logo.png"}}
    result = preview._asset_tree(node)
    source = result["props"]["source"]
    assert source.startswith("data:image/png;base64,")
    assert base64.b64decode(source.split(",")[1]) == (assets / "logo.png").read_bytes()
    assert node["props"]["source"] == "assets/logo.png"
    preview.update({"type": "Column", "children": [node]})
    assert "data:image/png;base64," in preview.state()["body"]


@pytest.mark.parametrize(
    "source",
    [
        "assets/../outside.png",
        "assets/%2e%2e/outside.png",
        "assets/..\\outside.png",
        "/etc/passwd",
        "file:///etc/passwd",
        "https://example.com/logo.png",
        "https://[malformed",
    ],
)
def test_web_assets_do_not_read_outside_root_or_fetch_remote(assets_preview, source):
    preview, assets = assets_preview
    (assets.parent / "outside.png").write_bytes(b"private image")
    node = {"type": "Image", "props": {"source": source}}
    assert preview._asset_tree(node)["props"]["source"] == source


def test_web_assets_reject_escaping_symlinks_and_non_images(assets_preview):
    preview, assets = assets_preview
    outside = assets.parent / "secret.png"
    outside.write_bytes(b"private")
    try:
        (assets / "link.png").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    (assets / "secret.txt").write_text("private")
    for source in ["assets/link.png", "assets/secret.txt"]:
        node = {"type": "Image", "props": {"source": source}}
        assert preview._asset_tree(node)["props"]["source"] == source


def test_web_assets_limit_file_size(assets_preview):
    preview, assets = assets_preview
    with (assets / "large.png").open("wb") as file:
        file.seek(8 * 1024 * 1024)
        file.write(b"X")
    node = {"type": "Image", "props": {"source": "assets/large.png"}}
    assert preview._asset_tree(node)["props"]["source"] == "assets/large.png"
