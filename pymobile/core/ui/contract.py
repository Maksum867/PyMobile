"""Public, typed contract for a serialised PyMobile widget tree."""

from __future__ import annotations

from typing import TypeAlias, TypedDict

__all__ = ["WidgetNode", "WidgetProps", "StyleNode", "SerializedValue", "text_value"]

SerializedValue: TypeAlias = str | int | float | bool | list[object] | dict[str, object] | None


def text_value(value: object) -> str:
    """A text prop rendered as a string: ``None`` means empty, not ``"None"``.

    The device renderer reads text props with ``optString(key, "")``, so a JSON
    ``null`` becomes an empty string there. Python-side previews used plain
    ``str(...)`` and printed the word ``None`` instead — a field that looked
    filled had no text on the phone. Every renderer goes through this helper,
    and widgets coerce their own text props on the way in.
    """
    return "" if value is None else str(value)
WidgetProps: TypeAlias = dict[str, SerializedValue]
StyleNode: TypeAlias = dict[str, SerializedValue]


class WidgetNode(TypedDict, total=False):
    """The stable renderer boundary emitted by :meth:`Widget.to_dict`.

    ``type``, ``id``, ``visible``, ``enabled`` and ``props`` are always emitted;
    ``style`` and ``children`` are omitted when empty. ``total=False`` keeps the
    contract compatible with Python 3.10 without a typing_extensions runtime
    dependency.
    """

    type: str
    id: str
    visible: bool
    enabled: bool
    props: WidgetProps
    style: StyleNode
    children: list[WidgetNode]
    animation: dict[str, SerializedValue]
