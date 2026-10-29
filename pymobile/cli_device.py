"""Focused CLI implementations; cli retains replaceable compatibility entry points."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
from pathlib import Path

from . import cli
from .errors import PyMobileError


def _sdk_root_for_device(args: argparse.Namespace) -> Path:
    """Find the SDK for adb/emulator commands without requiring build tools.

    Delegates to the one resolver in :mod:`pymobile.compiler.toolchain`, so
    ``PYMOBILE_SDK_HOME``, ``ANDROID_HOME`` and the managed install are read
    the same way by every command (P1: they used to disagree).
    """
    from .compiler.sdk_installer import default_sdk_home
    from .compiler.toolchain import _sdk_root

    resolved = _sdk_root(getattr(args, "sdk", None))
    return resolved if resolved is not None else default_sdk_home() / "sdk"


def _sdk_executable(root: Path, relative: str, name: str) -> Path:
    """Return an SDK tool or fail with a command that installs it."""
    suffixes = (".bat", ".cmd", ".exe", "") if platform.system() == "Windows" else ("",)
    for suffix in suffixes:
        candidate = root / relative / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    raise PyMobileError(
        f"Android SDK tool {name} was not found",
        hint=(
            "Run `pymobile setup-sdk` for adb, or `pymobile setup-sdk --with-emulator` "
            "for the emulator and x86_64 system image."
        ),
    )


def _device_environment(root: Path) -> dict[str, str]:
    """Set SDK/JDK variables for tools like avdmanager launched by subprocess."""
    environment = {
        **os.environ,
        "ANDROID_HOME": str(root),
        "ANDROID_SDK_ROOT": str(root),
    }
    if not environment.get("JAVA_HOME"):
        from .compiler.sdk_installer import _first_jdk_home, default_sdk_home

        for home in (root.parent, default_sdk_home()):
            jdk = _first_jdk_home(home)
            if jdk is not None:
                environment["JAVA_HOME"] = str(jdk)
                environment["PATH"] = f"{jdk / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
                break
    return environment


def _create_avd(root: Path, name: str) -> None:
    import subprocess

    avdmanager = cli._sdk_executable(root, "cmdline-tools/latest/bin", "avdmanager")
    command = [
        str(avdmanager),
        "create",
        "avd",
        "--name",
        name,
        "--package",
        cli._DEFAULT_EMULATOR_IMAGE,
        "--force",
    ]
    completed = subprocess.run(
        command,
        input="no\n",
        capture_output=True,
        text=True,
        env=cli._device_environment(root),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PyMobileError(
            f"could not create Android virtual device {name!r}",
            hint=(
                detail[-800:]
                or "Install the emulator image with `pymobile setup-sdk --with-emulator`."
            ),
        )
    cli._out.ok(f"created Android virtual device {name}")


def cmd_emulator(args: argparse.Namespace) -> int:
    """Create, list, or start the optional local Android emulator."""
    import subprocess

    root = cli._sdk_root_for_device(args)
    action = args.emulator_action
    name = getattr(args, "name", cli._DEFAULT_AVD_NAME)
    if action == "create":
        cli._create_avd(root, name)
        return 0

    emulator = cli._sdk_executable(root, "emulator", "emulator")
    if action == "list":
        completed = subprocess.run(
            [str(emulator), "-list-avds"],
            capture_output=True,
            text=True,
            env=cli._device_environment(root),
            check=False,
        )
        if completed.returncode != 0:
            raise PyMobileError(
                "could not list Android virtual devices", hint=completed.stderr.strip()
            )
        print(completed.stdout, end="")
        return 0

    listed = subprocess.run(
        [str(emulator), "-list-avds"],
        capture_output=True,
        text=True,
        env=cli._device_environment(root),
        check=False,
    )
    if listed.returncode != 0:
        raise PyMobileError("could not query Android virtual devices", hint=listed.stderr.strip())
    if name not in listed.stdout.splitlines():
        cli._out.info(f"creating the default virtual device {name}")
        cli._create_avd(root, name)
    command = [str(emulator), "-avd", name, "-no-snapshot"]
    if getattr(args, "no_window", False):
        command.append("-no-window")
    if getattr(args, "no_audio", False):
        command.append("-no-audio")
    cli._out.info(f"starting {name} — close the emulator window or press Ctrl+C to stop")
    emulator_process = subprocess.run(command, env=cli._device_environment(root), check=False)
    if emulator_process.returncode != 0:
        raise PyMobileError(
            f"emulator exited with code {emulator_process.returncode}",
            hint=(
                "Check host virtualization support; on Linux, enable KVM for hardware acceleration."
            ),
        )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install a built APK on a connected phone or local emulator with adb."""
    import subprocess

    config = cli._load(args)
    if getattr(args, "abi", None):
        config.abis = [args.abi]
        config.validate()
    if args.apk:
        apk = Path(args.apk).expanduser()
        if not apk.is_absolute():
            candidates = ((Path.cwd() / apk).resolve(), (config.root / apk).resolve())
            apk = next(
                (candidate for candidate in candidates if candidate.is_file()),
                candidates[0],
            )
    else:
        apk = config.output_path / config.apk_name
    if not apk.is_file():
        command = "pymobile build --native"
        if getattr(args, "abi", None):
            command += f" --abi {args.abi}"
        raise PyMobileError(
            f"APK not found: {apk}",
            hint=f"Build it first with `{command}` or pass an APK path to `pymobile install`.",
        )

    root = cli._sdk_root_for_device(args)
    adb = cli._sdk_executable(root, "platform-tools", "adb")
    environment = cli._device_environment(root)
    devices = subprocess.run(
        [str(adb), "devices"], capture_output=True, text=True, env=environment, check=False
    )
    if devices.returncode != 0:
        raise PyMobileError("adb could not list devices", hint=devices.stderr.strip())
    connected = [
        line.split()[0]
        for line in devices.stdout.splitlines()[1:]
        if len(line.split()) >= 2 and line.split()[1] == "device"
    ]
    device = getattr(args, "device", None)
    if device is None:
        if not connected:
            raise PyMobileError(
                "no Android phone or emulator is connected to adb",
                hint=(
                    "Start one with `pymobile emulator start` (after `pymobile setup-sdk "
                    "--with-emulator`) or connect a phone with USB debugging enabled."
                ),
            )
        if len(connected) > 1:
            raise PyMobileError(
                "more than one Android device is connected",
                hint="Pass `--device SERIAL`; available devices: " + ", ".join(connected),
            )
        device = connected[0]
    completed = subprocess.run(
        [str(adb), "-s", device, "install", "-r", str(apk)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PyMobileError(
            f"adb could not install {apk.name} on {device}",
            hint=(
                detail[-800:]
                or "Check that the APK ABI matches the device/emulator and storage is available."
            ),
        )
    result = (completed.stdout or "").strip()
    cli._out.ok(f"installed {apk.name} on {device}" + (f" — {result}" if result else ""))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Show only logcat lines from this app's PID (not another PyMobile app).

    For a crashed process pidof is empty: recover its PID from AndroidRuntime's
    `Process: <package>, PID: ...` record in the recent log buffer. If neither
    is available, refuse to show unrelated global `pymobile` tagged messages.
    """
    import re
    import subprocess

    config = cli._load(args)
    root = cli._sdk_root_for_device(args)
    adb = cli._sdk_executable(root, "platform-tools", "adb")
    env = cli._device_environment(root)
    devices = subprocess.run(
        [str(adb), "devices"], capture_output=True, text=True, env=env, check=False
    )
    if devices.returncode:
        raise PyMobileError("adb could not list devices", hint=devices.stderr.strip())
    connected = [
        parts[0]
        for line in devices.stdout.splitlines()[1:]
        if len(parts := line.split()) >= 2 and parts[1] == "device"
    ]
    device = args.device
    if device is None:
        if not connected:
            raise PyMobileError(
                "no Android device connected", hint="Connect a phone or start an emulator."
            )
        if len(connected) != 1:
            raise PyMobileError("more than one device connected", hint="Pass --device SERIAL")
        device = connected[0]
    elif device not in connected:
        raise PyMobileError(f"device {device!r} is not connected/authorized")

    prefix = [str(adb), "-s", device]
    pid_result = subprocess.run(
        [*prefix, "shell", "pidof", "-s", config.package],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    pid = pid_result.stdout.strip() if pid_result.stdout.strip().isdigit() else None
    if args.follow:
        if not pid:
            raise PyMobileError(
                f"{config.package} is not running",
                hint="Launch the app first; for a previous crash, "
                "use `pymobile logs` without --follow.",
            )
        cli._out.info(f"streaming {config.package} on {device} (PID {pid}); Ctrl+C to stop")
        result = subprocess.run(
            [*prefix, "logcat", f"--pid={pid}", "-v", "threadtime"], env=env, check=False
        )
        return int(result.returncode != 0)

    output = subprocess.run(
        [*prefix, "logcat", "-d", "-v", "threadtime", "-t", "3000"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if output.returncode:
        raise PyMobileError("adb logcat failed", hint=output.stderr.strip())
    lines = output.stdout.splitlines()
    # Crash headers give a reliable PID even after the process exits.
    if not pid:
        pattern = re.compile(r"\bProcess:\s*" + re.escape(config.package) + r",\s*PID:\s*(\d+)")
        for line in reversed(lines):
            match = pattern.search(line)
            if match:
                pid = match.group(1)
                break
    if not pid:
        raise PyMobileError(
            f"no recent logs found for {config.package}",
            hint="Run the app and retry immediately; logcat's buffer may have rotated.",
        )
    # threadtime's third field is the PID; comparing numeric fields prevents
    # accidentally including another app whose message happens to mention it.
    matches = [
        line for line in lines if len(parts := line.split(maxsplit=3)) >= 4 and parts[2] == pid
    ]
    if not matches:
        raise PyMobileError(f"no recent logcat lines for {config.package} (PID {pid})")
    for line in matches[-args.lines :]:
        print(line)
    return 0
