"""Tests for the widgets added in 0.14 (QRCode, TagInput, Countdown,
MarkdownText, SignaturePad).

They cover the QR matrix against ``segno``, decoding with OpenCV when it is
installed, state transitions, callbacks and the serialised tree.
"""

from __future__ import annotations

import json
import time

import pytest

from pymobile import (
    Column,
    Countdown,
    MarkdownText,
    QRCode,
    SignaturePad,
    TagInput,
)
from pymobile.core.ui.registry import unknown_types
from pymobile.errors import PyMobileError

# -- QRCode ------------------------------------------------------------------


@pytest.mark.parametrize("ecc", ["L", "M", "Q", "H"])
def test_qr_matrix_matches_segno(ecc: str) -> None:
    segno = pytest.importorskip("segno")
    text = "https://example.com/pymobile"
    code = QRCode(text, size=300, ecc=ecc, quiet_zone=0)
    ref = segno.make(text, error=ecc, micro=False)
    assert code.modules == ref.symbol_size(scale=1, border=0)[0]
    mod = code.module_size
    assert code.width == code.modules * mod <= 300
    grid = [[0] * code.modules for _ in range(code.modules)]
    for el in code.elements:
        g = el._geometry
        x0, y0 = round(g["x"] / mod), round(g["y"] / mod)
        for yy in range(y0, y0 + round(g["h"] / mod)):
            for xx in range(x0, x0 + round(g["w"] / mod)):
                grid[yy][xx] = 1
    expected = [[1 if c else 0 for c in row] for row in ref.matrix]
    assert grid == expected


@pytest.mark.parametrize("size", [240, 320, 500, 800, 1000])
def test_qr_decodes_with_opencv(size: int) -> None:
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    text = "PyMobile widgets"
    code = QRCode(text, size=size, ecc="M", quiet_zone=4)
    assert code.width <= size
    img = Image.new("RGB", (code.width, code.height), "white")
    draw = ImageDraw.Draw(img)
    for el in code.elements:
        g = el._geometry
        draw.rectangle([g["x"], g["y"], g["x"] + g["w"] - 1, g["y"] + g["h"] - 1], fill="black")
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(
        cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    )
    assert data == text


def test_qr_rejects_bad_arguments() -> None:
    with pytest.raises(TypeError):
        QRCode("")
    with pytest.raises(ValueError):
        QRCode("x", ecc="X")
    with pytest.raises(ValueError):
        QRCode("x", size=16)
    with pytest.raises(ValueError):
        QRCode("x", dark="black")


def test_qr_too_long_for_level_is_a_clear_error() -> None:
    pytest.importorskip("segno")
    with pytest.raises(ValueError, match="does not fit"):
        QRCode("a" * 3000, ecc="H")


# -- TagInput ----------------------------------------------------------------


def test_tags_are_normalised_deduplicated_and_capped() -> None:
    seen: list[list[str]] = []
    tags = TagInput([" a ", "a", "", "b  c"], max_tags=3, on_change=seen.append)
    assert tags.tags == ("a", "b c")
    assert seen == []  # constructor tags do not notify
    assert tags.add_tag("d") is True
    assert tags.add_tag("d") is False
    assert tags.add_tag("   ") is False
    assert tags.add_tag("e") is False  # over the cap of 3
    assert seen == [["a", "b c", "d"]]


def test_remove_and_clear_notify_once_each() -> None:
    seen: list[list[str]] = []
    tags = TagInput(["x", "y"], on_change=seen.append)
    assert tags.remove_tag("x") is True
    assert tags.remove_tag("missing") is False
    tags.clear_tags()
    assert tags.tags == ()
    assert seen == [["y"], []]


def test_tag_chip_press_removes_that_tag() -> None:
    tags = TagInput(["one", "two"])
    chip = tags._chips[0][1]
    chip.on_press()
    assert tags.tags == ("two",)


# -- Countdown ---------------------------------------------------------------


class _Job:
    def __init__(self, fn):
        self.fn = fn
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _App:
    def __init__(self):
        self.jobs: list[_Job] = []
        self.queue: list = []

    def repeat_job(self, interval_ms, fn, name=None):
        job = _Job(fn)
        self.jobs.append(job)
        return job

    def dispatch(self, callback, *args, **kwargs):
        self.queue.append(callback)
        return True


class _Screen:
    def __init__(self, app):
        self._app = app

    @property
    def app(self):
        return self._app

    def invalidate(self):
        pass


def _attached(widget) -> _App:
    app = _App()
    Column(widget)._screen = _Screen(app)  # the root holds the screen link
    return app


def _drain(app: _App) -> None:
    while app.queue:
        app.queue.pop(0)()


def test_countdown_formats_mm_ss_and_h_mm_ss() -> None:
    assert Countdown(90).text == "01:30"
    assert Countdown(59.2).text == "01:00"
    assert Countdown(3725).text == "1:02:05"


def test_countdown_start_requires_a_screen() -> None:
    with pytest.raises(PyMobileError):
        Countdown(5).start()


def test_countdown_runs_pauses_resumes_and_finishes_once() -> None:
    finished: list[int] = []
    cd = Countdown(2, on_finish=lambda: finished.append(1))
    app = _attached(cd)

    cd.start()
    assert cd.running
    time.sleep(1.05)
    app.jobs[-1].fn()
    _drain(app)
    assert cd.text == "00:01"

    cd.stop()
    assert not cd.running and app.jobs[-1].cancelled
    paused_left = cd.remaining
    time.sleep(0.2)
    assert abs(cd.remaining - paused_left) < 1e-6  # paused: time does not run

    cd.start()
    time.sleep(paused_left + 0.1)
    app.jobs[-1].fn()
    _drain(app)
    app.jobs[-1].fn()  # a second tick after finishing must do nothing
    _drain(app)
    assert cd.text == "00:00"
    assert finished == [1]
    assert not cd.running

    cd.reset()
    assert cd.text == "00:02"


# -- MarkdownText ------------------------------------------------------------


def test_markdown_builds_headings_lists_and_code() -> None:
    md = MarkdownText(
        "# Title\n\nPara with **bold** and [link](http://x).\n\n- one\n- two\n\n"
        "1. first\n\n```\ncode here\n```\n"
    )
    texts = [getattr(child, "text", None) for child in md.children]
    assert "Title" in texts
    assert any(t and "bold" in t and "**" not in t for t in texts)
    assert any(t and "link" in t and "http" not in t for t in texts)
    assert any(t == "code here" for t in texts)


def test_markdown_unknown_input_is_plain_text_not_error() -> None:
    md = MarkdownText("just words <b>not html</b>")
    assert md.children


def test_markdown_has_no_unknown_types_for_android() -> None:
    md = MarkdownText("# A\n- b\n```\nc\n```")
    assert unknown_types(md.to_dict(), renderer="android") == frozenset()


# -- SignaturePad ------------------------------------------------------------


def test_signature_receives_strokes_from_native() -> None:
    seen: list = []
    pad = SignaturePad(on_change=seen.append)
    pad._ui_set_value(json.dumps([[[0.1, 0.2], [0.3, 0.4]], [[0.9, 0.9]]]))
    assert pad.strokes == [[(0.1, 0.2), (0.3, 0.4)], [(0.9, 0.9)]]
    assert len(seen) == 1 and len(seen[0]) == 2


def test_signature_clamps_and_validates_points() -> None:
    pad = SignaturePad()
    pad._ui_set_value(json.dumps([[[-0.5, 1.5]]]))
    assert pad.strokes == [[(0.0, 1.0)]]
    for bad in ["nope", '[["x"]]', "[[[1,2,3]]]", "[[[true,0]]]", '{"a":1}']:
        with pytest.raises(ValueError):
            pad._ui_set_value(bad)


def test_signature_clear_bumps_revision_for_the_native_side() -> None:
    pad = SignaturePad()
    pad._ui_set_value(json.dumps([[[0.5, 0.5]]]))
    before = pad.props()["revision"]
    pad.clear()
    assert pad.is_empty
    assert pad.props()["revision"] == before + 1


def test_signature_rejects_bad_colour_and_size() -> None:
    with pytest.raises(ValueError):
        SignaturePad(stroke_color="red")
    with pytest.raises(TypeError):
        SignaturePad(height=0)
    with pytest.raises(ValueError):
        SignaturePad(stroke_width=0)


def test_signature_has_no_unknown_types_for_android() -> None:
    assert unknown_types(SignaturePad().to_dict(), renderer="android") == frozenset()
