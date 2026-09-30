"""Tests for the developer tooling: file watching and the interactive preview.

The Tk widgets themselves need a display, so the tests here exercise the parts
that do not: change detection, the structural fingerprint that decides between
patching and rebuilding, and the CLI wiring.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from pymobile import (
    App,
    Button,
    Column,
    Expanded,
    Grid,
    Label,
    Row,
    Screen,
    Style,
    TextInput,
    Widget,
)
from pymobile.core.ui.gui import skeleton, tkinter_available
from pymobile.core.watcher import FileWatcher


class TestFileWatcher:
    def test_reports_a_modified_file(self, tmp_path: Path) -> None:
        source = tmp_path / "main.py"
        source.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher([tmp_path])
        assert watcher.poll() == []

        source.write_text("x = 2\n", encoding="utf-8")
        assert watcher.poll() == [source]

    def test_same_second_edit_is_still_seen(self, tmp_path: Path) -> None:
        """mtime alone misses a second write inside the same clock tick."""
        source = tmp_path / "main.py"
        source.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher([tmp_path])
        source.write_text("x = 22\n", encoding="utf-8")  # different size
        assert watcher.poll() == [source]

    def test_new_and_deleted_files_are_reported(self, tmp_path: Path) -> None:
        watcher = FileWatcher([tmp_path])
        created = tmp_path / "extra.py"
        created.write_text("y = 1\n", encoding="utf-8")
        assert watcher.poll() == [created]

        created.unlink()
        assert watcher.poll() == [created]

    def test_irrelevant_files_are_ignored(self, tmp_path: Path) -> None:
        watcher = FileWatcher([tmp_path])
        (tmp_path / "notes.md").write_text("hello\n", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        assert watcher.poll() == []

    def test_cache_directories_are_skipped(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        watcher = FileWatcher([tmp_path])
        (cache / "main.cpython-313.py").write_text("compiled\n", encoding="utf-8")
        assert watcher.poll() == []

    def test_nested_sources_are_watched(self, tmp_path: Path) -> None:
        package = tmp_path / "app" / "screens"
        package.mkdir(parents=True)
        watcher = FileWatcher([tmp_path])
        nested = package / "home.py"
        nested.write_text("pass\n", encoding="utf-8")
        assert watcher.poll() == [nested]

    def test_a_single_file_can_be_watched(self, tmp_path: Path) -> None:
        source = tmp_path / "main.py"
        source.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher([source])
        source.write_text("x = 3\n", encoding="utf-8")
        assert watcher.poll() == [source]

    def test_missing_root_is_not_fatal(self, tmp_path: Path) -> None:
        assert FileWatcher([tmp_path / "nope"]).poll() == []

    def test_wait_returns_when_something_changes(self, tmp_path: Path) -> None:
        source = tmp_path / "main.py"
        source.write_text("x = 1\n", encoding="utf-8")
        watcher = FileWatcher([tmp_path], interval=0.01)

        import threading

        def edit() -> None:
            time.sleep(0.05)
            source.write_text("x = 2\n", encoding="utf-8")

        threading.Thread(target=edit, daemon=True).start()
        assert watcher.wait() == [source]


class _Home(Screen):
    title = "Home"

    def build(self) -> Widget:
        self.label = Label("0")
        self.field = TextInput()
        return Column(self.label, self.field, Button("Tap"))


class TestSkeleton:
    """The fingerprint that decides patch-vs-rebuild in the GUI preview."""

    def test_value_changes_keep_the_same_skeleton(self) -> None:
        screen = _Home()
        before = skeleton(screen.to_dict())
        screen.label.text = "42"
        assert skeleton(screen.to_dict()) == before

    def test_structural_changes_alter_the_skeleton(self) -> None:
        screen = _Home()
        before = skeleton(screen.to_dict())
        root = screen.root
        assert isinstance(root, Column)
        root.add(Label("new"))
        assert skeleton(screen.to_dict()) != before

    def test_visibility_alters_the_skeleton(self) -> None:
        screen = _Home()
        before = skeleton(screen.to_dict())
        screen.label.visible = False
        assert skeleton(screen.to_dict()) != before

    def test_different_screens_differ(self) -> None:
        class Other(Screen):
            def build(self) -> Widget:
                return Column(Label("other"))

        assert skeleton(_Home().to_dict()) != skeleton(Other().to_dict())


class TestGuiAvailability:
    def test_probe_does_not_raise(self) -> None:
        assert isinstance(tkinter_available(), bool)

    @pytest.mark.skipif(not tkinter_available(), reason="Tkinter is not installed")
    def test_bridge_records_and_forwards(self) -> None:
        """The GUI bridge stays a StubBridge, so tests keep working headless."""
        from pymobile.core.bridge import GuiBridge

        bridge = GuiBridge(verbose=False)
        app = App("Demo", bridge=bridge)
        app.run(_Home())
        assert bridge.calls_named("render")
        assert bridge.last_tree is not None
        app.toast("hi")
        assert bridge.calls_named("toast")


class _WebHome(Screen):
    title = "WebHome"

    def __init__(self) -> None:
        super().__init__()
        self.taps = 0

    def build(self) -> Widget:
        self.counter = Label(str(self.taps))
        self.field = TextInput(placeholder="name")
        return Column(self.counter, self.field, Button("Tap", on_press=self.tap))

    def tap(self) -> None:
        self.taps += 1
        self.counter.text = str(self.taps)


class TestWebRendering:
    """The browser preview renders the same tree the phone receives."""

    def _tree(self) -> dict[str, Any]:
        return _WebHome().to_dict()

    def test_widgets_become_html(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(self._tree())
        assert "<button" in html and "Tap" in html
        assert "<input" in html and 'placeholder="name"' in html

    def test_ids_are_carried_into_the_page(self) -> None:
        from pymobile.core.ui.web import render_html

        assert 'data-wid="counter"' in render_html(self._tree())

    def test_grid_uses_css_grid(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Grid(Label("a"), Label("b"), columns=2).to_dict())
        assert "grid-template-columns:repeat(2,1fr)" in html

    def test_expanded_becomes_flex(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Row(Expanded(Label("a"), flex=2)).to_dict())
        assert "flex:2 2 0" in html

    def test_hidden_widgets_are_not_rendered(self) -> None:
        from pymobile.core.ui.web import render_html

        assert render_html(Label("secret", visible=False).to_dict()) == ""

    def test_text_is_escaped(self) -> None:
        """A label must never be able to inject markup into the page."""
        from pymobile.core.ui.web import render_html

        html = render_html(Label("<script>alert(1)</script>").to_dict())
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_android_colour_is_converted_for_css(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(Label("x", style=Style(color="#80FF0000")).to_dict())
        assert "#FF000080" in html  # #AARRGGBB -> #RRGGBBAA


class TestWebPreviewServer:
    """End-to-end over real HTTP, the way a browser talks to it."""

    def _serve(self) -> tuple[App, _WebHome, Any, int]:
        from pymobile.core.bridge import WebBridge, set_bridge
        from pymobile.core.ui.web import WebPreview

        web = WebBridge(verbose=False)
        set_bridge(web)
        app = App("WebDemo", bridge=web)
        screen = _WebHome()
        app.run(screen)
        preview = WebPreview(app, port=0)
        web.attach(preview)
        port = preview.start_background()
        return app, screen, preview, port

    def test_page_and_click_round_trip(self) -> None:
        import json
        import urllib.request

        app, screen, preview, port = self._serve()
        try:
            base = f"http://127.0.0.1:{port}"
            page = urllib.request.urlopen(base, timeout=5).read().decode()
            assert "WebHome" in page

            state = json.loads(urllib.request.urlopen(f"{base}/state?v=0", timeout=5).read())
            assert state["title"] == "WebHome"

            button = screen.root.children[2].id
            request = urllib.request.Request(
                f"{base}/event",
                method="POST",
                data=json.dumps({"id": button, "kind": "press", "value": ""}).encode(),
                headers={"Content-Type": "application/json"},
            )
            after = json.loads(urllib.request.urlopen(request, timeout=5).read())
            assert screen.taps == 1
            assert after["version"] > state["version"]
        finally:
            preview.stop()
            app.stop()

    def test_version_only_moves_when_something_changes(self) -> None:
        import json
        import urllib.request

        app, _screen, preview, port = self._serve()
        try:
            base = f"http://127.0.0.1:{port}"
            first = json.loads(urllib.request.urlopen(f"{base}/state", timeout=5).read())
            second = json.loads(urllib.request.urlopen(f"{base}/state", timeout=5).read())
            assert first["version"] == second["version"]
        finally:
            preview.stop()
            app.stop()

    def test_the_phone_shell_follows_a_theme_change(self) -> None:
        """set_theme() must repaint the frame too — over /state, not a reload."""
        import json
        import urllib.request

        app, _screen, preview, port = self._serve()
        try:
            base = f"http://127.0.0.1:{port}"
            page = urllib.request.urlopen(base, timeout=5).read().decode()
            assert "--phone-bg" in page         # the rules read the variables
            assert "applyChrome" in page        # and the poll loop rewrites them

            light = json.loads(urllib.request.urlopen(f"{base}/state", timeout=5).read())
            assert light["chrome"]["phone_bg"] == "#FFFFFF"
            assert light["chrome"]["color_scheme"] == "light"

            app.set_theme("dark")
            dark = json.loads(urllib.request.urlopen(f"{base}/state", timeout=5).read())
            assert dark["chrome"]["phone_bg"] == "#121212"
            assert dark["chrome"]["page_bg"] == "#121212"
            assert dark["chrome"]["color_scheme"] == "dark"
        finally:
            preview.stop()
            app.stop()
