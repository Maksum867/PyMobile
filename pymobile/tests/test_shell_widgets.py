"""Tests for the 0.7.0 shell widgets: bottom tabs, dialogs, pickers."""
from __future__ import annotations

import pytest

from pymobile import (
    AlertDialog,
    App,
    BottomNavigation,
    BottomSheet,
    Column,
    ConfirmDialog,
    DatePicker,
    TimePicker,
)
from pymobile.core.bridge import StubBridge
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.web import render_html


def test_bottom_navigation_select_fires_once() -> None:
    seen: list[str] = []
    nav = BottomNavigation(["Home", "Stats"], on_select=seen.append)
    assert nav.value == "Home"
    nav.select("Stats")
    nav.select("Stats")
    assert seen == ["Stats"]
    with pytest.raises(ValueError):
        nav.select("Nope")
    with pytest.raises(ValueError):
        BottomNavigation([])


def test_bottom_navigation_change_event_routes_through_app() -> None:
    from pymobile import Column, Screen

    app = App("t", bridge=StubBridge(verbose=False))
    seen: list[str] = []
    nav = BottomNavigation(["A", "B"], on_select=seen.append)

    class Host(Screen):
        def build(self) -> Column:
            return Column(nav)

    app.run(Host())  # events are routed through the visible screen
    app.handle_ui_event(nav.id, "change", "B")
    assert seen == ["B"]
    assert nav.value == "B"


def test_confirm_dialog_closes_before_callback() -> None:
    order: list[tuple[str, bool]] = []
    dlg = ConfirmDialog("Sure?", "msg", on_confirm=lambda: order.append(("cb", dlg.shown)))
    dlg.open()
    assert dlg.shown is True
    row = dlg.children[1]          # Row(cancel, confirm)
    row.children[1].press()        # the confirm button
    assert order == [("cb", False)]  # the handler saw an already-closed dialog


def test_alert_dialog_message_property() -> None:
    dlg = AlertDialog("Hi", "one")
    dlg.message = "two"
    assert dlg.children[0].text == "two"
    acked: list[bool] = []
    dlg.on_acknowledge = lambda: acked.append(dlg.shown)
    dlg.children[1].press()
    assert acked == [False]


def test_bottom_sheet_is_a_sheet_dialog() -> None:
    sheet = BottomSheet(title="Actions")
    assert sheet.sheet is True
    assert sheet.props()["sheet"] is True


def test_date_picker_clamps_and_validates() -> None:
    seen: list[str] = []
    picker = DatePicker(
        "2026-06-01", minimum="2026-01-01", maximum="2026-12-31", on_change=seen.append
    )
    picker.set_value("2027-01-01")
    assert picker.value == "2026-12-31"
    with pytest.raises(ValueError):
        picker.set_value("19.09.2026")
    assert seen == ["2026-12-31"]
    with pytest.raises(ValueError):
        DatePicker("2026-01-01", minimum="2026-02-01", maximum="2026-01-02")


def test_time_picker_normalises() -> None:
    picker = TimePicker("7:05")
    assert picker.value == "07:05"
    with pytest.raises(ValueError):
        picker.set_value("24:00")


def test_renderers_cover_the_new_widgets() -> None:
    dialog = ConfirmDialog("Sure?", "msg")
    dialog.open()  # dialogs are hidden by default (audit B17)
    tree = Column(
        BottomNavigation(["Home", "Stats"], value="Home"),
        dialog,
        DatePicker("2026-09-19"),
        TimePicker("14:30"),
    )
    picture = render_ascii(tree)
    assert "[Home]" in picture
    assert "Sure?" in picture
    assert "[📅 2026-09-19]" in picture
    assert "[🕒 14:30]" in picture

    html = render_html(tree.to_dict())
    assert "<nav" in html
    assert "<section" in html
    assert 'type="date"' in html
    assert 'type="time"' in html


def test_browser_url_points_at_loopback_for_wildcard_binds() -> None:
    from pymobile.core.ui.web import browser_url

    assert browser_url("0.0.0.0", 8765) == "http://127.0.0.1:8765"
    assert browser_url("::", 8765) == "http://127.0.0.1:8765"
    assert browser_url("127.0.0.1", 9000) == "http://127.0.0.1:9000"
    assert browser_url("192.168.0.7", 9000) == "http://192.168.0.7:9000"

def test_pickers_are_silent_while_building() -> None:
    """A value set in build() must not fire on_change — Slider/Switch do not either."""
    from pymobile import Screen, Widget

    seen: list[str] = []

    class Form(Screen):
        def build(self) -> Widget:
            self.date = DatePicker("2026-06-01", on_change=seen.append)
            self.time = TimePicker("09:00", on_change=seen.append)
            self.date.value = "2026-07-01"       # inside build(): no callback
            self.time.value = "10:30"
            return Column(self.date, self.time)

    Form().root  # builds the tree, i.e. runs build()
    assert seen == []

    picker = DatePicker("2026-06-01", on_change=seen.append)
    picker.value = "2026-08-01"              # outside build(): the listener runs
    assert seen == ["2026-08-01"]


def test_tab_bar_is_silent_while_building() -> None:
    """Picking the initial tab in build() must not call on_select."""
    from pymobile import Screen, Widget

    seen: list[str] = []

    class Shell(Screen):
        def build(self) -> Widget:
            self.tabs = BottomNavigation(["Home", "Stats"], on_select=seen.append)
            self.tabs.value = "Stats"            # inside build(): no callback
            return Column(self.tabs)

    Shell().root
    assert seen == []

    tabs = BottomNavigation(["Home", "Stats"], on_select=seen.append)
    tabs.select("Stats")                     # outside build(): the listener runs
    assert seen == ["Stats"]


def test_radio_group_is_silent_while_building() -> None:
    from pymobile import RadioButton, RadioGroup, Screen, Widget

    seen: list[str] = []

    class Form(Screen):
        def build(self) -> Widget:
            self.group = RadioGroup(
                RadioButton("A"), RadioButton("B"), value="A", on_select=seen.append
            )
            self.group.select("B")               # inside build(): no callback
            return Column(self.group)

    Form().root
    assert seen == []

    group = RadioGroup(RadioButton("A"), RadioButton("B"), value="A", on_select=seen.append)
    group.select("B")                        # outside build(): the listener runs
    assert seen == ["B"]
