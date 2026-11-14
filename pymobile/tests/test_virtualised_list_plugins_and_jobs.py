"""Virtualised List, plugins, background jobs, diagnostics and x86_64."""

from __future__ import annotations

import time

import pytest

from pymobile import (
    App,
    Label,
    List,
    ListTile,
    Plugin,
    PluginRegistry,
    get_diagnostics,
    plugins,
)
from pymobile.core.bridge import StubBridge


# --------------------------------------------------------------------------
# Virtualised List
# --------------------------------------------------------------------------
def test_list_builds_visible_window():
    lst = List(100, builder=lambda i: ListTile(f"Item {i}"))
    assert lst.item_count == 100
    assert len(lst.children) == 20  # visible_count default


def test_list_custom_visible_count():
    lst = List(100, builder=lambda i: Label(str(i)), visible_count=5)
    assert len(lst.children) == 5


def test_list_refresh_after_count_change():
    lst = List(3, builder=lambda i: ListTile(f"r{i}"))
    assert len(lst.children) == 3
    lst.item_count = 50
    lst.refresh()
    assert len(lst.children) == 20  # capped at visible_count


def test_list_builder_used():
    lst = List(2, builder=lambda i: Label(f"row-{i}"))
    assert [c.to_dict()["props"]["text"] for c in lst.children] == ["row-0", "row-1"]


def test_list_default_builder_makes_tiles():
    lst = List(3)  # no builder
    assert all(isinstance(c, ListTile) for c in lst.children)


def test_list_add_is_blocked():
    lst = List(2)
    with pytest.raises(ValueError):
        lst.add(Label("x"))


def test_list_invalid_args():
    with pytest.raises(ValueError):
        List(-1)
    with pytest.raises(ValueError):
        List(2, visible_count=0)


def test_list_scroll_to_validates():
    lst = List(5)
    lst.scroll_to(3)  # ok
    with pytest.raises(IndexError):
        lst.scroll_to(99)


def test_list_tile_fields():
    tile = ListTile("Title", subtitle="Sub", trailing=">")
    assert tile.title == "Title"
    assert tile.subtitle == "Sub"
    assert tile.trailing == ">"


def test_list_tile_press():
    fired = []
    tile = ListTile("x", on_press=lambda: fired.append(1))
    tile.press()
    assert fired == [1]


def test_list_preview_renders_rows():
    from pymobile.core.ui.preview import render_ascii

    lst = List(3, builder=lambda i: ListTile(f"Item {i}", subtitle=f"row {i}"))
    text = render_ascii(lst.to_dict())
    assert "Item 0" in text and "row 1" in text
    assert "<List>" not in text and "<ListTile>" not in text


# --------------------------------------------------------------------------
# Plugins
# --------------------------------------------------------------------------
def test_plugin_registry_register_and_activate():
    registry = PluginRegistry()

    class P(Plugin):
        name = "p1"

        def activate(self, app):
            self.calls = 1

    p = P()
    registry.register(p)
    assert "p1" in registry.names
    app = App("t", bridge=StubBridge(verbose=False))
    registry.activate_all(app)
    assert p.calls == 1


def test_plugin_activate_once():
    registry = PluginRegistry()

    class P(Plugin):
        name = "p"

        def activate(self, app):
            self.count = getattr(self, "count", 0) + 1

    p = P()
    registry.register(p)
    app = App("t", bridge=StubBridge(verbose=False))
    registry.activate_all(app)
    registry.activate_all(app)  # second call must not re-activate
    assert p.count == 1


def test_plugin_duplicate_ignored():
    registry = PluginRegistry()

    class P(Plugin):
        name = "dup"

    registry.register(P())
    registry.register(P())
    assert len(registry) == 1


def test_plugin_lifecycle_hooks():
    registry = PluginRegistry()

    class P(Plugin):
        name = "p"
        started = stopped = False

        def on_app_start(self, app):
            P.started = True

        def on_app_stop(self, app):
            P.stopped = True

    registry.register(P())
    app = App("t", bridge=StubBridge(verbose=False))
    app.run_job(lambda: None)  # ensure app is usable
    registry.on_app_start(app)
    registry.on_app_stop(app)
    assert P.started and P.stopped


def test_plugin_error_does_not_stop_others():
    registry = PluginRegistry()

    class Bad(Plugin):
        name = "bad"

        def activate(self, app):
            raise RuntimeError("boom")

    class Good(Plugin):
        name = "good"

        def activate(self, app):
            self.done = True

    bad, good = Bad(), Good()
    registry.register(bad)
    registry.register(good)
    app = App("t", bridge=StubBridge(verbose=False))
    registry.activate_all(app)  # must not raise
    assert good.done


def test_plugins_integrated_with_app():
    from pymobile import Column, Screen, Widget

    plugins.clear()

    class Demo(Screen):
        def build(self) -> Widget:
            return Column(Label("hi"))

    class P(Plugin):
        name = "appplugin"

        def activate(self, app):
            self.activated = True

    p = P()
    plugins.register(p)
    app = App("t", bridge=StubBridge(verbose=False))
    app.run(Demo())
    assert p.activated


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------
def test_job_returns_result():
    app = App("t", bridge=StubBridge(verbose=False))
    handle = app.run_job(lambda: 42)
    assert handle.wait(timeout=5) == 42


def test_job_delivers_error_to_then():
    app = App("t", bridge=StubBridge(verbose=False))
    errors = []

    def boom():
        raise ValueError("nope")

    handle = app.run_job(boom)
    handle.then(lambda r: None, on_error=lambda e: errors.append(e))
    # wait for completion without raising (wait() re-raises the job's error)
    for _ in range(500):
        if handle.done:
            break
        time.sleep(0.01)
    assert handle.done
    assert len(errors) == 1 and isinstance(errors[0], ValueError)


def test_repeat_job_cancel():
    app = App("t", bridge=StubBridge(verbose=False))
    counter = []
    handle = app.repeat_job(10, lambda: counter.append(1))
    time.sleep(0.1)
    handle.cancel()
    time.sleep(0.05)
    n_after_cancel = len(counter)
    time.sleep(0.05)
    assert len(counter) == n_after_cancel  # stopped firing


def test_job_manager_shutdown_cancels():
    from pymobile.core.jobs import JobManager

    mgr = JobManager()
    mgr.every(10, lambda: None)
    assert mgr.active
    mgr.shutdown()
    assert not mgr.active


def test_job_invalid_interval():
    app = App("t", bridge=StubBridge(verbose=False))
    with pytest.raises(ValueError):
        app.repeat_job(0, lambda: None)


# --------------------------------------------------------------------------
# Diagnostics / logging
# --------------------------------------------------------------------------
def test_get_diagnostics_shape():
    d = get_diagnostics()
    assert d["framework"] == "pymobile"
    assert "platform" in d and "python" in d


def test_configure_with_log_file(tmp_path, monkeypatch):
    from pymobile import logging as plogging

    log_file = tmp_path / "app.log"
    plogging.configure("info", log_file=log_file)
    plogging.get_logger("test").info("hello log")
    assert log_file.exists()
    assert "hello log" in log_file.read_text(encoding="utf-8")
