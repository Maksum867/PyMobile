import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pymobile import App, Button, Card, Column, Grid, Label, Row, Screen, Style, Widget, Wrap
from pymobile.core.bridge import StubBridge
from pymobile.errors import WidgetKeywordError, WidgetParentError


@pytest.fixture(autouse=True)
def restore_framework_logger():
    """Avoid leaking logger configuration into other test modules."""
    logger = logging.getLogger("pymobile")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.fixture
def app(tmp_path):
    instance = App(
        "Demo", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "store.json")
    )
    yield instance
    instance.stop()


def test_run_widget_events_refresh_navigation_and_preserve_state(app):
    label = Label("before", id="label")
    button = Button("Change", id="change", on_press=lambda: setattr(label, "text", "after"))
    app.run(Column(label, button))
    assert isinstance(app.screen, Screen)
    assert label.screen is app.screen
    app.handle_ui_event("change", "press", None)
    assert label.text == "after"
    app.set_theme("dark")
    assert app.screen.root.children[0] is label
    assert label.screen is app.screen

    class Next(Screen):
        def build(self):
            return Label("Next")

    app.push(Next())
    app.pop()
    assert app.screen.root.children[0] is label
    assert label.text == "after"
    assert label.screen is app.screen


def test_run_widget_rejects_shared_root(app):
    label = Label("existing")
    Column(label)
    with pytest.raises((TypeError, ValueError)):
        app.run(label)


def test_old_run_screen(app):
    class Home(Screen):
        def build(self):
            return Label("Hello")

    home = Home()
    app.run(home)
    assert app.screen is home


def test_label_style_shortcuts_match_old():
    old = Label("Hello", id="a", style=Style(font_size=24, color="#333333"))
    new = Label("Hello", id="a", font_size=24, color="#333333")
    assert new.to_dict() == old.to_dict()
    assert Button("Go", font_size=20).style.font_size == 20
    with pytest.raises((TypeError, ValueError), match="style"):
        Label("x", style=Style(color="#000000"), color="#FFFFFF")
    with pytest.raises(ValueError):
        Label("x", font_size=0)
    with pytest.raises(WidgetKeywordError):
        Label("x", colr="#333333")


def test_custom_widget_preserves_extension_properties():
    class Custom(Widget):
        type_name = "Custom"

    custom = Custom(color="#123456", font_size=22)
    assert custom.to_dict()["props"]["color"] == "#123456"
    assert custom.to_dict()["props"]["font_size"] == 22
    assert custom.style.color is None


@pytest.mark.parametrize("container", [Column, Row, Grid, Wrap])
def test_list_children_and_legacy_arguments(container):
    one, two, three = Label("1"), Label("2"), Label("3")
    result = container(one, [two, three])
    assert list(result.children) == [one, two, three]
    assert all(child.parent is result for child in (one, two, three))
    assert len(container(*[Label("a"), Label("b")]).children) == 2
    assert len(container([]).children) == 0
    with pytest.raises(TypeError):
        container([Label("ok"), "not a widget"])


def test_list_rejects_duplicates():
    item = Label("item")
    with pytest.raises(WidgetParentError):
        Column([item, item])


def test_push_class_and_legacy_instance(app):
    class Home(Screen):
        def build(self):
            return Label("Home")

    class Details(Screen):
        def __init__(self, item_id):
            super().__init__()
            self.item_id = item_id

        def build(self):
            return Label(str(self.item_id))

    app.run(Home())
    new = app.push(Details, item_id=7)
    assert isinstance(new, Details) and new.item_id == 7
    assert app.screen is new
    app.pop()
    instance = Details(5)
    assert app.push(instance) is instance
    app.pop()
    with pytest.raises(TypeError):
        app.push(instance, item_id=10)
    assert isinstance(app.screen, Home)
    with pytest.raises(TypeError):
        app.push(Details)
    assert isinstance(app.screen, Home)


def test_cli_widget_entrypoint(tmp_path):
    project = tmp_path / "project"
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            filter(None, (str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")))
        ),
        PYMOBILE_STORAGE_DIR=str(tmp_path / "storage"),
    )
    init = subprocess.run(
        [sys.executable, "-m", "pymobile", "init", str(project), "--template", "basic"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    (project / "main.py").write_text(
        "from pymobile import App, Column, Label\n"
        'App("Demo").run(Column([Label("Hello", font_size=24, color="#333333")]))\n'
    )
    preview = subprocess.run(
        [sys.executable, "-m", "pymobile", "preview"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0 and "Hello" in preview.stdout, (preview.stdout, preview.stderr)
    audit = subprocess.run(
        [sys.executable, "-m", "pymobile", "check-ui", "--strict", "--json"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 0 and json.loads(audit.stdout) == [], (audit.stdout, audit.stderr)
    from pymobile.compiler.startup import check_startup
    from pymobile.core.config import load_config

    check_startup(load_config(project))


def test_root_is_detached_and_can_be_reused_after_stop(tmp_path):
    root = Column(Label("hello"))
    first = App("first", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "a.json"))
    first.run(root)
    first.stop()
    assert root.screen is None
    second = App("second", bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "b.json"))
    try:
        second.run(root)
        assert root.children[0].screen is second.screen
    finally:
        second.stop()


def test_style_shortcut_merges_distinct_fields_and_keeps_extra():
    label = Label(
        "Hello", style=Style(bold=True), color="#333333", font_size=16, extra={"color": "custom"}
    )
    assert label.style.bold and label.style.color == "#333333" and label.style.font_size == 16
    assert label.to_dict()["props"]["color"] == "custom"
    card = Card(Label("hello"), font_size=16)
    assert card.style.font_size == 16


def test_custom_builtin_subclass_keeps_its_color_as_extension():
    class CustomLabel(Label):
        pass

    item = CustomLabel("hello", color="#123456", font_size=20)
    assert item.to_dict()["props"]["color"] == "#123456"
    assert item.to_dict()["props"]["font_size"] == 20
    assert item.style.color is None


def test_failure_to_build_push_class_preserves_stack(app):
    class Home(Screen):
        def build(self):
            return Label("Home")

    class Broken(Screen):
        def build(self):
            raise RuntimeError("broken")

    home = Home()
    app.run(home)
    with pytest.raises(RuntimeError, match="broken"):
        app.push(Broken)
    assert app.screen is home
    assert len(app.navigator.stack) == 1
