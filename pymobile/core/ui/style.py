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

__all__ = ["Style", "Color", "Align", "EdgeInsets", "Border", "Gradient"]

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: Named sizes a Style width/height accepts (mirrors the native renderer).
_DIMENSION_WORDS = frozenset({"match", "fill", "wrap", "match_parent", "wrap_content"})

#: A font family is a comma-separated list of plain names (``"Georgia, serif"``).
#: Quoted names and ``;`` are refused: the value is written into the device's
#: ``Typeface.create`` and into inline CSS, where punctuation would break out.
_FONT_NAME = r"[A-Za-z0-9][A-Za-z0-9 \-]{0,63}"
_FONT_FAMILY = re.compile(rf"^{_FONT_NAME}(?: *, *{_FONT_NAME})*$")

#: Gradient directions, each mapped to CSS ``linear-gradient`` corner points.
#: Android draws its gradients top-to-bottom unless told otherwise, so every
#: renderer needs the same vocabulary to agree on the picture.
GRADIENT_DIRECTIONS: dict[str, tuple[float, float, float, float]] = {
    "to_bottom": (0.5, 0.0, 0.5, 1.0),
    "to_top": (0.5, 1.0, 0.5, 0.0),
    "to_right": (0.0, 0.5, 1.0, 0.5),
    "to_left": (1.0, 0.5, 0.0, 0.5),
    "to_bottom_right": (0.0, 0.0, 1.0, 1.0),
    "to_top_right": (0.0, 1.0, 1.0, 0.0),
    "to_bottom_left": (1.0, 0.0, 0.0, 1.0),
    "to_top_left": (1.0, 1.0, 0.0, 0.0),
}

#: ``text_transform`` values; the same four CSS offers, spelled the same way.
TEXT_TRANSFORMS = ("none", "uppercase", "lowercase", "capitalize")

#: ``text_overflow`` values. ``ellipsis`` clips a word that does not fit and
#: marks the cut with "…"; ``clip`` just cuts. The CSS spellings are accepted
#: as aliases because that is what people type first.
TEXT_OVERFLOWS = {
    "ellipsis": "ellipsis",
    "clip": "clip",
    "text-ellipsis": "ellipsis",
    "text-clip": "clip",
    "fade": "ellipsis",
}


class Color:
    """Named colours plus validation for ``#RGB``/``#RRGGBB``/``#AARRGGBB``."""

    PRIMARY = "#3F51B5"
    ON_PRIMARY = "#FFFFFF"
    ACCENT = "#FF4081"
    BACKGROUND = "#FFFFFF"
    SURFACE = "#F5F5F5"
    TEXT = "#212121"
    TEXT_MUTED = "#6F6F6F"
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


@dataclass(frozen=True, slots=True)
class Border:
    """A stroke drawn around a widget.

    ``width`` is in dp and is measured inside the widget's own box, the way
    Android's ``GradientDrawable.setStroke`` does it, so adding a border never
    changes a layout: ``Border(color="#E0E0E0")`` is a hairline, and
    ``Border(width=2, color=…)`` a heavier one.

    ``sides`` restricts the stroke to a subset (``Border(color=…, sides="bottom")``)
    for the "row with a rule under it" case; the default draws all four.
    """

    width: float = 1
    color: str = "#E0E0E0"
    #: Any combination of ``top``, ``right``, ``bottom``, ``left``.
    sides: str = "top right bottom left"

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, (int, float)):
            raise TypeError(f"border width must be a number of dp, got {self.width!r}")
        if self.width < 0:
            raise ValueError("border width must not be negative")
        if not isinstance(self.color, str):
            raise TypeError(f"border color must be a hex string, got {self.color!r}")
        Color.validate(self.color)

    @property
    def side_set(self) -> frozenset[str]:
        """The sides named, normalised; an empty border names none."""
        if self.width == 0:
            return frozenset()
        words = self.sides.replace(",", " ").split()
        unknown = [word for word in words if word not in ("top", "right", "bottom", "left")]
        if unknown:
            raise ValueError(
                f"unknown border side {unknown[0]!r}; expected a mix of "
                "top, right, bottom, left (all four by default)"
            )
        return frozenset(words)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON the renderers read."""
        return {
            "width": self.width,
            "color": Color.validate(self.color),
            "sides": sorted(self.side_set),
        }


@dataclass(frozen=True, slots=True)
class Gradient:
    """A linear colour ramp, interpolated in sRGB.

    Two colours are the common case (``Gradient("#3F51B5", "#1A237E")``); a
    third may be added to hold the middle of the ramp, and ``stops`` gives the
    full control when the app has real design values::

        Gradient("#FFFFFF", "#3F51B5", direction="to_bottom_right")
        Gradient(stops=((0.0, "#FFFFFF"), (0.6, "#E8EAF6"), (1.0, "#3F51B5")))

    ``direction`` is one of :data:`GRADIENT_DIRECTIONS` — ``to_bottom`` when
    omitted, which is also what Android draws by default.
    """

    #: Positional spelling: the first two are the ends, the third the middle.
    start: str = ""
    end: str = ""
    middle: str | None = None
    #: ``((offset, colour), ...)`` with offsets from 0 to 1; overrides the three
    #: positional colours when given.
    stops: tuple[tuple[float, str], ...] | None = None
    direction: str = "to_bottom"

    def __post_init__(self) -> None:
        direction = str(self.direction).replace("-", "_").lower().strip()
        if direction not in GRADIENT_DIRECTIONS:
            raise ValueError(
                f"invalid gradient direction {self.direction!r}; expected one of: "
                + ", ".join(GRADIENT_DIRECTIONS)
            )
        object.__setattr__(self, "direction", direction)
        if self.stops is not None:
            pairs: list[tuple[float, str]] = []
            for index, stop in enumerate(self.stops):
                if len(stop) != 2:  # type: ignore[arg-type]
                    raise TypeError(f"gradient stop {index} must be (offset, colour), got {stop!r}")
                offset, colour = stop
                if isinstance(offset, bool) or not isinstance(offset, (int, float)):
                    raise TypeError(
                        f"gradient stop {index}: offset must be a number, got {offset!r}"
                    )
                if not 0 <= offset <= 1:
                    raise ValueError(f"gradient stop {index}: offset must be from 0 to 1")
                if not isinstance(colour, str):
                    raise TypeError(f"gradient stop {index}: colour must be a hex string")
                Color.validate(colour)
                pairs.append((float(offset), colour))
            if len(pairs) < 2:
                raise ValueError("a gradient needs at least two stops")
            pairs.sort(key=lambda pair: pair[0])
            if pairs[0][0] != 0.0 or pairs[-1][0] != 1.0:
                raise ValueError(
                    "gradient stops must start at 0 and end at 1; without an end "
                    "colour the last one is stretched, which hides the mistake"
                )
            object.__setattr__(self, "stops", tuple(pairs))
            return
        if not self.start or not self.end:
            raise TypeError(
                "Gradient needs a start and an end colour: "
                "Gradient('#FFFFFF', '#3F51B5') or Gradient(stops=((0, '#FFFFFF'), (1, '#3F51B5')))"
            )
        for value in (self.start, self.end, self.middle):
            if value is not None:
                Color.validate(value)

    def ramp(self) -> tuple[tuple[float, str], ...]:
        """The stops as ``((offset, colour), ...)``, whatever spelling was used."""
        if self.stops is not None:
            return self.stops
        if self.middle:
            return ((0.0, self.start), (0.5, self.middle), (1.0, self.end))
        return ((0.0, self.start), (1.0, self.end))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON the renderers read."""
        return {
            "stops": [[offset, Color.validate(colour)] for offset, colour in self.ramp()],
            "direction": self.direction,
        }


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
    """Visual attributes of a widget; ``None`` means "inherit the default"."""

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
    # -- surface -----------------------------------------------------------
    #: ``0`` is fully transparent: for a whole screen it means "no background
    #: of my own", so a themed parent shows through.
    opacity: float | None = None
    #: Stroke around the widget; width ``0`` means no border.
    border: Border | None = None
    #: A two-colour gradient. ``background`` keeps winning when both are set:
    #: one of the two has to be the sRGB value the previews fall back on.
    gradient: Gradient | None = None
    # -- type --------------------------------------------------------------
    #: Extra space between lines, in dp (not a multiplier, unlike CSS).
    line_height: float | None = None
    #: Extra space between letters, in dp; may be negative to tighten.
    letter_spacing: float | None = None
    #: Typographic name: ``"serif"``, ``"monospace"``, ``"sans-serif"`` or a
    #: system family list such as ``"Georgia, serif"``.
    font_family: str | None = None
    #: ``"none"``, ``"uppercase"``, ``"lowercase"`` or ``"capitalize"``.
    text_transform: str | None = None
    #: How much of the text may be shown where the line ends (see
    #: :meth:`truncate`); also accepts the CSS spellings.
    text_overflow: str | None = None
    max_lines: int | None = None
    underline: bool = False
    strikethrough: bool = False

    def __post_init__(self) -> None:
        # Frozen dataclass: normalise through object.__setattr__. Without this
        # Style(padding=8) was accepted and then crashed in to_dict().
        for name in ("padding", "margin"):
            object.__setattr__(self, name, _coerce_insets(name, getattr(self, name)))
        for name in ("border", "gradient"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, (Border, Gradient)):
                raise TypeError(
                    f"{name} must be a {name.title()}, got {type(value).__name__!r}; "
                    f"write Style({name}={name.title()}(...))"
                )
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
        if self.opacity is not None:
            if isinstance(self.opacity, bool) or not _is_number(self.opacity):
                raise TypeError(f"opacity must be a number from 0 to 1, got {self.opacity!r}")
            if not 0 <= float(self.opacity) <= 1:
                raise ValueError("opacity must be from 0 (invisible) to 1 (opaque)")
        for name in ("line_height", "letter_spacing"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not _is_number(value):
                raise TypeError(f"{name} must be a number of dp, got {value!r}")
            if name == "line_height" and float(value) < 0:
                raise ValueError("line_height must not be negative")
        if self.font_family is not None:
            if not isinstance(self.font_family, str) or not _FONT_FAMILY.match(self.font_family):
                raise ValueError(
                    f"invalid font_family {self.font_family!r}; expected a font name or a "
                    "comma-separated list of names, e.g. 'serif' or 'Georgia, serif'"
                )
            object.__setattr__(self, "font_family", self.font_family.strip())
        if self.text_transform is not None:
            transform = str(self.text_transform).lower().strip()
            if transform not in TEXT_TRANSFORMS:
                raise ValueError(
                    f"invalid text_transform {self.text_transform!r}; expected one of: "
                    + ", ".join(TEXT_TRANSFORMS)
                )
            object.__setattr__(self, "text_transform", transform)
        if self.text_overflow is not None:
            overflow = TEXT_OVERFLOWS.get(str(self.text_overflow).lower().strip())
            if overflow is None:
                raise ValueError(
                    f"invalid text_overflow {self.text_overflow!r}; expected one of: ellipsis, clip"
                )
            object.__setattr__(self, "text_overflow", overflow)
        if self.max_lines is not None:
            if isinstance(self.max_lines, bool) or not isinstance(self.max_lines, int):
                raise TypeError(f"max_lines must be an int, got {self.max_lines!r}")
            if self.max_lines < 1:
                raise ValueError("max_lines must be at least 1")
            if self.text_overflow is None:
                # Cutting a paragraph short without saying how is what the
                # device does anyway; say it out loud and the previews agree.
                object.__setattr__(self, "text_overflow", "ellipsis")
        for name in ("underline", "strikethrough"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")

    def merge(self, **overrides: Any) -> Style:
        """Return a copy with some attributes replaced."""
        return replace(self, **overrides)

    def derive(self, **overrides: Any) -> Style:
        """Alias of :meth:`merge`, named the way design systems talk.

        ``base.derive(background=brand)`` reads better than
        ``base.merge(background=brand)`` at a call site, and both keep the rest
        of the style intact.
        """
        return replace(self, **overrides)

    @property
    def url(self) -> str | None:
        """The colour a preview would paint behind this widget, if any.

        ``Gradient`` wins by design: it is the more specific instruction, and a
        flat colour is what the app falls back on where gradients are absent.
        """
        if self.gradient is not None:
            return self.gradient.ramp()[0][1]
        if self.background is not None:
            return self.background
        if self.border is not None and self.border.width:
            return self.border.color
        return None

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
            if key == "border":
                border = self.border
                if border is not None and border.width and border.side_set:
                    result[key] = border.to_dict()
                continue
            if key == "gradient":
                gradient = self.gradient
                if gradient is not None:
                    result[key] = gradient.to_dict()
                continue
            result[key] = value
        return result
