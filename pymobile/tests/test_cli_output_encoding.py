"""Reproduce CLI output on pipes using legacy Windows encodings."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pymobile.compiler.scaffold import create_project

ROOT = Path(__file__).resolve().parents[2]


def run_cli(args, cwd, encoding):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
    env["PYTHONIOENCODING"] = encoding + ":strict"
    return subprocess.run(
        [sys.executable, "-m", "pymobile", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("encoding", ["cp1252", "cp1251", "ascii"])
def test_init_in_legacy_encoding(tmp_path, encoding):
    result = run_cli(["init", str(tmp_path / "newapp"), "--template", "basic"], tmp_path, encoding)
    assert result.returncode == 0, result.stderr.decode(encoding)
    assert "created project" in result.stdout.decode(encoding)


@pytest.mark.parametrize("encoding", ["cp1252", "cp1251", "ascii"])
@pytest.mark.parametrize("command", ["preview", "run", "check", "info", "doctor"])
def test_project_commands_in_legacy_encoding(tmp_path, encoding, command):
    project = create_project(tmp_path / "app", "Demo").directory
    (project / "main.py").write_text(
        'from pymobile import App, Label\nApp("Demo").run(Label("Hello Привіт"))\n',
        encoding="utf-8",
    )
    result = run_cli([command], project, encoding)
    assert result.returncode == 0, (command, result.stderr.decode(encoding))
    output = result.stdout.decode(encoding)
    if command in ("preview", "run"):
        assert "Hello" in output
        if encoding == "cp1251":
            assert "Привіт" in output
        else:
            assert "\\u041f\\u0440" in output
    elif command == "doctor":
        assert "Desktop preview" in output


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_info_json_remains_machine_readable(tmp_path, encoding):
    project = create_project(tmp_path / "app", "Demo").directory
    result = run_cli(["info", "--json"], project, encoding)
    assert result.returncode == 0, result.stderr.decode(encoding)
    assert json.loads(result.stdout.decode(encoding))["name"] == "Demo"


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_error_and_hint_are_printed_with_legacy_encoding(tmp_path, encoding):
    result = run_cli(["info"], tmp_path, encoding)
    assert result.returncode == 1
    error = result.stderr.decode(encoding)
    assert "hint:" in error
    assert "unexpected error" not in error


def test_utf8_preview_preserves_unicode(tmp_path):
    project = create_project(tmp_path / "app", "Demo").directory
    (project / "main.py").write_text(
        'from pymobile import App, Label\nApp("Demo").run(Label("Hello Привіт"))\n',
        encoding="utf-8",
    )
    result = run_cli(["preview"], project, "utf-8")
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert "Привіт" in result.stdout.decode("utf-8")
