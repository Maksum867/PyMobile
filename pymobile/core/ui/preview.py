"""Render a widget tree to a picture on the desktop.

The phone has a Java renderer; the desktop has no native views, so this
module turns the serialised tree into something you can actually look at.
Two backends are offered:

* :func:`render_ascii` — a dependency-free text picture, always available.
* :func:`render_png`   — a real raster image, used when Pillow is installed
  (the same optional extra the icon resizer uses).

Both accept either a live :class:`~pymobile.core.ui.widget.Widget` or a
serialised ``to_dict`` node, so the same code serves the CLI, tests and
notebooks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...log import get_logger
from .contract import text_value
from .extras_preview import extra_ascii
from .flow import flow_lines, line_offset

__all__ = [
    "render_ascii",
    "render_png",
    "render_mockup",
    "ascii_picture",
    "snapshot_path",
    "assert_snapshot",
]

_log = get_logger("ui.preview")

_BAR_WIDTH = 16
_DIVIDER_WIDTH = 24


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def render_ascii(widget_or_tree: Any, *, show_ids: bool = False, title: str = "") -> str:
    """Return a text picture of the widget tree."""
    node = _as_node(widget_or_tree)
    lines = _node_lines(node, show_ids=show_ids)
    if not lines:
        lines = ["<empty screen>"]
    lines += _snackbar_lines(node)
    width = max(len(line) for line in lines)
    body = "\n".join(line.rstrip() for line in lines)
    if title:
        bar = "─" * (width + 2)
        return f"┌{bar}┐\n│ {title.ljust(width)} │\n├{bar}┤\n{body}\n└{bar}┘"
    return body


def ascii_picture(widget_or_tree: Any, **kwargs: Any) -> str:  # alias
    """Backwards-friendly alias for :func:`render_ascii`."""
    return render_ascii(widget_or_tree, **kwargs)


#: TrueType faces that ship with common systems and cover Latin, Cyrillic and
#: Greek. Pillow's built-in bitmap font is ASCII-only, which turns every
#: non-English interface into a row of boxes.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    "/usr/share/fonts/google-noto/NotoSansMono-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

#: Monochrome symbol faces consulted as a *fallback* when the primary preview
#: font lacks a glyph (emoji, dingbats, box supplements). They are never used
#: as the primary face — only to patch holes in the coverage.
_SYMBOL_CANDIDATES = (
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    "/usr/share/fonts/truetype/Symbola.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/noto/NotoSansSymbols2-Regular.ttf",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
)

#: A plane-16 noncharacter: no font may define it, so rendering it always
#: yields the face's ``.notdef`` glyph — our probe for "has no glyph for ch".
_NOTDEF_PROBE = "\U0010FFFE"

_glyph_cache: dict[tuple[Any, Any, str], bool | bytes] = {}


def _preview_font(image_font: Any, scale: int) -> Any:
    """Return a Unicode-capable font, falling back to Pillow's default.

    ``PYMOBILE_PREVIEW_FONT`` overrides the search with an explicit .ttf path.
    """
    override = os.environ.get("PYMOBILE_PREVIEW_FONT")
    candidates = (override, *_FONT_CANDIDATES) if override else _FONT_CANDIDATES
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                return image_font.truetype(candidate, scale + 2)
            except Exception:  # pragma: no cover - unreadable/odd font file
                continue
    try:  # last resort: ASCII-only, but better than no picture at all
        return image_font.load_default()
    except Exception:  # pragma: no cover - very old Pillow
        return None


def _glyph_signature(font: Any, ch: str) -> bytes:
    """Raster bytes of one glyph, identical for every char a font lacks.

    Pillow draws the face's ``.notdef`` (a tofu box, or nothing) for anything
    absent from the charmap, so two characters produce the same signature
    exactly when neither is really covered. ``Image.tobytes`` is used because
    the mask object ``getmask`` returns has no bytes protocol of its own.
    """
    from PIL import Image, ImageDraw  # type: ignore

    bbox = font.getbbox(ch)
    width = max(bbox[2], 1) + 2
    height = max(bbox[3], 1) + 2
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text((1, 1), ch, fill=255, font=font)
    return canvas.tobytes()


def _has_glyph(font: Any, ch: str) -> bool:
    """Whether ``font`` really defines ``ch`` (and not just its ``.notdef``)."""
    if ch.isspace():
        return True
    key = (getattr(font, "path", None), getattr(font, "size", None), ch)
    probe_key = (key[0], key[1], _NOTDEF_PROBE)
    probe = _glyph_cache.get(probe_key)
    if probe is None:
        probe = _glyph_signature(font, _NOTDEF_PROBE)
        _glyph_cache[probe_key] = probe
    hit = _glyph_cache.get(key)
    if hit is None:
        hit = _glyph_signature(font, ch) != probe
        _glyph_cache[key] = hit
    return bool(hit)


def _missing_glyphs(font: Any, lines: list[str]) -> set[str]:
    """Every character of the picture that ``font`` cannot draw."""
    return {ch for line in lines for ch in line if not _has_glyph(font, ch)}


def _fallback_font(image_font: Any, scale: int, missing: set[str], primary: Any) -> Any:
    """Best symbol face covering ``missing``, or ``None`` when none helps."""
    primary_path = getattr(primary, "path", None)
    best: Any = None
    best_score = 0
    for candidate in (*_FONT_CANDIDATES, *_SYMBOL_CANDIDATES):
        if not candidate or not os.path.exists(candidate) or candidate == primary_path:
            continue
        try:
            font = image_font.truetype(candidate, scale + 2)
        except Exception:  # pragma: no cover - unreadable/odd font file
            continue
        score = sum(1 for ch in missing if _has_glyph(font, ch))
        if score > best_score:
            best, best_score = font, score
        if score == len(missing):
            break
    return best


def _font_for(ch: str, primary: Any, fallback: Any) -> Any:
    if _has_glyph(primary, ch):
        return primary
    if fallback is not None and _has_glyph(fallback, ch):
        return fallback
    return primary


def _char_advance(font: Any, ch: str) -> float:
    try:
        return float(font.getlength(ch))
    except AttributeError:  # pragma: no cover - Pillow's bitmap default font
        return float(font.getsize(ch)[0])


def _line_advance(line: str, primary: Any, fallback: Any) -> float:
    return sum(_char_advance(_font_for(ch, primary, fallback), ch) for ch in line)


def _draw_line(
    draw: Any, x: float, y: int, line: str, primary: Any, fallback: Any, fill: str
) -> None:
    """Draw ``line`` glyph by glyph so holes fall back to the symbol face."""
    for ch in line:
        font = _font_for(ch, primary, fallback)
        draw.text((x, y), ch, fill=fill, font=font)
        x += _char_advance(font, ch)


def render_mockup(widget_or_tree: Any, path: Any = None, **options: Any) -> Any:
    """Draw the screen the way the phone does; see :mod:`pymobile.core.ui.mockup`.

    Unlike :func:`render_png` (a picture of the *text* preview), this lays
    the tree out with the device's rules and paints Material-style widgets in
    the theme's colours.
    """
    from .mockup import render_mockup as _render_mockup

    return _render_mockup(widget_or_tree, path, **options)


def render_png(widget_or_tree: Any, path: str, *, scale: int = 12) -> str:
    """Draw the text preview (:func:`render_ascii`) to ``path`` as PNG using Pillow.

    For a picture of what the phone actually shows use :func:`render_mockup`.

    Raises :class:`RuntimeError` with an install hint when Pillow is missing,
    so callers can fall back to :func:`render_ascii`.

    The canvas is measured with the real glyph advances of the chosen font
    (never a guessed pixels-per-character constant), and characters the font
    lacks — emoji on a bare system, say — are patched from a symbol face when
    one is installed; anything still uncovered is logged once with a hint.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError(
            "PNG preview needs Pillow; install it with `pip install Pillow`."
        ) from exc

    node = _as_node(widget_or_tree)
    lines = _node_lines(node, show_ids=False)
    if not lines:
        lines = ["<empty screen>"]
    lines += _snackbar_lines(node)
    font = _preview_font(ImageFont, scale)

    fallback: Any = None
    missing = _missing_glyphs(font, lines)
    if missing:
        fallback = _fallback_font(ImageFont, scale, missing, font)
        still = sorted(ch for ch in missing if not _has_glyph(_font_for(ch, font, fallback), ch))
        if still:
            shown = "".join(still[:8]) + ("…" if len(still) > 8 else "")
            _log.warning(
                "no preview font covers %d glyph(s): %s — they render as boxes; "
                "hint: install a symbol/emoji font or point PYMOBILE_PREVIEW_FONT "
                "at a .ttf that covers them",
                len(still),
                shown,
            )

    line_height = scale + 6
    width = int(max(_line_advance(line, font, fallback) for line in lines)) + 24
    height = len(lines) * line_height + 24
    image = Image.new("RGB", (width, height), "#101418")
    draw = ImageDraw.Draw(image)
    y = 12
    for line in lines:
        _draw_line(draw, 12, y, line, font, fallback, "#e6e6e6")
        y += line_height
    image.save(path)
    return path


# ---------------------------------------------------------------------------
# tree -> list of equal-height text rows
# ---------------------------------------------------------------------------
def _as_node(widget_or_tree: Any) -> dict[str, Any]:
    node = widget_or_tree.to_dict() if hasattr(widget_or_tree, "to_dict") else widget_or_tree
    if not isinstance(node, dict):
        raise TypeError(f"cannot preview {type(widget_or_tree).__name__}")
    return node


def _zip_options_labels(props: dict[str, Any]) -> list[tuple[str, str]]:
    """П-11: zip ``options`` (values) with ``labels`` (visible text)."""
    option_values = [str(o) for o in (props.get("options") or [])]
    option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
    while len(option_labels) < len(option_values):
        option_labels.append(option_values[len(option_labels)])
    return list(zip(option_values, option_labels, strict=False))


def _snackbar_lines(tree: dict[str, Any]) -> list[str]:
    """The snackbar of a rendered tree (``App.snackbar``) as a bottom line."""
    data = tree.get("snackbar")
    if not isinstance(data, dict) or data.get("message") is None:
        return []
    action = f"   [{data['action']}]" if data.get("action") else ""
    return [f"▌ {data['message']}{action}"]


def _node_lines(node: dict[str, Any], *, show_ids: bool = False) -> list[str]:
    if not node.get("visible", True):
        return []

    node_type = node.get("type", "")
    children = node.get("children", ())

    if node_type == "Row" or (
        node_type == "ScrollView" and node.get("props", {}).get("horizontal")
    ):
        rows = _join_horizontal([_node_lines(child, show_ids=show_ids) for child in children])
    elif node_type == "Grid":
        rows = _grid_lines(node, show_ids=show_ids)
    elif node_type == "Wrap":
        rows = _wrap_lines(node, show_ids=show_ids)
    elif node_type in ("Expanded", "Flexible"):
        # A flex wrapper draws exactly as its child; the space it claims is a
        # device-side concept with no meaning in a text picture.
        rows = _join_vertical([_node_lines(child, show_ids=show_ids) for child in children])
    elif node_type == "PageView":
        props = node.get("props", {})
        rows = _join_vertical([_node_lines(child, show_ids=show_ids) for child in children])
        rows.append(f"◂ {int(props.get('value', 0)) + 1} / {props.get('item_count', 1)} ▸")
    elif node_type in (
        "Column",
        "ScrollView",
        "Stack",
        "Container",
        "SafeArea",
        "RadioGroup",
        "List",
    ):
        rows = _join_vertical([_node_lines(child, show_ids=show_ids) for child in children])
        list_props = node.get("props", {})
        if node_type == "List" and list_props.get("refreshing"):
            rows.insert(0, "⟳ refreshing…")
        if node_type == "List" and list_props.get("has_more"):
            rest = int(list_props.get("item_count", 0)) - int(
                list_props.get("loaded", len(children))
            )
            rows.append(f"… {rest} more (loaded as you scroll)")
    else:
        rows = _leaf_lines(node, show_ids=show_ids)

    if show_ids and rows:
        tag = f"({node.get('id', '?')}) "
        rows = [tag + rows[0], *rows[1:]]
    return rows


def _join_vertical(blocks: list[list[str]]) -> list[str]:
    out: list[str] = []
    for block in blocks:
        out.extend(block)
    return out


def _grid_lines(node: dict[str, Any], *, show_ids: bool = False) -> list[str]:
    """Draw a Grid as real rows of equal-width columns.

    Column widths are computed across the whole grid rather than per row, so
    the text picture shows the same alignment the device does — that is the
    entire reason ``Grid`` exists instead of nested ``Row``s.
    """
    children = list(node.get("children", ()))
    columns = max(1, int(node.get("props", {}).get("columns", 2)))
    cells = [_node_lines(child, show_ids=show_ids) for child in children]

    widths = [0] * columns
    for index, cell in enumerate(cells):
        column = index % columns
        widths[column] = max(widths[column], max((len(line) for line in cell), default=0))

    out: list[str] = []
    for start in range(0, len(cells), columns):
        band = cells[start : start + columns]
        height = max((len(cell) for cell in band), default=0)
        for line_index in range(height):
            parts = []
            for column, cell in enumerate(band):
                text = cell[line_index] if line_index < len(cell) else ""
                parts.append(text.ljust(widths[column]))
            out.append("  ".join(parts).rstrip())
    return out


#: Width of the text picture a ``Wrap`` breaks lines at (a phone holds about this
#: many characters of body text).
_WRAP_COLUMNS = 40


def _wrap_lines(node: dict[str, Any], *, show_ids: bool = False) -> list[str]:
    """Draw a Wrap: children side by side, a new line when the row is full."""
    blocks = [_node_lines(child, show_ids=show_ids) for child in node.get("children", ())]
    align = text_value(node.get("props", {}).get("align") or "start")
    return _flow_blocks(blocks, _WRAP_COLUMNS, align=align, gap=2)


def _flow_blocks(blocks: list[list[str]], columns: int, *, align: str, gap: int) -> list[str]:
    """Lay text blocks out in lines of at most ``columns`` characters."""
    widths = [max((len(line) for line in block), default=0) if block else -1 for block in blocks]
    out: list[str] = []
    for line in flow_lines(widths, columns, gap):
        cells = [blocks[index] for index in line]
        used = sum(widths[index] for index in line) + gap * (len(line) - 1)
        indent = " " * int(line_offset(align, columns, used))
        out.extend(indent + text for text in _join_horizontal(cells, gap=gap))
    return out


def _join_horizontal(blocks: list[list[str]], *, gap: int = 2) -> list[str]:
    blocks = [block for block in blocks if block]
    if not blocks:
        return []
    height = max(len(block) for block in blocks)
    widths = [max((len(line) for line in block), default=0) for block in blocks]
    out: list[str] = []
    for row in range(height):
        parts = []
        for block, width in zip(blocks, widths, strict=True):
            cell = block[row] if row < len(block) else ""
            parts.append(cell.ljust(width))
        out.append((" " * gap).join(parts).rstrip())
    return out


def _leaf_lines(node: dict[str, Any], show_ids: bool = False) -> list[str]:
    node_type = node.get("type", "")
    props = node.get("props", {})
    disabled = not node.get("enabled", True)

    if node_type in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "Chart"):
        return extra_ascii(node)

    if node_type == "Label":
        text = text_value(props.get("text", ""))
        return [text] if text else [" "]

    if node_type == "Button":
        label = text_value(props.get("text", "")) or "button"
        return [f"({label})" if not disabled else f"({label}) ✗"]

    if node_type == "TextInput":
        value = props.get("value") or props.get("placeholder") or ""
        masked = props.get("password")
        shown = "•" * len(value) if masked else str(value)
        return [f"⎡{shown or '…'}⎦"]

    if node_type == "Switch":
        return ["[●] on" if props.get("checked") else "[○] off"]

    if node_type == "ProgressBar":
        if props.get("indeterminate"):
            return ["[" + "≈" * _BAR_WIDTH + "]"]
        maximum = props.get("maximum", 100) or 100
        fraction = max(0.0, min(float(props.get("value", 0)) / maximum, 1.0))
        filled = round(_BAR_WIDTH * fraction)
        bar = "█" * filled + "░" * (_BAR_WIDTH - filled)
        return [f"[{bar}] {round(fraction * 100)}%"]

    if node_type == "Image":
        return [f"[🖼 {props.get('source', '')}]"]

    if node_type == "Spacer":
        return [" "]

    if node_type == "Divider":
        return ["│" if props.get("vertical") else "─" * _DIVIDER_WIDTH]

    if node_type == "Slider":
        minimum = float(props.get("minimum", 0))
        maximum = float(props.get("maximum", 100))
        value = max(minimum, min(float(props.get("value", 0)), maximum))
        span = max(maximum - minimum, 1e-9)
        pos = round((value - minimum) / span * (_BAR_WIDTH - 1))
        slider_bar: list[str] = list("─" * _BAR_WIDTH)
        slider_bar[pos] = "●"
        return [f"[{''.join(slider_bar)}] {value:g}"]

    if node_type == "Checkbox":
        return ["[✓] on" if props.get("checked") else "[ ] off"]

    if node_type == "RatingBar":
        rating = max(0.0, min(float(props.get("rating", 0)), int(props.get("maximum", 5))))
        maximum = int(props.get("maximum", 5))
        rating_filled = "★" * round(rating)
        empty = "☆" * max(0, maximum - round(rating))
        return [f"{rating_filled}{empty} {rating:g}/{maximum}"]

    if node_type == "Dropdown":
        # П-11: show the currently selected *label*, not the internal value,
        # so a localised dropdown reads as it does on device.
        option_values = [str(o) for o in (props.get("options") or [])]
        option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
        while len(option_labels) < len(option_values):
            option_labels.append(option_values[len(option_labels)])
        current_value = text_value(props.get("value", option_values[0] if option_values else ""))
        try:
            label = option_labels[option_values.index(current_value)]
        except ValueError:
            label = current_value
        return [f"[{label} ▾]"]

    if node_type == "Chip":
        label = text_value(props.get("text", "")) or "chip"
        mark = "● " if props.get("selected") else ""
        return [f"({mark}{label})" if not disabled else f"({mark}{label}) ✗"]

    if node_type == "Badge":
        return [f"{{ {props.get('text', '')} }}"]

    if node_type == "Stepper":
        value = props.get("value", 0)
        return [f"(-) {value} (+)"]

    if node_type == "SearchBar":
        value = text_value(props.get("value", "")) or text_value(
            props.get("placeholder", "Search…")
        )
        return [f"⎡🔍 {value}⎦"]

    if node_type == "RadioButton":
        mark = "◉" if props.get("selected") else "○"
        return [f"{mark} {props.get('text', '')}"]

    if node_type == "SegmentedButtons":
        option_values = [str(o) for o in (props.get("options") or [])]
        option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
        while len(option_labels) < len(option_values):
            option_labels.append(option_values[len(option_labels)])
        value = props.get("value", "")
        parts = [
            f"|{lbl}|" if v == value else f" {lbl} "
            for v, lbl in zip(option_values, option_labels, strict=False)
        ]
        if props.get("wrap"):  # the options flow onto more lines
            return _flow_blocks([[part] for part in parts], _WRAP_COLUMNS, align="start", gap=1)
        return [" ".join(parts)]

    if node_type == "ProgressText":
        return [f"[{props.get('text', '')}]"]

    if node_type == "Link":
        text = text_value(props.get("text", "")) or "link"
        return [f"<{text}>"]

    if node_type == "DataTable":
        headers = [str(h) for h in props.get("headers", [])]
        rows = [[str(c) for c in r] for r in props.get("rows", [])]
        if not headers:
            return ["<table>"]
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(cell))
        header = " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
        lines = [header, "-" * len(header)]
        for row in rows:
            padded = [
                cell.ljust(widths[i]) if i < len(widths) else cell for i, cell in enumerate(row)
            ]
            lines.append(" | ".join(padded))
        return lines

    if node_type == "Avatar":
        if props.get("source"):
            return [f"[🖼 {props.get('source', '')}]"]
        return [f"[{props.get('text', '')[:2].upper()}]"]

    if node_type == "ListTile":
        title = text_value(props.get("title", ""))
        subtitle = text_value(props.get("subtitle", ""))
        trailing = text_value(props.get("trailing", ""))
        base = title
        if subtitle:
            base += f" — {subtitle}"
        if trailing:
            base += f"  {trailing}"
        return [f"▸ {base}" if not disabled else f"  {base}"]

    if node_type == "BottomNavigation":
        pairs = _zip_options_labels(props)
        value = props.get("value")
        tabs = " ".join(f"[{lbl}]" if v == value else f" {lbl} " for v, lbl in pairs)
        bar = "─" * (len(tabs) + 2)
        return [bar, tabs, bar]

    if node_type == "Dialog":
        title = text_value(props.get("title", ""))
        inner: list[str] = []
        for child in node.get("children", ()):
            inner.extend(_node_lines(child, show_ids=show_ids))
        width = max([len(title) + 1, 18] + [len(line) for line in inner])
        if title:
            head = f"┌─ {title} " + "─" * (width - len(title) - 1) + "┐"
        else:
            head = "┌" + "─" * (width + 2) + "┐"
        body = [f"│ {line.ljust(width)} │" for line in inner] or [f"│ {' ' * width} │"]
        foot = "└" + "─" * (width + 2) + "┘"
        if props.get("sheet"):
            return ["▔" * (width + 4), *body, foot]
        return [head, *body, foot]

    if node_type == "DatePicker":
        return [f"[📅 {props.get('value', '')}]"]

    if node_type == "TimePicker":
        return [f"[🕒 {props.get('value', '')}]"]

    return [f"<{node_type}>"]


# ---------------------------------------------------------------------------
# Snapshot testing helpers (golden-file comparison for ASCII previews)
# ---------------------------------------------------------------------------
def snapshot_path(test_file: str, name: str = "screen", *, ext: str = ".txt") -> Path:
    """Conventional snapshot file location next to a test module.

    ``test_file`` is ``__file__`` from the calling test; the snapshot is written
    to a ``snapshots/`` folder beside it, named ``<module>__<name>.txt``.
    """
    source = Path(test_file).resolve()
    module = source.stem
    return source.parent / "snapshots" / f"{module}__{name}{ext}"


def assert_snapshot(
    widget_or_tree: Any,
    test_file: str,
    name: str = "screen",
    *,
    show_ids: bool = False,
    title: str = "",
    update: bool = False,
    hint: str = "Run with update=True to accept the new output.",
) -> str:
    """Compare an ASCII render of ``widget_or_tree`` against a golden snapshot.

    On the first run (or with ``update=True``) the snapshot file is written and
    the test passes; on later runs the render must equal the stored golden text,
    otherwise an :class:`AssertionError` is raised with a diff. This makes it
    trivial to pin a screen's layout in tests and catch unintended changes.

    ``hint`` closes the failure message: what to do to accept the change.

    Returns the rendered text so it can be reused.
    """
    rendered = render_ascii(widget_or_tree, show_ids=show_ids, title=title)
    path = snapshot_path(test_file, name)
    if update or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return rendered
    golden = path.read_text(encoding="utf-8")
    if rendered != golden:
        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                golden.splitlines(),
                rendered.splitlines(),
                fromfile="snapshot",
                tofile="rendered",
                lineterm="",
            )
        )
        raise AssertionError(f"Snapshot {path.name} changed:\n{diff}\n{hint}")
    return rendered
