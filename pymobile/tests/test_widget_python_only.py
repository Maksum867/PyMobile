"""``pymobile widget add --python``: a custom widget without any Java."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile.cli import main
from pymobile.compiler.widgets import parse_props, python_only_widget_source
from pymobile.core.ui.registry import unknown_types
from pymobile.errors import ConfigError


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    assert main(["init", str(root), "-n", "Demo", "-p", "org.example.demo"]) == 0
    return root


def _load(source: str, name: str) -> dict[str, object]:
    namespace: dict[str, object] = {"__name__": name}
    exec(compile(source, name, "exec"), namespace)  # generated test source
    return namespace


def test_python_only_widget_serialises_as_a_canvas() -> None:
    source = python_only_widget_source("Gauge", parse_props(["title", "value:int"]))
    module = _load(source, "gauge")
    node = module["Gauge"]("Steps", 7).to_dict()  # type: ignore[operator]
    assert node["type"] == "Canvas"
    assert unknown_types(node) == frozenset()
    assert "drawing" in node["props"]


def test_props_appear_in_the_drawing() -> None:
    source = python_only_widget_source("Gauge", parse_props(["title", "value:int"]))
    gauge = _load(source, "gauge")["Gauge"]("Steps", 7)  # type: ignore[operator]
    rendered = repr(gauge.to_dict())
    assert "title: Steps" in rendered
    assert "value: 7" in rendered


def test_list_props_are_copied_and_counted() -> None:
    source = python_only_widget_source("Trend", parse_props(["points:list"]))
    trend = _load(source, "trend")["Trend"]([1, 2, 3])  # type: ignore[operator]
    assert trend.points == [1, 2, 3]
    assert "points: 3" in repr(trend.to_dict())


def test_widget_with_no_props_still_builds() -> None:
    source = python_only_widget_source("Badge", ())
    assert _load(source, "badge")["Badge"]().to_dict()["type"] == "Canvas"  # type: ignore[operator]


def test_canvas_argument_names_are_refused() -> None:
    with pytest.raises(ConfigError, match="clash with the Canvas arguments"):
        python_only_widget_source("Wide", parse_props(["width"]))


def test_cli_writes_only_the_python_file(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert main(["widget", "add", "Gauge", "--python", "-p", "value:int", "-c", str(root)]) == 0
    assert (root / "widgets" / "gauge.py").is_file()
    assert not (root / "java" / "org" / "pymobile" / "app" / "widgets").exists()


def test_cli_refuses_to_overwrite_an_existing_python_widget(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert main(["widget", "add", "Gauge", "--python", "-c", str(root)]) == 0
    assert main(["widget", "add", "Gauge", "--python", "-c", str(root)]) == 1


def test_default_widget_add_still_creates_the_java_renderer(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert main(["widget", "add", "Gauge", "-c", str(root)]) == 0
    assert (root / "widgets" / "gauge.py").is_file()
    assert (root / "java" / "org" / "pymobile" / "app" / "widgets" / "GaugeRenderer.java").is_file()
