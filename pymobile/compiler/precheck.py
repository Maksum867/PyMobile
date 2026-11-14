"""``pymobile check``: find the mistakes that would stop a build, with locations.

``doctor`` checks the machine and ``check-ui`` checks the layout. This module
checks the project itself, in the order a build would hit problems:

1. ``pymobile.toml`` (or ``[tool.pymobile]``): TOML syntax, unknown keys and
   invalid values, each reported with its line and a suggestion.
2. The entry point: it exists and is valid Python, with a line and column.
3. ``from pymobile import ...`` names: a misspelled widget is reported at its
   import line instead of as a ``NameError`` on the phone.
4. A desktop start-up with the stub bridge, the same as ``build --check-startup``:
   an exception raised while the app starts is reported at the line of
   ``main.py`` that raised it.

Step 4 runs your code, like ``check-ui`` does. Nothing is installed or sent.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
import tokenize
import traceback
from dataclasses import dataclass, fields
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the same parser ships as tomli
    import tomli as tomllib  # type: ignore[no-redef]

import pymobile

from ..core.config import CONFIG_FILENAME, ProjectConfig, load_config
from ..errors import PyMobileError

__all__ = ["Finding", "check_project", "format_finding", "has_project_files"]

_UNKNOWN_KEYS_PREFIX = "Unknown configuration key(s): "
_TOML_POSITION = re.compile(r"at line (\d+), column (\d+)")


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem, with the place it was found."""

    path: Path
    message: str
    line: int | None = None
    column: int | None = None
    hint: str | None = None


def format_finding(finding: Finding, root: Path) -> str:
    """``main.py:12:5: error: ...`` plus an indented hint, when there is one."""
    try:
        shown = finding.path.resolve().relative_to(root.resolve())
    except ValueError:
        shown = finding.path
    location = str(shown)
    if finding.line is not None:
        location += f":{finding.line}"
        if finding.column is not None:
            location += f":{finding.column}"
    text = f"{location}: error: {finding.message}"
    if finding.hint:
        text += f"\n  hint: {finding.hint}"
    return text


def has_project_files(root: str | Path) -> bool:
    """Whether ``root`` holds a configuration file that :func:`check_project` can read."""
    return _config_file(Path(root).resolve()) is not None


def _icon_problem(config: ProjectConfig) -> PyMobileError | None:
    """The same icon rules ``build`` enforces, without writing anything."""
    from .icon import _validate_source

    icon = config.icon_path
    if icon is None:
        return None
    try:
        _validate_source(icon)
    except PyMobileError as exc:
        return exc
    return None


def check_project(root: str | Path, *, run_app: bool = True) -> list[Finding]:
    """Return every problem found in the project at ``root``; empty when it is ready.

    ``run_app=False`` skips the desktop start-up. ``build --native`` already
    performs that check, so the build only needs the static part.
    """
    root = Path(root).resolve()
    config_file = _config_file(root)
    if config_file is None:
        return [
            Finding(
                root / CONFIG_FILENAME,
                f"no {CONFIG_FILENAME} found in {root}",
                hint="Run `pymobile init` to create a new project.",
            )
        ]

    text = config_file.read_text(encoding="utf-8", errors="replace")
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        line, column = _toml_position(str(exc))
        return [
            Finding(
                config_file,
                f"{config_file.name} is not valid TOML: {exc}",
                line,
                column,
                hint="Check for missing quotes, brackets or a stray '=' .",
            )
        ]

    findings: list[Finding] = []
    config: ProjectConfig | None = None
    try:
        config = load_config(config_file)
    except PyMobileError as exc:
        findings.extend(_config_findings(config_file, text, exc))

    if config is not None and config.icon:
        icon_problem = _icon_problem(config)
        if icon_problem is not None:
            findings.append(
                Finding(
                    config_file,
                    icon_problem.message,
                    _key_line(text, "icon"),
                    None,
                    hint=icon_problem.hint,
                )
            )

    entry = config.entrypoint_path if config is not None else root / "main.py"
    if not entry.is_file():
        findings.append(
            Finding(
                entry,
                f"entry point not found: {entry.name}",
                hint="Check `entrypoint` in your configuration.",
            )
        )
        return findings

    source = entry.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(entry))
    except SyntaxError as exc:
        findings.append(Finding(entry, f"syntax error: {exc.msg}", exc.lineno, exc.offset))
        return findings

    name_findings = _import_findings(entry, tree)
    findings.extend(name_findings)
    findings.extend(_handler_findings(entry, source, tree))
    findings.extend(_module_findings(root, entry))
    if findings or config is None or not run_app:
        return findings
    return _startup_findings(config, entry)


def _config_file(root: Path) -> Path | None:
    """``pymobile.toml`` in ``root``, else a ``[tool.pymobile]`` in ``pyproject.toml``."""
    candidate = root / CONFIG_FILENAME
    if candidate.is_file():
        return candidate
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        if isinstance(data.get("tool", {}).get("pymobile"), dict):
            return pyproject
    return None


def _toml_position(message: str) -> tuple[int | None, int | None]:
    match = _TOML_POSITION.search(message)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _key_line(text: str, key: str) -> int | None:
    """Line of the first ``key = ...`` or ``[key]`` assignment, if the file has one."""
    pattern = re.compile(rf"^\s*(\[\s*)?{re.escape(key)}\s*(\]|=)")
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.match(line):
            return number
    return None


def _config_findings(config_file: Path, text: str, exc: PyMobileError) -> list[Finding]:
    """Turn a ``load_config`` error into one finding per problem, with lines."""
    known = sorted({spec.name for spec in fields(ProjectConfig)} - {"root"})
    message = exc.message
    if message.startswith(_UNKNOWN_KEYS_PREFIX):
        unknown = message[len(_UNKNOWN_KEYS_PREFIX) :].split(", ")
        findings = []
        for key in unknown:
            close = difflib.get_close_matches(key, known, n=1)
            hint = f"did you mean {close[0]!r}?" if close else f"valid keys: {', '.join(known)}"
            findings.append(
                Finding(
                    config_file,
                    f"unknown configuration key {key!r}",
                    _key_line(text, key),
                    hint=hint,
                )
            )
        return findings
    return [Finding(config_file, message, hint=exc.hint)]


def _module_findings(root: Path, entry: Path) -> list[Finding]:
    """Check local Python modules without importing or executing them."""
    excluded = {"tests", "test", "build", "dist", "venv", "env", "__pycache__", "node_modules"}
    findings: list[Finding] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not d.startswith("."))
        for name in sorted(files):
            path = Path(directory) / name
            if not name.endswith(".py") or path.is_symlink() or path.resolve() == entry.resolve():
                continue
            try:
                with tokenize.open(path) as source:
                    tree = ast.parse(source.read(), filename=str(path))
            except SyntaxError as exc:
                findings.append(Finding(path, f"syntax error: {exc.msg}", exc.lineno, exc.offset))
            except (OSError, UnicodeError) as exc:
                findings.append(Finding(path, f"cannot read Python source: {exc}"))
            else:
                findings.extend(_import_findings(path, tree))
    return findings


def _import_findings(entry: Path, tree: ast.AST) -> list[Finding]:
    """``from pymobile import Buton`` → a finding at that line, with a suggestion."""
    public = list(pymobile.__all__)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module == "pymobile" and node.level == 0):
            continue
        for alias in node.names:
            if alias.name == "*" or alias.name in public:
                continue
            close = difflib.get_close_matches(alias.name, public, n=1)
            hint = (
                f"did you mean {close[0]!r}?"
                if close
                else "the public names are listed in pymobile.__all__"
            )
            findings.append(
                Finding(
                    entry,
                    f"{alias.name!r} is not part of pymobile",
                    alias.lineno or node.lineno,
                    (alias.col_offset + 1) if alias.lineno else node.col_offset + 1,
                    hint,
                )
            )
    return findings


#: Keyword arguments whose value is meant to be a handler, not a call.
HANDLER_KEYWORDS = frozenset(
    {
        "on_press",
        "on_click",
        "on_change",
        "on_toggle",
        "on_select",
        "on_submit",
        "on_search",
        "on_long_press",
        "on_refresh",
        "on_action",
        "on_tap",
        "on_end",
        "on_error",
        "on_tick",
        "on_dismiss",
        "on_open",
        "on_close",
        "on_retry",
    }
)

#: Names that return a handler instead of calling one; their parentheses are fine.
HANDLER_FACTORIES = frozenset(
    {"functools.partial", "partial", "callback", "handler", "make_handler", "lambda"}
)


def _handler_findings(entry: Path, source: str, tree: ast.AST) -> list[Finding]:
    """``on_press=self.tap()``: a handler written with parentheses.

    The call runs once while the tree is built and passes its result, so the
    button silently does nothing when tapped — no exception, no placeholder,
    nothing to search for. The AST sees the shape, so `pymobile check` names the
    line and the fix before the app is ever started.

    Only a bare call counts: ``lambda: self.tap()``, ``partial(f, 1)`` and a
    function returning a handler are correct and are left alone.
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg not in HANDLER_KEYWORDS:
                continue
            value = keyword.value
            if not isinstance(value, ast.Call):
                continue
            called = value.func
            if isinstance(called, ast.Attribute):
                name = called.attr
            elif isinstance(called, ast.Name):
                name = called.id
            else:  # pragma: no cover - a subscript call is not a handler
                continue
            if name in HANDLER_FACTORIES or name.lstrip("_").startswith("lambda"):
                continue
            text = ast.get_source_segment(source, value) or f"{name}(…)"
            keep = text[:-2] if text.endswith("()") else text
            hint = (
                f"drop the parentheses: {keyword.arg}={keep} — the call runs once"
                " while the screen is built, so nothing is left to tap."
            )
            findings.append(
                Finding(
                    entry,
                    f"{keyword.arg} is given the result of {text}, not a handler",
                    value.lineno,
                    value.col_offset + 1,
                    hint,
                )
            )
    return findings


def _startup_findings(config: ProjectConfig, entry: Path) -> list[Finding]:
    """Start the app on the stub bridge; report where it fails in ``main.py``."""
    from ..cli import _execute  # lazy: ``cli`` imports this package
    from ..core.bridge import StubBridge, active_bridge, set_bridge

    bridge = StubBridge(verbose=False)
    previous = active_bridge()
    set_bridge(bridge)
    try:
        _execute(config, entry)
    except Exception as exc:  # the app's own error is the finding
        # ``_execute`` already names the file and line for a user error; take
        # the line from it rather than from the traceback, which by then leads
        # through the framework's own frames.
        line = getattr(exc, "line", None) or _traceback_line(exc, entry)
        message = str(exc) if isinstance(exc, PyMobileError) else f"{type(exc).__name__}: {exc}"
        return [
            Finding(
                entry,
                message,
                line,
                hint="The app failed on the desktop stub; the phone would fail too.",
            )
        ]
    finally:
        set_bridge(previous)

    if bridge.last_tree is None:
        return [
            Finding(
                entry,
                "the app rendered nothing",
                hint="Make sure main.py calls App(...).run(SomeScreen()).",
            )
        ]
    return []


def _traceback_line(exc: BaseException, entry: Path) -> int | None:
    """The innermost frame that belongs to ``entry``."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if Path(frame.filename).name == entry.name:
            return frame.lineno
    return None
