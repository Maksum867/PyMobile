"""Application themes: named colour palettes applied app-wide.

A :class:`Theme` maps the framework's semantic colour names (``PRIMARY``,
``ACCENT``, ``BACKGROUND``, ``SURFACE``, ``TEXT``, ``TEXT_MUTED``, ``SUCCESS``,
``WARNING``, ``ERROR``) to concrete ``#RRGGBB``/``#AARRGGBB`` values. Two
presets ship: :data:`Theme.LIGHT` and :data:`Theme.DARK`.

Give an app a theme and read resolved colours with :meth:`Theme.color` (or the
shorthand ``app.theme["PRIMARY"]``). ``Color.PRIMARY`` itself is a constant;
pass ``app.theme["PRIMARY"]`` into a ``Style`` to follow the active theme. A
theme change redraws the visible screen just like a language change.
"""

from __future__ import annotations

from .style import Color

__all__ = ["Theme", "Space", "TextScale"]

#: Semantic colour names a theme may (or may not) override.
_SEMANTIC = (
    "PRIMARY",
    "ACCENT",
    "BACKGROUND",
    "SURFACE",
    "TEXT",
    "TEXT_MUTED",
    "SUCCESS",
    "WARNING",
    "ERROR",
    "TRANSPARENT",
)


class Space:
    """The spacing scale: ``Space.sm`` is 8 dp, and so on.

    Fifteen hard-coded ``font_size``/``spacing`` numbers scattered through an
    app are a design system nobody wrote down. These are the multiples the
    platform itself uses::

        Column(..., spacing=Space.md)
        Label("…", style=Style(font_size=TextScale.body))
    """

    xs = 4
    sm = 8
    md = 12
    lg = 16
    xl = 24
    xxl = 32

    ALL = (("xs", xs), ("sm", sm), ("md", md), ("lg", lg), ("xl", xl), ("xxl", xxl))


class TextScale:
    """Font sizes of the Material type scale, in dp.

    Not every size is here — only the ones an app actually uses, so a heading
    and a caption stay consistent across screens.
    """

    caption = 12
    body = 14
    body_large = 16
    subtitle = 18
    title = 22
    headline = 28
    display = 34

    ALL = (
        ("caption", caption),
        ("body", body),
        ("body_large", body_large),
        ("subtitle", subtitle),
        ("title", title),
        ("headline", headline),
        ("display", display),
    )


class Theme:
    """A named set of colours resolved through the semantic palette.

    ``colors`` maps semantic names (or any name) to ``#RRGGBB`` hex strings.
    Unknown semantic names fall back to the built-in :class:`Color` defaults, so
    a partial theme (e.g. only ``BACKGROUND`` and ``TEXT`` for a dark mode) is
    enough.
    """

    __slots__ = ("name", "_colors")

    def __init__(self, name: str, colors: dict[str, str] | None = None) -> None:
        self.name = name
        self._colors: dict[str, str] = {}
        if colors:
            for key, value in colors.items():
                self._colors[key.upper()] = Color.validate(value)

    # -- presets -----------------------------------------------------------
    @classmethod
    def light(cls) -> Theme:
        """The default light palette (mirrors the built-in ``Color`` values)."""
        return cls(
            "light",
            {
                "PRIMARY": Color.PRIMARY,
                "ACCENT": Color.ACCENT,
                "BACKGROUND": Color.BACKGROUND,
                "SURFACE": Color.SURFACE,
                "TEXT": Color.TEXT,
                "TEXT_MUTED": Color.TEXT_MUTED,
                "SUCCESS": Color.SUCCESS,
                "WARNING": Color.WARNING,
                "ERROR": Color.ERROR,
            },
        )

    @classmethod
    def dark(cls) -> Theme:
        """A built-in dark palette (Material-ish dark colours)."""
        return cls(
            "dark",
            {
                "PRIMARY": "#5C6BC0",
                "ACCENT": "#FF80AB",
                "BACKGROUND": "#121212",
                "SURFACE": "#1E1E1E",
                "TEXT": "#EEEEEE",
                "TEXT_MUTED": "#9E9E9E",
                "SUCCESS": "#81C784",
                "WARNING": "#FFB74D",
                "ERROR": "#EF5350",
            },
        )

    @classmethod
    def dark_from(cls, light: Theme, *, name: str = "dark") -> Theme:
        """A dark palette derived from a light one, instead of invented by hand.

        Every colour is kept as a *hue* and re-lit for a dark background:
        ``BACKGROUND``/``SURFACE`` become near-black tints of the light
        background's own hue, text becomes near-white, and the accent colours
        are lightened until they read on the dark surface. A well-designed
        light theme therefore produces a usable dark one — no second palette to
        maintain, and no "I forgot to change one colour" bug::

            light = Theme("mocha", {"PRIMARY": "#6F4E37", "BACKGROUND": "#FFFBF5"})
            dark = Theme.dark_from(light)
        """
        if not isinstance(light, Theme):
            raise TypeError("dark_from expects a Theme")

        def channels(colour: str) -> tuple[int, int, int]:
            value = colour.lstrip("#")
            if len(value) == 8:  # strip the alpha the palette allows
                value = value[2:]
            if len(value) == 3:
                value = "".join(character * 2 for character in value)
            return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

        def hexed(colour: tuple[int, int, int]) -> str:
            return "#{:02X}{:02X}{:02X}".format(
                *(max(0, min(255, round(channel))) for channel in colour)
            )

        def mix(base: tuple[int, int, int], over: tuple[int, int, int], amount: float) -> str:
            return hexed(
                tuple(base[i] * (1 - amount) + over[i] * amount for i in range(3))  # type: ignore[arg-type]
            )

        def luminance(colour: tuple[int, int, int]) -> float:
            def linear(channel: float) -> float:
                channel /= 255.0
                if channel <= 0.03928:
                    return channel / 12.92
                # pow() rather than ``**``: the operator is inferred as Any here
                return float(pow((channel + 0.055) / 1.055, 2.4))

            weighted: float = (
                0.2126 * linear(colour[0])
                + 0.7152 * linear(colour[1])
                + 0.0722 * linear(colour[2])
            )
            return weighted

        background = channels(light.color("BACKGROUND"))
        black = (0, 0, 0)
        white = (255, 255, 255)
        colours: dict[str, str] = {
            # Keep the light theme's hue in the dark surfaces: a warm paper
            # theme yields a warm dark one, not a blue-grey slab.
            "BACKGROUND": mix(black, background, 0.09),
            "SURFACE": mix(black, background, 0.16),
            "TEXT": mix(black, white, 0.93),
            "TEXT_MUTED": mix(black, white, 0.62),
        }
        for key in ("PRIMARY", "ACCENT", "SUCCESS", "WARNING", "ERROR"):
            colour = channels(light.color(key))
            # Lighten accents until they clear 4.5:1 against the dark surface;
            # a colour already bright enough is kept as it is.
            for amount in (0.0, 0.1, 0.2, 0.3, 0.4):
                candidate = (
                    round(colour[0] + (255 - colour[0]) * amount),
                    round(colour[1] + (255 - colour[1]) * amount),
                    round(colour[2] + (255 - colour[2]) * amount),
                )
                ratio = (luminance(candidate) + 0.05) / (luminance((18, 18, 18)) + 0.05)
                colours[key] = hexed(candidate)
                if ratio >= 4.5:
                    break
        return cls(name, colours)

    # -- access ------------------------------------------------------------
    @property
    def is_dark(self) -> bool:
        """Whether this is the dark preset (or a custom theme named dark)."""
        return self.name.lower() == "dark"

    def color(self, name: str) -> str:
        """Resolve a semantic colour name to a hex string.

        Returns the themed value when present, otherwise the built-in
        :class:`Color` default for that name (so ``PRIMARY`` etc. always work),
        and finally raises ``ValueError`` for an unknown name.
        """
        key = name.upper()
        if key in self._colors:
            return self._colors[key]
        fallback = getattr(Color, key, None)
        if fallback is not None:
            return str(fallback)
        raise ValueError(f"unknown colour {name!r}")

    def as_dict(self) -> dict[str, str]:
        """All resolved colours as a ``{NAME: hex}`` mapping."""
        return {key: self.color(key) for key in _SEMANTIC}

    def __getitem__(self, name: str) -> str:
        return self.color(name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Theme {self.name!r} colours={len(self._colors)}>"


#: Convenience constants.
LIGHT = Theme.light()
DARK = Theme.dark()
