"""Typed shared contract for measurement and painting collaborators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._mockup_colors import _Colour
from ._mockup_fonts import _Fonts
from ._mockup_paint import _Box, _Painter


class _MockupContext(ABC):
    fonts: _Fonts
    p: dict[str, Any]
    assets: Path | None
    scale: float

    @abstractmethod
    def layout(
        self,
        node: dict[str, Any],
        max_w: float,
        *,
        fill: bool,
        force_h: float | None = None,
        axis: str = "v",
    ) -> _Box | None: ...

    @abstractmethod
    def _padding(
        self, style: dict[str, Any], default: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]: ...

    @abstractmethod
    def _divider(self, props: dict[str, Any], length: float, *, vertical_parent: bool) -> _Box: ...

    @abstractmethod
    def _flow(
        self, boxes: list[_Box], width: float, gap: float, run_gap: float, align: str
    ) -> tuple[list[tuple[float, float, _Box]], float, float]: ...

    @abstractmethod
    def _text_box(
        self,
        text: str,
        size: float,
        colour: _Colour,
        style: dict[str, Any],
        w: float,
        fill: bool,
        *,
        pad: tuple[float, float, float, float] = (0, 0, 0, 0),
        bold: bool = False,
        min_h: float = 0.0,
        min_w: float = 0.0,
        align: str = "",
        background: Callable[[_Painter, float, float, _Box], None] | None = None,
    ) -> _Box: ...
