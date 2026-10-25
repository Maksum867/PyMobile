"""Keyword ergonomics: strict built-in keywords, spacing, tappable containers.

Four findings from the field lists, each one a place where the framework
accepted something it could not honour:

1. an unknown keyword on a built-in widget was stored as an *extension prop*
   and ignored by every renderer (``Label("hi", colr="red")`` rendered a black
   label); a callable raised a message about JSON-like values instead;
2. ``spacing`` — the prop everyone reaches for — only existed on Column/Row/
   Grid/Wrap/ScrollView, so cards and dialogs were wrapped in a Column to get it;
3. only Button/Chip/ListTile/Link took a tap, so a tappable row had to be
   rebuilt as a ListTile with its content hidden in a Button;
4. ``EdgeInsets.top(8)`` raised ``'member_descriptor' object is not callable``.
"""

from __future__ import annotations

import pytest

from pymobile import (
    BottomSheet,
    Button,
    Card,
    Column,
    Container,
    Dialog,
    EdgeInsets,
    Label,
    Row,
    Screen,
    Stack,
    Style,
    Widget,
)
from pymobile.core.ui.contract import text_value


# ---------------------------------------------------------------------------
# 1. unknown keywords on a built-in widget are refused, with the valid names
# ---------------------------------------------------------------------------
class TestStrictKeywords:
    def test_a_typo_is_a_type_error(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument 'colr'"):
            Label("hi", colr="red")

    def test_the_message_lists_the_valid_names(self) -> None:
        with pytest.raises(TypeError) as caught:
            Label("hi", colr="red")
        assert "It takes: text" in str(caught.value)

    def test_a_callable_gets_the_same_error_not_a_json_prop_message(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument 'on_pres'"):
            Button("b", on_pres=lambda: None)  # type: ignore[call-arg]

    def test_spacing_on_a_leaf_points_at_the_layouts(self) -> None:
        with pytest.raises(TypeError) as caught:
            Label("hi", spacing=8)
        assert "spacing is a prop of" in (caught.value.hint or "")  # type: ignore[attr-defined]

    def test_a_style_attribute_points_at_style(self) -> None:
        with pytest.raises(TypeError) as caught:
            Label("hi", colr="red")
        hint = caught.value.hint or ""  # type: ignore[attr-defined]
        assert "Style(color=" in hint

    def test_extension_props_are_explicit(self) -> None:
        widget = Widget(extra={"my_key": 1})
        assert widget.to_dict()["props"]["my_key"] == 1

    def test_a_custom_widget_keeps_free_keywords(self) -> None:
        class Sparkline(Widget):
            type_name = "Sparkline"

        assert Sparkline(points=[1, 2, 3]).to_dict()["props"]["points"] == [1, 2, 3]

    def test_a_subclass_of_a_builtin_keeps_them_too(self) -> None:
        class TrackRow(Card):
            pass

        assert TrackRow(Label("t"), highlight="today").to_dict()["props"]["highlight"] == "today"

    def test_set_prop_is_still_the_escape_hatch(self) -> None:
        label = Label("hi")
        label.set_prop("legend", "bottom")
        assert label.to_dict()["props"]["legend"] == "bottom"

    def test_a_valid_keyword_still_works(self) -> None:
        assert Column(Label("a"), spacing=8, align="center").to_dict()["props"]["spacing"] == 8


# ---------------------------------------------------------------------------
# 2. spacing is a prop of Card, Container, Dialog, BottomSheet and Stack
# ---------------------------------------------------------------------------
class TestSpacing:
    def test_card_takes_spacing_itself(self) -> None:
        """``Card(*children, spacing=8)`` is the whole point: no wrapping Column."""
        card = Card(Label("a"), Label("b"), spacing=8)
        assert card.to_dict()["props"]["spacing"] == 8
        assert [type(child) for child in card.children] == [Label, Label]

    def test_card_keeps_its_default(self) -> None:
        assert Card(Label("a")).to_dict()["props"]["spacing"] == 12

    def test_container_takes_spacing(self) -> None:
        assert Container(Label("a"), Label("b"), spacing=6).to_dict()["props"]["spacing"] == 6

    def test_stack_takes_spacing(self) -> None:
        assert Stack(Label("a"), Label("b"), spacing=4).to_dict()["props"]["spacing"] == 4

    def test_dialog_takes_spacing(self) -> None:
        assert Dialog(Label("a"), Label("b"), spacing=10).to_dict()["props"]["spacing"] == 10

    def test_bottom_sheet_takes_spacing(self) -> None:
        sheet = BottomSheet(Label("a"), Label("b"), spacing=9)
        assert sheet.to_dict()["props"]["spacing"] == 9
        assert sheet.to_dict()["props"]["sheet"] is True

    def test_an_uncustomised_dialog_sends_no_spacing(self) -> None:
        """A missing prop must not appear, or every dialog would gain a gap."""
        assert "spacing" not in Dialog(Label("a")).to_dict()["props"]

    @pytest.mark.parametrize("kind", [Column, Row, Stack, Container])
    def test_spacing_is_validated(self, kind: type) -> None:
        with pytest.raises(ValueError):
            kind(Label("a"), spacing=-1)

    def test_a_subclass_value_is_not_overwritten_by_container(self) -> None:
        """Column sets spacing itself; Container.__init__ must not reset it."""
        assert Column(Label("a"), spacing=7).to_dict()["props"]["spacing"] == 7

    def test_the_web_preview_draws_the_dialog_gap(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Dialog(Label("a"), Label("b"), spacing=10, visible=True).to_dict())
        assert "gap:10px" in html

    def test_the_web_preview_draws_the_container_gap(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Container(Label("a"), Label("b"), spacing=5).to_dict())
        assert "gap:5px" in html


# ---------------------------------------------------------------------------
# 3. containers are tappable
# ---------------------------------------------------------------------------
class TestTappableContainers:
    def test_a_container_takes_on_press(self) -> None:
        taps: list[str] = []
        box = Container(Label("a"), on_press=lambda: taps.append("hit"))
        box.press()
        assert taps == ["hit"]

    def test_a_card_takes_on_press(self) -> None:
        taps: list[int] = []
        card = Card(Label("song"), Label("artist"), on_press=lambda: taps.append(1))
        card.press()
        assert taps == [1]

    @pytest.mark.parametrize("kind", [Row, Column, Stack, Container])
    def test_every_layout_takes_on_press(self, kind: type) -> None:
        taps: list[str] = []
        widget = kind(Label("a"), on_press=lambda: taps.append("x"))
        widget.press()
        assert taps == ["x"]

    def test_on_long_press_too(self) -> None:
        held: list[str] = []
        row = Row(Label("a"), on_long_press=lambda: held.append("held"))
        row.long_press()
        assert held == ["held"]

    def test_a_disabled_container_ignores_the_tap(self) -> None:
        taps: list[str] = []
        box = Container(Label("a"), on_press=lambda: taps.append("hit"), enabled=False)
        box.press()
        assert taps == []

    def test_pressable_says_whether_it_takes_a_tap(self) -> None:
        assert Container(Label("a")).pressable is False
        assert Container(Label("a"), on_press=lambda: None).pressable is True
        assert Container(Label("a"), on_long_press=lambda: None).pressable is True

    def test_the_handler_is_serialised(self) -> None:
        props = Card(Label("a"), on_press=lambda: None).to_dict()["props"]
        assert props["on_press"] == "<lambda>"

    def test_a_plain_container_serialises_nothing_extra(self) -> None:
        props = Container(Label("a")).to_dict()["props"]
        assert "on_press" not in props and "on_long_press" not in props

    def test_the_web_preview_makes_it_clickable(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Container(Label("a"), on_press=lambda: None, id="row").to_dict())
        assert 'role="button"' in html
        assert "send(this.dataset.wid,'press','')" in html
        assert 'data-wid="row"' in html

    def test_the_web_preview_keeps_a_plain_layout_inert(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Container(Label("a"), id="row").to_dict())
        assert "onclick" not in html
        assert 'role="button"' not in html

    def test_the_web_preview_disables_a_disabled_target(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(
            Row(Label("a"), on_press=lambda: None, enabled=False, id="row").to_dict()
        )
        assert 'aria-disabled="true"' in html

    def test_the_ascii_preview_marks_the_target(self) -> None:
        from pymobile.core.ui.preview import _node_lines

        lines = _node_lines(Card(Label("Song"), on_press=lambda: None).to_dict())
        assert lines[0].startswith("▸")
        assert lines[-1].startswith("◂")

    def test_the_ascii_preview_leaves_a_plain_card_alone(self) -> None:
        from pymobile.core.ui.preview import _node_lines

        lines = _node_lines(Card(Label("Song")).to_dict())
        assert not any(line.startswith("▸") for line in lines)

    def test_the_device_renderer_binds_the_tap(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "resources/android/java/ViewBuilder.java"
        ).read_text(encoding="utf-8")
        assert "bindContainerTap" in source
        assert 'dispatchEvent(id, "long_press", "")' in source

    def test_the_android_registry_claims_container(self) -> None:
        """Container used to be a placeholder on the phone while it worked in previews."""
        from pymobile.core.ui.registry import is_known

        assert is_known("Container", renderer="android")


# ---------------------------------------------------------------------------
# 4. EdgeInsets: only() instead of calling a field
# ---------------------------------------------------------------------------
class TestEdgeInsets:
    def test_only_top(self) -> None:
        assert EdgeInsets.only(top=8).to_list() == [0, 8, 0, 0]

    def test_only_several_sides(self) -> None:
        assert EdgeInsets.only(top=8, bottom=12).to_list() == [0, 8, 0, 12]

    def test_leading_and_trailing(self) -> None:
        assert EdgeInsets.only(leading=16, trailing=4).to_list() == [16, 0, 4, 0]

    def test_start_and_end(self) -> None:
        assert EdgeInsets.only(start=2, end=3).to_list() == [2, 0, 3, 0]

    def test_vertical_and_horizontal_groups(self) -> None:
        assert EdgeInsets.only(vertical=8).to_list() == [0, 8, 0, 8]
        assert EdgeInsets.only(horizontal=6).to_list() == [6, 0, 6, 0]
        assert EdgeInsets.only(horizontal=6, vertical=2).to_list() == [6, 2, 6, 2]

    def test_no_arguments_is_zero(self) -> None:
        assert EdgeInsets.only().to_list() == [0, 0, 0, 0]

    def test_calling_a_field_explains_itself(self) -> None:
        with pytest.raises(TypeError) as caught:
            EdgeInsets.top(8)  # type: ignore[attr-defined]
        assert "EdgeInsets.only(top=8)" in str(caught.value)

    def test_a_side_named_twice_is_refused(self) -> None:
        with pytest.raises(TypeError, match="twice"):
            EdgeInsets.only(top=8, vertical=8)
        with pytest.raises(TypeError, match="twice"):
            EdgeInsets.only(left=4, leading=4)

    def test_negative_and_non_int_sides_are_refused(self) -> None:
        with pytest.raises(ValueError):
            EdgeInsets.only(top=-1)
        with pytest.raises(TypeError):
            EdgeInsets.only(top=1.5)  # type: ignore[arg-type]

    def test_the_fields_are_still_plain_numbers(self) -> None:
        inset = EdgeInsets(1, 2, 3, 4)
        assert (inset.left, inset.top, inset.right, inset.bottom) == (1, 2, 3, 4)

    def test_style_still_accepts_it(self) -> None:
        assert Style(padding=EdgeInsets.only(top=8)).to_dict() == {"padding": [0, 8, 0, 0]}

    def test_dataclass_machinery_still_sees_the_fields(self) -> None:
        from dataclasses import asdict, replace

        inset = EdgeInsets(1, 2, 3, 4)
        assert asdict(inset) == {"left": 1, "top": 2, "right": 3, "bottom": 4}
        assert replace(inset, top=9).top == 9
        assert inset == EdgeInsets(1, 2, 3, 4)
        assert hash(inset) == hash(EdgeInsets(1, 2, 3, 4))


# ---------------------------------------------------------------------------
# the whole thing on a screen, through the driver
# ---------------------------------------------------------------------------
class Home(Screen):
    title = "Home"

    def build(self) -> Widget:
        self.status = Label("idle", id="status")
        return Column(
            Card(
                Label("Song"),
                Label("Artist"),
                id="track",
                spacing=6,
                on_press=self.tap,
            ),
            self.status,
        )

    def tap(self) -> None:
        self.status.text = "tapped"


class OnlyText(Screen):
    def build(self) -> Widget:
        return Column(Label("a", id="text"))


def _driver(tmp_path, screen: type[Screen]) -> tuple[object, object]:
    from pymobile import App
    from pymobile.core.bridge import StubBridge
    from pymobile.core.driver import Driver

    app = App("Ergonomics", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
    app.run(screen())
    return app, Driver(app, screens=[screen])


def test_a_tappable_card_works_through_the_driver(tmp_path) -> None:
    app, driver = _driver(tmp_path, Home)
    try:
        driver.press("track")
        assert driver.screen.get("status", Label).text == "tapped"
    finally:
        app.stop()


def test_a_leaf_still_cannot_be_pressed(tmp_path) -> None:
    from pymobile import PyMobileError

    app, driver = _driver(tmp_path, OnlyText)
    try:
        with pytest.raises(PyMobileError, match="cannot be pressed"):
            driver.press("text")
    finally:
        app.stop()


def test_text_is_still_plain_text() -> None:
    """The strictness above must not change what a valid widget serialises to."""
    assert text_value(Label("hi").to_dict()["props"].get("text")) == "hi"
