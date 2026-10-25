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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "WidgetCapability",
    "WIDGET_CAPABILITIES",
    "widget_types",
    "supported_by",
    "register_widget_type",
    "unregister_widget_type",
    "register_preview_renderer",
    "unregister_preview_renderer",
    "preview_renderer",
    "preview_renderers",
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
    # ViewBuilder.java now has a case for it (a vertical layout with spacing,
    # C-2); before that an explicit Container was a placeholder on the phone.
    WidgetCapability("Container", web=True, gui=True),
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
    # Declarative vector drawing (Canvas + Line/Path/Shape); it serialises into
    # the same primitives Chart does, so every renderer already knew how to
    # draw it once the type had a branch of its own.
    WidgetCapability("Canvas", web=True, gui=True),
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

    This function only *declares* capabilities. To teach a desktop preview how
    to draw the widget, register a function as well — otherwise the ASCII,
    browser and Tk previews fall back to ``<BarChart>``::

        register_preview_renderer("BarChart", "ascii", draw_ascii)
        register_preview_renderer("BarChart", "web", draw_html)

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


# ---------------------------------------------------------------------------
# Preview renderers for custom widget types
# ---------------------------------------------------------------------------
#: Renderers a preview function can be registered for.
PREVIEW_RENDERERS = ("ascii", "web", "gui")

#: ``(type_name, renderer)`` → drawing function. Kept apart from the capability
#: declarations: declaring says "a real renderer exists somewhere"; registering
#: a preview function says "here is how the desktop draws it".
_preview: dict[tuple[str, str], Callable[..., Any]] = {}


def register_preview_renderer(
    type_name: str,
    renderer: str,
    draw: Callable[..., Any],
) -> Callable[..., Any]:
    """Teach a desktop preview how to draw the custom widget ``type_name``.

    Without this, a custom widget — one the phone draws with your Java renderer
    — appears in the ASCII, browser and Tk previews as the placeholder
    ``<BoxBadge>``. Registering removes the branching from application code::

        from pymobile import register_widget_type, register_preview_renderer

        register_widget_type("BoxBadge")

        def badge_ascii(node):
            return [f"[{node['props'].get('text', '')}]"]

        def badge_html(node):
            text = escape(str(node["props"].get("text", "")))
            return f'<span class="badge">{text}</span>'

        def badge_tk(preview, parent, node):
            import tkinter as tk
            tk.Label(parent, text=node["props"].get("text", "")).pack()

        register_preview_renderer("BoxBadge", "ascii", badge_ascii)
        register_preview_renderer("BoxBadge", "web", badge_html)
        register_preview_renderer("BoxBadge", "gui", badge_tk)

    ``draw`` receives the serialised node (the same mapping
    :meth:`Widget.to_dict` produces: ``type``, ``id``, ``props``, ``style``,
    ``children``) and must return:

    * ``"ascii"`` — the lines of the text picture (a string is split on
      newlines). ``render_ascii(child)`` renders children of a container.
    * ``"web"`` — HTML markup; ``render_html(child)`` renders children.
    * ``"gui"`` — nothing. It is called as ``draw(preview, parent, node,
      background)`` where ``preview`` is the
      :class:`~pymobile.core.ui.gui.GuiPreview`, ``parent`` the Tk container to
      fill and ``background`` the inherited colour. ``preview.tk`` is the
      tkinter module and ``preview.dispatch(widget_id, kind, value)`` sends an
      event, exactly like a built-in control does.

    Registering for a built-in type is allowed — that is how a project can give
    ``ProgressBar`` its own look in a preview — but it only affects previews;
    the phone keeps drawing its native view.
    """
    if not isinstance(type_name, str) or not type_name.strip():
        raise ValueError("type_name must be a non-empty string")
    if renderer not in PREVIEW_RENDERERS:
        raise ValueError(
            f"unknown preview renderer {renderer!r}; expected one of "
            f"{', '.join(PREVIEW_RENDERERS)}"
        )
    if not callable(draw):
        raise TypeError(
            f"draw must be callable, got {type(draw).__name__!r}; pass the "
            f"function that draws {type_name!r} in the {renderer} preview"
        )
    _preview[(type_name, renderer)] = draw
    return draw


def unregister_preview_renderer(type_name: str, renderer: str | None = None) -> int:
    """Forget preview renderer(s) for ``type_name``; returns how many were removed."""
    if renderer is not None:
        if renderer not in PREVIEW_RENDERERS:
            raise ValueError(
                f"unknown preview renderer {renderer!r}; expected one of "
                f"{', '.join(PREVIEW_RENDERERS)}"
            )
        return 1 if _preview.pop((type_name, renderer), None) is not None else 0
    removed = [key for key in _preview if key[0] == type_name]
    for key in removed:
        del _preview[key]
    return len(removed)


def preview_renderer(type_name: str, renderer: str) -> Callable[..., Any] | None:
    """The function registered for ``type_name`` in ``renderer``, or ``None``."""
    if renderer not in PREVIEW_RENDERERS:
        raise ValueError(
            f"unknown preview renderer {renderer!r}; expected one of "
            f"{', '.join(PREVIEW_RENDERERS)}"
        )
    return _preview.get((type_name, renderer))


def preview_renderers() -> Mapping[str, tuple[str, ...]]:
    """Every custom type with a preview function, and the renderers it covers."""
    listed: dict[str, list[str]] = {}
    for type_name, renderer in _preview:
        listed.setdefault(type_name, []).append(renderer)
    return {
        type_name: tuple(sorted(renderer_list))
        for type_name, renderer_list in sorted(listed.items())
    }
