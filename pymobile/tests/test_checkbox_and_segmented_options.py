"""Checkbox labels and the option shapes SegmentedButtons accepts.

An unlabelled checkbox is an accessibility problem, a labelled one is not; and
segmented buttons read the same option shapes as Dropdown and RadioButtons.
"""

from __future__ import annotations

import pytest

from pymobile import Checkbox, SegmentedButtons
from pymobile.core.ui.audit import audit_ui
from pymobile.core.ui.preview import render_ascii


class TestCheckbox:
    def test_check_ui_accepts_a_labelled_checkbox(self) -> None:
        box = Checkbox(False, text="Remember me")
        assert audit_ui(box, width=320) == []

    def test_check_ui_still_flags_a_bare_checkbox(self) -> None:
        codes = [issue.code for issue in audit_ui(Checkbox(False), width=320)]
        assert "accessibility-label" in codes

    def test_the_text_preview_shows_the_label(self) -> None:
        assert render_ascii(Checkbox(False, text="Remember me").to_dict()) == "[ ] Remember me"
        assert render_ascii(Checkbox(True, text="Done").to_dict()) == "[✓] Done"

    def test_an_unlabelled_box_keeps_the_state_word(self) -> None:
        assert render_ascii(Checkbox(False).to_dict()) == "[ ] off"
        assert render_ascii(Checkbox(True).to_dict()) == "[✓] on"


class TestSegmentedOptions:
    def test_a_mapping_keeps_its_labels(self) -> None:
        buttons = SegmentedButtons({"all": "All", "mine": "Mine"})
        assert buttons.options == ["all", "mine"]
        assert buttons._labels == ["All", "Mine"]

    def test_a_list_of_single_item_mappings_works_too(self) -> None:
        buttons = SegmentedButtons([{"all": "All"}, {"mine": "Mine"}])
        assert buttons._labels == ["All", "Mine"]

    def test_pairs_and_plain_strings_still_work(self) -> None:
        assert SegmentedButtons(["a", ("b", "B")])._labels == ["a", "B"]

    def test_a_two_key_mapping_is_refused(self) -> None:
        with pytest.raises(ValueError, match="pairs of strings"):
            SegmentedButtons([{"a": "A", "b": "B"}])
