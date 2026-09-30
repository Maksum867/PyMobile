"""Regression tests for audit groups E-H: threads and lifecycle, widget API,
validation / i18n / events, and documentation that disagreed with the code.

Java and C cannot run on the desktop; as in ``test_audit_fixes.py`` they are
checked through their sources and the prebuilt binaries that actually ship.
"""

from __future__ import annotations

import gc
import logging
import queue
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pymobile import (
    App,
    Badge,
    Column,
    Label,
    List,
    ListTile,
    PyMobileDeprecationWarning,
    RadioButton,
    RadioGroup,
    Screen,
    Slider,
    Style,
    TextInput,
    Validator,
    Widget,
    plural_category,
)
from pymobile.core.bridge.stub import StubBridge
from pymobile.core.events import EventBus
from pymobile.core.i18n import Translations, translations
from pymobile.core.jobs import JobManager
from pymobile.core.net.http import HttpSecurityPolicy
from pymobile.core.validation import integer, number, required
from pymobile.errors import NetworkError

PACKAGE = Path(__file__).resolve().parents[1]
ANDROID_DIR = PACKAGE / "resources" / "android"
JAVA_DIR = ANDROID_DIR / "java"
PREBUILT = ANDROID_DIR / "prebuilt" / "arm64-v8a"
README = PACKAGE.parent / "README.md"


# --------------------------------------------------------------------------
# A bridge with a real event loop, like the device: App.run() blocks in it.
# --------------------------------------------------------------------------
class LoopBridge(StubBridge):
    def __init__(self) -> None:
        super().__init__(verbose=False)
        self.events: queue.Queue[tuple[str, str, str] | None] = queue.Queue()

    def next_event(self, timeout_ms: int = -1) -> tuple[str, str, str] | None:
        return self.events.get()

    def wake(self) -> None:
        self.events.put(("", "__wake__", ""))

    def send(self, widget_id: str, kind: str, value: str = "") -> None:
        self.events.put((widget_id, kind, value))

    def quit(self) -> None:
        self.events.put(None)


class _Counter(Screen):
    def build(self) -> Widget:
        self.label = Label("0", id="counter")
        return Column(self.label)


@pytest.fixture
def looping() -> Iterator[tuple[App, _Counter, LoopBridge, list[int]]]:
    bridge = LoopBridge()
    app = App("Loop", bridge=bridge)
    screen = _Counter()
    loop_ident: list[int] = []

    def run() -> None:
        loop_ident.append(threading.get_ident())
        app.run(screen)

    thread = threading.Thread(target=run, name="event-loop", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while app._loop_thread is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert app._loop_thread is not None
    yield app, screen, bridge, loop_ident
    app.stop()
    bridge.quit()
    thread.join(5)


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(r.getMessage() for r in self.records)


@pytest.fixture
def pm_log() -> Iterator[_Records]:
    """Records of the ``pymobile`` logger (it does not propagate to root)."""
    handler = _Records()
    logger = logging.getLogger("pymobile")
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def _wait(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.005)


# --------------------------------------------------------------------------
# E. Threads and lifecycle
# --------------------------------------------------------------------------
class TestThreads:
    def test_dispatch_runs_on_the_event_loop_thread(self, looping: Any) -> None:
        app, screen, bridge, loop_ident = looping
        ran_on: list[int] = []

        def update(text: str) -> None:
            ran_on.append(threading.get_ident())
            screen.label.text = text

        worker = threading.Thread(target=lambda: app.dispatch(update, "from worker"))
        worker.start()
        worker.join()
        _wait(lambda: ran_on)
        assert ran_on == loop_ident
        _wait(lambda: "from worker" in str(bridge.last_tree))

    def test_timers_run_on_the_event_loop_thread(self, looping: Any) -> None:
        app, _screen, _bridge, loop_ident = looping
        ran_on: list[int] = []
        app.set_timeout(5, lambda: ran_on.append(threading.get_ident()))
        _wait(lambda: ran_on)
        assert ran_on == loop_ident

    def test_background_timer_stays_on_the_timer_thread(self, looping: Any) -> None:
        app, _screen, _bridge, loop_ident = looping
        ran_on: list[int] = []
        app.set_timeout(5, lambda: ran_on.append(threading.get_ident()), background=True)
        _wait(lambda: ran_on)
        assert ran_on != loop_ident

    def test_job_callbacks_run_on_the_event_loop_thread(self, looping: Any) -> None:
        app, _screen, _bridge, loop_ident = looping
        worker: list[int] = []
        callback: list[int] = []

        def work() -> int:
            worker.append(threading.get_ident())
            return 42

        app.run_job(work).then(on_success=lambda _r: callback.append(threading.get_ident()))
        _wait(lambda: callback)
        assert callback == loop_ident
        assert worker != loop_ident

    def test_http_client_of_the_app_delivers_on_the_ui_side(self) -> None:
        app = App("Http", bridge=StubBridge(verbose=False))
        assert app.http.deliver is not None
        assert app.jobs._deliver is not None


class TestJobs:
    def test_error_in_on_success_goes_to_on_error(self, pm_log: _Records) -> None:
        errors: list[BaseException] = []

        def boom(_value: Any) -> None:
            raise RuntimeError("callback failed")

        handle = JobManager().enqueue(lambda: 7)
        handle.wait(5)
        handle.then(on_success=boom, on_error=errors.append)
        assert handle.result == 7  # the job's own outcome is kept
        assert handle.error is None
        assert [str(e) for e in errors] == ["callback failed"]
        assert any(r.exc_info for r in pm_log.records)

    def test_cancel_does_not_mark_a_running_job_done(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow() -> None:
            started.set()
            release.wait(5)

        handle = JobManager().enqueue(slow)
        started.wait(5)
        handle.cancel()
        assert handle.cancelled and not handle.done
        release.set()
        _wait(lambda: handle.done)


class TestLifecycle:
    def test_failed_build_in_replace_keeps_the_old_screen(self) -> None:
        class Broken(Screen):
            def build(self) -> Widget:
                raise RuntimeError("bad build")

        app = App("Nav", bridge=StubBridge(verbose=False))
        home = _Counter()
        app.run(home)
        with pytest.raises(RuntimeError):
            app.replace(Broken())
        assert app.navigator.current is home
        with pytest.raises(RuntimeError):
            app.reset(Broken())
        assert app.navigator.current is home

    def test_dropped_app_unsubscribes_from_translations(self) -> None:
        before = list(translations._listeners)
        app = App("Gone", bridge=StubBridge(verbose=False))
        app.run(_Counter())
        mine = [fn for fn in translations._listeners if fn not in before]
        assert len(mine) == 1
        # App.current() holds the most recently started app on purpose, so
        # start another one before dropping the first.
        successor = App("Next", bridge=StubBridge(verbose=False))
        successor.run(_Counter())
        del app
        gc.collect()
        assert mine[0] not in translations._listeners
        successor.stop()

    def test_type_error_in_a_handler_is_logged_with_traceback(self, pm_log: _Records) -> None:
        # The handler must live outside the pymobile package, like an app's.
        user_code: dict[str, Any] = {}
        exec(
            compile("def go():\n    raise TypeError('real bug in the handler')\n",
                    "/srv/app/main.py", "exec"),
            user_code,
        )

        class Home(Screen):
            def build(self) -> Widget:
                from pymobile import Button

                return Column(Button("Go", id="go", on_press=user_code["go"]))

        app = App("Err", bridge=StubBridge(verbose=False))
        app.run(Home())
        logging.getLogger("pymobile").addHandler(pm_log)  # run() reconfigures logging
        app.handle_ui_event("go", "press", "")
        records = [r for r in pm_log.records if r.exc_info]
        assert records, "the handler's TypeError must be logged with its traceback"
        assert "is not valid" not in pm_log.text


# --------------------------------------------------------------------------
# F. Widgets and API
# --------------------------------------------------------------------------
class TestWidgets:
    def test_conflicting_aliases_raise(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            Slider(minimum=0, min=10)
        # min=/max= still work, but are deprecated aliases of minimum=/maximum=.
        with pytest.warns(PyMobileDeprecationWarning) as record:
            assert Slider(min=10, max=20).props()["minimum"] == 10
        assert [str(w.message).split()[0] for w in record] == ["min=", "max="]

    def test_text_input_truncates_the_initial_value(self) -> None:
        assert TextInput(value="abcdef", max_length=3).value == "abc"

    def test_auto_ids_outside_build_do_not_collide(self) -> None:
        outside = Label("x")
        assert "-g" in outside.id

    def test_assigning_a_public_attribute_redraws(self, monkeypatch: pytest.MonkeyPatch) -> None:
        badge = Badge("new")
        calls: list[int] = []
        monkeypatch.setattr(Badge, "invalidate", lambda self: calls.append(1))
        badge.style = Style(bold=True)
        assert calls
        calls.clear()
        same = badge.style
        badge.style = same  # unchanged: no redraw
        assert not calls

    def test_radio_group_by_index_with_duplicate_labels(self) -> None:
        picked: list[str] = []
        group = RadioGroup(
            RadioButton("Other"),
            RadioButton("Other"),
            RadioButton("Third"),
            on_select=picked.append,
        )
        group.select_index(1)
        assert group.selected_index == 1
        assert [r.selected for r in group.children] == [False, True, False]  # type: ignore[attr-defined]
        group.children[2].press()  # type: ignore[attr-defined]
        assert group.selected_index == 2 and group.value == "Third"
        assert group.props()["selected_index"] == 2

    def test_list_loads_pages_and_ignores_stale_requests(self) -> None:
        rows = List(1000, builder=lambda i: ListTile(title=f"row {i}"), visible_count=10)
        assert rows.loaded == 10 and rows.has_more
        rows._ui_load_more("10")
        assert rows.loaded == 20
        rows._ui_load_more("10")  # the same request delivered twice
        assert rows.loaded == 20
        props = rows.props()
        assert props["loaded"] == 20 and props["has_more"] is True
        rows.scroll_to(999)
        assert rows.loaded == 1000 and not rows.has_more

    def test_stub_toast_defaults_to_short(self) -> None:
        bridge = StubBridge(verbose=False)
        bridge.toast("hi")
        App("Toast", bridge=bridge).toast("again")
        assert len(bridge.calls_named("toast")) == 2


class TestNativeSources:
    """The renderer and JNI bridge fixes, checked in the sources and binaries."""

    view_builder = (JAVA_DIR / "ViewBuilder.java").read_text(encoding="utf-8")
    jni = (ANDROID_DIR / "jni" / "pymobile_jni.c").read_text(encoding="utf-8")

    def test_list_pages_in_place(self) -> None:
        assert "updateList(" in self.view_builder
        assert '"load_more"' in self.view_builder
        assert "removeOnScrollChangedListener" in self.view_builder
        assert b"load_more" in (PREBUILT / "classes.dex").read_bytes()

    def test_jni_source_compiles_in_order(self) -> None:
        # event_free() was used before its definition: the 0.7.3 C source did
        # not compile, so the shipped .so silently predated it.
        assert self.jni.index("static void event_free") < self.jni.index("event_free(event);")
        assert '!= ""' not in self.jni
        assert "GetStaticMethodID" in self.jni.split("static jmethodID static_method")[1][:400]

    def test_prebuilt_bridge_matches_the_source(self) -> None:
        so = (PREBUILT / "libpymobile.so").read_bytes()
        assert b"pythonIsInitialized" in so
        assert b"__wake__" in so
        assert b"Wake the thread blocked in next_event()" in so


# --------------------------------------------------------------------------
# G. Validation, i18n, events
# --------------------------------------------------------------------------
class TestValidation:
    def test_matches_rejects_an_empty_confirmation(self) -> None:
        v = Validator({"password": ["required"], "confirm": [{"matches": "password"}]})
        assert "confirm" in v.validate({"password": "secret", "confirm": ""})
        opt = Validator({"confirm": ["optional", {"matches": "password"}]})
        assert "confirm" in opt.validate({"password": "secret", "confirm": ""})
        assert opt.validate({}) == {}

    def test_fields_without_optional_are_validated_when_empty(self) -> None:
        assert Validator({"e": ["email"]}).validate({"e": ""}) != {}
        assert Validator({"e": ["optional", "email"]}).validate({"e": "   "}) == {}

    def test_stricter_scalars(self) -> None:
        assert required("   ") is not None
        assert integer("4_2") is not None and integer(" -42 ") is None
        assert number("nan") is not None and number(float("inf")) is not None
        assert number("1e3") is None and number(".5") is None


class TestPlurals:
    def test_rule_follows_the_language(self) -> None:
        assert plural_category(21, "pl") == "many"
        assert plural_category(21, "uk") == "one"
        assert plural_category(22, "pl") == "few"
        assert plural_category(0, "fr") == "one"
        assert plural_category(5, "ja") == "other"
        assert plural_category(1.5, "uk") == "other"

    def test_catalogue_uses_its_own_language(self) -> None:
        cat = Translations(default_language="en")
        cat.load({"f": {"one": "{count} plik", "few": "{count} pliki", "many": "{count} plików"}},
                 language="pl")
        cat.load({"f": {"one": "{count} file", "other": "{count} files"}}, language="en")
        assert cat.get("f", count=21, language="pl") == "21 plików"
        assert cat.get("f", count=21, language="de") == "21 files"  # en fallback, en rule


class TestEvents:
    def test_off_accepts_a_bound_method(self) -> None:
        class Owner:
            def handler(self, _event: Any) -> None:
                pass

        bus = EventBus()
        owner = Owner()
        bus.on("x", owner.handler)
        assert bus.off("x", owner.handler) == 1
        assert len(bus) == 0

    def test_app_off(self) -> None:
        app = App("Off", bridge=StubBridge(verbose=False))
        seen: list[str] = []
        app.on("ping", lambda e: seen.append("a"))
        assert app.off("ping") == 1

    def test_cancel_removes_only_its_own_subscription(self) -> None:
        bus = EventBus()
        seen: list[int] = []

        def handler(_event: Any) -> None:
            seen.append(1)

        first = bus.on("x", handler)
        bus.on("x", handler)
        first.cancel()
        bus.emit("x")
        assert seen == [1]


# --------------------------------------------------------------------------
# H. Documentation vs behaviour, templates, security policy, store name
# --------------------------------------------------------------------------
class TestDocsAndTemplates:
    readme = README.read_text(encoding="utf-8") if README.exists() else ""
    templates = PACKAGE / "resources" / "templates"

    def test_template_relies_on_auto_render(self) -> None:
        main = (self.templates / "main.py.template").read_text(encoding="utf-8")
        assert "self.app.render()" not in main

    def test_template_does_not_promise_bytecode_by_default(self) -> None:
        toml = (self.templates / "pymobile.toml.template").read_text(encoding="utf-8")
        assert re.search(r"^optimize = false", toml, re.M)

    @pytest.mark.skipif(not README.exists(), reason="README not packaged")
    def test_readme_matches_the_code(self) -> None:
        assert "PLG-12" not in self.readme
        assert "## FAQ" in self.readme
        assert "## Threads and the UI" in self.readme
        assert "HttpSecurityPolicy" in self.readme
        duplicate = "from pymobile import HttpClient\n\nfrom pymobile import HttpCache"
        assert duplicate not in self.readme
        assert "<your.package.id>.json" not in self.readme

    def test_security_policy_hosts_are_case_insensitive(self) -> None:
        policy = HttpSecurityPolicy(require_https=True, allowed_hosts=["API.Example.com"])
        policy.validate("https://api.example.com/items")
        with pytest.raises(NetworkError):
            policy.validate("http://api.example.com/items")
        with pytest.raises(TypeError):
            HttpSecurityPolicy(allowed_hosts="api.example.com")  # type: ignore[arg-type]

    def test_store_named_with_dots_is_adopted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYMOBILE_STORAGE_DIR", str(tmp_path))
        (tmp_path / "com.arena.flashcards.json").write_text('{"deck": "seeded"}')
        app = App("Cards", package="com.arena.flashcards", bridge=StubBridge(verbose=False))
        assert app.storage.path.name == "com-arena-flashcards.json"
        assert app.storage.get("deck") == "seeded"


def test_build_fingerprint_covers_the_framework() -> None:
    from pymobile.compiler.pipeline import _framework_inputs

    names = {path.name for path in _framework_inputs()}
    assert {"classes.dex", "libpymobile.so", "ViewBuilder.java", "pymobile_jni.c"} <= names
