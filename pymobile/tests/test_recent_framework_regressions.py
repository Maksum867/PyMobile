import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pymobile import App, Card, FormField, ProgressText, Screen, Theme, audit_ui
from pymobile.core.bridge import StubBridge
from pymobile.core.i18n import Translations
from pymobile.core.ui.audit import contrast_ratio
from pymobile.logging import configure


@pytest.fixture(autouse=True)
def restore_framework_logger():
    """Avoid leaking logger configuration into other test modules."""
    logger = logging.getLogger("pymobile")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.mark.parametrize("background", ["#F8FAFC", "#FFFFFF", "#FFCC99", "#000000", "#123"])
@pytest.mark.parametrize("colour", ["#1E40AF", "#000000", "#FFFFFF", "#123", "#FF000000"])
def test_dark_derived_accents_contrast_against_real_surfaces(background, colour):
    light = Theme(
        "custom",
        {key: colour for key in ("PRIMARY", "ACCENT", "SUCCESS", "WARNING", "ERROR")}
        | {"BACKGROUND": background},
    )
    dark = Theme.dark_from(light)
    for key in ("PRIMARY", "ACCENT", "SUCCESS", "WARNING", "ERROR"):
        for bg in ("BACKGROUND", "SURFACE"):
            assert contrast_ratio(dark[key], dark[bg]) >= 4.5, (key, dark[key], dark[bg])


def test_dark_keeps_an_already_readable_accent():
    assert Theme.dark_from(Theme("x", {"PRIMARY": "#FFFFFF"}))["PRIMARY"] == "#FFFFFF"


def test_default_preset_contrast_and_card_hint(tmp_path):
    for theme, color in ((Theme.light(), "TEXT_MUTED"), (Theme.dark(), "PRIMARY")):
        for background in ("BACKGROUND", "SURFACE"):
            assert contrast_ratio(theme[color], theme[background]) >= 4.5

    class Home(Screen):
        def build(self):
            return Card(FormField("email", "Email", hint="Please enter your email"))

    app = App(bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "store.json"))
    try:
        app.run(Home())
        assert not [
            i
            for i in audit_ui(app.bridge.last_tree, background=app.theme["BACKGROUND"])
            if i.code == "contrast"
        ]
    finally:
        app.stop()


def test_custom_theme_round_trip_and_default_behavior(tmp_path):
    light = Theme("brand", {"PRIMARY": "#1E40AF", "BACKGROUND": "#F8FAFC"})
    app = App(
        theme=light, bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "store.json")
    )
    app.set_theme("dark")
    assert app.theme.as_dict() == Theme.dark_from(light).as_dict()
    app.set_theme("light")
    assert app.theme is light
    app.set_theme("dark")
    assert app.theme.as_dict() == Theme.dark_from(light).as_dict()
    app.stop()
    default = App(storage_path=str(tmp_path / "default.json"))
    default.set_theme("dark")
    assert default.theme.as_dict() == Theme.dark().as_dict()
    default.set_theme("light")
    assert default.theme.as_dict() == Theme.light().as_dict()
    default.stop()


def test_explicit_dark_theme_is_preserved(tmp_path):
    light = Theme("brand", {"PRIMARY": "#1E40AF"})
    dark = Theme("dark", {"PRIMARY": "#AAFFFF", "BACKGROUND": "#101010"})
    app = App(theme=light, dark_theme=dark, storage_path=str(tmp_path / "s.json"))
    try:
        app.set_theme("dark")
        assert app.theme is dark
        app.set_theme("light")
        assert app.theme is light
    finally:
        app.stop()


@pytest.mark.parametrize("flatten", [None, True])
def test_nested_load_quiet_and_plural_preserved(flatten, capsys):
    configure("warning")
    capsys.readouterr()
    tr = Translations(default_language="uk")
    tr.load(
        {"app": {"title": "Hello", "items": {"one": "one", "other": "many"}}},
        language="uk",
        flatten=flatten,
    )
    assert "nested objects" not in capsys.readouterr().err
    assert tr.get("app.title") == "Hello"
    assert tr.get("app.items", count=2) == "many"


def test_strict_flat_warning_after_normal_load(capsys):
    configure("warning")
    tr = Translations(default_language="uk")
    tr.load({"app": {"title": "Hello"}}, language="uk")
    capsys.readouterr()
    tr.load({"other": {"title": "No"}}, language="uk", flatten=False)
    assert "nested objects" in capsys.readouterr().err


def test_progress_text_documented_behaviour():
    assert ProgressText(66, label="").text == "66%"
    assert ProgressText(66, label="Done").text == "Done 66%"
    assert ProgressText(66, label="66%").text == "66% 66%"
    assert ProgressText(66, label="Done", format="{value}/{maximum}").text == "Done 66/100.0"


def test_cli_theme_flags_use_custom_theme(tmp_path):
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
        """from pymobile import App, Card, FormField, Label, Screen, Style, Theme

class Home(Screen):
    def build(self):
        return Card(
            Label(self.app.theme["PRIMARY"], style=Style(color=self.app.theme["PRIMARY"])),
            FormField("email", "Email", hint="Hint"),
        )

App("Brand", theme=Theme("brand", {"PRIMARY": "#1E40AF", "BACKGROUND": "#F8FAFC"})).run(Home())
"""
    )
    result = subprocess.run(
        [sys.executable, "-m", "pymobile", "check-ui", "--theme", "dark", "--strict", "--json"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == []
    preview = subprocess.run(
        [sys.executable, "-m", "pymobile", "preview", "--theme", "dark"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0, (preview.stdout, preview.stderr)
    expected = Theme.dark_from(Theme("brand", {"PRIMARY": "#1E40AF", "BACKGROUND": "#F8FAFC"}))[
        "PRIMARY"
    ]
    assert expected in preview.stdout
    light = subprocess.run(
        [sys.executable, "-m", "pymobile", "preview", "--theme", "light"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert light.returncode == 0 and "#1E40AF" in light.stdout, (light.stdout, light.stderr)


def test_custom_colour_names_survive_dark_mode_and_changes(tmp_path):
    light = Theme("brand", {"CUSTOM": "#123456"})
    app = App(theme=light, bridge=StubBridge(verbose=False), storage_path=str(tmp_path / "s.json"))
    try:
        app.set_theme("dark")
        assert app.theme["CUSTOM"] == "#123456"
        new = Theme("brand", {"CUSTOM": "#ABCDEF"})
        app.set_theme("light")
        app.set_theme(new)
        assert app.theme["CUSTOM"] == "#ABCDEF"
        app.set_theme("dark")
        assert app.theme["CUSTOM"] == "#ABCDEF"
    finally:
        app.stop()


def test_debug_nested_note_once_and_strict_warning_independent(capsys):
    configure("debug")
    tr = Translations(default_language="uk")
    tr.load({"app": {"title": "Hello"}}, language="uk")
    tr.load({"app": {"title": "Again"}}, language="uk", flatten=True)
    assert capsys.readouterr().err.count("nested objects") == 1
    tr.load({"other": {"title": "Not flattened"}}, language="uk", flatten=False)
    assert "nested objects" in capsys.readouterr().err


def test_dark_theme_type_and_name_validation(tmp_path):
    with pytest.raises(TypeError, match="dark_theme"):
        App(dark_theme="dark", storage_path=str(tmp_path / "s.json"))
    with pytest.raises(ValueError, match="named"):
        App(dark_theme=Theme("night"), storage_path=str(tmp_path / "s.json"))


def test_explicit_dark_palette_survives_light_theme_updates(tmp_path):
    dark = Theme("dark", {"PRIMARY": "#AAFFFF"})
    app = App(theme=Theme("first"), dark_theme=dark, storage_path=str(tmp_path / "s.json"))
    try:
        new_light = Theme("next", {"PRIMARY": "#111111"})
        app.set_theme(new_light)
        app.set_theme("dark_mode")
        assert app.theme is dark
        app.set_theme("light_mode")
        assert app.theme is new_light
    finally:
        app.stop()
