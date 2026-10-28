"""Command line interface.

Sub-commands: ``init``, ``build``, ``run``, ``dev``, ``watch``, ``preview``, ``check-ui``, ``info``,
``clean``, ``widget add``, ``widget-java``, ``setup-sdk``, ``emulator``,
``install``, ``doctor``.
Every command returns an exit code; :func:`main` is the console-script entry
point declared in ``pyproject.toml``.

Errors are printed as a short message plus an actionable hint — tracebacks only
appear with ``--verbose``, because a build tool that dumps a stack trace at a
user who typed a wrong package name is not helpful.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .compiler.pipeline import BuildPipeline
from .compiler.scaffold import create_project
from .core.config import ProjectConfig, load_config
from .errors import PyMobileError
from .log import configure, get_logger, supports_color

__all__ = ["main", "build_parser"]

_log = get_logger("cli")


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
class _Out:
    """Minimal styled console output."""

    def __init__(self) -> None:
        self.color = supports_color(sys.stdout)

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def ok(self, message: str) -> None:
        # stdout is block-buffered when piped while stderr is not, so progress
        # lines are flushed to keep them in order with warnings and errors.
        print(self._paint("✓", "32") + f" {message}", flush=True)

    def info(self, message: str) -> None:
        print(self._paint("•", "36") + f" {message}", flush=True)

    def warn(self, message: str) -> None:
        print(self._paint("!", "33") + f" {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(self._paint("✗", "31") + f" {message}", file=sys.stderr)

    def hint(self, message: str) -> None:
        print(f"  {self._paint('hint:', '2;37')} {message}", file=sys.stderr)

    def field(self, label: str, value: object) -> None:
        print(f"  {label:<14} {value}", flush=True)


_out = _Out()


def _invocation() -> str:
    """How the user launched us: ``pymobile`` or ``python -m pymobile``.

    pip's Scripts directory is frequently missing from PATH on Windows, so
    echoing back a bare ``pymobile ...`` would print a command that does not
    work for that user.
    """
    launched_as_module = Path(sys.argv[0]).name in ("__main__.py", "cli.py")
    if launched_as_module:
        return f"{Path(sys.executable).name} -m pymobile"
    return "pymobile"


def _export_command(name: str, value: object) -> str:
    """Render an environment-variable assignment for the host shell."""
    if platform.system() == "Windows":
        return f'$env:{name} = "{value}"'
    return f"export {name}={value}"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    """Create a new project."""
    directory = Path(args.directory).resolve()
    name = args.name or directory.name.replace("-", " ").replace("_", " ").title()
    result = create_project(directory, name, package=args.package, force=args.force)
    _out.ok(f"created project {name!r} in {result.directory}")
    for path in result.files:
        _out.field("", path.relative_to(result.directory))
    print()
    _out.info(f"cd {result.directory.name} && {_invocation()} build --native")
    return 0


def _warn_if_output_is_ignored(config: ProjectConfig) -> None:
    """Say so when the chosen output directory will never leave the machine.

    ``output_dir`` defaults to ``apk/`` for exactly this reason, but a project
    that sets ``build``/``dist``/``out`` is usually inside a ``.gitignore`` and
    a CI artefact rule — the APK is built, the file is fine, and nobody can
    find it. Silent builds that "disappear" are the worst version of this.
    """
    ignore_file = config.root / ".gitignore"
    if not ignore_file.is_file():
        return
    try:
        lines = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:  # pragma: no cover - unreadable file is not our problem
        return
    name = Path(config.output_dir).name
    patterns = {
        line.strip().strip("/").lstrip("!")
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }
    if name not in patterns and f"{name}/" not in patterns:
        return
    _out.warn(
        f"output_dir {config.output_dir!r} matches a rule in {ignore_file.name}: "
        "the APK will not be committed or kept as a CI artefact"
    )
    _out.hint("use `--output apk` (the default) or remove the ignore rule")


def cmd_build(args: argparse.Namespace) -> int:
    """Compile the project into an APK."""
    config = _load(args)
    if args.icon:
        config.icon = args.icon
    if args.output:
        config.output_dir = args.output
    if getattr(args, "optimize", False):
        config.optimize = True
    if getattr(args, "no_optimize", False):
        config.optimize = False
    if getattr(args, "minimal_stdlib", False):
        config.minimal_stdlib = True
    if getattr(args, "no_ssl", False):
        config.no_ssl = True
    if getattr(args, "abi", None):
        # One APK per ABI: arm64-v8a for phones, x86_64 for the emulator.
        config.abis = [args.abi]
    config.validate()

    if args.clean:
        _clean(config)

    if getattr(args, "ks_pass", None) or getattr(args, "key_pass", None):
        _out.warn(
            "passwords on the command line end up in shell history and `ps`; "
            "prefer PYMOBILE_KS_PASS / PYMOBILE_KEY_PASS"
        )

    _warn_if_output_is_ignored(config)
    native = getattr(args, "native", False)
    if native:
        _out.info("native build: this may take a few minutes on the first run")
    else:
        _out.warn(
            "This is a structural build — not installable on a device. "
            f"Use --native for a real APK (requires Android SDK: {_invocation()} setup-sdk)"
        )
    pipeline = BuildPipeline(
        config,
        use_cache=not args.no_cache and not args.clean,
        native=native,
        check_startup=getattr(args, "check_startup", None),
        on_stage=lambda stage: _out.info(f"{stage}…") if args.verbose else None,
        keystore=Path(args.keystore) if getattr(args, "keystore", None) else None,
        keystore_password=getattr(args, "ks_pass", None),
        key_alias=getattr(args, "key_alias", None),
        key_password=getattr(args, "key_pass", None),
    )
    try:
        result = pipeline.run()
    except Exception as exc:
        from .compiler.startup import StartupCheckError
        from .compiler.toolchain import ToolchainError
        from .compiler.widgets import MissingRendererError

        if isinstance(exc, ToolchainError):
            hint = getattr(exc, "hint", None) or ""
            if "setup-sdk" not in hint:
                hint = (f"{hint} " if hint else "") + (
                    f"Run `{_invocation()} setup-sdk` to install it automatically."
                )
            raise type(exc)(str(exc), hint=hint.strip()) from exc
        if native and not isinstance(exc, (MissingRendererError, StartupCheckError)):
            from .errors import PyMobileError as _PyErr

            if isinstance(exc, _PyErr) and exc.hint and "setup-sdk" not in exc.hint:
                exc.hint = f"{exc.hint} (for native: {_invocation()} setup-sdk)"
            elif isinstance(exc, _PyErr) and not exc.hint:
                exc.hint = f"Run `{_invocation()} setup-sdk` to install the Android SDK."
        raise

    for warning in result.warnings:
        _out.warn(warning)

    if result.cached:
        size = (
            f"{result.size / (1024 * 1024):.1f} MB"
            if result.size >= 1024 * 1024
            else f"{result.size_kb:.1f} KB"
        )
        _out.ok(f"up to date: {result.apk.name} ({size})")
        _out.hint("use --clean to force a full rebuild")
        return 0

    _out.ok(result.summary())
    _out.field("apk", result.apk)
    _out.field("icon", "default" if result.icon_is_default else config.icon)
    if result.native:
        _out.field("install", f"{_invocation()} install {result.apk}")
        if config.abis and config.abis[0] == "x86_64":
            _out.hint("x86_64 build: for the Android Studio emulator, not for a phone")
    if args.verbose:
        for timing in result.timings:
            _out.field(timing.name, f"{timing.seconds * 1000:.0f} ms")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview.cmd_run(args)


def _run_gui(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview._run_gui(config, entry, args)


def _run_web(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview._run_web(config, entry, args)


def cmd_dev(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview.cmd_dev(args)


def _open_gui_window(bridge: Any, config: ProjectConfig) -> Any:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview._open_gui_window(bridge, config)


def _close_gui_window(preview: Any) -> None:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview._close_gui_window(preview)


def cmd_watch(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview.cmd_watch(args)


def _reload(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> None:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview._reload(config, entry, args)


def _purge_project_modules(source: Path) -> None:
    """Forget modules imported from the project so the next run re-reads them.

    Dropping the ``sys.modules`` entry is not enough on its own: CPython
    validates a cached ``.pyc`` against the source's mtime *and size*, and on
    filesystems with a coarse clock (tmpfs, overlayfs, network shares) a quick
    edit that happens to keep the length can match both. The import then
    silently returns yesterday's bytecode, and the reload appears to do
    nothing. Removing the project's caches keeps that from happening.
    """
    import importlib
    import shutil

    source = source.resolve()
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        try:
            path = Path(origin).resolve()
        except (OSError, ValueError):
            continue
        if path.is_relative_to(source) and not path.name.startswith("__main__"):
            del sys.modules[name]

    for cache in source.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    importlib.invalidate_caches()


def _not_a_constant(name: str) -> object:
    raise ValueError(name)  # NaN and Infinity are words on a command line, not numbers


def _set_value(text: str) -> object:
    """The value of ``--set name=VALUE``: JSON when it parses (``7``, ``true``), else the text."""
    try:
        return json.loads(text, parse_constant=_not_a_constant)
    except ValueError:
        return text


def _screen_arguments(
    text: str | None, pairs: Sequence[str] | None = None
) -> tuple[list[object], dict[str, object]]:
    """``--args`` and ``--set``: the constructor arguments of the ``--screen`` to open.

    ``--args`` is JSON — an object is keywords, a list positional, a scalar one value.
    ``--set NAME=VALUE`` adds one keyword and needs no quoting, which is why it is the
    form for ``cmd.exe`` and Windows PowerShell, where the shell eats the quotes inside
    the JSON. A ``--set`` overrides the same name coming from ``--args``.
    """
    positional: list[object] = []
    keywords: dict[str, object] = {}
    if text is not None:
        try:
            value = json.loads(text)
        except ValueError as error:
            raise PyMobileError(
                f"--args is not valid JSON: {error}",
                hint=(
                    "A shell may have eaten the quotes (cmd.exe and Windows PowerShell do). "
                    "Pass the values one by one instead — --set score=7 --set player=Anna — "
                    "which needs no quoting; or escape the JSON for your shell."
                ),
            ) from error
        if isinstance(value, dict):
            keywords.update({str(key): item for key, item in value.items()})
        elif isinstance(value, list):
            positional.extend(value)
        else:
            positional.append(value)
    for pair in pairs or ():
        name, separator, raw = pair.partition("=")
        name = name.strip()
        if not separator or not name.isidentifier():
            raise PyMobileError(
                f"--set expects NAME=VALUE, got {pair!r}",
                hint="For example: --set score=7 --set player=Anna.",
            )
        keywords[name] = _set_value(raw)
    return positional, keywords


def _navigate(args: argparse.Namespace, config: ProjectConfig | None = None) -> None:
    """Take the running app to the screen ``--screen`` / ``--navigate`` ask for.

    The first screen is what ``App.run()`` shows; ``--screen ResultScreen`` pushes
    another one on top of it and ``--navigate "Menu.start, Quiz.next"`` presses its
    way there like a user, so a screen deep in the app can be previewed without
    editing ``main.py``. ``--tab Stats`` (or a ``@Stats`` step) picks a bottom-
    navigation tab, and both route forms may be combined with ``--screen``.

    A screen name is looked up among the classes the entry point imported.
    When it is not there, every project module that defines a ``…Screen`` is
    imported and the lookup is retried — a screen living in a module the entry
    point never touched (a lazy import is the usual reason) is still found.
    """
    screen = getattr(args, "screen", None)
    route = getattr(args, "navigate", None)
    tab = getattr(args, "tab", None)
    given = [
        flag
        for flag, present in (
            ("--args", getattr(args, "screen_args", None) is not None),
            ("--set", bool(getattr(args, "screen_set", None))),
        )
        if present
    ]
    if given and not screen:
        verb = "makes" if len(given) == 1 else "make"
        raise PyMobileError(
            f"{' and '.join(given)} only {verb} sense with --screen", hint="Add --screen NAME."
        )
    if not screen and not route and not tab:
        return

    from .core.app import App
    from .core.driver import Driver, discover_screens

    app = App.current()
    if app is None:
        raise PyMobileError(
            "No running application was found in the entry point.",
            hint="Make sure it calls App(...).run(SomeScreen()) before returning.",
        )
    driver = Driver(app)
    if screen:
        positional, keywords = _screen_arguments(
            getattr(args, "screen_args", None), getattr(args, "screen_set", None)
        )
        try:
            opened = driver.open(screen, *positional, **keywords)
        except PyMobileError as error:
            # "no screen called 'ResultScreen'": the class exists on disk but
            # its module was never imported (a lazy import, usually). Import the
            # project's screen modules once and retry before giving up.
            if config is None or "no screen called" not in str(error):
                raise
            found = discover_screens([config.source_path], exclude=[config.entrypoint_path])
            if not found:
                raise
            _out.info(
                "imported project modules to find screens: "
                + ", ".join(sorted(cls.__name__ for cls in found)[:6])
            )
            opened = driver.open(screen, *positional, **keywords)
        _out.info(f"opened {type(opened).__name__}")
    steps = route
    if tab:
        # ``--tab Stats`` is the short spelling of the ``@Stats`` route step.
        tab_step = f"@{tab}"
        steps = tab_step if not steps else f"{tab_step}, {steps}"
    if steps:
        for step, landed in zip(route_steps(steps), driver.navigate(steps), strict=True):
            _out.info(f"{step} → {type(landed).__name__}")


def route_steps(route: str) -> list[str]:
    """The steps of a ``--navigate`` route, for progress lines."""
    return [part.strip() for part in route.replace(">", ",").split(",") if part.strip()]


def _entrypoint(config: ProjectConfig) -> Path:
    """Resolve and validate the configured entry point."""
    entry = config.entrypoint_path
    if not entry.exists():
        raise PyMobileError(
            f"Entry point not found: {entry}",
            hint="Check `entrypoint` in your configuration.",
        )
    return entry


def _execute(config: ProjectConfig, entry: Path) -> dict[str, object]:
    """Execute the entry point and return its namespace.

    The running :class:`~pymobile.core.app.App` is looked up afterwards and
    exposed as ``__pymobile_app__`` so callers do not have to guess the name
    the developer gave their application object.
    """
    if str(config.source_path) not in sys.path:
        sys.path.insert(0, str(config.source_path))
    namespace: dict[str, object] = {"__name__": "__main__", "__file__": str(entry)}
    exec(compile(entry.read_text(encoding="utf-8"), str(entry), "exec"), namespace)

    from .core.app import App

    # The app is usually created inside a main() function, so it is found
    # through the registry rather than by scanning the module namespace.
    namespace["__pymobile_app__"] = App.current()
    return namespace


def _positive_int(value: str) -> int:
    """An argparse integer that must be greater than zero."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _parse_size(value: str | None) -> tuple[int, int | None]:
    """``"360x640"`` → (360, 640); ``"400"`` → (400, None); None → defaults."""
    if not value:
        return 360, None
    text = value.lower().replace("\u00d7", "x").strip()  # a typed multiplication sign works too
    try:
        if "x" in text:
            w, h = text.split("x", 1)
            return int(w), int(h)
        return int(text), None
    except ValueError:
        raise PyMobileError(
            f"Invalid --size {value!r}.", hint="Use WIDTHxHEIGHT in dp, e.g. 360x640."
        ) from None


def cmd_preview(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview.cmd_preview(args)


def cmd_check_ui(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_preview."""
    from . import cli_preview

    return cli_preview.cmd_check_ui(args)


def cmd_info(args: argparse.Namespace) -> int:
    """Print the resolved configuration."""
    config = _load(args)
    if args.json:
        print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        return 0

    _out.info(f"{config.name} {config.version} ({config.package})")
    _out.field("entrypoint", config.entrypoint_path)
    _out.field("source", config.source_path)
    _out.field("output", config.output_path)
    _out.field("apk", config.apk_name)
    min_sdk = str(config.effective_min_sdk)
    if config.min_sdk != config.effective_min_sdk:
        min_sdk += f" (pymobile.toml says {config.min_sdk})"
    _out.field("sdk", f"min {min_sdk} / target {config.target_sdk}")
    _out.field("abis", ", ".join(config.abis))
    _out.field("icon", config.icon or "default")
    _out.field("optimize", config.optimize)
    print("  permissions")
    for permission in sorted(config.permissions):
        _out.field("", permission)
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove build artifacts."""
    config = _load(args)
    removed = _clean(config)
    _out.ok(f"removed {removed}" if removed else "nothing to clean")
    from .compiler.backends.native import debug_keystore_path

    # The debug key lives outside build/, so cleaning never changes the
    # signature (a different key makes the next APK un-installable over this one).
    _out.info(f"debug signing key kept at {debug_keystore_path(config.package)}")
    return 0


def cmd_widget_java(args: argparse.Namespace) -> int:
    """Print (or save) the Android renderer branch for a custom widget type."""
    from .compiler.widgets import java_branch, parse_props
    from .errors import ResourceError
    from .resources import resource_path

    branch = java_branch(args.type_name, parse_props(args.prop))
    try:
        viewbuilder: Path | None = resource_path("android", "java", "ViewBuilder.java")
    except ResourceError:  # pragma: no cover - broken installation
        viewbuilder = None
    guide = branch.render(viewbuilder)
    if args.out:
        target = Path(args.out)
        target.write_text(guide, encoding="utf-8")
        _out.ok(f"wrote the {args.type_name} guide to {target}")
    else:
        print(guide)
    return 0


def cmd_widget_add(args: argparse.Namespace) -> int:
    """Scaffold a Python widget with an automatically compiled Java renderer."""
    import re

    from .compiler.widgets import parse_props, scaffold_widget_sources

    config = _load(args)
    python_source, java_source = scaffold_widget_sources(
        args.type_name,
        parse_props(args.prop),
    )
    module_name = re.sub(r"(?<!^)(?=[A-Z])", "_", args.type_name).lower()
    python_dir = config.source_path / "widgets"
    python_file = python_dir / f"{module_name}.py"
    package_init = python_dir / "__init__.py"
    java_file = (
        config.root
        / "java"
        / "org"
        / "pymobile"
        / "app"
        / "widgets"
        / f"{args.type_name}Renderer.java"
    )
    collisions = [path for path in (python_file, java_file) if path.exists()]
    if collisions:
        raise PyMobileError(
            "widget files already exist: " + ", ".join(str(path) for path in collisions),
            hint="Choose another widget type name or move the existing files first.",
        )

    python_dir.mkdir(parents=True, exist_ok=True)
    if not package_init.exists():
        package_init.write_text('"""Project-local PyMobile widgets."""\n', encoding="utf-8")
    python_file.write_text(python_source, encoding="utf-8")
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text(java_source, encoding="utf-8")
    _out.ok(f"created native widget {args.type_name}")
    _out.field("python", python_file.relative_to(config.root))
    _out.field("renderer", java_file.relative_to(config.root))
    _out.info(f"import it with: from widgets.{module_name} import {args.type_name}")
    _out.info("then run: pymobile build --native (the java/ overlay is compiled automatically)")
    return 0


def cmd_setup_sdk(args: argparse.Namespace) -> int:
    """Download and install the Android toolchain."""
    from .compiler.sdk_installer import default_sdk_home, install_sdk

    target_path = Path(args.path).expanduser() if args.path else default_sdk_home()
    with_ndk = getattr(args, "with_ndk", False)
    with_emulator = getattr(args, "with_emulator", False)
    size = (
        "~4 GB"
        if with_ndk and with_emulator
        else ("~3.5 GB" if with_emulator else "~2.7 GB" if with_ndk else "~900 MB")
    )

    # Check whether the SDK directory already exists and has cached content
    already_installed = target_path.exists() and any(target_path.iterdir())

    if already_installed:
        _out.info(f"using cached Android toolchain ({target_path})")
    else:
        _out.info(f"installing the Android toolchain ({size}, one time)...")

    if not with_ndk:
        _out.info("using the prebuilt native bridge; pass --with-ndk to build it from source")
    if with_emulator:
        _out.info("installing the x86_64 emulator and API 35 system image (no NDK required)")

    sdk = install_sdk(
        target_path if args.path else None,
        with_ndk=with_ndk,
        with_emulator=with_emulator,
    )

    if already_installed:
        _out.ok(f"toolchain verified: {sdk}")
    else:
        _out.ok(f"toolchain ready: {sdk}")

    _out.info(f"now run: {_invocation()} build --native")
    if not os.environ.get("ANDROID_HOME"):
        _out.hint(f"optional: {_export_command('ANDROID_HOME', sdk)}")

    return 0


_DEFAULT_AVD_NAME = "pymobile-api35-x86_64"
_DEFAULT_EMULATOR_IMAGE = "system-images;android-35;google_apis;x86_64"


def _sdk_root_for_device(args: argparse.Namespace) -> Path:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device._sdk_root_for_device(args)


def _sdk_executable(root: Path, relative: str, name: str) -> Path:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device._sdk_executable(root, relative, name)


def _device_environment(root: Path) -> dict[str, str]:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device._device_environment(root)


def _create_avd(root: Path, name: str) -> None:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device._create_avd(root, name)


def cmd_emulator(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device.cmd_emulator(args)


def cmd_install(args: argparse.Namespace) -> int:
    """Compatibility entry point; implementation is in cli_device."""
    from . import cli_device

    return cli_device.cmd_install(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the environment and configuration."""
    problems = 0
    _out.info(f"pymobile {__version__} on Python {sys.version.split()[0]}")

    try:
        import PIL  # noqa: F401

        _out.ok("Pillow available — icons will be resized for every density")
    except ImportError:
        _out.warn("Pillow missing — icons are copied without resizing")
        _out.hint("pip install Pillow")

    try:
        config = _load(args)
    except PyMobileError as exc:
        _out.warn(f"no usable project here: {exc}")
        return 0

    _out.ok(f"configuration valid: {config.name} ({config.package})")
    if not config.entrypoint_path.exists():
        _out.error(f"entry point missing: {config.entrypoint_path}")
        problems += 1
    else:
        _out.ok(f"entry point found: {config.entrypoint}")

    icon = config.icon_path
    if icon is not None and not icon.exists():
        _out.error(f"icon missing: {icon}")
        problems += 1
    elif icon is not None:
        _out.ok(f"custom icon: {icon.name}")
    else:
        _out.ok("using the default icon")

    from .compiler.toolchain import ToolchainError, find_toolchain

    sdk_ok = False
    try:
        toolchain = find_toolchain()
        toolchain.verify()
        _out.ok(f"Android toolchain ready (build-tools {toolchain.build_tools.name})")
        if toolchain.has_ndk:
            _out.ok("NDK present — the native bridge can be rebuilt from source")
        else:
            _out.ok("using the prebuilt native bridge (no NDK needed)")
        _out.info(f"native APK builds available: {_invocation()} build --native")
        sdk_ok = True
    except (ToolchainError, PyMobileError) as exc:
        _out.warn(f"Android SDK not usable — only structural builds are available: {exc}")
        _out.hint("run `pymobile setup-sdk` to install it automatically")

    if problems:
        _out.error(f"{problems} problem(s) found")
        return 1
    if sdk_ok:
        _out.ok("everything looks good")
    else:
        _out.ok("project configuration looks good")
        _out.warn("Android SDK is not installed — native APK builds are unavailable")
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load(args: argparse.Namespace) -> ProjectConfig:
    """Load the project configuration honouring ``--config``."""
    return load_config(getattr(args, "config", None) or Path.cwd())


def _clean(config: ProjectConfig) -> str:
    """Delete the output directory; returns what was removed."""
    output = config.output_path
    if output.exists():
        shutil.rmtree(output)
        return str(output)
    return ""


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the CLI grammar with the current command handlers.

    Resolve handlers here rather than freezing imports in cli_parser: embedders
    and tests may replace commands without changing parser implementation.
    """
    from .cli_parser import build_parser as make_parser

    return make_parser(
        {
            "build": cmd_build,
            "check_ui": cmd_check_ui,
            "clean": cmd_clean,
            "dev": cmd_dev,
            "doctor": cmd_doctor,
            "emulator": cmd_emulator,
            "info": cmd_info,
            "init": cmd_init,
            "install": cmd_install,
            "preview": cmd_preview,
            "run": cmd_run,
            "setup_sdk": cmd_setup_sdk,
            "watch": cmd_watch,
            "widget_add": cmd_widget_add,
            "widget_java": cmd_widget_java,
        },
        positive_int=_positive_int,
        default_avd=_DEFAULT_AVD_NAME,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # restore the defaults suppressed above
    args.verbose = getattr(args, "verbose", False)
    args.config = getattr(args, "config", None)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    configure("debug" if args.verbose else "warning")

    try:
        return int(args.func(args))
    except PyMobileError as exc:
        _out.error(str(exc))
        if exc.hint:
            _out.hint(exc.hint)
        if args.verbose:
            raise
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _out.warn("interrupted")
        return 130
    except Exception as exc:
        _out.error(f"unexpected error: {exc}")
        if args.verbose:
            raise
        _out.hint("re-run with --verbose to see the full traceback")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
