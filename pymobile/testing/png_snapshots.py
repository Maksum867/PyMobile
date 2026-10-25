"""Golden-*image* checks: a screen's mockup PNG compared pixel by pixel.

``assert_snapshot`` compares the text picture, which catches a layout change
but not a colour one: a button that turned grey, a chip that lost its
background, a theme that stopped applying. This module fills that gap with the
same workflow — the golden file lives next to the test, a missing one is
written locally and refused in CI, and ``--pymobile-update-snapshots`` rewrites
it.

    def test_home(pymobile_session, pymobile_png):
        pymobile_session.start(Home())
        pymobile_png()                     # snapshots/<module>__<test>.png

The comparison is deliberately tolerant of nothing by default: the mockup is
drawn from the widget tree with a fixed scale, so it is deterministic. Two
knobs exist for the cases where that is not quite true (a font that renders
one pixel differently, a machine without the same fallback face):
``tolerance`` (per-channel difference that still counts as equal) and
``max_diff_ratio`` (how much of the picture may differ).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from ..core.bridge import StubBridge
from ..core.ui.screen import Screen
from .snapshots import UPDATE_ENV, UPDATE_HINT, running_in_ci, snapshot_name

__all__ = ["PngSnapshotChecker", "assert_png_snapshot", "png_snapshot_path"]


def png_snapshot_path(test_file: str | Path, name: str) -> Path:
    """``snapshots/<module>__<name>.png`` next to the test file."""
    test_path = Path(test_file)
    return test_path.parent / "snapshots" / f"{test_path.stem}__{name}.png"


def _render(tree: Any, *, width: int, height: int, scale: float, theme: Any) -> Any:
    """Draw the mockup for ``tree``; Pillow is an optional dependency.

    A ``Screen`` is serialised first, exactly like :class:`PngSnapshotChecker`
    does, so ``pymobile_png(Screen())`` and ``assert_png_snapshot(Screen())``
    produce the same picture instead of two subtly different ones.
    """
    if isinstance(tree, Screen):
        tree = tree.to_dict()
    try:
        from ..core.ui.mockup import render_mockup
    except ImportError as exc:  # pragma: no cover - Pillow missing
        raise AssertionError(
            "PNG snapshots need Pillow: pip install 'pymobile-framework[icons]'"
        ) from exc
    return render_mockup(
        tree,
        width=width,
        height=height,
        scale=scale,
        theme=theme,
    )


def _difference(expected: Any, actual: Any, tolerance: int) -> tuple[int, int]:
    """``(differing pixels, total pixels)`` for two RGB images."""
    if expected.size != actual.size:
        return expected.size[0] * expected.size[1], expected.size[0] * expected.size[1]
    left = expected.convert("RGB")
    right = actual.convert("RGB")
    total = left.size[0] * left.size[1]
    left_bytes = left.tobytes()
    right_bytes = right.tobytes()
    if left_bytes == right_bytes:
        return 0, total
    if tolerance <= 0:
        differing = sum(
            1
            for index in range(0, len(left_bytes), 3)
            if left_bytes[index : index + 3] != right_bytes[index : index + 3]
        )
        return differing, total
    differing = 0
    for index in range(0, len(left_bytes), 3):
        if any(
            abs(left_bytes[index + channel] - right_bytes[index + channel]) > tolerance
            for channel in range(3)
        ):
            differing += 1
    return differing, total


def assert_png_snapshot(
    tree: Any,
    test_file: str | Path,
    name: str,
    *,
    width: int = 360,
    height: int = 640,
    scale: float = 2.0,
    theme: Any = None,
    tolerance: int = 0,
    max_diff_ratio: float = 0.0,
    update: bool = False,
    ci: bool | None = None,
    hint: str = UPDATE_HINT,
) -> Path:
    """Compare the mockup of ``tree`` with its golden PNG; return the path.

    A missing golden file is written (and the test passes with a warning),
    unless ``update`` or CI says otherwise. When the picture differs, the test
    fails with the number of differing pixels and a ``*.diff.png`` next to the
    golden file showing the new render, so the change can be reviewed by eye
    before it is accepted.
    """
    path = png_snapshot_path(test_file, snapshot_name(name))
    image = _render(tree, width=width, height=height, scale=scale, theme=theme)
    exists = path.exists()
    in_ci = running_in_ci() if ci is None else ci
    if not exists and not update and in_ci:
        raise AssertionError(
            f"PNG snapshot {path.name} does not exist, and CI does not create them: "
            f"generate it locally with `pytest --pymobile-update-snapshots` "
            f"(or {UPDATE_ENV}=1) and commit it"
        )
    if update or not exists:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        if not update:
            warnings.warn(
                f"PNG snapshot {path.name} did not exist and has been written: "
                "review and commit it",
                UserWarning,
                stacklevel=2,
            )
        return path

    from PIL import Image

    with Image.open(path) as golden:
        expected = golden.convert("RGB")
    differing, total = _difference(expected, image, tolerance)
    ratio = differing / total if total else 0.0
    if ratio <= max_diff_ratio:
        return path

    new_path = path.with_name(f"{path.stem}.new.png")
    image.save(new_path)
    diff_path = path.with_name(f"{path.stem}.diff.png")
    try:  # a picture of what is there now beats a wall of numbers
        image.save(diff_path)
    except OSError:  # pragma: no cover - read-only snapshots dir
        diff_path = new_path
    raise AssertionError(
        f"PNG snapshot {path.name} differs: {differing} of {total} pixels "
        f"({ratio:.1%}) changed, tolerance {tolerance}, max_diff_ratio "
        f"{max_diff_ratio:.1%}. The new render is in {diff_path.name} "
        f"(and {new_path.name}). {hint}"
    )


class PngSnapshotChecker:
    """The ``pymobile_png`` fixture: compare a screen's picture with its golden PNG.

    ::

        def test_colours(pymobile_session, pymobile_png):
            pymobile_session.start(Home())
            pymobile_png()                       # the screen the app shows
            pymobile_png(Home().root, "root")    # or any widget / screen / tree

    Same naming, same update flag and same CI rule as the text snapshots.
    """

    def __init__(
        self,
        test_file: str | Path,
        test_name: str,
        bridge: StubBridge | None = None,
        *,
        update: bool = False,
        ci: bool | None = None,
        width: int = 360,
        height: int = 640,
        scale: float = 2.0,
        tolerance: int = 0,
        max_diff_ratio: float = 0.0,
    ) -> None:
        self._test_file = str(test_file)
        self._test_name = snapshot_name(test_name)
        self._bridge = bridge
        self._update = update
        self._ci = running_in_ci() if ci is None else ci
        self._width = width
        self._height = height
        self._scale = scale
        self._tolerance = tolerance
        self._max_diff_ratio = max_diff_ratio
        self._unnamed = 0

    def _tree(self, target: Any) -> Any:
        if target is None:
            tree = None if self._bridge is None else self._bridge.last_tree
            if tree is None:
                raise AssertionError(
                    "nothing has been rendered yet: start the app "
                    "(pymobile_session.start(Screen())) or pass the widget to check"
                )
            return tree
        if isinstance(target, Screen):
            return target.to_dict()
        return target

    def __call__(self, target: Any = None, name: str | None = None) -> Path:
        """Check ``target`` (default: the last frame drawn); return the golden path."""
        if name is None:
            self._unnamed += 1
            chosen = (
                self._test_name if self._unnamed == 1 else f"{self._test_name}_{self._unnamed}"
            )
        else:
            chosen = snapshot_name(name)
        # ``ci`` is passed through instead of poking ``os.environ["CI"]``: the
        # checker's answer must not depend on — or disturb — process state.
        return assert_png_snapshot(
            self._tree(target),
            self._test_file,
            chosen,
            width=self._width,
            height=self._height,
            scale=self._scale,
            tolerance=self._tolerance,
            max_diff_ratio=self._max_diff_ratio,
            update=self._update,
            ci=self._ci,
        )
