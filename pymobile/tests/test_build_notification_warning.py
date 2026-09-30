"""The POST_NOTIFICATIONS build warning fires only for apps that post notifications."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile.compiler.collector import collect_sources
from pymobile.compiler.pipeline import BuildPipeline, find_notification_use
from pymobile.compiler.scaffold import create_project
from pymobile.core.config import ProjectConfig, load_config

PERMISSION = "android.permission.POST_NOTIFICATIONS"


def warnings_for(root: Path, **config: object) -> list[str]:
    settings: dict[str, object] = {"root": root, "target_sdk": 35}
    settings.update(config)
    result = BuildPipeline(ProjectConfig(**settings), use_cache=False).run()  # type: ignore[arg-type]
    return [w for w in result.warnings if "POST_NOTIFICATIONS" in w]


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write(tmp_path, "main.py", "x = 1\n")
    return tmp_path


def test_an_app_without_notification_code_gets_no_warning(root: Path) -> None:
    assert warnings_for(root) == []


@pytest.mark.parametrize(
    "call",
    [
        "app.notify('Done')",
        "self.app.notify('Done', 'body')",
        "app.notifications.notify('Done')",
        "App.current().notify('Done')",
        "get_bridge().notify(spec)",
        "self.my_app.notify('Done')",
        "Notifications(bridge)",
        "NotificationSpec(title='x')",
    ],
)
def test_each_way_of_posting_a_notification_triggers_the_warning(root: Path, call: str) -> None:
    write(root, "main.py", f"def go(self, app, spec, bridge):\n    {call}\n")
    messages = warnings_for(root)
    assert len(messages) == 1
    assert "main.py:2" in messages[0]
    assert "stay hidden" in messages[0]
    assert PERMISSION in messages[0]


def test_the_warning_names_the_file_that_posts(root: Path) -> None:
    write(
        root,
        "screens/alerts.py",
        "\n\nclass A:\n    def go(self):\n        self.app.notify('x')\n",
    )
    (message,) = warnings_for(root)
    assert "screens/alerts.py:5" in message


@pytest.mark.parametrize(
    "code",
    [
        "cond.notify()",
        "self._condition.notify_all()",
        "observer.notify(event)",
        "signal.notify('x')",
    ],
)
def test_other_notify_methods_do_not_count(root: Path, code: str) -> None:
    write(root, "main.py", f"def go(cond, observer, signal, event):\n    {code}\n")
    assert warnings_for(root) == []


def test_comments_docstrings_and_strings_do_not_count(root: Path) -> None:
    write(
        root,
        "main.py",
        '"""Call app.notify("x") when the timer ends (not implemented)."""\n'
        "# self.app.notify('later')\n"
        "HELP = 'use app.notify(...)'\n"
        "from pymobile import Notifications\n",  # an import alone is not a use
    )
    assert warnings_for(root) == []


def test_a_declared_permission_silences_it(root: Path) -> None:
    write(root, "main.py", "def go(app):\n    app.notify('x')\n")
    assert warnings_for(root, permissions=["android.permission.INTERNET", PERMISSION]) == []


def test_an_older_target_sdk_needs_no_permission(root: Path) -> None:
    write(root, "main.py", "def go(app):\n    app.notify('x')\n")
    assert warnings_for(root, target_sdk=32) == []
    assert len(warnings_for(root, target_sdk=33)) == 1


def test_virtualenvs_and_tests_are_not_scanned(root: Path) -> None:
    """They never ship, so what they call is not the app's business."""
    write(root, ".venv/lib/site.py", "def go(app):\n    app.notify('x')\n")
    write(root, "tests/test_alerts.py", "def test(app):\n    app.notify('x')\n")
    assert warnings_for(root) == []


def test_a_project_exclude_pattern_removes_a_file_from_the_scan(root: Path) -> None:
    write(root, "drafts/old.py", "def go(app):\n    app.notify('x')\n")
    assert len(warnings_for(root)) == 1
    assert warnings_for(root, exclude=["drafts/**"]) == []


def test_a_file_that_does_not_parse_falls_back_to_a_text_search(root: Path) -> None:
    write(root, "main.py", "def broken(:\n    app.notify('x')\n")
    (message,) = warnings_for(root)
    assert "main.py:2" in message


def test_a_file_that_does_not_parse_and_never_notifies_is_quiet(root: Path) -> None:
    write(root, "main.py", "def broken(:\n    pass\n")
    assert warnings_for(root) == []


BOM = b"\xef\xbb\xbf"  # what Notepad and Windows PowerShell put in front of a UTF-8 file


def test_a_file_with_a_utf8_bom_is_read_like_any_other(root: Path) -> None:
    (root / "main.py").write_bytes(BOM + b"def go(app):\n    app.notify('x')\n")
    (message,) = warnings_for(root)
    assert "main.py:2" in message


def test_a_bom_does_not_push_a_file_onto_the_text_search(root: Path) -> None:
    """Parsed, the comment is only a comment; searched as text it would look like a call."""
    (root / "main.py").write_bytes(BOM + b"# self.app.notify('later')\nx = 1\n")
    assert warnings_for(root) == []


def test_a_file_in_another_encoding_is_read_through_its_coding_line(root: Path) -> None:
    source = "# -*- coding: cp1251 -*-\n# Привіт\ndef go(app):\n    app.notify('Готово')\n"
    (root / "main.py").write_bytes(source.encode("cp1251"))
    (message,) = warnings_for(root)
    assert "main.py:4" in message


def test_requesting_the_permission_in_code_is_reported_once(root: Path) -> None:
    write(
        root,
        "main.py",
        "def go(app):\n    app.permissions.require(Permission.POST_NOTIFICATIONS)\n"
        "    app.notify('x')\n",
    )
    result = BuildPipeline(ProjectConfig(root=root, target_sdk=35), use_cache=False).run()
    assert sum("POST_NOTIFICATIONS" in warning for warning in result.warnings) == 1


def test_the_starter_project_posts_and_declares_so_it_is_quiet(tmp_path: Path) -> None:
    project = tmp_path / "starter"
    create_project(project, "Starter")
    config = load_config(project)
    assert PERMISSION in config.permissions
    sources = collect_sources(config.source_path, config.entrypoint_path, exclude=config.exclude)
    assert find_notification_use(sources) is not None  # the starter really does post one
    result = BuildPipeline(config, use_cache=False).run()
    assert not [w for w in result.warnings if "POST_NOTIFICATIONS" in w]
