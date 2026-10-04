"""Device-free coverage for the optional Android emulator and adb CLI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pymobile.cli as cli
from pymobile.cli import build_parser, main
from pymobile.compiler import sdk_installer
from pymobile.compiler.scaffold import create_project
from pymobile.core.config import load_config


class TestWidgetAddCommand:
    def test_creates_importable_python_and_project_java_sources(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Widget Scaffold")

        assert main(
            [
                "-c",
                str(tmp_path),
                "widget",
                "add",
                "BarChart",
                "-p",
                "title",
                "-p",
                "value:int",
                "-p",
                "values:list",
            ]
        ) == 0

        python_file = tmp_path / "widgets" / "bar_chart.py"
        java_file = (
            tmp_path
            / "java"
            / "org"
            / "pymobile"
            / "app"
            / "widgets"
            / "BarChartRenderer.java"
        )
        python_source = python_file.read_text(encoding="utf-8")
        java_source = java_file.read_text(encoding="utf-8")
        compile(python_source, str(python_file), "exec")
        assert 'register_widget_type("BarChart")' in python_source
        assert "class BarChartRenderer implements WidgetRenderer" in java_source
        assert "TODO: replace this labelled starter view" in java_source
        assert "java/ overlay is compiled automatically" in capsys.readouterr().out


class TestDeviceParser:
    def test_install_and_emulator_options(self) -> None:
        parser = build_parser()
        install = parser.parse_args(
            ["install", "--abi", "x86_64", "--device", "emulator-5554", "--sdk", "/opt/android"]
        )
        assert install.abi == "x86_64"
        assert install.device == "emulator-5554"
        assert install.sdk == "/opt/android"

        start = parser.parse_args(
            ["emulator", "start", "--no-window", "--no-audio", "--sdk", "/opt/android"]
        )
        assert start.emulator_action == "start"
        assert start.no_window is True
        assert start.no_audio is True
        assert start.sdk == "/opt/android"

    def test_emulator_is_opt_in_for_sdk_setup(self) -> None:
        args = build_parser().parse_args(["setup-sdk", "--with-emulator"])
        assert args.with_emulator is True
        assert args.with_ndk is False


def _x86_apk(project: Path) -> Path:
    config = load_config(project)
    config.abis = ["x86_64"]
    config.validate()
    config.output_path.mkdir(parents=True, exist_ok=True)
    apk = config.output_path / config.apk_name
    apk.write_bytes(b"test apk")
    return apk


class TestInstallCommand:
    def test_installs_x86_apk_on_the_only_connected_device(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Device App")
        apk = _x86_apk(tmp_path)
        sdk = tmp_path / "sdk"
        adb = sdk / "platform-tools" / "adb"
        monkeypatch.setattr(cli, "_sdk_executable", lambda *_: adb)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(command)
            if command[-1] == "devices":
                return SimpleNamespace(
                    returncode=0,
                    stdout="List of devices attached\nemulator-5554\tdevice\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="Success\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert main(["-c", str(tmp_path), "install", "--abi", "x86_64"]) == 0
        assert calls == [
            [str(adb), "devices"],
            [str(adb), "-s", "emulator-5554", "install", "-r", str(apk)],
        ]
        assert "installed" in capsys.readouterr().out

    def test_no_connected_device_has_actionable_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "No Device")
        _x86_apk(tmp_path)
        monkeypatch.setattr(cli, "_sdk_executable", lambda *_: tmp_path / "adb")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="List of devices attached\n", stderr=""
            ),
        )

        assert main(["-c", str(tmp_path), "install", "--abi", "x86_64"]) == 1
        error = capsys.readouterr().err
        assert "no Android phone or emulator" in error
        assert "pymobile emulator start" in error
        assert "USB debugging" in error

    def test_multiple_devices_require_a_serial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create_project(tmp_path, "Several Devices")
        _x86_apk(tmp_path)
        monkeypatch.setattr(cli, "_sdk_executable", lambda *_: tmp_path / "adb")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="List of devices attached\nphone-1\tdevice\nemulator-5554\tdevice\n",
                stderr="",
            ),
        )

        assert main(["-c", str(tmp_path), "install", "--abi", "x86_64"]) == 1
        error = capsys.readouterr().err
        assert "more than one Android device" in error
        assert "--device SERIAL" in error
        assert "phone-1" in error and "emulator-5554" in error


def test_device_tools_receive_the_sdk_bundled_jdk_when_java_home_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pymobile.compiler import sdk_installer

    sdk_home = tmp_path / "android-home"
    sdk = sdk_home / "sdk"
    javac = sdk_home / "jdk-17" / "bin" / "javac"
    javac.parent.mkdir(parents=True)
    javac.touch()
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(sdk_installer, "default_sdk_home", lambda: tmp_path / "other-home")

    environment = cli._device_environment(sdk)

    assert environment["JAVA_HOME"] == str(javac.parent.parent)
    assert environment["ANDROID_HOME"] == str(sdk)
    assert f"{javac.parent}{os.pathsep}" in environment["PATH"]


class TestEmulatorCommand:
    def test_start_creates_default_avd_if_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        emulator = tmp_path / "emulator"
        monkeypatch.setattr(cli, "_sdk_executable", lambda *_: emulator)
        created: list[tuple[Path, str]] = []
        monkeypatch.setattr(cli, "_create_avd", lambda root, name: created.append((root, name)))
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(command)
            if command[-1] == "-list-avds":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert main(["emulator", "start", "--sdk", str(tmp_path), "--no-window", "--no-audio"]) == 0
        assert len(created) == 1
        assert created[0][0] == tmp_path.resolve()
        assert created[0][1] == "pymobile-api35-x86_64"
        assert calls[-1] == [
            str(emulator),
            "-avd",
            "pymobile-api35-x86_64",
            "-no-snapshot",
            "-no-window",
            "-no-audio",
        ]
        assert "creating the default virtual device" in capsys.readouterr().out


def test_setup_sdk_passes_the_emulator_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "android-home"
    sdk = target / "sdk"
    seen: dict[str, object] = {}

    def fake_install(home: Path | None = None, **kwargs: object) -> Path:
        seen["home"] = home
        seen.update(kwargs)
        return sdk

    monkeypatch.setattr(sdk_installer, "default_sdk_home", lambda: target)
    monkeypatch.setattr(sdk_installer, "install_sdk", fake_install)

    assert main(["setup-sdk", "--with-emulator", "--path", str(target)]) == 0
    assert seen == {"home": target, "with_ndk": False, "with_emulator": True}
    output = capsys.readouterr().out
    assert "x86_64 emulator" in output
    assert "no NDK required" in output


def test_sdk_installer_adds_emulator_without_ndk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = tmp_path / "android-home" / "sdk"
    manager = sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
    manager.parent.mkdir(parents=True)
    manager.touch()
    monkeypatch.setattr(sdk_installer, "_ensure_jdk", lambda home: home / "jdk-17")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sdk_installer.subprocess, "run", fake_run)

    result = sdk_installer.install_sdk(tmp_path / "android-home", with_emulator=True)

    assert result == sdk
    packages = calls[-1][2:]
    assert packages == [*sdk_installer.MINIMAL_PACKAGES, *sdk_installer.EMULATOR_PACKAGES]
    assert not any(package.startswith("ndk;") for package in packages)


def test_sdk_installer_can_combine_emulator_and_ndk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = tmp_path / "android-home" / "sdk"
    manager = sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
    manager.parent.mkdir(parents=True)
    manager.touch()
    monkeypatch.setattr(sdk_installer, "_ensure_jdk", lambda home: home / "jdk-17")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sdk_installer.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    sdk_installer.install_sdk(
        tmp_path / "android-home", with_ndk=True, with_emulator=True
    )

    packages = calls[-1][2:]
    assert packages == [*sdk_installer.REQUIRED_PACKAGES, *sdk_installer.EMULATOR_PACKAGES]
