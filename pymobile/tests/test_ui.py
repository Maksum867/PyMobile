"""Tests for widgets, layouts, styling and navigation."""

from __future__ import annotations

import pytest

from pymobile import (
    Align,
    App,
    Button,
    Color,
    Column,
    Divider,
    EdgeInsets,
    Expanded,
    Flexible,
    Grid,
    Image,
    Label,
    ProgressBar,
    Row,
    SafeArea,
    Screen,
    ScrollView,
    Spacer,
    Style,
    Switch,
    TextInput,
    Widget,
)
from pymobile.core.ui.screen import Navigator
from pymobile.errors import PyMobileError


class TestWidgets:
    def test_label_serialises_text(self) -> None:
        label = Label("hi", id="greeting")
        node = label.to_dict()
        assert node["type"] == "Label"
        assert node["id"] == "greeting"
        assert node["props"]["text"] == "hi"

    def test_ids_are_unique(self) -> None:
        assert Label("a").id != Label("b").id

    def test_button_press_invokes_callback(self) -> None:
        calls: list[int] = []
        button = Button("go", on_press=lambda: calls.append(1))
        button.press()
        assert calls == [1]

    def test_disabled_button_ignores_press(self) -> None:
        calls: list[int] = []
        button = Button("go", on_press=lambda: calls.append(1), enabled=False)
        button.press()
        assert calls == []

    def test_button_set_text_updates_label(self) -> None:
        button = Button("Start")
        button.set_text("Pause")
        assert button.text == "Pause"
        assert button.props()["text"] == "Pause"

    def test_text_input_notifies_on_change(self) -> None:
        seen: list[str] = []
        field = TextInput(on_change=seen.append)
        field.set_value("abc")
        field.set_value("abc")  # unchanged -> no callback
        assert seen == ["abc"]

    def test_text_input_respects_max_length(self) -> None:
        field = TextInput(max_length=3)
        field.set_value("abcdef")
        assert field.value == "abc"

    def test_text_input_rejects_bad_max_length(self) -> None:
        with pytest.raises(ValueError, match="max_length"):
            TextInput(max_length=0)

    def test_switch_toggle(self) -> None:
        states: list[bool] = []
        switch = Switch(on_toggle=states.append)
        assert switch.toggle() is True
        switch.set_checked(True)  # no change -> no callback
        assert states == [True]

    def test_progress_bar_clamps(self) -> None:
        bar = ProgressBar(150, maximum=100)
        assert bar.value == 100
        bar.set_value(-5)
        assert bar.value == 0
        assert bar.fraction == 0

    def test_progress_bar_rejects_bad_maximum(self) -> None:
        with pytest.raises(ValueError, match="maximum"):
            ProgressBar(maximum=0)

    def test_image_validates_input(self) -> None:
        with pytest.raises(ValueError, match="source"):
            Image("")
        with pytest.raises(ValueError, match="fit"):
            Image("a.png", fit="stretch")

    def test_spacer_rejects_negative_size(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            Spacer(-1)


class TestContainers:
    def test_children_are_attached(self) -> None:
        child = Label("x")
        column = Column(child)
        assert child.parent is column
        assert list(column) == [child]
        assert len(column) == 1

    def test_double_parent_is_rejected(self) -> None:
        child = Label("x")
        Column(child)
        with pytest.raises(PyMobileError, match="already has a parent"):
            Row(child)

    def test_self_nesting_is_rejected(self) -> None:
        column = Column()
        with pytest.raises(ValueError, match="itself"):
            column.add(column)

    def test_remove_and_clear(self) -> None:
        child = Label("x")
        column = Column(child)
        column.remove(child)
        assert child.parent is None
        assert len(column) == 0
        column.clear()  # no-op, must not raise

    def test_walk_and_find(self) -> None:
        target = Label("deep", id="deep")
        tree = Column(Row(ScrollView(target)))
        assert tree.find("deep") is target
        assert tree.find("nope") is None
        assert len(list(tree.walk())) == 4

    def test_layout_props_serialised(self) -> None:
        node = Column(Label("a"), spacing=8).to_dict()
        assert node["props"]["spacing"] == 8
        assert len(node["children"]) == 1

    def test_negative_spacing_rejected(self) -> None:
        with pytest.raises(ValueError, match="spacing"):
            Column(spacing=-1)


class TestGrid:
    def test_children_are_laid_out_in_rows(self) -> None:
        grid = Grid(*[Label(str(i)) for i in range(5)], columns=2)
        assert grid.rows == 3  # last row is half full
        assert grid.props()["columns"] == 2

    def test_spacing_applies_to_both_axes(self) -> None:
        props = Grid(Label("a"), spacing=12).props()
        assert props["row_spacing"] == 12
        assert props["column_spacing"] == 12

    def test_axis_spacing_can_differ(self) -> None:
        props = Grid(Label("a"), spacing=12, column_spacing=4).props()
        assert props["row_spacing"] == 12
        assert props["column_spacing"] == 4

    def test_rejects_bad_geometry(self) -> None:
        with pytest.raises(ValueError, match="columns"):
            Grid(columns=0)
        with pytest.raises(ValueError, match="spacing"):
            Grid(spacing=-1)
        with pytest.raises(ValueError, match="row_spacing"):
            Grid(row_spacing=-1)

    def test_empty_grid_has_no_rows(self) -> None:
        assert Grid(columns=3).rows == 0

    def test_preview_aligns_columns(self) -> None:
        """The whole point of Grid: cells of different widths still line up."""
        from pymobile.core.ui.preview import render_ascii

        grid = Grid(
            Label("Completed"),
            Label("Focus"),
            Label("4"),
            Label("2 h 45 m"),
            columns=2,
        )
        lines = render_ascii(grid).splitlines()
        assert lines[0].index("Focus") == lines[1].index("2 h 45 m")


class TestScrollView:
    def test_spacing_is_supported_like_a_column(self) -> None:
        assert ScrollView(Label("a"), spacing=12).props()["spacing"] == 12

    def test_spacing_defaults_to_zero(self) -> None:
        assert ScrollView(Label("a")).props()["spacing"] == 0

    def test_negative_spacing_rejected(self) -> None:
        with pytest.raises(ValueError, match="spacing"):
            ScrollView(spacing=-1)

    def test_horizontal_preview_places_children_side_by_side(self) -> None:
        """A horizontal ScrollView used to be drawn as a vertical stack."""
        from pymobile.core.ui.preview import render_ascii

        scroller = ScrollView(Label("one"), Label("two"), horizontal=True)
        assert render_ascii(scroller).strip() == "one  two"

    def test_vertical_preview_stacks(self) -> None:
        from pymobile.core.ui.preview import render_ascii

        lines = render_ascii(ScrollView(Label("one"), Label("two"))).splitlines()
        assert lines == ["one", "two"]


class TestStyleReachesTheLayout:
    """Style fields that the renderer used to ignore."""

    def test_margin_serialises(self) -> None:
        style = Style(margin=EdgeInsets(1, 2, 3, 4))
        assert style.to_dict()["margin"] == [1, 2, 3, 4]

    def test_size_serialises_as_a_number(self) -> None:
        assert Style(width=120, height=48).to_dict() == {"width": 120, "height": 48}

    def test_size_accepts_the_documented_names(self) -> None:
        assert Style(width="match").to_dict()["width"] == "match"

    def test_elevation_serialises(self) -> None:
        assert Style(elevation=4).to_dict()["elevation"] == 4


class TestFlex:
    def test_expanded_is_tight_and_flexible_is_loose(self) -> None:
        assert Expanded(Label("a")).props()["fit"] == "tight"
        assert Flexible(Label("a")).props()["fit"] == "loose"

    def test_flex_share_serialises(self) -> None:
        assert Expanded(Label("a"), flex=3).props()["flex"] == 3

    def test_child_is_accessible(self) -> None:
        label = Label("a")
        assert Expanded(label).child is label

    def test_single_child_only(self) -> None:
        wrapper = Expanded(Label("a"))
        with pytest.raises(ValueError, match="single child"):
            wrapper.add(Label("b"))

    def test_rejects_bad_flex(self) -> None:
        with pytest.raises(ValueError, match="flex"):
            Expanded(Label("a"), flex=0)

    def test_row_of_expanded_children_serialises(self) -> None:
        node = Row(Expanded(Label("l")), Expanded(Label("r"), flex=2)).to_dict()
        assert [child["props"]["flex"] for child in node["children"]] == [1, 2]


class TestDivider:
    def test_defaults_are_horizontal_hairline(self) -> None:
        props = Divider().props()
        assert props["thickness"] == 1
        assert props["vertical"] is False

    def test_vertical_and_inset(self) -> None:
        props = Divider(vertical=True, inset=16, thickness=2).props()
        assert props == {
            "thickness": 2,
            "color": Divider().color,
            "inset": 16,
            "vertical": True,
        }

    def test_colour_is_validated(self) -> None:
        with pytest.raises(ValueError, match="invalid color"):
            Divider(color="grey")

    def test_rejects_bad_geometry(self) -> None:
        with pytest.raises(ValueError, match="thickness"):
            Divider(thickness=0)
        with pytest.raises(ValueError, match="inset"):
            Divider(inset=-1)

    def test_preview_draws_a_line(self) -> None:
        from pymobile.core.ui.preview import render_ascii

        assert set(render_ascii(Divider()).strip()) == {"─"}


class TestSafeArea:
    def test_all_edges_on_by_default(self) -> None:
        props = SafeArea(Label("a")).props()
        assert all(props[edge] for edge in ("top", "bottom", "left", "right"))
        assert props["minimum"] == 0

    def test_edges_can_be_disabled(self) -> None:
        assert SafeArea(Label("a"), top=False).props()["top"] is False

    def test_minimum_padding_is_validated(self) -> None:
        with pytest.raises(ValueError, match="minimum"):
            SafeArea(minimum=-1)

    def test_wraps_content(self) -> None:
        child = Column(Label("a"))
        assert SafeArea(child).children[0] is child


class TestCrossAlign:
    def test_unset_by_default(self) -> None:
        assert "cross_align" not in Row(Label("a")).props()

    def test_serialised_when_set(self) -> None:
        props = Row(Label("a"), cross_align=Align.CENTER).props()
        assert props["cross_align"] == "center"

    def test_stretch_is_allowed(self) -> None:
        assert Column(cross_align=Align.STRETCH).props()["cross_align"] == "stretch"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="cross_align"):
            Row(cross_align="middle")


class TestConstraints:
    def test_min_and_max_serialise(self) -> None:
        style = Style(min_width=120, max_width=200)
        assert style.to_dict() == {"min_width": 120, "max_width": 200}

    def test_aspect_ratio_serialises(self) -> None:
        ratio = Style(aspect_ratio=16 / 9).to_dict()["aspect_ratio"]
        assert ratio == pytest.approx(1.777, abs=1e-3)

    def test_contradictory_bounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_width"):
            Style(min_width=200, max_width=120)
        with pytest.raises(ValueError, match="min_height"):
            Style(min_height=200, max_height=120)

    def test_negative_bounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_width"):
            Style(min_width=-1)

    def test_bad_aspect_ratio_rejected(self) -> None:
        with pytest.raises(ValueError, match="aspect_ratio"):
            Style(aspect_ratio=0)


class TestStyle:
    def test_only_set_fields_serialise(self) -> None:
        assert Style().to_dict() == {}
        assert Style(font_size=14, bold=True).to_dict() == {"font_size": 14, "bold": True}

    def test_insets_serialise_as_list(self) -> None:
        style = Style(padding=EdgeInsets.all(4))
        assert style.to_dict()["padding"] == [4, 4, 4, 4]

    def test_empty_insets_are_dropped(self) -> None:
        assert Style(padding=EdgeInsets()).to_dict() == {}

    def test_symmetric_insets(self) -> None:
        insets = EdgeInsets.symmetric(horizontal=8, vertical=2)
        assert insets.to_list() == [8, 2, 8, 2]

    def test_invalid_color_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid color"):
            Style(color="red")

    def test_named_colors_are_valid(self) -> None:
        assert Style(background=Color.PRIMARY).to_dict()["background"] == Color.PRIMARY

    def test_merge_returns_copy(self) -> None:
        base = Style(font_size=12)
        merged = base.merge(font_size=20)
        assert base.font_size == 12
        assert merged.font_size == 20

    def test_invalid_font_size(self) -> None:
        with pytest.raises(ValueError, match="font_size"):
            Style(font_size=0)


class _Home(Screen):
    title = "Home"

    def build(self) -> Widget:
        return Column(Label("home"))


class _Details(Screen):
    title = "Details"

    def __init__(self) -> None:
        super().__init__()
        self.log: list[str] = []

    def build(self) -> Widget:
        return Column(Label("details"))

    def on_mount(self) -> None:
        self.log.append("mount")

    def on_show(self) -> None:
        self.log.append("show")

    def on_hide(self) -> None:
        self.log.append("hide")

    def on_unmount(self) -> None:
        self.log.append("unmount")


class TestScreen:
    def test_build_is_required(self) -> None:
        with pytest.raises(NotImplementedError):
            Screen().root

    def test_root_is_cached(self) -> None:
        screen = _Home()
        assert screen.root is screen.root

    def test_serialisation_includes_title(self) -> None:
        assert _Home().to_dict()["screen"] == "Home"

    def test_find_delegates_to_root(self) -> None:
        screen = _Home()
        assert screen.find(screen.root.children[0].id) is not None


class TestNavigator:
    def test_push_and_pop(self) -> None:
        nav = Navigator()
        home, details = _Home(), _Details()
        nav.push(home)
        nav.push(details)
        assert nav.depth == 2
        assert nav.current is details
        popped = nav.pop()
        assert popped is details
        assert nav.stack[-1] is home

    def test_cannot_pop_last_screen(self) -> None:
        nav = Navigator()
        nav.push(_Home())
        assert nav.pop() is None
        assert nav.depth == 1

    def test_lifecycle_order(self) -> None:
        nav = Navigator()
        nav.push(_Home())
        details = _Details()
        nav.push(details)
        nav.pop()
        assert details.log == ["mount", "show", "hide", "unmount"]

    def test_duplicate_push_rejected(self) -> None:
        nav = Navigator()
        home = _Home()
        nav.push(home)
        with pytest.raises(PyMobileError, match="already on the stack"):
            nav.push(home)

    def test_replace_swaps_top(self) -> None:
        nav = Navigator()
        nav.push(_Home())
        details = _Details()
        nav.replace(details)
        assert nav.depth == 1
        assert nav.current is details

    def test_reset_clears_stack(self) -> None:
        nav = Navigator()
        nav.push(_Home())
        nav.push(_Details())
        nav.reset(_Home())
        assert nav.depth == 1

    def test_stack_is_immutable_view(self) -> None:
        nav = Navigator()
        nav.push(_Home())
        assert isinstance(nav.stack, tuple)


class _Named(Screen):
    title = "Named"

    def build(self) -> Widget:
        self.counter = Label("0")
        self.start = Button("Start")
        return Column(self.counter, Label("anonymous"), self.start)


class TestWidgetIds:
    """Ids follow the code, not a global counter."""

    def test_attributes_name_their_widgets(self) -> None:
        screen = _Named()
        screen.root  # builds the tree
        assert screen.counter.id == "counter"
        assert screen.start.id == "start"

    def test_explicit_id_always_wins(self) -> None:
        class Explicit(Screen):
            def build(self) -> Widget:
                self.counter = Label("0", id="my-counter")
                return Column(self.counter)

        screen = Explicit()
        screen.root
        assert screen.counter.id == "my-counter"

    def test_anonymous_widgets_are_numbered_per_screen(self) -> None:
        """Adding a widget to one screen must not renumber another."""
        first = _Named()
        first.root
        second = _Named()
        second.root
        anonymous = [w.id for w in second.root.walk() if w.id.startswith("label-")]
        assert anonymous == ["label-2"]

    def test_counters_are_per_type(self) -> None:
        class Many(Screen):
            def build(self) -> Widget:
                return Column(Label("a"), Button("b"), Label("c"))

        ids = [widget.id for widget in Many().root.walk()]
        assert ids == ["column-1", "label-1", "button-1", "label-2"]

    def test_ids_stay_stable_when_a_widget_is_added_above(self) -> None:
        class Before(Screen):
            def build(self) -> Widget:
                self.total = Label("42")
                return Column(self.total)

        class After(Screen):
            def build(self) -> Widget:
                self.total = Label("42")
                return Column(Label("new heading"), self.total)

        before, after = Before(), After()
        before.root, after.root
        assert before.total.id == after.total.id == "total"

    def test_find_uses_the_readable_id(self) -> None:
        screen = _Named()
        assert screen.find("counter") is screen.counter

    def test_widgets_outside_the_tree_keep_their_id(self) -> None:
        class Detached(Screen):
            def build(self) -> Widget:
                self.unused = Label("not mounted")
                return Column(Label("shown"))

        screen = Detached()
        screen.root
        assert screen.unused.id.startswith("label-")


class _Counter(Screen):
    """A screen that keeps a handle on the widget it mutates."""

    title = "Counter"

    def build(self) -> Widget:
        self.label = Label("0")
        self.button = Button("Tap")
        self.field = TextInput()
        self.switch = Switch()
        self.bar = ProgressBar()
        return Column(self.label, self.button, self.field, self.switch, self.bar)


class TestAutoRender:
    """Assigning to a widget must be enough; render() is not the user's job."""

    def _app(self, bridge) -> tuple[App, _Counter]:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Counter()
        app.run(screen)
        bridge.reset()
        return app, screen

    def test_assignment_renders(self, bridge) -> None:  # type: ignore[no-untyped-def]
        _, screen = self._app(bridge)
        screen.label.text = "5"
        assert len(bridge.calls_named("render")) == 1
        assert bridge.last_tree["children"][0]["props"]["text"] == "5"

    def test_setter_method_renders(self, bridge) -> None:  # type: ignore[no-untyped-def]
        _, screen = self._app(bridge)
        screen.label.set_text("7")
        assert len(bridge.calls_named("render")) == 1

    def test_unchanged_value_does_not_render(self, bridge) -> None:  # type: ignore[no-untyped-def]
        _, screen = self._app(bridge)
        screen.label.text = "0"
        assert bridge.calls_named("render") == []

    def test_every_stateful_widget_is_reactive(self, bridge) -> None:  # type: ignore[no-untyped-def]
        _, screen = self._app(bridge)
        screen.button.text = "Pause"
        screen.field.value = "abc"
        screen.switch.checked = True
        screen.bar.value = 40
        screen.label.visible = False
        screen.button.enabled = False
        assert len(bridge.calls_named("render")) == 6

    def test_structural_changes_render(self, bridge) -> None:  # type: ignore[no-untyped-def]
        _, screen = self._app(bridge)
        column = screen.root
        assert isinstance(column, Column)
        column.add(Label("added"))
        assert len(bridge.calls_named("render")) == 1

    def test_batch_coalesces_into_one_frame(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app, screen = self._app(bridge)
        with app.batch():
            screen.label.text = "a"
            screen.button.text = "b"
            screen.bar.value = 10
        assert len(bridge.calls_named("render")) == 1

    def test_nested_batches_flush_once(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app, screen = self._app(bridge)
        with app.batch():
            screen.label.text = "a"
            with app.batch():
                screen.button.text = "b"
        assert len(bridge.calls_named("render")) == 1

    def test_batch_without_changes_renders_nothing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app, _ = self._app(bridge)
        with app.batch():
            pass
        assert bridge.calls_named("render") == []

    def test_hidden_screen_does_not_render(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app, screen = self._app(bridge)
        app.push(_Home())
        bridge.reset()
        screen.label.text = "background"
        assert bridge.calls_named("render") == []

    def test_detached_widget_is_safe_to_mutate(self) -> None:
        Label("free").text = "still free"  # must not raise

    def test_auto_render_can_be_switched_off(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge, auto_render=False)
        screen = _Counter()
        app.run(screen)
        bridge.reset()
        screen.label.text = "5"
        assert bridge.calls_named("render") == []
        app.render()
        assert len(bridge.calls_named("render")) == 1

    def test_manual_mode_warns_once(self, bridge, capsys) -> None:  # type: ignore[no-untyped-def]
        """Silence is the trap; warn, but do not nag on every keystroke.

        The framework logger deliberately does not propagate to the root, so
        the warning is read back from stderr rather than through caplog.
        """
        app = App("Demo", bridge=bridge, auto_render=False)
        screen = _Counter()
        app.run(screen)
        capsys.readouterr()
        screen.label.text = "a"
        screen.label.text = "b"
        assert capsys.readouterr().err.count("auto_render is off") == 1


class _Ticker(Screen):
    title = "Ticker"

    def __init__(self) -> None:
        super().__init__()
        self.ticks = 0

    def build(self) -> Widget:
        return Column(Label("tick"))

    def on_mount(self) -> None:
        self.on("pomodoro:tick", self._tick)

    def _tick(self, _event) -> None:  # type: ignore[no-untyped-def]
        self.ticks += 1


class TestScreenSubscriptions:
    def test_handler_receives_events(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Ticker()
        app.run(screen)
        app.events.emit("pomodoro:tick")
        assert screen.ticks == 1

    def test_unmount_cancels_the_subscription(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The bug this fixes: a popped screen kept reacting to events."""
        app = App("Demo", bridge=bridge)
        app.run(_Home())
        screen = app.push(_Ticker())
        app.pop()
        app.events.emit("pomodoro:tick")
        assert screen.ticks == 0
        assert "pomodoro:tick" not in app.events

    def test_pushing_twice_does_not_double_up(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        app.run(_Home())
        first = app.push(_Ticker())
        app.pop()
        second = app.push(_Ticker())
        app.events.emit("pomodoro:tick")
        assert (first.ticks, second.ticks) == (0, 1)
        assert len(app.events) == 1

    def test_replace_and_reset_also_cancel(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        app.run(_Home())
        replaced = app.push(_Ticker())
        app.navigator.replace(_Home())
        app.navigator.reset(_Home())
        app.events.emit("pomodoro:tick")
        assert replaced.ticks == 0

    def test_app_on_can_be_bound_to_a_screen(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        app.run(_Home())
        screen = app.push(_Home())
        seen: list[str] = []
        app.on("ping", lambda e: seen.append(e.name), screen=screen)
        app.pop()
        app.events.emit("ping")
        assert seen == []

    def test_subscription_can_still_be_cancelled_by_hand(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Ticker()
        app.run(screen)
        screen._subscriptions[0].cancel()
        app.events.emit("pomodoro:tick")
        assert screen.ticks == 0

    def test_subscribing_before_mount_is_an_error(self) -> None:
        with pytest.raises(PyMobileError, match="running app"):
            _Ticker().on("x", lambda _e: None)


class TestRefresh:
    def test_refresh_rebuilds_and_renders(self, bridge) -> None:  # type: ignore[no-untyped-def]
        app = App("Demo", bridge=bridge)
        screen = _Home()
        app.run(screen)
        first = screen.root
        screen.refresh()
        assert screen.root is not first
        assert len(bridge.calls_named("render")) >= 2


class TestOneFramePerInteraction:
    """Batching lives inside handle_ui_event, not in each front end."""

    def test_a_tap_that_changes_three_widgets_renders_once(self, bridge) -> None:  # type: ignore[no-untyped-def]
        class Triple(Screen):
            def build(self) -> Widget:
                self.a = Label("a")
                self.b = Label("b")
                self.c = Label("c")
                self.button = Button("go", on_press=self.go)
                return Column(self.a, self.b, self.c, self.button)

            def go(self) -> None:
                self.a.text = "1"
                self.b.text = "2"
                self.c.text = "3"

        app = App("Demo", bridge=bridge)
        screen = Triple()
        app.run(screen)
        bridge.reset()
        app.handle_ui_event(screen.button.id, "press", "")
        assert len(bridge.calls_named("render")) == 1

    def test_every_front_end_uses_the_same_entry_point(self) -> None:
        """gui.py and web.py must not reach past the batching wrapper."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for name in ("core/ui/gui.py", "core/ui/web.py"):
            source = (root / name).read_text(encoding="utf-8")
            assert "app.handle_ui_event(" in source, name
            assert "_handle_ui_event(" not in source, name


class TestScreensAsTabs:
    """Only ``navigator.current`` is rendered; a tab is rebuilt by its shell.

    The model is deliberate (see "Screens as tabs" in the README) — these tests
    pin the behaviour down and the diagnostics that replaced the silence.
    """

    def test_refresh_on_a_tab_is_reported(self, capsys: pytest.CaptureFixture[str]) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()

        class Tab(Screen):
            def build(self) -> Widget:
                return Label("tab")

        Tab().refresh()  # never pushed: no frame contains this tree
        err = capsys.readouterr().err
        assert "nothing displays it" in err
        assert "Screens as tabs" in err

    def test_the_report_is_emitted_once_per_screen(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()

        class Tab(Screen):
            def build(self) -> Widget:
                return Label("tab")

        tab = Tab()
        tab.refresh()
        tab.refresh()
        assert capsys.readouterr().err.count("nothing displays it") == 1

    def test_refresh_on_a_stacked_screen_is_quiet(
        self, bridge, capsys: pytest.CaptureFixture[str]  # type: ignore[no-untyped-def]
    ) -> None:
        """A hidden screen keeps its rebuilt tree for when it comes back."""
        from pymobile.logging import configure

        configure("warning")
        app = App("Demo", bridge=bridge)
        first = _Counter()
        app.run(first)
        app.push(_Home())
        capsys.readouterr()
        first.refresh()
        assert "nothing displays it" not in capsys.readouterr().err

    def test_a_shell_repaints_the_active_tab(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The documented pattern: tabs hold data, the shell holds the frame."""

        class Tab:
            title = "Home"

            def __init__(self, shell: Shell) -> None:
                self.shell = shell
                self.items: list[str] = []

            def build(self) -> Widget:
                return Column(Label(f"{len(self.items)} items"), Button("Load", on_press=self.load))

            def load(self) -> None:
                self.items = ["a", "b"]
                self.shell.notify_data_changed()

        class Shell(Screen):
            def __init__(self) -> None:
                super().__init__()
                # Tabs live outside build(): a rebuild re-runs build(), and a
                # tab recreated there would lose the data it holds.
                self.tabs = [Tab(self)]

            def build(self) -> Widget:
                return Column(self.tabs[0].build())

            def notify_data_changed(self) -> None:
                self.refresh()

        app = App("Demo", bridge=bridge)
        shell = Shell()
        app.run(shell)
        bridge.reset()
        shell.tabs[0].load()
        assert len(bridge.calls_named("render")) == 1
        text = shell.root.children[0].children[0].to_dict()["props"]["text"]
        assert text == "2 items"

    def test_a_refresh_inside_build_does_not_recurse(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """render() → build() → refresh() → render() used to overflow the stack."""

        class SelfRefreshing(Screen):
            def __init__(self) -> None:
                super().__init__()
                self.builds = 0

            def build(self) -> Widget:
                self.builds += 1
                self.refresh()  # "data changed" from inside the frame being built
                return Column(Label("shell"))

            def on_show(self) -> None:
                self.refresh()  # drop the tree, so the next frame rebuilds it

        app = App("Demo", bridge=bridge)
        screen = SelfRefreshing()
        app.run(screen)  # RecursionError before render() became non-re-entrant
        assert screen.builds <= 3
        assert len(bridge.calls_named("render")) >= 1

    def test_a_tree_that_redraws_on_every_frame_is_stopped(
        self, bridge, capsys: pytest.CaptureFixture[str]  # type: ignore[no-untyped-def]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        holder: list[App] = []

        class Chatty(Widget):
            type_name = "Label"

            def props(self):  # type: ignore[no-untyped-def]
                holder[0].render()  # asks for another frame on every serialise
                return {"text": "x"}

        class Home(Screen):
            def build(self) -> Widget:
                return Column(Chatty())

        app = App("Demo", bridge=bridge)
        holder.append(app)
        app.run(Home())
        assert "stopping the redraw loop" in capsys.readouterr().err
        assert len(bridge.calls_named("render")) <= 16  # bounded, not endless
