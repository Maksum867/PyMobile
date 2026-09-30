"""``pymobile.testing``: the session, the snapshot checker and the pytest plugin."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import pymobile
from pymobile import App, Button, Column, Label, Screen
from pymobile.core.bridge import StubBridge, get_bridge
from pymobile.core.i18n import translations
from pymobile.testing import (
    Driver,
    SnapshotChecker,
    app_session,
    assert_snapshot,
    parse_steps,
    render_ascii,
)
from pymobile.testing.snapshots import env_flag, running_in_ci, snapshot_name

#: What a child Python needs so that its output is UTF-8 whatever the OS locale is
#: (cp1251/cp1252 on Windows), and how this file reads that output back.
UTF8_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
UTF8_TEXT = {"encoding": "utf-8", "errors": "replace"}

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

REPO = Path(pymobile.__file__).resolve().parents[1]


class Home(Screen):
    def build(self) -> Column:
        return Column(Label("Home", id="title"), Button("Go", id="go", on_press=self.go))

    def go(self) -> None:
        self.app.push(Other())


class Other(Screen):
    def build(self) -> Label:
        return Label("Other", id="other")


# --------------------------------------------------------------------------
# app_session
# --------------------------------------------------------------------------
class TestAppSession:
    def test_it_wires_an_app_a_stub_bridge_and_a_driver(self, tmp_path: Path) -> None:
        with app_session(tmp_path) as session:
            assert isinstance(session.bridge, StubBridge)
            assert session.app.bridge is session.bridge
            assert get_bridge() is session.bridge
            assert session.driver.app is session.app
            assert session.app.running is False

    def test_the_store_lives_in_the_given_directory(self, tmp_path: Path) -> None:
        with app_session(tmp_path) as session:
            session.app.storage.set("k", "v")
            assert session.app.storage.path.parent == tmp_path
        assert (tmp_path / "pymobile-test-store.json").exists()

    def test_start_runs_the_app_and_drives_it(self, tmp_path: Path) -> None:
        with app_session(tmp_path) as session:
            home = session.start(Home())
            assert session.app.running and session.screen is home
            session.driver.press("go")
            assert isinstance(session.screen, Other)
            assert session.bridge.last_tree["props"]["text"] == "Other"

    def test_it_cleans_up_after_itself(self, tmp_path: Path) -> None:
        previous_language = translations.language
        with app_session(tmp_path) as session:
            session.start(Home())
            translations.use("uk")
            assert App.current() is session.app
        assert session.app.running is False
        assert App.current() is None
        assert translations.language == previous_language
        assert get_bridge() is not session.bridge

    def test_it_cleans_up_when_the_test_body_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="boom"), app_session(tmp_path) as session:
            session.start(Home())
            raise RuntimeError("boom")
        assert App.current() is None

    def test_app_options_reach_the_app(self, tmp_path: Path) -> None:
        with app_session(tmp_path, name="Notes", theme="dark", package="com.example.notes") as s:
            assert s.app.name == "Notes"
            assert s.app.theme.is_dark
            assert s.app.package == "com.example.notes"

    def test_a_driver_can_attach_to_an_app_the_code_created_itself(self, tmp_path: Path) -> None:
        with app_session(tmp_path) as session:

            def main() -> None:  # what an app's entry point does
                session.app.run(Home())

            main()
            driver = Driver.attach()
            assert driver.app is session.app
            driver.press("go")
            assert isinstance(driver.screen, Other)

    def test_attach_without_a_running_app_explains(self) -> None:
        with pytest.raises(pymobile.PyMobileError, match="no PyMobile app is running"):
            Driver.attach()


# --------------------------------------------------------------------------
# SnapshotChecker
# --------------------------------------------------------------------------
class TestSnapshotChecker:
    def checker(self, tmp_path: Path, **options: object) -> SnapshotChecker:
        return SnapshotChecker(tmp_path / "test_sample.py", "test_home", **options)  # type: ignore[arg-type]

    def test_a_missing_snapshot_is_written_with_a_warning(self, tmp_path: Path) -> None:
        with pytest.warns(UserWarning, match="did not exist and has been written"):
            text = self.checker(tmp_path, ci=False)(Home())
        golden = tmp_path / "snapshots" / "test_sample__test_home.txt"
        assert golden.read_text(encoding="utf-8") == text
        assert "Home" in text

    def test_a_matching_snapshot_passes_quietly(self, tmp_path: Path, recwarn: object) -> None:
        with pytest.warns(UserWarning):
            self.checker(tmp_path, ci=False)(Home())
        self.checker(tmp_path, ci=False)(Home())  # no warning this time (pytest would error)

    def test_a_changed_screen_fails_with_a_diff_and_the_update_hint(self, tmp_path: Path) -> None:
        with pytest.warns(UserWarning):
            self.checker(tmp_path, ci=False)(Label("v1", id="x"))
        with pytest.raises(AssertionError) as caught:
            self.checker(tmp_path, ci=False)(Label("v2", id="x"))
        message = str(caught.value)
        assert "-v1" in message and "+v2" in message
        assert "--pymobile-update-snapshots" in message
        assert "PYMOBILE_UPDATE_SNAPSHOTS=1" in message

    def test_update_rewrites_the_golden_file(self, tmp_path: Path) -> None:
        with pytest.warns(UserWarning):
            self.checker(tmp_path, ci=False)(Label("v1", id="x"))
        self.checker(tmp_path, update=True)(Label("v2", id="x"))
        self.checker(tmp_path)(Label("v2", id="x"))  # now the golden text

    def test_ci_does_not_invent_missing_snapshots(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError, match="CI does not create them"):
            self.checker(tmp_path, ci=True)(Home())
        assert not (tmp_path / "snapshots").exists()

    def test_ci_still_compares_existing_ones_and_update_still_creates(self, tmp_path: Path) -> None:
        self.checker(tmp_path, ci=True, update=True)(Home())
        self.checker(tmp_path, ci=True)(Home())
        with pytest.raises(AssertionError, match="changed"):
            self.checker(tmp_path, ci=True)(Label("other", id="x"))

    def test_unnamed_checks_in_one_test_get_numbered_files(self, tmp_path: Path) -> None:
        check = self.checker(tmp_path, update=True)
        check(Label("a", id="x"))
        check(Label("b", id="x"))
        check(Label("c", id="x"))
        names = sorted(p.name for p in (tmp_path / "snapshots").iterdir())
        assert names == [
            "test_sample__test_home.txt",
            "test_sample__test_home_2.txt",
            "test_sample__test_home_3.txt",
        ]

    def test_a_name_picks_the_file(self, tmp_path: Path) -> None:
        self.checker(tmp_path, update=True)(Label("a", id="x"), "the home page")
        assert (tmp_path / "snapshots" / "test_sample__the_home_page.txt").exists()

    def test_the_target_may_be_a_screen_a_widget_a_tree_or_the_last_frame(
        self, tmp_path: Path
    ) -> None:
        bridge = StubBridge(verbose=False)
        with app_session(tmp_path) as session:
            session.start(Home())
            bridge = session.bridge
        check = SnapshotChecker(tmp_path / "t.py", "t", bridge, update=True)
        texts = {
            check(),  # the last frame the app drew
            check(Home()),  # a screen
            check(Home().root),  # a widget
            check(Home().to_dict()),  # a serialised tree
        }
        assert len(texts) <= 2 and all("Home" in text for text in texts)

    def test_nothing_rendered_yet_is_explained(self, tmp_path: Path) -> None:
        check = SnapshotChecker(tmp_path / "t.py", "t", StubBridge(verbose=False), update=True)
        with pytest.raises(AssertionError, match="nothing has been rendered yet"):
            check()

    def test_ids_and_title_are_passed_on(self, tmp_path: Path) -> None:
        text = self.checker(tmp_path, update=True)(Home(), ids=True, title="App")
        assert "(title)" in text and "App" in text
        assert text == render_ascii(Home(), show_ids=True, title="App")

    def test_it_matches_the_older_helper(self, tmp_path: Path) -> None:
        """Same files as ``assert_snapshot``: ``snapshots/<module>__<name>.txt``."""
        module = tmp_path / "test_sample.py"
        SnapshotChecker(module, "x", update=True)(Home(), "page")
        assert_snapshot(Home(), str(module), "page")  # accepts the file the checker wrote

    @pytest.mark.parametrize(
        ("raw", "safe"),
        [
            ("test_menu", "test_menu"),
            ("test_menu[dark-mode]", "test_menu_dark-mode"),
            ("test_x[a b/c]", "test_x_a_b_c"),
            ("[]", "screen"),
        ],
    )
    def test_names_are_made_file_safe(self, raw: str, safe: str) -> None:
        assert snapshot_name(raw) == safe


class TestEnvironment:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
    def test_flags_that_are_on(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("PYMOBILE_X", value)
        assert env_flag("PYMOBILE_X")

    @pytest.mark.parametrize("value", ["", "0", "false", "No", "OFF"])
    def test_flags_that_are_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("PYMOBILE_X", value)
        assert not env_flag("PYMOBILE_X")

    def test_an_unset_flag_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYMOBILE_X", raising=False)
        assert not env_flag("PYMOBILE_X")

    def test_ci_is_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        assert running_in_ci()
        monkeypatch.delenv("CI")
        assert not running_in_ci()


def test_the_package_root_needs_no_pytest() -> None:
    """``import pymobile.testing`` must be safe inside an application."""
    code = (
        "import sys; sys.modules['pytest'] = None; "
        "import pymobile.testing as t; assert t.app_session"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        **UTF8_TEXT,
        env={**os.environ, **UTF8_ENV, "PYTHONPATH": str(REPO)},
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_parse_steps_is_reexported() -> None:
    assert parse_steps("a")[0].target == "a"


# --------------------------------------------------------------------------
# the plugin, in real pytest runs
# --------------------------------------------------------------------------
SAMPLE = textwrap.dedent(
    """
    from pymobile import App, Button, Column, Label, Screen, translations

    class Menu(Screen):
        def build(self):
            return Column(
                Label("Main menu", id="h"),
                Button("Start", id="start", on_press=lambda: self.app.push(Quiz())),
            )

    class Quiz(Screen):
        def build(self):
            return Label("Question", id="q")

    def test_the_fixtures_fit_together(
        pymobile_session, pymobile_app, pymobile_bridge, pymobile_driver, tmp_path
    ):
        assert pymobile_session.app is pymobile_app
        assert pymobile_session.bridge is pymobile_bridge
        assert pymobile_session.driver is pymobile_driver
        assert pymobile_app.storage.path.parent == tmp_path
        pymobile_session.start(Menu())
        pymobile_driver.navigate("Menu.start")
        assert isinstance(pymobile_driver.screen, Quiz)

    def test_first_leaves_state_behind(pymobile_session):
        pymobile_session.start(Menu())
        translations.use("uk")

    def test_second_starts_clean(pymobile_session):
        assert App.current() is None
        assert translations.language == "en"

    def test_the_menu_looks_right(pymobile_session, pymobile_snapshot):
        pymobile_session.start(Menu())
        pymobile_snapshot()
    """
)


def run_pytest(
    directory: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("CI", "PYMOBILE_UPDATE_SNAPSHOTS")}
    base["PYTHONPATH"] = str(REPO)
    base["PYTEST_ADDOPTS"] = ""
    base.update(UTF8_ENV)
    base.update(env or {})
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "pymobile.testing.plugin",
            "-W",
            "ignore::DeprecationWarning",
            *args,
        ],
        cwd=directory,
        capture_output=True,
        **UTF8_TEXT,
        env=base,
        check=False,
        timeout=120,
    )


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    (tmp_path / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def golden(directory: Path) -> Path:
    return directory / "snapshots" / "test_sample__test_the_menu_looks_right.txt"


class TestPlugin:
    def test_the_fixtures_exist_and_isolate_tests(self, sample: Path) -> None:
        result = run_pytest(sample, "-k", "not looks_right")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "3 passed" in result.stdout

    def test_it_is_active_without_any_flag_once_installed(self, sample: Path) -> None:
        """The entry point registers it; -p is only how these tests do not depend on that."""
        from importlib.metadata import entry_points

        names = {entry.name for entry in entry_points(group="pytest11")}
        if "pymobile.testing.plugin" not in names:
            pytest.skip("pymobile is not installed with its entry points (pip install -e .)")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-k", "fit_together"],
            cwd=sample,
            capture_output=True,
            **UTF8_TEXT,
            env={**os.environ, **UTF8_ENV, "PYTHONPATH": str(REPO), "PYTEST_ADDOPTS": ""},
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_first_run_writes_the_snapshot_and_says_so(self, sample: Path) -> None:
        result = run_pytest(sample, "-k", "looks_right")
        assert result.returncode == 0, result.stdout + result.stderr
        assert golden(sample).exists()
        assert "did not exist and has been written" in result.stdout
        assert "Main menu" in golden(sample).read_text(encoding="utf-8")

    def test_the_second_run_compares_quietly(self, sample: Path) -> None:
        run_pytest(sample, "-k", "looks_right")
        result = run_pytest(sample, "-k", "looks_right")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "did not exist" not in result.stdout

    def test_a_change_fails_and_the_flag_accepts_it(self, sample: Path) -> None:
        run_pytest(sample, "-k", "looks_right")
        changed = SAMPLE.replace("Main menu", "Home")
        (sample / "test_sample.py").write_text(changed, encoding="utf-8")

        failed = run_pytest(sample, "-k", "looks_right")
        assert failed.returncode == 1
        assert "-Main menu" in failed.stdout and "+Home" in failed.stdout
        assert "--pymobile-update-snapshots" in failed.stdout

        accepted = run_pytest(sample, "-k", "looks_right", "--pymobile-update-snapshots")
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        assert "Home" in golden(sample).read_text(encoding="utf-8")
        assert run_pytest(sample, "-k", "looks_right").returncode == 0

    def test_the_environment_variable_updates_too(self, sample: Path) -> None:
        run_pytest(sample, "-k", "looks_right")
        changed = SAMPLE.replace("Main menu", "Hello")
        (sample / "test_sample.py").write_text(changed, encoding="utf-8")
        result = run_pytest(sample, "-k", "looks_right", env={"PYMOBILE_UPDATE_SNAPSHOTS": "1"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Hello" in golden(sample).read_text(encoding="utf-8")

    def test_ci_refuses_to_create_a_snapshot(self, sample: Path) -> None:
        result = run_pytest(sample, "-k", "looks_right", env={"CI": "true"})
        assert result.returncode == 1
        assert "CI does not create them" in result.stdout
        assert not golden(sample).exists()

    def test_ci_accepts_a_committed_snapshot(self, sample: Path) -> None:
        run_pytest(sample, "-k", "looks_right")
        assert run_pytest(sample, "-k", "looks_right", env={"CI": "true"}).returncode == 0

    def test_the_option_is_documented_in_help(self, sample: Path) -> None:
        result = run_pytest(sample, "--help")
        assert "--pymobile-update-snapshots" in result.stdout


def test_the_entry_point_is_declared_in_pyproject() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    points = data["project"]["entry-points"]["pytest11"]
    assert points == {"pymobile.testing.plugin": "pymobile.testing.plugin"}


def test_the_testing_package_ships_but_the_test_suite_does_not() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    find = data["tool"]["setuptools"]["packages"]["find"]
    assert find["include"] == ["pymobile*"]
    assert "pymobile.tests*" in find["exclude"]
    assert not any(pattern.startswith("pymobile.testing") for pattern in find["exclude"])
