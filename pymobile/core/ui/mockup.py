"""A picture of the screen as the phone draws it: :func:`render_mockup`.

:func:`~pymobile.core.ui.preview.render_png` photographs the *text* preview —
handy in a terminal, but it does not show what a user will see. This module
lays the widget tree out with the same rules as the Android renderer
(``ViewBuilder.java``: vertical containers stretch their children across,
rows wrap them, ``Expanded`` takes a weighted share of the free space, a
``Stack`` overlays full-size children…) and paints it with Material-style
widgets in the app's theme colours: grey raised buttons, underlined text
fields, switches, list rows, dialogs and the snackbar.

It is an approximation, not a screenshot. Fonts differ (the phone uses
Roboto; the mockup uses whatever sans-serif face the machine has), and
system colours such as the action bar and the tint of switches vary between
Android versions and vendors — here they follow the app's ``PRIMARY`` colour.
Sizes are in dp, exactly as in a ``Style``, so spacing and proportions match.

Pillow is the only requirement (``pip install Pillow``).
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

# Keep these compatibility exports: diagnostic tools use the measurement
# helpers, while implementation now lives in focused, acyclic modules.
from ._mockup_colors import _palette as _palette
from ._mockup_colors import _rgba as _rgba
from ._mockup_fonts import _Fonts as _Fonts
from ._mockup_layout import _APP_BAR, _STATUS_BAR, _Layout
from ._mockup_paint import _Box as _Box
from ._mockup_paint import _Painter
from .preview import _as_node

__all__ = ["render_mockup"]

_DEFAULT_WIDTH = 360
_DEFAULT_HEIGHT = 640


def render_mockup(
    widget_or_tree: Any,
    path: str | os.PathLike[str] | None = None,
    *,
    width: int = _DEFAULT_WIDTH,
    height: int | None = None,
    scale: float = 2.0,
    theme: Any = None,
    title: str = "",
    chrome: bool = True,
    assets: str | os.PathLike[str] | None = None,
) -> Any:
    """Draw the widget tree the way the phone would and save it as an image.

    ``widget_or_tree`` is a live widget, a screen's root, or a rendered tree
    (``StubBridge.last_tree``: its ``snackbar`` and ``theme`` entries are
    honoured). Sizes are in dp; ``scale`` is the pixel density of the image
    (2 → a 360 dp wide screen becomes 720 px).

    ``height=None`` grows the picture until the whole tree fits, which is the
    most useful view of a long, scrolling screen; pass ``height=640`` to see
    exactly the first screenful of a typical phone. ``theme`` is a
    :class:`~pymobile.core.ui.theme.Theme`, ``"light"``/``"dark"`` or a
    colour dict; by default the tree's own theme (or the light one) is used.
    ``title`` is shown in the action bar (the app name on a device);
    ``chrome=False`` leaves out the status and action bars. ``assets`` is the
    directory ``Image`` sources are resolved against.

    Returns ``path`` when one is given, otherwise the ``PIL.Image``.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError(
            "The PNG mockup needs Pillow; install it with `pip install Pillow`."
        ) from exc

    tree = widget_or_tree if isinstance(widget_or_tree, dict) else None
    node = _as_node(widget_or_tree)
    if width < 120:
        raise ValueError("width must be at least 120 dp")
    if scale <= 0:
        raise ValueError("scale must be positive")

    palette = _palette(theme if theme is not None else (tree or {}).get("theme"))
    fonts = _Fonts(ImageFont, scale)
    layout = _Layout(fonts, palette, Path(assets) if assets else None, scale)

    top = (_STATUS_BAR + _APP_BAR) if chrome else 0
    viewport = (height if height is not None else _DEFAULT_HEIGHT) - top
    root = layout.layout(node, float(width), fill=True, force_h=None)
    if root is not None and root.outer_h < viewport:
        # The root view is MATCH_PARENT in both directions on the device, so
        # a short tree still gets the whole screen (Expanded, end alignment).
        root = layout.layout(node, float(width), fill=True, force_h=float(viewport))
    content_h = root.outer_h if root is not None else 0.0
    total_h = height if height is not None else top + max(viewport, math.ceil(content_h))

    image = Image.new(
        "RGB", (round(width * scale), round(total_h * scale)), palette["BACKGROUND"][:3]
    )
    painter = _Painter(ImageDraw, image, scale, fonts)
    if root is not None:
        painter.paint(root, 0.0, float(top))
    screen = (0.0, float(top), float(width), float(total_h - top))
    # Stacking order of the device: content, snackbar (part of the content
    # view), action bar; a dialog is a window of its own that dims the whole
    # activity — action bar included, status bar not.
    snackbar = (tree or {}).get("snackbar")
    if isinstance(snackbar, dict) and snackbar.get("message") is not None:
        layout.paint_snackbar(painter, snackbar, screen)
    if chrome:
        layout.paint_chrome(painter, float(width), title or str((tree or {}).get("screen") or ""))
    window = (
        0.0,
        float(_STATUS_BAR if chrome else 0),
        float(width),
        float(total_h - (_STATUS_BAR if chrome else 0)),
    )
    for dialog in layout.dialogs:
        layout.paint_dialog(painter, dialog, window)

    if path is None:
        return image
    image.save(os.fspath(path))
    return os.fspath(path)
