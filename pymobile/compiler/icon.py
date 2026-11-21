"""Application icon handling.

Rules:

* a custom icon given in the config is validated and resized to every density;
* when none is set, the packaged default Android-style icon is used;
* an **adaptive** icon (``mipmap-anydpi-v26``: a background layer, a foreground
  layer and a monochrome layer) is written next to the legacy squares, so
  Android 8+ masks the icon to the launcher's shape instead of shrinking a
  square into a white tile, and Android 13+ can tint the monochrome one;
* Pillow is optional — without it a valid single-density icon is still emitted,
  so a build never fails just because an image library is missing.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ResourceError
from ..log import get_logger
from ..resources import default_icon_path

__all__ = ["IconSet", "prepare_icons", "DENSITIES", "ADAPTIVE_DENSITIES", "write_adaptive_icon"]

_log = get_logger("compiler.icon")

#: Android mipmap densities and their icon edge length in pixels.
DENSITIES: dict[str, int] = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}

#: Edge length of each adaptive layer. The launcher shows the middle 72 of the
#: 108 units and may mask it to any shape, so the artwork is drawn at that size
#: and the safe zone keeps the visible part inside a circle of 66 units.
ADAPTIVE_DENSITIES: dict[str, int] = {
    "mdpi": 108,
    "hdpi": 162,
    "xhdpi": 216,
    "xxhdpi": 324,
    "xxxhdpi": 432,
}

#: Edge of the largest adaptive layer. A source icon smaller than this is
#: upscaled to draw the layers, which looks soft on high-density screens.
LARGEST_LAYER_EDGE = max(ADAPTIVE_DENSITIES.values())

_SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True, slots=True)
class IconSet:
    """Result of icon preparation."""

    source: Path
    is_default: bool
    files: dict[str, Path]
    #: Non-fatal problems worth surfacing to the user, e.g. a source smaller
    #: than the largest adaptive layer.
    warnings: list[str] = field(default_factory=list)

    @property
    def densities(self) -> list[str]:
        """Densities that were generated."""
        return sorted(self.files)


def _validate_source(icon: Path) -> None:
    """Fail early with actionable messages for a bad icon path."""
    if not icon.exists():
        raise ResourceError(
            f"Icon not found: {icon}",
            hint="Check the `icon` path in pymobile.toml, or remove it to use the default.",
        )
    if icon.suffix.lower() not in _SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
        raise ResourceError(
            f"Unsupported icon format: {icon.suffix or 'no extension'}",
            hint=f"Use one of: {supported}. A square PNG of at least 512x512 works best.",
        )
    if icon.stat().st_size == 0:
        raise ResourceError(f"Icon file is empty: {icon}")


def prepare_icons(icon: Path | None, output_dir: Path) -> IconSet:
    """Write ``ic_launcher.png`` into ``mipmap-*`` folders under ``output_dir``.

    Falls back to the bundled default when ``icon`` is ``None``.
    """
    is_default = icon is None
    source = icon if icon is not None else default_icon_path()
    _validate_source(source)

    output_dir.mkdir(parents=True, exist_ok=True)
    resized = _resize_all(source, output_dir)
    if resized is None:
        resized = _copy_all(source, output_dir)
        _log.debug("Pillow unavailable: icon copied without resizing")

    warnings = _source_warnings(source)
    _log.debug("prepared %d icon densities (default=%s)", len(resized), is_default)
    return IconSet(source=source, is_default=is_default, files=resized, warnings=warnings)


def _source_warnings(source: Path) -> list[str]:
    """Warn when the source cannot fill the largest adaptive layer sharply."""
    try:
        from PIL import Image
    except ImportError:
        return []
    with Image.open(source) as image:
        width, height = image.size
    if min(width, height) >= LARGEST_LAYER_EDGE:
        return []
    return [
        f"icon source is {width}x{height}; the largest adaptive layer is "
        f"{LARGEST_LAYER_EDGE}x{LARGEST_LAYER_EDGE}, so it is upscaled and may look soft"
    ]


def _resize_all(source: Path, output_dir: Path) -> dict[str, Path] | None:
    """Resize the icon for every density; ``None`` when Pillow is missing."""
    try:
        from PIL import Image
    except ImportError:
        return None

    # Pillow >= 9.1 exposes the filters via Image.Resampling; keep the old
    # attribute as a fallback so older installations still work.
    resample = getattr(Image, "Resampling", Image).LANCZOS

    files: dict[str, Path] = {}
    with Image.open(source) as image:
        rgba = image.convert("RGBA")
        for density, size in DENSITIES.items():
            target_dir = output_dir / f"mipmap-{density}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "ic_launcher.png"
            rgba.resize((size, size), resample).save(target, format="PNG", optimize=True)
            files[density] = target
    return files


def _copy_all(source: Path, output_dir: Path) -> dict[str, Path]:
    """Copy the icon unchanged into each density folder."""
    files: dict[str, Path] = {}
    for density in DENSITIES:
        target_dir = output_dir / f"mipmap-{density}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "ic_launcher.png"
        shutil.copyfile(source, target)
        files[density] = target
    return files


def write_adaptive_icon(source: Path, res_dir: Path) -> list[Path]:
    """Write an Android 8+ adaptive icon built from ``source``.

    Three layers are produced per density:

    * ``ic_launcher_background.png`` — ``source`` scaled to fill the canvas and
      blurred, so a photo icon still reads as one colour behind the artwork;
      transparent areas are filled with the artwork's mean colour so a square
      mask never shows black corners;
    * ``ic_launcher_foreground.png`` — ``source`` fitted inside the 66/108 safe
      zone, so no launcher mask can clip it;
    * ``ic_launcher_monochrome.png`` — the same shape in white on transparent,
      which Android 13+ tints to the user's theme.

    ``mipmap-anydpi-v26/ic_launcher.xml`` ties them together; the legacy
    ``mipmap-<density>/icon.png`` files stay for Android 7, which ignores
    ``-anydpi-v26``. Returns the files written, also when Pillow is missing
    (then the foreground is a copy of ``source`` and the XML still works).
    """
    written: list[Path] = []
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        Image = None  # type: ignore[assignment]
        ImageStat = None  # type: ignore[assignment]

    for density, size in ADAPTIVE_DENSITIES.items():
        folder = res_dir / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        if Image is None:
            for name in ("ic_launcher_background.png", "ic_launcher_foreground.png"):
                target = folder / name
                shutil.copyfile(source, target)
                written.append(target)
            continue

        resample = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")

            # A launcher that masks the icon to a square shows whatever sits
            # behind a transparent corner; dropping the alpha would turn those
            # corners black, so they are filled with the artwork's own colour.
            alpha = rgba.getchannel("A")
            if alpha.getbbox() is None:
                fill = (255, 255, 255)
            else:
                mean = ImageStat.Stat(rgba.convert("RGB"), mask=alpha).mean
                fill = (round(mean[0]), round(mean[1]), round(mean[2]))

            background = rgba.resize((size, size), resample)
            background = background.filter(ImageFilter.GaussianBlur(radius=size / 24))
            background = Image.alpha_composite(
                Image.new("RGBA", (size, size), (*fill, 255)),
                background,
            )
            background_path = folder / "ic_launcher_background.png"
            background.convert("RGB").save(background_path, format="PNG", optimize=True)
            written.append(background_path)

            safe = round(size * 66 / 108)
            foreground = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            scaled = rgba.copy()
            scaled.thumbnail((safe, safe), resample)
            foreground.alpha_composite(
                scaled,
                ((size - scaled.width) // 2, (size - scaled.height) // 2),
            )
            foreground_path = folder / "ic_launcher_foreground.png"
            foreground.save(foreground_path, format="PNG", optimize=True)
            written.append(foreground_path)

            # A monochrome layer is an alpha mask: Android ignores its colours
            # and tints whatever is opaque.
            alpha = foreground.getchannel("A")
            monochrome = Image.new("RGBA", (size, size), (255, 255, 255, 0))
            monochrome.putalpha(alpha)
            monochrome_path = folder / "ic_launcher_monochrome.png"
            monochrome.save(monochrome_path, format="PNG", optimize=True)
            written.append(monochrome_path)

    anydpi = res_dir / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    xml = anydpi / "ic_launcher.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n'
        "</adaptive-icon>\n",
        encoding="utf-8",
    )
    written.append(xml)
    _log.debug("wrote %d adaptive icon layers", len(written))
    return written


def round_icon_xml() -> str:
    """``mipmap-anydpi-v26/ic_launcher_round.xml`` — the same layers, round mask.

    Launchers that ask for ``android:roundIcon`` fall back to nothing when the
    resource is absent, so a round-masked launcher drew the square legacy icon.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n'
        "</adaptive-icon>\n"
    )
