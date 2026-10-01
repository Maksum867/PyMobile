"""Declared renderer capabilities and parity checks for built-in widgets.

The registry is intentionally conservative: a renderer is listed only when it
has a dedicated implementation, not a generic ``<Widget>`` fallback. This lets
CI expose parity gaps without pretending that previews support native features.

It is also the answer to the quietest failure mode in the framework: a widget
with a ``type_name`` no renderer knows about. The desktop and browser previews
print ``<BarChart>`` and look fine, while ``ViewBuilder.java`` falls back to an
empty view — the widget is simply missing on the phone, with no error anywhere.
Two guards use this module: :func:`unknown_types` checks a serialised tree, and
:class:`~pymobile.core.app.App` calls it on every frame sent to a native
renderer. Declare a type you implemented in Java with
:func:`register_widget_type` so the check stops reporting it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "WidgetCapability",
    "WIDGET_CAPABILITIES",
    "widget_types",
    "supported_by",
    "register_widget_type",
    "unregister_widget_type",
    "declared_types",
    "known_types",
    "is_known",
    "unknown_types",
]

#: Renderers a capability can be declared for.
RENDERERS = ("android", "web", "gui", "ascii")


@dataclass(frozen=True, slots=True)
class WidgetCapability:
    """Support declaration for one serialised widget type."""

    type_name: str
    android: bool = True
    web: bool = False
    gui: bool = False
    ascii: bool = True


# Every type produced by a built-in widget must have one row here. Web/GUI are
# deliberately explicit: an unsupported node renders a visible fallback rather
# than silently claiming native parity.
WIDGET_CAPABILITIES: tuple[WidgetCapability, ...] = (
    WidgetCapability("Label", web=True, gui=True),
    WidgetCapability("Button", web=True, gui=True),
    WidgetCapability("TextInput", web=True, gui=True),
    WidgetCapability("Image", web=True, gui=True),
    WidgetCapability("Switch", web=True, gui=True),
    WidgetCapability("ProgressBar", web=True, gui=True),
    WidgetCapability("Spacer", web=True, gui=True),
    WidgetCapability("Column", web=True, gui=True),
    WidgetCapability("Row", web=True, gui=True),
    WidgetCapability("ScrollView", web=True, gui=True),
    WidgetCapability("Stack", web=True, gui=True),
    WidgetCapability("Grid", web=True, gui=True),
    WidgetCapability("Wrap", web=True, gui=True),
    WidgetCapability("Expanded", web=True, gui=True),
    WidgetCapability("Flexible", web=True, gui=True),
    WidgetCapability("Divider", web=True, gui=True),
    WidgetCapability("SafeArea", web=True, gui=True),
    WidgetCapability("Container", android=False, web=True, gui=True),
    WidgetCapability("Slider", web=True, gui=True),
    WidgetCapability("Checkbox", web=True, gui=True),
    WidgetCapability("RatingBar", web=True, gui=True),
    WidgetCapability("Dropdown", web=True, gui=True),
    WidgetCapability("Chip", web=True, gui=True),
    WidgetCapability("Badge", web=True, gui=True),
    WidgetCapability("Stepper", web=True, gui=True),
    WidgetCapability("SearchBar", web=True, gui=True),
    WidgetCapability("RadioButton", web=True, gui=True),
    WidgetCapability("RadioGroup", web=True, gui=True),
    WidgetCapability("SegmentedButtons", web=True, gui=True),
    WidgetCapability("ProgressText", web=True, gui=True),
    WidgetCapability("Link", web=True, gui=True),
    WidgetCapability("DataTable", web=True, gui=True),
    WidgetCapability("Avatar", web=True, gui=True),
    WidgetCapability("List", web=True, gui=True),
    WidgetCapability("ListTile", web=True, gui=True),
    WidgetCapability("BottomNavigation", web=True, gui=True),
    WidgetCapability("Dialog", web=True, gui=True),
    WidgetCapability("DatePicker", web=True, gui=True),
    WidgetCapability("TimePicker", web=True, gui=True),
    WidgetCapability("Icon", web=True, gui=True),
    WidgetCapability("IconButton", web=True, gui=True),
    WidgetCapability("AutoComplete", web=True, gui=True),
    WidgetCapability("RangeSlider", web=True, gui=True),
    WidgetCapability("PageView", web=True, gui=True),
    WidgetCapability("Chart", web=True, gui=True),
)

#: Types an application declared as handled by renderer code of its own.
#: Populated through :func:`register_widget_type`, normally at import time.
_declared: dict[str, WidgetCapability] = {}


def widget_types() -> frozenset[str]:
    """All built-in serialised widget names."""
    return frozenset(capability.type_name for capability in WIDGET_CAPABILITIES)


def declared_types() -> frozenset[str]:
    """Custom types declared with :func:`register_widget_type`."""
    return frozenset(_declared)


def known_types() -> frozenset[str]:
    """Every type the framework has a declaration for: built-in and custom."""
    return widget_types() | declared_types()


def register_widget_type(
    type_name: str,
    *,
    android: bool = True,
    web: bool = False,
    gui: bool = False,
    ascii: bool = True,
) -> WidgetCapability:
    """Declare a custom ``type_name`` as handled by the renderers you built.

    Call this once your Java branch (or web/GUI renderer) exists, so the
    "no renderer for this widget type" warning stops firing::

        class BarChart(Widget):
            type_name = "BarChart"

        register_widget_type("BarChart")           # case "BarChart": in ViewBuilder.java
        register_widget_type("Sparkline", android=False, web=True)   # preview-only

    Returns the stored :class:`WidgetCapability`. Built-in names are refused —
    their capabilities ship with the framework — and re-declaring a custom name
    simply replaces the previous declaration.
    """
    if not isinstance(type_name, str) or not type_name.strip():
        raise ValueError("type_name must be a non-empty string")
    if type_name in widget_types():
        raise ValueError(f"{type_name!r} is a built-in widget type")
    capability = WidgetCapability(
        type_name, android=android, web=web, gui=gui, ascii=ascii
    )
    _declared[type_name] = capability
    return capability


def unregister_widget_type(type_name: str) -> bool:
    """Forget a declaration; returns whether there was one (tests, plugins)."""
    return _declared.pop(type_name, None) is not None


def supported_by(renderer: str) -> frozenset[str]:
    """Return types with dedicated support in ``android``, ``web``, ``gui`` or ``ascii``.

    Declared custom types count for the renderers they were declared for.
    """
    if renderer not in RENDERERS:
        raise ValueError(f"unknown renderer: {renderer!r}")
    capabilities = (*WIDGET_CAPABILITIES, *_declared.values())
    return frozenset(
        capability.type_name
        for capability in capabilities
        if bool(getattr(capability, renderer))
    )


def is_known(type_name: str, *, renderer: str | None = None) -> bool:
    """Whether ``type_name`` has a declaration — overall, or for one renderer."""
    if renderer is None:
        return type_name in known_types()
    return type_name in supported_by(renderer)


def unknown_types(render_tree: Mapping[str, Any], *, renderer: str | None = None) -> frozenset[str]:
    """Type names in a serialised tree that no renderer claims to draw.

    ``unknown_types(screen.to_dict())`` is what a test needs to catch a
    component the phone would silently drop::

        assert unknown_types(screen.to_dict()) == frozenset()

    Pass ``renderer="android"`` to ask about one renderer specifically — a
    preview-only widget is unknown there and known for ``web``. The walk is
    iterative and ignores anything that is not a mapping, so a partially
    malformed tree still yields the offending names instead of raising.
    """
    names: set[str] = set()
    stack: list[object] = [render_tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, Mapping):
            continue
        name = node.get("type")
        if isinstance(name, str) and not is_known(name, renderer=renderer):
            names.add(name)
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return frozenset(names)
