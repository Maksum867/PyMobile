"""Styling primitives.

Deliberately small: a handful of layout and colour attributes that map cleanly
onto Android view properties. Unset fields are dropped during serialisation so
the native side can apply its own defaults.
"""

from __future__ import annotations

import re
import types
from dataclasses import asdict, dataclass, replace
from typing import Any

__all__ = ["Style", "Color", "Align", "EdgeInsets"]

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: Named sizes a Style width/height accepts (mirrors the native renderer).
_DIMENSION_WORDS = frozenset({"match", "fill", "wrap", "match_parent", "wrap_content"})


class Color:
    """Named colours plus validation for ``#RGB``/``#RRGGBB``/``#AARRGGBB``."""

    PRIMARY = "#3F51B5"
    ACCENT = "#FF4081"
    BACKGROUND = "#FFFFFF"
    SURFACE = "#F5F5F5"
    TEXT = "#212121"
    TEXT_MUTED = "#757575"
    SUCCESS = "#2E7D32"
    WARNING = "#F9A825"
    ERROR = "#C62828"
    TRANSPARENT = "#00000000"

    @staticmethod
    def validate(value: str) -> str:
        """Return the colour unchanged, or raise :class:`ValueError`."""
        if not _HEX_COLOR.match(value):
            raise ValueError(f"invalid color {value!r}; expected #RGB, #RRGGBB or #AARRGGBB")
        return value.upper()


class Align:
    """Alignment constants shared by layouts and text.

    ``START``/``CENTER``/``END``/``SPACE_BETWEEN`` position children along a
    container's main axis. The cross axis (how a Row's children line up
    vertically, or how wide a Column's children are) uses the same names plus
    :attr:`STRETCH`, and is set through ``cross_align``.
    """

    START = "start"
    CENTER = "center"
    END = "end"
    SPACE_BETWEEN = "space_between"
    #: Cross-axis only: fill the container across the axis.
    STRETCH = "stretch"

    #: Values accepted by ``align`` (main axis).
    MAIN = (START, CENTER, END, SPACE_BETWEEN)
    #: Values accepted by ``cross_align``.
    CROSS = (START, CENTER, END, STRETCH)

    @classmethod
    def validate_main(cls, value: str) -> str:
        """Return a valid ``align`` value, or raise :class:`ValueError`."""
        if value not in cls.MAIN:
            raise ValueError(f"invalid align {value!r}; expected one of {', '.join(cls.MAIN)}")
        return value

    @classmethod
    def validate_cross(cls, value: str) -> str:
        """Return a valid ``cross_align`` value, or raise :class:`ValueError`."""
        if value not in cls.CROSS:
            raise ValueError(
                f"invalid cross_align {value!r}; expected one of {', '.join(cls.CROSS)}"
            )
        return value


class _FieldInsteadOfMethod(TypeError):
    """A dataclass field called like a constructor: ``EdgeInsets.top(8)``.

    ``top`` is a field, so the class attribute is a slot descriptor and the
    call died with ``'member_descriptor' object is not callable`` — a message
    that says nothing about what to write instead.
    """


class _FieldShorthand:
    """Stand-in for a slot descriptor on the class, so calling it explains itself."""

    __slots__ = ("_owner", "_name")

    def __init__(self, owner: type, name: str) -> None:
        self._owner = owner
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise _FieldInsteadOfMethod(
            f"{self._owner.__name__}.{self._name}(…) is not a constructor: "
            f"{self._name!r} is a field of the dataclass. Write "
            f"{self._owner.__name__}.only({self._name}=8) — or .all(8), "
            f".symmetric(horizontal=…, vertical=…) — instead."
        )

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        # Instance access keeps working: inset.top is the number. The slot
        # descriptor of the same name still lives on the class, so this hands
        # over to the descriptor protocol directly — the argument mypy wants
        # is the accessor itself, not this stand-in.
        descriptor = types.MemberDescriptorType
        return descriptor.__get__(descriptor, instance, owner)  # type: ignore[arg-type]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self._owner.__name__}.{self._name}: a field, not a method>"


class _FieldGuard(type):
    """Turn ``EdgeInsets.top(8)`` into a hint instead of a descriptor error.

    Only the *class* attribute is wrapped; ``inset.top`` on an instance still
    returns the number, and ``dataclasses.asdict``/``replace``/``fields`` — the
    framework's own serialisation path — keep seeing the plain slots.
    """

    def __getattribute__(cls, name: str) -> Any:
        value = super().__getattribute__(name)
        if isinstance(value, types.MemberDescriptorType):
            return _FieldShorthand(cls, name)
        return value


#: ``EdgeInsets.only()`` spellings that name an edge already in the dataclass:
#: logical start/end are left/right in this LTR-only layout.
_ONLY_ALIASES = {"leading": "left", "trailing": "right", "start": "left", "end": "right"}


@dataclass(frozen=True, slots=True)
class EdgeInsets(metaclass=_FieldGuard):
    """Padding or margin in density-independent pixels.

    The four sides are plain fields, so the named constructors are the way to
    say "only these sides": :meth:`only` (``only(top=8)``,
    ``only(leading=16, trailing=4)``), :meth:`all`, :meth:`symmetric`. Calling
    a field — ``EdgeInsets.top(8)`` — fails with a ``TypeError`` that names
    :meth:`only`, instead of the old ``'member_descriptor' object is not
    callable``.
    """

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def all(cls, value: int) -> EdgeInsets:
        """The same inset on every side."""
        return cls(value, value, value, value)

    @classmethod
    def symmetric(cls, *, horizontal: int = 0, vertical: int = 0) -> EdgeInsets:
        """Separate horizontal and vertical insets."""
        return cls(horizontal, vertical, horizontal, vertical)

    @classmethod
    def only(
        cls,
        *,
        left: int | None = None,
        top: int | None = None,
        right: int | None = None,
        bottom: int | None = None,
        leading: int | None = None,
        trailing: int | None = None,
        start: int | None = None,
        end: int | None = None,
        horizontal: int | None = None,
        vertical: int | None = None,
    ) -> EdgeInsets:
        """Inset only the sides named; every other side stays ``0``.

        This is what ``EdgeInsets.top(8)`` meant to say. ``leading``/``trailing``
        (and ``start``/``end``) are the left/right spellings, and
        ``horizontal``/``vertical`` group a pair the way :meth:`symmetric` does::

            EdgeInsets.only(top=8)                      # 0, 8, 0, 0
            EdgeInsets.only(top=8, bottom=12)           # 0, 8, 0, 12
            EdgeInsets.only(leading=16, trailing=4)     # 16, 0, 4, 0
            EdgeInsets.only(vertical=8)                 # 0, 8, 0, 8

        Naming the same side twice (``only(left=4, leading=4)``,
        ``only(top=8, vertical=8)``) is refused rather than silently letting
        one value win.
        """
        named: dict[str, int] = {}
        for name, value in (
            ("left", left),
            ("top", top),
            ("right", right),
            ("bottom", bottom),
            ("leading", leading),
            ("trailing", trailing),
            ("start", start),
            ("end", end),
            ("horizontal", horizontal),
            ("vertical", vertical),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"EdgeInsets.only({name}={value!r}): every side must be an int")
            if value < 0:
                raise ValueError(f"EdgeInsets.only({name}={value}): a side must not be negative")
            target = _ONLY_ALIASES.get(name, name)
            touched = (
                ("top", "bottom")
                if target == "vertical"
                else ("left", "right")
                if target == "horizontal"
                else (target,)
            )
            for side in touched:
                if side in named:
                    raise TypeError(
                        f"EdgeInsets.only() names {side!r} twice (as {name!r} and as another "
                        "argument). Name each side once: only(top=8), only(vertical=8) or "
                        "only(leading=8) — not two of them together."
                    )
                named[side] = value
        return cls(
            named.get("left", 0),
            named.get("top", 0),
            named.get("right", 0),
            named.get("bottom", 0),
        )

    def to_list(self) -> list[int]:
        """``[left, top, right, bottom]``."""
        return [self.left, self.top, self.right, self.bottom]

    def __bool__(self) -> bool:
        return any(self.to_list())


#: What ``Style(padding=...)`` / ``Style(margin=...)`` accept.
InsetsLike = EdgeInsets | int | float | tuple[int, int, int, int] | list[int]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_insets(name: str, value: object) -> EdgeInsets | None:
    """Normalise a padding/margin shorthand to :class:`EdgeInsets`.

    ``8`` is ``EdgeInsets.all(8)`` and ``(l, t, r, b)`` follows the
    :class:`EdgeInsets` argument order. A pair is rejected on purpose: CSS
    reads it as (vertical, horizontal), :meth:`EdgeInsets.symmetric` the
    other way round, and guessing wrong would be silent.
    """
    if value is None or isinstance(value, EdgeInsets):
        return value
    example = (
        f"write Style({name}=8), Style({name}=(16, 8, 16, 8)) for left, top, right, bottom, "
        f"or Style({name}=EdgeInsets.symmetric(horizontal=16, vertical=8))"
    )
    sides: list[int]
    if _is_number(value):
        sides = [round(value)] * 4  # type: ignore[call-overload]
    elif isinstance(value, (tuple, list)) and len(value) == 4 and all(map(_is_number, value)):
        sides = [round(side) for side in value]
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        raise TypeError(
            f"{name}={value!r} is ambiguous (vertical/horizontal or the other way round?); "
            "use EdgeInsets.symmetric(horizontal=..., vertical=...) or four numbers "
            "(left, top, right, bottom)"
        )
    else:
        raise TypeError(
            f"{name} must be a number of dp, an EdgeInsets or four numbers, "
            f"got {value!r}; {example}"
        )
    if any(side < 0 for side in sides):
        raise ValueError(f"{name} must not be negative, got {value!r}")
    return EdgeInsets(*sides)


@dataclass(frozen=True, slots=True)
class Style:
    """Visual attributes of a widget; ``None`` means "inherit the default".

    Besides the fixed ``width``/``height`` there is a set of constraints —
    ``min_width``, ``max_width``, ``min_height``, ``max_height`` and
    ``aspect_ratio`` — for the common "at least this big, never bigger than
    that" card, and for images that must keep a 16:9 shape.
    """

    background: str | None = None
    color: str | None = None
    font_size: int | None = None
    bold: bool = False
    italic: bool = False
    #: ``8`` or ``(left, top, right, bottom)`` are shorthands; after
    #: construction the attribute always holds an :class:`EdgeInsets`.
    padding: InsetsLike | None = None
    margin: InsetsLike | None = None
    width: int | str | None = None
    height: int | str | None = None
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    aspect_ratio: float | None = None
    align: str | None = None
    corner_radius: int | None = None
    elevation: int | None = None
    weight: float | None = None

    def __post_init__(self) -> None:
        # Frozen dataclass: normalise through object.__setattr__. Without this
        # Style(padding=8) was accepted and then crashed in to_dict().
        for name in ("padding", "margin"):
            object.__setattr__(self, name, _coerce_insets(name, getattr(self, name)))
        for value in (self.background, self.color):
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError(
                        f"color must be a hex string like '#RRGGBB', got {type(value).__name__!r}; "
                        f"write Style(color='#FF0000') instead of Style(color={value!r})"
                    )
                Color.validate(value)
        if self.font_size is not None:
            if not isinstance(self.font_size, (int, float)):
                raise TypeError(
                    f"font_size must be a number, got {type(self.font_size).__name__!r}; "
                    f"write Style(font_size=16) instead of Style(font_size={self.font_size!r})"
                )
            if self.font_size <= 0:
                raise ValueError("font_size must be positive")
        for name in ("min_width", "max_width", "min_height", "max_height"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        # A contradictory pair silently clips the widget to nothing on device,
        # which is far harder to debug than an exception here.
        if (
            self.min_width is not None
            and self.max_width is not None
            and self.min_width > self.max_width
        ):
            raise ValueError("min_width must not exceed max_width")
        if (
            self.min_height is not None
            and self.max_height is not None
            and self.min_height > self.max_height
        ):
            raise ValueError("min_height must not exceed max_height")
        if self.aspect_ratio is not None and self.aspect_ratio <= 0:
            raise ValueError("aspect_ratio must be positive")
        if (
            self.align is not None
            and self.align not in Align.MAIN
            and self.align not in Align.CROSS
        ):
            raise ValueError(f"invalid align {self.align!r}")
        for name, size in (("width", self.width), ("height", self.height)):
            if isinstance(size, int):
                if size <= 0:
                    raise ValueError(f"{name} must be a positive number of dp")
            elif isinstance(size, str):
                if size not in _DIMENSION_WORDS:
                    raise ValueError(
                        f"invalid {name} {size!r}; expected a positive number "
                        f"or one of: {', '.join(sorted(_DIMENSION_WORDS))}"
                    )
            elif size is not None:
                raise ValueError(
                    f"invalid {name} {size!r}; expected a positive number "
                    f"or one of: {', '.join(sorted(_DIMENSION_WORDS))}"
                )
        if self.weight is not None and self.weight < 0:
            raise ValueError("weight must not be negative")
        if self.elevation is not None and self.elevation < 0:
            raise ValueError("elevation must not be negative")
        if self.corner_radius is not None and self.corner_radius < 0:
            raise ValueError("corner_radius must not be negative")

    def merge(self, **overrides: Any) -> Style:
        """Return a copy with some attributes replaced."""
        return replace(self, **overrides)

    def to_dict(self) -> dict[str, Any]:
        """Serialise, skipping unset and falsy-default fields."""
        raw = asdict(self)
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if value is None or value is False:
                continue
            if key in ("padding", "margin"):
                insets = getattr(self, key)
                if insets:
                    result[key] = insets.to_list()
                continue
            result[key] = value
        return result
