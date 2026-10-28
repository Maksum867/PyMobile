"""Regression cases from the P1-P6 review, without requiring Android or a display."""

from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from pymobile import (
    App,
    Button,
    Column,
    Label,
    PermissionDeniedError,
    PermissionError_,
    Screen,
    Style,
    audit_ui,
    cli,
)
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.validation import entrypoint_warnings
from pymobile.core.driver import Driver
from pymobile.errors import WidgetNotFoundError


def test_dev_display_failure_falls_back(project, monkeypatch, capsys):
    class TclError(Exception):
        pass

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(TclError=TclError))
    monkeypatch.setattr("pymobile.core.ui.gui.tkinter_available", lambda: True)
    monkeypatch.setattr(cli, "_load", lambda args: project)
    monkeypatch.setattr(cli, "_execute", lambda *args: None)
    monkeypatch.setattr(cli, "_navigate", lambda *args: None)

    def fail(*args):
        raise TclError("no display")

    monkeypatch.setattr(cli, "_open_gui_window", fail)
    calls = []
    monkeypatch.setattr(cli, "cmd_watch", lambda args: calls.append(args) or 0)
    args = argparse.Namespace()
    assert cli.cmd_dev(args) == 0
    assert calls == [args]
    output = capsys.readouterr()
    assert "using text watch" in output.out + output.err


def test_dev_does_not_hide_unrelated_errors(project, monkeypatch):
    monkeypatch.setattr("pymobile.core.ui.gui.tkinter_available", lambda: True)
    monkeypatch.setattr(cli, "_load", lambda args: project)
    monkeypatch.setattr(cli, "_execute", lambda *args: None)
    monkeypatch.setattr(cli, "_navigate", lambda *args: None)
    monkeypatch.setitem(
        sys.modules, "tkinter", SimpleNamespace(TclError=type("TclError", (Exception,), {}))
    )

    def fail(*args):
        raise ValueError("programming error")

    monkeypatch.setattr(cli, "_open_gui_window", fail)
    with pytest.raises(ValueError, match="programming error"):
        cli.cmd_dev(argparse.Namespace())


def test_native_validation_warns_on_empty_startup_and_full_stdlib(project):
    pipeline = BuildPipeline(project, native=True, check_startup=False)
    pipeline._validate()
    assert any("no .run(...)" in warning for warning in pipeline.warnings)
    assert any("--minimal-stdlib" in warning for warning in pipeline.warnings)


def test_startup_ast_ignores_comments_and_strings(tmp_path):
    entry = tmp_path / "main.py"
    entry.write_text('# App().run(Home())\nprint("app.run(Home())")\n')
    assert entrypoint_warnings(entry)
    entry.write_text('App("Demo").run(Home())\n')
    assert entrypoint_warnings(entry) == []


def test_wire_ids_work_for_find_and_press_and_reject_stale(bridge, tmp_path):
    taps = []

    class Home(Screen):
        def build(self):
            return Button("Tap", id="tap", on_press=lambda: taps.append(1))

    app = App("IDs", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    try:
        app.run(Home())
        driver = Driver(app)
        wire_id = driver.tree()["id"]
        assert driver.find(wire_id) is driver.find("tap")
        driver.press(wire_id)
        assert taps == [1]
        app.push(Home())
        with pytest.raises(WidgetNotFoundError):
            driver.press(wire_id)
        assert taps == [1]
    finally:
        app.stop()


def test_audit_uses_default_surface_and_nearest_override():
    text = Label("Light grey", style=Style(color="#eeeeee"))
    assert any(i.code == "contrast" for i in audit_ui(text, background="#ffffff"))
    assert not any(i.code == "contrast" for i in audit_ui(text, background="#000000"))
    tree = Column(Label("Grey", style=Style(color="#eeeeee")), style=Style(background="#000000"))
    assert not any(i.code == "contrast" for i in audit_ui(tree, background="#ffffff"))


@pytest.mark.parametrize("theme,has_contrast", [("light", True), ("dark", False)])
def test_check_ui_theme(project, monkeypatch, capsys, theme, has_contrast):
    project.entrypoint_path.write_text(
        "from pymobile import App, Label, Screen, Style\n"
        "class Home(Screen):\n"
        "    def build(self):\n"
        '        return Label("Grey", style=Style(color="#eeeeee"))\n'
        'App("Contrast").run(Home())\n'
    )
    monkeypatch.setattr(cli, "_load", lambda args: project)
    assert cli.main(["check-ui", "--theme", theme, "--json"]) == 0
    issues = json.loads(capsys.readouterr().out)
    assert any(i["code"] == "contrast" for i in issues) == has_contrast


def test_pop_rebuilds_underlying_screen_without_data_notification(bridge, tmp_path):
    data = ["old"]

    class Home(Screen):
        def build(self):
            return Label(data[0], id="value")

    app = App("Back", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    try:
        home = Home()
        app.run(home)
        app.push(Home())
        data[0] = "new"
        app.pop()
        assert home.get("value").text == "new"
        assert app.pop() is None
    finally:
        app.stop()


def test_permission_error_alias_is_backward_compatible():
    assert PermissionDeniedError is PermissionError_
    with pytest.raises(PermissionError_):
        raise PermissionDeniedError("android.permission.CAMERA")
