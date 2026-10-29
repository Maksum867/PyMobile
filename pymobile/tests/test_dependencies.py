"""Third-party wheel collection, startup and cache regression coverage."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from pymobile.compiler.dependencies import prepare_dependencies
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.core.config import ProjectConfig, load_config
from pymobile.errors import ConfigError, PyMobileError


def _fake_install(monkeypatch):
    calls = []

    def install(command, **kwargs):
        calls.append(command)
        destination = Path(command[command.index("--target") + 1])
        (destination / "demo").mkdir(parents=True)
        (destination / "demo" / "__init__.py").write_bytes(b"VALUE = 17\n")
        (destination / "demo" / "cert.pem").write_text("package data")
        metadata = destination / "demo-1.0.dist-info"
        metadata.mkdir()
        (metadata / "WHEEL").write_text(
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("pymobile.compiler.dependencies.subprocess.run", install)
    return calls


def test_config_reads_pymobile_and_project_dependencies(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["requests==2.32.5"]\n')
    (tmp_path / "pymobile.toml").write_text('[app]\nname = "App"\n')
    assert load_config(tmp_path).dependencies == ["requests==2.32.5"]
    (tmp_path / "pymobile.toml").write_text("[app]\ndependencies = []\n")
    assert load_config(tmp_path).dependencies == []
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests"]\n[tool.pymobile]\nname = "App"\n'
    )
    (tmp_path / "pymobile.toml").unlink()
    assert load_config(tmp_path).dependencies == ["requests"]


@pytest.mark.parametrize(
    "requirements", ["requests", [3], ["-r secret"], ["https://x"], ["pkg @ https://x"]]
)
def test_invalid_requirements(requirements):
    with pytest.raises(ConfigError):
        ProjectConfig(dependencies=requirements)


def test_install_flags_cache_and_package_data(project, monkeypatch):
    calls = _fake_install(monkeypatch)
    project.dependencies = ["demo==1.0"]
    project.entrypoint_path.write_text("from demo import VALUE\nassert VALUE == 17\n")
    first = BuildPipeline(project, check_startup=False).run()
    with zipfile.ZipFile(first.apk) as apk:
        assert apk.read("assets/app/demo/__init__.py") == b"VALUE = 17\n"
        assert apk.read("assets/app/demo/cert.pem") == b"package data"
        assert "assets/app/demo-1.0.dist-info/WHEEL" in apk.namelist()
        assert not any(".pymobile-requirements" in name for name in apk.namelist())
    assert len(calls) == 1
    assert all(
        flag in calls[0]
        for flag in ("--only-binary=:all:", "--platform", "any", "--python-version")
    )
    assert BuildPipeline(project, check_startup=False).run().cached
    assert len(calls) == 1
    (project.root / ".pymobile/dependencies/demo/__init__.py").write_text("VALUE = 18\n")
    assert not BuildPipeline(project, check_startup=False).run().cached


def test_startup_can_import_dependency(project, monkeypatch):
    _fake_install(monkeypatch)
    project.dependencies = ["demo==1.0"]
    project.entrypoint_path.write_text(
        "from demo import VALUE\nassert VALUE == 17\n"
        "from pymobile import App, Screen, Label\n"
        "class Home(Screen):\n    def build(self): return Label(str(VALUE))\n"
        "App('Demo').run(Home())\n"
    )
    result = BuildPipeline(project, check_startup=True).run()
    assert result.apk.exists()


def test_dependency_collision_fails(project, monkeypatch):
    _fake_install(monkeypatch)
    project.dependencies = ["demo==1.0"]
    (project.source_path / "demo").mkdir()
    (project.source_path / "demo/__init__.py").write_text("VALUE = 99")
    with pytest.raises(PyMobileError, match="conflict"):
        BuildPipeline(project, check_startup=False).run()


def test_native_extension_rejected(project, monkeypatch):
    _fake_install(monkeypatch)
    project.dependencies = ["demo==1.0"]
    prepare_dependencies(project)
    (project.root / ".pymobile/dependencies/demo/native.so").write_bytes(b"ELF")
    # A corrupt cached tree is never shipped; a reinstallation cleans it.
    assert all(name != "demo/native.so" for name, _ in prepare_dependencies(project))


def test_native_extension_in_installed_wheel_rejected(project, monkeypatch):
    _fake_install(monkeypatch)
    project.dependencies = ["demo==1.0"]
    from pymobile.compiler import dependencies

    install = dependencies.subprocess.run

    def broken(command, **kwargs):
        result = install(command, **kwargs)
        target = Path(command[command.index("--target") + 1])
        (target / "demo/extension.so").write_bytes(b"ELF")
        return result

    monkeypatch.setattr(dependencies.subprocess, "run", broken)
    with pytest.raises(PyMobileError, match="native/binary"):
        prepare_dependencies(project)
