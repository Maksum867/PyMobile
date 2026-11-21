"""Adaptive icon layers are drawn from the original artwork, not a density copy.

A 0.15.0 build fed ``mipmap-mdpi`` (48x48) to the Android 8+ layer writer, and
``Image.thumbnail`` never enlarges: the foreground at xxxhdpi held a 48px
bitmap that a 640dpi launcher interpolated across a 264px safe zone, so the
icon looked blurred. The background layer also dropped its alpha, which turned
transparent corners black under a square mask.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pymobile.compiler.backends import native
from pymobile.compiler.backends.native import NativeBackend
from pymobile.compiler.icon import LARGEST_LAYER_EDGE, prepare_icons, write_adaptive_icon
from pymobile.compiler.toolchain import Toolchain
from pymobile.core.config import ProjectConfig


def _disc(path: Path, size: int, colour: tuple[int, int, int]) -> None:
    """An opaque disc on a transparent canvas, so the corners stay transparent."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0, 0, size - 1, size - 1), fill=(*colour, 255))
    image.save(path)


def test_adaptive_layers_are_drawn_from_the_original_artwork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "logo.png"
    _disc(source, 512, (10, 120, 200))
    icons = prepare_icons(source, tmp_path / "res")
    assert icons.source == source

    captured: dict[str, Path] = {}

    def fake_write_adaptive_icon(source_path: Path, res_dir: Path) -> list[Path]:
        captured["source"] = source_path
        return []

    monkeypatch.setattr(native, "write_adaptive_icon", fake_write_adaptive_icon)
    monkeypatch.setattr(native, "_run", lambda *args, **kwargs: None)
    backend = NativeBackend(
        ProjectConfig(root=tmp_path, package="com.example.a"),
        Toolchain(tmp_path, tmp_path, tmp_path / "android.jar", tmp_path / "jdk"),
        tmp_path / "runtime",
    )

    backend.link_resources(tmp_path / "work", icons)

    assert captured["source"] == source


def test_the_background_layer_is_opaque_and_keeps_transparent_corners(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    _disc(source, 512, (220, 40, 40))

    written = write_adaptive_icon(source, tmp_path / "res")
    background = next(
        path
        for path in written
        if path.name == "ic_launcher_background.png" and "xxxhdpi" in path.parent.name
    )

    with Image.open(background) as image:
        assert image.mode == "RGB"
        corners = image.convert("RGBA")
        assert corners.getchannel("A").getextrema() == (255, 255)
        corner = corners.getpixel((0, 0))

    assert corner[:3] != (0, 0, 0), "a transparent corner must not become black"
    assert corner[0] > 128, "the corner keeps the artwork's own colour"


def test_a_source_smaller_than_the_largest_layer_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "small.png"
    _disc(source, 256, (255, 255, 255))

    icons = prepare_icons(source, tmp_path / "res")

    assert any(
        "256x256" in warning and str(LARGEST_LAYER_EDGE) in warning for warning in icons.warnings
    )


def test_a_source_large_enough_for_the_layers_is_not_reported(tmp_path: Path) -> None:
    source = tmp_path / "large.png"
    _disc(source, LARGEST_LAYER_EDGE, (255, 255, 255))

    icons = prepare_icons(source, tmp_path / "res")

    assert icons.warnings == []
