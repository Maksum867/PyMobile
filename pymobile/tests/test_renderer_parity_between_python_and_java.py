"""The widget types Python can serialise stay in step with the Android renderer.

``ViewBuilder.java`` has one branch per built-in type. A type that gains a Python
widget but no Java branch draws as a placeholder on the phone while every
desktop preview looks right — the failure the audit calls "Android behaviour is
not checked in CI". These tests are the CI half that needs no SDK: they compare
the capability table, the two preview tables and the compiled Java source, and
they check that every native call the framework makes still has a Java entry
point.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from pymobile.core.bridge.base import Bridge
from pymobile.core.ui.registry import WIDGET_CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "resources" / "android" / "java"
CAPABILITY_TABLE = ROOT / "core" / "ui" / "registry.py"

#: Python-side names that never cross into Java: the event queue, the wake
#: helper and the shutdown call all live in the C layer.
PYTHON_ONLY_CALLS = {"wake", "next_event", "finish_app"}


def snake_to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(word.capitalize() for word in tail)


def java_cases() -> set[str]:
    """Every ``case "Type":`` label in ``ViewBuilder.java``."""
    return set(re.findall(r'case "([A-Za-z]+)":', (JAVA / "ViewBuilder.java").read_text("utf-8")))


def java_methods(filename: str) -> set[str]:
    text = (JAVA / filename).read_text(encoding="utf-8")
    return set(
        re.findall(r"\b(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\], .]+\s(\w+)\(", text)
    )


def test_every_widget_type_the_phone_must_draw_has_a_java_branch():
    android = {c.type_name for c in WIDGET_CAPABILITIES if c.android}
    missing = android - java_cases()
    assert missing == set(), "no Java branch for: " + ", ".join(sorted(missing))


def test_the_capability_table_does_not_claim_a_java_branch_that_is_gone():
    android = {c.type_name for c in WIDGET_CAPABILITIES if c.android}
    stale = java_cases() - android
    assert stale == set(), "in ViewBuilder.java but not marked android in the table: " + ", ".join(
        sorted(stale)
    )


@pytest.mark.parametrize("renderer", ["web", "gui"])
def test_preview_tables_cover_exactly_the_types_marked_for_them(renderer: str) -> None:
    if renderer == "web":
        from pymobile.core.ui.web import _HTML_RENDERERS as drawn
    else:
        from pymobile.core.ui.gui import _EXTRA_GUI_KINDS, _TK_BUILDERS

        drawn = set(_TK_BUILDERS) | set(_EXTRA_GUI_KINDS)
    capabilities = {c.type_name for c in WIDGET_CAPABILITIES if getattr(c, renderer)}
    assert capabilities - set(drawn) == set(), (
        f"{renderer} draws no {sorted(capabilities - set(drawn))}"
    )
    assert set(drawn) - capabilities == set(), (
        f"{renderer} draws {sorted(set(drawn) - capabilities)} without a capability row"
    )


def test_every_native_call_python_makes_has_a_java_entry_point():
    methods = java_methods("Native.java")
    source = (ROOT / "core" / "bridge" / "android.py").read_text(encoding="utf-8")
    called = set(re.findall(r"(?:_native|native)\.(\w+)\(", source))
    called |= set(re.findall(r'getattr\((?:self\._native|native),\s*"(\w+)"', source))
    called -= PYTHON_ONLY_CALLS
    missing = {name for name in called if snake_to_camel(name) not in methods}
    assert missing == set(), "Native.java has no entry point for: " + ", ".join(sorted(missing))


def test_every_documented_native_method_is_still_called_by_something():
    """A Java entry point with no caller is dead weight, not a feature."""
    methods = java_methods("Native.java")
    source = (ROOT / "core" / "bridge" / "android.py").read_text(encoding="utf-8")
    called = set(re.findall(r"(?:_native|native)\.(\w+)\(", source))
    called |= set(re.findall(r'getattr\((?:self\._native|native),\s*"(\w+)"', source))
    callers = {snake_to_camel(name) for name in called} | {name for name in called}
    # Kotlin/Java callbacks and the C entry points that only Java calls.
    java_to_python = {"dispatchEvent", "stopEventLoop", "finishApp"}
    unused = methods - callers - java_to_python
    assert unused == set(), "no Python caller for: " + ", ".join(sorted(unused))


def test_the_android_bridge_implements_the_whole_protocol():
    from pymobile.core.bridge.android import AndroidBridge

    protocol = {name for name in vars(Bridge) if not name.startswith("_")}
    missing = {name for name in protocol if not hasattr(AndroidBridge, name)}
    assert missing == set(), "AndroidBridge is missing: " + ", ".join(sorted(missing))


def test_the_capability_table_is_one_literal_row_per_type():
    names = [c.type_name for c in WIDGET_CAPABILITIES]
    assert len(names) == len(set(names))
    tree = ast.parse(CAPABILITY_TABLE.read_text(encoding="utf-8"))
    table = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and getattr(node.target, "id", "") == "WIDGET_CAPABILITIES"
    )
    assert isinstance(table.value, ast.Tuple)
    assert len(table.value.elts) == len(names), "add a row to the literal table, not a loop"
    assert all(isinstance(element, ast.Call) for element in table.value.elts)
