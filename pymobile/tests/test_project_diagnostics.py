"""Offline tests of doctor, wheel checks, scoped adb logs and APK size attribution."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import pymobile.cli as cli
from pymobile.cli import main
from pymobile.compiler import dependency_checks
from pymobile.compiler.scaffold import create_project


def test_doctor_strict_missing_sdk(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Diagnostic")
    monkeypatch.setattr(
        "pymobile.compiler.toolchain.find_toolchain",
        lambda: (_ for _ in ()).throw(cli.PyMobileError("SDK missing")),
    )
    assert main(["-c", str(tmp_path), "doctor"]) == 0
    capsys.readouterr()
    assert main(["-c", str(tmp_path), "doctor", "--strict"]) == 1
    assert "setup-sdk" in capsys.readouterr().err


def test_doctor_missing_pip_with_dependencies(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Diagnostic")
    config = tmp_path / "pymobile.toml"
    config.write_text(config.read_text() + '\ndependencies = ["example==1.0"]\n')
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert main(["-c", str(tmp_path), "doctor"]) == 1
    assert "ensurepip" in capsys.readouterr().err


def test_deps_check_downloads_universal_wheels(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    config = tmp_path / "pymobile.toml"
    config.write_text(config.read_text() + '\ndependencies = ["requests==2.32.5"]\n')
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        with zipfile.ZipFile(Path(command[command.index("--dest") + 1]) / "demo-1.whl", "w") as z:
            z.writestr("demo-1.dist-info/WHEEL", "Root-Is-Purelib: true\n")
            z.writestr("demo/__init__.py", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(dependency_checks.subprocess, "run", fake_run)
    assert main(["-c", str(tmp_path), "deps", "check"]) == 0
    assert "universal wheels" in capsys.readouterr().out
    assert "--platform" in commands[0] and "any" in commands[0]
    assert "--python-version" in commands[0]


def test_deps_check_rejects_binary_and_failure(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo")
    config = tmp_path / "pymobile.toml"
    config.write_text(config.read_text() + '\ndependencies = ["bad==1.0"]\n')

    def binary(command, **kwargs):
        with zipfile.ZipFile(Path(command[command.index("--dest") + 1]) / "bad.whl", "w") as z:
            z.writestr("bad-1.dist-info/WHEEL", "Root-Is-Purelib: true\n")
            z.writestr("bad/native.so", "elf")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(dependency_checks.subprocess, "run", binary)
    assert main(["-c", str(tmp_path), "deps", "check"]) == 1
    assert "binary extension" in capsys.readouterr().err
    monkeypatch.setattr(
        dependency_checks.subprocess,
        "run",
        lambda c, **kw: subprocess.CompletedProcess(c, 1, "", "network down"),
    )
    assert main(["-c", str(tmp_path), "deps", "check"]) == 1
    assert "network down" in capsys.readouterr().err


def _zip_apk(path):
    record = io.StringIO()
    csv.writer(record).writerow(["demo/__init__.py", "", ""])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("assets/app/main.py", "app")
        z.writestr("assets/app/demo/__init__.py", "vendor")
        z.writestr("assets/app/demo-1.dist-info/RECORD", record.getvalue())
        z.writestr("assets/app/pymobile/__init__.py", "framework")
        z.writestr("assets/python/lib/python3.14/os.py", "runtime")
        z.writestr("assets/icon.png", "resource")


def test_size_json_attributes_dependencies_and_totals(tmp_path, capsys):
    create_project(tmp_path, "Demo")
    apk = tmp_path / "size.apk"
    _zip_apk(apk)
    assert main(["-c", str(tmp_path), "size", str(apk), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == apk.stat().st_size
    assert set(report["categories"]) == {
        "runtime",
        "framework",
        "dependencies",
        "app",
        "resources",
        "archive/signing overhead",
    }
    assert sum(report["categories"].values()) == report["total"]
    assert report["dependencies"]["demo-1.dist-info"] > 0


def test_size_bad_apk(tmp_path, capsys):
    create_project(tmp_path, "Demo")
    assert main(["-c", str(tmp_path), "size"]) == 1
    assert "APK not found" in capsys.readouterr().err


def _mock_adb(monkeypatch, tmp_path, *, pid="42", package="com.example.demo"):
    monkeypatch.setattr(cli, "_sdk_executable", lambda *_: tmp_path / "adb")
    lines = (
        "10-29 12:00:00.000  42  44 E pymobile: Traceback (most recent call last):\n"
        "10-29 12:00:00.001  42  44 E pymobile: ModuleNotFoundError: No module named 'foo'\n"
        "10-29 12:00:00.002  99  99 E pymobile: another app's traceback\n"
    )
    if not pid:
        lines += f"10-29 12:00:00.003  42  44 E AndroidRuntime: Process: {package}, PID: 42\n"

    def fake_run(command, **kwargs):
        if command[-1] == "devices":
            return SimpleNamespace(
                returncode=0, stdout="List of devices attached\nphone\tdevice\n", stderr=""
            )
        if "pidof" in command:
            return SimpleNamespace(returncode=0 if pid else 1, stdout=pid, stderr="")
        return SimpleNamespace(returncode=0, stdout=lines, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.mark.parametrize("pid", ["42", ""])
def test_logs_scoped_to_app_pid(tmp_path, monkeypatch, capsys, pid):
    create_project(tmp_path, "Demo", package="com.example.demo")
    _mock_adb(monkeypatch, tmp_path, pid=pid)
    assert main(["-c", str(tmp_path), "logs"]) == 0
    output = capsys.readouterr().out
    assert "ModuleNotFoundError" in output
    assert "another app's traceback" not in output


def test_logs_no_pid_does_not_leak_other_apps(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo", package="com.example.demo")
    _mock_adb(monkeypatch, tmp_path, pid="", package="org.other.app")
    assert main(["-c", str(tmp_path), "logs"]) == 1
    assert "no recent logs" in capsys.readouterr().err


def test_logs_follow_requires_running_app(tmp_path, monkeypatch, capsys):
    create_project(tmp_path, "Demo", package="com.example.demo")
    _mock_adb(monkeypatch, tmp_path, pid="")
    assert main(["-c", str(tmp_path), "logs", "--follow"]) == 1
    assert "not running" in capsys.readouterr().err


def test_logs_follow_uses_pid_filter(tmp_path, monkeypatch):
    create_project(tmp_path, "Demo", package="com.example.demo")
    monkeypatch.setattr(cli, "_sdk_executable", lambda *_: tmp_path / "adb")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[-1] == "devices":
            return SimpleNamespace(
                returncode=0, stdout="List of devices attached\nphone\tdevice\n", stderr=""
            )
        if "pidof" in command:
            return SimpleNamespace(returncode=0, stdout="42\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert main(["-c", str(tmp_path), "logs", "--follow"]) == 0
    assert commands[-1][-4:] == ["logcat", "--pid=42", "-v", "threadtime"]
