"""Project-level commands: ``init`` (with its questions), ``schema`` and shell completion.

Kept apart from ``cli.py`` so that module stays small. ``cli`` is imported as a
module, not by name, so it is fully loaded before any of these functions run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli
from .compiler.scaffold import TEMPLATE_DESCRIPTIONS, create_project

__all__ = ["cmd_completion", "cmd_complete", "cmd_init", "cmd_schema"]


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
