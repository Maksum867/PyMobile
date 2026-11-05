"""Regression coverage for sequence children in supported layout containers."""

import pytest

from pymobile import Column, Grid, Label, Row, Wrap
from pymobile.errors import WidgetParentError


@pytest.fixture(params=[Column, Row, Grid, Wrap], ids=lambda cls: cls.__name__)
def layout_type(request):
    return request.param


def test_mixed_tuples_and_lists_serialize_in_order(layout_type):
    first = Label("first", id="first")
    second = Label("second", id="second")
    third = Label("third", id="third")
    fourth = Label("fourth", id="fourth")
    layout = layout_type((first, second), third, [fourth], spacing=7)

    assert layout.children == (first, second, third, fourth)
    assert all(child.parent is layout for child in layout.children)
    tree = layout.to_dict()
    assert [child["id"] for child in tree["children"]] == ["first", "second", "third", "fourth"]
    assert tree["props"]["spacing"] == 7


@pytest.mark.parametrize("kind", ["none", "number", "nested"])
def test_invalid_sequence_does_not_attach_any_children(layout_type, kind):
    before = Label("before")
    in_sequence = Label("inside")
    invalid = {"none": None, "number": 42, "nested": [Label("nested")]}[kind]
    with pytest.raises(TypeError, match="children must be Widget"):
        layout_type(before, [in_sequence, invalid])
    assert before.parent is None
    assert in_sequence.parent is None


def test_sequence_rejects_child_owned_by_another_container(layout_type):
    child = Label("already owned")
    original = Column(child)
    with pytest.raises(WidgetParentError):
        layout_type([child])
    assert child.parent is original
    assert original.children == (child,)


def test_sequence_child_can_be_detached_and_reused(layout_type):
    child = Label("reusable", id="reusable")
    original = layout_type([child])
    child.detach()
    replacement = layout_type((child,))
    assert original.children == ()
    assert replacement.children == (child,)
    assert child.parent is replacement
    assert replacement.to_dict()["children"][0]["id"] == "reusable"
