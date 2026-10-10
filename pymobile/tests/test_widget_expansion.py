"""Cards, forms, tabs, icons, autocomplete, ranges, pagers, calendars and charts."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import pymobile
from pymobile import (
    Accordion,
    App,
    AutoComplete,
    BarChart,
    Button,
    Calendar,
    Card,
    Carousel,
    Column,
    DateRangePicker,
    EmptyState,
    ExpansionPanel,
    FloatingActionButton,
    Form,
    FormField,
    Icon,
    IconButton,
    Label,
    LineChart,
    MultiSelect,
    PageView,
    PieChart,
    RangeSlider,
    Screen,
    Skeleton,
    TabView,
    TextInput,
    Validator,
)
from pymobile.core.bridge import StubBridge
from pymobile.core.driver import Driver
from pymobile.core.ui.drawing import ICON_NAMES, drawing_svg, icon_drawing
from pymobile.core.ui.gui import skeleton
from pymobile.core.ui.mockup import render_mockup
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.registry import supported_by, unknown_types
from pymobile.core.ui.web import render_html

ROOT = Path(pymobile.__file__).parent


def walk(node: dict) -> list[dict]:
    result = [node]
    for child in node.get("children", []):
        result.extend(walk(child))
    return result


def types(node: dict) -> set[str]:
    return {item["type"] for item in walk(node)}


class Holder(Screen):
    def __init__(self, widget) -> None:
        super().__init__("Holder")
        self.widget = widget

    def build(self):
        return Column(self.widget)


def running(widget, bridge: StubBridge, tmp_path: Path):
    app = App("t", bridge=bridge, storage_path=str(tmp_path / "s.json"))
    app.run(Holder(widget))
    return app


def send(app: App, widget_id: str, kind: str, value: str = "") -> None:
    app.handle_ui_event(widget_id, kind, value)


class TestRegistryAndRenderers:
    def test_every_new_serialised_type_is_native_web_and_gui_supported(self) -> None:
        tree = Column(
            Icon("add"),
            IconButton("add"),
            AutoComplete(["a"]),
            RangeSlider(),
            PageView(Label("x")),
            BarChart([1]),
            LineChart([1]),
            PieChart([1]),
            Card(Label("c")),
            Form(FormField("a")),
            TabView({"a": Label("a")}),
            Accordion(ExpansionPanel("x")),
            EmptyState(),
            Skeleton(),
            MultiSelect(["a"]),
            Calendar("2026-10-01"),
            DateRangePicker(),
            Carousel(Label("x")),
        ).to_dict()
        for renderer in ("android", "web", "gui", "ascii"):
            assert unknown_types(tree, renderer=renderer) == frozenset()
        for name in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "PageView", "Chart"):
            assert name in supported_by("android")

    def test_java_cases_and_prebuilt_dex_include_the_new_types(self) -> None:
        java = (ROOT / "resources/android/java/ViewBuilder.java").read_text(encoding="utf-8")
        dex = (ROOT / "resources/android/prebuilt/arm64-v8a/classes.dex").read_bytes()
        for name in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "PageView", "Chart"):
            assert f'case "{name}":' in java
            assert name.encode() in dex
        assert b"AdvancedViews" in dex

    def test_every_desktop_renderer_draws_every_native_type(self) -> None:
        tree = Column(
            Icon("search"),
            IconButton("add", label="Add"),
            AutoComplete(["Kyiv"], "k"),
            RangeSlider(10, 70),
            PageView(Label("page")),
            BarChart({"a": 1, "b": -2}),
            LineChart([1, 3, 2]),
            PieChart([1, 2]),
        )
        ascii_ = render_ascii(tree)
        assert "[icon:search]" in ascii_ and "page" in ascii_ and "PieChart" in ascii_
        html = render_html(tree.to_dict())
        assert "<svg" in html and 'type="range"' in html and "<datalist" in html
        assert "&lt;" not in html
        pytest.importorskip("PIL")
        image = render_mockup(tree, None)
        assert image is not None

    def test_gui_skeleton_distinguishes_pages(self) -> None:
        first = PageView(Label("a"), Label("b"), id="p").to_dict()
        pager = PageView(Label("a"), Label("b"), id="q", value=1).to_dict()
        assert skeleton(first) != skeleton(pager)


class TestIcons:
    def test_all_icons_have_drawings_and_safe_svg(self) -> None:
        for name in ICON_NAMES:
            drawing = icon_drawing(name, "#112233")
            assert drawing["shapes"]
            assert "<svg" in drawing_svg(drawing, label="x<script>")
            assert "<script>" not in drawing_svg(drawing, label="x<script>")

    def test_unknown_icon_color_and_size_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="unknown icon"):
            Icon("emoji")
        with pytest.raises(ValueError):
            Icon("add", color="red")
        with pytest.raises(ValueError):
            Icon("add", size=0)

    def test_icon_button_has_label_target_and_presses(self, bridge, tmp_path) -> None:
        pressed: list[str] = []
        widget = IconButton("add", label="Add item", on_press=lambda: pressed.append("x"), id="add")
        app = running(widget, bridge, tmp_path)
        # The frame handed to the bridge carries generation-qualified ids
        # ("3:add"); to_dict() reports the ids the application itself wrote.
        node = next(item for item in walk(app.screen.to_dict()) if item["id"] == "add")
        assert node["props"]["label"] == "Add item"
        assert node["style"]["width"] == node["style"]["height"] == 48
        send(app, "add", "press")
        assert pressed == ["x"]
        widget.enabled = False
        send(app, "add", "press")
        assert pressed == ["x"]
        with pytest.raises(ValueError):
            IconButton("add", size=47)
        with pytest.raises(ValueError):
            IconButton("add", label="")

    def test_fab_is_elevated_and_theme_colored(self) -> None:
        node = FloatingActionButton("add").to_dict()
        assert node["style"]["elevation"] == 6
        assert node["style"]["background"] == node["props"]["background"]


class TestCard:
    def test_card_is_composed_from_supported_types(self) -> None:
        card = Card(Label("Body"), title="Title", actions=[Button("Open")], id="card")
        node = card.to_dict()
        assert node["type"] == "Column"
        assert node["style"]["corner_radius"] == 12 and node["style"]["elevation"] == 2
        assert node["style"]["background"]
        assert types(node) == {"Column", "Label", "Wrap", "Button"}

    def test_explicit_style_wins_and_children_keep_ownership(self) -> None:
        from pymobile import Style

        label = Label("x")
        card = Card(
            label, style=Style(background="#ffffff", padding=4, corner_radius=1, elevation=0)
        )
        node = card.to_dict()
        assert node["style"]["background"] == "#FFFFFF" or node["style"]["background"] == "#ffffff"
        assert label.parent is card
        with pytest.raises(ValueError):
            Card(padding=-1)


class TestForm:
    def test_validates_shows_and_clears_errors_with_existing_validator(
        self, bridge, tmp_path
    ) -> None:
        submitted: list[dict[str, str]] = []
        email = FormField("email", "Email", required=True, rules=["email"], id="email")
        form = Form(email, on_submit=submitted.append, id="form")
        app = running(form, bridge, tmp_path)
        assert form.submit() is False
        assert email.error and email.touched
        assert not submitted
        send(app, "email:input", "change", "me@example.com")
        assert email.error == ""
        assert form.submit() is True
        assert submitted == [{"email": "me@example.com"}]
        assert email.dirty
        form.reset()
        assert email.value == "" and not email.dirty and not email.touched

    def test_cross_field_validator_and_server_error(self) -> None:
        password = FormField("password", password=True)
        confirm = FormField("confirm")
        validator = Validator({"password": ["required"], "confirm": [{"matches": "password"}]})
        form = Form(password, confirm, validator=validator, validate_on="change")
        password.value = "abc"
        confirm.value = "abd"
        assert form.errors["confirm"]
        confirm.set_error("taken")
        assert confirm.error == "taken"
        with pytest.raises(ValueError):
            Form(FormField("a"), FormField("a"))
        with pytest.raises(ValueError):
            FormField("x", rules=["not_a_rule"])

    def test_domain_errors_are_mapped_by_field_name(self) -> None:
        name = FormField("name", label="Plant name")
        species = FormField("species", label="Species")
        form = Form(name, species)
        form.set_errors({"name": "A plant with this name already exists.", "species": "Unknown."})
        assert form.errors == {
            "name": "A plant with this name already exists.",
            "species": "Unknown.",
        }
        assert name.error == "A plant with this name already exists."
        assert species.error == "Unknown."
        form.to_dict()  # language refresh must not erase domain/model errors
        assert name.error == "A plant with this name already exists."
        form.set_errors({"species": "Try another species."})
        assert name.error == "" and species.error == "Try another species."
        species.value = "Fern"
        assert species.error == "" and "species" not in form.errors
        with pytest.raises(ValueError, match="unknown form field"):
            form.set_errors({"scientific_name": "Unknown field."})

    def test_hint_hides_while_error_and_disabled_form_cannot_submit(self) -> None:
        field = FormField("name", hint="Your name", required=True)
        form = Form(field)
        form.enabled = False
        assert form.submit() is False
        node = form.to_dict()
        assert all(not item["enabled"] for item in walk(node))
        form.enabled = True
        assert form.validate() is False
        children = {item["id"]: item for item in walk(field.to_dict())}
        assert children[f"{field.id}:error"]["visible"]
        assert not children[f"{field.id}:hint"]["visible"]

    def test_form_messages_are_localisable(self) -> None:
        field = FormField("name", required=True)
        form = Form(field, messages={"required": "обов'язкове поле"})
        assert form.validate() is False
        assert field.error == "обов'язкове поле"

    def test_max_length_reset_and_autocomplete_control(self) -> None:
        auto = AutoComplete(["Kyiv"], max_length=3, id="city-input")
        field = FormField("city", control=auto)
        field.reset("abcdef")
        assert field.value == "abc"
        with pytest.raises(TypeError):
            FormField("x", control=Label("x"))  # type: ignore[arg-type]


class TestTabsAndPanels:
    def test_tab_state_builder_and_native_types(self, bridge, tmp_path) -> None:
        built: list[str] = []
        changes: list[str] = []
        tabs = TabView(
            {"home": Label("Home"), "more": lambda: built.append("more") or Label("More")},
            labels={"home": "Головна"},
            on_select=changes.append,
            id="tabs",
        )
        app = running(tabs, bridge, tmp_path)
        assert built == []
        send(app, "tabs:tab:more", "press")
        assert tabs.value == "more" and built == ["more"] and changes == ["more"]
        texts = [item["props"].get("text") for item in walk(app.render())]
        assert "More" in texts and "Home" not in texts
        tabs.select("more")
        assert changes == ["more"]
        with pytest.raises(ValueError):
            tabs.select("missing")
        tabs.set_labels({"more": "Більше"})
        assert "Більше" in [item["props"].get("text") for item in walk(tabs.to_dict())]

    def test_tab_builder_must_return_widget_and_failure_is_atomic(self) -> None:
        tabs = TabView({"a": Label("a"), "b": lambda: "nope"})  # type: ignore[dict-item]
        with pytest.raises(TypeError):
            tabs.select("b")
        assert tabs.value == "a"

    def test_accordion_single_and_multiple_modes(self) -> None:
        a, b = ExpansionPanel("A", Label("a"), expanded=True), ExpansionPanel("B", Label("b"))
        changes: list[tuple[str, ...]] = []
        group = Accordion(a, b, on_change=changes.append)
        b.toggle()
        assert not a.expanded and b.expanded and changes[-1] == (b.id,)
        group.multiple = True
        a.toggle()
        assert a.expanded and b.expanded
        nodes = {item["id"]: item for item in walk(group.to_dict())}
        assert nodes[f"{a.id}:content"]["visible"] is True
        with pytest.raises(TypeError):
            a.set_expanded("yes")  # type: ignore[arg-type]

    def test_disabled_panel_ignores_taps(self, bridge, tmp_path) -> None:
        panel = ExpansionPanel("A", Label("a"), id="panel")
        app = running(panel, bridge, tmp_path)
        panel.enabled = False
        send(app, "panel:header", "press")
        assert not panel.expanded


class TestStates:
    def test_empty_state_action_and_skeleton_validation(self) -> None:
        calls: list[int] = []
        state = EmptyState(
            "Empty", description="Nothing", action_label="Retry", on_action=lambda: calls.append(1)
        )
        node = state.to_dict()
        assert types(node) == {"Column", "Icon", "Label", "Button"}
        next(item for item in walk(node) if item["type"] == "Button")
        state.children[-1].press()
        assert calls == [1]
        assert Skeleton(2, width=30).to_dict()["children"][0]["style"]["background"]
        for bad in (0, -1):
            with pytest.raises(ValueError):
                Skeleton(bad)


class TestMultiSelect:
    def test_keys_limit_order_labels_and_events(self, bridge, tmp_path) -> None:
        seen: list[tuple[str, ...]] = []
        widget = MultiSelect(
            {"a": "Alpha", "b": "Beta", "c": "Gamma"},
            ["b"],
            maximum_selected=2,
            on_change=seen.append,
            id="ms",
        )
        app = running(widget, bridge, tmp_path)
        send(app, "ms:option:c", "press")
        send(app, "ms:option:a", "press")  # ignored: limit reached
        assert widget.value == ("b", "c") and seen == [("b", "c")]
        widget.set_labels({"a": "Альфа"})
        assert "Альфа" in str(widget.to_dict())
        send(app, "ms", "change", json.dumps(["a"]))
        assert widget.value == ("a",)
        with pytest.raises(ValueError):
            widget.set_value(["z"])
        with pytest.raises(ValueError):
            widget.set_value(["a", "b", "c"])
        with pytest.raises(TypeError):
            MultiSelect("abc")  # type: ignore[arg-type]


class TestAutoComplete:
    def test_substring_threshold_selection_and_no_value_change_loop(self, bridge, tmp_path) -> None:
        changes: list[str] = []
        selected: list[str] = []
        widget = AutoComplete(
            ["Kyiv", "Lviv", "Odesa"],
            threshold=2,
            on_change=changes.append,
            on_select=selected.append,
            id="city",
        )
        app = running(widget, bridge, tmp_path)
        assert widget.suggestions("v") == ()
        assert widget.suggestions("IV") == ("Kyiv", "Lviv")
        send(app, "city", "change", "ly")
        send(app, "city", "select", "Lviv")
        assert widget.value == "Lviv" and selected == ["Lviv"] and changes[-1] == "Lviv"
        with pytest.raises(ValueError):
            widget.select("Paris")
        widget.set_options(["Paris"])
        assert app.render() is not None
        with pytest.raises(TypeError):
            AutoComplete("Kyiv")  # type: ignore[arg-type]

    def test_disabled_and_duplicate_options(self, bridge, tmp_path) -> None:
        widget = AutoComplete(["a", "a", "b"], id="auto", enabled=False)
        app = running(widget, bridge, tmp_path)
        assert widget.options == ("a", "b")
        send(app, "auto", "change", "abc")
        send(app, "auto", "select", "a")
        assert widget.value == ""


class TestRangeSlider:
    def test_snap_clamp_validation_and_ui_json(self, bridge, tmp_path) -> None:
        changes: list[tuple[float, float]] = []
        widget = RangeSlider(
            12, 88, minimum=0, maximum=100, step=10, on_change=changes.append, id="r"
        )
        assert widget.value == (10, 90)
        app = running(widget, bridge, tmp_path)
        send(app, "r", "change", "[20, 95]")
        assert widget.value == (20, 100) and changes == [(20, 100)]
        send(app, "r", "change", "[80, 20]")
        assert widget.value == (20, 100)
        send(app, "r", "change", "nope")
        assert widget.value == (20, 100)
        node = widget.to_dict()
        assert node["props"]["low"] == 20 and node["props"]["high"] == 100
        for bad in ((0, math.nan), (0, math.inf), (True, 1), (1, 0)):
            with pytest.raises((TypeError, ValueError)):
                RangeSlider(*bad)
        with pytest.raises(ValueError):
            RangeSlider(minimum=1, maximum=1)
        with pytest.raises(ValueError):
            RangeSlider(step=0)

    def test_fractional_steps_do_not_leak_float_noise(self) -> None:
        widget = RangeSlider(0.1, 0.7, minimum=0, maximum=1, step=0.1)
        assert widget.value == (0.1, 0.7)
        widget.value = (0.30000000000000004, 0.6000000000000001)
        assert widget.value == (0.3, 0.6)


class TestPageViewAndCarousel:
    def test_lazy_builder_loop_atomic_failure_and_events(self, bridge, tmp_path) -> None:
        built: list[int] = []
        selected: list[int] = []

        def build(index: int):
            if index == 2 and len(built) > 5:
                raise RuntimeError("boom")
            built.append(index)
            return Label(f"page {index}")

        pager = PageView(
            item_count=3, builder=build, loop=True, on_select=selected.append, id="pager"
        )
        assert built == [0]
        app = running(pager, bridge, tmp_path)
        send(app, "pager", "change", "1")
        assert pager.value == 1 and selected == [1]
        send(app, "pager", "change", "9")
        assert pager.value == 1
        pager.previous()
        pager.previous()
        assert pager.value == 2
        pager.next()
        assert pager.value == 0
        built.extend([0] * 6)
        with pytest.raises(RuntimeError):
            pager.select(2)
        assert pager.value == 0 and len(pager.children) == 1
        assert "page 0" in str(pager.to_dict())
        with pytest.raises(IndexError):
            PageView(Label("x"), value=2)
        with pytest.raises(ValueError):
            PageView()
        with pytest.raises(ValueError):
            pager.add(Label("x"))

    def test_page_survives_screen_refresh_and_ownership_is_repaired(self, bridge, tmp_path) -> None:
        pager = PageView(Label("a"), Label("b"), id="pager")
        app = running(pager, bridge, tmp_path)
        pager.select(1)
        app.screen.refresh()
        assert pager.value == 1
        assert all(child.parent is pager for child in pager.children)
        assert "b" in str(app.render())

    def test_carousel_buttons_and_indicator(self) -> None:
        carousel = Carousel(Label("a"), Label("b"), id="car")
        assert carousel._previous.enabled is False
        carousel.next()
        assert carousel.value == 1 and "2 / 2" in str(carousel.to_dict())
        assert carousel._next.enabled is False


class TestCalendar:
    def test_navigation_bounds_week_start_and_disabled_dates(self) -> None:
        picked: list[str] = []
        calendar = Calendar(
            "2026-10-01",
            minimum="2026-09-15",
            maximum="2026-10-20",
            first_weekday=6,
            disabled_date=lambda d: d.day == 10,
            on_change=picked.append,
            weekday_names=list("MTWTFSS"),
            id="cal",
        )
        nodes = {item["id"]: item for item in walk(calendar.to_dict())}
        assert nodes["cal:weekday:0"]["props"]["text"] == "S"
        assert nodes["cal:day:2026-10-10"]["enabled"] is False
        assert (
            nodes["cal:day:2026-10-21"]["enabled"] is False
            if "cal:day:2026-10-21" in nodes
            else True
        )
        assert nodes["cal:next"]["enabled"] is False
        calendar.set_value("2026-10-02")
        assert picked == ["2026-10-02"]
        for bad in ("2026-10-10", "2026-10-21", "2026-1-2", "2026-02-30"):
            with pytest.raises(ValueError):
                calendar.set_value(bad)
        calendar._shift(-1)
        assert calendar.month == "2026-09"
        assert calendar._month.month == 9

    def test_leap_year_and_localised_title(self) -> None:
        calendar = Calendar("2028-02-29", month_names=[str(i) for i in range(1, 13)])
        nodes = {item["id"]: item for item in walk(calendar.to_dict())}
        assert nodes[f"{calendar.id}:day:2028-02-29"]
        assert nodes[f"{calendar.id}:month"]["props"]["text"] == "2 2028"

    def test_range_picker_two_taps_reverse_clear_and_validation(self, bridge, tmp_path) -> None:
        changes: list[tuple[str, str]] = []
        picker = DateRangePicker(month="2026-10", on_change=changes.append, id="range")
        app = running(picker, bridge, tmp_path)
        send(app, "range:calendar:day:2026-10-10", "press")
        assert picker.value == ("2026-10-10", "") and not picker.complete
        send(app, "range:calendar:day:2026-10-03", "press")
        assert picker.value == ("2026-10-03", "2026-10-10") and picker.complete
        send(app, "range:calendar:day:2026-10-20", "press")
        assert picker.value == ("2026-10-20", "")
        send(app, "range:calendar:day:2026-10-20", "press")
        assert picker.value == ("2026-10-20", "2026-10-20")
        send(app, "range:clear", "press")
        assert picker.value == ("", "")
        assert changes[-1] == ("", "")
        for bad in (("2026-10-10", "2026-10-01"), ("", "2026-10-01"), ("bad", "")):
            with pytest.raises((TypeError, ValueError)):
                picker.set_value(bad)
        blocked = DateRangePicker(disabled_date=lambda d: d.isoformat() == "2026-10-05")
        with pytest.raises(ValueError):
            blocked.set_value(("2026-10-01", "2026-10-09"))


class TestCharts:
    def test_signed_constant_empty_and_large_values(self) -> None:
        chart = BarChart({"a": -5, "b": 10}, title="Sales")
        assert chart.to_dict()["props"]["drawing"]["shapes"]
        assert "Sales" in chart.to_dict()["props"]["label"]
        assert LineChart([4, 4, 4]).to_dict()["props"]["drawing"]["shapes"]
        empty = PieChart([0, 0], empty_text="Nothing")
        assert "Nothing" in str(empty.to_dict()["props"]["drawing"])
        assert BarChart([]).to_dict()["props"]["drawing"]["shapes"]
        with pytest.raises(ValueError):
            PieChart([1e308, 1e308])
        with pytest.raises(ValueError):
            BarChart([1e308, -1e308])

    def test_input_validation_and_updates(self) -> None:
        chart = BarChart([1, 2], labels=["a", "b"])
        chart.set_data({"x": 1})
        assert chart.labels == ("x",)
        chart.data = [3]
        assert chart.data == (3.0,) and chart.labels == ("x",)
        chart.data = [3, 4]
        assert chart.labels == ("1", "2")
        for factory in (
            lambda: BarChart([1, math.nan]),
            lambda: BarChart("123"),
            lambda: BarChart([1], labels=[]),
            lambda: PieChart([1, -1]),
            lambda: BarChart({"a": 1}, labels=["a"]),
            lambda: BarChart([1], height=10),
            lambda: BarChart([1], color="red"),
        ):
            with pytest.raises((TypeError, ValueError)):
                factory()

    def test_many_points_are_bounded(self) -> None:
        drawing = LineChart(list(range(1000))).to_dict()["props"]["drawing"]
        assert len(drawing["shapes"]) < 120
        assert PieChart(list(range(1, 80))).to_dict()["props"]["drawing"]["shapes"]


class TestNativeContract:
    def test_java_sources_have_matching_snap_and_backend_includes_file(self) -> None:
        java = (ROOT / "resources/android/java/AdvancedViews.java").read_text(encoding="utf-8")
        assert "Math.floor((value - minimum) / step + 0.5)" in java
        backend = (ROOT / "compiler/backends/native.py").read_text(encoding="utf-8")
        assert '"AdvancedViews.java"' in backend

    def test_driver_can_drive_new_widgets(self, bridge: StubBridge, tmp_path: Path) -> None:
        class Page(Screen):
            def build(self):
                return Column(RangeSlider(id="range"), AutoComplete(["a"], id="auto"))

        app = App("d", bridge=bridge, storage_path=str(tmp_path / "s.json"))
        app.run(Page())
        driver = Driver(app, screens=[Page])
        driver.change("range", "[10, 20]")
        assert driver.find("range").value == (10, 20)
        driver.change("auto", "abc")
        assert driver.find("auto").value == "abc"
        app.stop()

    def test_plain_text_input_events_still_work(self, bridge: StubBridge, tmp_path: Path) -> None:
        field = TextInput(id="plain")
        app = running(field, bridge, tmp_path)
        send(app, "plain", "change", "x")
        assert field.value == "x"


class TestDisabledComposites:
    def test_disabled_form_blocks_events_for_its_inputs_and_buttons(self, bridge, tmp_path) -> None:
        pressed: list[int] = []
        field = FormField("name", id="name")
        button = Button("Go", id="go", on_press=lambda: pressed.append(1))
        form = Form(field, id="form")
        form.add(button)
        app = running(form, bridge, tmp_path)
        form.enabled = False
        send(app, "name:input", "change", "typed")
        send(app, "go", "press")
        assert field.value == "" and pressed == []
        form.enabled = True
        send(app, "go", "press")
        assert pressed == [1]

    def test_disabled_ordinary_container_behaviour_is_unchanged(self, bridge, tmp_path) -> None:
        pressed: list[int] = []
        inner = Column(Button("Go", id="go", on_press=lambda: pressed.append(1)), enabled=False)
        app = running(inner, bridge, tmp_path)
        send(app, "go", "press")
        assert pressed == [1]
