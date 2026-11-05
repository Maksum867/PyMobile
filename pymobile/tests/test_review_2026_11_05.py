"""Regression tests for the 2026-11-05 review findings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pymobile import HttpClient, PyMobileError, ResourceError, Storage
from pymobile.compiler.precheck import check_project

ROOT = Path(__file__).resolve().parents[2]

requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions, so an unreadable file cannot be simulated",
)


# a read error is retryable; schema failures stay sticky.
@requires_non_root
def test_read_error_is_retryable_once_the_file_is_readable(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"keep": "me"}', encoding="utf-8")
    path.chmod(0)
    try:
        with pytest.raises(ResourceError):
            Storage(path)  # eager load: fails here
    finally:
        path.chmod(0o600)
    assert json.loads(path.read_text(encoding="utf-8")) == {"keep": "me"}
    assert Storage(path)["keep"] == "me"


@requires_non_root
def test_read_error_does_not_replace_the_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"keep": "me"}', encoding="utf-8")
    path.chmod(0)
    try:
        with pytest.raises(ResourceError):
            Storage(path)
        raw = path.stat()
        assert raw.st_size == len('{"keep": "me"}')
    finally:
        path.chmod(0o600)


def test_newer_schema_fails_in_the_constructor(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"__schema_version__": 9, "name": "Ann"}), encoding="utf-8")
    with pytest.raises(PyMobileError, match="9"):
        Storage(path, schema_version=2, migrations={2: lambda d: d})
    # and the file is untouched
    assert json.loads(path.read_text(encoding="utf-8"))["__schema_version__"] == 9


def test_failing_migration_stays_sticky(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"__schema_version__": 1}), encoding="utf-8")

    def boom(data):
        raise RuntimeError("bad migration")

    with pytest.raises(RuntimeError):
        Storage(path, schema_version=2, migrations={2: boom})
    # a later open of the same store is refused too, not silently served
    with pytest.raises(RuntimeError, match="bad migration"):
        Storage(path, schema_version=2, migrations={2: lambda d: d})
    assert json.loads(path.read_text(encoding="utf-8")) == {"__schema_version__": 1}


# options that belong to HttpClient(...) are named as such.
@pytest.mark.parametrize("call", [
    lambda c: c.get("/x", retries=2),
    lambda c: c.post("/x", retries=2),
    lambda c: c.get_async("/x", backoff=1.0),
])
def test_client_option_on_a_request_names_the_constructor(call):
    client = HttpClient(base_url="http://127.0.0.1:9")
    with pytest.raises(TypeError, match=r"HttpClient\(\w+=\.\.\.\)"):
        call(client)


def test_unknown_request_option_lists_the_accepted_ones():
    client = HttpClient(base_url="http://127.0.0.1:9")
    with pytest.raises(TypeError, match="accepted options are"):
        client.get("/x", bogus=1)


# check reports an icon that build would refuse.
def _project(root: Path, icon_line: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text(
        "from pymobile import App, Label, Screen, Widget\n\n"
        "class Home(Screen):\n"
        "    def build(self) -> Widget:\n"
        "        return Label('hi')\n\n"
        "if __name__ == '__main__':\n"
        "    App('Demo').run(Home())\n",
        encoding="utf-8",
    )
    (root / "pymobile.toml").write_text(
        "[app]\n"
        'name = "Demo"\n'
        'package = "org.example.demo"\n'
        'version = "0.1.0"\n'
        "version_code = 1\n"
        'entrypoint = "main.py"\n'
        f"{icon_line}\n",
        encoding="utf-8",
    )
    return root


def test_check_reports_a_missing_icon(tmp_path):
    root = _project(tmp_path / "app", 'icon = "assets/missing.png"')
    findings = check_project(root, run_app=False)
    assert any("Icon not found" in f.message for f in findings), findings
    assert any(f.line is not None for f in findings), "finding should point at the icon line"


def test_check_accepts_a_project_without_icon(tmp_path):
    root = _project(tmp_path / "app", "")
    assert [f for f in check_project(root, run_app=False) if "icon" in f.message.lower()] == []


# the starter form names its text field for screen readers.
def test_starter_form_template_has_an_accessible_name_for_its_field():
    template = (ROOT / "pymobile" / "resources" / "templates" / "main_form.py.template").read_text(
        encoding="utf-8"
    )
    assert 'accessibility_label="Your name"' in template
