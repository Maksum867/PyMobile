"""Focused CLI implementations; cli retains replaceable compatibility entry points."""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from pathlib import Path
from typing import Any

from . import cli
from .core.config import ProjectConfig
from .errors import PyMobileError


def cmd_run(args: argparse.Namespace) -> int:
    """Import the entry point and render the first screen on the desktop."""
    config = cli._load(args)
    entry = cli._entrypoint(config)

    if getattr(args, "web", False):
        return cli._run_web(config, entry, args)
    if getattr(args, "gui", False):
        return cli._run_gui(config, entry, args)

    cli._out.info(f"running {entry.name} in desktop preview mode")
    cli._execute(config, entry)
    cli._navigate(args, config)

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
    cli._out.hint("use --gui for a clickable window")
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
    cli._execute(config, entry)
    cli._navigate(args, config)
    app = App.current()
    if app is None:
        raise PyMobileError(
            "No running application was found in the entry point.",
            hint="Make sure it calls App(...).run(SomeScreen()) before returning.",
        )

    cli._out.ok(f"{app.name} is running — close the window to stop")
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
    cli._execute(config, entry)
    cli._navigate(args, config)
    app = App.current()
    if app is None:
        raise PyMobileError(
            "No running application was found in the entry point.",
            hint="Make sure it calls App(...).run(SomeScreen()) before returning.",
        )

    preview = WebPreview(
        app,
        host=args.host,
        port=args.port,
        token=getattr(args, "token", None),
        require_token=not getattr(args, "no_token", False),
        allow_origins=tuple(getattr(args, "allow_origin", None) or ()),
        frame_options="" if getattr(args, "embed", False) else "DENY",
    )
    bridge.attach(preview)
    cli._out.ok(f"{app.name} is running at {browser_url(args.host, preview.port, preview.token)}")
    if preview.exposed and preview.require_token:
        # Network exposure is opt-in, and the warning says what it means: the
        # port can read the app's state and press its widgets, so the session
        # token (already part of the printed URL) is mandatory.
        cli._out.warn(
            f"listening on {args.host}: the preview is reachable from the network. "
            "It requires the session token in the URL above; treat that URL as a "
            "secret and use an SSH tunnel (--host 127.0.0.1) when you only need "
            "it locally."
        )
    elif preview.exposed:
        cli._out.warn(
            f"listening on {args.host} WITHOUT a session token (--no-token): anyone "
            "who can reach this port can read the screen and press its widgets. "
            "Only do this behind a proxy, tunnel or container network that "
            "authenticates callers."
        )
    if preview.allow_origins:
        note = (
            " (every origin — the gateway must authenticate)"
            if "*" in preview.allow_origins
            else ""
        )
        cli._out.hint(
            "cross-origin requests are allowed from: " + ", ".join(preview.allow_origins) + note
        )
    cli._out.info("press Ctrl+C to stop")
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C is how you stop it
        preview.serve_forever()
    cli._out.ok("stopped")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """Interactive window + live reload — ``run --gui`` and ``watch`` in one.

    The two commands existed but had to be remembered and combined by hand:
    ``watch`` printed a picture on every save, ``run --gui`` opened a window
    that never noticed an edit. ``dev`` keeps a Tk window open and re-executes
    the app inside it whenever a source file changes, which is the loop a
    desktop preview is for. Falls back to the text preview when Tk is missing.
    """
    from .core.bridge import GuiBridge, StubBridge, set_bridge
    from .core.ui.gui import tkinter_available
    from .core.watcher import FileWatcher

    config = cli._load(args)
    entry = cli._entrypoint(config)
    roots = [config.source_path]

    if not tkinter_available():
        cli._out.warn("Tkinter is not available; showing the text preview instead")
        cli._out.hint("install python3-tk (Linux) or use `pymobile watch`")
        return cli.cmd_watch(args)

    cli._out.info(f"dev: {config.name} — edit a file to reload, Ctrl+C to stop")
    window = None
    try:
        while True:
            cli._purge_project_modules(config.source_path)
            # A fresh bridge per reload, so a removed widget does not linger.
            bridge = GuiBridge(verbose=False)
            set_bridge(bridge)
            try:
                cli._execute(config, entry)
                cli._navigate(args, config)
            except Exception as error:
                cli._out.error(f"{type(error).__name__}: {error}")
                if isinstance(error, PyMobileError) and error.hint:
                    cli._out.hint(error.hint)
                if getattr(args, "verbose", False):
                    import traceback

                    traceback.print_exc()
            try:
                window = cli._open_gui_window(bridge, config)
            except Exception as error:
                import tkinter

                if not isinstance(error, tkinter.TclError):
                    raise
                cli._out.warn(f"Cannot open a Tk display: {error}; using text watch instead")
                from .core.app import App

                app = App.current()
                if app is not None and app.bridge is bridge:
                    app.stop()
                set_bridge(StubBridge(verbose=False))
                return cli.cmd_watch(args)
            cli._out.ok("watching for changes")
            watcher = FileWatcher(roots, interval=getattr(args, "interval", 0.5))
            changed = watcher.wait()
            names = ", ".join(path.name for path in changed[:3])
            if len(changed) > 3:
                names += f" (+{len(changed) - 3})"
            cli._out.info(f"changed: {names} — reloading")
            cli._close_gui_window(window)
            window = None
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print()
        cli._out.ok("stopped")
        return 0
    finally:
        if window is not None:
            cli._close_gui_window(window)
        set_bridge(StubBridge(verbose=False))


def _open_gui_window(bridge: Any, config: ProjectConfig) -> Any:
    """Open the Tk preview for ``dev`` without blocking on ``mainloop()``.

    Returns the preview, or ``None`` on a headless machine — ``dev`` then keeps
    reloading and says so instead of dying on the first save.
    """
    from .core.app import App
    from .core.ui.gui import GuiPreview

    app = App.current()
    if app is None:
        return None
    preview = GuiPreview(app, title=f"{app.name} — dev")
    bridge.attach(preview)
    preview.root.update_idletasks()
    preview.root.update()
    return preview


def _close_gui_window(preview: Any) -> None:
    """Close a window opened by :func:`_open_gui_window` (missing display, too)."""
    if preview is None:
        return
    # The window may already be gone (closed by the user, or never drawn).
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        preview.root.destroy()


def cmd_watch(args: argparse.Namespace) -> int:
    """Re-render the app whenever a source file changes."""
    from .core.watcher import FileWatcher

    config = cli._load(args)
    entry = cli._entrypoint(config)
    roots = [config.source_path]

    cli._out.info(f"watching {config.source_path} — press Ctrl+C to stop")
    cli._reload(config, entry, args)

    watcher = FileWatcher(roots, interval=args.interval)
    try:
        while True:
            changed = watcher.wait()
            names = ", ".join(path.name for path in changed[:3])
            if len(changed) > 3:
                names += f" (+{len(changed) - 3})"
            print()
            cli._out.info(f"changed: {names}")
            cli._reload(config, entry, args)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print()
        cli._out.ok("stopped watching")
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
    cli._purge_project_modules(config.source_path)

    try:
        cli._execute(config, entry)
        cli._navigate(args, config)
    except Exception as error:
        cli._out.error(f"{type(error).__name__}: {error}")
        if isinstance(error, PyMobileError) and error.hint:
            cli._out.hint(error.hint)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return

    tree = bridge.last_tree
    if tree is None:
        cli._out.warn("the app rendered nothing")
        return

    elapsed = (time.perf_counter() - started) * 1000
    from .core.app import App

    app = App.current()
    display_title = app.name if app is not None else config.name
    if args.png and getattr(args, "text", False):
        render_png(tree, args.png)
        cli._out.ok(f"wrote {args.png} in {elapsed:.0f} ms")
    elif args.png:
        render_mockup(
            tree,
            args.png,
            theme=app.theme if app is not None else None,
            title=display_title,
            assets=config.source_path,
        )
        elapsed = (time.perf_counter() - started) * 1000
        cli._out.ok(f"wrote {args.png} in {elapsed:.0f} ms")
    else:
        print(render_ascii(tree, show_ids=args.ids, title=display_title))
        cli._out.ok(f"rendered in {elapsed:.0f} ms")


def cmd_preview(args: argparse.Namespace) -> int:
    """Render a screen of the app — the first, or the one --screen/--navigate reach."""
    from .core.app import App
    from .core.bridge import StubBridge, set_bridge
    from .core.ui.preview import render_ascii, render_mockup, render_png

    config = cli._load(args)
    entry = cli._entrypoint(config)

    bridge = StubBridge(verbose=False)
    set_bridge(bridge)
    cli._execute(config, entry)

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

    cli._navigate(args, config)

    tree = bridge.last_tree
    if tree is None:
        raise PyMobileError(
            "The app rendered nothing.",
            hint="Make sure the entry point calls App(...).run(SomeScreen()).",
        )

    if args.png and getattr(args, "text", False):
        path = render_png(tree, args.png)
        cli._out.ok(f"wrote the text preview to {path}")
    elif args.png:
        width, height = cli._parse_size(getattr(args, "size", None))
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
        cli._out.ok(f"wrote a mockup of the screen to {path}")
        cli._out.hint("an approximation: fonts and system colours differ between phones")
    else:
        print(render_ascii(tree, show_ids=args.ids, title=display_title))
    return 0


def cmd_check_ui(args: argparse.Namespace) -> int:
    """Run the static accessibility and narrow-screen audit on the app tree."""
    from .core.app import App
    from .core.bridge import StubBridge, active_bridge, set_bridge
    from .core.ui.audit import audit_ui

    config = cli._load(args)
    entry = cli._entrypoint(config)
    bridge = StubBridge(verbose=False)
    previous_bridge = active_bridge()
    set_bridge(bridge)
    previous_app = App.current()
    try:
        cli._execute(config, entry)
        app = App.current()
        requested_theme = getattr(args, "theme", None)
        if requested_theme and app is not None:
            app.set_theme(requested_theme)
        cli._navigate(args, config)
        tree = bridge.last_tree
        if tree is None:
            raise PyMobileError(
                "The app rendered nothing.",
                hint="Make sure the entry point calls App(...).run(SomeScreen()).",
            )

        app = App.current()
        issues = audit_ui(
            tree,
            width=args.width,
            min_touch_target=args.min_touch_target,
            background=app.theme.color("BACKGROUND") if app is not None else None,
        )
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
            cli._out.info(f"checking {title!r} at {args.width} dp wide")
            if issues:
                for issue in issues:
                    cli._out.warn(f"{issue.widget_id} [{issue.code}] {issue.message}")
                    cli._out.hint(issue.hint)
                cli._out.info(f"{len(issues)} advisory warning(s)")
            else:
                cli._out.ok("no issues found by the static UI audit")
            cli._out.hint(
                "heuristic only: verify font scaling, screen-reader behavior and layout on a device"
            )
        return 1 if args.strict and issues else 0
    finally:
        app = App.current()
        if app is not None and app is not previous_app:
            app.stop()
        set_bridge(previous_bridge)
