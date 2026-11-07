"""Audit block 4: shared tables, image fitting, chrome and semantic contrast."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

import pymobile
from pymobile import (
    App,
    Avatar,
    Badge,
    Button,
    Calendar,
    Chip,
    Column,
    Image,
    Label,
    Screen,
    StubBridge,
    Style,
    TabView,
    Theme,
)
from pymobile.core.ui import theme as theme_module
from pymobile.core.ui._mockup_colors import _palette, _rgba
from pymobile.core.ui._mockup_fonts import _Fonts
from pymobile.core.ui._mockup_layout import _Layout
from pymobile.core.ui._mockup_paint import _Painter
from pymobile.core.ui.mockup import render_mockup
from pymobile.core.ui.web import render_html


def _readable_on(*args):
    return theme_module._readable_on(*args)


JAVA = Path(pymobile.__file__).parent / "resources/android/java"


@pytest.fixture
def layout():
    return _Layout(_Fonts(ImageFont, 1), _palette(None), None, 1)


@pytest.mark.parametrize("width", [80, 200, 360])
@pytest.mark.parametrize("fill", [False, True])
def test_table_columns_align_after_measuring_all_rows(layout, width, fill):
    props = {
        "headers": ["A", "Long header", "C"],
        "rows": [["a very long first value", "x", "short"], ["b", "yy", "last"]],
    }
    box = layout._table(props, width, fill)
    assert len(box.kids) == 9
    for column in range(3):
        entries = [box.kids[row * 3 + column] for row in range(3)]
        assert len({round(x, 5) for x, _, _ in entries}) == 1
        assert len({round(cell.w, 5) for _, _, cell in entries}) == 1
    for row in range(3):
        cells = box.kids[row * 3 : row * 3 + 3]
        assert len({cell.h for _, _, cell in cells}) == 1
        assert cells[-1][0] + cells[-1][2].w <= width + 1e-6
    assert box.h == sum(box.kids[row * 3][2].h for row in range(3))


def test_table_remeasures_after_mutation_and_handles_empty(layout):
    props = {"headers": ["a", "b"], "rows": [["1", "2"]]}
    before = layout._table(props, 360, False)
    props["rows"][0][0] = "much wider value"
    after = layout._table(props, 360, False)
    assert after.kids[1][0] > before.kids[1][0]
    assert after.kids[1][0] == after.kids[3][0]
    assert layout._table({}, 360, False).h == 0


@pytest.mark.parametrize(
    "fit,painted", [("contain", False), ("cover", True), ("fill", True), ("none", False)]
)
def test_mockup_image_fit_modes_and_clipping(layout, fit, painted):
    picture = PILImage.new("RGBA", (20, 10), "red")
    image = PILImage.new("RGBA", (60, 60), "white")
    painter = _Painter(ImageDraw, image, 1, layout.fonts)
    layout._paste_image(painter, picture, 10, 10, 40, 40, fit)
    assert image.getpixel((30, 30)) == (255, 0, 0, 255)
    assert image.getpixel((12, 12)) == ((255, 0, 0, 255) if painted else (255, 255, 255, 255))
    assert image.getpixel((9, 30)) == image.getpixel((50, 30)) == (255, 255, 255, 255)
    if fit == "none":
        assert image.getpixel((19, 30)) == (255, 255, 255, 255)
        assert image.getpixel((20, 30)) == (255, 0, 0, 255)


def test_fit_none_crops_oversized_image_without_bleeding_or_losing_alpha(layout):
    image = PILImage.new("RGBA", (50, 50), "white")
    picture = PILImage.new("RGBA", (100, 100), (255, 0, 0, 128))
    painter = _Painter(ImageDraw, image, 1, layout.fonts)
    layout._paste_image(painter, picture, 10, 10, 20, 20, "none")
    assert image.getpixel((15, 15)) == (255, 127, 127, 255)
    assert image.getpixel((9, 15)) == image.getpixel((30, 15)) == (255, 255, 255, 255)


@pytest.mark.parametrize("fit", ["contain", "cover", "fill", "none"])
def test_image_web_uses_requested_fit_with_fixed_box(fit):
    html = render_html(
        Image("assets/picture.png", fit=fit, style=Style(width=80, height=50)).to_dict()
    )
    assert f"object-fit:{fit}" in html and "height:100%" in html
    assert "width:80px" in html and "height:50px" in html


def test_compact_png_navigation_stays_single_line(layout):
    props = {
        "options": ["a", "b", "c", "d", "e"],
        "labels": ["Analytics", "Transactions", "Categories", "Preferences", "Overview"],
        "value": "c",
    }
    box = layout._bottom_nav(props, 320)
    assert box.h == 48
    for _, _, child in box.kids:
        painter = Mock()
        child.paint(painter, 0, 0, child)
        _, _, width, lines, size, _ = painter.lines.call_args.args
        assert len(lines) == 1 and child.h == 48
        assert layout.fonts.width(lines[0], size) <= width
        assert width == 52  # 64dp tab minus 6dp on each side


class Home(Screen):
    def build(self):
        return Label("body")


def test_preview_chrome_tracks_push_pop_title_and_override(monkeypatch):
    bridge = StubBridge(verbose=False)
    app = App("Application name", bridge=bridge)
    calls = []
    from pymobile.core.ui._mockup_chrome import _ChromeLayout

    original = _ChromeLayout.paint_chrome

    def capture(self, painter, width, title, *, back=False):
        calls.append((title, back))
        return original(self, painter, width, title, back=back)

    monkeypatch.setattr(_ChromeLayout, "paint_chrome", capture)
    try:
        app.run(Home("Home title"))
        assert bridge.last_tree["chrome"] == {"title": "Home title", "can_go_back": False}
        app.push(Home("Detail title"))
        render_mockup(bridge.last_tree, width=200, height=240)
        assert calls[-1] == ("Detail title", True)
        render_mockup(bridge.last_tree, title="Override", width=200, height=240)
        assert calls[-1] == ("Override", True)
        app.pop()
        render_mockup(bridge.last_tree, width=200, height=240)
        assert calls[-1] == ("Home title", False)
    finally:
        app.stop()


def test_png_chrome_draws_back_arrow_and_reserves_title_space(layout):
    painter = Mock()
    layout.paint_chrome(painter, 200, "Very long title " * 10, back=True)
    assert painter.line.call_count == 2
    title_call = painter.text.call_args
    assert title_call.args[0] == 56
    assert title_call.args[2].endswith("…")
    assert layout.fonts.width(title_call.args[2], 20, True) <= 128


def contrast(fg, bg):
    def luminance(colour):
        def linear(v):
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        return sum(
            w * linear(c / 255)
            for w, c in zip((0.2126, 0.7152, 0.0722), _rgba(colour)[:3], strict=True)
        )

    a, b = sorted([luminance(fg), luminance(bg)])
    return (b + 0.05) / (a + 0.05)


@pytest.mark.parametrize(
    "primary", ["#000000", "#FFFFFF", "#7986CB", "#9FA8DA", "#777777", "#00AA88", "#FF0000", "#ABC"]
)
def test_on_primary_wins_wcag_contrast_and_roundtrips(primary):
    theme = Theme("custom", {"PRIMARY": primary})
    foreground = theme["ON_PRIMARY"]
    assert contrast(foreground, primary) >= 4.5
    assert theme.as_dict()["ON_PRIMARY"] == foreground
    assert _palette(theme)["ON_PRIMARY"] == _rgba(foreground)
    assert _palette(theme.as_dict())["ON_PRIMARY"] == _rgba(foreground)


def test_on_primary_override_and_derived_dark_recompute():
    theme = Theme("custom", {"PRIMARY": "#EEEEEE", "ON_PRIMARY": "#123456"})
    assert theme["ON_PRIMARY"] == theme.as_dict()["ON_PRIMARY"] == "#123456"
    assert _palette(theme)["ON_PRIMARY"] == (18, 52, 86, 255)
    dark = Theme.dark_from(theme)
    assert contrast(dark["ON_PRIMARY"], dark["PRIMARY"]) >= 4.5
    assert dark["ON_PRIMARY"] in ("#000000", "#FFFFFF")
    assert contrast(Theme.dark()["ON_PRIMARY"], Theme.dark()["PRIMARY"]) >= 4.5


def test_translucent_primary_is_composited_over_surface():
    assert _readable_on("#00000000", "#FFFFFF") == "#000000"
    assert _readable_on("#00FFFFFF", "#000000") == "#FFFFFF"
    assert _palette({"PRIMARY": "invalid", "ON_PRIMARY": "invalid"})["ON_PRIMARY"] == (
        255,
        255,
        255,
        255,
    )


def test_patterns_badges_and_avatars_use_current_on_primary():
    app = App(
        "contrast",
        bridge=StubBridge(verbose=False),
        theme=Theme("custom", {"PRIMARY": "#EEEEEE", "ON_PRIMARY": "#123456"}),
    )
    tabs = TabView({"one": Label("first"), "two": Label("second")})
    calendar = Calendar(value="2026-11-06", month="2026-11")
    badge, avatar = Badge("1"), Avatar("AB")
    explicit = Badge("2", background="#FFFFFF")
    try:
        app.run(Column(tabs, calendar, badge, avatar, explicit))
        app.render()
        tab = next(n for n in tabs.walk() if isinstance(n, Button))
        selected_day = next(n for n in calendar.walk() if n.id.endswith(":day:2026-11-06"))
        assert tab.style.color == selected_day.style.color == "#123456"
        assert badge.props()["color"] == avatar.props()["color"] == "#123456"
        assert explicit.props()["color"] == "#000000"
        app.set_theme(Theme.dark())
        assert badge.props()["color"] == app.theme["ON_PRIMARY"]
    finally:
        app.stop()


def test_mockup_primary_button_chip_and_appbar_use_semantic_colour(layout):
    layout.p = _palette({"PRIMARY": "#EEEEEE", "ON_PRIMARY": "#123456"})
    for widget in [Button("text"), Chip("chip", selected=True)]:
        box = layout.layout(widget.to_dict(), 160, fill=True)
        painter = Mock()
        box.paint(painter, 0, 0, box)
        assert painter.lines.call_args.args[5] == (18, 52, 86, 255)
    painter = Mock()
    layout.paint_chrome(painter, 200, "Title", back=True)
    assert painter.text.call_args.args[4] == (18, 52, 86, 255)
    assert painter.line.call_args.args[1] == (18, 52, 86, 255)


def test_android_table_navigation_and_image_contracts():
    source = (JAVA / "ViewControls.java").read_text()
    table = source.split("View buildDataTable(")[1].split("boolean updateDataTable")[0]
    assert "new TableLayout" in table and table.count("new TableRow") == 2
    assert "setStretchAllColumns(true)" in table and "setShrinkAllColumns(true)" in table
    assert "getChildCount() != headers.length()" in source
    for mode in ["CENTER_CROP", "FIT_CENTER", "FIT_XY", "CENTER"]:
        assert "ImageView.ScaleType." + mode in source
    nav = source.split("View buildBottomNavigation")[1].split("void styleTab")[0]
    assert "setSingleLine(true)" in nav and "TextUtils.TruncateAt.END" in nav
    assert "setMinWidth(0)" in nav and "owner.dp(6)" in nav
    assert "int textColour = owner.colorOnPrimary" in source


def test_real_java_contrast_method_matches_python(tmp_path):
    javac = shutil.which("javac")
    java = shutil.which("java")
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        binary = Path(java_home) / "bin"
        suffix = ".exe" if os.name == "nt" else ""
        javac = javac or shutil.which(str(binary / ("javac" + suffix)))
        java = java or shutil.which(str(binary / ("java" + suffix)))
    if not javac or not java:
        pytest.skip("JDK needed to execute the Java contrast algorithm")
    source = (JAVA / "ViewBuilder.java").read_text()
    methods = source[
        source.index("    static int readableOn(int background)") : source.index(
            "    View buildTextInput("
        )
    ]
    harness = (
        """public class ContrastHarness {
      static class Color {
        static final int BLACK = 0xff000000, WHITE = 0xffffffff;
        static int red(int c) { return (c >>> 16) & 255; }
        static int green(int c) { return (c >>> 8) & 255; }
        static int blue(int c) { return c & 255; }
        static int alpha(int c) { return c >>> 24; }
      }
    """
        + methods
        + """
      public static void main(String[] args) {
        for(int i=0; i<args.length; i+=2) {
          int bg=(int)Long.parseLong(args[i],16), surface=(int)Long.parseLong(args[i+1],16);
          System.out.println(readableOn(bg, surface)==Color.BLACK ? "#000000" : "#FFFFFF");
        }
      }
    }"""
    )
    (tmp_path / "ContrastHarness.java").write_text(harness)
    subprocess.run([javac, str(tmp_path / "ContrastHarness.java")], check=True, capture_output=True)
    cases = [
        (f"#{alpha:02X}{r:02X}{g:02X}{b:02X}", surface)
        for alpha, r, g, b in [
            (255, 121, 134, 203),
            (255, 159, 168, 218),
            (128, 200, 20, 80),
            (0, 0, 0, 0),
            (255, 119, 119, 119),
        ]
        for surface in ["#FFFFFFFF", "#FF000000", "#80EEEEEE"]
    ]
    args = [colour[1:] for pair in cases for colour in pair]
    result = subprocess.run(
        [java, "-cp", str(tmp_path), "ContrastHarness", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [_readable_on(bg, surface) for bg, surface in cases]


@pytest.mark.parametrize("reload", [False, True])
def test_cli_png_uses_screen_title_not_application_name(tmp_path, monkeypatch, reload):
    from pymobile import cli
    from pymobile.compiler.scaffold import create_project
    from pymobile.core.ui import preview

    create_project(tmp_path, "App name")
    (tmp_path / "main.py").write_text("""from pymobile import App, Screen, Label
class Home(Screen):
    def build(self): return Label("body")
App("App name").run(Home("Screen title"))
""")
    calls = []

    def capture(tree, path, **kwargs):
        calls.append(kwargs["title"])
        return path

    monkeypatch.setattr(preview, "render_mockup", capture)
    args = cli.build_parser().parse_args(
        ["preview", "-c", str(tmp_path), "--png", str(tmp_path / "shot.png")]
    )
    try:
        if reload:
            config = cli._load(args)
            cli._reload(config, cli._entrypoint(config), args)
        else:
            cli.cmd_preview(args)
        assert calls == ["Screen title"]
    finally:
        if App.current():
            App.current().stop()


@pytest.mark.parametrize("fit", ["contain", "cover", "fill", "none"])
def test_public_png_image_fit_geometry(tmp_path, fit):
    source = PILImage.new("RGBA", (20, 10), "blue")
    ImageDraw.Draw(source).rectangle((0, 0, 4, 9), fill="red")
    source.save(tmp_path / "sample.png")
    widget = Image(str(tmp_path / "sample.png"), fit=fit, style=Style(width=80, height=80))
    output = render_mockup(widget, width=120, height=100, scale=1, chrome=False)
    expected = {
        "contain": (255, 255, 255),
        "cover": (0, 0, 255),
        "fill": (255, 0, 0),
        "none": (255, 255, 255),
    }
    assert output.getpixel((5, 5))[:3] == expected[fit]
    assert output.getpixel((40, 40))[:3] == (0, 0, 255)
    assert output.getpixel((85, 40))[:3] == (255, 255, 255)
    if fit == "none":
        assert output.getpixel((29, 40))[:3] == (255, 255, 255)
        assert output.getpixel((30, 40))[:3] == (255, 0, 0)
