"""Golden-file checks of a screen's text picture (``render_ascii``)."""

from __future__ import annotations

import os
import re
import warnings
from pathlib import Path
from typing import Any

from ..core.bridge import StubBridge
from ..core.ui.preview import assert_snapshot, snapshot_path
from ..core.ui.screen import Screen

__all__ = ["SnapshotChecker", "env_flag", "running_in_ci", "snapshot_name"]

UPDATE_ENV = "PYMOBILE_UPDATE_SNAPSHOTS"
UPDATE_HINT = (
    "If the change is intended, accept it with `pytest --pymobile-update-snapshots` "
    f"(or {UPDATE_ENV}=1) and commit the file."
)


def env_flag(name: str) -> bool:
    """Whether an environment variable is set to something other than ``0``/``false``/``no``."""
    value = os.environ.get(name)
    return value is not None and value.strip().lower() not in ("", "0", "false", "no", "off")


def running_in_ci() -> bool:
    """Whether the ``CI`` variable (set by GitHub Actions, GitLab, Travis…) is on."""
    return env_flag("CI")


def snapshot_name(text: str) -> str:
    """``test_menu[dark-mode]`` → ``test_menu_dark-mode``: safe as a file name."""
    return re.sub(r"[^\w.-]+", "_", text).strip("_") or "screen"


class SnapshotChecker:
    """The ``pymobile_snapshot`` fixture: compare a screen with its golden text.

    ::

        def test_menu(pymobile_session, pymobile_snapshot):
            pymobile_session.start(Menu())
            pymobile_snapshot()                    # the screen the app shows
            pymobile_snapshot(Menu().root, "root")  # or any widget / screen / tree

    The golden file is ``snapshots/<test module>__<test name>.txt`` next to the
    test file (a second unnamed check in one test gets ``_2``…; a ``name`` picks the
    file: ``snapshots/<module>__<name>.txt``). A missing file is written and the
    test passes with a warning — unless ``CI`` is set, where a snapshot nobody
    committed is a failure. ``--pymobile-update-snapshots`` rewrites them all.
    """

    def __init__(
        self,
        test_file: str | Path,
        test_name: str,
        bridge: StubBridge | None = None,
        *,
        update: bool = False,
        ci: bool | None = None,
    ) -> None:
        self._test_file = str(test_file)
        self._test_name = snapshot_name(test_name)
        self._bridge = bridge
        self._update = update
        self._ci = running_in_ci() if ci is None else ci
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

    def __call__(
        self,
        target: Any = None,
        name: str | None = None,
        *,
        ids: bool = False,
        title: str = "",
    ) -> str:
        """Check ``target`` (default: the last frame drawn) and return its text."""
        if name is None:
            self._unnamed += 1
            chosen = self._test_name if self._unnamed == 1 else f"{self._test_name}_{self._unnamed}"
        else:
            chosen = snapshot_name(name)
        path = snapshot_path(self._test_file, chosen)
        tree = self._tree(target)

        exists = path.exists()
        if not exists and not self._update and self._ci:
            raise AssertionError(
                f"snapshot {path.name} does not exist, and CI does not create them: "
                f"generate it locally with `pytest --pymobile-update-snapshots` and commit it"
            )
        if not exists and not self._update:
            warnings.warn(
                f"snapshot {path.name} did not exist and has been written: review and commit it",
                UserWarning,
                stacklevel=2,
            )
        return assert_snapshot(
            tree,
            self._test_file,
            chosen,
            show_ids=ids,
            title=title,
            update=self._update,
            hint=UPDATE_HINT,
        )
