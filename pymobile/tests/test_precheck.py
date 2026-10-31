"""`pymobile check` reports problems with a file, a line and a hint."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile.compiler.precheck import check_project, format_finding
from pymobile.compiler.scaffold import create_project


@pytest.fixture
def project(tmp_path: Path) -> Path:
    create_project(tmp_path, "Checked")
    return tmp_path


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_a_fresh_project_is_ready(project: Path) -> None:
    assert check_project(project) == []


def test_a_misspelled_pymobile_name_is_found_at_its_import(project: Path) -> None:
    _replace(project / "main.py", "    Button,\n", "    Buton,\n")
    findings = check_project(project)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path.name == "main.py"
    assert finding.line is not None and "Buton" in (project / "main.py").read_text().splitlines()[
        finding.line - 1
    ]
    assert finding.hint == "did you mean 'Button'?"


def test_an_unknown_config_key_points_at_its_line(project: Path) -> None:
    _replace(project / "pymobile.toml", "name = ", "nmae = ")
    findings = check_project(project)
    assert len(findings) == 1
    assert findings[0].path.name == "pymobile.toml"
    assert findings[0].line is not None
    assert findings[0].hint == "did you mean 'name'?"


def test_a_syntax_error_has_line_and_column(project: Path) -> None:
    (project / "main.py").write_text("from pymobile import App\n\ndef main(:\n    pass\n")
    findings = check_project(project)
    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].column is not None


def test_an_error_while_starting_points_at_the_line_in_main_py(project: Path) -> None:
    (project / "main.py").write_text(
        "from pymobile import App, Label, Screen, Widget\n"
        "\n"
        "\n"
        "class Home(Screen):\n"
        "    def build(self) -> Widget:\n"
        "        return Label(undefined_name)\n"
        "\n"
        "\n"
        'App("Checked").run(Home())\n',
        encoding="utf-8",
    )
    findings = check_project(project)
    assert len(findings) == 1
    assert "NameError" in findings[0].message
    assert findings[0].line == 6


def test_invalid_toml_is_reported_with_its_position(project: Path) -> None:
    (project / "pymobile.toml").write_text('[app\nname = "x"\n', encoding="utf-8")
    findings = check_project(project)
    assert len(findings) == 1
    assert findings[0].line == 1


def test_a_missing_config_is_reported(tmp_path: Path) -> None:
    findings = check_project(tmp_path)
    assert len(findings) == 1
    assert "no pymobile.toml found" in findings[0].message


def test_format_shows_location_and_hint(project: Path) -> None:
    _replace(project / "main.py", "    Button,\n", "    Buton,\n")
    text = format_finding(check_project(project)[0], project)
    assert text.startswith("main.py:")
    assert "error:" in text
    assert "\n  hint: did you mean 'Button'?" in text
