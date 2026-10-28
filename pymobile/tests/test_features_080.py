"""Features added in 0.8.0: typed find, locale formatting, list gestures,
snackbar, x86_64 emulator builds and the PNG mockup.

Java cannot run on the desktop: its side is checked through the sources and
the prebuilt binaries that actually ship, as in ``test_audit_fixes.py``.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pymobile import (
    App,
    Button,
    Column,
    ConfirmDialog,
    EdgeInsets,
    Label,
    List,
    ListTile,
    Row,
    Screen,
    ScrollView,
    Snackbar,
    Style,
    TextInput,
    WidgetNotFoundError,
    WidgetTypeError,
    format_currency,
    format_date,
    format_datetime,
    format_number,
    format_percent,
    format_time,
)
from pymobile.core.bridge.stub import StubBridge
from pymobile.core.config import ProjectConfig
from pymobile.core.i18n import Translations
from pymobile.core.ui.gui import skeleton
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.snackbar import SNACKBAR_ID
from pymobile.core.ui.web import render_html
from pymobile.errors import PyMobileError

from .java_sources import renderer_source

PACKAGE = Path(__file__).resolve().parents[1]
ANDROID_DIR = PACKAGE / "resources" / "android"
JAVA_DIR = ANDROID_DIR / "java"
PREBUILT = ANDROID_DIR / "prebuilt"
README = PACKAGE.parent / "README.md"


def _run(screen: Screen, bridge: StubBridge | None = None) -> tuple[App, StubBridge]:
    stub = bridge or StubBridge(verbose=False)
    app = App("Test", bridge=stub)
    app.run(screen)
    return app, stub


def _find(node: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    found = [node] if node.get("type") == kind else []
    for child in node.get("children", ()):
        found += _find(child, kind)
    return found


# --------------------------------------------------------------------------
# 1. typed find (closes FND-07)
# --------------------------------------------------------------------------
class _Form(Screen):
    def build(self) -> Column:
        return Column(Label("Hi", id="title"), TextInput(id="name"), Button("Go", id="go"))


def test_find_with_a_class_returns_that_type() -> None:
    app, _ = _run(_Form())
    screen = app.screen
    name = screen.find("name", TextInput)
    assert isinstance(name, TextInput)
    assert screen.find("missing", TextInput) is None
    assert screen.find("title") is not None  # the untyped form still works


def test_find_with_the_wrong_class_raises_a_helpful_error() -> None:
    app, _ = _run(_Form())
    with pytest.raises(WidgetTypeError) as info:
        app.screen.find("title", TextInput)
    assert isinstance(info.value, TypeError)
    assert "Label" in str(info.value) and "TextInput" in str(info.value)


def test_get_raises_when_the_id_is_missing() -> None:
    app, _ = _run(_Form())
    assert app.screen.get("go", Button).text == "Go"
    with pytest.raises(WidgetNotFoundError) as info:
        app.screen.get("nmae", TextInput)
    assert isinstance(info.value, LookupError)
    assert "name" in (info.value.hint or "")  # "did you mean" hint


def test_find_all_collects_every_widget_of_a_class() -> None:
    app, _ = _run(_Form())
    root = app.screen.root
    assert [w.id for w in root.find_all(Button)] == ["go"]
    assert len(root.find_all(Label)) == 1


# --------------------------------------------------------------------------
# 2. locale-aware formatting (closes I18-08)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "1,234,567.89"),
        ("uk", "1\u00a0234\u00a0567,89"),
        ("de", "1.234.567,89"),
        ("fr", "1\u202f234\u202f567,89"),
        ("de-CH", "1’234’567.89"),
    ],
)
def test_format_number_follows_the_language(language: str, expected: str) -> None:
    assert format_number(1234567.891, 2, language=language) == expected


def test_format_number_rounds_half_even_and_hides_float_noise() -> None:
    assert format_number(0.1 + 0.2, language="en") == "0.3"
    assert format_number(Decimal("2.5"), 0, language="en") == "2"
    assert format_percent(0.256, language="uk") == "26%"
    assert format_percent(0.256, language="de") == "26\u00a0%"


def test_format_currency_places_the_symbol_per_language() -> None:
    assert format_currency(1250, "UAH", language="uk") == "1\u00a0250,00\u00a0₴"
    assert format_currency(1250, "USD", language="en") == "$1,250.00"
    assert format_currency(-5, "EUR", language="de") == "-5,00\u00a0€"
    assert format_currency(1250, "JPY", language="en").endswith("1,250")


def test_format_date_and_time_use_month_names_and_patterns() -> None:
    day = date(2026, 10, 1)
    assert format_date(day, "long", language="uk") == "1 жовтня 2026 р."
    assert format_date(day, "long", language="en") == "October 1, 2026"
    assert format_date(day, pattern="d MMMM", language="uk") == "1 жовтня"
    assert format_time(dtime(14, 5), language="en") == "2:05 PM"
    assert format_time(dtime(14, 5), language="uk") == "14:05"
    assert "2026" in format_datetime(datetime(2026, 10, 1, 9, 30), language="de")


def test_unknown_language_falls_back_to_english() -> None:
    assert format_number(1234.5, 1, language="xx") == "1,234.5"


def test_translation_placeholders_accept_format_specifiers() -> None:
    catalog = Translations()
    catalog.load({"total": "Разом: {sum:currency:UAH}, до {day:date:long}"}, language="uk")
    catalog.use("uk")
    text = catalog.get("total", sum=1250, day=date(2026, 10, 1))
    assert text == "Разом: 1\u00a0250,00\u00a0₴, до 1 жовтня 2026 р."


def test_a_bad_specifier_does_not_crash_the_screen() -> None:
    catalog = Translations()
    catalog.load({"due": "до {day:date}"}, language="en")
    catalog.use("en")
    assert catalog.get("due", day=5) == "до {day:date}"


# --------------------------------------------------------------------------
# 3. list gestures: swipe, pull to refresh, scroll_to
# --------------------------------------------------------------------------
class _Inbox(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.items = [f"mail {i}" for i in range(30)]
        self.log: list[Any] = []
        self.pending: list[Any] = []

    def build(self) -> ScrollView:
        return ScrollView(
            List(
                len(self.items),
                builder=self.row,
                on_refresh=self.reload,
                visible_count=10,
                id="inbox",
            )
        )

    def row(self, index: int) -> ListTile:
        return ListTile(
            self.items[index],
            on_swipe_left=lambda: self.delete(index),
            on_swipe_right=lambda: self.log.append(("archive", index)),
        )

    def delete(self, index: int) -> None:
        self.log.append(("delete", index))
        self.items.pop(index)
        self.refresh()

    def reload(self) -> Any:
        self.log.append("reload")
        return self.pending.pop() if self.pending else None


def test_list_tile_serialises_its_swipe_directions() -> None:
    _, stub = _run(_Inbox())
    tile = _find(stub.last_tree, "ListTile")[0]
    assert tile["props"]["swipe_left"] is True
    assert tile["props"]["swipe_right"] is True
    assert tile["props"]["swipe_left_color"] == "#E53935"
    plain = ListTile("x").to_dict()["props"]
    assert plain["swipe_left"] is False and plain["swipe_right"] is False


def test_a_swipe_event_calls_the_handler_of_its_direction() -> None:
    app, stub = _run(_Inbox())
    screen = app.screen
    first = _find(stub.last_tree, "ListTile")[0]["id"]
    app._handle_ui_event(first, "swipe", "right")
    app._handle_ui_event(first, "swipe", "left")
    assert screen.log == [("archive", 0), ("delete", 0)]
    assert _find(stub.last_tree, "ListTile")[0]["props"]["title"] == "mail 1"


def test_swipe_is_ignored_when_disabled_and_rejects_bad_directions() -> None:
    calls: list[str] = []
    tile = ListTile("x", on_swipe_left=lambda: calls.append("l"))
    tile.swipe("right")  # no handler: nothing happens
    tile.enabled = False
    tile.swipe("left")
    assert calls == []
    with pytest.raises(ValueError):
        tile.swipe("up")


def test_pull_to_refresh_spins_until_an_async_result_finishes() -> None:
    class Future:
        def __init__(self) -> None:
            self.callbacks: tuple[Any, Any] | None = None

        def then(self, ok: Any, error: Any) -> None:
            self.callbacks = (ok, error)

    app, stub = _run(_Inbox())
    screen = app.screen
    future = Future()
    screen.pending.append(future)
    inbox = screen.get("inbox", List)
    props = _find(stub.last_tree, "List")[0]["props"]
    assert props["refreshable"] is True and props["refreshing"] is False

    app._handle_ui_event("inbox", "refresh", "")
    assert inbox.refreshing is True
    assert _find(stub.last_tree, "List")[0]["props"]["refreshing"] is True
    app._handle_ui_event("inbox", "refresh", "")  # impatient second pull
    assert screen.log == ["reload"]

    assert future.callbacks is not None
    future.callbacks[0]("done")
    assert inbox.refreshing is False
    assert _find(stub.last_tree, "List")[0]["props"]["refreshing"] is False


def test_a_synchronous_refresh_stops_the_spinner_immediately() -> None:
    app, _ = _run(_Inbox())
    inbox = app.screen.get("inbox", List)
    inbox.pull_to_refresh()
    assert app.screen.log == ["reload"] and inbox.refreshing is False


def test_a_list_without_on_refresh_is_not_refreshable() -> None:
    props = List(3, builder=lambda i: ListTile(str(i))).to_dict()["props"]
    assert props["refreshable"] is False


def test_scroll_to_builds_the_rows_and_bumps_the_serial() -> None:
    app, stub = _run(_Inbox())
    inbox = app.screen.get("inbox", List)
    assert _find(stub.last_tree, "List")[0]["props"]["loaded"] == 10
    inbox.scroll_to(25, animated=False)
    props = _find(stub.last_tree, "List")[0]["props"]
    assert props["loaded"] >= 26
    assert (props["scroll_to"], props["scroll_serial"], props["scroll_animated"]) == (25, 1, False)
    inbox.scroll_to(25)  # again: the user may have scrolled away
    assert _find(stub.last_tree, "List")[0]["props"]["scroll_serial"] == 2
    assert inbox.scroll_target == 25
    with pytest.raises(IndexError):
        inbox.scroll_to(30)


# --------------------------------------------------------------------------
# 4. snackbar
# --------------------------------------------------------------------------
class _Notes(Screen):
    def build(self) -> Column:
        return Column(Label("notes"))


def test_snackbar_is_part_of_the_rendered_frame() -> None:
    app, stub = _run(_Notes())
    restored: list[bool] = []
    bar = app.snackbar("Deleted", action="Undo", on_action=lambda: restored.append(True))
    assert isinstance(bar, Snackbar) and app.current_snackbar is bar
    data = stub.last_tree["snackbar"]
    assert data == {
        "id": SNACKBAR_ID,
        "message": "Deleted",
        "action": "Undo",
        "token": bar.token,
        "duration_ms": 7000,
    }
    app._handle_ui_event(SNACKBAR_ID, "press", str(bar.token))
    assert restored == [True]
    assert app.current_snackbar is None
    assert "snackbar" not in stub.last_tree


def test_a_new_snackbar_replaces_the_old_one_and_stale_taps_are_ignored() -> None:
    app, _ = _run(_Notes())
    calls: list[str] = []
    old = app.snackbar("one", action="A", on_action=lambda: calls.append("old"))
    new = app.snackbar("two", action="B", on_action=lambda: calls.append("new"))
    assert not old.visible and app.current_snackbar is new
    app._handle_ui_event(SNACKBAR_ID, "press", str(old.token))
    assert calls == [] and app.current_snackbar is new
    app._handle_ui_event(SNACKBAR_ID, "dismiss", str(new.token))
    assert calls == [] and app.current_snackbar is None


def test_snackbar_hides_itself_after_its_duration() -> None:
    app, _ = _run(_Notes())
    app.snackbar("brief", duration_ms=30)
    deadline = time.monotonic() + 3
    while app.current_snackbar is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert app.current_snackbar is None


def test_snackbar_survives_navigation_and_validates_arguments() -> None:
    app, stub = _run(_Notes())
    app.snackbar("saved", duration_ms=0)
    app.push(_Form())
    assert stub.last_tree["snackbar"]["message"] == "saved"
    with pytest.raises(ValueError):
        app.snackbar("x", on_action=lambda: None)  # nothing to tap
    with pytest.raises(ValueError):
        app.snackbar("x", action=" ")
    with pytest.raises(ValueError):
        app.snackbar("x", duration_ms=-1)


def test_previews_show_the_snackbar_and_the_gestures() -> None:
    app, stub = _run(_Inbox())
    app.snackbar("Deleted", action="Undo")
    picture = render_ascii(stub.last_tree)
    assert picture.splitlines()[-1] == "▌ Deleted   [Undo]"
    html = render_html(stub.last_tree)
    assert 'title="swipe left"' in html and "Pull to refresh" in html
    assert 'data-scroll-serial="0"' in html and 'data-row="0"' in html
    tile = _find(stub.last_tree, "ListTile")[0]
    changed = {**tile, "props": {**tile["props"], "swipe_left": False}}
    assert skeleton(tile) != skeleton(changed)  # Tk rebuilds its buttons


def test_java_renders_the_snackbar_and_the_gestures() -> None:
    builder = renderer_source()
    activity = (JAVA_DIR / "MainActivity.java").read_text(encoding="utf-8")
    for needle in (
        "void syncSnackbar(",
        '"dismiss"',
        "class PullList",
        '"refresh"',
        "void setSwipe(",
        '"swipe"',
        "void applyScrollRequest(",
        "scroll_serial",
    ):
        assert needle in builder, needle
    assert "syncSnackbar(" in activity and '"snackbar"' in activity


def test_the_shipped_dex_contains_the_new_java() -> None:
    dex = (PREBUILT / "arm64-v8a" / "classes.dex").read_bytes()
    for name in (b"syncSnackbar", b"PullList", b"setSwipe", b"applyScrollRequest"):
        assert name in dex, name


# --------------------------------------------------------------------------
# 5. x86_64 emulator builds
# --------------------------------------------------------------------------
def test_an_x86_64_bridge_ships_next_to_the_arm64_one() -> None:
    library = (PREBUILT / "x86_64" / "libpymobile.so").read_bytes()
    assert library[:4] == b"\x7fELF"
    assert int.from_bytes(library[18:20], "little") == 62  # EM_X86_64
    arm = (PREBUILT / "arm64-v8a" / "libpymobile.so").read_bytes()
    assert int.from_bytes(arm[18:20], "little") == 183  # EM_AARCH64


def test_the_apk_name_carries_a_non_default_abi(project: ProjectConfig) -> None:
    phone = project.apk_name
    assert "x86_64" not in phone
    project.abis = ["x86_64"]
    assert project.apk_name == phone.replace(".apk", "-x86_64.apk")


def test_build_accepts_an_abi_flag() -> None:
    from pymobile.cli import build_parser

    args = build_parser().parse_args(["build", "--abi", "x86_64"])
    assert args.abi == "x86_64"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["build", "--abi", "armeabi-v7a"])


def test_an_x86_64_build_copies_the_x86_64_bridge(tmp_path: Path, project: ProjectConfig) -> None:
    from pymobile.compiler.backends.native import NativeBackend

    backend = object.__new__(NativeBackend)
    backend.abi = "x86_64"
    backend.config = project
    libdir = tmp_path / "runtime"
    libdir.mkdir()
    out = tmp_path / "lib"
    out.mkdir()
    backend._use_prebuilt_bridge(out, libdir)
    copied = (out / "libpymobile.so").read_bytes()
    assert copied == (PREBUILT / "x86_64" / "libpymobile.so").read_bytes()


def test_readme_explains_the_emulator() -> None:
    text = README.read_text(encoding="utf-8")
    assert "pymobile build --native --abi x86_64" in text
    assert "-x86_64.apk" in text


# --------------------------------------------------------------------------
# 6. PNG mockup
# --------------------------------------------------------------------------
@pytest.fixture
def PIL() -> Any:
    pytest.importorskip("PIL.Image")
    import PIL.Image

    return PIL


def _pixel(image: Any, x: float, y: float, scale: float = 2) -> tuple[int, int, int]:
    return image.getpixel((round(x * scale), round(y * scale)))[:3]


def test_mockup_draws_the_screen_with_theme_colours(PIL: Any) -> None:
    from pymobile.core.ui.mockup import render_mockup

    _, stub = _run(_Form())
    image = render_mockup(stub.last_tree, title="Form")
    assert image.size == (720, 1280)  # 360 x 640 dp at 2x
    assert _pixel(image, 180, 50) == (0x3F, 0x51, 0xB5)  # action bar = PRIMARY
    assert _pixel(image, 180, 600) == (255, 255, 255)  # background
    dark = render_mockup(stub.last_tree, theme="dark", chrome=False)
    assert _pixel(dark, 180, 600) == (18, 18, 18)


def test_mockup_lays_out_rows_expanded_and_buttons_like_the_device(PIL: Any) -> None:
    from PIL import ImageFont

    from pymobile.core.ui.mockup import _Fonts, _Layout, _palette

    tree = Column(
        Row(Button("A"), Label("", style=None), Button("B")),
        Label("text"),
    ).to_dict()
    layout = _Layout(_Fonts(ImageFont, 2), _palette(None), None, 2)
    box = layout.layout(tree, 360, fill=True)
    assert box is not None and box.w == 360
    row = box.kids[0][2]
    first, _, second = (kid for _, _, kid in row.kids)
    assert first.h == 48 and first.w == 88  # Material minimums
    assert second.w == 88
    assert row.kids[2][0] >= first.w  # laid out left to right


def test_mockup_expanded_takes_the_free_height(PIL: Any) -> None:
    from PIL import ImageFont

    from pymobile import Expanded
    from pymobile.core.ui.mockup import _Fonts, _Layout, _palette

    tree = Column(Label("top"), Expanded(Column()), Button("bottom")).to_dict()
    layout = _Layout(_Fonts(ImageFont, 2), _palette(None), None, 2)
    box = layout.layout(tree, 360, fill=True, force_h=560)
    assert box is not None
    _, y, last = box.kids[-1]
    assert y + last.h == pytest.approx(560)


def test_mockup_paints_dialogs_and_the_snackbar(tmp_path: Path, PIL: Any) -> None:
    from pymobile.core.ui.mockup import render_mockup

    class WithDialog(Screen):
        def build(self) -> Column:
            self.dialog = ConfirmDialog("Delete?", "Sure?")
            return Column(Label("x"), self.dialog)

        def on_mount(self) -> None:
            self.dialog.open()

    app, stub = _run(WithDialog())
    app.snackbar("Deleted", action="Undo")
    path = render_mockup(stub.last_tree, tmp_path / "shot.png", height=640)
    image = PIL.Image.open(path).convert("RGB")
    assert _pixel(image, 180, 600) != (255, 255, 255)  # dimmed by the dialog
    assert _pixel(image, 180, 320) == (255, 255, 255)  # the dialog surface


def test_preview_png_writes_a_mockup_and_text_keeps_the_old_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, PIL: Any
) -> None:
    from pymobile.cli import main

    (tmp_path / "pymobile.toml").write_text(
        '[app]\nname = "Demo"\npackage = "org.example.demo"\nversion = "1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from pymobile import App, Screen, Label, Button, Column\n"
        "class S(Screen):\n"
        "    def build(self):\n"
        "        return Column(Label('Hello'), Button('Go'))\n"
        "App('Demo').run(S())\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["preview", "--png", "m.png", "--size", "320x480"]) == 0
    assert PIL.Image.open(tmp_path / "m.png").size == (640, 960)
    assert main(["preview", "--png", "t.png", "--text"]) == 0
    assert PIL.Image.open(tmp_path / "t.png").getpixel((0, 0))[:3] == (0x10, 0x14, 0x18)
    with pytest.raises((SystemExit, PyMobileError)):
        code = main(["preview", "--png", "x.png", "--size", "big"])
        if code:
            raise SystemExit(code)


# --------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------
def test_readme_documents_the_new_features() -> None:
    text = README.read_text(encoding="utf-8")
    for needle in (
        'find("',
        "format_currency",
        "on_swipe_left",
        "on_refresh",
        "app.snackbar(",
        "--abi x86_64",
        "render_mockup",
    ):
        assert needle in text, needle
    known = text[text.index("Known issues") :]
    assert "FND-07" not in known and "I18-08" not in known


# --------------------------------------------------------------------------
# release fixes: Android 7.0 floor, Style padding/margin shorthands
# --------------------------------------------------------------------------
def _manifest_min_sdk(config: ProjectConfig) -> str | None:
    from xml.etree import ElementTree as ET

    from pymobile.compiler.manifest import ANDROID_NS, build_manifest

    sdk = ET.fromstring(build_manifest(config)).find("uses-sdk")
    assert sdk is not None
    return sdk.get(f"{{{ANDROID_NS}}}minSdkVersion")


def test_new_projects_target_android_7(tmp_path: Path) -> None:
    from pymobile.compiler.scaffold import create_project
    from pymobile.core.config import RUNTIME_MIN_SDK, load_config

    assert RUNTIME_MIN_SDK == 24
    assert ProjectConfig(root=tmp_path).min_sdk == 24
    create_project(tmp_path / "fresh", "Fresh")
    config = load_config(tmp_path / "fresh")
    assert config.min_sdk == 24
    assert _manifest_min_sdk(config) == "24"


def test_legacy_min_sdk_21_is_raised_to_24_with_a_warning(project: ProjectConfig) -> None:
    from pymobile.compiler.pipeline import BuildPipeline

    project.min_sdk = 21
    project.validate()  # still loads: projects created before 0.8.0 say 21
    assert project.effective_min_sdk == 24
    assert _manifest_min_sdk(project) == "24"
    result = BuildPipeline(project).run()
    assert any("minSdkVersion 24" in warning for warning in result.warnings)
    project.min_sdk = 26
    assert _manifest_min_sdk(project) == "26"
    assert not any("minSdkVersion" in w for w in BuildPipeline(project).run().warnings)


def test_min_sdk_errors_name_the_real_floor(tmp_path: Path) -> None:
    from pymobile.errors import ConfigError

    with pytest.raises(ConfigError, match="at least 24"):
        ProjectConfig(root=tmp_path, min_sdk=19)
    with pytest.raises(ConfigError, match="target_sdk"):
        ProjectConfig(root=tmp_path, min_sdk=21, target_sdk=23)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8, [8, 8, 8, 8]),
        (7.6, [8, 8, 8, 8]),
        ((1, 2, 3, 4), [1, 2, 3, 4]),
        ([1, 2, 3, 4], [1, 2, 3, 4]),
        (EdgeInsets.symmetric(horizontal=16, vertical=8), [16, 8, 16, 8]),
    ],
)
def test_style_accepts_padding_and_margin_shorthands(value: Any, expected: list[int]) -> None:
    style = Style(padding=value, margin=value)
    assert isinstance(style.padding, EdgeInsets)
    assert style.to_dict()["padding"] == expected
    assert style.to_dict()["margin"] == expected


def test_style_shorthands_survive_merge_hashing_and_rendering() -> None:
    style = Style(padding=8).merge(margin=4)
    assert style.to_dict() == {"padding": [8, 8, 8, 8], "margin": [4, 4, 4, 4]}
    assert hash(Style(padding=[1, 2, 3, 4])) == hash(Style(padding=EdgeInsets(1, 2, 3, 4)))
    assert Style(padding=0).to_dict() == {}
    tree = Label("hi", style=Style(padding=12)).to_dict()
    assert tree["style"]["padding"] == [12, 12, 12, 12]


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        ((8, 16), TypeError, "ambiguous"),
        ("8", TypeError, "number of dp"),
        (True, TypeError, "number of dp"),
        ((1, 2, 3), TypeError, "four numbers"),
        (-1, ValueError, "negative"),
        ((0, -4, 0, 0), ValueError, "negative"),
    ],
)
def test_style_rejects_unclear_insets(value: Any, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        Style(margin=value)
