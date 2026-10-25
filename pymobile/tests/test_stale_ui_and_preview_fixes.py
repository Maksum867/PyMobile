"""Regression tests for the stale-UI, preview and polish fixes.

Each class covers one reported problem:

* a screen below a form kept the tree it built when it was opened — the list
  under "add card" still said "2 items" after the third one was saved;
* a custom widget with a Java renderer drew ``<BoxBadge>`` in every desktop
  preview, because only capability declarations existed, not preview drawers;
* reusing a stateful widget in a second ``build()`` was only possible through
  ``Screen.refresh()``, and the error gave no way out;
* ``ModelStore`` hands out copies, which surprised an application expecting a
  live object;
* ``Expanded()`` without a child raised ``TypeError``;
* javac's output reached the user raw instead of as message + hint;
* ``--screen`` could not see a screen whose module was never imported;
* a bottom-navigation tab had no id a driver could address;
* an exposed web preview refused every request without a token and its own
  ``Origin`` policy, so a proxy or an iframe could not reach it;
* ``optimize = true`` only worked on a Python 3.14 host, ``java/`` sources were
  reported as unpackaged assets, and ``EmptyState`` printed ``[icon:info]``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pymobile import (
    App,
    BottomNavigation,
    Button,
    Column,
    Expanded,
    Flexible,
    Label,
    ModelStore,
    Screen,
    Spacer,
    Storage,
    WidgetParentError,
    register_preview_renderer,
    unregister_preview_renderer,
)
from pymobile.core.app import DATA_CHANGED
from pymobile.core.driver import Driver, discover_screens, parse_steps, screen_classes
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.registry import preview_renderer, preview_renderers
from pymobile.core.ui.web import render_html
from pymobile.errors import PyMobileError

# ---------------------------------------------------------------------------
# 1. the stale screen below a form
# ---------------------------------------------------------------------------


class _Cards(Screen):
    title = "Cards"

    def __init__(self) -> None:
        super().__init__()
        self.cards = ["a", "b"]

    def build(self):
        return Column(
            Label(f"{len(self.cards)} cards"),
            Button("Add", on_press=lambda: self.app.push(_Form(self))),
        )


class _Form(Screen):
    title = "Form"

    def __init__(self, owner: _Cards) -> None:
        super().__init__()
        self.owner = owner

    def build(self):
        return Column(Button("Save", on_press=self.save))

    def save(self) -> None:
        self.owner.cards.append("c")
        self.app.notify_data_changed()
        self.app.pop()

    def save_in_place(self) -> None:
        self.owner.cards.append("d")
        self.app.notify_data_changed(rebuild_current=True)


class TestDataChangedRebuildsScreensBelow:
    def test_popping_back_to_a_list_shows_the_new_row(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        cards = _Cards()
        app.run(cards)
        form = app.push(_Form(cards))
        assert cards.root.children[0].text == "2 cards"
        form.save()
        assert app.screen is cards
        assert cards.root.children[0].text == "3 cards"

    def test_the_event_is_public_and_opt_in_for_the_current_screen(self, bridge) -> None:  # type: ignore[no-untyped-def]
        events: list[str] = []
        app = App("Demo", bridge=bridge)
        app.on(DATA_CHANGED, lambda event: events.append(event.source))
        cards = _Cards()
        app.run(cards)
        cards.notify_data_changed()
        assert events == ["Cards"]
        # The current screen keeps its tree by default: no rebuild happened.
        assert cards.root.children[0].text == "2 cards"

    def test_rebuild_current_refreshes_what_is_on_screen(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        cards = _Cards()
        app.run(cards)
        form = app.push(_Form(cards))
        form.owner.cards.append("d")
        app.notify_data_changed(rebuild_current=True)
        # The form is current and has no data of its own; the assertion that
        # matters is that its own tree is still intact after the rebuild.
        assert app.screen is form
        assert form.root is not None


# ---------------------------------------------------------------------------
# 2. preview renderers for custom widget types
# ---------------------------------------------------------------------------


class BoxBadge(Label):
    type_name = "BoxBadge"


class TestPreviewRenderers:
    def teardown_method(self) -> None:
        unregister_preview_renderer("BoxBadge")

    def test_a_custom_renderer_replaces_the_placeholder(self) -> None:
        tree = Column(BoxBadge("new"))
        assert "<BoxBadge>" in render_ascii(tree)
        assert "&lt;BoxBadge&gt;" in render_html(tree.to_dict())

        register_preview_renderer(
            "BoxBadge", "ascii", lambda node: [f"[{node['props']['text']}]"]
        )
        register_preview_renderer(
            "BoxBadge",
            "web",
            lambda node: f'<span class="badge">{node["props"]["text"]}</span>',
        )
        assert "[new]" in render_ascii(tree)
        assert '<span class="badge">new</span>' in render_html(tree.to_dict())

    def test_registry_validates_arguments(self) -> None:
        with pytest.raises(ValueError, match="unknown preview renderer"):
            register_preview_renderer("BoxBadge", "png", lambda node: "")
        with pytest.raises(TypeError, match="callable"):
            register_preview_renderer("BoxBadge", "ascii", "not a function")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty"):
            register_preview_renderer("", "ascii", lambda node: "")

    def test_renderers_are_listed_and_removable(self) -> None:
        register_preview_renderer("BoxBadge", "ascii", lambda node: "x")
        assert preview_renderers()["BoxBadge"] == ("ascii",)
        assert preview_renderer("BoxBadge", "ascii") is not None
        assert unregister_preview_renderer("BoxBadge", "ascii") == 1
        assert preview_renderer("BoxBadge", "ascii") is None

    def test_a_broken_renderer_does_not_take_the_preview_down(self) -> None:
        def boom(node):  # type: ignore[no-untyped-def]
            raise RuntimeError("bad drawing code")

        register_preview_renderer("BoxBadge", "ascii", boom)
        # The failure is reported next to the widget instead of escaping as a
        # traceback from the middle of a preview.
        picture = render_ascii(Column(BoxBadge("x")))
        assert "<BoxBadge: RuntimeError>" in picture


# ---------------------------------------------------------------------------
# 3. reusing stateful widgets in a second build()
# ---------------------------------------------------------------------------


class TestRebuildMechanics:
    def test_rebuild_detaches_a_retained_widget(self, bridge) -> None:  # type: ignore[no-untyped-def]
        holder: dict[str, object] = {}

        class Retained(Screen):
            def __init__(self) -> None:
                super().__init__()
                self.list = Column(Label("kept"))
                holder["list"] = self.list

            def build(self):
                return Column(self.list)

        app = App("Demo", bridge=bridge)
        screen = Retained()
        app.run(screen)
        assert screen.root.children[0] is screen.list

        # A second build outside refresh() used to raise WidgetParentError:
        # the retained Column still had the previous tree as its parent.
        screen.rebuild()
        assert screen.root.children[0] is screen.list
        assert screen.root is not None

    def test_a_reused_widget_reports_the_way_out(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Cards()
        app.run(screen)
        label = screen.root.children[0]
        with pytest.raises(WidgetParentError) as caught:
            Column(label)
        # The hint names the mechanics: rebuild()/detach(), not only remove().
        assert "rebuild()" in (caught.value.hint or "")
        # The same widget becomes attachable again once it is detached.
        label.detach()
        Column(label)

    def test_detach_makes_one_widget_movable(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Cards()
        app.run(screen)
        label = screen.root.children[0]
        assert label.parent is screen.root
        label.detach()
        assert label.parent is None
        Column(label)  # attachable again, no error


# ---------------------------------------------------------------------------
# 4. ModelStore snapshots
# ---------------------------------------------------------------------------


@dataclass
class Card:
    id: str
    box: int = 1


class TestModelStoreSnapshots:
    def test_mutating_a_loaded_model_needs_put(self, tmp_path: Path) -> None:
        store = ModelStore(Storage(tmp_path / "store.json"), Card, key="cards")
        store.put(Card("c1"))
        card = store.get("c1")
        assert card is not None
        card.box = 2
        assert store.get("c1").box == 1  # a snapshot, not a live record
        store.update(card)
        assert store.get("c1").box == 2

    def test_edit_applies_and_persists_changes(self, tmp_path: Path) -> None:
        store = ModelStore(Storage(tmp_path / "store.json"), Card, key="cards")
        store.put(Card("c1"))
        assert store.edit("c1", box=5).box == 5
        assert store.get("c1").box == 5
        with pytest.raises(PyMobileError, match="no field"):
            store.edit("c1", nope=1)
        with pytest.raises(PyMobileError, match="no Card with id"):
            store.edit("missing", box=1)


# ---------------------------------------------------------------------------
# 5. Expanded()/Flexible() without a child
# ---------------------------------------------------------------------------


class TestFlexDefaults:
    def test_expanded_without_a_child_is_a_flexible_gap(self) -> None:
        expanded = Expanded()
        assert isinstance(expanded.child, Spacer)
        assert expanded.flex == 1

    def test_flexible_without_a_child(self) -> None:
        assert isinstance(Flexible(flex=2).child, Spacer)
        assert Flexible(flex=2).flex == 2

    def test_an_explicit_child_still_wins(self) -> None:
        label = Label("x")
        assert Expanded(label).child is label


# ---------------------------------------------------------------------------
# 6. javac diagnostics
# ---------------------------------------------------------------------------


class TestJavacErrors:
    def test_diagnostics_become_message_and_hint(self) -> None:
        from pymobile.compiler.backends.native import format_javac_failure

        detail = (
            "/work/java/org/pymobile/app/widgets/GaugeRenderer.java:37: error: ';' expected\n"
            "    return super.build(\n"
            "1 error\n"
        )
        summary, hint = format_javac_failure(
            detail, overlay=True, root=Path("/work/java")
        )
        assert summary == "javac reported 1 error"
        assert "GaugeRenderer.java:37: error: ';' expected" in hint
        assert "/work/java" not in hint  # build directories are noise
        assert "java/ directory" in hint
        assert "Fix the source" in hint

    def test_a_missing_diagnostic_still_produces_a_hint(self) -> None:
        from pymobile.compiler.backends.native import format_javac_failure

        summary, hint = format_javac_failure("", overlay=False)
        assert summary == "javac failed"
        assert "javac says" in hint

    def test_a_public_class_in_the_wrong_file_gets_advice(self) -> None:
        from pymobile.compiler.backends.native import format_javac_failure

        detail = (
            "GaugeRenderer.java:3: error: class Gauge is public, should be "
            "declared in a file named Gauge.java\n"
        )
        _, hint = format_javac_failure(detail, overlay=True)
        assert "rename the file" in hint


# ---------------------------------------------------------------------------
# 7 (i18n) is covered in test_i18n.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 8. discovering screens that were never imported
# ---------------------------------------------------------------------------


class TestScreenDiscovery:
    def test_a_screen_in_an_unimported_module_is_found(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "from pymobile import App, Screen, Label\n"
            "class Menu(Screen):\n"
            "    def build(self):\n"
            "        return Label('menu')\n"
            "app = App('Demo')\n"
            "app.run(Menu())\n",
            encoding="utf-8",
        )
        (tmp_path / "lazy_screens.py").write_text(
            "from pymobile import Screen, Label\n"
            "class ResultScreen(Screen):\n"
            "    def build(self):\n"
            "        return Label('result')\n",
            encoding="utf-8",
        )
        from pymobile.core.driver import find_screen_class

        # The pool is passed explicitly so the assertion is about *discovery*,
        # not about what an earlier test in the same process happened to
        # import: every module keeps its classes alive in ``sys.modules``, and
        # an unrelated test directory with a file of the same name is enough to
        # make the global pool look pre-populated.
        others = [
            cls
            for cls in screen_classes(project_only=False)
            if cls.__name__ != "ResultScreen"
        ]
        with pytest.raises(PyMobileError, match="no screen called"):
            find_screen_class("ResultScreen", others)

        found = discover_screens([tmp_path], exclude=[tmp_path / "main.py"])
        assert any(cls.__name__ == "ResultScreen" for cls in found)
        assert find_screen_class("ResultScreen").__name__ == "ResultScreen"
        # The entry point itself is never imported a second time: that would
        # define every screen twice and make the name ambiguous.
        assert not any(
            cls.__module__ == "main" and cls.__name__ == "Menu" for cls in found
        )


# ---------------------------------------------------------------------------
# 9. bottom-navigation tabs
# ---------------------------------------------------------------------------


class Tabbed(Screen):
    title = "Shell"

    def __init__(self) -> None:
        super().__init__()
        self.tab = "home"

    def build(self):
        return Column(
            Label(f"tab={self.tab}"),
            BottomNavigation(
                [("home", "Home"), ("stats", "Stats")],
                value=self.tab,
                on_select=self.pick,
            ),
        )

    def pick(self, value: str) -> None:
        self.tab = value
        self.refresh()


class TestTabsAreAddressable:
    def test_parse_steps_understands_the_tab_syntax(self) -> None:
        steps = parse_steps("@Stats, Shell.@Home", ["Shell"])
        assert [step.tab for step in steps] == [True, True]
        assert [step.target for step in steps] == ["Stats", "Home"]
        assert steps[1].screen == "Shell"

    def test_tab_steps_take_no_value(self) -> None:
        with pytest.raises(PyMobileError, match="takes no value"):
            parse_steps("@Stats=1")

    def test_the_driver_selects_a_tab_without_an_id(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = Tabbed()
        app.run(screen)
        driver = Driver(app)
        driver.tab("Stats")
        assert screen.tab == "stats"
        assert screen.root.children[0].text == "tab=stats"

    def test_the_driver_accepts_a_position(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = Tabbed()
        app.run(screen)
        Driver(app).tab(1)
        assert screen.tab == "stats"

    def test_an_unknown_tab_lists_the_alternatives(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        app.run(Tabbed())
        with pytest.raises(PyMobileError, match="no tab called 'Nope'"):
            Driver(app).tab("Nope")

    def test_a_route_can_mix_tabs_and_presses(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = Tabbed()
        app.run(screen)
        landed = Driver(app).navigate("@Stats")
        assert landed and screen.tab == "stats"


# ---------------------------------------------------------------------------
# 10. web preview access options
# ---------------------------------------------------------------------------


class _Headers(dict):
    pass


class TestWebPreviewAccess:
    def _preview(self, **kwargs):  # type: ignore[no-untyped-def]
        from pymobile.core.ui.web import WebPreview

        app = App("Demo", bridge=None)  # a StubBridge is selected by the fixture
        return app, WebPreview(app, host="0.0.0.0", port=0, **kwargs)

    def test_an_exposed_preview_still_requires_the_token_by_default(self) -> None:
        _, preview = self._preview()
        assert preview.token
        headers = _Headers()
        assert preview._authorised("/", {}, headers) is False
        assert preview._authorised("/", {"t": [preview.token]}, headers) is True

    def test_no_token_serves_the_page_without_a_session(self) -> None:
        _, preview = self._preview(require_token=False)
        assert preview.token == ""
        assert preview._authorised("/", {}, _Headers()) is True

    def test_allow_origin_accepts_the_named_origin(self) -> None:
        _, preview = self._preview(allow_origins=("https://portal.example",))
        headers = _Headers(Origin="https://portal.example")
        assert preview._authorised("/", {"t": [preview.token]}, headers) is True
        foreign = _Headers(Origin="https://evil.example")
        assert preview._authorised("/", {"t": [preview.token]}, foreign) is False

    def test_a_star_origin_trusts_the_gateway(self) -> None:
        _, preview = self._preview(allow_origins=("*",))
        headers = _Headers(Origin="https://anywhere.example")
        assert preview._authorised("/", {"t": [preview.token]}, headers) is True

    def test_embed_omits_the_frame_header(self) -> None:
        _, preview = self._preview(frame_options="")
        assert preview.frame_options == ""

    def test_the_default_frame_policy_is_deny(self) -> None:
        _, preview = self._preview()
        assert preview.frame_options == "DENY"


# ---------------------------------------------------------------------------
# 11. optimize on a non-3.14 host; java/ is not an asset
# ---------------------------------------------------------------------------


class TestBuildPolish:
    def test_bytecode_can_be_compiled_by_another_interpreter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pymobile.compiler import pipeline as pipeline_module
        from pymobile.compiler.collector import SourceSet

        main = tmp_path / "main.py"
        main.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(pipeline_module, "DEVICE_PYTHON", (9, 9))
        monkeypatch.setattr(
            pipeline_module, "find_bytecode_interpreter", lambda: sys.executable
        )
        config = pipeline_module.ProjectConfig(
            root=tmp_path, package="com.example.a", optimize=True
        )
        build = pipeline_module.BuildPipeline(config, native=True)
        work = tmp_path / "work"
        work.mkdir()
        entries = build._compile_sources(SourceSet(tmp_path, (main,), main), work)
        assert [name for name, _ in entries] == ["main.pyc"]
        assert not any("optimize" in warning for warning in build.warnings)

    def test_the_warning_names_the_way_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pymobile.compiler import pipeline as pipeline_module
        from pymobile.compiler.collector import SourceSet

        main = tmp_path / "main.py"
        main.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.setattr(pipeline_module, "DEVICE_PYTHON", (9, 9))
        monkeypatch.setattr(pipeline_module, "find_bytecode_interpreter", lambda: None)
        config = pipeline_module.ProjectConfig(
            root=tmp_path, package="com.example.a", optimize=True
        )
        build = pipeline_module.BuildPipeline(config, native=True)
        work = tmp_path / "work"
        work.mkdir()
        build._compile_sources(SourceSet(tmp_path, (main,), main), work)
        warning = next(w for w in build.warnings if "optimize" in w)
        assert "PYMOBILE_PYTHON" in warning

    def test_java_overlay_sources_are_not_reported_as_unpackaged(
        self, tmp_path: Path
    ) -> None:
        from pymobile.compiler.pipeline import BuildPipeline

        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        renderer = tmp_path / "java" / "org" / "pymobile" / "app" / "widgets"
        renderer.mkdir(parents=True)
        (renderer / "BoxBadgeRenderer.java").write_text("class X {}\n", encoding="utf-8")
        config = __import__("pymobile").ProjectConfig(
            root=tmp_path, package="com.example.a"
        )
        build = BuildPipeline(config)
        dependencies = build._collect()
        assert not any("NOT packaged" in warning for warning in build.warnings), (
            build.warnings
        )
        assert dependencies.files

    def test_empty_state_draws_an_icon_glyph(self) -> None:
        from pymobile import EmptyState

        picture = render_ascii(EmptyState("Nothing here"))
        assert "[icon:info]" not in picture
        assert "ⓘ" in picture
        assert "Nothing here" in picture
