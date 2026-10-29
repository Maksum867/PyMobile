"""Desktop startup smoke check (default for native builds), in a disposable process.

This is process/state isolation, NOT a security sandbox. The entrypoint is
trusted code and can still use the network or write explicitly named files.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

from ..core.config import ProjectConfig
from ..errors import PyMobileError


class StartupCheckError(PyMobileError):
    """The desktop smoke check failed before APK/toolchain work began."""


def _terminate_probe(process: subprocess.Popen[bytes], *, windows: bool) -> None:
    """Terminate and reap startup, including Windows venv redirector children.

    Killing only python.exe can leave the real venv interpreter holding the
    inherited output handle on Windows. taskkill /T runs while the parent PID
    still exists, before a fallback kill/reap, so its descendants are included.
    """
    if windows:
        taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/taskkill.exe"
        # Keep the direct child killable even if taskkill is unavailable.
        # Reaping is mandatory before the output file/workspace is closed.
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
    elif sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait()


def _run_probe(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    output: BinaryIO,
    timeout: float,
) -> int:
    """Do not let TemporaryDirectory cleanup race a still-running child."""
    windows = os.name == "nt"
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=not windows,
    ) as process:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            _terminate_probe(process, windows=windows)
            raise


def check_startup(
    config: ProjectConfig, *, timeout: float = 10.0, dependencies_dir: Path | None = None
) -> None:
    """Require a rendered startup screen, without polluting the build process.

    Default framework storage is redirected to a temporary directory. Relative
    assets still resolve against the project root, just like the normal CLI.
    This does not emulate Android APIs and cannot prove device compatibility.
    """
    with tempfile.TemporaryDirectory(prefix="pymobile-startup-") as temporary:
        work = Path(temporary)
        result_path = work / "result.json"
        environment = os.environ.copy()
        environment["PYMOBILE_STORAGE_DIR"] = str(work / "storage")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        # Use the framework running this build even when it is an editable
        # checkout rather than an installed package; import it before app code.
        package_root = Path(__file__).resolve().parents[2]
        launcher = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from pymobile.compiler.startup import _worker; _worker()"
        )
        with (work / "output.log").open("w+b") as output:
            try:
                returncode = _run_probe(
                    [
                        sys.executable,
                        "-c",
                        launcher,
                        str(package_root),
                        str(config.entrypoint_path.resolve()),
                        str(config.source_path.resolve()),
                        str(result_path),
                        str(dependencies_dir or ""),
                    ],
                    cwd=config.root,
                    environment=environment,
                    output=output,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as error:
                raise StartupCheckError(
                    f"Startup check timed out after {timeout:g}s.",
                    hint="The entrypoint must return after App(...).run(SomeScreen()). "
                    "Move blocking work out of startup; this is a desktop stub, not Android.",
                ) from error
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if returncode == 0 and result.get("ok") is True:
                    return
                message = str(result.get("error") or "startup process failed")
            else:
                message = f"startup process exited without a result (exit {returncode})"
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 2000))
            diagnostic = output.read().decode("utf-8", errors="replace").strip()
        raise StartupCheckError(
            f"Startup check failed: {message}",
            hint="Make sure App(...).run(SomeScreen()) renders a screen on the desktop stub. "
            "Android-only startup code may need a desktop guard, or use "
            "--skip-startup-check after device testing."
            + (f"\nLast app output:\n{diagnostic}" if diagnostic else ""),
        )


def _worker() -> None:
    """Child-only entry point. The report channel is independent of app stdout."""
    import runpy

    from ..core.app import App
    from ..core.bridge import StubBridge, set_bridge

    entry, source, destination, dependencies_dir = sys.argv[1:]
    result: dict[str, Any] = {"ok": False}
    bridge = StubBridge(verbose=False)
    set_bridge(bridge)
    if dependencies_dir:
        sys.path.insert(0, dependencies_dir)
    sys.path.insert(0, source)
    sys.argv = [entry]
    try:
        try:
            runpy.run_path(entry, run_name="__main__")
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        app = App.current()
        if app is None or app.navigator.current is None or bridge.last_tree is None:
            result["error"] = "The app rendered nothing."
        else:
            result["ok"] = True
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        app = App.current()
        if app is not None:
            try:
                app.stop()
            except Exception as error:
                result = {"ok": False, "error": f"cleanup failed: {error}"}
        Path(destination).write_text(json.dumps(result), encoding="utf-8")
