import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import pymobile.cli as cli
from pymobile.cli import main
from pymobile.compiler.scaffold import TEMPLATES, create_project


@pytest.fixture(autouse=True)
def restore_framework_logger():
    """Avoid leaking logger configuration into other test modules."""
    logger = logging.getLogger("pymobile")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def unavailable(monkeypatch):
    def fail():
        raise cli.PyMobileError("SDK missing", hint="Select an SDK or install the toolchain")

    monkeypatch.setattr("pymobile.compiler.toolchain.find_toolchain", fail)


def test_doctor_separates_preview_and_native_without_executing_app(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    marker = tmp_path / "executed"
    (tmp_path / "main.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n", encoding="utf-8"
    )
    unavailable(monkeypatch)
    assert main(["-c", str(tmp_path), "doctor"]) == 0
    result = capsys.readouterr()
    text = result.out + result.err
    assert "Desktop preview" in text and "not run" in text
    assert "Native APK" in text and "setup-sdk" in text
    assert not (tmp_path / "executed").exists()
    assert main(["-c", str(tmp_path), "doctor", "--strict"]) == 1


def test_doctor_toolchain_present_does_not_claim_build_tested(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    fake = SimpleNamespace(build_tools=Path("tools"), has_ndk=False, verify=lambda: None)
    monkeypatch.setattr("pymobile.compiler.toolchain.find_toolchain", lambda: fake)
    assert main(["-c", str(tmp_path), "doctor", "--strict"]) == 0
    text = capsys.readouterr().out
    assert "Desktop preview" in text and "Native APK" in text
    assert "not run" in text
    assert "build --native" in text


def test_doctor_missing_entry_has_help_and_does_not_claim_pass(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    (tmp_path / "main.py").unlink()
    unavailable(monkeypatch)
    assert main(["-c", str(tmp_path), "doctor"]) == 1
    result = capsys.readouterr()
    text = result.out + result.err
    assert "entry point missing" in text and "entrypoint" in text
    assert "Desktop preview" in text and "not ready" in text.lower()


def test_doctor_no_project_is_explicit_and_keeps_exit_codes(tmp_path, capsys):
    assert main(["-c", str(tmp_path), "doctor"]) == 0
    result = capsys.readouterr()
    text = result.out + result.err
    assert "no usable project" in text and "init" in text
    assert "not checked" in text
    assert main(["-c", str(tmp_path), "doctor", "--strict"]) == 1


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_generated_readme_has_useful_correct_commands(tmp_path, template):
    result = create_project(tmp_path / "app", "Demo", template=template)
    text = (result.directory / "README.md").read_text(encoding="utf-8")
    assert "{{" not in text
    for part in (
        "main.py",
        "pymobile.toml",
        "assets/",
        "run --web",
        "check-ui",
        "doctor",
        "setup-sdk",
        "build --native",
        "apk/",
        "output_dir",
    ):
        assert part in text, (template, part)
    assert "written to `build/`" not in text
    assert "not installable" in text


def test_doctor_project_problem_blocks_native_status(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    path = tmp_path / "pymobile.toml"
    path.write_text(
        path.read_text().replace('# icon = "assets/icon.png"', 'icon = "assets/missing.png"')
    )
    fake = SimpleNamespace(build_tools=Path("tools"), has_ndk=False, verify=lambda: None)
    monkeypatch.setattr("pymobile.compiler.toolchain.find_toolchain", lambda: fake)
    assert main(["-c", str(tmp_path), "doctor"]) == 1
    result = capsys.readouterr()
    text = result.out + result.err
    assert "icon missing" in text and "Native APK: not ready" in text
    assert "checks passed; build not run" not in text


def test_doctor_invalid_configuration_offers_help(tmp_path, capsys):
    (tmp_path / "pymobile.toml").write_text("[app\nnot valid")
    assert main(["-c", str(tmp_path), "doctor"]) == 0
    result = capsys.readouterr()
    text = result.out + result.err
    assert "no usable project" in text and "not checked" in text
    assert "project directory" in text


def test_generated_examples_work_on_desktop(tmp_path):
    import os
    import subprocess
    import sys

    project = create_project(tmp_path / "app", "Demo").directory
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            filter(None, (str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")))
        ),
        PYMOBILE_STORAGE_DIR=str(tmp_path / "storage"),
    )
    for command in (["preview"], ["check"], ["info"]):
        result = subprocess.run(
            [sys.executable, "-m", "pymobile", *command],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (command, result.stdout, result.stderr)
