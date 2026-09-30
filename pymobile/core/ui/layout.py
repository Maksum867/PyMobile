"""Layout containers.

Three primitives cover the simplest mobile screens: a vertical stack, a
horizontal stack and a scrollable region. Real applications need a little
more, so this module also ships the patterns that were previously faked with
``Row(weight=...)`` gymnastics:

* :class:`Grid`      — an N-column grid of equal-width cells;
* :class:`Wrap`      — children side by side, on a new line when the row is full;
* :class:`Expanded`  — "take all the space left over on the main axis";
* :class:`Flexible`  — the same, but the child may stay smaller;
* :class:`Divider`   — a hairline between sections;
* :class:`SafeArea`  — keeps content clear of the notch and the status bar.

Anything more exotic can still be built by composing them, or by subclassing
:class:`~pymobile.core.ui.widget.Container`.
"""

from __future__ import annotations

from typing import Any

from .flow import ALIGNMENTS
from .style import Align, Color
from .widget import Container, Widget

__all__ = [
    "Column",
    "Row",
    "ScrollView",
    "Stack",
    "Grid",
    "Wrap",
    "Expanded",
    "Flexible",
    "Divider",
    "SafeArea",
]


class _Linear(Container):
    """Shared behaviour of :class:`Column` and :class:`Row`.

    ``align`` positions children along the main axis, ``cross_align`` across
    it. Leaving ``cross_align`` unset keeps the platform default (children
    stretch in a Column, and are vertically centred in a Row), which is what
    the previous releases did.
    """

    __slots__ = ("spacing", "align", "cross_align")

    def __init__(
        self,
        *children: Widget,
        spacing: int = 0,
        align: str = Align.START,
        cross_align: str | None = None,
        **kwargs: Any,
    ) -> None:
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        if cross_align is not None:
            Align.validate_cross(cross_align)
        self.spacing = spacing
        self.align = align
        self.cross_align = cross_align
        super().__init__(*children, **kwargs)

    def props(self) -> dict[str, Any]:
        props = {**super().props(), "spacing": self.spacing, "align": self.align}
        if self.cross_align is not None:
            props["cross_align"] = self.cross_align
        return props


class Column(_Linear):
    """Stacks children vertically."""

    type_name = "Column"
    __slots__ = ()


class Row(_Linear):
    """Stacks children horizontally."""

    type_name = "Row"
    __slots__ = ()


class ScrollView(Container):
    """Makes its content scrollable.

    Children are laid out exactly as a :class:`Column` would place them (or a
    :class:`Row` when ``horizontal``), so ``spacing``, per-widget margins and
    flex shares behave the same whether or not the content scrolls.
    """

    type_name = "ScrollView"
    __slots__ = ("horizontal", "spacing")

    def __init__(
        self,
        *children: Widget,
        horizontal: bool = False,
        spacing: int = 0,
        **kwargs: Any,
    ) -> None:
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        self.horizontal = horizontal
        self.spacing = spacing
        super().__init__(*children, **kwargs)

    def props(self) -> dict[str, Any]:
        return {**super().props(), "horizontal": self.horizontal, "spacing": self.spacing}


class Stack(Container):
    """Overlays children on top of each other (last child on top)."""

    type_name = "Stack"
    __slots__ = ()


class Grid(Container):
    """Lays children out in a grid of equal-width columns.

    This is the container ``Row(weight=1)`` could never be: every cell in a
    column gets exactly the same width, so two stat cards stay aligned even
    when one holds ``"5"`` and the other ``"2 h 45 m"``.

    ::

        Grid(
            card("Completed", "12"), card("Focus time", "5 h"),
            card("Breaks", "4"),     card("Average", "25 m"),
            columns=2, spacing=12,
        )

    Rows are filled left to right; the last row may be partially filled.
    """

    type_name = "Grid"
    __slots__ = ("columns", "spacing", "row_spacing", "column_spacing")

    def __init__(
        self,
        *children: Widget,
        columns: int = 2,
        spacing: int = 0,
        row_spacing: int | None = None,
        column_spacing: int | None = None,
        **kwargs: Any,
    ) -> None:
        if columns < 1:
            raise ValueError("columns must be >= 1")
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        for name, value in (("row_spacing", row_spacing), ("column_spacing", column_spacing)):
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        self.columns = columns
        self.spacing = spacing
        self.row_spacing = row_spacing
        self.column_spacing = column_spacing
        super().__init__(*children, **kwargs)

    @property
    def rows(self) -> int:
        """Number of rows the current children occupy."""
        return -(-len(self.children) // self.columns)  # ceil division

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "columns": self.columns,
            "row_spacing": self.spacing if self.row_spacing is None else self.row_spacing,
            "column_spacing": (
                self.spacing if self.column_spacing is None else self.column_spacing
            ),
        }


class Wrap(Container):
    """Lays children side by side and starts a new line when the row is full.

    The layout for things whose number and width are not known in advance — tags,
    filter chips, quick-reply buttons, translated labels — where a :class:`Row`
    would push the last one off the screen and a :class:`Grid` would give every
    cell the width of the widest::

        Wrap(*[Chip(tag) for tag in tags], spacing=8, run_spacing=8)

    ``spacing`` is the gap between children on a line, ``run_spacing`` the gap
    between lines (it defaults to ``spacing``). ``align`` places each line when
    it is shorter than the container: ``"start"`` (default), ``"center"`` or
    ``"end"``. Children keep their natural size; one wider than the container
    gets a line of its own.
    """

    type_name = "Wrap"
    __slots__ = ("spacing", "run_spacing", "align")

    def __init__(
        self,
        *children: Widget,
        spacing: int = 0,
        run_spacing: int | None = None,
        align: str = Align.START,
        **kwargs: Any,
    ) -> None:
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        if run_spacing is not None and run_spacing < 0:
            raise ValueError("run_spacing must not be negative")
        if align not in ALIGNMENTS:
            raise ValueError(f"invalid align {align!r}; expected one of {', '.join(ALIGNMENTS)}")
        self.spacing = spacing
        self.run_spacing = run_spacing
        self.align = align
        super().__init__(*children, **kwargs)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "spacing": self.spacing,
            "run_spacing": self.spacing if self.run_spacing is None else self.run_spacing,
            "align": self.align,
        }


class _Flex(Container):
    """Shared behaviour of :class:`Expanded` and :class:`Flexible`."""

    __slots__ = ("flex",)

    #: ``"tight"`` forces the child to fill the share, ``"loose"`` allows it
    #: to stay smaller.
    fit: str = "tight"

    def __init__(self, child: Widget, *, flex: int = 1, **kwargs: Any) -> None:
        if flex < 1:
            raise ValueError("flex must be >= 1")
        self.flex = flex
        super().__init__(child, **kwargs)

    def add(self, child: Widget) -> Widget:
        """Accept exactly one child; a second one is a programming error."""
        if self.children:
            raise ValueError(f"{type(self).__name__} takes a single child")
        return super().add(child)

    @property
    def child(self) -> Widget:
        """The wrapped widget."""
        return self.children[0]

    def props(self) -> dict[str, Any]:
        return {**super().props(), "flex": self.flex, "fit": self.fit}


class Expanded(_Flex):
    """Gives its child all the remaining space on the parent's main axis.

    Two children with ``flex=1`` split the row in half; ``flex=2`` next to
    ``flex=1`` takes two thirds::

        Row(Expanded(left), Expanded(right, flex=2), spacing=8)
    """

    type_name = "Expanded"
    fit = "tight"
    __slots__ = ()


class Flexible(_Flex):
    """Like :class:`Expanded`, but the child may be smaller than its share."""

    type_name = "Flexible"
    fit = "loose"
    __slots__ = ()


class Divider(Widget):
    """A hairline separating two sections.

    Horizontal by default, so it works straight inside a :class:`Column`; pass
    ``vertical=True`` to separate the children of a :class:`Row`.
    """

    type_name = "Divider"
    __slots__ = ("thickness", "color", "inset", "vertical")

    def __init__(
        self,
        *,
        thickness: int = 1,
        color: str = "#1F000000",
        inset: int = 0,
        vertical: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if thickness < 1:
            raise ValueError("thickness must be >= 1")
        if inset < 0:
            raise ValueError("inset must not be negative")
        self.thickness = thickness
        self.color = Color.validate(color)
        self.inset = inset
        self.vertical = vertical

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "thickness": self.thickness,
            "color": self.color,
            "inset": self.inset,
            "vertical": self.vertical,
        }


class SafeArea(Container):
    """Keeps its content out from under the notch, status bar and gesture bar.

    Wrap the root of a screen in it and the padding is applied by the platform
    from the real window insets — no hard-coded ``EdgeInsets(0, 24, 0, 0)``
    that is wrong on the next phone::

        def build(self):
            return SafeArea(Column(...))

    Individual edges can be opted out of, which is handy when a header should
    bleed into the status bar area but the bottom must stay clear.
    """

    type_name = "SafeArea"
    __slots__ = ("top", "bottom", "left", "right", "minimum")

    def __init__(
        self,
        *children: Widget,
        top: bool = True,
        bottom: bool = True,
        left: bool = True,
        right: bool = True,
        minimum: int = 0,
        **kwargs: Any,
    ) -> None:
        if minimum < 0:
            raise ValueError("minimum must not be negative")
        self.top = top
        self.bottom = bottom
        self.left = left
        self.right = right
        self.minimum = minimum
        super().__init__(*children, **kwargs)

    def props(self) -> dict[str, Any]:
        return {
            **super().props(),
            "top": self.top,
            "bottom": self.bottom,
            "left": self.left,
            "right": self.right,
            "minimum": self.minimum,
        }
