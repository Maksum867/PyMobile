"""Built-in Banner, Pagination, TreeView and ColorPicker widgets."""

from __future__ import annotations

import pytest

import pymobile
from pymobile import (
    Banner,
    ColorPicker,
    Column,
    ExpansionPanel,
    IconButton,
    Pagination,
    TreeNode,
    TreeView,
)
from pymobile.core import ui
from pymobile.core.ui.audit import audit_ui
from pymobile.core.ui.patterns import _readable_text
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.registry import unknown_types
from pymobile.core.ui.web import render_html

NEW_NAMES = ("Banner", "ColorPicker", "Pagination", "TreeNode", "TreeView")


def find(widget, predicate):
    """Return the first descendant (or the widget itself) matching ``predicate``."""
    for node in widget.walk():
        if predicate(node):
            return node
    raise AssertionError("no matching widget")


def button_named(widget, text: str):
    return find(widget, lambda n: getattr(n, "text", None) == text and n.type_name == "Button")


def iconbutton_labelled(widget, label: str) -> IconButton:
    return find(widget, lambda n: isinstance(n, IconButton) and n.label == label)


# --------------------------------------------------------------------------
# exports and the native-type contract
# --------------------------------------------------------------------------


class TestExportsAndRenderers:
    @pytest.mark.parametrize("name", NEW_NAMES)
    def test_exported_from_the_package_and_the_ui_layer(self, name: str) -> None:
        assert name in pymobile.__all__
        assert getattr(pymobile, name) is getattr(ui, name)

    def test_all_four_compose_only_existing_native_types(self) -> None:
        tree = Column(
            Banner("Saved", kind="success", action_label="Undo", on_action=lambda: None),
            Pagination(total_pages=9, current=4),
            TreeView(TreeNode("Docs", TreeNode("Reports")), TreeNode("Downloads")),
            ColorPicker(["#E53935", "#1E88E5"], value="#1E88E5"),
        ).to_dict()
        for renderer in ("android", "web", "gui", "ascii"):
            assert unknown_types(tree, renderer=renderer) == frozenset()

    def test_every_desktop_renderer_draws_them(self) -> None:
        tree = Column(
            Banner("Offline", kind="error", dismissible=True),
            Pagination(total_pages=12, current=6),
            TreeView(TreeNode("Docs", TreeNode("Reports"), expanded=True)),
            ColorPicker(["#E53935", "#1E88E5"], value="#E53935"),
        )
        ascii_ = render_ascii(tree)
        assert "Offline" in ascii_ and "Docs" in ascii_
        html = render_html(tree.to_dict())
        assert "Offline" in html and "&lt;" not in html


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------


class TestBanner:
    def test_message_and_kind_are_kept(self) -> None:
        banner = Banner("Email not verified", kind="warning")
        assert banner.message == "Email not verified"
        assert banner.kind == "warning"
        assert banner.dismissed is False

    @pytest.mark.parametrize("kind", ["info", "success", "warning", "error"])
    def test_every_kind_renders_without_audit_issues(self, kind: str) -> None:
        banner = Banner("Message", kind=kind, action_label="Ok", on_action=lambda: None)
        assert audit_ui(banner) == []

    @pytest.mark.parametrize("kind", ["info", "success", "warning", "error"])
    def test_icon_is_labelled_with_the_kind_for_screen_readers(self, kind: str) -> None:
        icon = find(Banner("Message", kind=kind), lambda n: n.type_name == "Icon")
        assert icon.label == kind

    def test_unknown_kind_lists_the_valid_ones(self) -> None:
        with pytest.raises(ValueError, match="info, success, warning, error"):
            Banner("x", kind="fatal")

    @pytest.mark.parametrize("message", ["", "   ", None])
    def test_empty_message_is_rejected(self, message) -> None:
        with pytest.raises(ValueError):
            Banner(message)

    def test_action_button_calls_on_action(self) -> None:
        calls: list[str] = []
        banner = Banner("Update ready", action_label="Install", on_action=lambda: calls.append("x"))
        button_named(banner, "Install").on_press()
        assert calls == ["x"]

    def test_action_without_handler_is_disabled(self) -> None:
        banner = Banner("Update ready", action_label="Install")
        assert button_named(banner, "Install").enabled is False

    def test_dismiss_hides_once_and_reports_once(self) -> None:
        seen: list[str] = []
        banner = Banner("Tip", dismissible=True, on_dismiss=lambda: seen.append("d"))
        iconbutton_labelled(banner, "Dismiss").on_press()
        assert banner.dismissed is True
        assert banner.visible is False
        banner.dismiss()
        assert seen == ["d"]

    def test_no_close_button_unless_dismissible(self) -> None:
        banner = Banner("Tip")
        assert not any(isinstance(n, IconButton) for n in banner.walk())


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


class TestPaginationPages:
    @pytest.mark.parametrize(
        ("total", "current", "expected"),
        [
            (12, 1, [1, 2, None, 12]),
            (12, 6, [1, None, 5, 6, 7, None, 12]),
            (12, 12, [1, None, 11, 12]),
            # One hidden page is shown instead of "…".
            (7, 4, [1, 2, 3, 4, 5, 6, 7]),
            (5, 1, [1, 2, None, 5]),
            (1, 1, [1]),
        ],
    )
    def test_numbers_with_gaps(self, total: int, current: int, expected: list) -> None:
        assert Pagination(total_pages=total, current=current)._numbers() == expected

    def test_siblings_widen_the_window(self) -> None:
        pager = Pagination(total_pages=12, current=6, siblings=2)
        assert pager._numbers() == [1, None, 4, 5, 6, 7, 8, None, 12]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"total_pages": 0},
            {"total_pages": True},
            {"total_pages": 3, "current": 4},
            {"total_pages": 3, "current": 0},
            {"total_pages": 3, "current": True},
            {"total_pages": 3, "siblings": -1},
        ],
    )
    def test_invalid_arguments_are_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            Pagination(**kwargs)


class TestPaginationBehaviour:
    def test_tapping_a_page_reports_it(self) -> None:
        seen: list[int] = []
        pager = Pagination(total_pages=5, current=1, on_change=seen.append)
        # Page 3 is hidden behind "…" here, so tap the visible page 2.
        button_named(pager, "2").on_press()
        assert pager.current == 2
        assert seen == [2]

    def test_tapping_the_current_page_does_nothing(self) -> None:
        seen: list[int] = []
        pager = Pagination(total_pages=5, current=2, on_change=seen.append)
        pager.select(2)
        assert seen == []

    def test_next_and_previous_stop_at_the_ends(self) -> None:
        seen: list[int] = []
        pager = Pagination(total_pages=3, current=1, on_change=seen.append)
        pager.previous()
        assert pager.current == 1
        pager.next()
        pager.next()
        pager.next()
        assert pager.current == 3
        assert seen == [2, 3]

    def test_arrow_buttons_are_disabled_at_the_ends(self) -> None:
        first = Pagination(total_pages=3, current=1)
        assert iconbutton_labelled(first, "Previous page").enabled is False
        assert iconbutton_labelled(first, "Next page").enabled is True
        last = Pagination(total_pages=3, current=3)
        assert iconbutton_labelled(last, "Next page").enabled is False

    def test_select_out_of_range_raises(self) -> None:
        pager = Pagination(total_pages=3)
        for bad in (0, 4, True, "2"):
            with pytest.raises(ValueError):
                pager.select(bad)  # type: ignore[arg-type]

    def test_current_page_is_marked_for_screen_readers(self) -> None:
        pager = Pagination(total_pages=3, current=2)
        assert button_named(pager, "2").accessibility_label == "Page 2, current"
        assert button_named(pager, "1").accessibility_label == "Page 1"

    def test_rendered_pagination_has_no_audit_issues(self) -> None:
        assert audit_ui(Pagination(total_pages=12, current=6)) == []


# --------------------------------------------------------------------------
# TreeView
# --------------------------------------------------------------------------


class TestTreeNode:
    def test_label_and_children(self) -> None:
        node = TreeNode("Docs", TreeNode("Reports"))
        assert node.label == "Docs"
        assert len(node.children) == 1
        assert node.expanded is False

    def test_empty_label_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            TreeNode("  ")

    def test_children_must_be_tree_nodes(self) -> None:
        with pytest.raises(TypeError):
            TreeNode("Docs", "Reports")  # type: ignore[arg-type]


class TestTreeView:
    def test_leaf_tap_reports_the_node(self) -> None:
        seen: list[TreeNode] = []
        leaf = TreeNode("Reports")
        tree = TreeView(TreeNode("Docs", leaf), on_select=seen.append)
        button_named(tree, "• Reports").on_press()
        assert seen == [leaf]

    def test_folder_tap_only_opens_it(self) -> None:
        seen: list[TreeNode] = []
        folder = TreeNode("Docs", TreeNode("Reports"))
        tree = TreeView(folder, on_select=seen.append)
        assert folder.expanded is False
        find(tree, lambda n: isinstance(n, ExpansionPanel)).toggle()
        assert folder.expanded is True
        assert seen == []

    def test_expanded_flag_starts_the_folder_open(self) -> None:
        tree = TreeView(TreeNode("Docs", TreeNode("Reports"), expanded=True))
        panel = find(tree, lambda n: isinstance(n, ExpansionPanel))
        assert panel.expanded is True

    def test_nested_levels_render(self) -> None:
        deep = TreeNode("a", TreeNode("b", TreeNode("c")))
        html = render_html(TreeView(deep, TreeNode("z")).to_dict())
        assert "c" in html

    def test_only_tree_nodes_are_accepted(self) -> None:
        with pytest.raises(TypeError):
            TreeView("Docs")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# ColorPicker
# --------------------------------------------------------------------------


PALETTE = ["#E53935", "#1E88E5", "#43A047", "#FDD835"]


class TestColorPickerValidation:
    @pytest.mark.parametrize("bad", ["red", "#FFF", "#GGGGGG", "1E88E5", "#1E88E5FF"])
    def test_colours_must_be_six_digit_hex(self, bad: str) -> None:
        with pytest.raises(ValueError):
            ColorPicker([bad])

    def test_duplicates_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            ColorPicker(["#E53935", "#e53935"])

    def test_empty_palette_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ColorPicker([])

    def test_value_must_be_in_the_palette(self) -> None:
        with pytest.raises(ValueError):
            ColorPicker(PALETTE, value="#000000")

    def test_swatch_must_meet_the_touch_target(self) -> None:
        with pytest.raises(ValueError, match="48"):
            ColorPicker(PALETTE, swatch_size=40)

    def test_initial_value_is_normalised_to_upper_case(self) -> None:
        picker = ColorPicker(PALETTE, value="#1e88e5")
        assert picker.value == "#1E88E5"

    def test_no_initial_value_means_none(self) -> None:
        assert ColorPicker(PALETTE).value is None


class TestColorPickerBehaviour:
    def test_tapping_a_swatch_selects_it(self) -> None:
        seen: list[str] = []
        picker = ColorPicker(PALETTE, on_change=seen.append)
        button_named_by_label(picker, "Color #43A047").on_press()
        assert picker.value == "#43A047"
        assert seen == ["#43A047"]

    def test_selecting_the_same_colour_does_nothing(self) -> None:
        seen: list[str] = []
        picker = ColorPicker(PALETTE, value="#E53935", on_change=seen.append)
        picker.select("#e53935")
        assert seen == []

    def test_select_rejects_colours_outside_the_palette(self) -> None:
        picker = ColorPicker(PALETTE)
        with pytest.raises(ValueError):
            picker.select("#000000")

    def test_selected_swatch_is_marked_with_a_tick_and_label(self) -> None:
        picker = ColorPicker(PALETTE, value="#1E88E5")
        selected = button_named_by_label(picker, "Color #1E88E5, selected")
        assert selected.text == "✓"
        other = button_named_by_label(picker, "Color #E53935")
        assert other.text == ""

    def test_palette_passes_the_contrast_audit(self) -> None:
        picker = ColorPicker([*PALETTE, "#808080", "#FFFFFF", "#000000"], value="#808080")
        assert [issue for issue in audit_ui(picker) if issue.code == "contrast"] == []

    @pytest.mark.parametrize("background", ["#E53935", "#1E88E5", "#FDD835", "#808080"])
    def test_swatch_text_colour_has_at_least_4_5_to_1_contrast(self, background: str) -> None:
        def linear(channel: int) -> float:
            value = channel / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        rgb = [int(background[i : i + 2], 16) for i in (1, 3, 5)]
        lum = 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2])
        text = _readable_text(background)
        text_lum = 0.0 if text == "#000000" else 1.0
        ratio = (max(lum, text_lum) + 0.05) / (min(lum, text_lum) + 0.05)
        assert ratio >= 4.5


def button_named_by_label(widget, label: str):
    return find(widget, lambda n: getattr(n, "accessibility_label", None) == label)


# --------------------------------------------------------------------------
# inside a running app: taps arrive as UI events and survive a refresh
# --------------------------------------------------------------------------


class TestInsideAnApp:
    def test_pagination_tap_through_the_event_path(self, bridge, tmp_path) -> None:
        from pymobile import App, Screen

        class Holder(Screen):
            def __init__(self, widget) -> None:
                super().__init__()
                self.widget = widget

            def build(self):
                return self.widget

        seen: list[int] = []
        pager = Pagination(total_pages=5, current=1, on_change=seen.append, id="pages")
        app = App("t", bridge=bridge, storage_path=str(tmp_path / "s.json"))
        app.run(Holder(pager))
        try:
            app.handle_ui_event(f"{pager.id}:page-2", "press", "")
            assert pager.current == 2
            assert seen == [2]
            app.screen.refresh()
            assert pager.current == 2
            assert all(child.parent is not None for child in pager.children)
        finally:
            app.stop()
