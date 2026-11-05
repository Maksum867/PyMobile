"""Reusable UI patterns composed from supported native widgets.

Stateful patterns can be kept on a Screen (create them in __init__). They
restore their children's parent links after Screen.refresh() detaches a tree.
No new native type is needed for cards, forms, tabs, calendars or filter chips.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as _dt
import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from typing import Any

from ..formatting import format_date
from ..validation import Validator
from .components import Button, ButtonVariant, Chip, Label, TextInput
from .contract import WidgetNode
from .controls import Icon, IconButton, PageView
from .drawing import positive_int, theme_color
from .layout import Column, Expanded, Grid, Row, ScrollView, Wrap
from .style import Style
from .widget import Widget, in_build_scope

__all__ = [
    "Card",
    "FormField",
    "Form",
    "TabView",
    "Tabs",
    "ExpansionPanel",
    "Accordion",
    "EmptyState",
    "Skeleton",
    "MultiSelect",
    "Calendar",
    "DateRangePicker",
    "Carousel",
    "Banner",
    "Pagination",
    "TreeNode",
    "TreeView",
    "ColorPicker",
]


def _iso(value: str) -> _dt.date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("date must have the ISO YYYY-MM-DD shape")
    try:
        parsed = _dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a valid ISO date") from None
    if parsed.isoformat() != value:
        raise ValueError("date must have the ISO YYYY-MM-DD shape")
    return parsed


def _bind(name: str, function: Callable[..., object], *args: object) -> Callable[[], object]:
    """A zero-argument press handler with a readable name in the serialised tree."""

    def handler() -> object:
        return function(*args)

    handler.__name__ = name
    return handler


class _Pattern(Column):
    __slots__ = ()
    _gates_events = True

    def _batch(self) -> Any:
        screen = self.screen
        return screen.app.batch() if screen is not None and screen.mounted else nullcontext()

    def _replace(self, children: Sequence[Widget], *, notify: bool = True) -> None:
        # Validate before changing the old tree and invalidate just once.
        if any(not isinstance(child, Widget) for child in children):
            raise TypeError("pattern children must be widgets")
        if len({id(child) for child in children}) != len(children):
            raise ValueError("a widget cannot be used twice in a pattern")
        for child in children:
            if child is self or (child.parent is not None and child.parent is not self):
                raise ValueError("child already belongs to a different container")
            ancestor = self.parent
            while ancestor is not None:
                if child is ancestor:
                    raise ValueError("a pattern cannot contain one of its ancestors")
                ancestor = ancestor.parent
        for old in self._children:
            old._parent = None
        self._children = list(children)
        self._repair()
        if notify:
            self.invalidate()

    def _repair(self) -> None:
        for node in self.walk():
            for child in node.children:
                child._parent = node

    def to_dict(self) -> WidgetNode:
        self._repair()
        node = super().to_dict()
        if not self.enabled:
            stack = [node]
            while stack:
                child_node = stack.pop()
                child_node["enabled"] = False
                stack.extend(child_node.get("children", []))
        return node


class Card(_Pattern):
    """A themed surface with optional title and action widgets."""

    __slots__ = ("_theme_background",)

    def __init__(
        self,
        *children: Widget,
        title: str = "",
        actions: Sequence[Widget] = (),
        padding: int = 16,
        radius: int = 12,
        elevation: int = 2,
        style: Style | None = None,
        **kwargs: Any,
    ) -> None:
        for name, value in (("padding", padding), ("radius", radius), ("elevation", elevation)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        base = style or Style()
        self._theme_background = base.background is None
        resolved = base.merge(
            padding=base.padding if base.padding is not None else padding,
            corner_radius=base.corner_radius if base.corner_radius is not None else radius,
            elevation=base.elevation if base.elevation is not None else elevation,
        )
        contents: list[Widget] = list(children)
        if title:
            contents.insert(0, Label(title, style=Style(bold=True, font_size=18)))
        if actions:
            contents.append(Wrap(*actions, spacing=8, run_spacing=8))
        kwargs.setdefault("spacing", 12)
        super().__init__(*contents, style=resolved, **kwargs)

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        if self._theme_background and self.style.background is None:
            node.setdefault("style", {})["background"] = theme_color(self, "SURFACE")
        return node


class FormField(_Pattern):
    """A text field with label, hint, validation error and dirty/touched state.

    ``rules`` uses the existing Validator's rule syntax. A Form can also supply
    one Validator for cross-field rules. The input is available as ``field.input``.
    """

    __slots__ = (
        "name",
        "label",
        "hint",
        "required",
        "rules",
        "input",
        "on_change",
        "_original_change",
        "_form_change",
        "_error",
        "_dirty",
        "_touched",
        "_label_widget",
        "_hint_widget",
        "_error_widget",
        "_initial",
    )

    def __init__(
        self,
        name: str,
        label: str = "",
        value: str = "",
        *,
        hint: str = "",
        required: bool = False,
        rules: Sequence[Any] = (),
        control: TextInput | None = None,
        password: bool = False,
        placeholder: str = "",
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if name is None:
            raise TypeError(
                "FormField needs a field name as its first argument, e.g. "
                "FormField('email', label='Email'); label= is display text, not the form key."
            )
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a form field needs a non-empty name")
        if isinstance(rules, (str, bytes)):
            raise TypeError("rules must be a sequence, e.g. ['required', 'email']")
        super().__init__(spacing=4, **kwargs)
        self.name, self.label, self.hint, self.required = (
            name,
            str(label or name),
            str(hint),
            bool(required),
        )
        own_rules = list(rules)
        if required and "required" not in own_rules:
            own_rules.insert(0, "required")
        Validator({name: own_rules})  # Fail fast on misspelled rules.
        self.rules = tuple(own_rules)
        self.input = (
            control
            if control is not None
            else TextInput(value, password=password, placeholder=placeholder, id=f"{self.id}:input")
        )
        if not isinstance(self.input, TextInput):
            raise TypeError("control must be a TextInput or AutoComplete")
        if not self.input.accessibility_label:
            self.input.accessibility_label = self.label
        self._initial = self.input.value
        self._original_change = self.input.on_change
        self.input.on_change = self._changed
        self.on_change = on_change
        self._form_change: Callable[[str, str], None] | None = None
        self._error, self._dirty, self._touched = "", False, False
        self._label_widget = Label("", id=f"{self.id}:label")
        self._hint_widget = Label("", id=f"{self.id}:hint", style=Style(font_size=12))
        self._error_widget = Label("", id=f"{self.id}:error", style=Style(font_size=12))
        self._sync()
        self._replace(
            (self._label_widget, self.input, self._hint_widget, self._error_widget), notify=False
        )

    @property
    def value(self) -> str:
        return self.input.value

    @value.setter
    def value(self, value: str) -> None:
        self.input.value = value

    def set_value(self, value: str) -> None:
        self.value = value

    @property
    def error(self) -> str:
        return self._error

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def touched(self) -> bool:
        return self._touched

    def _sync(self) -> None:
        # Updating descriptions during serialization must not schedule new frames.
        self._label_widget._text = self.label + (" *" if self.required else "")
        self._hint_widget._text = self.hint
        self._hint_widget._visible = bool(self.hint) and not bool(self.error)
        self._error_widget._text = self.error
        self._error_widget._visible = bool(self.error)

    def set_error(self, error: str | None) -> None:
        text = "" if error is None else str(error)
        if text != self._error:
            self._error = text
            self._sync()
            self.invalidate()

    def _changed(self, value: str) -> None:
        self._dirty, self._touched = value != self._initial, True
        if self._form_change is not None:
            self._form_change(self.name, value)
        if self._original_change is not None:
            self._original_change(value)
        if self.on_change is not None:
            self.on_change(value)

    def validate(self) -> bool:
        """Mark the field touched, update :attr:`error`, and return valid/invalid.

        This returns a bool, not a list of messages. Read ``field.error`` for
        this field's message, or ``form.errors`` for a mapping of all invalid
        field names to their first message.
        """
        errors = Validator({self.name: self.rules}).validate({self.name: self.value})
        self._touched = True
        self.set_error(errors.get(self.name))
        return not errors

    def reset(self, value: str | None = None) -> None:
        text = self._initial if value is None else str(value)
        if self.input.max_length is not None:
            text = text[: self.input.max_length]
        self.input._value = text
        self.input._revision += 1
        self._dirty, self._touched, self._error = False, False, ""
        self._sync()
        self.invalidate()

    def to_dict(self) -> WidgetNode:
        self._sync()
        node = super().to_dict()
        for child in node.get("children", []):
            if child["id"] == self._error_widget.id:
                child.setdefault("style", {})["color"] = theme_color(self, "ERROR")
            elif child["id"] == self._hint_widget.id:
                child.setdefault("style", {})["color"] = theme_color(self, "TEXT_MUTED")
        return node


class Form(_Pattern):
    """Collect and validate named FormFields. ``submit()`` returns success."""

    __slots__ = (
        "fields",
        "validator",
        "validate_on",
        "on_submit",
        "_errors",
        "_external_errors",
    )

    def __init__(
        self,
        *fields: FormField,
        validator: Validator | None = None,
        messages: Mapping[str, Any] | None = None,
        validate_on: str = "submit",
        on_submit: Callable[[dict[str, str]], object] | None = None,
        **kwargs: Any,
    ) -> None:
        if any(not isinstance(field, FormField) for field in fields):
            raise TypeError("Form accepts FormField children")
        if len({field.name for field in fields}) != len(fields):
            raise ValueError("form field names must be unique")
        if validate_on not in ("submit", "change"):
            raise ValueError("validate_on must be 'submit' or 'change'")
        if validator is not None and not isinstance(validator, Validator):
            raise TypeError("validator must be a Validator")
        kwargs.setdefault("spacing", 12)
        super().__init__(*fields, **kwargs)
        self.fields = tuple(fields)
        if validator is not None and messages is not None:
            raise ValueError("pass messages to your own Validator, not to the Form")
        self.validator = validator or Validator(
            {field.name: list(field.rules) for field in fields}, messages=messages
        )
        self.validate_on, self.on_submit = validate_on, on_submit
        self._errors: dict[str, str] = {}
        self._external_errors: dict[str, str] = {}
        for field in fields:
            field._form_change = self._field_changed

    @property
    def values(self) -> dict[str, str]:
        return {field.name: field.value for field in self.fields}

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def set_errors(self, errors: Mapping[str, str]) -> None:
        """Show domain/server errors on the matching named fields.

        Replaces the current error mapping and clears messages for fields not
        present in ``errors``. Unknown names are rejected instead of silently
        losing an error because the domain model and the form drifted apart::

            try:
                service.save(values)
            except ValidationError as error:
                form.set_errors(error.errors)
        """
        if not isinstance(errors, Mapping):
            raise TypeError("errors must be a mapping of form field names to messages")
        if any(not isinstance(name, str) for name in errors):
            raise TypeError("form error keys must be strings")
        field_names = {field.name for field in self.fields}
        unknown = set(errors) - field_names
        if unknown:
            raise ValueError(f"errors refer to unknown form field(s): {', '.join(sorted(unknown))}")
        cleaned = {
            name: str(message)
            for name, message in errors.items()
            if message is not None and str(message).strip()
        }
        with self._batch():
            self._external_errors = cleaned
            self._errors = dict(cleaned)
            for field in self.fields:
                if field.name in cleaned:
                    field._touched = True
                field.set_error(cleaned.get(field.name))
            self.invalidate()

    def _field_changed(self, name: str, _value: str) -> None:
        had_external_error = name in self._external_errors
        self._external_errors.pop(name, None)
        if self.validate_on == "change" or self._errors or had_external_error:
            self.validate()

    def validate(self) -> bool:
        local_errors = self.validator.validate(self.values)
        errors = {**local_errors, **self._external_errors}
        with self._batch():
            self._errors = errors
            for field in self.fields:
                field._touched = True
                field.set_error(errors.get(field.name))
            self.invalidate()
        return not errors

    def submit(self) -> bool:
        if not self.enabled or not self.validate():
            return False
        if self.on_submit is not None and not in_build_scope():
            self.on_submit(self.values)
        return True

    def reset(self, values: Mapping[str, str] | None = None) -> None:
        if values is not None and set(values) - {field.name for field in self.fields}:
            raise ValueError("reset contains unknown field names")
        with self._batch():
            self._errors = {}
            self._external_errors = {}
            for field in self.fields:
                field.reset(None if values is None else values.get(field.name, field._initial))
            self.invalidate()

    def to_dict(self) -> WidgetNode:
        # Re-evaluate existing messages when the language changes, without
        # changing touched state or firing callbacks during rendering.
        if self._errors or self._external_errors:
            local_errors = self.validator.validate(self.values)
            self._errors = {**local_errors, **self._external_errors}
            for field in self.fields:
                field._error = self._errors.get(field.name, "")
        return super().to_dict()


class TabView(_Pattern):
    """Top tabs with stable keys, label overrides and widget/builder contents.

    Tabs are not nested Screens: the owning Screen renders one active widget.
    A builder creates fresh content; keep its data outside the builder.
    """

    __slots__ = ("_tabs", "_labels", "_value", "on_select")

    def __init__(
        self,
        tabs: Mapping[str, Widget | Callable[[], Widget]],
        *,
        labels: Mapping[str, str] | None = None,
        value: str | None = None,
        on_select: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(tabs, Mapping) or not tabs:
            raise ValueError("tabs must be a non-empty key/content mapping")
        if any(not isinstance(key, str) or not key.strip() for key in tabs):
            raise ValueError("tab keys must be non-empty strings")
        if any(not isinstance(page, Widget) and not callable(page) for page in tabs.values()):
            raise TypeError("tab contents must be widgets or builders")
        widget_pages = [page for page in tabs.values() if isinstance(page, Widget)]
        if len({id(page) for page in widget_pages}) != len(widget_pages):
            raise ValueError("one content widget cannot belong to two tabs")
        if any(page.parent is not None for page in widget_pages):
            raise ValueError("tab contents must be detached widgets")
        super().__init__(spacing=8, **kwargs)
        self._tabs = dict(tabs)
        self._labels = {key: key for key in tabs}
        if labels is not None:
            self._update_labels(labels)
        self._value = next(iter(tabs)) if value is None else value
        if self._value not in self._tabs:
            raise ValueError("selected tab is not a known key")
        self.on_select = on_select
        self._rebuild(notify=False)

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self.select(value)

    def _update_labels(self, labels: Mapping[str, str]) -> None:
        if set(labels) - set(self._tabs):
            raise ValueError("labels contains unknown tab keys")
        self._labels.update({key: str(label) for key, label in labels.items()})

    def set_labels(self, labels: Mapping[str, str]) -> None:
        self._update_labels(labels)
        self._rebuild()

    def _content(self, key: str) -> Widget:
        page = self._tabs[key]
        result = page() if callable(page) else page
        if not isinstance(result, Widget):
            raise TypeError("tab builder must return a Widget, not a Screen")
        return result

    def _rebuild(self, *, notify: bool = True, content: Widget | None = None) -> None:
        page = self._content(self.value) if content is None else content
        buttons = [
            Button(
                self._labels[key],
                id=f"{self.id}:tab:{key}",
                on_press=_bind("select_tab", self._ui_set_value, key),
                style=Style(
                    background=theme_color(self, "PRIMARY")
                    if key == self.value
                    else theme_color(self, "SURFACE"),
                    color="#FFFFFF" if key == self.value else theme_color(self, "TEXT"),
                ),
            )
            for key in self._tabs
        ]
        header = ScrollView(Row(*buttons, spacing=4), horizontal=True, id=f"{self.id}:tabs")
        self._replace((header, page), notify=notify)

    def select(self, key: str) -> None:
        if key not in self._tabs:
            raise ValueError(f"unknown tab key {key!r}")
        if key == self._value:
            return
        page = self._content(key)
        if page.parent is not None and page.parent is not self:
            raise ValueError("tab content already belongs to another container")
        self._value = key
        self._rebuild(content=page)
        if self.on_select is not None and not in_build_scope():
            self.on_select(key)

    def set_value(self, value: str) -> None:
        self.select(value)

    def _ui_set_value(self, value: str) -> None:
        if self.enabled:
            self.select(value)

    def to_dict(self) -> WidgetNode:
        # Recolor kept tab headers without recreating the content/state.
        header = self.children[0]
        for button, key in zip(header.children[0].children, self._tabs, strict=True):
            object.__setattr__(
                button,
                "style",
                Style(
                    background=theme_color(self, "PRIMARY")
                    if key == self.value
                    else theme_color(self, "SURFACE"),
                    color="#FFFFFF" if key == self.value else theme_color(self, "TEXT"),
                ),
            )
        return super().to_dict()


Tabs = TabView


class ExpansionPanel(_Pattern):
    """An expandable section with an optional native fade/scale transition."""

    __slots__ = ("title", "_expanded", "on_toggle", "_header", "_content")

    def __init__(
        self,
        title: str,
        *content: Widget,
        expanded: bool = False,
        animated: bool = True,
        animation_duration_ms: int = 220,
        on_toggle: Callable[[bool], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(spacing=8, **kwargs)
        self.title, self._expanded, self.on_toggle = str(title), bool(expanded), on_toggle
        if not isinstance(animated, bool):
            raise TypeError("animated must be a bool")
        self._header = Button("", on_press=self.toggle, id=f"{self.id}:header")
        self._content = Column(
            *content,
            spacing=8,
            id=f"{self.id}:content",
            visible=self._expanded,
            animate_visibility=animated,
            animation_duration_ms=animation_duration_ms,
        )
        self._sync()
        self._replace((self._header, self._content), notify=False)

    @property
    def expanded(self) -> bool:
        return self._expanded

    @expanded.setter
    def expanded(self, value: bool) -> None:
        self.set_expanded(value)

    def _sync(self) -> None:
        self._header._text = ("▾ " if self.expanded else "▸ ") + self.title
        self._content._visible = self.expanded

    def set_expanded(self, value: bool, *, notify: bool = True) -> None:
        if not isinstance(value, bool):
            raise TypeError("expanded must be a bool")
        if value != self._expanded:
            self._expanded = value
            self._sync()
            self.invalidate()
            if notify and self.on_toggle is not None and not in_build_scope():
                self.on_toggle(value)

    def toggle(self) -> None:
        if self.enabled:
            self.set_expanded(not self.expanded)

    def to_dict(self) -> WidgetNode:
        self._sync()
        return super().to_dict()


class Accordion(_Pattern):
    """A group of ExpansionPanels; by default only one may be open."""

    __slots__ = ("panels", "multiple", "on_change", "_changing")

    def __init__(
        self,
        *panels: ExpansionPanel,
        multiple: bool = False,
        on_change: Callable[[tuple[str, ...]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if any(not isinstance(panel, ExpansionPanel) for panel in panels):
            raise TypeError("Accordion accepts ExpansionPanel children")
        kwargs.setdefault("spacing", 8)
        super().__init__(*panels, **kwargs)
        self.panels, self.multiple, self.on_change = tuple(panels), bool(multiple), on_change
        self._changing = False
        seen = False
        for panel in panels:
            if panel.expanded and not multiple:
                if seen:
                    panel._expanded = False
                    panel._sync()
                seen = True
            original = panel.on_toggle
            panel.on_toggle = lambda open_, panel=panel, original=original: self._panel_changed(
                panel, open_, original
            )

    @property
    def expanded(self) -> tuple[str, ...]:
        return tuple(panel.id for panel in self.panels if panel.expanded)

    def _panel_changed(
        self, panel: ExpansionPanel, open_: bool, original: Callable[[bool], None] | None
    ) -> None:
        if self._changing:
            return
        self._changing = True
        try:
            with self._batch():
                if open_ and not self.multiple:
                    for other in self.panels:
                        if other is not panel:
                            other.set_expanded(False, notify=False)
                if original is not None:
                    original(open_)
                if self.on_change is not None and not in_build_scope():
                    self.on_change(self.expanded)
                self.invalidate()
        finally:
            self._changing = False


class EmptyState(_Pattern):
    """A reusable empty/error state with an optional action."""

    __slots__ = ()

    def __init__(
        self,
        title: str = "Nothing here yet",
        *,
        description: str = "",
        icon: str | None = "info",
        action_label: str = "",
        on_action: Callable[[], object] | None = None,
        **kwargs: Any,
    ) -> None:
        children: list[Widget] = []
        if icon:
            children.append(Icon(icon, size=40))
        children.append(Label(title, style=Style(bold=True, font_size=18)))
        if description:
            children.append(Label(description))
        if action_label:
            children.append(Button(action_label, on_press=on_action, enabled=on_action is not None))
        kwargs.setdefault("spacing", 8)
        kwargs.setdefault("cross_align", "center")
        super().__init__(*children, **kwargs)


class Skeleton(_Pattern):
    """Static loading placeholders, with no timers or per-frame bridge traffic."""

    __slots__ = ()

    def __init__(
        self, lines: int = 3, *, line_height: int = 16, width: int | None = None, **kwargs: Any
    ) -> None:
        positive_int(lines, "lines")
        positive_int(line_height, "line_height")
        if width is not None:
            positive_int(width, "width")
        children = []
        for index in range(lines):
            w = width if width is None or index < lines - 1 else max(1, width * 2 // 3)
            children.append(Label(" ", style=Style(width=w, height=line_height, corner_radius=4)))
        kwargs.setdefault("spacing", 8)
        super().__init__(*children, **kwargs)

    def to_dict(self) -> WidgetNode:
        node = super().to_dict()
        screen = self.screen
        dark = screen is not None and screen.mounted and screen.app.theme.is_dark
        for child in node.get("children", []):
            child.setdefault("style", {})["background"] = "#33FFFFFF" if dark else "#1F000000"
        return node


class MultiSelect(_Pattern):
    """Filter chips with stable keys and an optional selection limit."""

    __slots__ = ("_options", "_value", "maximum_selected", "on_change", "_chips")

    def __init__(
        self,
        options: Mapping[str, str] | Sequence[str],
        value: Sequence[str] = (),
        *,
        maximum_selected: int | None = None,
        on_change: Callable[[tuple[str, ...]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(options, (str, bytes)):
            raise TypeError("options must be a sequence or a key/label mapping")
        values = (
            dict(options)
            if isinstance(options, Mapping)
            else {str(key): str(key) for key in options}
        )
        if any(not isinstance(key, str) or not key for key in values):
            raise ValueError("option keys must be non-empty strings")
        if maximum_selected is not None:
            positive_int(maximum_selected, "maximum_selected")
        super().__init__(**kwargs)
        self._options = {key: str(label) for key, label in values.items()}
        self.maximum_selected, self.on_change = maximum_selected, on_change
        self._value = self._normalise(value)
        self._chips = {
            key: Chip(
                label,
                selected=key in self._value,
                id=f"{self.id}:option:{key}",
                on_press=_bind("toggle_option", self.toggle, key),
            )
            for key, label in self._options.items()
        }
        self._replace((Wrap(*self._chips.values(), spacing=8, run_spacing=8),), notify=False)

    def _normalise(self, value: Sequence[str]) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)) or any(not isinstance(key, str) for key in value):
            raise TypeError("selected values must be a sequence of string keys")
        selected = set(value)
        if selected - set(self._options):
            raise ValueError("selection contains unknown option keys")
        if self.maximum_selected is not None and len(selected) > self.maximum_selected:
            raise ValueError("selection exceeds maximum_selected")
        return tuple(key for key in self._options if key in selected)

    @property
    def value(self) -> tuple[str, ...]:
        return self._value

    @value.setter
    def value(self, value: Sequence[str]) -> None:
        self.set_value(value)

    def set_value(self, value: Sequence[str]) -> None:
        new = self._normalise(value)
        if new != self._value:
            self._value = new
            for key, chip in self._chips.items():
                chip._selected = key in new
            self.invalidate()
            if self.on_change is not None and not in_build_scope():
                self.on_change(new)

    def toggle(self, key: str) -> None:
        if not self.enabled:
            return
        if key not in self._options:
            raise ValueError("unknown option key")
        selected = set(self.value)
        if key in selected:
            selected.remove(key)
        else:
            if self.maximum_selected is not None and len(selected) >= self.maximum_selected:
                return
            selected.add(key)
        self.set_value(tuple(selected))

    def clear_selection(self) -> None:
        self.set_value(())

    def set_labels(self, labels: Mapping[str, str]) -> None:
        if set(labels) - set(self._options):
            raise ValueError("labels contains unknown option keys")
        with self._batch():
            for key, label in labels.items():
                self._options[key] = str(label)
                self._chips[key].text = str(label)
            self.invalidate()

    def _ui_set_value(self, value: str) -> None:
        if self.enabled:
            decoded = json.loads(value)
            if not isinstance(decoded, list):
                raise ValueError("multiselect UI values must be a JSON array")
            self.set_value(decoded)


class Calendar(_Pattern):
    """Inline month calendar with locale-aware names and selectable ISO dates.

    ``disabled_date`` receives a datetime.date. Weekday names, if supplied,
    must be Monday..Sunday; ``first_weekday`` rotates the displayed week.
    """

    __slots__ = (
        "_value",
        "_month",
        "minimum",
        "maximum",
        "first_weekday",
        "disabled_date",
        "on_change",
        "on_select",
        "weekday_names",
        "month_names",
        "_range",
    )

    def __init__(
        self,
        value: str = "",
        *,
        month: str | None = None,
        minimum: str | None = None,
        maximum: str | None = None,
        first_weekday: int = 0,
        disabled_date: Callable[[_dt.date], bool] | None = None,
        weekday_names: Sequence[str] | None = None,
        month_names: Sequence[str] | None = None,
        on_change: Callable[[str], None] | None = None,
        on_select: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if (
            isinstance(first_weekday, bool)
            or not isinstance(first_weekday, int)
            or not 0 <= first_weekday <= 6
        ):
            raise ValueError("first_weekday must be an int from 0 (Monday) to 6 (Sunday)")
        if weekday_names is not None and (
            isinstance(weekday_names, str) or len(weekday_names) != 7
        ):
            raise ValueError("weekday_names needs seven Monday..Sunday names")
        if month_names is not None and (isinstance(month_names, str) or len(month_names) != 12):
            raise ValueError("month_names needs twelve month names")
        self.minimum = _iso(minimum) if minimum else None
        self.maximum = _iso(maximum) if maximum else None
        if self.minimum and self.maximum and self.minimum > self.maximum:
            raise ValueError("minimum must not be after maximum")
        self.first_weekday, self.disabled_date, self.on_change = (
            first_weekday,
            disabled_date,
            on_change,
        )
        self.on_select = on_select
        self.weekday_names = tuple(weekday_names) if weekday_names is not None else None
        self.month_names = tuple(month_names) if month_names is not None else None
        self._value = ""
        self._range: tuple[str, str] | None = None
        if value:
            if self.is_disabled(_iso(value)):
                raise ValueError("initial date is disabled or out of bounds")
            self._value = value
        initial = _iso(value) if value else _dt.date.today()
        if self.minimum and initial < self.minimum:
            initial = self.minimum
        if self.maximum and initial > self.maximum:
            initial = self.maximum
        self._month = _iso(month + "-01") if month is not None else initial.replace(day=1)
        kwargs.setdefault("spacing", 8)
        super().__init__(**kwargs)
        self._rebuild(notify=False)

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self.set_value(value)

    @property
    def month(self) -> str:
        return self._month.isoformat()[:7]

    def is_disabled(self, date: _dt.date) -> bool:
        return bool(
            (self.minimum and date < self.minimum)
            or (self.maximum and date > self.maximum)
            or (self.disabled_date is not None and self.disabled_date(date))
        )

    def set_value(self, value: str) -> None:
        if value:
            parsed = _iso(value)
            if self.is_disabled(parsed):
                raise ValueError("date is disabled or out of bounds")
        if value != self._value:
            self._value = value
            if value:
                self._month = _iso(value).replace(day=1)
            self._rebuild()
            if self.on_change is not None and not in_build_scope():
                self.on_change(value)

    def _choose(self, value: str) -> None:
        if self.enabled:
            self.set_value(value)
            if self.on_select is not None and not in_build_scope():
                self.on_select(value)

    def show_month(self, year: int, month: int) -> None:
        if isinstance(year, bool) or isinstance(month, bool):
            raise TypeError("year and month must be ints")
        new = _dt.date(year, month, 1)
        if new != self._month:
            self._month = new
            self._rebuild()

    def _shift(self, delta: int) -> None:
        if not self.enabled:
            return
        index = (self._month.year - 1) * 12 + self._month.month - 1 + delta
        if 0 <= index < 9999 * 12:
            year, month = divmod(index, 12)
            self.show_month(year + 1, month + 1)

    def _rebuild(self, *, notify: bool = True) -> None:
        year, month = self._month.year, self._month.month
        title = (
            f"{self.month_names[month - 1]} {year}"
            if self.month_names
            else format_date(self._month, pattern="LLLL y")
        )
        prev = self._month > _dt.date(1, 1, 1) and (
            self.minimum is None or self._month > self.minimum.replace(day=1)
        )
        following = self._month < _dt.date(9999, 12, 1) and (
            self.maximum is None or self._month < self.maximum.replace(day=1)
        )
        header = Row(
            IconButton(
                "back",
                label="Previous month",
                on_press=lambda: self._shift(-1),
                enabled=prev,
                id=f"{self.id}:previous",
            ),
            Expanded(Label(title, style=Style(bold=True), id=f"{self.id}:month")),
            IconButton(
                "forward",
                label="Next month",
                on_press=lambda: self._shift(1),
                enabled=following,
                id=f"{self.id}:next",
            ),
        )
        monday = _dt.date(2026, 9, 28)
        names = self.weekday_names or tuple(
            format_date(monday + _dt.timedelta(days=i), pattern="EEEE")[:2] for i in range(7)
        )
        cells: list[Widget] = [
            Label(
                names[(self.first_weekday + i) % 7],
                style=Style(font_size=12, align="center"),
                id=f"{self.id}:weekday:{i}",
            )
            for i in range(7)
        ]
        weeks = _calendar.Calendar(self.first_weekday).monthdayscalendar(year, month)
        for row_index, week in enumerate(weeks):
            for column, day in enumerate(week):
                if not day:
                    cells.append(Label(" ", id=f"{self.id}:blank:{row_index}:{column}"))
                    continue
                date = _dt.date(year, month, day)
                iso = date.isoformat()
                selected = iso == self.value or (
                    self._range is not None and self._range[0] <= iso <= self._range[1]
                )
                cells.append(
                    Button(
                        str(day),
                        id=f"{self.id}:day:{iso}",
                        enabled=not self.is_disabled(date),
                        on_press=_bind("choose_date", self._choose, iso),
                        style=Style(
                            background=theme_color(self, "PRIMARY")
                            if selected
                            else theme_color(self, "SURFACE"),
                            color="#FFFFFF" if selected else theme_color(self, "TEXT"),
                            padding=2,
                            min_width=0,
                            min_height=48,
                            font_size=12,
                        ),
                    )
                )
        grid = Grid(*cells, columns=7, row_spacing=2, column_spacing=2, id=f"{self.id}:days")
        self._replace((header, grid), notify=notify)

    def to_dict(self) -> WidgetNode:
        # Fresh locale/theme descriptions, stable explicit ids and unchanged state.
        self._rebuild(notify=False)
        return super().to_dict()


class DateRangePicker(_Pattern):
    """Inline calendar: first tap starts a range, second tap completes it.

    ``value`` is ``(start, end)`` in ISO format; an incomplete range has an
    empty end. ``on_change`` fires for both start and completion. A new tap on
    a completed range starts another one. Reversed programmatic ranges fail.
    """

    __slots__ = ("calendar", "_value", "on_change", "_summary", "empty_text", "clear_label")

    def __init__(
        self,
        start: str = "",
        end: str = "",
        *,
        on_change: Callable[[tuple[str, str]], None] | None = None,
        empty_text: str = "Select a date range",
        clear_label: str = "Clear",
        minimum: str | None = None,
        maximum: str | None = None,
        disabled_date: Callable[[_dt.date], bool] | None = None,
        first_weekday: int = 0,
        month: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(spacing=8, **kwargs)
        self.on_change, self.empty_text, self.clear_label = (
            on_change,
            str(empty_text),
            str(clear_label),
        )
        self.calendar = Calendar(
            minimum=minimum,
            maximum=maximum,
            disabled_date=disabled_date,
            first_weekday=first_weekday,
            month=month,
            on_select=self._date_chosen,
            id=f"{self.id}:calendar",
        )
        self._value = self._normalise((start, end))
        self._summary = Label("", id=f"{self.id}:summary")
        self._sync()
        self._replace(
            (
                self._summary,
                self.calendar,
                Button(clear_label, on_press=self.clear_selection, id=f"{self.id}:clear"),
            ),
            notify=False,
        )

    def _normalise(self, value: Sequence[str]) -> tuple[str, str]:
        if isinstance(value, (str, bytes)) or len(value) != 2:
            raise ValueError("date range needs exactly start and end")
        start, end = value
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("range dates must be ISO strings")
        if end and not start:
            raise ValueError("an end date needs a start date")
        begin = _iso(start) if start else None
        finish = _iso(end) if end else None
        if begin is not None and self.calendar.is_disabled(begin):
            raise ValueError("start date is disabled or out of bounds")
        if finish is not None and self.calendar.is_disabled(finish):
            raise ValueError("end date is disabled or out of bounds")
        if begin and finish:
            if begin > finish:
                raise ValueError("start must not be after end")
            if self.calendar.disabled_date is not None:
                day = begin
                while day < finish:
                    if self.calendar.is_disabled(day):
                        raise ValueError("range includes a disabled date")
                    day += _dt.timedelta(days=1)
        return start, end

    @property
    def value(self) -> tuple[str, str]:
        return self._value

    @value.setter
    def value(self, value: Sequence[str]) -> None:
        self.set_value(value)

    @property
    def complete(self) -> bool:
        return bool(self._value[0] and self._value[1])

    def _sync(self) -> None:
        start, end = self.value
        self._summary._text = f"{start} — {end or '…'}" if start else self.empty_text
        self.calendar._value = end or start
        self.calendar._range = (start, end or start) if start else None
        if start:
            self.calendar._month = _iso(end or start).replace(day=1)
        self.calendar._rebuild(notify=False)

    def set_value(self, value: Sequence[str]) -> None:
        new = self._normalise(value)
        if new != self._value:
            self._value = new
            self._sync()
            self.invalidate()
            if self.on_change is not None and not in_build_scope():
                self.on_change(new)

    def select_date(self, value: str) -> None:
        if not self.enabled:
            return
        _iso(value)
        start, end = self.value
        new = (value, "") if not start or end else (min(start, value), max(start, value))
        self.set_value(new)

    def _date_chosen(self, value: str) -> None:
        try:
            self.select_date(value)
        except (TypeError, ValueError):
            self._sync()
            raise

    def clear_selection(self) -> None:
        self.set_value(("", ""))

    def to_dict(self) -> WidgetNode:
        self._summary._text = (
            f"{self.value[0]} — {self.value[1] or '…'}" if self.value[0] else self.empty_text
        )
        return super().to_dict()


class Carousel(_Pattern):
    """Swipeable PageView with buttons and a page indicator, no autoplay timer."""

    __slots__ = ("pager", "on_select", "_indicator", "_previous", "_next")

    def __init__(
        self,
        *pages: Widget,
        item_count: int | None = None,
        builder: Callable[[int], Widget] | None = None,
        value: int = 0,
        loop: bool = False,
        height: int = 180,
        on_select: Callable[[int], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(spacing=8, **kwargs)
        self.on_select = on_select
        self.pager = PageView(
            *pages,
            item_count=item_count,
            builder=builder,
            value=value,
            loop=loop,
            height=height,
            on_select=self._selected,
            id=f"{self.id}:pages",
        )
        self._indicator = Label("", id=f"{self.id}:indicator")
        self._previous = IconButton(
            "back", label="Previous page", on_press=self.previous, id=f"{self.id}:previous"
        )
        self._next = IconButton(
            "forward", label="Next page", on_press=self.next, id=f"{self.id}:next"
        )
        self._sync()
        self._replace(
            (
                self.pager,
                Row(self._previous, self._indicator, self._next, align="center", spacing=12),
            ),
            notify=False,
        )

    @property
    def value(self) -> int:
        return self.pager.value

    @value.setter
    def value(self, value: int) -> None:
        self.pager.select(value)

    def set_value(self, value: int) -> None:
        self.value = value

    def _sync(self) -> None:
        self._indicator._text = f"{self.value + 1} / {self.pager.item_count}"
        self._previous._enabled = self.pager.loop or self.value > 0
        self._next._enabled = self.pager.loop or self.value < self.pager.item_count - 1

    def _selected(self, value: int) -> None:
        self._sync()
        self.invalidate()
        if self.on_select is not None and not in_build_scope():
            self.on_select(value)

    def next(self) -> None:
        if self.enabled:
            self.pager.next()

    def previous(self) -> None:
        if self.enabled:
            self.pager.previous()

    def to_dict(self) -> WidgetNode:
        self._sync()
        return super().to_dict()


# --------------------------------------------------------------------------
# Banner, Pagination, TreeView, ColorPicker — built from existing widgets.
# --------------------------------------------------------------------------

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")

#: kind -> (background, text colour, icon name)
_BANNER_STYLES: dict[str, tuple[str, str, str]] = {
    "info": ("#E3F2FD", "#0D47A1", "info"),
    "success": ("#E8F5E9", "#1B5E20", "check"),
    "warning": ("#FFF3E0", "#8D4E00", "info"),
    "error": ("#FDECEA", "#B71C1C", "info"),
}


def _hex_color(value: object, name: str) -> str:
    """Return ``value`` as an upper-case ``#RRGGBB`` string or raise ValueError."""
    if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
        raise ValueError(f"{name} must be a '#RRGGBB' hex string, got {value!r}")
    return value.upper()


def _readable_text(background: str) -> str:
    """Black or white, whichever has the higher WCAG contrast on ``background``.

    The better of the two is always at least 4.5:1 (WCAG AA for normal text).
    """

    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(background[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    on_black = (luminance + 0.05) / 0.05
    on_white = 1.05 / (luminance + 0.05)
    return "#000000" if on_black >= on_white else "#FFFFFF"


class Banner(_Pattern):
    """A message that stays on the screen until the user acts on it or closes it.

    ``kind`` picks the colours and icon: ``info``, ``success``, ``warning`` or
    ``error``. ``action_label`` adds a text button; ``dismissible=True`` adds a
    close button that hides the banner.
    """

    __slots__ = ("message", "kind", "on_action", "on_dismiss")

    def __init__(
        self,
        message: str,
        *,
        kind: str = "info",
        action_label: str = "",
        on_action: Callable[[], object] | None = None,
        dismissible: bool = False,
        on_dismiss: Callable[[], object] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Banner message must be a non-empty string")
        if kind not in _BANNER_STYLES:
            raise ValueError(f"kind must be one of {', '.join(_BANNER_STYLES)}; got {kind!r}")
        background, foreground, icon = _BANNER_STYLES[kind]
        kwargs.setdefault("spacing", 0)
        super().__init__(**kwargs)
        self.message, self.kind = message, kind
        self.on_action, self.on_dismiss = on_action, on_dismiss
        parts: list[Widget] = [
            Icon(icon, size=20, color=foreground, label=kind, id=f"{self.id}:icon"),
            Expanded(Label(message, style=Style(color=foreground), id=f"{self.id}:text")),
        ]
        if action_label:
            parts.append(
                Button(
                    action_label,
                    variant=ButtonVariant.TEXT,
                    on_press=self._act,
                    enabled=on_action is not None,
                    style=Style(color=foreground),
                    id=f"{self.id}:action",
                )
            )
        if dismissible:
            parts.append(
                IconButton(
                    "close",
                    label="Dismiss",
                    color=foreground,
                    on_press=self.dismiss,
                    id=f"{self.id}:dismiss",
                )
            )
        row = Row(
            *parts,
            spacing=8,
            align="center",
            style=Style(background=background, corner_radius=8, padding=12),
            id=f"{self.id}:row",
        )
        self._replace((row,), notify=False)

    @property
    def dismissed(self) -> bool:
        """True while the banner is hidden, whether closed by the user or by code."""
        return not self.visible

    def _act(self) -> None:
        if self.enabled and self.on_action is not None and not in_build_scope():
            self.on_action()

    def show(self) -> None:
        """Show a dismissed banner again; it can be dismissed again afterwards."""
        self.visible = True

    def dismiss(self) -> None:
        """Hide the banner. Does nothing while it is already hidden."""
        if not self.visible:
            return
        self.visible = False
        if self.on_dismiss is not None and not in_build_scope():
            self.on_dismiss()


class Pagination(_Pattern):
    """Previous/next buttons and page numbers, with ``…`` for skipped pages.

    Pages are numbered from 1. ``on_change(page)`` runs whenever the current
    page changes, from a tap or from :meth:`select`.
    """

    __slots__ = ("total_pages", "current", "siblings", "on_change")

    def __init__(
        self,
        *,
        total_pages: int,
        current: int = 1,
        siblings: int = 1,
        on_change: Callable[[int], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(total_pages, bool) or not isinstance(total_pages, int) or total_pages < 1:
            raise ValueError("total_pages must be an integer of at least 1")
        if isinstance(siblings, bool) or not isinstance(siblings, int) or siblings < 0:
            raise ValueError("siblings must be a non-negative integer")
        if (
            isinstance(current, bool)
            or not isinstance(current, int)
            or not (1 <= current <= total_pages)
        ):
            raise ValueError(f"current must be between 1 and {total_pages}, got {current!r}")
        super().__init__(**kwargs)
        self.total_pages, self.current, self.siblings = total_pages, current, siblings
        self.on_change = on_change
        self._rebuild(notify=False)

    def _numbers(self) -> list[int | None]:
        """Page numbers to show; ``None`` marks a gap of two or more pages."""
        wanted = {1, self.total_pages}
        wanted.update(range(self.current - self.siblings, self.current + self.siblings + 1))
        pages = sorted(page for page in wanted if 1 <= page <= self.total_pages)
        items: list[int | None] = []
        last: int | None = None
        for page in pages:
            if last is not None:
                if page - last == 2:
                    items.append(last + 1)  # one hidden page: show it rather than "…"
                elif page - last > 2:
                    items.append(None)
            items.append(page)
            last = page
        return items

    def _rebuild(self, *, notify: bool = True) -> None:
        controls: list[Widget] = [
            IconButton(
                "back",
                label="Previous page",
                enabled=self.current > 1,
                on_press=self.previous,
                id=f"{self.id}:previous",
            )
        ]
        for item in self._numbers():
            if item is None:
                controls.append(Label("…"))
                continue
            current = item == self.current
            controls.append(
                Button(
                    str(item),
                    variant=ButtonVariant.PRIMARY if current else ButtonVariant.TEXT,
                    accessibility_label=f"Page {item}" + (", current" if current else ""),
                    on_press=_bind(f"page {item}", self.select, item),
                    id=f"{self.id}:page-{item}",
                )
            )
        controls.append(
            IconButton(
                "forward",
                label="Next page",
                enabled=self.current < self.total_pages,
                on_press=self.next,
                id=f"{self.id}:next",
            )
        )
        self._replace(
            (Wrap(*controls, spacing=4, run_spacing=4, align="center", id=f"{self.id}:pages"),),
            notify=notify,
        )

    def select(self, page: int) -> None:
        """Show ``page``. Raises ValueError when it is out of range."""
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not (1 <= page <= self.total_pages)
        ):
            raise ValueError(f"page must be between 1 and {self.total_pages}, got {page!r}")
        if page == self.current:
            return
        self.current = page
        self._rebuild()
        if self.on_change is not None and not in_build_scope():
            self.on_change(page)

    def next(self) -> None:
        if self.enabled and self.current < self.total_pages:
            self.select(self.current + 1)

    def previous(self) -> None:
        if self.enabled and self.current > 1:
            self.select(self.current - 1)


class TreeNode:
    """One entry of a :class:`TreeView`: a label, optional children, open state."""

    __slots__ = ("label", "children", "expanded")

    def __init__(self, label: str, *children: TreeNode, expanded: bool = False) -> None:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("TreeNode label must be a non-empty string")
        if any(not isinstance(child, TreeNode) for child in children):
            raise TypeError("TreeNode children must be TreeNode objects")
        self.label = label
        self.children = children
        self.expanded = bool(expanded)

    def __repr__(self) -> str:
        return f"TreeNode({self.label!r}, children={len(self.children)}, expanded={self.expanded})"


class TreeView(_Pattern):
    """A nested list where nodes with children open and close.

    ``on_select(node)`` runs when the user taps a node without children. Tapping
    a node with children only opens or closes it. ``node.expanded`` tracks the
    current state.
    """

    __slots__ = ("nodes", "on_select")

    def __init__(
        self,
        *nodes: TreeNode,
        on_select: Callable[[TreeNode], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if any(not isinstance(node, TreeNode) for node in nodes):
            raise TypeError("TreeView accepts TreeNode objects")
        kwargs.setdefault("spacing", 4)
        super().__init__(**kwargs)
        self.nodes, self.on_select = tuple(nodes), on_select
        self._replace([self._row(node) for node in nodes], notify=False)

    def _row(self, node: TreeNode) -> Widget:
        if not node.children:
            return Button(
                f"• {node.label}",
                variant=ButtonVariant.TEXT,
                on_press=_bind(f"open {node.label}", self._open, node),
            )
        children = Column(
            *(self._row(child) for child in node.children),
            spacing=4,
            style=Style(margin=(16, 0, 0, 0)),
        )

        def remember(open_: bool, node: TreeNode = node) -> None:
            node.expanded = open_

        return ExpansionPanel(node.label, children, expanded=node.expanded, on_toggle=remember)

    def _open(self, node: TreeNode) -> None:
        if self.enabled and self.on_select is not None and not in_build_scope():
            self.on_select(node)


class ColorPicker(_Pattern):
    """A grid of colour swatches; the chosen one shows a tick.

    ``colors`` are ``#RRGGBB`` strings. ``value`` is the initially chosen colour
    (or none). ``on_change(color)`` runs when the user picks a colour, or when
    :meth:`select` changes it. Colours are returned in upper case.
    """

    __slots__ = ("colors", "on_change", "_value", "_swatch_size")

    def __init__(
        self,
        colors: Sequence[str],
        *,
        value: str | None = None,
        swatch_size: int = 48,
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(colors, str) or not colors:
            raise ValueError("ColorPicker needs a sequence of at least one colour")
        names = tuple(_hex_color(color, "color") for color in colors)
        if len(set(names)) != len(names):
            raise ValueError("ColorPicker colours must be unique")
        if isinstance(swatch_size, bool) or not isinstance(swatch_size, int) or swatch_size < 48:
            raise ValueError("swatch_size must be an integer of at least 48 dp (touch target)")
        chosen = None if value is None else _hex_color(value, "value")
        if chosen is not None and chosen not in names:
            raise ValueError(f"value {value!r} is not one of the picker's colours")
        super().__init__(**kwargs)
        self.colors, self.on_change, self._swatch_size = names, on_change, swatch_size
        self._value = chosen
        self._rebuild(notify=False)

    @property
    def value(self) -> str | None:
        """The chosen colour in upper case, or ``None``."""
        return self._value

    def _rebuild(self, *, notify: bool = True) -> None:
        swatches: list[Widget] = []
        for color in self.colors:
            selected = color == self._value
            label = f"Color {color}" + (", selected" if selected else "")
            swatches.append(
                Button(
                    "✓" if selected else "",
                    variant=ButtonVariant.TEXT,
                    accessibility_label=label,
                    on_press=_bind(f"pick {color}", self.select, color),
                    style=Style(
                        background=color,
                        color=_readable_text(color),
                        width=self._swatch_size,
                        height=self._swatch_size,
                        corner_radius=self._swatch_size // 2,
                    ),
                    id=f"{self.id}:{color[1:]}",
                )
            )
        self._replace(
            (Wrap(*swatches, spacing=8, run_spacing=8, id=f"{self.id}:swatches"),),
            notify=notify,
        )

    def select(self, color: str) -> None:
        """Choose ``color``. It must be one of the picker's colours."""
        name = _hex_color(color, "color")
        if name not in self.colors:
            raise ValueError(f"{color!r} is not one of this picker's colours")
        if name == self._value:
            return
        self._value = name
        self._rebuild()
        if self.on_change is not None and not in_build_scope():
            self.on_change(name)
