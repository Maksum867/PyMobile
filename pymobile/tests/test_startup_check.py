"""Real subprocess coverage of the opt-in build startup check."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pymobile import App, Label, Screen, TextInput, cli
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.startup import check_startup
from pymobile.core.bridge import active_bridge
from pymobile.errors import PyMobileError

APP_SOURCE = """from pymobile import App, Label, Screen
class Home(Screen):
    def build(self):
        return Label("Started")
App("Smoke").run(Home())
"""


def test_valid_startup_does_not_mutate_parent_state(project):
    project.entrypoint_path.write_text(APP_SOURCE)
    app, bridge, argv, path = App.current(), active_bridge(), sys.argv[:], sys.path[:]
    check_startup(project)
    assert App.current() is app
    assert active_bridge() is bridge
    assert sys.argv == argv
    assert sys.path == path


@pytest.mark.parametrize(
    "source,expected",
    [
        ('print("hello")', "rendered nothing"),
        ('# App().run(Home())\nprint("run()")', "rendered nothing"),
        ("if False:\n    app.run(home)", "rendered nothing"),
        ("class Other:\n    def run(self): pass\nOther().run()", "rendered nothing"),
        ('raise RuntimeError("bad launch")', "RuntimeError: bad launch"),
        ("import sys; sys.exit(2)", "SystemExit: 2"),
        ("import sys; sys.exit(0)", "rendered nothing"),
        ("import os; os._exit(0)", "exited without a result"),
        ("this is not valid python!", "SyntaxError"),
    ],
)
def test_rejects_broken_startup(project, source, expected):
    project.entrypoint_path.write_text(source)
    with pytest.raises(PyMobileError, match=expected):
        check_startup(project)


def test_delegated_startup_and_project_relative_assets(project):
    (project.source_path / "startup_module.py").write_text(APP_SOURCE)
    (project.root / "asset.txt").write_text("asset data")
    project.entrypoint_path.write_text(
        "from pathlib import Path\n"
        'assert Path("asset.txt").read_text() == "asset data"\n'
        "import startup_module\n"
    )
    check_startup(project)


def test_report_channel_is_not_confused_by_stdout(project):
    project.entrypoint_path.write_text("print('{\"ok\": true}')\n")
    with pytest.raises(PyMobileError, match="rendered nothing") as caught:
        check_startup(project)
    assert '{"ok": true}' in caught.value.hint


def test_slow_entrypoint_times_out(project):
    project.entrypoint_path.write_text("import time; time.sleep(30)")
    with pytest.raises(PyMobileError, match="timed out"):
        check_startup(project, timeout=0.2)


def test_default_storage_is_redirected(project, monkeypatch, tmp_path):
    real_storage = tmp_path / "user-storage"
    real_storage.mkdir()
    original = real_storage / "existing.json"
    original.write_text('{"untouched": true}')
    monkeypatch.setenv("PYMOBILE_STORAGE_DIR", str(real_storage))
    project.entrypoint_path.write_text(APP_SOURCE + 'App.current().storage.set("probe", 123)\n')
    check_startup(project)
    assert list(real_storage.iterdir()) == [original]
    assert original.read_text() == '{"untouched": true}'


def test_pipeline_fails_before_toolchain_or_packaging(project):
    pipeline = BuildPipeline(project, native=True, check_startup=True)
    with pytest.raises(PyMobileError, match="rendered nothing"):
        pipeline.run()
    assert not project.output_path.exists()


def test_cli_check_startup_fails_before_native_toolchain(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load", lambda args: project)
    assert cli.main(["build", "--native", "--check-startup"]) == 1
    output = capsys.readouterr()
    assert "rendered nothing" in output.err
    assert "built " not in output.out
    assert "setup-sdk" not in output.err


@pytest.mark.skipif(sys.platform != "linux", reason="checks Linux headless Tk and SIGINT")
def test_real_headless_dev_falls_back_and_ctrl_c_exits(tmp_path):
    pytest.importorskip("tkinter")
    (tmp_path / "main.py").write_text(APP_SOURCE)
    (tmp_path / "pymobile.toml").write_text(
        '[app]\nname="Headless"\npackage="org.example.headless"\n'
    )
    environment = os.environ.copy()
    environment.pop("DISPLAY", None)
    environment.pop("WAYLAND_DISPLAY", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment["PYMOBILE_STORAGE_DIR"] = str(tmp_path / "storage")
    launcher = (
        "import os, signal, threading; "
        "timer=threading.Timer(1.5, lambda: os.kill(os.getpid(), signal.SIGINT)); "
        "timer.daemon=True; timer.start(); "
        'from pymobile.cli import main; raise SystemExit(main(["dev"]))'
    )
    process = subprocess.run(
        [sys.executable, "-c", launcher],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "Cannot open a Tk display" in process.stderr
    assert "using text watch" in process.stderr
    assert "watching" in process.stdout
    assert "stopped" in process.stdout
    assert "unexpected error" not in process.stderr


def test_pop_retains_explicitly_owned_input_and_rebuilds_once(bridge, tmp_path):
    builds, shown = [], []

    class Home(Screen):
        def __init__(self):
            super().__init__()
            self.field = TextInput("draft", id="draft")

        def build(self):
            builds.append(1)
            return self.field

        def on_show(self):
            shown.append(self.is_current)
            # on_show may itself refresh; pop must not build twice.
            self.refresh()

    class Form(Screen):
        def build(self):
            return Label("Form")

    app = App("Lifecycle", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    try:
        home = Home()
        app.run(home)
        home.field.value = "unsaved draft"
        app.push(Form())
        before = len(builds)
        app.pop()
        assert len(builds) == before + 1
        assert home.get("draft") is home.field
        assert home.field.value == "unsaved draft"
        assert all(shown)
    finally:
        app.stop()


def test_dev_parser_matches_watch_navigation_and_fallback_defaults():
    args = cli.build_parser().parse_args(
        [
            "dev",
            "--screen",
            "Details",
            "--args",
            '{"score": 7}',
            "--set",
            "title=Demo",
        ]
    )
    assert args.screen_args == '{"score": 7}'
    assert args.screen_set == ["title=Demo"]
    assert args.png is None
    assert args.text is False


@pytest.mark.parametrize("background,expected", [("#ffffff", 1), ("#000000", 0)])
def test_check_ui_uses_custom_app_theme_without_override(
    project, monkeypatch, capsys, background, expected
):
    import json

    project.entrypoint_path.write_text(
        "from pymobile import App, Label, Screen, Style, Theme\n"
        "class Home(Screen):\n"
        "    def build(self):\n"
        '        return Label("Grey", style=Style(color="#eeeeee"))\n'
        f'App("Custom", theme=Theme("custom", {{"BACKGROUND": "{background}"}})).run(Home())\n'
    )
    monkeypatch.setattr(cli, "_load", lambda args: project)
    assert cli.main(["check-ui", "--strict", "--json"]) == expected
    issues = json.loads(capsys.readouterr().out)
    assert any(issue["code"] == "contrast" for issue in issues) == bool(expected)


@pytest.mark.parametrize("already_exited", [False, True])
def test_windows_timeout_terminates_tree_before_reaping(monkeypatch, already_exited):
    from unittest.mock import Mock

    from pymobile.compiler import startup

    events = []
    process = Mock(pid=12345)
    process.poll.return_value = 0 if already_exited else None
    process.kill.side_effect = lambda: events.append("kill")
    process.wait.side_effect = lambda: events.append("wait")

    def taskkill(command, **kwargs):
        events.append("tree")
        assert Path(command[0]).name == "taskkill.exe"
        assert command[1:] == ["/PID", "12345", "/T", "/F"]
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdin"] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(startup.subprocess, "run", taskkill)
    startup._terminate_probe(process, windows=True)
    assert events == (["tree", "wait"] if already_exited else ["tree", "kill", "wait"])


@pytest.mark.parametrize(
    "failure", [OSError("unavailable"), subprocess.TimeoutExpired("taskkill", 10)]
)
def test_windows_taskkill_failure_still_reaps_direct_child(monkeypatch, failure):
    from unittest.mock import Mock

    from pymobile.compiler import startup

    process = Mock(pid=12345)
    process.poll.return_value = None
    monkeypatch.setattr(startup.subprocess, "run", Mock(side_effect=failure))
    startup._terminate_probe(process, windows=True)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with()


def test_timeout_under_fresh_venv_cleans_temporary_directory(project, tmp_path, monkeypatch):
    import tempfile
    import venv

    from pymobile.compiler import startup

    # On Windows this executable is the venv redirector, not the base Python.
    # No pip/network is needed; inherit installed dependencies from the runner.
    environment = tmp_path / "probe-venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr(startup.sys, "executable", str(executable))
    real_temporary_directory = tempfile.TemporaryDirectory
    created = []

    def temporary_directory(**kwargs):
        directory = real_temporary_directory(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(startup.tempfile, "TemporaryDirectory", temporary_directory)
    project.entrypoint_path.write_text("import time; time.sleep(30)")
    with pytest.raises(startup.StartupCheckError, match="timed out"):
        check_startup(project, timeout=0.2)
    assert created
    assert all(not directory.exists() for directory in created)
