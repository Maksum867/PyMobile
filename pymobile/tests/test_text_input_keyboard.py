"""TextInput keyboard types, IME actions and submission.

The keyboard has to reach both the device and the web preview, and an IME
action without a handler is a mistake worth naming.
"""

from __future__ import annotations

import pytest

from pymobile import App, Column, Screen, TextInput, Widget
from pymobile.core.ui.web import render_html
from pymobile.testing import StubBridge


class TestTextInputKeyboard:
    def test_the_keyboard_reaches_both_renderers(self) -> None:
        field = TextInput(keyboard="email", ime_action="send", on_submit=lambda value: None)
        assert field.props()["keyboard"] == "email"
        assert field.multiline is False
        html = render_html(field.to_dict())
        assert 'type="email"' in html and 'inputmode="email"' in html
        assert "'submit'" in html

    def test_an_unknown_keyboard_is_refused(self) -> None:
        with pytest.raises(ValueError, match="invalid keyboard"):
            TextInput(keyboard="swipe")

    def test_password_and_multiline_follow_the_keyboard(self) -> None:
        assert TextInput(keyboard="password").password is True
        assert TextInput(keyboard="multiline").multiline is True

    def test_contradictory_flags_are_named(self) -> None:
        with pytest.raises(ValueError, match="disagree"):
            TextInput(multiline=True, keyboard="email")
        with pytest.raises(ValueError, match="cannot use the password keyboard"):
            TextInput(multiline=True, keyboard="password")

    def test_an_ime_action_needs_a_handler(self) -> None:
        with pytest.raises(ValueError, match="pass on_submit"):
            TextInput(ime_action="search")

    def test_submit_runs_the_handler_with_the_current_text(self) -> None:
        seen: list[str] = []
        field = TextInput("hello", on_submit=seen.append, ime_action="go")
        field.submit()
        assert seen == ["hello"]

    def test_a_disabled_field_does_not_submit(self) -> None:
        seen: list[str] = []
        field = TextInput("hello", on_submit=seen.append, enabled=False)
        field.submit()
        assert seen == []

    def test_the_device_event_reaches_the_handler(self) -> None:
        seen: list[str] = []
        app = App(bridge=StubBridge(verbose=False))

        class Form(Screen):
            def build(self) -> Widget:
                self.field = TextInput("text", on_submit=seen.append, ime_action="send")
                return Column(self.field)

        screen = Form()
        app.run(screen)
        try:
            app._handle_ui_event(f"{app._generation}:{screen.field.id}", "submit", "text")
        finally:
            app.stop()
        assert seen == ["text"]
