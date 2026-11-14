"""A child that is not a widget fails with the mistake the developer made.

``Column(Label("a"), "text")`` used to raise ``'str' object has no attribute
'_parent'`` three frames later — a message about an attribute nobody wrote.
Each test quotes the sentence the user now gets, because the wording is the
whole point of the fix.
"""

from __future__ import annotations

import pytest

from pymobile.core.ui import Column, Label


def test_a_string_child_names_the_widget_to_use() -> None:
    with pytest.raises(TypeError) as caught:
        Column(Label("a"), "just a string")
    message = str(caught.value)
    assert "Column cannot contain str" in message
    assert "Column(Label('just a string'))" in message


def test_a_function_used_as_a_factory_is_explained() -> None:
    def make_card() -> Label:
        return Label("card")

    with pytest.raises(TypeError) as caught:
        Column(make_card)
    message = str(caught.value)
    assert "Column(make_card())" in message
    assert "Button(on_press=make_card)" in message


def test_a_called_handler_child_mentions_the_parentheses() -> None:
    calls: list[str] = []
    with pytest.raises(TypeError) as caught:
        Column(Label("a"), calls.append("tap"))
    assert "on_press=self.tap()" in str(caught.value)


def test_nested_lists_explain_the_one_level_rule() -> None:
    with pytest.raises(TypeError) as caught:
        Column([[Label("a"), Label("b")]])
    message = str(caught.value)
    assert "Column children must be Widget instances" in message
    assert "flattened only one level deep" in message
