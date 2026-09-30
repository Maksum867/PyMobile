"""Duplicate spellings of one argument: a canonical name, and a deprecated alias.

``max=`` / ``maximum=``, ``min=`` / ``minimum=``, ``maxlength=`` / ``max_length=``
and ``on_change=`` / ``on_select=`` (on the widgets that pick one option) used to
be permanent twins. The canonical name is the one the widget stores and
serialises; the other keeps working, warns, and is removed in 1.0.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

import pytest

import pymobile
from pymobile import (
    BottomNavigation,
    Dropdown,
    ProgressBar,
    ProgressText,
    PyMobileDeprecationWarning,
    RatingBar,
    SegmentedButtons,
    Slider,
    Stepper,
    TextInput,
    Widget,
)
from pymobile.deprecation import (
    ALIASES_DEPRECATED_IN,
    ALIASES_REMOVED_IN,
    warn_deprecated,
)

Factory = Callable[..., Widget]


# (widget factory, deprecated keyword, canonical keyword, value, prop that carries it)
ALIASES: list[tuple[Factory, str, str, Any, str]] = [
    (ProgressBar, "max", "maximum", 50, "maximum"),
    (Slider, "max", "maximum", 50, "maximum"),
    (Slider, "min", "minimum", 5, "minimum"),
    (RatingBar, "max", "maximum", 7, "maximum"),
    (Stepper, "max", "maximum", 50, "maximum"),
    (Stepper, "min", "minimum", 5, "minimum"),
    (ProgressText, "max", "maximum", 50, "maximum"),
    (TextInput, "maxlength", "max_length", 12, "max_length"),
]


@pytest.mark.parametrize(("factory", "old", "new", "value", "prop"), ALIASES)
def test_the_deprecated_keyword_still_works_and_says_what_to_use(
    factory: Factory, old: str, new: str, value: Any, prop: str
) -> None:
    with pytest.warns(PyMobileDeprecationWarning) as record:
        widget = factory(**{old: value})
    assert widget.props()[prop] == value
    assert len(record) == 1
    message = str(record[0].message)
    assert message.startswith(f"{old}= is deprecated")
    assert f"use {new}= instead" in message
    assert ALIASES_DEPRECATED_IN in message
    assert ALIASES_REMOVED_IN in message


@pytest.mark.parametrize(("factory", "old", "new", "value", "prop"), ALIASES)
def test_the_canonical_keyword_is_silent_and_equivalent(
    factory: Factory, old: str, new: str, value: Any, prop: str
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        widget = factory(**{new: value})
    assert widget.props()[prop] == value


@pytest.mark.parametrize(("factory", "old", "new", "value", "prop"), ALIASES)
def test_the_warning_points_at_the_calling_code(
    factory: Factory, old: str, new: str, value: Any, prop: str
) -> None:
    with pytest.warns(PyMobileDeprecationWarning) as record:
        factory(**{old: value})
    assert record[0].filename == __file__


@pytest.mark.parametrize(("factory", "old", "new", "value", "prop"), ALIASES)
def test_giving_both_spellings_is_still_an_error(
    factory: Factory, old: str, new: str, value: Any, prop: str
) -> None:
    with pytest.raises(ValueError, match="either"):
        factory(**{old: value, new: value})


SELECTION_WIDGETS: list[Callable[..., Widget]] = [
    lambda **kw: Dropdown(["a", "b"], **kw),
    lambda **kw: SegmentedButtons(["a", "b"], **kw),
    lambda **kw: BottomNavigation(["a", "b"], **kw),
]


@pytest.mark.parametrize("factory", SELECTION_WIDGETS)
def test_on_change_of_a_selection_widget_is_a_deprecated_on_select(factory: Factory) -> None:
    seen: list[str] = []
    with pytest.warns(PyMobileDeprecationWarning, match=r"on_change= is deprecated.*on_select="):
        widget = factory(on_change=seen.append)
    widget.set_value("b")  # type: ignore[attr-defined]
    assert seen == ["b"]
    assert widget.on_select is not None  # type: ignore[attr-defined]


@pytest.mark.parametrize("factory", SELECTION_WIDGETS)
def test_on_select_is_the_silent_canonical_name(factory: Factory) -> None:
    seen: list[str] = []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        widget = factory(on_select=seen.append)
    widget.set_value("b")  # type: ignore[attr-defined]
    assert seen == ["b"]


@pytest.mark.parametrize("factory", SELECTION_WIDGETS[:2])
def test_giving_both_selection_callbacks_is_an_error(factory: Factory) -> None:
    with pytest.raises(ValueError, match="either on_select or on_change"):
        factory(on_select=print, on_change=print)


def test_value_widgets_keep_on_change_as_their_canonical_name() -> None:
    """``on_change`` is not being retired: TextInput/Slider/Stepper report a value."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        TextInput(on_change=print)
        Slider(on_change=print)
        Stepper(on_change=print)
        RatingBar(on_change=print)


def test_the_category_is_a_deprecation_warning_and_public() -> None:
    assert issubclass(PyMobileDeprecationWarning, DeprecationWarning)
    assert pymobile.PyMobileDeprecationWarning is PyMobileDeprecationWarning
    assert "PyMobileDeprecationWarning" in pymobile.__all__


def test_one_filter_turns_every_pymobile_deprecation_into_an_error() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", PyMobileDeprecationWarning)
        with pytest.raises(PyMobileDeprecationWarning):
            Slider(max=10)


class TestWarnDeprecated:
    def test_message_names_the_replacement_and_both_releases(self) -> None:
        with pytest.warns(PyMobileDeprecationWarning) as record:
            warn_deprecated("old()", "new()", since="0.9.0", removal="1.0.0")
        assert str(record[0].message) == (
            "old() is deprecated since PyMobile 0.9.0 and will be removed in 1.0.0; "
            "use new() instead."
        )

    def test_the_replacement_is_optional(self) -> None:
        with pytest.warns(PyMobileDeprecationWarning) as record:
            warn_deprecated("gone()", since="0.9.0", removal="1.0.0")
        assert str(record[0].message).endswith("removed in 1.0.0.")

    def test_stacklevel_counts_from_the_caller(self) -> None:
        def helper() -> None:
            warn_deprecated("x", since="0.9.0", removal="1.0.0", stacklevel=2)

        with pytest.warns(PyMobileDeprecationWarning) as record:
            helper()  # <- the line stacklevel=2 must blame
        assert record[0].filename == __file__

    def test_the_removal_release_is_later_than_the_deprecating_one(self) -> None:
        def key(version: str) -> tuple[int, ...]:
            return tuple(int(part) for part in version.split("."))

        assert key(ALIASES_REMOVED_IN) > key(ALIASES_DEPRECATED_IN)
