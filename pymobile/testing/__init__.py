"""Test your PyMobile app without a phone, an emulator or your own scaffolding.

``pip install pymobile-framework`` also installs a pytest plugin
(:mod:`pymobile.testing.plugin`) with fixtures — ``pymobile_session``,
``pymobile_app``, ``pymobile_bridge``, ``pymobile_driver``, ``pymobile_snapshot``
— and this package holds what they are made of, usable from any test runner::

    from pymobile.testing import app_session

    with app_session(tmp_dir) as session:
        session.start(Menu())
        session.driver.navigate("Menu.start, Quiz.answer=42, Quiz.next")
        assert session.driver.find("score").text == "42/10"

Nothing here imports pytest, so ``import pymobile.testing`` is safe in an app.
"""

from __future__ import annotations

from ..core.bridge import StubBridge
from ..core.driver import Driver, Step, find_screen_class, parse_steps, screen_classes
from ..core.ui.preview import assert_snapshot, render_ascii, snapshot_path
from .session import Session, app_session
from .snapshots import SnapshotChecker

__all__ = [
    "Driver",
    "Session",
    "SnapshotChecker",
    "Step",
    "StubBridge",
    "app_session",
    "assert_snapshot",
    "find_screen_class",
    "parse_steps",
    "render_ascii",
    "screen_classes",
    "snapshot_path",
]
