"""The custom-widget path: no silent hole on the phone, and a generator for the Java half."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from pymobile import Widget
from pymobile.cli import main
from pymobile.compiler import pipeline as pipeline_module
from pymobile.compiler.collector import collect_sources
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.widgets import (
    CustomWidgets,
    MissingRendererError,
    dex_has_case,
    dex_has_class,
    java_branch,
    parse_props,
    scan_custom_widgets,
)
from pymobile.core.config import ProjectConfig
from pymobile.core.ui.registry import unknown_types, unregister_widget_type
from pymobile.errors import ConfigError, PyMobileError

PREBUILT_DEX = (
    Path(pipeline_module.__file__).resolve().parents[1]
    / "resources"
    / "android"
    / "prebuilt"
    / "arm64-v8a"
    / "classes.dex"
)


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def scan(root: Path) -> CustomWidgets:
    return scan_custom_widgets(sorted(root.rglob("*.py")))


# --------------------------------------------------------------------------
# scanning the project
# --------------------------------------------------------------------------
class TestScan:
    def test_a_widget_class_with_its_own_type_name(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "main.py",
            """
            from pymobile import Widget

            class BarChart(Widget):
                type_name = "BarChart"
            """,
        )
        found = scan(tmp_path)
        assert found.types == {"BarChart"}
        assert found.undeclared == {"BarChart"}
        assert found.android == {"BarChart"}

    def test_builtin_type_names_are_not_custom(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "main.py",
            """
            from pymobile import Label

            class Title(Label):
                type_name = "Label"
            """,
        )
        assert scan(tmp_path).types == frozenset()

    def test_a_widget_may_derive_from_another_widget_of_the_project(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "base.py",
            """
            from pymobile import Container

            class Card(Container):
                type_name = "Card"
            """,
        )
        write(
            tmp_path,
            "main.py",
            """
            from base import Card

            class FancyCard(Card):
                type_name = "FancyCard"
            """,
        )
        assert scan(tmp_path).types == {"Card", "FancyCard"}

    def test_qualified_bases_and_annotated_assignments(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "main.py",
            """
            import pymobile

            class Gauge(pymobile.Widget):
                type_name: str = "Gauge"
            """,
        )
        assert scan(tmp_path).types == {"Gauge"}

    def test_an_unrelated_class_with_a_type_name_is_left_alone(self, tmp_path: Path) -> None:
        """The scan used to be a regex: any ``type_name = "..."`` counted."""
        write(
            tmp_path,
            "main.py",
            """
            class Database:
                type_name = "sqlite"

            class Row2(dict):
                type_name = "record"
            """,
        )
        assert scan(tmp_path).types == frozenset()

    def test_a_dynamic_type_name_is_invisible_not_an_error(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "main.py",
            """
            from pymobile import Widget

            NAME = "Dyn"

            class Dyn(Widget):
                type_name = NAME
            """,
        )
        assert scan(tmp_path).types == frozenset()

    def test_declarations_and_preview_only_types(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "main.py",
            """
            from pymobile import Widget, register_widget_type

            class A(Widget):
                type_name = "A"

            class B(Widget):
                type_name = "B"

            register_widget_type("A")
            register_widget_type("B", android=False, web=True)
            """,
        )
        found = scan(tmp_path)
        assert found.declared == {"A", "B"}
        assert found.preview_only == {"B"}
        assert found.undeclared == frozenset()
        assert found.android == {"A"}  # B is preview-only

    def test_a_file_that_does_not_parse_is_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "broken.py", "def (:\n")
        write(
            tmp_path,
            "main.py",
            """
            from pymobile import Widget

            class Ok(Widget):
                type_name = "Ok"
            """,
        )
        assert scan(tmp_path).types == {"Ok"}

    def test_a_file_with_a_utf8_bom_is_scanned(self, tmp_path: Path) -> None:
        """Notepad and Windows PowerShell write a BOM; a widget there must still count."""
        (tmp_path / "main.py").write_bytes(b"\xef\xbb\xbf" + BAR_CHART.encode("utf-8"))
        assert scan(tmp_path).types == {"BarChart"}

    def test_a_file_in_another_encoding_is_scanned_through_its_coding_line(
        self, tmp_path: Path
    ) -> None:
        source = "# -*- coding: cp1251 -*-\n# Діаграма\n" + BAR_CHART
        (tmp_path / "main.py").write_bytes(source.encode("cp1251"))
        assert scan(tmp_path).types == {"BarChart"}


# --------------------------------------------------------------------------
# asking the dex
# --------------------------------------------------------------------------
class TestDex:
    def test_the_packaged_dex_knows_the_builtin_cases_and_not_a_custom_one(self) -> None:
        dex = PREBUILT_DEX.read_bytes()
        for name in ("Label", "Slider", "Stepper", "Dialog", "SegmentedButtons"):
            assert dex_has_case(dex, name), name
        assert not dex_has_case(dex, "BarChart")

    def test_only_a_whole_string_table_entry_counts(self) -> None:
        entry = b"\x08BarChart\x00"
        assert dex_has_case(b"junk" + entry + b"junk", "BarChart")
        assert not dex_has_case(b"junk\x0aMyBarChart\x00junk", "BarChart")
        assert not dex_has_case(b"junk" + entry, "Chart")
        assert not dex_has_case(b"junk", "")

    def test_a_long_name_uses_a_two_byte_length(self) -> None:
        name = "A" * 130
        assert dex_has_case(b"\x82\x01" + name.encode() + b"\x00", name)

    def test_a_non_ascii_name_falls_back_to_the_bytes(self) -> None:
        assert dex_has_case("графік".encode(), "графік")
        assert not dex_has_case(b"nothing", "графік")


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------
BAR_CHART = textwrap.dedent(
    """
    from pymobile import Widget

    class BarChart(Widget):
        type_name = "BarChart"
    """
)
REGISTER = 'from pymobile import register_widget_type\n\nregister_widget_type("BarChart"{})\n'


def pipeline_for(root: Path, *, native: bool = False) -> BuildPipeline:
    return BuildPipeline(ProjectConfig(root=root), use_cache=False, native=native)


def scanned(pipeline: BuildPipeline, root: Path) -> BuildPipeline:
    pipeline._check_widget_types(
        collect_sources(root, root / "main.py", exclude=pipeline.config.exclude)
    )
    return pipeline


class TestBuildChecks:
    def test_the_structural_build_still_only_warns(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", BAR_CHART)
        pipeline = scanned(pipeline_for(tmp_path), tmp_path)
        (warning,) = pipeline.warnings
        assert "BarChart" in warning
        assert "ViewBuilder.java" in warning
        assert "pymobile widget-java BarChart" in warning

    def test_an_unrelated_type_name_gets_no_warning(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", 'class Config:\n    type_name = "sqlite"\n')
        assert scanned(pipeline_for(tmp_path), tmp_path).warnings == []

    def test_a_native_build_stops_when_the_dex_has_no_branch(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", BAR_CHART)
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        with pytest.raises(ConfigError) as caught:
            pipeline._verify_renderers(PREBUILT_DEX)
        assert "'BarChart'" in str(caught.value)
        assert caught.value.hint is not None
        assert "pymobile widget-java BarChart" in caught.value.hint
        assert "normal `pymobile build --native`" in caught.value.hint
        assert "android=False" in caught.value.hint

    def test_a_project_renderer_class_satisfies_the_dex_check(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", BAR_CHART + REGISTER.format(""))
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        descriptor = b"Lorg/pymobile/app/widgets/BarChartRenderer;"
        dex_string = bytes((len(descriptor),)) + descriptor + b"\0"
        dex = tmp_path / "classes.dex"
        dex.write_bytes(PREBUILT_DEX.read_bytes() + dex_string)
        pipeline._verify_renderers(dex)
        assert dex_has_class(dex.read_bytes(), "org.pymobile.app.widgets.BarChartRenderer")

    def test_a_widget_in_a_file_with_a_bom_does_not_escape_the_native_check(
        self, tmp_path: Path
    ) -> None:
        """The silent hole this whole check exists to close, reopened by an editor's BOM."""
        (tmp_path / "main.py").write_bytes(b"\xef\xbb\xbf" + BAR_CHART.encode("utf-8"))
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        with pytest.raises(ConfigError, match="BarChart"):
            pipeline._verify_renderers(PREBUILT_DEX)

    def test_a_registered_type_is_checked_too(self, tmp_path: Path) -> None:
        """register_widget_type() promises a Java branch; the dex is asked to prove it."""
        write(tmp_path, "main.py", BAR_CHART + REGISTER.format(""))
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        assert not pipeline.warnings  # declared: no "undeclared" warning ...
        with pytest.raises(ConfigError, match="BarChart"):
            pipeline._verify_renderers(PREBUILT_DEX)  # ... but the dex has no branch

    def test_a_declared_type_that_is_not_used_by_a_class_is_still_checked(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path,
            "main.py",
            'from pymobile import register_widget_type\nregister_widget_type("Gauge")\n',
        )
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        with pytest.raises(ConfigError, match="Gauge"):
            pipeline._verify_renderers(PREBUILT_DEX)

    def test_a_dex_that_has_the_branch_passes(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", BAR_CHART)
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        dex = tmp_path / "classes.dex"
        dex.write_bytes(PREBUILT_DEX.read_bytes() + b"\x08BarChart\x00")
        pipeline._verify_renderers(dex)  # no exception

    def test_a_preview_only_type_warns_but_does_not_stop_a_native_build(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "main.py", BAR_CHART + REGISTER.format(", android=False, web=True"))
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        pipeline._verify_renderers(PREBUILT_DEX)  # nothing to stop
        assert any(
            "preview-only" in warning and "BarChart" in warning for warning in pipeline.warnings
        )
        # ... and a structural build does not mention the phone at all
        quiet = scanned(pipeline_for(tmp_path), tmp_path)
        assert not any("preview-only" in warning for warning in quiet.warnings)

    def test_an_app_of_builtin_widgets_is_never_stopped(self, tmp_path: Path) -> None:
        write(tmp_path, "main.py", "from pymobile import Label\nx = Label('hi')\n")
        pipeline = scanned(pipeline_for(tmp_path, native=True), tmp_path)
        pipeline._verify_renderers(PREBUILT_DEX)
        assert pipeline.warnings == []

    def test_the_native_run_asks_the_dex_before_it_links_resources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wired into ``_run_native``: after the dex stage, before anything is packaged."""
        write(tmp_path, "main.py", BAR_CHART)
        calls: list[str] = []
        entrypoints: list[tuple[str, str]] = []

        class FakeToolchain:
            def verify(self, **_: Any) -> None:
                calls.append("verify")

        class FakeBackend:
            def __init__(self, *_: Any, **__: Any) -> None:
                self.warnings: list[str] = []

            def set_entrypoint(self, name: str, kind: str) -> None:
                # The launcher has to know what to run: a custom entry point
                # from the TOML and/or the bytecode-only form.
                entrypoints.append((name, kind))

            def set_payload_digest(self, digest: str) -> None:
                # Recorded in the APK so the launcher re-extracts its bundled
                # Python when the packaged content changes.
                entrypoints.append((digest[:8], "digest"))

            def compile_jni(self, workdir: Path) -> Path:
                calls.append("jni")
                return workdir

            def compile_java(self, workdir: Path) -> Path:
                calls.append("dex")
                target = workdir / "classes.dex"
                target.write_bytes(PREBUILT_DEX.read_bytes())
                return target

            def link_resources(self, *_: Any) -> Path:  # pragma: no cover - must not run
                calls.append("resources")
                raise AssertionError("resources were linked for an APK that cannot render")

        monkeypatch.setattr(pipeline_module, "find_toolchain", lambda *_a, **_k: FakeToolchain())
        monkeypatch.setattr(pipeline_module, "ensure_runtime", lambda *_a, **_k: object())
        monkeypatch.setattr(pipeline_module, "NativeBackend", FakeBackend)
        with pytest.raises(ConfigError, match="BarChart"):
            BuildPipeline(ProjectConfig(root=tmp_path), use_cache=False, native=True).run()
        assert calls == ["verify", "jni", "dex"]
        assert entrypoints[0] == ("main.py", "py")
        assert entrypoints[1][1] == "digest" and entrypoints[1][0]


# --------------------------------------------------------------------------
# the generator
# --------------------------------------------------------------------------
class TestGenerator:
    def test_props_are_parsed_with_a_default_kind(self) -> None:
        props = parse_props(["title", "value:int", "ratio:float", "on:bool", "items:list"])
        assert [(p.name, p.kind) for p in props] == [
            ("title", "str"),
            ("value", "int"),
            ("ratio", "float"),
            ("on", "bool"),
            ("items", "list"),
        ]

    @pytest.mark.parametrize(
        "spec", ["", "1x", "a-b", "_private", "class", "x:decimal", "id", "style", "visible"]
    )
    def test_bad_props_are_refused_with_a_hint(self, spec: str) -> None:
        with pytest.raises(ConfigError) as caught:
            parse_props([spec])
        assert caught.value.hint

    def test_a_prop_listed_twice_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="twice"):
            parse_props(["a", "a:int"])

    def test_the_java_case_and_method(self) -> None:
        branch = java_branch("BarChart", parse_props(["title", "max_length:int", "values:list"]))
        assert branch.case == 'case "BarChart":\n    view = buildBarChart(id, props);\n    break;'
        assert "private View buildBarChart(final String id, JSONObject props)" in branch.method
        assert 'String title = props.optString("title", "");' in branch.method
        assert 'int maxLength = props.optInt("max_length", 0);' in branch.method
        assert 'JSONArray values = props.optJSONArray("values");' in branch.method
        assert 'Native.dispatchEvent(id, "press", "");' in branch.method
        assert 'if ("BarChart".equals(type))' in branch.update

    def test_java_keywords_and_locals_do_not_become_variable_names(self) -> None:
        props = parse_props(["default", "new:bool", "type", "context", "view:int"])
        reads = "\n".join(prop.read for prop in props)
        assert 'String defaultValue = props.optString("default", "");' in reads
        assert 'boolean newValue = props.optBoolean("new", false);' in reads
        assert 'String typeValue = props.optString("type", "");' in reads
        assert 'String contextValue = props.optString("context", "");' in reads
        assert 'int viewValue = props.optInt("view", 0);' in reads

    @pytest.mark.parametrize("name", ["", "Bar-Chart", "1Chart", "Bar Chart", "Chärt"])
    def test_a_bad_type_name_is_refused(self, name: str) -> None:
        with pytest.raises(ConfigError, match="not a usable widget type name"):
            java_branch(name)

    def test_a_builtin_type_name_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="built-in widget type"):
            java_branch("Slider")

    def test_the_guide_lists_every_step_in_order(self) -> None:
        viewbuilder = Path("/pkg/ViewBuilder.java")
        guide = java_branch("BarChart").render(viewbuilder)
        positions = [guide.index(f"\n{step}) ") for step in range(1, 7)]
        assert positions == sorted(positions)
        # The path is printed the way the OS spells it (\pkg\ViewBuilder.java on Windows):
        # that is what the user can paste into a terminal or an editor.
        assert str(viewbuilder) in guide
        assert 'register_widget_type("BarChart")' in guide
        assert "pymobile build --native" in guide

    def test_the_generated_python_widget_really_works(self) -> None:
        branch = java_branch("BarChart", parse_props(["title", "value:int", "values:list"]))
        namespace: dict[str, Any] = {}
        exec(branch.python, namespace)
        try:
            widget = namespace["BarChart"](title="Sales", value=3, values=[1, 2])
            assert isinstance(widget, Widget)
            node = widget.to_dict()
            assert node["type"] == "BarChart"
            assert node["props"] == {"title": "Sales", "value": 3, "values": [1, 2]}
            # Until it is declared, the tree says the renderer has nothing for it.
            assert unknown_types(node) == {"BarChart"}
        finally:
            unregister_widget_type("BarChart")


class TestCommand:
    def test_it_prints_the_guide(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["widget-java", "BarChart", "-p", "title", "-p", "value:int"]) == 0
        out = capsys.readouterr().out
        assert 'case "BarChart":' in out
        assert 'int value = props.optInt("value", 0);' in out
        assert "ViewBuilder.java" in out

    def test_it_can_write_the_guide_to_a_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "bar_chart.txt"
        assert main(["widget-java", "BarChart", "--out", str(target)]) == 0
        assert 'case "BarChart":' in target.read_text(encoding="utf-8")
        assert "wrote" in capsys.readouterr().out

    def test_a_builtin_type_fails_with_a_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["widget-java", "Label"]) == 1
        err = capsys.readouterr().err
        assert "built-in widget type" in err
        assert "hint:" in err

    def test_a_bad_prop_fails_with_a_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["widget-java", "Bar", "-p", "x:decimal"]) == 1
        assert "unknown prop type" in capsys.readouterr().err


class TestNativeBuildMessage:
    """The command line's generic "install the SDK" advice must not hijack this error."""

    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "pymobile.toml").write_text('name = "T"\npackage = "com.example.t"\n', "utf-8")
        monkeypatch.chdir(tmp_path)

    def failing_pipeline(self, error: PyMobileError) -> type:
        class Failing:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            def run(self) -> None:
                raise error

        return Failing

    def test_the_missing_renderer_error_is_a_config_error(self) -> None:
        assert issubclass(MissingRendererError, ConfigError)

    def test_its_hint_is_about_the_widget_not_the_sdk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.project(tmp_path, monkeypatch)
        error = MissingRendererError("no renderer", hint="run pymobile widget-java BarChart")
        monkeypatch.setattr("pymobile.cli.BuildPipeline", self.failing_pipeline(error))
        assert main(["build", "--native"]) == 1
        err = capsys.readouterr().err
        assert "widget-java BarChart" in err
        assert "setup-sdk" not in err

    def test_other_native_errors_keep_the_sdk_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.project(tmp_path, monkeypatch)
        error = PyMobileError("something native broke", hint="look at the log")
        monkeypatch.setattr("pymobile.cli.BuildPipeline", self.failing_pipeline(error))
        assert main(["build", "--native"]) == 1
        assert "setup-sdk" in capsys.readouterr().err
