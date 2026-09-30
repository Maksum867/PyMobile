"""``Wrap`` and long ``SegmentedButtons`` labels (roadmap items #LAY-05 and the 5-segment bug).

The reported failure: "Географія" in a five-segment control at 360 dp was squeezed
into a column of letters. Segments now keep their natural width and the bar
scrolls, or — with ``wrap=True`` — flows onto more lines; ``Wrap`` is the general
flow container.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

import pymobile
from pymobile import (
    Chip,
    Column,
    Label,
    SegmentedButtons,
    Wrap,
)
from pymobile.core.ui.flow import ALIGNMENTS, flow_lines, line_offset
from pymobile.core.ui.gui import place_flow, skeleton
from pymobile.core.ui.preview import render_ascii
from pymobile.core.ui.registry import supported_by, unknown_types, widget_types
from pymobile.core.ui.web import render_html

ANDROID = Path(pymobile.__file__).resolve().parent / "resources" / "android"
VIEW_BUILDER = ANDROID / "java" / "ViewBuilder.java"
SUBJECTS = ["Географія", "Історія", "Математика", "Біологія", "Хімія"]


# --------------------------------------------------------------------------
# the line-breaking rule
# --------------------------------------------------------------------------
class TestFlowRule:
    def test_items_fill_a_line_then_start_the_next(self) -> None:
        assert flow_lines([100, 100, 100, 100], 250, 10) == [[0, 1], [2, 3]]
        assert flow_lines([100, 100, 100], 320, 10) == [[0, 1, 2]]  # 100+10+100+10+100 = 320
        assert flow_lines([100, 100, 100], 319, 10) == [[0, 1], [2]]

    def test_an_item_wider_than_the_line_gets_a_line_of_its_own(self) -> None:
        assert flow_lines([50, 400, 50], 300, 0) == [[0], [1], [2]]
        assert flow_lines([400], 300, 0) == [[0]]

    def test_hidden_items_take_no_place_and_no_gap(self) -> None:
        assert flow_lines([100, -1, 100], 210, 10) == [[0, 2]]
        assert flow_lines([-1, -1], 100, 0) == []

    def test_nothing_gives_no_lines(self) -> None:
        assert flow_lines([], 100, 5) == []

    def test_a_zero_width_item_still_costs_its_gap(self) -> None:
        assert flow_lines([0, 0, 0], 5, 3) == [[0, 1], [2]]  # 0 + 3 + 0 = 3; 3 + 3 > 5

    @pytest.mark.parametrize("seed", range(20))
    def test_lines_are_greedy_and_never_overflow_unless_alone(self, seed: int) -> None:
        rng = random.Random(seed)
        widths = [rng.choice([-1, 0, 10, 40, 90, 200, 500]) for _ in range(rng.randint(0, 12))]
        available, gap = rng.choice([50, 100, 360]), rng.choice([0, 4, 8])
        lines = flow_lines(widths, available, gap)
        placed = sorted(i for line in lines for i in line)
        assert placed == [i for i, w in enumerate(widths) if w >= 0]
        for position, line in enumerate(lines):
            used = sum(widths[i] for i in line) + gap * (len(line) - 1)
            assert used <= available or len(line) == 1
            if position + 1 < len(lines):  # greedy: the next item really did not fit
                assert used + gap + widths[lines[position + 1][0]] > available

    @pytest.mark.parametrize(
        ("align", "expected"), [("start", 0.0), ("center", 50.0), ("end", 100.0)]
    )
    def test_a_short_line_is_placed_by_the_alignment(self, align: str, expected: float) -> None:
        assert line_offset(align, 300, 200) == expected

    def test_a_line_that_fills_the_width_has_no_offset(self) -> None:
        assert line_offset("end", 300, 300) == 0.0
        assert line_offset("center", 300, 500) == 0.0
        assert ALIGNMENTS == ("start", "center", "end")


# --------------------------------------------------------------------------
# the widgets
# --------------------------------------------------------------------------
class TestWrapWidget:
    def test_defaults_and_serialisation(self) -> None:
        node = Wrap(Label("a", id="a"), Label("b", id="b"), id="w").to_dict()
        assert node["type"] == "Wrap"
        assert node["props"] == {"spacing": 0, "run_spacing": 0, "align": "start"}
        assert [child["id"] for child in node["children"]] == ["a", "b"]

    def test_run_spacing_follows_spacing_until_it_is_given(self) -> None:
        assert Wrap(spacing=8).props()["run_spacing"] == 8
        assert Wrap(spacing=8, run_spacing=2).props()["run_spacing"] == 2
        assert Wrap(spacing=8, run_spacing=0).props()["run_spacing"] == 0

    @pytest.mark.parametrize("align", ["start", "center", "end"])
    def test_alignments(self, align: str) -> None:
        assert Wrap(align=align).props()["align"] == align

    @pytest.mark.parametrize(
        "kwargs",
        [{"spacing": -1}, {"run_spacing": -1}, {"align": "space_between"}, {"align": "left"}],
    )
    def test_bad_arguments_are_refused(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            Wrap(**kwargs)

    def test_it_is_a_container(self) -> None:
        wrap = Wrap()
        label = wrap.add(Label("x", id="x"))
        assert wrap.children == (label,)
        assert wrap.find("x") is label

    def test_mutating_a_wrap_on_screen_redraws_like_any_container(self) -> None:
        wrap = Wrap(Label("a", id="a"))
        wrap.add(Label("b", id="b"))
        wrap.spacing = 12
        assert wrap.props()["spacing"] == 12

    def test_it_is_public(self) -> None:
        from pymobile.core.ui import Wrap as FromUi
        from pymobile.core.ui.layout import Wrap as FromLayout

        assert pymobile.Wrap is Wrap is FromUi is FromLayout
        assert "Wrap" in pymobile.__all__

    def test_it_is_a_built_in_type_every_renderer_knows(self) -> None:
        assert "Wrap" in widget_types()
        for renderer in ("android", "web", "gui", "ascii"):
            assert "Wrap" in supported_by(renderer)
        tree = Column(Wrap(Label("x", id="x"), id="w"), id="root").to_dict()
        assert unknown_types(tree) == frozenset()
        assert unknown_types(tree, renderer="android") == frozenset()


class TestSegmentedWrapOption:
    def test_wrap_is_off_by_default_and_serialised(self) -> None:
        assert SegmentedButtons(SUBJECTS).props()["wrap"] is False
        assert SegmentedButtons(SUBJECTS, wrap=True).props()["wrap"] is True

    def test_it_can_be_switched_later(self) -> None:
        bar = SegmentedButtons(SUBJECTS)
        bar.wrap = True
        assert bar.props()["wrap"] is True


# --------------------------------------------------------------------------
# the renderers
# --------------------------------------------------------------------------
class TestAscii:
    def chips(self, *names: str) -> list[Chip]:
        return [Chip(name, id=f"chip-{index}") for index, name in enumerate(names)]

    def test_a_wrap_breaks_into_lines(self) -> None:
        picture = render_ascii(
            Wrap(*self.chips("python", "android", "kotlin", "мобільні застосунки", "ui", "flow"))
        )
        assert picture.splitlines() == [
            "(python)  (android)  (kotlin)",
            "(мобільні застосунки)  (ui)  (flow)",
        ]

    def test_a_short_wrap_stays_on_one_line(self) -> None:
        assert render_ascii(Wrap(*self.chips("a", "b", "c"))) == "(a)  (b)  (c)"

    def test_center_alignment_indents_short_lines(self) -> None:
        lines = render_ascii(Wrap(*self.chips("aaaa", "bb"), align="center")).splitlines()
        assert len(lines) == 1
        assert lines[0].startswith(" ")  # centred in the 40-character picture
        assert lines[0].strip() == "(aaaa)  (bb)"

    def test_hidden_children_leave_no_gap(self) -> None:
        chips = self.chips("a", "b", "c")
        chips[1].visible = False
        assert render_ascii(Wrap(*chips)) == "(a)  (c)"

    def test_wrapping_segments_flow_onto_lines_and_the_default_stays_one_line(self) -> None:
        wrapped = render_ascii(SegmentedButtons(SUBJECTS, wrap=True)).splitlines()
        assert wrapped == ["|Географія|  Історія   Математика", " Біологія   Хімія"]
        single = render_ascii(SegmentedButtons(SUBJECTS)).splitlines()
        assert len(single) == 1


class TestWeb:
    def test_wrap_is_a_wrapping_flex_container(self) -> None:
        html = render_html(
            Column(
                Wrap(Chip("a", id="a"), spacing=8, run_spacing=4, align="center", id="w"),
                id="root",
            ).to_dict()
        )
        assert 'class="wrap"' in html
        assert "flex-wrap:wrap" in html
        assert "gap:4px 8px" in html
        assert "justify-content:center" in html

    def test_segments_scroll_by_default_and_wrap_on_request(self) -> None:
        plain = render_html(SegmentedButtons(SUBJECTS, id="s").to_dict())
        assert '<div class="seg"' in plain
        wrapped = render_html(SegmentedButtons(SUBJECTS, wrap=True, id="s").to_dict())
        assert '<div class="seg wrap"' in wrapped

    def test_the_stylesheet_lets_segments_keep_their_width(self) -> None:
        from pymobile.core.ui.web import _PAGE  # the page template: braces are doubled

        assert ".seg {{ display: flex; gap: 0; overflow-x: auto; }}" in _PAGE
        assert "white-space: nowrap" in _PAGE
        assert ".seg.wrap {{ flex-wrap: wrap; overflow-x: visible; }}" in _PAGE


class FakeTk:
    """Enough of a Tk widget to run ``place_flow`` on a machine without a display."""

    def __init__(self, width: int = 0, height: int = 0) -> None:
        self.width = width
        self.height = height
        self.position: tuple[int, int] | None = None
        self.configured = 0

    def winfo_width(self) -> int:
        return self.width

    def winfo_reqwidth(self) -> int:
        return self.width

    def winfo_reqheight(self) -> int:
        return self.height

    def place(self, *, x: int, y: int) -> None:
        self.position = (x, y)

    def cget(self, option: str) -> int:
        assert option == "height"
        return self.height

    def configure(self, *, height: int) -> None:
        self.configured += 1
        self.height = height


class TestTk:
    def test_cells_are_placed_in_lines_and_the_frame_gets_their_height(self) -> None:
        frame = FakeTk(width=300, height=1)
        cells = [FakeTk(100, 30), FakeTk(100, 20), FakeTk(100, 40), FakeTk(60, 10)]
        place_flow(frame, cells, gap=10, run_gap=5)
        assert [cell.position for cell in cells] == [(0, 0), (110, 0), (0, 35), (110, 35)]
        assert frame.height == 35 + 40

    def test_alignment_and_a_second_pass_that_changes_nothing(self) -> None:
        frame = FakeTk(width=300, height=1)
        cells = [FakeTk(100, 30), FakeTk(50, 30)]
        place_flow(frame, cells, gap=10, run_gap=0, align="end")
        assert [cell.position for cell in cells] == [(140, 0), (250, 0)]
        before = frame.configured
        place_flow(frame, cells, gap=10, run_gap=0, align="end")
        assert frame.configured == before  # the height did not change: no <Configure> loop

    def test_an_unmapped_frame_is_left_alone(self) -> None:
        frame = FakeTk(width=1, height=1)
        cell = FakeTk(10, 10)
        place_flow(frame, [cell], 0, 0)
        assert cell.position is None

    def test_a_failure_is_logged_not_raised(self) -> None:
        class Broken(FakeTk):
            def winfo_reqwidth(self) -> int:
                raise RuntimeError("boom")

        place_flow(FakeTk(width=200, height=1), [Broken(10, 10)], 0, 0)  # must not raise

    def test_the_skeleton_tells_a_wrap_from_a_row(self) -> None:
        wrap = skeleton(Wrap(Label("a", id="a"), id="c").to_dict())
        column = skeleton(Column(Label("a", id="a"), id="c").to_dict())
        assert wrap != column


# --------------------------------------------------------------------------
# the mockup: what the phone would draw
# --------------------------------------------------------------------------
@pytest.fixture
def layout() -> Any:
    pytest.importorskip("PIL")
    from PIL import ImageFont

    from pymobile.core.ui.mockup import _Fonts, _Layout, _palette

    return _Layout(_Fonts(ImageFont, 2), _palette(None), None, 2)


def laid_out(layout: Any, node: Any, width: float = 360) -> Any:
    box = layout.layout(node.to_dict(), width, fill=True)
    assert box is not None
    return box


class TestMockup:
    def test_a_wrap_flows_onto_new_lines_at_the_container_width(self, layout: Any) -> None:
        chips = [Chip(text, id=f"c{i}") for i, text in enumerate(["python", "android", "kotlin"])]
        chips.append(Chip("мобільні застосунки та інше", id="long"))
        box = laid_out(layout, Wrap(*chips, spacing=8, run_spacing=4, id="w"))
        kids = box.kids
        assert kids[0][:2] == (0.0, 0.0)  # first chip at the origin
        assert kids[1][1] == kids[0][1]  # the second on the same line
        assert kids[3][1] > kids[0][1]  # the long one wrapped down
        for x, _, kid in kids:
            assert x + kid.outer_w <= 360 + 0.01 or x == 0  # nothing hangs off the line
        first_line_height = max(kid.outer_h for _, y, kid in kids if y == 0)
        assert kids[3][1] == pytest.approx(first_line_height + 4)  # run_spacing between lines

    def test_a_wrap_of_one_wide_child_gives_it_the_line(self, layout: Any) -> None:
        box = laid_out(layout, Wrap(Label("x " * 80, id="wide"), id="w"))
        assert box.kids[0][:2] == (0.0, 0.0)
        assert box.kids[0][2].w <= 360

    def test_center_alignment_offsets_the_short_line(self, layout: Any) -> None:
        box = laid_out(layout, Wrap(Chip("hi", id="c"), align="center", id="w"))
        x, _, kid = box.kids[0]
        assert x == pytest.approx((360 - kid.outer_w) / 2)

    def test_hidden_children_are_skipped(self, layout: Any) -> None:
        chips = [Chip("a", id="a"), Chip("b", id="b"), Chip("c", id="c")]
        chips[1].visible = False
        box = laid_out(layout, Wrap(*chips, spacing=8, id="w"))
        assert len(box.kids) == 2
        assert box.kids[1][0] == pytest.approx(box.kids[0][2].outer_w + 8)

    def test_long_labels_keep_their_width_and_the_bar_scrolls(self, layout: Any) -> None:
        """The reported bug: five segments at 360 dp used to squeeze 'Біологія' into letters."""
        box = laid_out(layout, SegmentedButtons(SUBJECTS, id="s"))
        heights = {kid.h for _, _, kid in box.kids}
        assert heights == {48}, "a segment was squeezed into several lines"
        assert box.clip, "the overflowing bar must scroll (the mockup clips it at the edge)"
        assert box.h == 48
        assert box.w == 360

    def test_a_bar_that_fits_is_not_clipped(self, layout: Any) -> None:
        box = laid_out(layout, SegmentedButtons(["Day", "Week", "Month"], id="s"))
        assert not box.clip
        assert {kid.h for _, _, kid in box.kids} == {48}

    def test_wrapping_segments_show_every_option(self, layout: Any) -> None:
        box = laid_out(layout, SegmentedButtons(SUBJECTS, wrap=True, id="s"))
        assert not box.clip
        assert len(box.kids) == len(SUBJECTS)
        assert len({y for _, y, _ in box.kids}) >= 2  # more than one line
        assert box.h > 48
        for x, _, kid in box.kids:
            assert x + kid.outer_w <= 360 + 0.01

    def test_the_picture_can_be_drawn(self, layout: Any, tmp_path: Path) -> None:
        from PIL import Image

        from pymobile.core.ui.preview import render_mockup

        tree = Column(
            SegmentedButtons(SUBJECTS, id="s"),
            SegmentedButtons(SUBJECTS, wrap=True, id="t"),
            Wrap(*[Chip(name, id=f"c{i}") for i, name in enumerate(SUBJECTS)], spacing=8, id="w"),
            id="root",
        )
        path = render_mockup(tree, tmp_path / "wrap.png", width=360)
        with Image.open(path) as image:
            assert image.width == 720


# --------------------------------------------------------------------------
# the Android renderer (sources and the dex that ships)
# --------------------------------------------------------------------------
def method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after {signature}")


class TestAndroid:
    source = VIEW_BUILDER.read_text(encoding="utf-8")

    def test_wrap_has_a_renderer_branch(self) -> None:
        assert 'case "Wrap":\n                view = buildWrap(node, props);' in self.source
        build = method_body(self.source, "private View buildWrap(")
        assert "new FlowLayout(" in build
        assert '"run_spacing"' in build
        assert "applyBoxStyle(" in build  # child margins and explicit sizes still apply

    def test_the_flow_layout_honours_margins_gone_children_and_alignment(self) -> None:
        flow = method_body(self.source, "static final class FlowLayout extends ViewGroup")
        for marker in (
            "measureChildWithMargins",
            "View.GONE",
            "FlowMath.offset(alignment",
            "checkLayoutParams",
            "MarginLayoutParams",
        ):
            assert marker in flow, marker

    def test_segments_scroll_instead_of_squeezing(self) -> None:
        build = method_body(self.source, "private View buildSegmented(")
        assert "new HorizontalScrollView(context)" in build
        assert "new FlowLayout(context, 0, 0, FlowLayout.ALIGN_START)" in build  # wrap=true
        assert 'props.optBoolean("wrap", false)' in build
        assert "scroller.scrollTo(" in build  # the selected segment is brought into view
        # The squeezing was a plain wrap_content LinearLayout: it must not be what is returned.
        assert "return row;\n        }\n\n        final HorizontalScrollView" in build

    def test_the_in_place_update_finds_the_buttons_and_rebuilds_on_new_labels(self) -> None:
        assert "private static ViewGroup segmentRow(View view)" in self.source
        update = self.source[self.source.index('if ("SegmentedButtons".equals(type)') :]
        update = update[: update.index('if ("DataTable".equals(type)')]
        assert "segmentRow(view)" in update
        assert "return false;" in update  # different labels / wrap flag: rebuild the bar
        assert "(view instanceof FlowLayout)" in update

    def test_the_rule_is_documented_as_shared_with_python(self) -> None:
        assert "pymobile/core/ui/flow.py" in self.source
        assert "flow_lines()" in self.source

    def test_the_shipped_dex_has_the_new_renderers(self) -> None:
        dex = (ANDROID / "prebuilt" / "arm64-v8a" / "classes.dex").read_bytes()
        for symbol in (
            b"buildWrap",
            b"segmentRow",
            b"FlowLayout",
            b"FlowMath",
            b"buildUnknown",  # the placeholder the README promises for unknown types
        ):
            assert symbol in dex, f"{symbol.decode()} is missing: rebuild the dex"
        from pymobile.compiler.widgets import dex_has_case

        assert dex_has_case(dex, "Wrap")


@pytest.mark.skipif(shutil.which("javac") is None, reason="needs a JDK to compile FlowMath")
def test_java_flow_math_breaks_lines_exactly_like_python(tmp_path: Path) -> None:
    """The phone and the previews must break lines in the same places."""
    source = VIEW_BUILDER.read_text(encoding="utf-8")
    body = method_body(source, "static final class FlowMath")
    body = body.replace("FlowLayout.ALIGN_CENTER", "1").replace("FlowLayout.ALIGN_END", "2")
    assert "android" not in body, "FlowMath must stay free of Android classes"
    (tmp_path / "Check.java").write_text(
        textwrap.dedent(
            """
            import java.io.*;
            public class Check {
            """
        )
        + body
        + textwrap.dedent(
            """
                public static void main(String[] args) throws Exception {
                    BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
                    String text;
                    while ((text = in.readLine()) != null) {
                        String[] p = text.trim().split("\\\\s+");
                        int available = Integer.parseInt(p[0]);
                        int gap = Integer.parseInt(p[1]);
                        int[] widths = new int[p.length - 2];
                        for (int i = 2; i < p.length; i++) widths[i - 2] = Integer.parseInt(p[i]);
                        StringBuilder out = new StringBuilder();
                        int[] lines = FlowMath.lines(widths, available, gap);
                        for (int line : lines) out.append(line).append(' ');
                        System.out.println(out.toString().trim());
                    }
                }
            }
            """
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        ["javac", "-d", str(tmp_path), str(tmp_path / "Check.java")],
        capture_output=True,
        encoding="utf-8",
        errors="replace",  # javac speaks the OS code page; only a failure message is read
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    rng = random.Random(3)
    cases = []
    for _ in range(300):
        widths = [rng.choice([-1, 0, 10, 40, 90, 200, 500]) for _ in range(rng.randint(0, 12))]
        cases.append((rng.choice([0, 50, 100, 360]), rng.choice([0, 4, 8]), widths))
    stdin = "\n".join(f"{a} {g} " + " ".join(map(str, w)) for a, g, w in cases) + "\n"
    ran = subprocess.run(
        ["java", "-cp", str(tmp_path), "Check"],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    for (available, gap, widths), row in zip(cases, ran.stdout.splitlines(), strict=True):
        expected = [-1] * len(widths)
        for number, line in enumerate(flow_lines(widths, available, gap)):
            for index in line:
                expected[index] = number
        assert [int(part) for part in row.split()] == expected, (available, gap, widths)
