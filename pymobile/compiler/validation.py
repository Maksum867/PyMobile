"""Side-effect-free entrypoint checks shared by build validation.

Do not execute user code during a build: it may access device-only APIs or
perform network/file writes. This is deliberately advisory, not proof of launch.
"""

from __future__ import annotations

import ast
from pathlib import Path


def entrypoint_warnings(entry: Path) -> list[str]:
    """Flag an entrypoint with no visible run() call without executing it.

    Delegating startup to an imported module is valid, so absence is a warning.
    Syntax errors remain the responsibility of the compiler's syntax check.
    """
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8-sig"), filename=str(entry))
    except (OSError, UnicodeError, SyntaxError):
        return []
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        for node in ast.walk(tree)
    ):
        return []
    return [
        f"{entry.name}: no .run(...) call found; this APK may open no screen. "
        "Call App(...).run(SomeScreen()), or verify delegated startup with "
        "`pymobile preview --text` before distributing."
    ]
