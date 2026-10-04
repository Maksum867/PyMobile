"""Native screen and visibility transitions carry explicit, testable metadata."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import App, ExpansionPanel, Label, Screen
from pymobile.core.bridge.stub import StubBridge


class NativeRecorder(StubBridge):
    native_widgets = True
    accepts_theme = True


class Page(Screen):
    def build(self) -> Label:
        return Label("screen")


def test_android_screen_navigation_sends_transition_and_reverse_direction(
    tmp_path: Path,
) -> None:
    bridge = NativeRecorder(verbose=False)
    app = App(
        "Transitions",
        bridge=bridge,
        transition="slide",
        transition_duration_ms=340,
        storage_path=str(tmp_path / "store.json"),
    )
    app.run(Page())
    try:
        app.push(Page())
        assert bridge.last_tree is not None
        assert bridge.last_tree["navigation"] == {
            "type": "slide",
            "duration_ms": 340,
            "reverse": False,
        }
        app.pop()
        assert bridge.last_tree is not None
        assert bridge.last_tree["navigation"]["reverse"] is True
        assert app._pending_navigation is None
    finally:
        app.stop()


def test_screen_transition_arguments_are_validated() -> None:
    with pytest.raises(ValueError, match="transition must be one of"):
        App("Bad", transition="zoom")
    with pytest.raises(ValueError, match="transition_duration_ms"):
        App("Bad", transition_duration_ms=True)


def test_animated_widget_visibility_is_serialized_and_expansion_panel_opts_in() -> None:
    widget = Label("Details", visible=False, animate_visibility=True, animation_duration_ms=410)
    assert widget.to_dict()["animation"] == {
        "visibility": "fade_scale",
        "duration_ms": 410,
    }

    panel = ExpansionPanel("More", Label("Body"))
    panel_tree = panel.to_dict()
    assert panel_tree["children"][1]["animation"]["visibility"] == "fade_scale"


def test_android_activity_contains_the_native_transition_and_title_handlers() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "android"
        / "java"
        / "MainActivity.java"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "animateScreenChange" in source
    assert 'root.optJSONObject("navigation")' in source
    assert 'root.remove("navigation")' in source
    assert 'root.optString("app_title", "")' in source
    assert "requestedTitle, (Bitmap) null" in source
