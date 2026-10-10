"""Static accessibility and narrow-screen checks for a widget tree.

The audit deliberately does not attempt to emulate Android's full layout
engine or font metrics. It reports clear mistakes and conservative estimates
from the declared widget tree; always verify important screens on a device.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .widget import Widget

__all__ = ["UiIssue", "audit_ui"]


@dataclass(frozen=True, slots=True)
class UiIssue:
    """One actionable advisory produced by :func:`audit_ui`."""

    code: str
    widget_id: str
    message: str
    hint: str
    severity: str = "warning"

    def __str__(self) -> str:
        return f"{self.severity}: {self.widget_id} [{self.code}] {self.message}"


_INTERACTIVE = frozenset(
    {
        "Button",
        "IconButton",
        "FloatingActionButton",
        "TextInput",
        "AutoComplete",
        "Switch",
        "Checkbox",
        "Slider",
        "RangeSlider",
        "RatingBar",
        "Dropdown",
        "SearchBar",
        "Stepper",
        "DatePicker",
        "TimePicker",
        "RadioButton",
        "SegmentedButtons",
        "Link",
        "Chip",
        "ListTile",
    }
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _insets(style: Mapping[str, Any], key: str) -> tuple[float, float, float, float]:
    value = style.get(key)
    if isinstance(value, (list, tuple)) and len(value) == 4:
        numbers = tuple(_number(side) for side in value)
        if all(side is not None for side in numbers):
            return (
                float(numbers[0] or 0),
                float(numbers[1] or 0),
                float(numbers[2] or 0),
                float(numbers[3] or 0),
            )
    return (0.0, 0.0, 0.0, 0.0)


def _text_width(text: object, font_size: object, padding: float = 0.0) -> float:
    size = _number(font_size) or 14.0
    # Average glyph width is a rough lower-cost estimate, not a font measurement.
    return len(str(text)) * size * 0.56 + padding


def _has_name(kind: str, props: Mapping[str, Any]) -> bool:
    explicit = props.get("accessibility_label")
    if isinstance(explicit, str) and explicit.strip():
        return True
    labels = {
        "Button": "text",
        "Chip": "text",
        "IconButton": "label",
        "FloatingActionButton": "label",
        "Link": "text",
        "RadioButton": "text",
        "RangeSlider": "label",
        "ListTile": "title",
    }
    key = labels.get(kind)
    value = props.get(key, "") if key else ""
    return isinstance(value, str) and bool(value.strip())


def _has_weight(node: Mapping[str, Any]) -> bool:
    style = node.get("style")
    return isinstance(style, Mapping) and bool(_number(style.get("weight")))


def _natural_width(node: Mapping[str, Any], available: float) -> float | None:
    """Estimate a child's natural width in dp for a Row overflow warning."""
    kind = str(node.get("type", ""))
    props = _mapping(node.get("props"))
    style = _mapping(node.get("style"))
    width = style.get("width")
    fixed = _number(width)
    if fixed is not None:
        estimate = fixed
    elif isinstance(width, str) and width.lower() in {"fill", "match", "match_parent"}:
        estimate = available
    elif kind in {"Expanded", "Flexible"} or _number(style.get("weight")):
        return 0.0
    elif kind in {"Icon", "IconButton", "FloatingActionButton"}:
        estimate = _number(props.get("size")) or (24.0 if kind == "Icon" else 48.0)
    elif kind == "Button":
        estimate = max(64.0, _text_width(props.get("text", ""), style.get("font_size"), 32.0))
    elif kind == "Chip":
        estimate = max(48.0, _text_width(props.get("text", ""), style.get("font_size"), 28.0))
    elif kind in {"Label", "Link"}:
        estimate = _text_width(props.get("text", ""), style.get("font_size"))
    elif kind in {"TextInput", "AutoComplete", "SearchBar"}:
        text = props.get("value") or props.get("placeholder", "")
        estimate = max(128.0, _text_width(text, style.get("font_size"), 24.0))
    elif kind == "Dropdown":
        estimate = max(112.0, _text_width(props.get("value", ""), style.get("font_size"), 32.0))
    elif kind in {"Switch", "Checkbox", "RadioButton"}:
        estimate = 48.0 if kind != "RadioButton" else max(
            80.0, _text_width(props.get("text", ""), style.get("font_size"), 48.0)
        )
    elif kind in {"Slider", "RangeSlider"}:
        estimate = 160.0
    elif kind == "Stepper":
        estimate = 112.0
    elif kind == "ListTile":
        estimate = max(160.0, _text_width(props.get("title", ""), style.get("font_size"), 40.0))
    elif kind in {"Column", "Container", "SafeArea", "ScrollView", "Grid", "Stack"}:
        children = node.get("children", ())
        if not isinstance(children, (list, tuple)):
            return None
        estimates = [
            _natural_width(child, available)
            for child in children
            if isinstance(child, Mapping) and child.get("visible", True)
        ]
        known = [value for value in estimates if value is not None]
        estimate = max(known, default=0.0)
    else:
        return None

    min_width = _number(style.get("min_width"))
    max_width = _number(style.get("max_width"))
    if min_width is not None:
        estimate = max(estimate, min_width)
    if max_width is not None:
        estimate = min(estimate, max_width)
    padding = _insets(style, "padding")
    margin = _insets(style, "margin")
    return estimate + padding[0] + padding[2] + margin[0] + margin[2]


def audit_ui(
    widget_or_tree: Widget | Mapping[str, Any],
    *,
    width: int = 320,
    min_touch_target: int = 48,
) -> list[UiIssue]:
    """Check a live widget or serialized tree for common UI issues.

    ``width`` is the narrow screen width in dp to use for responsive checks.
    The audit warns about interactive controls with an explicitly constrained
    width or height below ``min_touch_target``, unnamed controls/images, fixed
    widths wider than their parent, and estimated ``Row`` content wider than the
    available space. Unspecified native control sizes are not guessed for the
    touch-target check.
    """
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("width must be a positive integer number of dp")
    if (
        isinstance(min_touch_target, bool)
        or not isinstance(min_touch_target, int)
        or min_touch_target <= 0
    ):
        raise ValueError("min_touch_target must be a positive integer number of dp")
    if isinstance(widget_or_tree, Widget):
        root: Mapping[str, Any] = widget_or_tree.to_dict()
    elif isinstance(widget_or_tree, Mapping):
        root = widget_or_tree
    else:
        raise TypeError("audit_ui expects a Widget or a serialized widget tree")
    if not isinstance(root.get("type"), str):
        raise ValueError("widget tree must contain a string 'type'")

    issues: list[UiIssue] = []
    seen: set[tuple[str, str, str]] = set()

    def report(code: str, node: Mapping[str, Any], message: str, hint: str) -> None:
        widget_id = str(node.get("id") or node.get("type") or "widget")
        identity = (code, widget_id, message)
        if identity not in seen:
            seen.add(identity)
            issues.append(UiIssue(code, widget_id, message, hint))

    def visit(node: Mapping[str, Any], available: float) -> None:
        if not node.get("visible", True):
            return
        kind = str(node.get("type", ""))
        props = _mapping(node.get("props"))
        style = _mapping(node.get("style"))
        margin = _insets(style, "margin")
        padding = _insets(style, "padding")
        limit = max(0.0, available - margin[0] - margin[2])
        fixed_width = _number(style.get("width"))
        min_width = _number(style.get("min_width"))
        if fixed_width is not None and fixed_width > limit:
            report(
                "narrow-overflow",
                node,
                f"fixed width {fixed_width:g} dp exceeds the available {limit:g} dp",
                "Use a flexible width, reduce the fixed width, or place this content "
                "in a horizontal ScrollView.",
            )
        elif min_width is not None and min_width > limit:
            report(
                "narrow-overflow",
                node,
                f"min_width {min_width:g} dp exceeds the available {limit:g} dp",
                "Reduce min_width or let the widget wrap/fill its parent.",
            )

        if kind in _INTERACTIVE and not _has_name(kind, props):
            # A ListTile with no action is text, not a control that needs a name.
            tile_is_action = any(
                props.get(name)
                for name in ("on_press", "on_long_press", "on_swipe_left", "on_swipe_right")
            )
            if kind != "ListTile" or tile_is_action:
                report(
                    "accessibility-label",
                    node,
                    f"{kind} has no accessible name",
                    "Pass accessibility_label='…' (FormField supplies its label to its TextInput).",
                )
        if kind == "Image" and not props.get("decorative") and not _has_name(kind, props):
            report(
                "accessibility-label",
                node,
                "Image has no accessibility description",
                "Pass accessibility_label='…', or mark a purely decorative image "
                "with decorative=True.",
            )

        target_dimensions = [
            _number(style.get("height")),
            _number(style.get("max_height")),
            _number(style.get("width")),
            _number(style.get("max_width")),
        ]
        if kind in {"IconButton", "FloatingActionButton"}:
            target_dimensions.append(_number(props.get("size")))
        constrained_dimensions = [value for value in target_dimensions if value is not None]
        if kind in _INTERACTIVE and constrained_dimensions:
            smallest = min(constrained_dimensions)
            if smallest < min_touch_target:
                report(
                    "touch-target",
                    node,
                    f"explicit size constraint {smallest:g} dp is below "
                    f"the {min_touch_target} dp touch-target guideline",
                    "Remove the small width/height constraint or raise it to at least "
                    "the configured target size.",
                )

        children = node.get("children", ())
        if not isinstance(children, (list, tuple)):
            return
        visible = [
            child
            for child in children
            if isinstance(child, Mapping) and child.get("visible", True)
        ]
        own_width = fixed_width if fixed_width is not None else limit
        content_width = max(0.0, own_width - padding[0] - padding[2])

        if kind == "Row":
            spacing = _number(props.get("spacing")) or 0.0
            widths = [
                _natural_width(child, content_width)
                for child in visible
                if child.get("type") not in {"Expanded", "Flexible"}
                and not _has_weight(child)
            ]
            if all(value is not None for value in widths):
                total = sum(value or 0.0 for value in widths) + spacing * max(0, len(visible) - 1)
                if total > content_width + 0.5:
                    report(
                        "narrow-overflow",
                        node,
                        f"Row needs about {total:.0f} dp but only "
                        f"{content_width:.0f} dp is available",
                        "Use Expanded/Flexible, reduce child widths or spacing, or use "
                        "Wrap for controls that may move to another line.",
                    )
            remaining = content_width
            for child in visible:
                child_limit = (
                    remaining
                    if child.get("type") not in {"Expanded", "Flexible"}
                    else content_width
                )
                visit(child, child_limit)
                child_width = _natural_width(child, remaining)
                if child_width is not None and child.get("type") not in {"Expanded", "Flexible"}:
                    remaining = max(0.0, remaining - child_width - spacing)
        elif kind == "Grid":
            columns = max(1, int(_number(props.get("columns")) or 2))
            gap = _number(props.get("column_spacing"))
            if gap is None:
                gap = _number(props.get("spacing")) or 0.0
            cell_width = max(0.0, (content_width - gap * (columns - 1)) / columns)
            for child in visible:
                visit(child, cell_width)
        elif kind == "ScrollView" and props.get("horizontal"):
            # Horizontal scrolling is an explicit overflow strategy.
            for child in visible:
                visit(child, max(content_width, float(width)))
        else:
            for child in visible:
                visit(child, content_width)

    visit(root, float(width))
    return issues
