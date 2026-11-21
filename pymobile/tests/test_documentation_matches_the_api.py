"""The tutorial and the beginner page must describe the code that exists.

Documentation rots silently: a renamed widget or a moved command leaves a
copy-paste-able snippet that raises for every beginner who tries it. These
tests read the Markdown and check it against the real package and the real CLI,
so a rename breaks CI instead of someone's first hour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PAGES = (DOCS / "TUTORIAL.md", DOCS / "BEGINNER-MISTAKES.md", DOCS / "CONTRIBUTING.md")


def test_every_name_the_tutorial_imports_exists() -> None:
    """A renamed widget must not survive in the tutorial's import lines."""
    import pymobile

    tutorial = (DOCS / "TUTORIAL.md").read_text(encoding="utf-8")
    imported: set[str] = set()
    for block in re.findall(r"from pymobile import \(([^)]*)\)", tutorial):
        imported.update(part.strip() for part in block.split(",") if part.strip())
    for line in re.findall(r"from pymobile import ([^(\n]+)", tutorial):
        imported.update(part.strip() for part in line.split(",") if part.strip())
    assert imported, "no `from pymobile import` line was found in the tutorial"
    missing = sorted(name for name in imported if not hasattr(pymobile, name))
    assert not missing, f"the tutorial imports names that do not exist: {missing}"


def test_the_documented_commands_exist() -> None:
    """Every `python -m pymobile <command>` in the docs is a real command."""
    from pymobile.cli import build_parser

    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    for path in (*PAGES, ROOT / "README.md"):
        text = path.read_text(encoding="utf-8")
        for command in set(re.findall(r"python -m pymobile ([a-z-]+)", text)):
            if command.startswith("--"):  # a global flag, not a subcommand
                continue
            assert command in choices, (
                f"{path.name} documents `pymobile {command}`, which is not a command"
            )


def test_the_docs_do_not_call_methods_that_do_not_exist() -> None:
    """Every ``Name(...).method()`` in the docs must be a method that exists.

    ``Label(self.count).bind()`` read like the Value binding API, but ``bind``
    lives on ``Value``, so the line raised AttributeError for everyone who
    copied it. A constructor call directly followed by a method call is the
    shape that rots when a method moves, and these pages are where a beginner
    copies from.
    """
    import pymobile

    pattern = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)\(([^()]*)\)\.([a-z_][a-z0-9_]*)\(")
    checked: list[str] = []
    for path in (*PAGES, ROOT / "README.md"):
        text = path.read_text(encoding="utf-8")
        # ``Path`` names a pymobile canvas shape and pathlib's class in the
        # same page; a name imported from another module is not ours to check.
        foreign: set[str] = set()
        for line in text.splitlines():
            imported = re.match(r"from\s+([A-Za-z_][\w.]*)\s+import\s+(.+)", line)
            if imported and not imported.group(1).startswith("pymobile"):
                foreign.update(
                    part.strip().split(" as ")[-1].strip() for part in imported.group(2).split(",")
                )
        for cls_name, _, method in pattern.findall(text):
            if cls_name in foreign:
                continue
            cls = getattr(pymobile, cls_name, None)
            if cls is None:
                continue
            checked.append(f"{cls_name}.{method}")
            assert hasattr(cls, method), (
                f"{path.name} calls {cls_name}(...).{method}(), which does not exist"
            )
    assert checked, "no documented method call was found to check"


def test_the_beginner_page_covers_the_mistakes_the_framework_detects() -> None:
    page = (DOCS / "BEGINNER-MISTAKES.md").read_text(encoding="utf-8")
    for name in ("super().__init__()", "on_press=self.save()", "refresh()", "WidgetTypeError"):
        assert name in page
    assert "save_secret" in page


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_relative_links_resolve(page: Path) -> None:
    """A link inside these pages must point at a file that is really there."""
    text = page.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:|#)([^)#]+)", text):
        if target.startswith("../.."):  # a link to the repository on GitHub
            continue
        resolved = (page.parent / target).resolve()
        assert resolved.exists(), f"{page.name} links to {target}, which does not exist"


def test_the_repository_keeps_no_examples_tree() -> None:
    """Examples live in the tutorial, not in a second copy of the code.

    The project used to carry an `examples/` folder; it drifted out of date and
    was removed once. Restoring it means maintaining a second copy of every
    snippet that the tests above cannot check for staleness.
    """
    assert not (ROOT / "examples").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "examples/" not in readme
