"""`pymobile init --template` chooses the starter main.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile.compiler.scaffold import TEMPLATES, create_project
from pymobile.errors import ConfigError


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_generates_a_project(tmp_path: Path, template: str) -> None:
    result = create_project(tmp_path / "app", "Demo", template=template)

    main = (result.directory / "main.py").read_text(encoding="utf-8")
    assert "Demo" in main
    assert "{{" not in main  # every placeholder was rendered
    compile(main, "main.py", "exec")  # the starter is valid Python
    assert (result.directory / "pymobile.toml").is_file()
    assert (result.directory / "README.md").is_file()


def test_templates_produce_different_main_files(tmp_path: Path) -> None:
    texts = {
        name: (create_project(tmp_path / name, "Demo", template=name).directory / "main.py")
        .read_text(encoding="utf-8")
        for name in TEMPLATES
    }
    assert len(set(texts.values())) == len(TEMPLATES)


def test_default_template_is_basic(tmp_path: Path) -> None:
    default = create_project(tmp_path / "a", "Demo").directory / "main.py"
    basic = create_project(tmp_path / "b", "Demo", template="basic").directory / "main.py"
    assert default.read_text(encoding="utf-8") == basic.read_text(encoding="utf-8")


def test_unknown_template_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown template"):
        create_project(tmp_path / "app", "Demo", template="nope")
