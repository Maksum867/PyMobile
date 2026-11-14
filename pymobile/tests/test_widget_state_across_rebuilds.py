"""A widget created in build() keeps what the user typed after a rebuild.

Screen.rebuild() builds a fresh tree, so the typed text, the switch, the box
and the slider have to be carried over by widget id.
"""

from __future__ import annotations

from pymobile import App, Checkbox, Column, Screen, Slider, Switch, TextInput, Widget
from pymobile.testing import StubBridge


def _screen(cls: type[Screen]) -> tuple[App, Screen]:
    screen = cls()
    app = App(screen, bridge=StubBridge(verbose=False))
    app.run(screen)
    return app, screen


class Searchy(Screen):
    def build(self) -> Widget:
        self.field = TextInput(placeholder="search")
        self.toggle = Switch(False)
        self.box = Checkbox(False, text="only mine")
        self.slider = Slider(value=10, minimum=0, maximum=100)
        return Column(self.field, self.toggle, self.box, self.slider)


class TestStateAcrossRebuilds:
    def test_typed_text_survives_a_refresh(self) -> None:
        app, screen = _screen(Searchy)
        try:
            screen.field.set_value("hello")  # type: ignore[attr-defined]
            screen.refresh()
            assert screen.field.value == "hello"  # type: ignore[attr-defined]
        finally:
            app.stop()

    def test_toggles_and_sliders_survive_a_refresh(self) -> None:
        app, screen = _screen(Searchy)
        try:
            screen.toggle.set_checked(True)  # type: ignore[attr-defined]
            screen.box.set_checked(True)  # type: ignore[attr-defined]
            screen.slider.set_value(70)  # type: ignore[attr-defined]
            screen.refresh()
            assert screen.toggle.checked is True  # type: ignore[attr-defined]
            assert screen.box.checked is True  # type: ignore[attr-defined]
            assert screen.slider.value == 70  # type: ignore[attr-defined]
        finally:
            app.stop()

    def test_an_empty_field_stays_empty(self) -> None:
        app, screen = _screen(Searchy)
        try:
            screen.refresh()
            assert screen.field.value == ""  # type: ignore[attr-defined]
        finally:
            app.stop()
