"""`pymobile run` names the file and line of a mistake in the app's code.

A plain `NameError` used to reach the CLI catch-all and print "unexpected
error: …" with a hint to re-run with --verbose, while `pymobile check` already
reported `main.py:5`. The same information belongs in the same place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import cli
from pymobile.core.config import ProjectConfig
from pymobile.errors import PyMobileError


@pytest.fixture
def project(tmp_path: Path) -> tuple[ProjectConfig, Path]:
    entry = tmp_path / "main.py"
    config = ProjectConfig(name="Demo", package="com.example.demo", root=tmp_path)
    return config, entry


class TestUserErrors:
    def test_a_name_error_names_the_file_and_the_line(
        self, project: tuple[ProjectConfig, Path]
    ) -> None:
        config, entry = project
        # The mistake is at import time: `build()` would only run once the app
        # starts, and the CLI reports that through the same path.
        entry.write_text(
            "from pymobile import Label\n\n\nlabel = Label(undefined_name)\n",
            encoding="utf-8",
        )
        with pytest.raises(PyMobileError) as caught:
            cli._execute(config, entry)
        message = str(caught.value)
        assert "main.py:4" in message
        assert "undefined_name" in message
        assert caught.value.hint and "not defined" in caught.value.hint

    def test_a_syntax_error_reports_its_own_position(
        self, project: tuple[ProjectConfig, Path]
    ) -> None:
        config, entry = project
        entry.write_text("def broken(:\n    pass\n", encoding="utf-8")
        with pytest.raises(PyMobileError) as caught:
            cli._execute(config, entry)
        assert "main.py:1" in str(caught.value)

    def test_a_missing_super_init_is_explained(self, project: tuple[ProjectConfig, Path]) -> None:
        config, entry = project
        entry.write_text(
            "from pymobile import Label, Screen\n"
            "\n"
            "\n"
            "class Home(Screen):\n"
            "    def __init__(self):\n"
            "        self.title = 'no super'\n"
            "\n"
            "    def build(self):\n"
            "        return Label(self._root)\n"
            "\n"
            "\n"
            "Home().build()\n",
            encoding="utf-8",
        )
        with pytest.raises(PyMobileError) as caught:
            cli._execute(config, entry)
        assert "super().__init__()" in (caught.value.hint or "")

    def test_a_framework_error_is_not_rewrapped(self, project: tuple[ProjectConfig, Path]) -> None:
        from pymobile.errors import ResourceError

        config, entry = project
        entry.write_text(
            "from pymobile.errors import ResourceError\n"
            "raise ResourceError('the entry point said no')\n",
            encoding="utf-8",
        )
        with pytest.raises(ResourceError, match="the entry point said no"):
            cli._execute(config, entry)

    def test_the_location_points_at_the_app_not_the_framework(self) -> None:
        """A failure deep inside pymobile still names the app's own line."""
        from pymobile.compiler.precheck import format_finding  # noqa: F401  (import kept honest)

        entry = Path("/app/main.py")
        try:
            exec(compile("label = Label(1, 2, 3)\n", str(entry), "exec"), {"Label": int})
        except Exception as exc:
            assert cli._user_code_location(exc, entry) == "main.py:1"
        else:  # pragma: no cover - int() raises
            pytest.fail("the snippet was expected to raise")
