
"""Developer conveniences: config schema, tab completion, interactive init, build check."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from pymobile.cli import build_parser, main
from pymobile.cli_completion import complete_line, completion_script
from pymobile.compiler import scaffold
from pymobile.compiler.scaffold import TEMPLATE_DESCRIPTIONS, TEMPLATES, create_project
from pymobile.compiler.schema import SCHEMA_FILENAME, build_config_schema
from pymobile.core.config import _ABI_CHOICES, ProjectConfig

# --- config schema -----------------------------------------------------------


def _app_properties() -> dict:
    return build_config_schema()["properties"]["app"]["properties"]


def test_schema_covers_every_config_field_except_root() -> None:
    expected = {spec.name for spec in fields(ProjectConfig)} - {"root"}
    assert set(_app_properties()) == expected


def test_every_schema_property_has_a_description() -> None:
    missing = [name for name, entry in _app_properties().items() if "description" not in entry]
    assert missing == []


def test_abi_values_come_from_the_config_module() -> None:
    abis = _app_properties()["abis"]["items"]
    assert abis == {"enum": list(_ABI_CHOICES)}


def test_schema_rejects_unknown_app_keys_by_name() -> None:
    assert build_config_schema()["properties"]["app"]["additionalProperties"] is False


def test_init_writes_the_schema_and_points_the_template_at_it(tmp_path: Path) -> None:
    result = create_project(tmp_path / "app", "Demo")
    assert (result.directory / SCHEMA_FILENAME).is_file()
    config = (result.directory / "pymobile.toml").read_text(encoding="utf-8")
    assert config.splitlines()[0] == f"#:schema {SCHEMA_FILENAME}"


# --- tab completion ----------------------------------------------------------


@pytest.fixture(scope="module")
def parser():
    return build_parser()


def test_completes_top_level_commands(parser) -> None:
    assert "build" in complete_line(parser, "pymobile b")
    assert "build" in complete_line(parser, "pymobile ")
    assert "_complete" not in complete_line(parser, "pymobile _")


def test_completes_options_of_a_command(parser) -> None:
    assert "--no-check" in complete_line(parser, "pymobile build --no")
    assert "--list-templates" in complete_line(parser, "pymobile init --l")


def test_completes_choices_of_an_option(parser) -> None:
    assert complete_line(parser, "pymobile init -t ") == sorted(TEMPLATES)
    assert complete_line(parser, "pymobile init -t l") == ["list"]


def test_completes_choices_of_a_positional(parser) -> None:
    assert complete_line(parser, "pymobile completion p") == ["powershell"]


def test_completes_nested_subcommands(parser) -> None:
    assert complete_line(parser, "pymobile widget ") == ["add"]


def test_option_values_do_not_count_as_commands(parser) -> None:
    # "-n" takes a name; the word after it is a value, not a command.
    assert complete_line(parser, "pymobile init -n build ") == []


def test_completion_scripts_call_the_live_command() -> None:
    assert "_complete" in completion_script("bash")
    assert "Register-ArgumentCompleter" in completion_script("powershell")
    with pytest.raises(ValueError):
        completion_script("fish")


# --- templates and interactive init ------------------------------------------


def test_every_template_has_a_description() -> None:
    assert set(TEMPLATE_DESCRIPTIONS) == set(TEMPLATES)
    assert all(text for text in TEMPLATE_DESCRIPTIONS.values())


def test_list_templates_prints_them_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--list-templates"]) == 0
    out = capsys.readouterr().out
    for key in scaffold.TEMPLATES:
        assert key in out


def test_piped_init_does_not_ask_anything(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pymobile.cli_tools._interactive", lambda: False)

    def no_input(_prompt: str = "") -> str:  # pragma: no cover - must not be called
        raise AssertionError("init asked a question without a terminal")

    monkeypatch.setattr("builtins.input", no_input)
    assert main(["init", str(tmp_path / "piped"), "-n", "Piped"]) == 0
    assert "Piped" in (tmp_path / "piped" / "main.py").read_text(encoding="utf-8")


def test_interactive_init_asks_for_name_and_template(tmp_path: Path, monkeypatch) -> None:
    answers = iter(["Asked Name", "2"])
    monkeypatch.setattr("pymobile.cli_tools._interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    target = tmp_path / "asked"
    assert main(["init", str(target)]) == 0
    main_py = (target / "main.py").read_text(encoding="utf-8")
    assert "Asked Name" in main_py
    assert "ListTile" in main_py  # menu choice 2 is the list template


def test_interactive_menu_accepts_a_template_name(tmp_path: Path, monkeypatch) -> None:
    answers = iter(["form"])  # the name came from -n, so only the template is asked
    monkeypatch.setattr("pymobile.cli_tools._interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    target = tmp_path / "named"
    assert main(["init", str(target), "-n", "Named"]) == 0
    assert "TextInput" in (target / "main.py").read_text(encoding="utf-8")


# --- build runs the project check first --------------------------------------


def test_build_stops_on_a_project_error_before_touching_the_sdk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_project(tmp_path, "Broken")
    main_py = tmp_path / "main.py"
    main_py.write_text(main_py.read_text().replace("    Button,\n", "    Buton,\n"))
    assert main(["build", "--native", "-c", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "'Buton' is not part of pymobile" in captured.out
    assert "--no-check" in captured.err
