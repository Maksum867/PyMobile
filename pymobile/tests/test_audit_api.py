"""Audit block 2: deterministic job/lifecycle and public API regressions."""

import threading
from unittest.mock import Mock

import pytest

from pymobile import App, Checkbox, Label, Plugin, PluginRegistry, Screen, Theme
from pymobile.compiler.precheck import check_project
from pymobile.compiler.scaffold import create_project
from pymobile.core.bridge import Bridge, StubBridge
from pymobile.core.jobs import JobHandle, JobManager
from pymobile.core.ui._mockup_colors import _palette
from pymobile.core.ui.gui import GuiPreview
from pymobile.core.ui.web import render_html


class Home(Screen):
    def build(self):
        return Label("home")


def test_public_bridges():
    import pymobile

    assert pymobile.Bridge is Bridge
    assert pymobile.StubBridge is StubBridge
    assert {"Bridge", "StubBridge"} <= set(pymobile.__all__)


def test_registry_snapshot_iteration():
    registry = PluginRegistry()
    a, b = Plugin(), Plugin()
    a.name, b.name = "a", "b"
    registry.register(a)
    registry.register(b)
    names, values, items = iter(registry), registry.all(), registry.items()
    registry.unregister("a")
    assert tuple(names) == ("a", "b")
    assert values == (a, b)
    assert items == (("a", a), ("b", b))
    assert len(registry) == 1 and "b" in registry
    assert list(registry) == ["b"]


def test_checkbox_text_and_backwards_compatible_checked():
    toggles = []
    checkbox = Checkbox(True, text='<Agree & "continue">', on_toggle=toggles.append)
    assert checkbox.checked
    assert checkbox.props()["text"] == '<Agree & "continue">'
    markup = render_html(checkbox.to_dict())
    assert "&lt;Agree &amp; &quot;continue&quot;&gt;" in markup
    assert " checked" in markup and "</label>" in markup
    checkbox.text = "new"
    assert checkbox.props()["text"] == "new"
    assert checkbox.toggle() is False and toggles == [False]
    checkbox.enabled = False
    assert checkbox.toggle() is False and toggles == [False]


def test_checkbox_tk_patch_updates_text_state_and_value():
    preview = object.__new__(GuiPreview)
    widget, variable = Mock(), Mock()
    variable.get.return_value = False
    preview._widgets = {"check": widget}
    preview._variables = {"check": variable}
    preview._patch_node(
        {
            "id": "check",
            "type": "Checkbox",
            "enabled": False,
            "props": {"text": "agree", "checked": True},
        }
    )
    variable.set.assert_called_once_with(True)
    widget.configure.assert_any_call(text="agree")
    widget.configure.assert_any_call(state="disabled")


def test_checkbox_tk_build_uses_clickable_text():
    preview = object.__new__(GuiPreview)
    preview._tk = Mock()
    preview.root = Mock()
    preview._widgets, preview._variables = {}, {}
    preview._build(Mock(), Checkbox(text="agree", id="check").to_dict())
    assert preview._tk.Checkbutton.call_args.kwargs["text"] == "agree"
    assert callable(preview._tk.Checkbutton.call_args.kwargs["command"])


@pytest.mark.parametrize("theme", [Theme.light(), Theme.dark()])
def test_stub_records_app_theme(theme):
    bridge = StubBridge(verbose=False)
    app = App("theme", bridge=bridge, theme=theme)
    try:
        app.run(Home())
        assert bridge.last_tree["theme"]["PRIMARY"] == theme.as_dict()["PRIMARY"]
        assert _palette(bridge.last_tree["theme"])["dark"] == theme.is_dark
    finally:
        app.stop()


def test_mockup_divider_dark_and_custom_surface():
    dark = _palette({"dark": True, "SURFACE": "#123456"})
    light = _palette({"dark": False, "SURFACE": "#FAF6F0"})
    assert dark["DIVIDER"] == (255, 255, 255, 31)
    assert light["DIVIDER"] == (0, 0, 0, 31)
    assert light["SURFACE"] == (250, 246, 240, 255)
    assert dark["SURFACE"] == (18, 52, 86, 255)


@pytest.fixture
def project(tmp_path):
    create_project(tmp_path, "audit")
    return tmp_path


def test_check_scans_lazy_modules_without_executing(project):
    folder = project / "screens"
    folder.mkdir()
    path = folder / "lazy.py"
    path.write_text('raise RuntimeError("must not execute")\nfrom pymobile import Buton\n')
    findings = check_project(project, run_app=False)
    assert len(findings) == 1
    assert findings[0].path == path and findings[0].line == 2
    assert "Button" in findings[0].hint


@pytest.mark.parametrize("excluded", ["tests", "build", ".venv", "__pycache__", "dist"])
def test_check_excludes_non_project_sources(project, excluded):
    folder = project / excluded
    folder.mkdir(exist_ok=True)
    (folder / "bad.py").write_text("from pymobile import Buton\n")
    assert check_project(project, run_app=False) == []


def test_check_reports_nested_syntax_and_valid_bridge_import(project):
    (project / "bridge_config.py").write_text("from pymobile import Bridge, StubBridge\n")
    (project / "broken.py").write_text("def broken(:\n")
    findings = check_project(project, run_app=False)
    assert len(findings) == 1 and findings[0].path.name == "broken.py"
    assert findings[0].line == 1 and "syntax error" in findings[0].message


def test_check_does_not_follow_symlinks(project, tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-outside")
    external.mkdir()
    (external / "bad.py").write_text("from pymobile import Buton\n")
    try:
        (project / "linked").symlink_to(external, target_is_directory=True)
        (project / "linked.py").symlink_to(external / "bad.py")
    except OSError:
        pytest.skip("symlinks unavailable")
    assert check_project(project, run_app=False) == []


def test_callback_aware_wait_waits_for_sync_callback():
    entered, release = threading.Event(), threading.Event()
    handle = JobHandle("sync", lambda: None)

    def callback(value):
        entered.set()
        assert release.wait(2)

    handle.then(callback)
    worker = threading.Thread(target=lambda: handle._complete(42, None))
    worker.start()
    try:
        assert entered.wait(2)
        assert handle.wait(0) == 42  # backwards-compatible outcome-only wait
        with pytest.raises(TimeoutError):
            handle.wait(0.01, include_callbacks=True)
    finally:
        release.set()
        worker.join(2)
    assert handle.wait(1, include_callbacks=True) == 42


def test_queued_and_late_callbacks_are_awaited():
    queue, seen = [], []
    handle = JobHandle("queued", lambda: None, deliver=queue.append)
    handle.then(seen.append)
    handle._complete(42, None)
    with pytest.raises(TimeoutError):
        handle.wait(0, include_callbacks=True)
    queue.pop(0)()
    assert handle.wait(0, include_callbacks=True) == 42 and seen == [42]
    handle.then(seen.append)
    with pytest.raises(TimeoutError):
        handle.wait(0, include_callbacks=True)
    queue.pop(0)()
    assert handle.wait(0, include_callbacks=True) == 42 and seen == [42, 42]


def test_callback_errors_and_reentrant_registration_do_not_strand_waiters():
    handle = JobHandle("reentrant", lambda: None)
    seen = []

    def callback(value):
        handle.then(lambda value: seen.append(value + 1))
        raise ValueError("callback error")

    handle.then(callback, on_error=lambda error: seen.append(str(error)))
    handle._complete(42, None)
    assert handle.wait(0, include_callbacks=True) == 42
    assert seen == [43, "callback error"] and handle.error is None


def test_job_error_waits_for_error_callback():
    queue, seen = [], []
    error = ValueError("job failed")
    handle = JobHandle("error", lambda: None, deliver=queue.append)
    handle.then(on_error=seen.append)
    handle._complete(None, error)
    with pytest.raises(TimeoutError):
        handle.wait(0, include_callbacks=True)
    queue.pop()()
    with pytest.raises(ValueError, match="job failed"):
        handle.wait(0, include_callbacks=True)
    assert seen == [error]


def test_delivery_failure_does_not_change_result_or_strand_waiter():
    def reject(callback):
        raise RuntimeError("queue closed")

    handle = JobHandle("rejected", lambda: None, deliver=reject)
    handle.then(lambda value: None)
    handle._complete(42, None)
    assert handle.wait(0, include_callbacks=True) == 42


def test_cancel_suppresses_queued_callbacks_and_releases_waiter():
    queue, seen = [], []
    handle = JobHandle("cancel", lambda: None, deliver=queue.append)
    handle.then(seen.append)
    handle._complete(42, None)
    handle.cancel()
    assert handle.wait(0, include_callbacks=True) == 42
    queue.pop()()
    assert not seen


def test_job_manager_wait_with_registered_callback():
    gate = threading.Event()
    manager = JobManager()
    seen = []
    handle = manager.enqueue(lambda: (gate.wait(2), 42)[1]).then(seen.append)
    try:
        gate.set()
        assert handle.wait(2, include_callbacks=True) == 42
        assert seen == [42]
    finally:
        gate.set()
        manager.shutdown()


@pytest.mark.parametrize("action", ["pop", "stop", "remount", "raising_unmount"])
def test_screen_jobs_suppress_callbacks_after_unmount(action):
    queue, seen = [], []
    app = App("jobs", bridge=StubBridge(verbose=False))
    # Deterministic stand-in for a queued native UI dispatcher.
    app.jobs._deliver = queue.append
    screen = Home()
    gate = threading.Event()
    try:
        app.run(Home())
        app.push(screen)
        handle = screen.enqueue_job(lambda: (gate.wait(2), 42)[1]).then(seen.append)
        gate.set()
        assert handle.wait(2) == 42
        if action == "raising_unmount":
            screen.on_unmount = Mock(side_effect=RuntimeError("hook failed"))
        if action == "stop":
            app.stop()
        else:
            app.pop()
        if action == "remount":
            app.push(screen)
        handle.then(seen.append)
        for callback in queue:
            callback()
        assert not seen and handle.cancelled
    finally:
        gate.set()
        app.stop()


def test_covered_screen_receives_job_result():
    app = App("covered", bridge=StubBridge(verbose=False))
    screen = Home()
    gate, seen = threading.Event(), []
    try:
        app.run(screen)
        app.push(Home())
        handle = screen.enqueue_job(lambda: (gate.wait(2), 42)[1]).then(seen.append)
        gate.set()
        assert handle.wait(2, include_callbacks=True) == 42
        assert seen == [42]
    finally:
        gate.set()
        app.stop()


def test_unmounted_screen_rejects_jobs():
    from pymobile.errors import PyMobileError

    with pytest.raises(PyMobileError, match="mounted"):
        Home().enqueue_job(lambda: None)


def test_wait_shares_one_timeout_budget(monkeypatch):
    import pymobile.core.jobs as jobs

    times = iter([10.0, 10.75])
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(times))
    handle = JobHandle("budget", lambda: None)
    handle._done = Mock()
    handle._callbacks_done = Mock()
    handle._done.wait.return_value = True
    handle._callbacks_done.wait.return_value = True
    handle.wait(1.0, include_callbacks=True)
    handle._done.wait.assert_called_once_with(1.0)
    handle._callbacks_done.wait.assert_called_once_with(0.25)


def test_completed_pruned_screen_handle_cannot_deliver_after_remount():
    queue, seen = [], []
    app = App("generation", bridge=StubBridge(verbose=False))
    app.jobs._deliver = queue.append
    screen = Home()
    try:
        app.run(Home())
        app.push(screen)
        old = screen.enqueue_job(lambda: 42)
        assert old.wait(2) == 42
        other = screen.enqueue_job(lambda: 99)  # prunes old from screen._jobs
        assert other.wait(2) == 99
        old.then(seen.append)
        assert queue
        app.pop()
        app.push(screen)
        assert not old.cancelled  # the generation guard, not cancellation, protects this
        for callback in queue:
            callback()
        assert old.wait(0, include_callbacks=True) == 42
        assert not seen
    finally:
        app.stop()


@pytest.mark.parametrize("fails", [False, True])
def test_unmount_cancels_work_still_running(fails):
    gate, entered, seen = threading.Event(), threading.Event(), []
    app = App("running", bridge=StubBridge(verbose=False))
    screen = Home()

    def work():
        entered.set()
        assert gate.wait(2)
        if fails:
            raise ValueError("late failure")
        return 42

    try:
        app.run(Home())
        app.push(screen)
        handle = screen.enqueue_job(work).then(seen.append, on_error=seen.append)
        assert entered.wait(2)
        app.pop()
        assert handle.cancelled and not handle.done
        gate.set()
        if fails:
            with pytest.raises(ValueError, match="late failure"):
                handle.wait(2, include_callbacks=True)
        else:
            assert handle.wait(2, include_callbacks=True) == 42
        assert not seen
    finally:
        gate.set()
        app.stop()
