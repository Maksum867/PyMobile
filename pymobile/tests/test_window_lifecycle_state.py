"""The window can be destroyed without the app: state is saved, Python stays.

An Activity is destroyed for reasons that have nothing to do with the user
leaving: back on the root screen followed by a relaunch, a configuration change
outside ``configChanges``, a low-memory kill of the task's UI. Each of those
used to restart the interpreter from ``main.py`` and lose everything — every
counter, form and navigation step. These tests pin the contract that replaced
it: a lifecycle event, saved widget state, and a resume that keeps the same
objects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymobile import App, Button, Column, Label, Screen, TextInput
from pymobile.core.app import LIFECYCLE_EVENT, WAKE_EVENT


class Counter(Screen):
    """A screen whose state only exists in memory."""

    stops = 0
    starts = 0

    def __init__(self) -> None:
        super().__init__()
        self.count = 0
        self.field = TextInput(id="note", placeholder="type here")

    def build(self) -> Column:
        return Column(Label(f"count={self.count}"), self.field, Button("+", on_press=self.bump))

    def bump(self) -> None:
        self.count += 1

    def on_stop(self) -> None:
        type(self).stops += 1

    def on_start(self) -> None:
        type(self).starts += 1


@pytest.fixture
def app(tmp_path: Path) -> App:
    Counter.stops = 0
    Counter.starts = 0
    application = App("Lifecycle", storage_path=tmp_path / "state.json")
    yield application
    if not application._stopped:
        application.stop()


def test_a_stop_event_keeps_the_running_app_and_its_state(app: App) -> None:
    screen = Counter()
    app.run(screen)
    screen.bump()
    screen.field.set_value("half-typed")

    app._lifecycle("stop")
    app._lifecycle("resume")

    # The same screen object, the same field, the same counter: nothing was
    # rebuilt and nothing was read back from disk over the live value.
    assert app.navigator.current is screen
    assert screen.count == 1
    assert screen.field.value == "half-typed"


def test_the_lifecycle_event_reaches_the_screen(app: App) -> None:
    app.run(Counter())
    app._lifecycle("stop")
    assert Counter.stops == 1
    app._lifecycle("resume")
    assert Counter.starts == 1


def test_the_typed_value_is_written_to_the_store(app: App, tmp_path: Path) -> None:
    screen = Counter()
    app.run(screen)
    screen.field.set_value("draft")
    app._lifecycle("stop")

    stored = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    # namespaced under "pymobile", keyed by the widget id, so the app's own keys
    # are untouched.
    payload = stored["pymobile.state"]
    assert payload["0"]["note"]["value"] == "draft"


def test_a_restarted_app_restores_the_widget_state(tmp_path: Path) -> None:
    """The process was killed: a fresh App reads the saved field back."""
    Counter.stops = 0
    Counter.starts = 0
    first = App("Lifecycle", storage_path=tmp_path / "state.json")
    screen = Counter()
    first.run(screen)
    screen.field.set_value("survived")
    first._lifecycle("stop")
    first.stop()

    second = App("Lifecycle", storage_path=tmp_path / "state.json")
    revived = Counter()
    second.run(revived)
    assert revived.field.value == "survived"
    second.stop()


def test_a_screen_without_state_writes_nothing(app: App, tmp_path: Path) -> None:
    """No tracked widget state means no store write at all."""

    class Plain(Screen):
        def build(self) -> Column:
            return Column(Label("hello"))

    app.run(Plain())
    app._lifecycle("stop")  # must not raise and must not create an empty store
    assert not (tmp_path / "state.json").exists()


def test_the_loop_recognises_both_event_kinds() -> None:
    # The device sends the kind as the event's second field and the phase as
    # the third; the names are a wire contract with MainActivity/PythonRuntime.
    assert LIFECYCLE_EVENT == "__lifecycle__"
    assert WAKE_EVENT == "__wake__"


def test_a_stop_is_not_an_input_event(app: App) -> None:
    """A press delivered after the stop must not run a handler."""
    screen = Counter()
    app.run(screen)
    button = next(w for w in screen.root.walk() if isinstance(w, Button))
    app._lifecycle("stop")

    app.handle_ui_event(button.id, "press", "")
    # Still alive and still showing the same screen — just no longer counting.
    assert app.navigator.current is screen
    assert screen.count == 0
