"""Tests for the widget registry and the unknown-type guards.

A custom ``type_name`` with no branch in ``ViewBuilder.java`` is drawn as an
empty view on the phone while the previews look fine. These tests pin down the
three places that now say so: the registry helpers, the runtime warning in
``App`` and the build-time scan in ``BuildPipeline``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile import App, Column, Screen, Widget
from pymobile.compiler.pipeline import build_apk
from pymobile.core.bridge import StubBridge
from pymobile.core.config import ProjectConfig
from pymobile.core.ui.registry import (
    declared_types,
    is_known,
    known_types,
    register_widget_type,
    supported_by,
    unknown_types,
    unregister_widget_type,
    widget_types,
)


@pytest.fixture(autouse=True)
def _clean_declarations() -> None:
    """Tests register types; nothing may leak into the next one."""
    yield
    for name in declared_types():
        unregister_widget_type(name)


class BarChart(Widget):
    """A custom widget with a type name the native renderer does not know."""

    type_name = "BarChart"

    def props(self):  # type: ignore[no-untyped-def]
        return {**super().props(), "text": "chart"}


class NativeStubBridge(StubBridge):
    """A stub that claims to feed the Java renderer, like AndroidBridge does."""

    name = "android"
    native_widgets = True


class TestRegistry:
    def test_builtins_are_known(self) -> None:
        assert "Label" in widget_types()
        assert is_known("Label")
        assert is_known("Label", renderer="android")

    def test_a_custom_type_is_unknown_until_declared(self) -> None:
        assert not is_known("BarChart")
        register_widget_type("BarChart")
        assert is_known("BarChart")
        assert "BarChart" in declared_types()
        assert "BarChart" in known_types()
        assert "BarChart" in supported_by("android")

    def test_a_declaration_can_be_removed(self) -> None:
        register_widget_type("Sparkline")
        assert unregister_widget_type("Sparkline")
        assert not unregister_widget_type("Sparkline")
        assert not is_known("Sparkline")

    def test_declaring_a_builtin_is_refused(self) -> None:
        with pytest.raises(ValueError, match="built-in"):
            register_widget_type("Label")

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            register_widget_type("   ")

    def test_a_preview_only_type_is_not_supported_on_android(self) -> None:
        register_widget_type("Sparkline", android=False, web=True)
        assert is_known("Sparkline", renderer="web")
        assert not is_known("Sparkline", renderer="android")

    def test_unknown_renderer_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown renderer"):
            supported_by("canvas")

    def test_unknown_types_walks_the_tree(self) -> None:
        tree = {
            "type": "Column",
            "children": [
                {"type": "Label"},
                {"type": "BarChart", "children": [{"type": "Legend"}]},
            ],
        }
        assert unknown_types(tree) == frozenset({"BarChart", "Legend"})
        assert unknown_types(tree, renderer="android") == frozenset({"BarChart", "Legend"})

    def test_helpers_are_exported_from_the_package(self) -> None:
        import pymobile

        assert pymobile.unknown_types({"type": "BarChart"}) == frozenset({"BarChart"})
        pymobile.register_widget_type("Legend")
        try:
            assert pymobile.unknown_types({"type": "Legend"}) == frozenset()
        finally:
            pymobile.unregister_widget_type("Legend")

    def test_unknown_types_ignores_malformed_nodes(self) -> None:
        tree = {"type": "Column", "children": ["not a node", None, {"no_type": True}]}
        assert unknown_types(tree) == frozenset()

    def test_a_declared_type_stops_being_unknown(self) -> None:
        register_widget_type("BarChart")
        assert unknown_types(BarChart().to_dict()) == frozenset()


class TestRuntimeWarning:
    def _screen(self) -> Screen:
        class Chart(Screen):
            def build(self) -> Widget:
                return Column(BarChart())

        return Chart()

    def test_a_native_bridge_reports_an_unknown_type(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        app = App("Demo", bridge=NativeStubBridge(verbose=False))
        app.run(self._screen())
        err = capsys.readouterr().err
        assert "BarChart" in err
        assert "ViewBuilder.java" in err
        assert "register_widget_type" in err

    def test_the_warning_is_emitted_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        from pymobile.logging import configure

        configure("warning")
        app = App("Demo", bridge=NativeStubBridge(verbose=False))
        app.run(self._screen())
        capsys.readouterr()
        app.render()
        app.render()
        assert "no branch in the native renderer" not in capsys.readouterr().err

    def test_a_preview_bridge_says_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The desktop previews draw what they are handed — no warning there."""
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        app = App("Demo", bridge=StubBridge(verbose=False))
        app.run(self._screen())
        assert "ViewBuilder.java" not in capsys.readouterr().err

    def test_declaring_the_type_silences_the_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        register_widget_type("BarChart")
        capsys.readouterr()
        app = App("Demo", bridge=NativeStubBridge(verbose=False))
        app.run(self._screen())
        assert "ViewBuilder.java" not in capsys.readouterr().err


class TestBuildWarning:
    def test_a_custom_type_is_reported_before_the_apk_is_written(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "from pymobile import Widget\n\n\n"
            "class BarChart(Widget):\n"
            '    type_name = "BarChart"\n\n'
            "    def props(self):\n"
            '        return {"text": "chart"}\n',
            encoding="utf-8",
        )
        result = build_apk(ProjectConfig(root=tmp_path))
        assert any("BarChart" in warning for warning in result.warnings)
        assert any("ViewBuilder.java" in warning for warning in result.warnings)

    def test_a_declared_type_is_not_reported(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "from pymobile import Widget\n"
            "from pymobile.core.ui.registry import register_widget_type\n\n\n"
            "class BarChart(Widget):\n"
            '    type_name = "BarChart"\n\n'
            "    def props(self):\n"
            '        return {"text": "chart"}\n\n\n'
            'register_widget_type("BarChart")\n',
            encoding="utf-8",
        )
        result = build_apk(ProjectConfig(root=tmp_path))
        assert not any("BarChart" in warning for warning in result.warnings)

    def test_plain_projects_stay_quiet(self, project: ProjectConfig) -> None:
        result = build_apk(project)
        assert not any("no renderer" in warning for warning in result.warnings)
        assert any(label in widget_types() for label in ("Label", "Column"))


class TestJavaRenderer:
    """The renderer itself must not silently drop an unknown type either."""

    def _source(self) -> str:
        root = Path(__file__).resolve().parents[1]
        return (root / "resources/android/java/ViewBuilder.java").read_text(encoding="utf-8")

    def test_the_default_branch_builds_a_visible_placeholder(self) -> None:
        source = self._source()
        assert "buildUnknown(type, props)" in source
        assert "no native renderer" in source
        # The old shape — "case "Label": default:" — is what made a missing
        # branch look like an empty Label on the phone.
        assert 'case "Label":\n            default:' not in source

    def test_the_placeholder_is_logged_and_coloured(self) -> None:
        source = self._source()
        assert 'android.util.Log.w("pymobile", "no native renderer for widget type' in source
        assert "#C62828" in source
