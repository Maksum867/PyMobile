"""Command line interface.

Sub-commands: ``init``, ``build``, ``run``, ``watch``, ``preview``, ``check-ui``, ``info``,
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
import contextlib
import json
import os
import platform
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .compiler.pipeline import BuildPipeline
from .compiler.scaffold import create_project
from .core.config import CONFIG_FILENAME, ProjectConfig, load_config
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
        on_stage=lambda stage: _out.info(f"{stage}…") if args.verbose else None,
        keystore=Path(args.keystore) if getattr(args, "keystore", None) else None,
        keystore_password=getattr(args, "ks_pass", None),
        key_alias=getattr(args, "key_alias", None),
        key_password=getattr(args, "key_pass", None),
    )
    try:
        result = pipeline.run()
    except Exception as exc:
        from .compiler.toolchain import ToolchainError
        from .compiler.widgets import MissingRendererError

        if isinstance(exc, ToolchainError):
            hint = getattr(exc, "hint", None) or ""
            if "setup-sdk" not in hint:
                hint = (f"{hint} " if hint else "") + (
                    f"Run `{_invocation()} setup-sdk` to install it automatically."
                )
            raise type(exc)(str(exc), hint=hint.strip()) from exc
        if native and not isinstance(exc, MissingRendererError):
            from .errors import PyMobileError as _PyErr

            if isinstance(exc, _PyErr) and exc.hint and "setup-sdk" not in exc.hint:
                exc.hint = f"{exc.hint} (for native: {_invocation()} setup-sdk)"
            elif isinstance(exc, _PyErr) and not exc.hint:
                exc.hint = (
                    f"Run `{_invocation()} setup-sdk` to install the Android SDK."
                )
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
    """Import the entry point and render the first screen on the desktop."""
    config = _load(args)
    entry = _entrypoint(config)

    if getattr(args, "web", False):
        return _run_web(config, entry, args)
    if getattr(args, "gui", False):
        return _run_gui(config, entry, args)

    _out.info(f"running {entry.name} in desktop preview mode")
    _execute(config, entry)
    _navigate(args)

    from .core.bridge import get_bridge
    from .core.ui.preview import render_ascii

    # last_tree lives on the stub bridges; the abstract Bridge has no such
    # attribute, so read it defensively.
    tree = getattr(get_bridge(), "last_tree", None)
    if tree is None:
        raise PyMobileError(
            "The app rendered nothing.",
            hint="Make sure the entry point calls App(...).run(SomeScreen()).",
        )
    from .core.app import App

    app = App.current()
    print(render_ascii(tree, title=app.name if app is not None else config.name))
    _out.hint("use --gui for a clickable window")
    return 0


def _run_gui(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> int:
    """Run the app in an interactive Tkinter window."""
    from .core.bridge import GuiBridge, set_bridge
    from .core.ui.gui import GuiPreview, tkinter_available

    if not tkinter_available():
        raise PyMobileError(
            "Tkinter is not available in this Python installation",
            hint="Install it (Debian/Ubuntu: `sudo apt install python3-tk`), "
            "or use `pymobile preview` for a text picture.",
        )

    from .core.app import App

    bridge = GuiBridge(verbose=False)
    set_bridge(bridge)
    _execute(config, entry)
    _navigate(args)
    app = App.current()
    if app is None:
        raise PyMobileError(
            "No running application was found in the entry point.",
            hint="Make sure it calls App(...).run(SomeScreen()) before returning.",
        )

    _out.ok(f"{app.name} is running — close the window to stop")
    try:
        preview = GuiPreview(app, title=app.name)
        bridge.attach(preview)
        preview.run()
    except Exception as error:  # pragma: no cover - display problems
        raise PyMobileError(
            f"Could not open the preview window: {error}",
            hint="On a headless machine use `pymobile preview` instead.",
        ) from error
    return 0


def _run_web(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> int:
    """Serve the app to a browser."""
    from .core.app import App
    from .core.bridge import WebBridge, set_bridge
    from .core.ui.web import WebPreview, browser_url

    bridge = WebBridge(verbose=False)
    set_bridge(bridge)
    _execute(config, entry)
    _navigate(args)
    app = App.current()
    if app is None:
        raise PyMobileError(
            "No running application was found in the entry point.",
            hint="Make sure it calls App(...).run(SomeScreen()) before returning.",
        )

    preview = WebPreview(app, host=args.host, port=args.port)
    bridge.attach(preview)
    _out.ok(f"{app.name} is running at {browser_url(args.host, preview.port, preview.token)}")
    if preview.exposed:
        # Network exposure is opt-in, and the warning says what it means: the
        # port can read the app's state and press its widgets, so the session
        # token (already part of the printed URL) is mandatory.
        _out.warn(
            f"listening on {args.host}: the preview is reachable from the network. "
            "It requires the session token in the URL above; treat that URL as a "
            "secret and use an SSH tunnel (--host 127.0.0.1) when you only need "
            "it locally."
        )
    _out.info("press Ctrl+C to stop")
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C is how you stop it
        preview.serve_forever()
    _out.ok("stopped")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Re-render the app whenever a source file changes."""
    from .core.watcher import FileWatcher

    config = _load(args)
    entry = _entrypoint(config)
    roots = [config.source_path]

    _out.info(f"watching {config.source_path} — press Ctrl+C to stop")
    _reload(config, entry, args)

    watcher = FileWatcher(roots, interval=args.interval)
    try:
        while True:
            changed = watcher.wait()
            names = ", ".join(path.name for path in changed[:3])
            if len(changed) > 3:
                names += f" (+{len(changed) - 3})"
            print()
            _out.info(f"changed: {names}")
            _reload(config, entry, args)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print()
        _out.ok("stopped watching")
        return 0


def _reload(config: ProjectConfig, entry: Path, args: argparse.Namespace) -> None:
    """Run the entry point once and show the result.

    Every reload starts from a clean module table for the project's own
    modules, so editing an imported helper takes effect too — a plain re-exec
    of main.py would keep the stale version cached in sys.modules.
    """
    from .core.bridge import StubBridge, set_bridge
    from .core.ui.preview import render_ascii, render_mockup, render_png

    started = time.perf_counter()
    bridge = StubBridge(verbose=False)
    set_bridge(bridge)
    _purge_project_modules(config.source_path)

    try:
        _execute(config, entry)
        _navigate(args)
    except Exception as error:
        _out.error(f"{type(error).__name__}: {error}")
        if isinstance(error, PyMobileError) and error.hint:
            _out.hint(error.hint)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return

    tree = bridge.last_tree
    if tree is None:
        _out.warn("the app rendered nothing")
        return

    elapsed = (time.perf_counter() - started) * 1000
    from .core.app import App

    app = App.current()
    display_title = app.name if app is not None else config.name
    if args.png and getattr(args, "text", False):
        render_png(tree, args.png)
        _out.ok(f"wrote {args.png} in {elapsed:.0f} ms")
    elif args.png:
        render_mockup(
            tree,
            args.png,
            theme=app.theme if app is not None else None,
            title=display_title,
            assets=config.source_path,
        )
        elapsed = (time.perf_counter() - started) * 1000
        _out.ok(f"wrote {args.png} in {elapsed:.0f} ms")
    else:
        print(render_ascii(tree, show_ids=args.ids, title=display_title))
        _out.ok(f"rendered in {elapsed:.0f} ms")


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


def _navigate(args: argparse.Namespace) -> None:
    """Take the running app to the screen ``--screen`` / ``--navigate`` ask for.

    The first screen is what ``App.run()`` shows; ``--screen ResultScreen`` pushes
    another one on top of it and ``--navigate "Menu.start, Quiz.next"`` presses its
    way there like a user, so a screen deep in the app can be previewed without
    editing ``main.py``. Both may be combined (the screen first, then the route).
    """
    screen = getattr(args, "screen", None)
    route = getattr(args, "navigate", None)
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
    if not screen and not route:
        return

    from .core.app import App
    from .core.driver import Driver

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
        opened = driver.open(screen, *positional, **keywords)
        _out.info(f"opened {type(opened).__name__}")
    if route:
        for step, landed in zip(route_steps(route), driver.navigate(route), strict=True):
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
    """Render a screen of the app — the first, or the one --screen/--navigate reach."""
    from .core.app import App
    from .core.bridge import StubBridge, set_bridge
    from .core.ui.preview import render_ascii, render_mockup, render_png

    config = _load(args)
    entry = _entrypoint(config)

    bridge = StubBridge(verbose=False)
    set_bridge(bridge)
    _execute(config, entry)

    # --theme must reach the *app* before the tree is read, not just the
    # renderer: build() resolves colours through app.theme (the documented
    # `self.app.theme["SURFACE"]`), so rendering a tree built in the light
    # palette with the dark one paints dark text over light cards. set_theme()
    # rebuilds the screens, hence last_tree is read afterwards — and after
    # --navigate, whose screens must be built in that theme too.
    requested_theme = getattr(args, "theme", None)
    app = App.current()
    if requested_theme and app is not None:
        app.set_theme(requested_theme)
    display_title = app.name if app is not None else config.name

    _navigate(args)

    tree = bridge.last_tree
    if tree is None:
        raise PyMobileError(
            "The app rendered nothing.",
            hint="Make sure the entry point calls App(...).run(SomeScreen()).",
        )

    if args.png and getattr(args, "text", False):
        path = render_png(tree, args.png)
        _out.ok(f"wrote the text preview to {path}")
    elif args.png:
        width, height = _parse_size(getattr(args, "size", None))
        # П-05: surface size validation as an expected error instead of the
        # generic "unexpected error: width must be at least 120 dp".
        if width < 120:
            raise PyMobileError(
                "width must be at least 120 dp",
                hint="Use --size 360x640 (or larger) for a phone-shaped mockup.",
            )
        # The app carries the requested theme by now; the flag is the fallback
        # for an entry point that never created one.
        theme = app.theme if app is not None else requested_theme
        try:
            path = render_mockup(
                tree,
                args.png,
                width=width,
                height=height,
                theme=theme,
                title=display_title,
                assets=config.source_path,
            )
        except ValueError as exc:
            raise PyMobileError(str(exc)) from exc
        _out.ok(f"wrote a mockup of the screen to {path}")
        _out.hint("an approximation: fonts and system colours differ between phones")
    else:
        print(render_ascii(tree, show_ids=args.ids, title=display_title))
    return 0


def cmd_check_ui(args: argparse.Namespace) -> int:
    """Run the static accessibility and narrow-screen audit on the app tree."""
    from .core.app import App
    from .core.bridge import StubBridge, active_bridge, set_bridge
    from .core.ui.audit import audit_ui

    config = _load(args)
    entry = _entrypoint(config)
    bridge = StubBridge(verbose=False)
    previous_bridge = active_bridge()
    set_bridge(bridge)
    previous_app = App.current()
    try:
        _execute(config, entry)
        _navigate(args)
        tree = bridge.last_tree
        if tree is None:
            raise PyMobileError(
                "The app rendered nothing.",
                hint="Make sure the entry point calls App(...).run(SomeScreen()).",
            )

        app = App.current()
        issues = audit_ui(tree, width=args.width, min_touch_target=args.min_touch_target)
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "severity": issue.severity,
                            "code": issue.code,
                            "widget_id": issue.widget_id,
                            "message": issue.message,
                            "hint": issue.hint,
                        }
                        for issue in issues
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            title = app.name if app is not None else config.name
            _out.info(f"checking {title!r} at {args.width} dp wide")
            if issues:
                for issue in issues:
                    _out.warn(f"{issue.widget_id} [{issue.code}] {issue.message}")
                    _out.hint(issue.hint)
                _out.info(f"{len(issues)} advisory warning(s)")
            else:
                _out.ok("no issues found by the static UI audit")
            _out.hint(
                "heuristic only: verify font scaling, screen-reader behavior and layout on a device"
            )
        return 1 if args.strict and issues else 0
    finally:
        app = App.current()
        if app is not None and app is not previous_app:
            app.stop()
        set_bridge(previous_bridge)


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
    size = "~4 GB" if with_ndk and with_emulator else (
        "~3.5 GB" if with_emulator else "~2.7 GB" if with_ndk else "~900 MB"
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
    """Find the SDK for adb/emulator commands without requiring build tools."""
    explicit = getattr(args, "sdk", None)
    if explicit:
        return Path(explicit).expanduser().resolve()
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser().resolve()
    from .compiler.sdk_installer import default_sdk_home

    return default_sdk_home() / "sdk"


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
                environment["PATH"] = (
                    f"{jdk / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
                )
                break
    return environment


def _create_avd(root: Path, name: str) -> None:
    import subprocess

    avdmanager = _sdk_executable(root, "cmdline-tools/latest/bin", "avdmanager")
    command = [
        str(avdmanager),
        "create",
        "avd",
        "--name",
        name,
        "--package",
        _DEFAULT_EMULATOR_IMAGE,
        "--force",
    ]
    completed = subprocess.run(
        command,
        input="no\n",
        capture_output=True,
        text=True,
        env=_device_environment(root),
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
    _out.ok(f"created Android virtual device {name}")


def cmd_emulator(args: argparse.Namespace) -> int:
    """Create, list, or start the optional local Android emulator."""
    import subprocess

    root = _sdk_root_for_device(args)
    action = args.emulator_action
    name = getattr(args, "name", _DEFAULT_AVD_NAME)
    if action == "create":
        _create_avd(root, name)
        return 0

    emulator = _sdk_executable(root, "emulator", "emulator")
    if action == "list":
        completed = subprocess.run(
            [str(emulator), "-list-avds"],
            capture_output=True,
            text=True,
            env=_device_environment(root),
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
        env=_device_environment(root),
        check=False,
    )
    if listed.returncode != 0:
        raise PyMobileError("could not query Android virtual devices", hint=listed.stderr.strip())
    if name not in listed.stdout.splitlines():
        _out.info(f"creating the default virtual device {name}")
        _create_avd(root, name)
    command = [str(emulator), "-avd", name, "-no-snapshot"]
    if getattr(args, "no_window", False):
        command.append("-no-window")
    if getattr(args, "no_audio", False):
        command.append("-no-audio")
    _out.info(f"starting {name} — close the emulator window or press Ctrl+C to stop")
    emulator_process = subprocess.run(command, env=_device_environment(root), check=False)
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

    config = _load(args)
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

    root = _sdk_root_for_device(args)
    adb = _sdk_executable(root, "platform-tools", "adb")
    environment = _device_environment(root)
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
    _out.ok(f"installed {apk.name} on {device}" + (f" — {result}" if result else ""))
    return 0


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
    """Construct the argument parser.

    ``--verbose`` and ``--config`` are attached both globally and to every
    sub-command, so ``pymobile -v build`` and ``pymobile build -v`` both work —
    users should not have to remember where a flag belongs.
    """
    # SUPPRESS keeps unset flags out of the namespace, so a sub-command does
    # not silently reset a value given before the sub-command name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show detailed output",
    )
    common.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"path to {CONFIG_FILENAME} or a project directory",
    )

    # Which screen to show: shared by run, watch and preview.
    where = argparse.ArgumentParser(add_help=False)
    where.add_argument(
        "--screen",
        metavar="NAME",
        help="show this screen (a Screen subclass of the project) instead of the first one",
    )
    where.add_argument(
        "--args",
        dest="screen_args",
        metavar="JSON",
        help=(
            "with --screen: constructor arguments as JSON — an object is keywords "
            '(\'{"score": 7}\'), a list is positional (\'[7, 10]\'). cmd.exe and Windows '
            "PowerShell eat the quotes inside JSON: use --set there"
        ),
    )
    where.add_argument(
        "--set",
        dest="screen_set",
        metavar="NAME=VALUE",
        action="append",
        help=(
            "with --screen: one constructor argument, e.g. --set score=7 --set player=Anna "
            "(repeatable; the value is JSON if it parses — 7, true, [1, 2] — else plain text; "
            "needs no quoting in any shell)"
        ),
    )
    where.add_argument(
        "--navigate",
        metavar="STEPS",
        help=(
            'press your way to a screen: "Menu.start, Quiz.next" takes the step '
            "[Screen.]widget in order; widget=value types a value, <back> goes back"
        ),
    )

    parser = argparse.ArgumentParser(
        prog="pymobile",
        description="Build Android applications with Python.",
        epilog="Docs: https://github.com/Maksum867/PyMobile",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"pymobile {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    init = sub.add_parser("init", help="create a new project", parents=[common])
    init.add_argument("directory", nargs="?", default=".", help="target directory")
    init.add_argument("-n", "--name", help="application name")
    init.add_argument("-p", "--package", help="package id, e.g. com.example.app")
    init.add_argument("-f", "--force", action="store_true", help="write into a non-empty directory")
    init.set_defaults(func=cmd_init)

    build = sub.add_parser("build", help="compile the project into an APK", parents=[common])
    build.add_argument("-o", "--output", help="output directory")
    build.add_argument("-i", "--icon", help="path to a custom launcher icon")
    build.add_argument(
        "--native",
        action="store_true",
        help=(
            "build a real, signed, installable APK "
            "(needs the Android SDK; NDK only to rebuild JNI)"
        ),
    )
    build.add_argument("--clean", action="store_true", help="rebuild from scratch")
    build.add_argument("--no-cache", action="store_true", help="ignore the incremental cache")
    build.add_argument("--optimize", action="store_true", help="ship bytecode instead of sources")
    build.add_argument(
        "--no-optimize",
        action="store_true",
        help="ship .py sources even if pymobile.toml has optimize = true",
    )
    build.add_argument("--keystore", metavar="PATH", help="release keystore for --native signing")
    build.add_argument(
        "--ks-pass",
        metavar="PASS",
        help="keystore password; prefer the PYMOBILE_KS_PASS environment variable",
    )
    build.add_argument("--key-alias", metavar="ALIAS", help="key alias inside the keystore")
    build.add_argument(
        "--key-pass",
        metavar="PASS",
        help="key password (defaults to the keystore password; or PYMOBILE_KEY_PASS)",
    )
    build.add_argument(
        "--minimal-stdlib",
        action="store_true",
        help="drop desktop-only stdlib packages (pydoc, unittest, venv, ...): ~1.7 MB",
    )
    build.add_argument(
        "--no-ssl",
        action="store_true",
        help="omit OpenSSL and the CA bundle: ~4 MB smaller, but no HTTPS",
    )
    build.add_argument(
        "--abi",
        choices=("arm64-v8a", "x86_64"),
        help=(
            "CPU architecture of the APK: arm64-v8a (phones, the default) or "
            "x86_64 (the Android Studio emulator); overrides `abis` in pymobile.toml"
        ),
    )
    build.set_defaults(func=cmd_build)

    run = sub.add_parser(
        "run", help="preview the app on this machine", parents=[common, where]
    )
    run.add_argument(
        "--gui",
        action="store_true",
        help="open a clickable window instead of printing to the console",
    )
    run.add_argument(
        "--web",
        action="store_true",
        help="serve a clickable preview in the browser (works over SSH)",
    )
    run.add_argument("--port", type=int, default=8765, help="port for --web (default: 8765)")
    run.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "interface for --web (default: 127.0.0.1, this machine only). Any "
            "other value exposes the preview to the network and requires its "
            "session token"
        ),
    )
    run.set_defaults(func=cmd_run)

    watch = sub.add_parser(
        "watch",
        help="re-render automatically when a source file changes",
        parents=[common, where],
    )
    watch.add_argument(
        "--png", metavar="PATH", help="write a mockup of the screen as a PNG on every reload"
    )
    watch.add_argument(
        "--text", action="store_true", help="with --png: write the text picture instead"
    )
    watch.add_argument("--ids", action="store_true", help="annotate widgets with their id")
    watch.add_argument(
        "--interval",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="how often to poll for changes (default: 0.2)",
    )
    watch.set_defaults(func=cmd_watch)

    preview = sub.add_parser(
        "preview",
        help="draw a screen (the first, or --screen / --navigate) as a desktop picture",
        parents=[common, where],
    )
    preview.add_argument(
        "--png",
        metavar="PATH",
        help="save a mockup of the screen as the phone draws it (needs Pillow)",
    )
    preview.add_argument(
        "--text",
        action="store_true",
        help="with --png: save the text picture instead of the mockup (the pre-0.8 output)",
    )
    preview.add_argument(
        "--theme",
        choices=("light", "dark"),
        help="with --png: build the tree and draw it in this theme instead of the app's",
    )
    preview.add_argument(
        "--size",
        metavar="WxH",
        help=(
            "with --png: screen size in dp, e.g. 360x640 "
            "(default: 360 wide, as tall as the content)"
        ),
    )
    preview.add_argument("--ids", action="store_true", help="annotate widgets with their id")
    preview.set_defaults(func=cmd_preview)

    check_ui = sub.add_parser(
        "check-ui",
        help="warn about accessibility gaps and likely narrow-screen overflow",
        parents=[common, where],
    )
    check_ui.add_argument(
        "--width",
        type=_positive_int,
        default=320,
        metavar="DP",
        help="narrow screen width to check (default: 320 dp)",
    )
    check_ui.add_argument(
        "--min-touch-target",
        type=_positive_int,
        default=48,
        metavar="DP",
        help="minimum explicit control width/height to accept (default: 48 dp)",
    )
    check_ui.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when advisory warnings are found",
    )
    check_ui.add_argument("--json", action="store_true", help="print machine-readable warnings")
    check_ui.set_defaults(func=cmd_check_ui)

    info = sub.add_parser("info", help="show the resolved configuration", parents=[common])
    info.add_argument("--json", action="store_true", help="machine-readable output")
    info.set_defaults(func=cmd_info)

    clean = sub.add_parser("clean", help="remove build artifacts", parents=[common])
    clean.set_defaults(func=cmd_clean)

    widget_group = sub.add_parser("widget", help="create project-local custom widgets")
    widget_actions = widget_group.add_subparsers(dest="widget_action", required=True)
    widget_add = widget_actions.add_parser(
        "add",
        help="generate a Python widget and project-local Android renderer",
        parents=[common],
    )
    widget_add.add_argument("type_name", metavar="TYPE", help="custom type name, e.g. BarChart")
    widget_add.add_argument(
        "-p",
        "--prop",
        action="append",
        default=[],
        metavar="NAME[:TYPE]",
        help="widget prop; TYPE is str (default), int, float, bool or list; repeat as needed",
    )
    widget_add.set_defaults(func=cmd_widget_add)

    widget = sub.add_parser(
        "widget-java",
        help="print the ViewBuilder.java branch for a custom widget type",
        parents=[common],
    )
    widget.add_argument("type_name", metavar="TYPE", help="the widget's type_name, e.g. BarChart")
    widget.add_argument(
        "-p",
        "--prop",
        action="append",
        default=[],
        metavar="NAME[:TYPE]",
        help=(
            "a prop the Python widget sends; TYPE is str (default), int, float, bool or "
            "list. Repeat for several: -p title -p value:int"
        ),
    )
    widget.add_argument("-o", "--out", metavar="FILE", help="write the guide to FILE, not stdout")
    widget.set_defaults(func=cmd_widget_java)

    setup = sub.add_parser(
        "setup-sdk", help="download the Android SDK/NDK for native builds", parents=[common]
    )
    setup.add_argument("--path", help="install directory (default: ~/.andro)")
    setup.add_argument(
        "--with-ndk",
        action="store_true",
        help="also download the NDK (~2 GB), only needed to rebuild the native bridge",
    )
    setup.add_argument(
        "--with-emulator",
        action="store_true",
        help="also download the x86_64 emulator and API 35 image (several extra GB; no NDK)",
    )
    setup.set_defaults(func=cmd_setup_sdk)

    install = sub.add_parser(
        "install",
        help="install a built APK on a connected Android device/emulator",
        parents=[common],
    )
    install.add_argument(
        "apk",
        nargs="?",
        help="APK path (default: this project's configured build output)",
    )
    install.add_argument(
        "--abi",
        choices=("arm64-v8a", "x86_64"),
        help="choose the matching configured APK",
    )
    install.add_argument(
        "--device",
        metavar="SERIAL",
        help="adb device serial (auto-picks if exactly one is connected)",
    )
    install.add_argument(
        "--sdk",
        metavar="PATH",
        help="Android SDK root (otherwise ANDROID_HOME / ~/.andro/sdk)",
    )
    install.set_defaults(func=cmd_install)

    emulator = sub.add_parser(
        "emulator",
        help="manage a local Android emulator (optional multi-GB download)",
    )
    emulator_actions = emulator.add_subparsers(dest="emulator_action", required=True)
    emulator_create = emulator_actions.add_parser(
        "create", help="create the default x86_64 virtual device"
    )
    emulator_create.add_argument(
        "--name",
        default=_DEFAULT_AVD_NAME,
        help=f"AVD name (default: {_DEFAULT_AVD_NAME})",
    )
    emulator_create.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_create.set_defaults(func=cmd_emulator)
    emulator_list = emulator_actions.add_parser(
        "list", help="list existing Android virtual devices"
    )
    emulator_list.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_list.set_defaults(func=cmd_emulator)
    emulator_start = emulator_actions.add_parser(
        "start", help="start a virtual device; creates it if missing"
    )
    emulator_start.add_argument(
        "--name",
        default=_DEFAULT_AVD_NAME,
        help=f"AVD name (default: {_DEFAULT_AVD_NAME})",
    )
    emulator_start.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_start.add_argument(
        "--no-window",
        action="store_true",
        help="run headless (useful on CI with acceleration)",
    )
    emulator_start.add_argument("--no-audio", action="store_true", help="disable audio output")
    emulator_start.set_defaults(func=cmd_emulator)

    doctor = sub.add_parser("doctor", help="check the environment", parents=[common])
    doctor.set_defaults(func=cmd_doctor)

    return parser


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
