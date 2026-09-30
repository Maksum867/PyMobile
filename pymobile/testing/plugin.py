"""The pytest plugin: fixtures for testing a PyMobile app.

Registered by the ``pytest11`` entry point, so it is active as soon as
``pymobile`` is installed — no ``conftest.py`` needed. Opt out with
``-p no:pymobile.testing.plugin``.

===================  =====================================================
``pymobile_session`` app + stub bridge + driver, torn down after the test
``pymobile_app``     the :class:`~pymobile.core.app.App` (created, not running)
``pymobile_bridge``  the recording :class:`~pymobile.core.bridge.StubBridge`
``pymobile_driver``  a :class:`~pymobile.core.driver.Driver`: open, press, navigate
``pymobile_snapshot``  compare a screen's text picture with a golden file
===================  =====================================================

::

    def test_the_quiz(pymobile_session):
        pymobile_session.start(Menu())
        pymobile_session.driver.navigate("Menu.start, Quiz.answer=42, Quiz.next")
        assert pymobile_session.driver.find("score").text == "42/10"

    def test_the_menu_looks_right(pymobile_session, pymobile_snapshot):
        pymobile_session.start(Menu())
        pymobile_snapshot()
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ..core.app import App
from ..core.bridge import StubBridge
from ..core.driver import Driver
from .session import Session, app_session
from .snapshots import UPDATE_ENV, SnapshotChecker, env_flag

__all__ = [
    "pymobile_app",
    "pymobile_bridge",
    "pymobile_driver",
    "pymobile_session",
    "pymobile_snapshot",
]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pymobile", "PyMobile")
    group.addoption(
        "--pymobile-update-snapshots",
        action="store_true",
        default=False,
        help=f"rewrite the golden files of pymobile_snapshot (or set {UPDATE_ENV}=1)",
    )


@pytest.fixture
def pymobile_session(tmp_path: Path) -> Iterator[Session]:
    """An app, a stub bridge and a driver; the app's store is in ``tmp_path``."""
    with app_session(str(tmp_path)) as session:
        yield session


@pytest.fixture
def pymobile_app(pymobile_session: Session) -> App:
    """The app under test: created, not running — ``app.run(FirstScreen())``."""
    return pymobile_session.app


@pytest.fixture
def pymobile_bridge(pymobile_session: Session) -> StubBridge:
    """The recording stub bridge: ``notifications``, ``calls``, ``last_tree``."""
    return pymobile_session.bridge


@pytest.fixture
def pymobile_driver(pymobile_session: Session) -> Driver:
    """Open screens, press widgets, type, go back, follow a route."""
    return pymobile_session.driver


@pytest.fixture
def pymobile_snapshot(
    request: pytest.FixtureRequest, pymobile_session: Session
) -> SnapshotChecker:
    """``pymobile_snapshot()`` checks the current screen against its golden text file."""
    update = bool(
        request.config.getoption("--pymobile-update-snapshots", default=False)
    ) or env_flag(UPDATE_ENV)
    return SnapshotChecker(
        getattr(request, "path", None) or request.node.fspath,  # .path needs pytest 7
        request.node.name,
        pymobile_session.bridge,
        update=update,
    )
