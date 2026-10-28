"""Public contract, parity-registry and UI-dispatcher tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import App, HttpClient, HttpSecurityPolicy, Label
from pymobile.core.bridge import StubBridge
from pymobile.core.dispatcher import UiDispatcher
from pymobile.core.ui.registry import WIDGET_CAPABILITIES, supported_by, widget_types
from pymobile.core.ui.widget import Widget

from .java_sources import renderer_source

ROOT = Path(__file__).parents[1]


def test_widget_serialisation_has_stable_required_contract() -> None:
    node = Label("Hello", id="greeting").to_dict()
    assert node == {
        "type": "Label",
        "id": "greeting",
        "visible": True,
        "enabled": True,
        "props": {"text": "Hello"},
    }


def test_extension_props_are_portable_and_validated() -> None:
    widget = Widget(priority=3, metadata={"tags": ["field"]})
    assert widget.props() == {"priority": 3, "metadata": {"tags": ["field"]}}
    with pytest.raises(TypeError, match="JSON-like"):
        Widget(callback=lambda: None)
    with pytest.raises(ValueError, match="public identifiers"):
        widget.set_prop("not-valid", 1)


def test_dispatcher_is_fifo_and_closes_cleanly() -> None:
    dispatcher = UiDispatcher()
    observed: list[int] = []
    assert dispatcher.post(observed.append, 1)
    assert dispatcher.post(observed.append, 2)
    assert dispatcher.drain() == 2
    assert observed == [1, 2]
    dispatcher.close()
    assert not dispatcher.post(observed.append, 3)


def test_app_dispatch_updates_ui_before_render() -> None:
    bridge = StubBridge(verbose=False)
    app = App("dispatch", bridge=bridge)

    class Home:
        # Screen protocol is deliberately exercised through a real minimal class below.
        pass

    from pymobile import Screen
    from pymobile import Widget as BaseWidget

    class ScreenUnderTest(Screen):
        def build(self) -> BaseWidget:
            self.label = Label("before")
            return self.label

    screen = ScreenUnderTest()
    app.run(screen)
    assert app.dispatch(setattr, screen.label, "text", "after")
    assert screen.label.text == "after"
    assert bridge.last_tree["props"]["text"] == "after"
    app.stop()


def test_http_security_policy_blocks_insecure_or_unapproved_hosts() -> None:
    secure = HttpClient(security=HttpSecurityPolicy(require_https=True))
    with pytest.raises(Exception, match="Insecure HTTP"):
        secure._build_url("http://example.test", None)
    restricted = HttpClient(
        security=HttpSecurityPolicy(allowed_hosts=frozenset({"api.example.test"}))
    )
    assert restricted._build_url("https://api.example.test/v1", None).startswith("https://")
    with pytest.raises(Exception, match="blocked"):
        restricted._build_url("https://other.example.test/v1", None)


def test_registry_has_unique_widget_types() -> None:
    names = [capability.type_name for capability in WIDGET_CAPABILITIES]
    assert len(names) == len(set(names))
    assert widget_types() == frozenset(names)


def test_registry_matches_explicit_renderer_implementations() -> None:
    android = renderer_source()
    web = (ROOT / "core/ui/web.py").read_text(encoding="utf-8")
    gui = (ROOT / "core/ui/gui.py").read_text(encoding="utf-8")
    for capability in WIDGET_CAPABILITIES:
        quoted = f'"{capability.type_name}"'
        if capability.android:
            assert quoted in android, capability.type_name
        if capability.web:
            assert quoted in web, capability.type_name
        if capability.gui:
            assert quoted in gui, capability.type_name
    assert "Label" in supported_by("android")
    with pytest.raises(ValueError, match="unknown renderer"):
        supported_by("canvas")
