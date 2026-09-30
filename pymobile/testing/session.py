"""An app under test: a stub bridge, temporary storage and a driver, set up and torn down."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.app import App
from ..core.bridge import StubBridge, reset_bridge, set_bridge
from ..core.driver import Driver
from ..core.i18n import translations
from ..core.ui.screen import Screen

__all__ = ["Session", "app_session"]


@dataclass(slots=True)
class Session:
    """The pieces a test of an app needs, wired together."""

    app: App
    bridge: StubBridge
    driver: Driver

    def start(self, screen: Screen) -> Screen:
        """Run the app on ``screen`` (what ``main()`` does) and return the screen."""
        self.app.run(screen)
        return screen

    @property
    def screen(self) -> Screen:
        """The screen the app shows now."""
        return self.driver.screen


@contextmanager
def app_session(
    storage_dir: str | Path,
    *,
    name: str = "Test App",
    **app_options: Any,
) -> Iterator[Session]:
    """An :class:`~pymobile.core.app.App` on a :class:`StubBridge`, isolated from the machine.

    * the bridge is a recording stub (nothing is drawn, notifications and
      vibrations are kept in ``bridge.notifications`` / ``bridge.calls``) and is
      the process-wide bridge while the block runs;
    * the app's key-value store lives in ``storage_dir`` — never in the
      user's real application data;
    * on exit the app is stopped, the process-wide bridge is reset and the
      language the test switched to is undone.

    ``app_options`` go to ``App(...)`` (``theme="dark"``, ``package=…``).
    The app is created but **not running**: call :meth:`Session.start` with the
    first screen, or run a ``main()`` and use ``Driver.attach()``.
    """
    previous_language = translations.language
    bridge = StubBridge(verbose=False)
    set_bridge(bridge)
    app_options.setdefault("log_level", "warning")
    app = App(
        name,
        bridge=bridge,
        storage_path=str(Path(storage_dir) / "pymobile-test-store.json"),
        **app_options,
    )
    try:
        yield Session(app=app, bridge=bridge, driver=Driver(app))
    finally:
        app.stop()
        reset_bridge()
        translations.use(previous_language)
