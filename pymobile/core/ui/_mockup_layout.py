"""Measure widget trees and create paintable boxes for the mockup facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._mockup_chrome import _ChromeLayout
from ._mockup_colors import _rgba
from ._mockup_fonts import _Fonts
from ._mockup_paint import _Box, _Painter
from ._mockup_widgets import _WidgetLayout
from .contract import text_value
from .flow import flow_lines, line_offset

_STATUS_BAR = 24
_APP_BAR = 56

_VERTICAL = ("Column", "Container", "SafeArea", "RadioGroup", "List")
#: Containers that C-3 made tappable: the mockup outlines them so the touch
#: target is visible. A Button serialises ``on_press`` too and is already drawn
#: as a button, so it stays out of this set.
_TAPPABLE_CONTAINERS = (
    "Column",
    "Row",
    "Container",
    "Stack",
    "Dialog",
    "Grid",
    "Wrap",
    "ScrollView",
    "SafeArea",
)
_BUTTON_LIKE = ("Button", "Chip", "DatePicker", "TimePicker")


def _insets(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            left, top, right, bottom = (float(v) for v in value)
        except (TypeError, ValueError):
            return None
        return (left, top, right, bottom)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _flex_of(node: dict[str, Any]) -> float:
    kind = node.get("type")
    if kind in ("Expanded", "Flexible"):
        try:
            return max(1.0, float(node.get("props", {}).get("flex", 1) or 1))
        except (TypeError, ValueError):
            return 1.0
    weight = _number((node.get("style") or {}).get("weight"))
    return weight if weight and weight > 0 else 0.0


class _Layout(_WidgetLayout, _ChromeLayout):
    def __init__(
        self, fonts: _Fonts, palette: dict[str, Any], assets: Path | None, scale: float
    ) -> None:
        self.fonts = fonts
        self.p = palette
        self.assets = assets
        self.scale = scale
        self._dialogs: dict[str, dict[str, Any]] = {}

    @property
    def dialogs(self) -> list[dict[str, Any]]:
        """Visible dialogs, once each however often the tree was measured."""
        return list(self._dialogs.values())

    # -- entry -------------------------------------------------------------
    def layout(
        self,
        node: dict[str, Any],
        max_w: float,
        *,
        fill: bool,
        force_h: float | None = None,
        axis: str = "v",
    ) -> _Box | None:
        """Lay ``node`` out in at most ``max_w`` dp (margins included)."""
        if not node.get("visible", True):
            return None
        kind = str(node.get("type", ""))
        if kind == "Dialog":
            self._dialogs[str(node.get("id") or len(self._dialogs))] = node
            return _Box(0, 0)
        style = dict(node.get("style") or {})
        margin = _insets(style.get("margin")) or (0.0, 0.0, 0.0, 0.0)
        avail = max(0.0, max_w - margin[0] - margin[2])

        fixed_w = _number(style.get("width"))
        wanted = str(style.get("width") or "").lower()
        if fixed_w is not None:
            width: float | None = min(fixed_w, avail) if avail else fixed_w
        elif wanted in ("fill", "match", "match_parent"):
            width = avail
        elif wanted in ("wrap", "wrap_content"):
            width = None
        else:
            width = avail if fill else None
        max_width = _number(style.get("max_width"))
        if max_width is not None:
            avail = min(avail, max_width)
            if width is not None:
                width = min(width, max_width)

        fixed_h = _number(style.get("height"))
        inner_force = (
            fixed_h
            if fixed_h is not None
            else (None if force_h is None else max(0.0, force_h - margin[1] - margin[3]))
        )

        box = self._layout_kind(
            kind,
            node,
            style,
            avail if width is None else width,
            width is not None,
            inner_force,
            axis,
        )
        # Width constraints.
        min_w = _number(style.get("min_width"))
        if width is not None:
            box.w = width
        if min_w is not None:
            box.w = max(box.w, min(min_w, max_w))
        if max_width is not None:
            box.w = min(box.w, max_width)
        # Height constraints.
        if inner_force is not None:
            box.h = inner_force
        min_h = _number(style.get("min_height"))
        if min_h is not None:
            box.h = max(box.h, min_h)
        max_h = _number(style.get("max_height"))
        if max_h is not None:
            box.h = min(box.h, max_h)
        ratio = _number(style.get("aspect_ratio"))
        if ratio and ratio > 0 and fixed_h is None:
            box.h = box.w / ratio

        box.margin = margin
        box.style = style
        # C-3: a tappable container is a touch target on the phone, so the
        # mockup outlines it in the accent colour — the same hint the ASCII
        # preview shows as a ▸ … ◂ frame.
        props = node.get("props") or {}
        if (
            kind in _TAPPABLE_CONTAINERS
            and isinstance(props, dict)
            and (props.get("on_press") or props.get("on_long_press"))
        ):
            style = dict(style)
            style["_tappable"] = True
            style["_tappable_colour"] = self.p["PRIMARY"]
            box.style = style
        if style.get("elevation") and style.get("background") is None:
            style["_surface"] = self.p["SURFACE"]
        if not node.get("enabled", True):
            box.fade = 0.45
        return box

    def _padding(
        self, style: dict[str, Any], default: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return _insets(style.get("padding")) or default

    # -- dispatch ----------------------------------------------------------
    def _layout_kind(
        self,
        kind: str,
        node: dict[str, Any],
        style: dict[str, Any],
        w: float,
        fill: bool,
        force_h: float | None,
        axis: str,
    ) -> _Box:
        props = node.get("props") or {}
        children = [c for c in node.get("children", ()) if isinstance(c, dict)]
        if kind in _VERTICAL or (kind == "ScrollView" and not props.get("horizontal")):
            return self._vertical(kind, node, props, style, children, w, fill, force_h)
        if kind == "Row":
            return self._row(props, style, children, w, fill, force_h)
        if kind == "ScrollView":
            return self._hscroll(props, style, children, w, force_h)
        if kind == "Grid":
            return self._grid(props, style, children, w, force_h)
        if kind == "Wrap":
            return self._wrap(props, style, children, w, fill)
        if kind == "Stack":
            return self._stack(props, style, children, w, fill, force_h)
        if kind == "PageView":
            return self._vertical("Column", node, props, style, children[:1], w, True, force_h)
        if kind in ("Expanded", "Flexible"):
            pad = self._padding(style, (0, 0, 0, 0))
            inner = (
                self.layout(
                    children[0],
                    w - pad[0] - pad[2],
                    fill=True,
                    force_h=None if force_h is None else force_h - pad[1] - pad[3],
                )
                if children
                else None
            )
            box = _Box(
                w if fill else (inner.outer_w if inner else 0) + pad[0] + pad[2],
                (inner.outer_h if inner else 0) + pad[1] + pad[3],
            )
            if inner is not None:
                box.kids.append((pad[0], pad[1], inner))
            return box
        return self._leaf(kind, node, props, style, w, fill, axis)

    # -- containers --------------------------------------------------------
    def _vertical(
        self,
        kind: str,
        node: dict[str, Any],
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        default_pad = (0.0, 0.0, 0.0, 0.0)
        if kind == "SafeArea":
            m = float(props.get("minimum", 0) or 0)
            default_pad = (m, m, m, m)
        pad = self._padding(style, default_pad)
        cw = max(0.0, w - pad[0] - pad[2])
        spacing = float(props.get("spacing", 0) or 0)
        cross = (
            ""
            if kind in ("ScrollView", "SafeArea", "RadioGroup")
            else text_value(props.get("cross_align") or "")
        )
        align = text_value(props.get("align") or "") if kind == "Column" else ""
        stretch = cross in ("", "stretch")
        if not fill and children:
            # WRAP_CONTENT: LinearLayout measures the children by their
            # content first; MATCH_PARENT children then get that width.
            widths = [
                self._child_v(child, cw, False).outer_w
                for child in children
                if child.get("visible", True) and child.get("type") not in ("Spacer", "Divider")
            ]
            cw = min(cw, max([*widths, 0.0]))
            fill = True

        head: _Box | None = None
        if kind == "List" and props.get("refreshable") and props.get("refreshing"):
            head = _Box(cw, 56, self._paint_refresh_header)

        entries: list[tuple[dict[str, Any], _Box | None, float]] = []
        for child in children:
            if not child.get("visible", True):
                continue
            flex = _flex_of(child)
            if flex and force_h is not None:
                entries.append((child, None, flex))
                continue
            entries.append((child, self._child_v(child, cw, stretch), 0.0))

        used = sum(b.outer_h for _, b, _ in entries if b is not None)
        used += spacing * max(0, len(entries) - 1) + (head.h if head else 0)
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        free = 0.0 if inner_h is None else max(0.0, inner_h - used)
        total_flex = sum(f for _, b, f in entries if b is None)
        resolved: list[_Box] = []
        for child, laid, flex in entries:
            if laid is None:
                share = free * flex / total_flex if total_flex else 0.0
                loose = child.get("props", {}).get("fit") == "loose"
                laid = self.layout(child, cw, fill=True, force_h=None if loose else share) or _Box(
                    0, 0
                )
                if loose:
                    laid.set_outer_h(min(laid.outer_h, share))
            resolved.append(laid)

        content_w = (
            cw if fill else max([b.outer_w for b in resolved] + [head.w if head else 0.0, 0.0])
        )
        box = _Box(content_w + pad[0] + pad[2], 0)
        y = pad[1]
        if head is not None:
            head.w = content_w
            box.kids.append((pad[0], y, head))
            y += head.h
        total = sum(b.outer_h for b in resolved) + spacing * max(0, len(resolved) - 1)
        gap = spacing
        if inner_h is not None and not total_flex:
            leftover = max(0.0, inner_h - total - (head.h if head else 0))
            if align == "center":
                y += leftover / 2
            elif align == "end":
                y += leftover
            elif align == "space_between" and len(resolved) > 1:
                gap += leftover / (len(resolved) - 1)
        for index, laid in enumerate(resolved):
            if index:
                y += gap
            dx = 0.0
            if not stretch and laid.outer_w < content_w:
                if cross == "center":
                    dx = (content_w - laid.outer_w) / 2
                elif cross == "end":
                    dx = content_w - laid.outer_w
            box.kids.append((pad[0] + dx, y, laid))
            y += laid.outer_h
        box.h = y + pad[3]
        if kind == "List" and props.get("has_more"):
            footer = _Box(content_w, 48, self._paint_load_more)
            box.kids.append((pad[0], box.h - pad[3], footer))
            box.h += 48
        return box

    def _child_v(self, child: dict[str, Any], cw: float, stretch: bool) -> _Box:
        kind = child.get("type")
        props = child.get("props") or {}
        if kind == "Spacer":
            size = float(props.get("size", 8) or 0)
            return _Box(cw if stretch else size, size)
        if kind == "Divider":
            return self._divider(props, cw, vertical_parent=True)
        return self.layout(child, cw, fill=stretch, axis="v") or _Box(0, 0)

    def _divider(self, props: dict[str, Any], length: float, *, vertical_parent: bool) -> _Box:
        thickness = max(1.0, float(props.get("thickness", 1) or 1))
        colour = _rgba(props.get("color"), self.p["DIVIDER"])
        inset = float(props.get("inset", 0) or 0)
        upright = bool(props.get("vertical"))

        def paint(p: _Painter, x: float, y: float, b: _Box) -> None:
            p.rect(x, y, b.w, b.h, colour)

        if upright:
            box = _Box(thickness, 24 if vertical_parent else 0, paint)
            box.margin = (0.0, inset, 0.0, inset)
        else:
            box = _Box(max(0.0, length - 2 * inset) if vertical_parent else 24, thickness, paint)
            box.margin = (inset, 0.0, inset, 0.0)
        return box

    def _row(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        spacing = float(props.get("spacing", 0) or 0)
        cross = text_value(props.get("cross_align") or "")
        align = text_value(props.get("align") or "")
        visible = [c for c in children if c.get("visible", True)]
        remaining = cw - spacing * max(0, len(visible) - 1)
        laid: list[_Box | None] = []
        flexes: list[float] = []
        upright_dividers: list[_Box] = []
        for child in visible:
            flex = _flex_of(child)
            flexes.append(flex)
            if flex:
                laid.append(None)
                continue
            kind = child.get("type")
            cprops = child.get("props") or {}
            if kind == "Spacer":
                size = float(cprops.get("size", 8) or 0)
                box = _Box(size, size)
            elif kind == "Divider":
                box = self._divider(cprops, 0, vertical_parent=False)
                if cprops.get("vertical"):
                    upright_dividers.append(box)
            else:
                box = self.layout(child, max(0.0, remaining), fill=False, axis="h") or _Box(0, 0)
            remaining -= box.outer_w
            laid.append(box)
        total_flex = sum(flexes)
        free = max(0.0, remaining)
        for index, child in enumerate(visible):
            if laid[index] is None:
                share = free * flexes[index] / total_flex if total_flex else 0.0
                loose = child.get("props", {}).get("fit") == "loose"
                box = self.layout(child, share, fill=not loose, axis="h") or _Box(0, 0)
                laid[index] = box
        boxes = [b for b in laid if b is not None]
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        row_h = (
            inner_h
            if inner_h is not None
            else max([b.outer_h for b in boxes if b not in upright_dividers] + [0.0])
        )
        for divider in upright_dividers:
            divider.set_outer_h(row_h)
        used = sum(b.outer_w for b in boxes) + spacing * max(0, len(boxes) - 1)
        content_w = cw if fill else min(cw, used)
        x = pad[0]
        gap = spacing
        leftover = max(0.0, content_w - used)
        if not total_flex:
            if align == "center":
                x += leftover / 2
            elif align == "end":
                x += leftover
            elif align == "space_between" and len(boxes) > 1:
                gap += leftover / (len(boxes) - 1)
        box = _Box(content_w + pad[0] + pad[2], row_h + pad[1] + pad[3])
        for index, child_box in enumerate(boxes):
            if index:
                x += gap
            if cross == "stretch":
                child_box.set_outer_h(row_h)
                dy = 0.0
            elif cross == "start":
                dy = 0.0
            elif cross == "end":
                dy = row_h - child_box.outer_h
            else:
                dy = (row_h - child_box.outer_h) / 2
            box.kids.append((x, pad[1] + dy, child_box))
            x += child_box.outer_w
        return box

    def _hscroll(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        spacing = float(props.get("spacing", 0) or 0)
        x = pad[0]
        kids: list[tuple[float, _Box]] = []
        for child in children:
            box = self.layout(child, 100000.0, fill=False, axis="h")
            if box is None:
                continue
            if kids:
                x += spacing
            kids.append((x, box))
            x += box.outer_w
        h = max([b.outer_h for _, b in kids] + [0.0])
        box = _Box(w, h + pad[1] + pad[3])
        box.kids = [(kx, pad[1] + (h - b.outer_h) / 2, b) for kx, b in kids]
        box.clip = True
        return box

    def _grid(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        columns = max(1, int(props.get("columns", 2) or 2))
        row_gap = float(props.get("row_spacing", 0) or 0)
        col_gap = float(props.get("column_spacing", 0) or 0)
        cell_w = max(0.0, (cw - col_gap * (columns - 1)) / columns)
        box = _Box(w, 0)
        y = pad[1]
        # Hidden cells keep their slot, as on the device (GONE inside a weighted row).
        for start in range(0, len(children), columns):
            row = children[start : start + columns]
            laid = [self.layout(child, cell_w, fill=True) for child in row]
            height = max([b.outer_h for b in laid if b is not None] + [0.0])
            if start:
                y += row_gap
            for column, cell in enumerate(laid):
                if cell is None:
                    continue
                box.kids.append((pad[0] + column * (cell_w + col_gap), y, cell))
            y += height
        box.h = y + pad[3]
        return box

    def _flow(
        self, boxes: list[_Box], width: float, gap: float, run_gap: float, align: str
    ) -> tuple[list[tuple[float, float, _Box]], float, float]:
        """Place boxes in lines, as ``ViewBuilder.FlowLayout`` does.

        Returns the positioned boxes, the height used and the widest line.
        """
        placed: list[tuple[float, float, _Box]] = []
        y = 0.0
        widest = 0.0
        for line in flow_lines([b.outer_w for b in boxes], width, gap):
            used = sum(boxes[i].outer_w for i in line) + gap * (len(line) - 1)
            x = line_offset(align, width, used)
            for index in line:
                placed.append((x, y, boxes[index]))
                x += boxes[index].outer_w + gap
            y += max(boxes[i].outer_h for i in line) + run_gap
            widest = max(widest, used)
        return placed, max(0.0, y - run_gap) if placed else 0.0, widest

    def _wrap(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
    ) -> _Box:
        """Wrap: children in natural size, a new line when the row is full."""
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        gap = float(props.get("spacing", 0) or 0)
        run_value = props.get("run_spacing")
        run_gap = gap if run_value is None else float(run_value or 0)
        align = text_value(props.get("align") or "start")
        visible = [c for c in children if c.get("visible", True)]
        boxes = [self.layout(child, cw, fill=False, axis="h") or _Box(0, 0) for child in visible]
        placed, height, widest = self._flow(boxes, cw, gap, run_gap, align)
        if not fill:
            # A wrap-content box is as wide as its longest line, and the shorter
            # lines are aligned inside that width.
            placed, height, widest = self._flow(boxes, widest, gap, run_gap, align)
        box = _Box((cw if fill else widest) + pad[0] + pad[2], height + pad[1] + pad[3])
        box.kids = [(pad[0] + x, pad[1] + y, child) for x, y, child in placed]
        return box

    def _stack(
        self,
        props: dict[str, Any],
        style: dict[str, Any],
        children: list[dict[str, Any]],
        w: float,
        fill: bool,
        force_h: float | None,
    ) -> _Box:
        pad = self._padding(style, (0, 0, 0, 0))
        cw = max(0.0, w - pad[0] - pad[2])
        if not fill and children:
            measured = [self.layout(child, cw, fill=False) for child in children]
            cw = min(cw, max([b.outer_w for b in measured if b is not None] + [0.0]))
            fill = True
        # FrameLayout's default params are MATCH_PARENT: every child fills.
        laid = [
            b for b in (self.layout(child, cw, fill=True) for child in children) if b is not None
        ]
        inner_h = None if force_h is None else max(0.0, force_h - pad[1] - pad[3])
        height = inner_h if inner_h is not None else max([b.outer_h for b in laid] + [0.0])
        for child_box in laid:
            child_box.set_outer_h(height)
        # C-2: ``spacing`` in a Stack separates the layers along the frame, so
        # a badge/overlay does not sit exactly on top of what it annotates.
        spacing = float(props.get("spacing", 0) or 0)
        box = _Box(cw + pad[0] + pad[2], height + pad[1] + pad[3] + spacing * max(0, len(laid) - 1))
        box.kids = [(pad[0], pad[1] + index * spacing, child) for index, child in enumerate(laid)]
        return box

    # -- leaves ------------------------------------------------------------
