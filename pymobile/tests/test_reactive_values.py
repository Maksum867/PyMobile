"""Reactive values: Value, Computed and the bindings built on them.

A screen keeps its state in values instead of rebuilding itself, so a bound
label has to change when the value does.
"""

from __future__ import annotations

import pytest

from pymobile import App, Button, Column, Computed, Label, Screen, TextInput, Value, Widget
from pymobile.testing import StubBridge


def _screen(cls: type[Screen]) -> tuple[App, Screen]:
    screen = cls()
    app = App(screen, bridge=StubBridge(verbose=False))
    app.run(screen)
    return app, screen


class Counter(Screen):
    """A screen whose state lives in values, not in a rebuilt tree."""

    def __init__(self) -> None:
        super().__init__()
        self.taps = Value(0)
        self.label = Label("0")
        self.taps.bind(self.label, "text", transform=lambda n: f"Taps: {n}")
        self.extra = Value(1)
        self.total = Computed(lambda: self.taps.value + self.extra.value)
        self.total_label = Label("")
        self.total.bind(self.total_label, "text", transform=lambda n: f"Total: {n}")

    def build(self) -> Widget:
        return Column(self.label, self.total_label, Button("+1", on_press=self.increment))

    def increment(self) -> None:
        self.taps.value += 1


class TestReactiveValues:
    def test_a_bound_label_updates_without_a_refresh(self) -> None:
        app, screen = _screen(Counter)
        try:
            labelled = screen.label  # type: ignore[attr-defined]
            assert labelled.text == "Taps: 0"
            screen.increment()  # type: ignore[attr-defined]
            screen.increment()  # type: ignore[attr-defined]
            assert labelled.text == "Taps: 2"
        finally:
            app.stop()

    def test_a_computed_value_follows_its_sources(self) -> None:
        app, screen = _screen(Counter)
        try:
            screen.increment()  # type: ignore[attr-defined]
            screen.extra.value = 5  # type: ignore[attr-defined]
            assert screen.total.value == 6  # type: ignore[attr-defined]
            assert screen.total_label.text == "Total: 6"  # type: ignore[attr-defined]
        finally:
            app.stop()

    def test_setting_the_same_value_notifies_nobody(self) -> None:
        calls: list[int] = []
        value: Value[int] = Value(1)
        value.subscribe(calls.append)
        value.value = 1
        value.value = 2
        assert calls == [2]

    def test_arithmetic_spellings_work_on_a_value(self) -> None:
        value: Value[int] = Value(1)
        value += 4
        assert value.value == 5
        assert value + 1 == 6
        assert 1 + value == 6

    def test_toggle_flips_a_bool_and_refuses_anything_else(self) -> None:
        flag: Value[bool] = Value(False)
        assert flag.toggle() is True
        with pytest.raises(TypeError, match="needs a bool"):
            Value(3).toggle()

    def test_bind_get_follows_a_field_the_user_types_into(self) -> None:
        query: Value[str] = Value("")
        field = TextInput("start")
        query.bind_get(field, "value")
        assert query.value == "start"
        field.set_value("typed")
        assert query.value == "start"  # one-way by design until subscribe() is used

    def test_a_computed_recomputes_after_its_source_changes(self) -> None:
        price: Value[float] = Value(2.0)
        doubled = Computed(lambda: price.value * 2)
        assert doubled.value == 4.0
        price.value = 3.0
        assert doubled.value == 6.0

    def test_a_self_referential_computed_returns_instead_of_hanging(self) -> None:
        holder: dict[str, Computed[int] | None] = {"value": None}

        def compute() -> int:
            own = holder["value"]
            return 1 if own is None else own.value + 1

        computed = Computed(compute)
        holder["value"] = computed
        # A genuine cycle reads its own previous value rather than recursing.
        assert computed.value == 1
