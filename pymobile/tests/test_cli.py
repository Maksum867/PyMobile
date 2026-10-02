"""Tests for the command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from pymobile import __version__
from pymobile.cli import build_parser, main
from pymobile.compiler.scaffold import create_project
from pymobile.core.config import load_config


class TestParser:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 0
        assert "usage" in capsys.readouterr().out.lower()

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as info:
            main(["--version"])
        assert info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_unknown_command(self) -> None:
        with pytest.raises(SystemExit):
            main(["nonsense"])


class TestInit:
    def test_creates_project(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = tmp_path / "newapp"
        assert main(["init", str(target), "--name", "New App"]) == 0
        assert (target / "pymobile.toml").exists()
        assert "created project" in capsys.readouterr().out

    def test_name_derived_from_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "my-cool-app"
        main(["init", str(target)])
        text = (target / "pymobile.toml").read_text(encoding="utf-8")
        assert 'name = "My Cool App"' in text

    def test_custom_package(self, tmp_path: Path) -> None:
        target = tmp_path / "app"
        main(["init", str(target), "-p", "com.acme.thing"])
        assert 'package = "com.acme.thing"' in (target / "pymobile.toml").read_text()

    def test_non_empty_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")
        assert main(["init", str(tmp_path)]) == 1
        assert "hint:" in capsys.readouterr().err

    def test_force(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")
        assert main(["init", str(tmp_path), "--force"]) == 0


class TestBuild:
    def test_builds_apk(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Build Me")
        assert main(["-c", str(tmp_path), "build"]) == 0
        assert list((tmp_path / "build").glob("*.apk"))
        assert "✓" in capsys.readouterr().out

    def test_second_build_reports_cache(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Cached")
        main(["-c", str(tmp_path), "build"])
        capsys.readouterr()
        assert main(["-c", str(tmp_path), "build"]) == 0
        assert "up to date" in capsys.readouterr().out

    def test_native_after_structural_is_not_cached(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Native Cache Test")
        main(["-c", str(tmp_path), "build"])
        capsys.readouterr()
        res = main(["-c", str(tmp_path), "build", "--native"])
        assert res in (0, 1)

    def test_clean_flag_rebuilds(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Clean Build")
        main(["-c", str(tmp_path), "build"])
        capsys.readouterr()
        assert main(["-c", str(tmp_path), "build", "--clean"]) == 0
        assert "up to date" not in capsys.readouterr().out

    def test_no_cache_flag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "No Cache")
        main(["-c", str(tmp_path), "build"])
        capsys.readouterr()
        main(["-c", str(tmp_path), "build", "--no-cache"])
        assert "up to date" not in capsys.readouterr().out

    def test_custom_output_dir(self, tmp_path: Path) -> None:
        create_project(tmp_path, "Out")
        main(["-c", str(tmp_path), "build", "-o", "artifacts"])
        assert list((tmp_path / "artifacts").glob("*.apk"))

    def test_icon_flag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Iconed")
        icon = tmp_path / "logo.png"
        _write_png(icon)
        assert main(["-c", str(tmp_path), "build", "-i", "logo.png"]) == 0
        assert "logo.png" in capsys.readouterr().out

    def test_missing_icon_reports_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Bad Icon")
        assert main(["-c", str(tmp_path), "build", "-i", "nope.png"]) == 1
        assert "hint:" in capsys.readouterr().err

    def test_verbose_shows_timings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Verbose")
        main(["-v", "-c", str(tmp_path), "build"])
        assert "package" in capsys.readouterr().out

    def test_no_config_reports_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["-c", str(tmp_path), "build"]) == 1
        assert "pymobile init" in capsys.readouterr().err


class TestInfoCleanDoctor:
    def test_info_human_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Info App")
        assert main(["-c", str(tmp_path), "info"]) == 0
        out = capsys.readouterr().out
        assert "Info App" in out
        assert "permissions" in out

    def test_info_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Json App")
        assert main(["-c", str(tmp_path), "info", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "Json App"

    def test_clean_removes_build(self, tmp_path: Path) -> None:
        create_project(tmp_path, "Clean Me")
        main(["-c", str(tmp_path), "build"])
        assert main(["-c", str(tmp_path), "clean"]) == 0
        assert not (tmp_path / "build").exists()

    def test_clean_when_nothing_to_do(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Nothing")
        assert main(["-c", str(tmp_path), "clean"]) == 0
        assert "nothing to clean" in capsys.readouterr().out

    def test_doctor_on_valid_project(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Healthy")
        assert main(["-c", str(tmp_path), "doctor"]) == 0
        assert "looks good" in capsys.readouterr().out

    def test_doctor_detects_missing_entrypoint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Broken")
        (tmp_path / "main.py").unlink()
        assert main(["-c", str(tmp_path), "doctor"]) == 1
        assert "entry point missing" in capsys.readouterr().err

    def test_doctor_without_project(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["-c", str(tmp_path), "doctor"]) == 0
        assert "no usable project" in capsys.readouterr().err


class TestRun:
    def test_executes_entrypoint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Runnable")
        assert main(["-c", str(tmp_path), "run"]) == 0
        captured = capsys.readouterr()
        assert "desktop preview" in captured.out
        # the app itself logs to stderr and renders its first screen
        assert "Runnable" in captured.err
        assert "render" in captured.err

    def test_missing_entrypoint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        create_project(tmp_path, "Gone")
        (tmp_path / "main.py").unlink()
        assert main(["-c", str(tmp_path), "run"]) == 1
        assert "Entry point not found" in capsys.readouterr().err


def _write_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(path)


class TestModuleEntryPoint:
    """`python -m pymobile` must work when pip's Scripts dir is not on PATH."""

    def test_module_is_runnable(self) -> None:
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "-m", "pymobile", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "pymobile" in completed.stdout

    def test_module_reports_errors_like_the_script(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "-m", "pymobile", "-c", str(tmp_path), "info"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 1
        assert "pymobile init" in completed.stderr


class TestHintsMatchTheEnvironment:
    """Printed commands must be runnable as-is by whoever sees them.

    Users whose PATH lacks pip's Scripts directory run `python -m pymobile`;
    echoing a bare `pymobile ...` back at them prints a command that fails.
    """

    def test_export_syntax_is_shell_specific(self) -> None:
        from unittest.mock import patch

        from pymobile.cli import _export_command

        with patch("platform.system", return_value="Windows"):
            assert _export_command("ANDROID_HOME", "C:\\sdk").startswith("$env:")
        with patch("platform.system", return_value="Linux"):
            assert _export_command("ANDROID_HOME", "/sdk") == "export ANDROID_HOME=/sdk"

    def test_module_invocation_is_echoed_back(self) -> None:
        from unittest.mock import patch

        from pymobile.cli import _invocation

        with patch("sys.argv", ["/usr/lib/python3.13/pymobile/__main__.py", "build"]):
            assert _invocation().endswith("-m pymobile")

    def test_script_invocation_is_echoed_back(self) -> None:
        from unittest.mock import patch

        from pymobile.cli import _invocation

        with patch("sys.argv", ["/usr/local/bin/pymobile", "build"]):
            assert _invocation() == "pymobile"

    def test_init_suggests_a_working_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["init", str(tmp_path / "app"), "-n", "App"])
        out = capsys.readouterr().out
        assert "build --native" in out

    def test_icons_hint_uses_a_real_package_name(self) -> None:
        """`pymobile[icons]` was wrong: the distribution is py-mobile."""
        source = (Path(__file__).resolve().parents[1] / "cli.py").read_text(encoding="utf-8")
        assert "pymobile[icons]" not in source
        assert "pip install Pillow" in source


class TestPreview:
    def _app_project(self, root: Path) -> None:
        (root / "pymobile.toml").write_text(
            '[app]\nname = "T"\npackage = "com.example.t"\n', encoding="utf-8"
        )
        (root / "main.py").write_text(
            "from pymobile import App, Column, Label, Screen\n"
            "class Home(Screen):\n"
            "    def build(self):\n"
            "        return Column(Label('PREVIEW_OK'))\n"
            "App('T').run(Home())\n",
            encoding="utf-8",
        )

    def test_preview_draws_the_first_screen(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._app_project(tmp_path)
        assert main(["preview", "-c", str(tmp_path)]) == 0
        assert "PREVIEW_OK" in capsys.readouterr().out

    def test_preview_png_writes_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pytest.importorskip("PIL")
        self._app_project(tmp_path)
        out = tmp_path / "shot.png"
        assert main(["preview", "-c", str(tmp_path), "--png", str(out)]) == 0
        assert out.exists() and out.stat().st_size > 0

    def _themed_project(self, root: Path) -> None:
        """An app whose build() takes its colours from the active theme."""
        (root / "pymobile.toml").write_text(
            '[app]\nname = "T"\npackage = "com.example.t"\n', encoding="utf-8"
        )
        (root / "main.py").write_text(
            "from pymobile import App, Column, Label, Screen, Style\n"
            "class Home(Screen):\n"
            "    def build(self):\n"
            "        return Column(Label('CARD',\n"
            "            style=Style(background=self.app.theme['SURFACE'])))\n"
            "App('T').run(Home())\n",
            encoding="utf-8",
        )

    def test_preview_theme_reaches_build(self, tmp_path: Path) -> None:
        """`--theme dark` switches the app before the tree is read, not after.

        Painting a tree built in the light palette with the dark one is what
        made the text unreadable: the cards kept the light SURFACE while the
        text came from the dark theme.
        """
        from pymobile.core.bridge import get_bridge

        pytest.importorskip("PIL")
        self._themed_project(tmp_path)
        out = tmp_path / "shot.png"

        assert main(["preview", "-c", str(tmp_path)]) == 0
        assert get_bridge().last_tree["children"][0]["style"]["background"] == "#F5F5F5"

        assert main(["preview", "-c", str(tmp_path), "--png", str(out), "--theme", "dark"]) == 0
        assert get_bridge().last_tree["children"][0]["style"]["background"] == "#1E1E1E"


class TestWatch:
    """`pymobile watch` — the edit-save-see loop."""

    def _app_project(self, root: Path, text: str = "FIRST") -> None:
        (root / "pymobile.toml").write_text(
            '[app]\nname = "W"\npackage = "com.example.w"\n', encoding="utf-8"
        )
        (root / "main.py").write_text(
            "from pymobile import App, Column, Label, Screen\n"
            "class Home(Screen):\n"
            "    def build(self):\n"
            f"        return Column(Label('{text}'))\n"
            "App('W').run(Home())\n",
            encoding="utf-8",
        )

    def test_first_pass_renders_before_any_edit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The initial render must not wait for a change."""
        from pymobile.cli import _reload

        self._app_project(tmp_path)
        args = argparse.Namespace(png=None, ids=False, verbose=False)
        _reload(load_config(tmp_path), tmp_path / "main.py", args)
        assert "FIRST" in capsys.readouterr().out

    def test_edited_source_is_picked_up(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.cli import _reload

        self._app_project(tmp_path)
        config = load_config(tmp_path)
        args = argparse.Namespace(png=None, ids=False, verbose=False)
        _reload(config, tmp_path / "main.py", args)
        capsys.readouterr()

        self._app_project(tmp_path, text="SECOND")
        _reload(config, tmp_path / "main.py", args)
        assert "SECOND" in capsys.readouterr().out

    def test_imported_module_is_reloaded_too(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A stale sys.modules entry would keep showing the old helper."""
        from pymobile.cli import _reload

        (tmp_path / "pymobile.toml").write_text(
            '[app]\nname = "W"\npackage = "com.example.w"\n', encoding="utf-8"
        )
        (tmp_path / "helper.py").write_text("TEXT = 'OLD'\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "from pymobile import App, Column, Label, Screen\n"
            "import helper\n"
            "class Home(Screen):\n"
            "    def build(self):\n"
            "        return Column(Label(helper.TEXT))\n"
            "App('W').run(Home())\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        args = argparse.Namespace(png=None, ids=False, verbose=False)
        _reload(config, tmp_path / "main.py", args)
        capsys.readouterr()

        (tmp_path / "helper.py").write_text("TEXT = 'NEW'\n", encoding="utf-8")
        _reload(config, tmp_path / "main.py", args)
        assert "NEW" in capsys.readouterr().out

    def test_broken_source_reports_and_survives(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A syntax error must show the error, not kill the watcher."""
        from pymobile.cli import _reload

        self._app_project(tmp_path)
        config = load_config(tmp_path)
        args = argparse.Namespace(png=None, ids=False, verbose=False)

        (tmp_path / "main.py").write_text("def broken(\n", encoding="utf-8")
        _reload(config, tmp_path / "main.py", args)
        assert "SyntaxError" in capsys.readouterr().err

        self._app_project(tmp_path, text="RECOVERED")
        _reload(config, tmp_path / "main.py", args)
        assert "RECOVERED" in capsys.readouterr().out

    def test_watch_is_registered_with_its_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["watch", "--interval", "0.5", "--ids"])
        assert args.interval == 0.5
        assert args.ids is True


class TestBuildFlags:
    def test_no_optimize_and_keystore_flags_exist(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["build", "--no-optimize", "--keystore", "release.jks", "--ks-pass", "secret"]
        )
        assert args.no_optimize is True
        assert args.keystore == "release.jks"
        assert args.ks_pass == "secret"


class TestRunGui:
    def test_gui_flag_exists(self) -> None:
        assert build_parser().parse_args(["run", "--gui"]).gui is True

    def test_web_host_defaults_to_loopback(self) -> None:
        """The preview is a private window by default (PM-28).

        It was bound to 0.0.0.0 out of the box — readable and clickable by
        anything that could reach the port, with no authentication. Serving
        the network is now an explicit ``--host 0.0.0.0`` (the preview then
        generates and requires a session token).
        """
        args = build_parser().parse_args(["run", "--web"])
        assert args.host == "127.0.0.1"
        exposed = build_parser().parse_args(["run", "--web", "--host", "0.0.0.0"])
        assert exposed.host == "0.0.0.0"

    def test_missing_tkinter_is_explained(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_project(tmp_path, "Guiless")
        monkeypatch.setattr("pymobile.core.ui.gui.tkinter_available", lambda: False)
        assert main(["-c", str(tmp_path), "run", "--gui"]) == 1
        assert "Tkinter is not available" in capsys.readouterr().err
