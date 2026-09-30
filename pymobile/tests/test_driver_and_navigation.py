"""Driving an app like a user: ``Driver``, and ``preview --screen`` / ``--navigate``."""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from pymobile import (
    App,
    Button,
    Column,
    Dropdown,
    Label,
    Screen,
    Slider,
    Switch,
    TextInput,
)
from pymobile.cli import main
from pymobile.core.bridge import StubBridge
from pymobile.core.driver import (
    Driver,
    Step,
    find_screen_class,
    parse_steps,
    screen_classes,
)
from pymobile.errors import PyMobileError, WidgetNotFoundError


# --------------------------------------------------------------------------
# a small app: Menu -> Quiz -> Result
# --------------------------------------------------------------------------
class Menu(Screen):
    def build(self) -> Column:
        return Column(
            Label("Menu", id="heading"),
            Button("Start", id="start", on_press=lambda: self.app.push(Quiz())),
            Button("Off", id="off", enabled=False),
        )


class Quiz(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.answer = TextInput(id="answer")
        self.difficulty = Dropdown(["easy", "hard"], id="difficulty")
        self.sound = Switch(id="sound")
        self.volume = Slider(id="volume")

    def build(self) -> Column:
        return Column(
            self.answer,
            self.difficulty,
            self.sound,
            self.volume,
            Button("Next", id="next", on_press=self.finish),
        )

    def finish(self) -> None:
        self.app.push(Result(score=int(self.answer.value or 0)))


class Result(Screen):
    def __init__(self, score: int = 0, total: int = 10) -> None:
        super().__init__()
        self.score = score
        self.total = total

    def build(self) -> Label:
        return Label(f"{self.score}/{self.total}", id="score")


SCREENS = [Menu, Quiz, Result]


@pytest.fixture
def driver(bridge: StubBridge, tmp_path: Path) -> Iterator[Driver]:
    app = App("Quiz", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    app.run(Menu())
    yield Driver(app, screens=SCREENS)
    app.stop()


# --------------------------------------------------------------------------
# finding screens
# --------------------------------------------------------------------------
class TestFindScreens:
    def test_a_screen_is_found_by_simple_and_qualified_name(self) -> None:
        assert find_screen_class("Quiz", SCREENS) is Quiz
        qualified = f"{Quiz.__module__}.{Quiz.__qualname__}"
        assert find_screen_class(qualified, SCREENS) is Quiz

    def test_an_unknown_name_lists_the_screens_and_suggests_one(self) -> None:
        with pytest.raises(PyMobileError) as caught:
            find_screen_class("Resulz", SCREENS)
        assert "Did you mean 'Result'" in str(caught.value)
        assert caught.value.hint is not None
        assert "Menu, Quiz, Result" in caught.value.hint

    def test_two_screens_with_one_name_ask_for_the_module(self) -> None:
        class Menu(Screen):
            pass

        with pytest.raises(PyMobileError, match="more than one screen is called 'Menu'"):
            find_screen_class("Menu", [globals()["Menu"], Menu])

    def test_screen_classes_finds_subclasses_and_hides_the_frameworks(self) -> None:
        class Mine(Screen):
            pass

        Mine.__module__ = "myapp.screens"
        assert Mine in screen_classes()
        assert Mine in screen_classes(project_only=False)
        assert Menu not in screen_classes()  # a class of the pymobile.tests package
        assert Menu in screen_classes(project_only=False)

    def test_the_newest_definition_of_a_reloaded_class_wins(self) -> None:
        def define() -> type[Screen]:
            class Reloaded(Screen):
                pass

            Reloaded.__module__ = "__main__"
            Reloaded.__qualname__ = "Reloaded"
            return Reloaded

        older, newer = define(), define()
        assert find_screen_class("Reloaded") is newer
        assert older is not newer


# --------------------------------------------------------------------------
# parsing routes
# --------------------------------------------------------------------------
class TestParseSteps:
    def test_press_type_and_back(self) -> None:
        steps = parse_steps("Menu.start, Quiz.answer=42, <back>, next", {"Menu", "Quiz"})
        assert steps == [
            Step("Menu.start", "start", "Menu", None),
            Step("Quiz.answer=42", "answer", "Quiz", "42"),
            Step("<back>", "<back>"),
            Step("next", "next", None, None),
        ]

    def test_a_dot_belongs_to_the_widget_unless_it_names_a_screen(self) -> None:
        (step,) = parse_steps("row.1", {"Menu"})
        assert (step.screen, step.target) == (None, "row.1")
        (step,) = parse_steps("Menu.row.1", {"Menu"})
        assert (step.screen, step.target) == ("Menu", "row.1")

    def test_a_list_of_steps_and_newlines(self) -> None:
        assert [s.target for s in parse_steps(["a", "b"])] == ["a", "b"]
        assert [s.target for s in parse_steps("a,\n b\n<back>")] == ["a", "b", "<back>"]

    def test_an_empty_route_is_an_error(self) -> None:
        with pytest.raises(PyMobileError, match="no steps"):
            parse_steps(" , ")
        with pytest.raises(PyMobileError, match="empty step"):
            parse_steps("=3")


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------
class TestOpen:
    def test_open_by_name_with_constructor_arguments(self, driver: Driver) -> None:
        screen = driver.open("Result", score=7)
        assert isinstance(screen, Result)
        assert driver.screen is screen
        assert driver.find("score").text == "7/10"  # type: ignore[attr-defined]

    def test_open_by_class_and_by_instance(self, driver: Driver) -> None:
        assert isinstance(driver.open(Quiz), Quiz)
        instance = Result(total=3)
        assert driver.open(instance) is instance

    def test_arguments_with_an_instance_are_refused(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError, match="only apply when the screen is opened by class"):
            driver.open(Result(), score=1)

    def test_constructor_arguments_that_do_not_fit_get_a_hint(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError) as caught:
            driver.open("Result", nope=1)
        assert "cannot create Result" in str(caught.value)
        assert caught.value.hint is not None
        assert "--args" in caught.value.hint

    def test_opening_needs_a_running_app(self, bridge: StubBridge, tmp_path: Path) -> None:
        idle = Driver(App("x", bridge=bridge, storage_path=str(tmp_path / "s.json")))
        with pytest.raises(PyMobileError, match="no current screen"):
            idle.screen
        with pytest.raises(PyMobileError, match="not running"):
            idle.open(Result())


class TestInteract:
    def test_press_runs_the_handler(self, driver: Driver) -> None:
        driver.press("start")
        assert isinstance(driver.screen, Quiz)

    def test_an_unknown_id_names_the_closest_ones(self, driver: Driver) -> None:
        with pytest.raises(WidgetNotFoundError) as caught:
            driver.press("strt")
        assert caught.value.hint == "Did you mean 'start'?"

    def test_a_label_cannot_be_pressed(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError, match="'heading' is a Label"):
            driver.press("heading")

    def test_a_disabled_widget_cannot_be_pressed(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError, match="disabled"):
            driver.press("off")

    def test_change_types_picks_and_sets(self, driver: Driver) -> None:
        driver.press("start")
        quiz = driver.screen
        assert isinstance(quiz, Quiz)
        driver.change("answer", "Ann")
        driver.change("difficulty", "hard")
        driver.change("volume", 30)
        assert (quiz.answer.value, quiz.difficulty.value, quiz.volume.value) == ("Ann", "hard", 30)

    @pytest.mark.parametrize(("value", "expected"), [(True, True), ("on", True), ("off", False)])
    def test_change_switches_a_switch(self, driver: Driver, value: object, expected: bool) -> None:
        driver.press("start")
        quiz = driver.screen
        assert isinstance(quiz, Quiz)
        quiz.sound.checked = not expected
        driver.change("sound", value)
        assert quiz.sound.checked is expected

    def test_back_pops_and_on_the_root_it_stops_the_app(self, driver: Driver) -> None:
        driver.press("start")
        driver.back()
        assert isinstance(driver.screen, Menu)
        driver.back()
        assert not driver.app.running

    def test_tree_is_what_the_renderer_receives(self, driver: Driver) -> None:
        tree = driver.tree()
        assert tree["screen"] == "Menu"
        assert tree["type"] == "Column"


class TestNavigate:
    def test_a_route_returns_the_screen_after_each_step(self, driver: Driver) -> None:
        landed = driver.navigate("Menu.start, Quiz.answer=42, Quiz.next")
        assert [type(screen) for screen in landed] == [Quiz, Quiz, Result]
        assert driver.find("score").text == "42/10"  # type: ignore[attr-defined]

    def test_a_wrong_screen_prefix_stops_the_route_where_it_drifted(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError) as caught:
            driver.navigate("Menu.start, Menu.next")
        assert "expects the screen Menu, but the current screen is Quiz" in str(caught.value)
        assert caught.value.hint is not None

    def test_back_in_a_route(self, driver: Driver) -> None:
        landed = driver.navigate("start, <back>")
        assert [type(screen) for screen in landed] == [Quiz, Menu]

    def test_a_route_that_exits_the_app_says_so(self, driver: Driver) -> None:
        with pytest.raises(PyMobileError, match="the app stopped at step '<back>'"):
            driver.navigate("<back>")

    def test_a_list_of_steps_works_too(self, driver: Driver) -> None:
        driver.navigate(["start", "answer=5", "next"])
        assert isinstance(driver.screen, Result)


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------
MAIN = textwrap.dedent(
    """
    from pymobile import App, Button, Column, Label, Screen, TextInput

    class Menu(Screen):
        def build(self):
            return Column(
                Label("Main menu", id="h"),
                Button("Start", id="start", on_press=lambda: self.app.push(Quiz())),
            )

    class Quiz(Screen):
        def build(self):
            self.answer = TextInput(id="answer")
            return Column(
                Label("Question 1", id="q"),
                self.answer,
                Button("Next", id="next", on_press=self.finish),
            )

        def finish(self):
            self.app.push(Result(int(self.answer.value or 0)))

    class Result(Screen):
        def __init__(self, score=0, total=10):
            super().__init__()
            self.score, self.total = score, total

        def build(self):
            return Label(f"Score {self.score} of {self.total}", id="score")

    def main():
        App("Quiz").run(Menu())

    if __name__ == "__main__":
        main()
    """
)


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "main.py").write_text(MAIN, encoding="utf-8")
    (tmp_path / "pymobile.toml").write_text(
        'name = "Quiz"\npackage = "com.example.quiz"\n', encoding="utf-8"
    )
    monkeypatch.setenv("PYMOBILE_KEYSTORE_DIR", str(tmp_path / "keys"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def preview(*args: str) -> int:
    return main(["preview", *args])


class TestPreviewCommand:
    def test_without_flags_it_still_draws_the_first_screen(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview() == 0
        assert "Main menu" in capsys.readouterr().out

    def test_screen_shows_another_screen_with_constructor_arguments(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--screen", "Result", "--args", '{"score": 7}') == 0
        out = capsys.readouterr().out
        assert "Score 7 of 10" in out
        assert "Main menu" not in out

    def test_args_can_be_a_list(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--screen", "Result", "--args", "[3, 5]") == 0
        assert "Score 3 of 5" in capsys.readouterr().out

    def test_set_passes_keyword_arguments_without_any_quoting(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd.exe and Windows PowerShell eat the quotes inside JSON; --set needs none."""
        assert preview("--screen", "Result", "--set", "score=7", "--set", "total=12") == 0
        assert "Score 7 of 12" in capsys.readouterr().out

    def test_set_adds_to_args_and_wins_over_it(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            preview("--screen", "Result", "--args", '{"score": 3, "total": 5}', "--set", "score=9")
            == 0
        )
        assert "Score 9 of 5" in capsys.readouterr().out

    def test_set_goes_with_positional_args(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--screen", "Result", "--args", "[4]", "--set", "total=8") == 0
        assert "Score 4 of 8" in capsys.readouterr().out

    def test_navigate_presses_its_way_there(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--navigate", "Menu.start, Quiz.answer=42, Quiz.next") == 0
        captured = capsys.readouterr()
        assert "Score 42 of 10" in captured.out
        assert "Menu.start → Quiz" in captured.out

    def test_screen_and_navigate_combine(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--screen", "Quiz", "--navigate", "answer=9, next") == 0
        assert "Score 9 of 10" in capsys.readouterr().out

    def test_the_mockup_png_shows_the_chosen_screen(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pytest.importorskip("PIL")
        target = project_dir / "result.png"
        assert preview("--screen", "Result", "--png", str(target)) == 0
        assert target.stat().st_size > 1000

    def test_an_unknown_screen_suggests_the_right_one(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--screen", "Resulz") == 1
        err = capsys.readouterr().err
        assert "Did you mean 'Result'" in err
        assert "Menu, Quiz, Result" in err

    def test_a_wrong_step_reports_the_widget(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--navigate", "Menu.strt") == 1
        assert "Did you mean 'start'" in capsys.readouterr().err

    def test_a_step_on_the_wrong_screen_is_reported(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--navigate", "Quiz.next") == 1
        assert "expects the screen Quiz" in capsys.readouterr().err

    def test_args_without_screen_is_an_error(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--args", '{"a": 1}') == 1
        assert "--args only makes sense with --screen" in capsys.readouterr().err

    def test_set_without_screen_is_an_error(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert preview("--set", "score=1") == 1
        assert "--set only makes sense with --screen" in capsys.readouterr().err
        assert preview("--args", "[1]", "--set", "a=1") == 1
        assert "--args and --set only make sense with --screen" in capsys.readouterr().err

    def test_bad_json_is_explained(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # what cmd.exe and Windows PowerShell 5.1 hand over for --args '{"score": 7}'
        assert preview("--screen", "Result", "--args", "{score: 7}") == 1
        err = capsys.readouterr().err
        assert "not valid JSON" in err
        assert "--set score=7" in err  # the way out that needs no quotes

    @pytest.mark.parametrize("pair", ["score", "=1", "my-key=1", "1x=2", " =3"])
    def test_a_malformed_set_is_explained(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str], pair: str
    ) -> None:
        assert preview("--screen", "Result", "--set", pair) == 1
        err = capsys.readouterr().err
        assert "--set expects NAME=VALUE" in err
        assert "--set score=7" in err

    def test_run_and_watch_accept_the_same_flags(self) -> None:
        from pymobile.cli import build_parser

        parser = build_parser()
        for command in ("run", "watch", "preview"):
            args = parser.parse_args(
                [command, "--screen", "Result", "--args", "[1]", "--navigate", "a"]
            )
            assert (args.screen, args.screen_args, args.navigate) == ("Result", "[1]", "a")
            args = parser.parse_args([command, "--screen", "R", "--set", "a=1", "--set", "b=2"])
            assert args.screen_set == ["a=1", "b=2"]
            # --arg would be a one-letter typo of --args; argparse must not guess it
            assert parser.parse_args([command]).screen_set is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("7", 7),
            ("0.5", 0.5),
            ("true", True),
            ("null", None),
            ('"quoted"', "quoted"),
            ("[1, 2]", [1, 2]),
            ("Anna", "Anna"),
            ("007", "007"),  # not JSON (leading zeros): stays text
            ("NaN", "NaN"),  # a word on a command line, not a float
            ("Infinity", "Infinity"),
            ("", ""),
            ("a=b", "a=b"),  # only the first = separates name and value
        ],
    )
    def test_a_set_value_is_json_when_it_parses_and_text_otherwise(
        self, raw: str, expected: object
    ) -> None:
        from pymobile.cli import _screen_arguments

        assert _screen_arguments(None, [f"x={raw}"]) == ([], {"x": expected})


class TestRunAndWatch:
    def test_run_starts_on_the_requested_screen(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["run", "--screen", "Result", "--args", '{"score": 2}']) == 0
        assert "Score 2 of 10" in capsys.readouterr().out

    def test_a_reload_lands_on_the_requested_screen(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.cli import _entrypoint, _reload, build_parser
        from pymobile.core.config import load_config

        args = build_parser().parse_args(["watch", "--navigate", "Menu.start"])
        args.verbose = False
        config = load_config(project_dir)
        _reload(config, _entrypoint(config), args)
        assert "Question 1" in capsys.readouterr().out

    def test_a_reload_reports_a_bad_screen_with_its_hint(
        self, project_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.cli import _entrypoint, _reload, build_parser
        from pymobile.core.config import load_config

        args = build_parser().parse_args(["watch", "--screen", "Nope"])
        args.verbose = False
        config = load_config(project_dir)
        _reload(config, _entrypoint(config), args)
        err = capsys.readouterr().err
        assert "no screen called 'Nope'" in err
        assert "hint:" in err
