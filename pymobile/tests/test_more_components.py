"""Tests for the second batch of UI components: RadioButton, RadioGroup,
SegmentedButtons, ProgressText, Link, DataTable, Avatar."""

from __future__ import annotations

import pytest

from pymobile import (
    Avatar,
    DataTable,
    Link,
    ProgressText,
    RadioButton,
    RadioGroup,
    SegmentedButtons,
)
from pymobile.core.ui.preview import render_ascii


# --------------------------------------------------------------------------
# RadioButton / RadioGroup
# --------------------------------------------------------------------------
def test_radio_button_state():
    r = RadioButton("A", selected=True)
    assert r.selected
    r.set_selected(False)
    assert not r.selected


def test_radio_group_selects_first_selected():
    group = RadioGroup(RadioButton("A"), RadioButton("B", selected=True))
    assert group.value == "B"
    assert group.children[1].selected
    assert not group.children[0].selected


def test_radio_group_select_switches():
    group = RadioGroup(RadioButton("A"), RadioButton("B"))
    group.select("B")
    assert group.value == "B"
    assert group.children[1].selected
    assert not group.children[0].selected


def test_radio_group_unknown_option():
    group = RadioGroup(RadioButton("A"))
    with pytest.raises(ValueError):
        group.select("Z")


def test_radio_group_rejects_non_radio_child():
    with pytest.raises(ValueError):
        RadioGroup(1)


def test_radio_group_on_select():
    events = []
    group = RadioGroup(RadioButton("A"), RadioButton("B"), on_select=lambda v: events.append(v))
    group.select("B")
    assert events == ["B"]


def test_radio_button_press_selects_in_group():
    group = RadioGroup(RadioButton("A"), RadioButton("B"))
    group.children[1].press()
    assert group.value == "B"
    assert group.children[1].selected
    assert not group.children[0].selected


def test_radio_group_value_in_ctor_does_not_fire_callback():
    """A constructor must not report setup as a user edit."""
    events = []
    group = RadioGroup(
        RadioButton("A"), RadioButton("B"), value="B", on_select=lambda v: events.append(v)
    )
    assert group.value == "B"
    assert events == []


# --------------------------------------------------------------------------
# SegmentedButtons
# --------------------------------------------------------------------------
def test_segmented_basic():
    seg = SegmentedButtons(["All", "Done", "Paused"], value="Done")
    assert seg.value == "Done"
    assert seg.options == ["All", "Done", "Paused"]


def test_segmented_select():
    seg = SegmentedButtons(["All", "Done"])
    seg.select("Done")
    assert seg.value == "Done"


def test_segmented_unknown():
    seg = SegmentedButtons(["A", "B"])
    with pytest.raises(ValueError):
        seg.select("C")


def test_segmented_empty():
    with pytest.raises(ValueError):
        SegmentedButtons([])


# --------------------------------------------------------------------------
# ProgressText
# --------------------------------------------------------------------------
def test_progress_text_text():
    pt = ProgressText(42, maximum=100)
    assert pt.percent == 42
    assert pt.text == "42%"
    assert pt.fraction == pytest.approx(0.42)


def test_progress_text_with_label():
    pt = ProgressText(42, maximum=100, label="Downloading")
    assert pt.text == "Downloading 42%"


def test_progress_text_custom_format():
    pt = ProgressText(50, maximum=200, format="{value}/{maximum}")
    assert pt.text == "50/200"


def test_progress_text_clamps():
    pt = ProgressText(150, maximum=100)
    assert pt.value == 100
    assert pt.percent == 100


def test_progress_text_invalid_max():
    with pytest.raises(ValueError):
        ProgressText(maximum=0)


# --------------------------------------------------------------------------
# Link
# --------------------------------------------------------------------------
def test_link_fields():
    link = Link("Visit", url="https://example.com")
    assert link.text == "Visit"
    assert link.url == "https://example.com"


def test_link_press():
    fired = []
    link = Link("go", on_press=lambda: fired.append(1))
    link.press()
    assert fired == [1]


def test_link_press_disabled():
    fired = []
    link = Link("go", on_press=lambda: fired.append(1), enabled=False)
    link.press()
    assert fired == []


# --------------------------------------------------------------------------
# DataTable
# --------------------------------------------------------------------------
def test_data_table_basic():
    table = DataTable(["Name", "Age"], [["Ann", 30], ["Bob", 25]])
    assert table.headers == ["Name", "Age"]
    assert len(table.rows) == 2


def test_data_table_add_row_pads():
    table = DataTable(["A", "B", "C"])
    table.add_row(["x"])
    assert table.rows[0] == ["x", "", ""]


def test_data_table_add_row_too_many():
    table = DataTable(["A", "B"])
    with pytest.raises(ValueError):
        table.add_row(["1", "2", "3"])


def test_data_table_empty_headers():
    with pytest.raises(ValueError):
        DataTable([])


def test_data_table_values_stringified():
    table = DataTable(["n", "m", "k"], [[1, 2.5, True]])
    assert table.rows[0] == ["1", "2.5", "True"]


# --------------------------------------------------------------------------
# Avatar
# --------------------------------------------------------------------------
def test_avatar_text():
    av = Avatar(text="OK")
    assert av.text == "OK"
    assert av.size == 48


def test_avatar_positional_initials():
    av = Avatar("MK")
    assert av.text == "MK"
    assert av.source == ""


def test_avatar_image_keyword():
    av = Avatar("MK", image="assets/photo.png")
    assert av.text == "MK"
    assert av.source == "assets/photo.png"


def test_avatar_requires_source_or_text():
    with pytest.raises(ValueError):
        Avatar()


def test_avatar_invalid_size():
    with pytest.raises(ValueError):
        Avatar(text="x", size=0)


def test_avatar_invalid_color():
    with pytest.raises(ValueError):
        Avatar(text="x", background="notacolor")


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------
def test_radio_group_preview():
    text = render_ascii(RadioGroup(RadioButton("A"), RadioButton("B", selected=True)).to_dict())
    assert "◉ B" in text and "○ A" in text


def test_segmented_preview_marks_selected():
    text = render_ascii(SegmentedButtons(["All", "Done"], value="Done").to_dict())
    assert "|Done|" in text


def test_progress_text_preview():
    text = render_ascii(ProgressText(42, label="Downloading").to_dict())
    assert "Downloading 42%" in text


def test_data_table_preview():
    text = render_ascii(DataTable(["Name", "Age"], [["Ann", 30]]).to_dict())
    assert "Ann" in text and "Age" in text


def test_avatar_preview():
    assert "[OK]" in render_ascii(Avatar(text="OK").to_dict())
