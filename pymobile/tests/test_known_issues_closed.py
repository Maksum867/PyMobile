"""The Known-issues rows that bit new users: #APP-09, #CNT-02 and #AVT-04."""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import pymobile
from pymobile import App, Avatar, Column, Label, PyMobileError, Screen, WidgetParentError
from pymobile.core import app as app_module
from pymobile.core.bridge import StubBridge


class Blank(Screen):
    def build(self) -> Label:
        return Label("blank", id="blank")


def make_app(bridge: StubBridge, tmp_path: Path, name: str) -> App:
    return App(name, bridge=bridge, storage_path=str(tmp_path / f"{name}.json"))


@pytest.fixture(autouse=True)
def _no_leftover_current() -> Iterator[None]:
    yield
    current = App.current()
    if current is not None:
        current.stop()


# --------------------------------------------------------------------------
# #APP-09: App.current() and stop() no longer race
# --------------------------------------------------------------------------
class TestAppCurrent:
    def test_the_last_app_started_is_current(self, bridge: StubBridge, tmp_path: Path) -> None:
        app = make_app(bridge, tmp_path, "one")
        assert App.current() is None
        app.run(Blank())
        assert App.current() is app
        app.stop()
        assert App.current() is None

    def test_stopping_an_older_app_does_not_withdraw_the_newer_one(
        self, bridge: StubBridge, tmp_path: Path
    ) -> None:
        first, second = make_app(bridge, tmp_path, "a"), make_app(bridge, tmp_path, "b")
        first.run(Blank())
        second.run(Blank())
        first.stop()
        assert App.current() is second

    def test_reading_current_waits_for_a_writer(self, bridge: StubBridge, tmp_path: Path) -> None:
        """current() goes through the same lock as run()/stop()."""
        app = make_app(bridge, tmp_path, "locked")
        app.run(Blank())
        results: list[App | None] = []
        started = threading.Event()

        def reader() -> None:
            started.set()
            results.append(App.current())

        with app_module._current_lock:
            thread = threading.Thread(target=reader)
            thread.start()
            started.wait(2)
            thread.join(0.2)
            assert thread.is_alive(), "current() returned while a writer held the lock"
        thread.join(2)
        assert results == [app]

    def test_run_publishes_the_app_under_the_lock(
        self, bridge: StubBridge, tmp_path: Path
    ) -> None:
        app = make_app(bridge, tmp_path, "publisher")
        thread = threading.Thread(target=lambda: app.run(Blank()))
        with app_module._current_lock:
            thread.start()
            thread.join(0.3)
            assert thread.is_alive(), "run() published the app without taking the lock"
        thread.join(3)
        assert not thread.is_alive()
        assert App.current() is app

    def test_stop_withdraws_the_app_under_the_lock(
        self, bridge: StubBridge, tmp_path: Path
    ) -> None:
        """The check ``_current is self`` and the write that follows are one step."""
        app = make_app(bridge, tmp_path, "withdrawer")
        app.run(Blank())
        thread = threading.Thread(target=app.stop)
        with app_module._current_lock:
            thread.start()
            thread.join(0.3)
            assert thread.is_alive(), "stop() withdrew the app without taking the lock"
        thread.join(3)
        assert not thread.is_alive()
        assert App.current() is None

    def test_concurrent_run_and_stop_leave_a_consistent_state(
        self, bridge: StubBridge, tmp_path: Path
    ) -> None:
        """A smoke test of the interleaving the two tests above pin down exactly."""
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            for round_number in range(40):
                old = make_app(bridge, tmp_path, f"old{round_number}")
                new = make_app(bridge, tmp_path, f"new{round_number}")
                old.run(Blank())
                barrier = threading.Barrier(2)

                def stopper(app: App = old, gate: threading.Barrier = barrier) -> None:
                    gate.wait()
                    app.stop()

                def starter(app: App = new, gate: threading.Barrier = barrier) -> None:
                    gate.wait()
                    app.run(Blank())

                threads = [threading.Thread(target=stopper), threading.Thread(target=starter)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                assert App.current() is new, f"round {round_number}"
                new.stop()
        finally:
            sys.setswitchinterval(previous)

    def test_a_worker_thread_can_read_it_while_the_app_stops(
        self, bridge: StubBridge, tmp_path: Path
    ) -> None:
        app = make_app(bridge, tmp_path, "worker")
        app.run(Blank())
        seen: list[App | None] = []
        stop = threading.Event()

        def worker() -> None:
            while not stop.is_set():
                seen.append(App.current())

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            app.stop()
        finally:
            stop.set()
            thread.join(2)
        assert seen, "the worker never ran"
        assert all(value in (app, None) for value in seen)
        assert App.current() is None


# --------------------------------------------------------------------------
# #CNT-02: Container.add raises something callers can catch as ValueError
# --------------------------------------------------------------------------
class TestWidgetParentError:
    def test_add_of_an_owned_widget_is_a_value_error_and_a_pymobile_error(self) -> None:
        label = Label("x", id="x")
        Column(label)
        other = Column()
        with pytest.raises(WidgetParentError) as caught:
            other.add(label)
        error = caught.value
        assert isinstance(error, ValueError)
        assert isinstance(error, PyMobileError)
        assert error.widget_id == "x"
        assert "already has a parent" in str(error)
        assert error.hint and "one place" in error.hint

    def test_except_value_error_catches_it(self) -> None:
        label = Label("x", id="x")
        Column(label)
        with pytest.raises(ValueError, match="already has a parent"):
            Column().add(label)

    def test_it_matches_the_other_add_rejections(self) -> None:
        column = Column()
        with pytest.raises(ValueError, match="cannot contain itself"):
            column.add(column)

    def test_it_is_exported_from_the_package_root(self) -> None:
        assert pymobile.WidgetParentError is WidgetParentError
        assert "WidgetParentError" in pymobile.__all__


# --------------------------------------------------------------------------
# #AVT-04: an Avatar string can be forced either way
# --------------------------------------------------------------------------
class TestAvatarAmbiguity:
    @pytest.mark.parametrize("value", ["foo/bar", "a\\b", "a.png", "https://x.test/me"])
    def test_a_path_shaped_string_can_be_forced_to_initials(self, value: str) -> None:
        avatar = Avatar(value, is_image=False)
        assert (avatar.source, avatar.text) == ("", value)

    @pytest.mark.parametrize("value", ["photo", "me", "MK"])
    def test_a_plain_string_can_be_forced_to_an_image(self, value: str) -> None:
        avatar = Avatar(value, is_image=True)
        assert (avatar.source, avatar.text) == (value, "")

    def test_the_default_still_guesses(self) -> None:
        assert Avatar("MK").text == "MK"
        assert Avatar("assets/me.png").source == "assets/me.png"
        assert Avatar("me.jpg").source == "me.jpg"
        assert Avatar("https://x.test/me").source == "https://x.test/me"

    def test_keywords_state_the_intent_without_a_flag(self) -> None:
        assert Avatar(text="foo/bar").text == "foo/bar"
        assert Avatar(image="photo").source == "photo"

    def test_initials_that_look_like_a_path_can_accompany_an_image(self) -> None:
        avatar = Avatar("a/b", image="me.png", is_image=False)
        assert (avatar.source, avatar.text) == ("me.png", "a/b")

    def test_without_the_flag_two_images_are_still_refused(self) -> None:
        with pytest.raises(ValueError, match="either source or image"):
            Avatar("a/b", image="me.png")

    def test_forced_initials_and_text_together_are_ambiguous(self) -> None:
        with pytest.raises(ValueError, match="is_image=False"):
            Avatar("a/b", text="AB", is_image=False)

    def test_is_image_must_be_a_boolean_or_none(self) -> None:
        with pytest.raises(TypeError, match="is_image"):
            Avatar("x", is_image="yes")  # type: ignore[arg-type]

    def test_props_carry_the_resolved_reading(self) -> None:
        props = Avatar("foo/bar", is_image=False).props()
        assert (props["source"], props["text"]) == ("", "foo/bar")
