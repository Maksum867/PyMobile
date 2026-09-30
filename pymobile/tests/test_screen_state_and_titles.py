"""``Screen.is_current`` (no more reading the private ``_app``) and ``Screen.title_key``."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from pymobile import App, Label, Screen
from pymobile.core.bridge import StubBridge
from pymobile.core.i18n import translations


@pytest.fixture
def app(bridge: StubBridge, tmp_path) -> Iterator[App]:
    application = App("Demo", bridge=bridge, storage_path=str(tmp_path / "store.json"))
    yield application
    application.stop()


@pytest.fixture(autouse=True)
def _clean_catalogue() -> Iterator[None]:
    translations.clear()
    yield
    translations.clear()


class Recorder(Screen):
    """Remembers what ``is_current`` said inside every lifecycle hook."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.seen: list[tuple[str, bool]] = []

    def build(self) -> Label:
        return Label(self.title, id="title")

    def on_mount(self) -> None:
        self.seen.append(("mount", self.is_current))

    def on_show(self) -> None:
        self.seen.append(("show", self.is_current))

    def on_hide(self) -> None:
        self.seen.append(("hide", self.is_current))

    def on_unmount(self) -> None:
        self.seen.append(("unmount", self.is_current))


# --------------------------------------------------------------------------
# Screen.is_current
# --------------------------------------------------------------------------
def test_a_screen_that_was_never_pushed_is_not_current() -> None:
    assert Recorder("a").is_current is False


def test_is_current_follows_the_stack(app: App) -> None:
    first, second = Recorder("first"), Recorder("second")
    app.run(first)
    assert first.is_current and app.screen is first

    app.push(second)
    assert second.is_current
    assert not first.is_current  # covered, although still mounted
    assert first.mounted

    app.pop()
    assert first.is_current
    assert not second.is_current
    assert second.mounted is False


def test_is_current_is_false_inside_hide_and_true_inside_show(app: App) -> None:
    first, second = Recorder("first"), Recorder("second")
    app.run(first)
    app.push(second)
    app.pop()
    assert first.seen == [
        ("mount", True),
        ("show", True),
        ("hide", False),  # covered by `second`
        ("show", True),  # uncovered by pop()
    ]
    assert second.seen == [("mount", True), ("show", True), ("hide", False), ("unmount", False)]


def test_replace_and_reset_hand_the_flag_over(app: App) -> None:
    first, second, third = Recorder("1"), Recorder("2"), Recorder("3")
    app.run(first)
    app.replace(second)
    assert second.is_current and not first.is_current
    app.navigator.reset(third)
    assert third.is_current and not second.is_current


def test_stopping_the_app_clears_the_flag(app: App) -> None:
    screen = Recorder("only")
    app.run(screen)
    assert screen.is_current
    app.stop()
    assert screen.is_current is False


def test_is_current_never_raises_after_the_screen_left_the_stack(app: App) -> None:
    """``screen.app`` raises for a popped screen; asking whether it is current must not."""
    first, second = Recorder("1"), Recorder("2")
    app.run(first)
    app.push(second)
    app.pop()
    with pytest.raises(Exception, match="no longer on the stack"):
        second.app
    assert second.is_current is False


def test_is_current_can_be_read_from_a_worker_thread(app: App) -> None:
    screen = Recorder("only")
    app.run(screen)
    seen: list[bool] = []
    worker = threading.Thread(target=lambda: seen.append(screen.is_current))
    worker.start()
    worker.join()
    assert seen == [True]


def test_is_current_is_read_only() -> None:
    with pytest.raises(AttributeError):
        Recorder("x").is_current = True  # type: ignore[misc]


# --------------------------------------------------------------------------
# Screen.title_key
# --------------------------------------------------------------------------
class Settings(Screen):
    title = "Settings"
    title_key = "settings.title"

    def build(self) -> Label:
        return Label(f"<{self.title}>", id="heading")


def test_without_a_catalogue_the_class_title_is_the_fallback(bridge: StubBridge, app: App) -> None:
    app.run(Settings())
    assert bridge.last_tree["screen"] == "Settings"


def test_the_key_is_translated_when_the_screen_is_built(bridge: StubBridge, app: App) -> None:
    translations.load({"settings.title": "Налаштування"}, language="uk")
    translations.use("uk")
    app.run(Settings())
    assert bridge.last_tree["screen"] == "Налаштування"
    # build() sees the translated title too, not the class-level literal.
    assert bridge.last_tree["props"]["text"] == "<Налаштування>"


def test_switching_the_language_retitles_the_screen(bridge: StubBridge, app: App) -> None:
    translations.load({"settings.title": "Settings"}, language="en")
    translations.load({"settings.title": "Налаштування"}, language="uk")
    app.run(Settings())
    assert bridge.last_tree["screen"] == "Settings"
    translations.use("uk")
    assert bridge.last_tree["screen"] == "Налаштування"
    translations.use("en")
    assert bridge.last_tree["screen"] == "Settings"


def test_screens_below_the_current_one_are_retitled_too(bridge: StubBridge, app: App) -> None:
    translations.load({"settings.title": "Налаштування"}, language="uk")
    below = Settings()
    app.run(below)
    app.push(Recorder("top"))
    translations.use("uk")
    app.pop()
    assert bridge.last_tree["screen"] == "Налаштування"


def test_title_key_can_be_passed_to_the_constructor(bridge: StubBridge, app: App) -> None:
    class Plain(Screen):
        def build(self) -> Label:
            return Label("x", id="x")

    translations.load({"about": "Про застосунок"}, language="uk")
    translations.use("uk")
    app.run(Plain("About", title_key="about"))
    assert bridge.last_tree["screen"] == "Про застосунок"


def test_a_missing_key_keeps_the_fallback_title(bridge: StubBridge, app: App) -> None:
    translations.load({"other": "x"}, language="uk")
    translations.use("uk")
    app.run(Settings())
    assert bridge.last_tree["screen"] == "Settings"


def test_the_class_name_is_the_last_fallback(bridge: StubBridge, app: App) -> None:
    class Untitled(Screen):
        title_key = "nope"

        def build(self) -> Label:
            return Label("x", id="x")

    app.run(Untitled())
    assert bridge.last_tree["screen"] == "Untitled"


def test_an_empty_title_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="title_key"):
        Screen("x", title_key="  ")


def test_a_title_assigned_in_build_still_works() -> None:
    """The other idiom: a data-dependent title set inside ``build()``."""

    class Profile(Screen):
        def build(self) -> Label:
            self.title = "Profile: Олена"
            return Label("x", id="x")

    assert Profile().to_dict()["screen"] == "Profile: Олена"


def test_to_dict_reports_the_freshly_translated_title(app: App) -> None:
    """The title is read after the tree is built: the first frame after a
    language change must not carry the previous language's title."""
    translations.load({"settings.title": "Налаштування"}, language="uk")
    screen = Settings()
    app.run(screen)
    translations.use("uk")
    screen.refresh()  # drops the tree; the next to_dict() rebuilds it
    assert screen.to_dict()["screen"] == "Налаштування"
