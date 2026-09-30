"""How a flow layout breaks its children into lines.

``Wrap`` (and ``SegmentedButtons(wrap=True)``) put children side by side and
start a new line when the next one does not fit. The rule is deliberately tiny,
and the Android renderer (``ViewBuilder.FlowMath.lines``) implements exactly the
same one, so the desktop previews break the lines where the phone does:

* an item goes on the current line if it fits in what is left after the gap;
* the first item of a line is always placed, even wider than the line — it
  then takes the whole line (its own text wraps) instead of vanishing;
* an item of negative width is hidden and takes no place.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["ALIGNMENTS", "flow_lines", "line_offset"]

#: Where a line sits when it is shorter than the container.
ALIGNMENTS = ("start", "center", "end")


def flow_lines(widths: Sequence[float], available: float, gap: float = 0.0) -> list[list[int]]:
    """Group item indices into lines of at most ``available`` (gaps included).

    ``widths[i] < 0`` marks a hidden item; it appears in no line.
    """
    lines: list[list[int]] = []
    current: list[int] = []
    used = 0.0
    for index, width in enumerate(widths):
        if width < 0:
            continue
        if current and used + gap + width > available:
            lines.append(current)
            current = []
        used = width if not current else used + gap + width
        current.append(index)
    if current:
        lines.append(current)
    return lines


def line_offset(align: str, available: float, used: float) -> float:
    """Space before the first item of a line of width ``used``."""
    spare = max(0.0, available - used)
    if align == "center":
        return spare / 2
    if align == "end":
        return spare
    return 0.0
