"""Regression checks for public API coverage and the English Markdown docs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_names_the_public_apis_that_were_easy_to_miss() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    documented = (
        "__version__",
        "default_storage_path()",
        "ProjectConfig",
        "load_config",
        "Scheduler",
        "PermissionManager",
        "HttpFuture",
        "Event",
        "Response",
    )
    missing = [name for name in documented if name not in readme]
    assert not missing, f"README is missing public API documentation for: {missing}"
    assert 'format_date(date(2026, 10, 10), "full", language="en-GB")' in readme


def test_markdown_docs_are_english_and_do_not_reintroduce_deferred_topics() -> None:
    documentation_files = (ROOT / "README.md", ROOT / "CHANGELOG.md")
    for path in documentation_files:
        text = path.read_text(encoding="utf-8")
        non_english = [character for character in text if "\u0400" <= character <= "\u052f"]
        assert not non_english, f"Cyrillic text remains in {path.relative_to(ROOT)}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deferred = (
        "format_datetime",
        "FloatingActionButton",
        "current_platform",
        "is_android",
        "is_desktop",
    )
    assert not [name for name in deferred if name in readme]
    assert not (ROOT / "examples").exists()
    assert "Plant Tracker" not in readme
    assert not (ROOT / "examples" / "plant_tracker").exists()
