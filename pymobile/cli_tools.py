"""Project commands: ``init``, ``doctor``, ``schema`` and shell completion.

Kept apart from ``cli.py`` so that module stays small. ``cli`` is imported as a
module, not by name, so it is fully loaded before any of these functions run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import cli
from .compiler.scaffold import TEMPLATE_DESCRIPTIONS, create_project
from .errors import PyMobileError

__all__ = ["cmd_completion", "cmd_complete", "cmd_init", "cmd_schema", "cmd_doctor"]


def _interactive() -> bool:
    """Ask questions only when a person is at the terminal, never in scripts or CI."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask_template() -> str:
    """Show the templates as a numbered menu; Enter keeps ``basic``."""
    names = list(TEMPLATE_DESCRIPTIONS)
    print("Starter template:")
    for number, key in enumerate(names, start=1):
        marker = " (default)" if key == "basic" else ""
        print(f"  {number}. {key:<6} {TEMPLATE_DESCRIPTIONS[key]}{marker}")
    while True:
        answer = input(f"Choose 1-{len(names)} or a name [basic]: ").strip().lower()
        if not answer:
            return "basic"
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            return names[int(answer) - 1]
        if answer in names:
            return answer
        print(f"  not a template: {answer!r}")


def cmd_init(args: argparse.Namespace) -> int:
    """Create a new project."""
    if getattr(args, "list_templates", False):
        for key, description in TEMPLATE_DESCRIPTIONS.items():
            print(f"{key:<6} {description}")
        return 0

    directory = Path(args.directory).resolve()
    default_name = directory.name.replace("-", " ").replace("_", " ").title()
    name = args.name
    template = args.template
    if _interactive():
        if name is None:
            name = input(f"Application name [{default_name}]: ").strip() or None
        if template is None:
            template = _ask_template()
    name = name or default_name
    result = create_project(
        directory,
        name,
        package=args.package,
        force=args.force,
        template=template or "basic",
    )
    cli._out.ok(f"created project {name!r} in {result.directory}")
    for path in result.files:
        cli._out.field("", path.relative_to(result.directory))
    print()
    cli._out.info(f"cd {result.directory.name} && {cli._invocation()} build --native")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """Print the JSON Schema for pymobile.toml, or write it to a file."""
    from .compiler.schema import build_config_schema, write_config_schema

    if getattr(args, "output", None):
        target = write_config_schema(args.output)
        cli._out.ok(f"wrote the pymobile.toml schema to {target}")
        return 0
    print(json.dumps(build_config_schema(), indent=2, ensure_ascii=False))
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """Print the tab-completion script for a shell."""
    from .cli_completion import completion_script

    print(completion_script(args.shell))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """Internal: list completion candidates for the line typed so far."""
    from .cli_completion import complete_line

    for candidate in complete_line(cli.build_parser(), args.line):
        print(candidate)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the environment and configuration."""
    _out, _load, _invocation = cli._out, cli._load, cli._invocation
    problems = 0
    _out.info("Desktop preview — basic project checks")
    try:
        config = _load(args)
    except PyMobileError as exc:
        _out.warn(f"no usable project here: {exc}")
        if exc.hint:
            _out.hint(exc.hint)
        _out.hint("Open the project directory or pass -c PATH, then run doctor again.")
        _out.warn("Desktop preview: not checked — a usable project is required")
        _out.warn("Native APK: not checked — a usable project is required")
        return 1 if getattr(args, "strict", False) else 0

    _out.ok(f"configuration valid: {config.name} ({config.package})")
    entry_ok = config.entrypoint_path.is_file()
    if not entry_ok:
        _out.error(f"entry point missing: {config.entrypoint_path}")
        _out.hint("Restore the entry point or correct entrypoint and source_dir in the config.")
        problems += 1
    else:
        _out.ok(f"entry point found: {config.entrypoint}")

    icon = config.icon_path
    if icon is not None and not icon.exists():
        _out.error(f"icon missing: {icon}")
        _out.hint("Fix or remove the icon path in the project config before building.")
        problems += 1
    elif icon is not None:
        _out.ok(f"custom icon: {icon.name}")
    else:
        _out.ok("using the default icon")

    # pip is only necessary for declared third-party packages. Do not confuse
    # Pillow (optional build-time icon resizing) with an Android dependency.
    if config.dependencies:
        import importlib.util

        if importlib.util.find_spec("pip") is None:
            _out.error("pip is missing — app dependencies cannot be installed")
            _out.hint(f"{Path(sys.executable).name} -m ensurepip --upgrade")
            problems += 1
        else:
            _out.ok(f"pip available for {len(config.dependencies)} declared dependencies")
        _out.info(f"check package compatibility: {_invocation()} deps check")

    if entry_ok:
        _out.ok("Desktop preview: configuration and entry point found; app not run")
        _out.info(f"from the project directory, try `{_invocation()} run`")
        _out.info(f"for a browser preview, use `{_invocation()} run --web`")
        _out.info("app imports and dependencies were not checked; GUI preview needs a display")
    else:
        _out.warn("Desktop preview: not ready — the entry point is missing")

    _out.info("Native APK — project and toolchain checks")
    try:
        free = shutil.disk_usage(config.root).free
        if free < 1024**3:
            _out.warn(f"only {free / 1024**3:.1f} GiB free; native setup needs ~1 GiB")
            _out.hint("Free disk space or choose another setup location with setup-sdk --path.")
        else:
            _out.ok(f"disk space: {free / 1024**3:.1f} GiB free")
    except OSError as exc:
        _out.warn(f"could not check disk space: {exc}")

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
        _out.info(f"to attempt a native build: {_invocation()} build --native")
        sdk_ok = True
    except (ToolchainError, PyMobileError) as exc:
        _out.warn(f"Android SDK or toolchain not usable: {exc}")
        if exc.hint:
            _out.hint(exc.hint)
        _out.hint(f"run `{_invocation()} setup-sdk` to install the toolchain")

    if sdk_ok and not problems:
        _out.ok("Native APK: basic project and toolchain checks passed; build not run")
    elif not sdk_ok:
        _out.warn("Native APK: not ready — Android toolchain is missing or incomplete")
    else:
        _out.warn("Native APK: not ready — fix the project problems above")
    _out.info("doctor does not run the app, install tools, or build an APK")

    if problems:
        _out.error(f"{problems} problem(s) found")
        return 1
    if sdk_ok:
        _out.ok("everything looks good (basic checks only)")
    else:
        _out.ok("project configuration looks good")
        _out.warn("native APK builds need a usable Android toolchain")
    return 1 if getattr(args, "strict", False) and not sdk_ok else 0
