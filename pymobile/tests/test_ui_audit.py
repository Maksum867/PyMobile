"""Common accessibility and narrow-screen findings."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from pymobile import (
    Button,
    Column,
    Form,
    FormField,
    IconButton,
    Image,
    Row,
    Style,
    Switch,
    TextInput,
    audit_ui,
)
from pymobile.core.ui.web import render_html


def test_reports_small_controls_missing_names_and_row_overflow() -> None:
    tree = Column(
        Button("Save", style=Style(height=32), id="small-save"),
        Button("Tiny", style=Style(width=32), id="small-width"),
        TextInput(id="search"),
        Row(
            Button("One", style=Style(width=220), id="one"),
            Button("Two", style=Style(width=220), id="two"),
            id="actions",
        ),
    )

    issues = audit_ui(tree, width=320)
    found = {(issue.code, issue.widget_id) for issue in issues}
    assert ("touch-target", "small-save") in found
    assert ("touch-target", "small-width") in found
    assert ("accessibility-label", "search") in found
    assert ("narrow-overflow", "actions") in found
    assert all(issue.severity == "warning" and issue.hint for issue in issues)


def test_formfield_supplies_an_accessible_name_and_custom_controls_can_be_named() -> None:
    field = FormField("plant_name", label="Plant name", id="plant-field")
    assert field.input.accessibility_label == "Plant name"
    assert field.input.to_dict()["props"]["accessibility_label"] == "Plant name"

    tree = Column(
        Form(field),
        Switch(accessibility_label="Watering reminder", id="reminder"),
        TextInput(accessibility_label="Search plants", id="named-search"),
    )
    assert not [issue for issue in audit_ui(tree) if issue.code == "accessibility-label"]


def test_images_need_a_description_unless_marked_decorative() -> None:
    tree = {
        "type": "Column",
        "id": "root",
        "props": {},
        "children": [
            {
                "type": "Image",
                "id": "photo",
                "props": {"source": "photo.png"},
            },
            {
                "type": "Image",
                "id": "decoration",
                "props": {"source": "flourish.png", "decorative": True},
            },
            {
                "type": "Image",
                "id": "logo",
                "props": {"source": "logo.png", "accessibility_label": "Plant house logo"},
            },
        ],
    }
    issues = audit_ui(tree)
    assert [(issue.code, issue.widget_id) for issue in issues] == [
        ("accessibility-label", "photo")
    ]


def test_web_preview_emits_aria_label_and_image_alt_text() -> None:
    text_input = TextInput(accessibility_label="Plant name", id="plant-name")
    assert 'aria-label="Plant name"' in render_html(text_input.to_dict())
    icon = IconButton("add", label="Add", accessibility_label="Add a plant", id="add")
    assert 'aria-label="Add a plant"' in render_html(icon.to_dict())

    image = Image("logo.png", accessibility_label="PyMobile logo", id="logo")
    assert 'alt="PyMobile logo"' in render_html(image.to_dict())
    decorative = Image("leaf.png", decorative=True, id="leaf")
    assert 'aria-hidden="true"' in render_html(decorative.to_dict())


@pytest.mark.parametrize(
    "optional_fields",
    [
        pytest.param({}, id="absent"),
        pytest.param({"props": None, "style": None}, id="null"),
        pytest.param({"props": [], "style": "invalid"}, id="non-mapping"),
        pytest.param(
            {"props": MappingProxyType({}), "style": MappingProxyType({})},
            id="read-only-mapping",
        ),
    ],
)
def test_audit_normalizes_optional_props_and_style(
    optional_fields: dict[str, object],
) -> None:
    tree = {
        "type": "Row",
        "id": "root",
        **optional_fields,
        "children": [{"type": "Button", "id": "control", **optional_fields}],
    }

    issues = audit_ui(tree)
    assert [(issue.code, issue.widget_id) for issue in issues] == [
        ("accessibility-label", "control")
    ]


def test_audit_preserves_read_only_mapping_values() -> None:
    tree = {
        "type": "Row",
        "id": "actions",
        "props": MappingProxyType({"spacing": 4}),
        "children": [
            {
                "type": "Button",
                "id": "save",
                "props": MappingProxyType({"text": "Save"}),
                "style": MappingProxyType({"width": 200}),
            },
            {
                "type": "Button",
                "id": "cancel",
                "props": MappingProxyType({"text": "Cancel"}),
                "style": MappingProxyType({"width": 120}),
            },
        ],
    }

    issues = audit_ui(tree, width=320)
    assert ("narrow-overflow", "actions") in {
        (issue.code, issue.widget_id) for issue in issues
    }
    assert not [issue for issue in issues if issue.code == "accessibility-label"]


def test_audit_rejects_invalid_dimensions_and_widget_inputs() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        audit_ui(Column(), width=0)
    with pytest.raises(TypeError, match="Widget or a serialized"):
        audit_ui(object())  # type: ignore[arg-type]
