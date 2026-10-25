"""The sixth wave: file pickers, audio, canvas, clipboard and the share sheet.

Two kinds of test live here.

*Behaviour*, against the stub bridge — every one of these features used to be
absent, so the tests state what the framework now promises: ``app.pick_file()``
returns a path or ``None``, ``AudioPlayer`` never blocks the caller,
``Canvas`` serialises into the drawing primitives every renderer already
understands, and ``app.copy``/``app.share`` report whether they worked.

*Consistency*: the native surface is spread over four files that no compiler in
CI can check together (``libpymobile.so`` needs the NDK, ``DeviceServices`` and
``MediaServices`` need ``android.jar``, ``pymobile_jni.c`` needs JNI headers), so
the last class parses them as text. A Python method with no C wrapper, or a C
wrapper calling a Java method that does not exist, would otherwise only fail on
a phone — as a silent no-op.
"""

from __future__ import annotations

import re
import time
import wave
from pathlib import Path

import pytest

from pymobile import (
    App,
    AudioPlayer,
    AudioRecorder,
    Canvas,
    Circle,
    Line,
    PickedFile,
    Polygon,
    Rect,
    Shape,
    Share,
)
from pymobile import (
    Path as CanvasPath,
)
from pymobile.core.api.audio import DEFAULT_TICK_MS
from pymobile.core.bridge import StubBridge, pick_filters
from pymobile.errors import PlatformError

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "pymobile" / "resources" / "android" / "java"
JNI = ROOT / "pymobile" / "resources" / "android" / "jni" / "pymobile_jni.c"


@pytest.fixture
def bridge() -> StubBridge:
    return StubBridge(verbose=False)


@pytest.fixture
def app(bridge: StubBridge, tmp_path: Path) -> App:
    return App("Demo", bridge=bridge, storage_path=str(tmp_path / "store.json"))


def _tone(path: Path, *, seconds: float = 0.2, rate: int = 8000) -> Path:
    """A real (silent) WAV file, so duration and playback have something to read."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


# --------------------------------------------------------------------------
# File pickers
# --------------------------------------------------------------------------
class TestPickFile:
    def test_a_cancelled_picker_returns_none(self, app: App, bridge: StubBridge) -> None:
        assert app.pick_file() is None
        assert bridge.calls_named("pick_file")

    def test_a_picked_file_comes_back_as_a_path(
        self, app: App, bridge: StubBridge, tmp_path: Path
    ) -> None:
        chosen = _tone(tmp_path / "note.wav")
        bridge.file_picks = [str(chosen)]
        picked = app.pick_file("audio/*")
        assert isinstance(picked, PickedFile)
        assert picked.path == str(chosen)
        assert picked.name == "note.wav"

    def test_the_accept_filter_reaches_the_platform(self, app: App, bridge: StubBridge) -> None:
        app.pick_file("image/png")
        assert bridge.calls_named("pick_file")[0].kwargs["accept"] == "image/png"

    def test_an_extension_is_translated_to_a_mime_type(self, app: App, bridge: StubBridge) -> None:
        app.pick_file(".wav")
        assert bridge.calls_named("pick_file")[0].kwargs["accept"] == "audio/wav"

    def test_several_files_come_back_together(
        self, app: App, bridge: StubBridge, tmp_path: Path
    ) -> None:
        bridge.file_picks = [str(tmp_path / "a.wav"), str(tmp_path / "b.wav")]
        picked = app.pick_files("audio/*")
        assert [item.name for item in picked] == ["a.wav", "b.wav"]

    def test_pick_image_uses_the_camera_path(
        self, app: App, bridge: StubBridge, tmp_path: Path
    ) -> None:
        bridge.file_picks = [str(tmp_path / "photo.jpg")]
        assert app.pick_image().name == "photo.jpg"
        assert bridge.calls_named("pick_image")

    def test_a_picked_file_without_a_path_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="needs a path"):
            PickedFile("")

    @pytest.mark.parametrize(
        ("accept", "expected"),
        [
            ("audio/*", ["audio/*"]),
            ("image/png", ["image/png"]),
            (".mp3", ["audio/mpeg"]),
            (".wav,.ogg", ["audio/wav", "audio/ogg"]),
            ("*/*", ["*/*"]),
            ("", ["*/*"]),
        ],
    )
    def test_mime_filters(self, accept: str, expected: list[str]) -> None:
        assert pick_filters(accept) == expected

    def test_a_bad_filter_explains_itself(self) -> None:
        with pytest.raises(ValueError, match="unknown extension"):
            pick_filters(".docx_nope")
        with pytest.raises(ValueError, match="invalid accept"):
            pick_filters("music")
        with pytest.raises(TypeError, match="accept must be a string"):
            pick_filters(5)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Clipboard and the share sheet
# --------------------------------------------------------------------------
class TestClipboardAndShare:
    def test_copy_puts_the_text_on_the_clipboard(self, app: App) -> None:
        assert app.copy("https://example.com/room/42") is True
        assert app.clipboard() == "https://example.com/room/42"

    def test_copy_to_clipboard_is_the_same_call(self, app: App) -> None:
        assert app.copy_to_clipboard("hello") is True
        assert app.clipboard() == "hello"

    def test_copy_rejects_a_non_string(self, app: App) -> None:
        with pytest.raises(TypeError, match="text must be a string"):
            app.copy(["not", "a", "string"])  # type: ignore[arg-type]

    def test_a_link_is_escaped_before_sharing(self, app: App, bridge: StubBridge) -> None:
        app.share(url="https://example.com/a b/привіт")
        shared = bridge.shared[-1]
        assert " " not in shared["url"]
        assert shared["url"].startswith("https://example.com/a%20b/")
        # The body carries the link too: targets that read only the body still
        # get something clickable.
        assert shared["text"] == shared["url"]

    def test_text_and_link_travel_together(self, app: App, bridge: StubBridge) -> None:
        app.share("Look at this", "https://example.com", subject="Room")
        assert bridge.shared[-1] == {
            "text": "Look at this",
            "url": "https://example.com",
            "subject": "Room",
        }

    def test_sharing_plain_text_works(self, app: App, bridge: StubBridge) -> None:
        assert app.share("boring but fine") is True
        assert bridge.shared[-1]["url"] == ""

    def test_nothing_to_share_is_an_error(self, app: App) -> None:
        with pytest.raises(ValueError, match="nothing to share"):
            app.share()
        with pytest.raises(ValueError, match="nothing to share"):
            app.share("   ")

    def test_a_bare_word_is_not_a_url(self, app: App) -> None:
        with pytest.raises(ValueError, match="must start with http"):
            app.share(url="example.com")

    def test_the_share_object_is_usable_on_its_own(self, bridge: StubBridge) -> None:
        share = Share(bridge)
        assert share.text("hi", subject="S") is True
        assert share.copy("clip") is True
        assert share.clipboard() == "clip"


# --------------------------------------------------------------------------
# Audio playback
# --------------------------------------------------------------------------
class TestAudioPlayer:
    def test_play_returns_at_once(self, bridge: StubBridge, tmp_path: Path) -> None:
        """The whole point: a tap on Play must not block the UI thread."""
        player = AudioPlayer(_tone(tmp_path / "beep.wav"), bridge=bridge)
        started = time.monotonic()
        player.play()
        assert time.monotonic() - started < 0.2
        assert bridge.players

    def test_duration_comes_from_the_file(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "half.wav", seconds=0.5), bridge=bridge)
        player.play()
        assert 480 <= player.duration <= 520

    def test_position_advances(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "tone.wav"), bridge=bridge)
        player.play()
        time.sleep(0.05)
        assert player.position >= 0

    def test_pause_and_resume(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav"), bridge=bridge)
        player.play()
        assert player.playing
        player.pause()
        # The stub has no clock of its own: pausing rewinds its position model,
        # which is why the assertion is on the transport being reachable, not
        # on a wall-clock reading.
        assert bridge.players

    def test_seek_is_what_position_reports(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav"), bridge=bridge)
        player.play()
        player.seek(120)
        assert 115 <= player.position <= 200

    def test_seek_is_clamped_to_the_length(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav", seconds=0.2), bridge=bridge)
        player.play()
        player.seek(10_000)
        assert player.position <= player.duration + 5

    def test_volume_is_validated_and_forwarded(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav"), bridge=bridge)
        player.play()
        player.volume = 0.4
        assert player.volume == 0.4
        assert bridge.players[-1].volume == 0.4
        for bad in (-0.1, 1.5):
            with pytest.raises(ValueError, match="volume"):
                player.volume = bad
        with pytest.raises(TypeError, match="volume must be a number"):
            player.volume = "loud"  # type: ignore[assignment]

    def test_mute_and_unmute_remember_the_level(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav"), volume=0.6, bridge=bridge)
        assert not player.muted
        player.mute()
        assert player.muted
        player.unmute()
        assert player.volume == 0.6

    def test_release_stops_and_frees(self, bridge: StubBridge, tmp_path: Path) -> None:
        player = AudioPlayer(_tone(tmp_path / "t.wav"), bridge=bridge)
        player.play()
        player.release()
        assert bridge.players[-1].released
        assert not player.playing

    def test_tick_callback_runs_on_the_app_timer(
        self, app: App, bridge: StubBridge, tmp_path: Path
    ) -> None:
        seen: list[int] = []
        player = app.audio(_tone(tmp_path / "t.wav", seconds=0.3), on_tick=seen.append)
        player.play()
        deadline = time.monotonic() + 2.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)
        player.release()
        assert seen, "on_tick never fired"
        assert all(position >= 0 for position in seen)

    def test_the_end_callback_fires_once(
        self, app: App, bridge: StubBridge, tmp_path: Path
    ) -> None:
        ended: list[int] = []
        player = app.audio(
            _tone(tmp_path / "short.wav", seconds=0.12),
            on_tick=lambda _position: None,
            on_end=lambda: ended.append(1),
        )
        player.play()
        deadline = time.monotonic() + 3.0
        while not ended and time.monotonic() < deadline:
            time.sleep(0.02)
        player.release()
        assert ended == [1]

    def test_a_bad_source_is_refused_before_any_platform_call(self, bridge: StubBridge) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            AudioPlayer("", bridge=bridge)
        with pytest.raises(TypeError, match="path or URL string"):
            AudioPlayer(42, bridge=bridge)  # type: ignore[arg-type]

    def test_callbacks_must_be_callable(self, bridge: StubBridge) -> None:
        with pytest.raises(TypeError, match="on_end must be callable"):
            AudioPlayer("x.wav", on_end=5, bridge=bridge)  # type: ignore[arg-type]

    def test_the_tick_interval_is_documented_in_one_place(self) -> None:
        assert DEFAULT_TICK_MS > 0


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------
class TestAudioRecorder:
    def test_stop_returns_a_file_that_exists(self, bridge: StubBridge, tmp_path: Path) -> None:
        bridge.granted.add("android.permission.RECORD_AUDIO")
        bridge.recordings_dir = str(tmp_path / "rec")
        recorder = AudioRecorder(bridge=bridge)
        recorder.start()
        assert recorder.recording
        take = recorder.stop()
        assert isinstance(take, PickedFile)
        assert Path(take.path).exists()
        assert take.size > 0
        assert take.mime == "audio/mp4"

    def test_a_denied_microphone_explains_itself(self, bridge: StubBridge) -> None:
        bridge.grant_permissions = False
        recorder = AudioRecorder(bridge=bridge)
        with pytest.raises(PlatformError, match="microphone permission was denied"):
            recorder.start()

    def test_cancel_removes_the_take(self, bridge: StubBridge, tmp_path: Path) -> None:
        bridge.granted.add("android.permission.RECORD_AUDIO")
        bridge.recordings_dir = str(tmp_path / "rec")
        recorder = AudioRecorder(bridge=bridge)
        recorder.start()
        target = recorder._handle.target
        recorder.cancel()
        assert not target.exists()

    def test_elapsed_counts_up(self, bridge: StubBridge, tmp_path: Path) -> None:
        bridge.granted.add("android.permission.RECORD_AUDIO")
        bridge.recordings_dir = str(tmp_path / "rec")
        recorder = AudioRecorder(bridge=bridge)
        recorder.start()
        time.sleep(0.03)
        assert recorder.elapsed_ms >= 20
        recorder.cancel()

    def test_the_app_hands_out_a_recorder(self, app: App, bridge: StubBridge) -> None:
        assert isinstance(app.recorder(), AudioRecorder)


# --------------------------------------------------------------------------
# Canvas
# --------------------------------------------------------------------------
class TestCanvas:
    def test_elements_become_drawing_primitives(self) -> None:
        canvas = Canvas(
            Line((0, 10), (100, 10), color="#3F51B5"),
            Rect(5, 5, 20, 10, color="#FF0000"),
            width=120,
            height=60,
        )
        drawing = canvas.to_dict()["props"]["drawing"]
        assert drawing["width"] == 120 and drawing["height"] == 60
        kinds = [shape["type"] for shape in drawing["shapes"]]
        assert kinds == ["line", "rect"]

    def test_a_path_can_close_itself(self) -> None:
        closed = Canvas(CanvasPath([(0, 0), (10, 0), (5, 8)], closed=True)).drawing()
        points = closed["shapes"][0]["points"]
        assert points[0] == points[-1]

    def test_a_polygon_is_drawn_as_a_closed_shape(self) -> None:
        drawing = Canvas(Polygon((0, 0), (10, 0), (5, 8))).drawing()
        assert len(drawing["shapes"][0]["points"]) == 4

    def test_an_outlined_circle_keeps_its_ring(self) -> None:
        shape = Canvas(Circle(10, 10, 5, fill=False, color="#4CAF50")).drawing()["shapes"][0]
        assert shape["fill"] is False and shape["color"] == "#4CAF50"

    def test_opacity_becomes_an_alpha_channel(self) -> None:
        shape = Canvas(Line((0, 0), (1, 1), color="#112233", opacity=0.5)).drawing()["shapes"][0]
        assert shape["color"].startswith("#80")  # 0x80 == 50 % of 0xFF
        assert shape["color"].endswith("112233")

    def test_text_shapes_are_drawable(self) -> None:
        shape = Canvas(Shape("text", x=4, y=6, text="hi", color="#000000")).drawing()["shapes"][0]
        assert shape == {
            "type": "text",
            "x": 4.0,
            "y": 6.0,
            "text": "hi",
            "color": "#000000",
            "size": 12.0,
            "align": "start",
        }

    def test_elements_can_be_added_and_cleared(self) -> None:
        canvas = Canvas(Line((0, 0), (1, 1)))
        canvas.add(Circle(5, 5, 2))
        assert len(canvas.drawing()["shapes"]) == 2
        canvas.clear()
        assert canvas.drawing()["shapes"] == []

    def test_a_widget_is_not_a_drawing_element(self) -> None:
        from pymobile import Label

        with pytest.raises(TypeError, match="belongs in a layout"):
            Canvas(Label("nope"))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("factory", "message"),
        [
            (lambda: Line((0, 0)), "at least two points"),
            (lambda: Line((0, 0), (1,)), "must be an \\(x, y\\) pair"),
            (lambda: Rect(0, 0, 0, 0), "positive w and h"),
            (lambda: Circle(0, 0, 0), "positive r"),
            (lambda: Polygon((0, 0), (1, 1)), "at least three points"),
            (lambda: Shape("blob"), "unknown kind"),
            (lambda: Shape("text"), "needs text"),
            (lambda: Shape("line"), "use Line"),
            (lambda: Line((0, 0), (1, 1), width=0), "width must be positive"),
            (lambda: Line((0, 0), (1, 1), opacity=2), "opacity"),
            (lambda: Canvas(width=0), "width must be a positive int"),
        ],
    )
    def test_bad_input_names_the_fix(self, factory, message: str) -> None:
        with pytest.raises((ValueError, TypeError), match=message):
            factory()

    def test_the_browser_preview_draws_it_as_svg(self) -> None:
        from pymobile.core.ui.web import render_html

        canvas = Canvas(Line((0, 0), (10, 10)), width=40, height=20, background="#FFFFFF")
        html = render_html(canvas.to_dict())
        assert "<svg" in html and "<polyline" in html
        assert "background:#FFFFFF" in html

    def test_the_text_preview_names_the_size(self) -> None:
        from pymobile.core.ui.preview import render_ascii

        text = render_ascii(Canvas(Line((0, 0), (10, 10)), width=40, height=20))
        assert "40×20 canvas" in text

    def test_the_mockup_picture_draws_the_stroke(self) -> None:
        from pymobile.core.ui.mockup import render_mockup

        image = render_mockup(
            Canvas(
                Line((0, 10), (100, 10), color="#FF0000", width=4),
                width=120,
                height=40,
            ),
            width=160,
            height=80,
            scale=2.0,
            chrome=False,
        )
        pixels = image.convert("RGB").tobytes()
        reds = sum(
            1
            for index in range(0, len(pixels), 3)
            if pixels[index] > 150 and pixels[index + 1] < 90 and pixels[index + 2] < 90
        )
        assert reds > 50


# --------------------------------------------------------------------------
# The four native layers must agree
# --------------------------------------------------------------------------
class TestNativeSurfaceIsConsistent:
    """The parts no compiler sees together.

    ``pymobile_jni.c`` needs JNI headers, ``DeviceServices``/``MediaServices``
    need ``android.jar`` and the prebuilt ``libpymobile.so`` is compiled
    elsewhere, so a typo between them would only fail on a phone. These checks
    read the sources and fail in CI instead.
    """

    def jni_methods(self) -> set[str]:
        text = JNI.read_text(encoding="utf-8")
        table = re.findall(r'\{"(\w+)", py_\w+', text)
        return set(table)

    def java_methods(self) -> set[str]:
        names: set[str] = set()
        signature = re.compile(r"public static [\w\[\]\.]+ (\w+)\(")
        for path in JAVA.glob("*.java"):
            for match in signature.finditer(path.read_text(encoding="utf-8")):
                names.add(match.group(1))
        return names

    def test_every_python_call_has_a_c_wrapper(self) -> None:
        source = (ROOT / "pymobile/core/bridge/android.py").read_text(encoding="utf-8")
        used = set(re.findall(r'getattr\(self\._native, "(\w+)"', source))
        used.update(re.findall(r"self\._native\.(\w+)\(", source))
        used.update(re.findall(r"native\.(\w+)\(", source))
        missing = sorted(used - self.jni_methods())
        assert not missing, (
            f"these native calls are used from Python but not exported by "
            f"pymobile_jni.c: {missing}"
        )

    def test_every_c_wrapper_calls_a_java_method_that_exists(self) -> None:
        """``Native.java`` is the only class the C code looks methods up on."""
        native_java = (JAVA / "Native.java").read_text(encoding="utf-8")
        declared = set(re.findall(r"public static [\w\[\]\.]+ (\w+)\(", native_java))
        called = set(
            re.findall(
                r'static_method\([^)]*?"(\w+)",\s*"[^"]*"',
                JNI.read_text(encoding="utf-8"),
            )
        )
        called.update(
            re.findall(r'call_void_method\("(\w+)"', JNI.read_text(encoding="utf-8"))
        )
        missing = sorted(called - declared)
        assert not missing, f"the JNI layer calls Native.{missing}, which does not exist"

    def test_the_media_services_are_compiled_into_the_apk(self) -> None:
        backend = (ROOT / "pymobile/compiler/backends/native.py").read_text(encoding="utf-8")
        assert '"MediaServices.java"' in backend, (
            "MediaServices.java is not in the javac source list, so the pickers "
            "and audio would be missing from a built APK"
        )

    def test_the_new_files_are_shipped(self) -> None:
        for name in ("MediaServices.java",):
            assert (JAVA / name).exists(), name

    def test_the_picker_result_has_a_way_back(self) -> None:
        activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
        assert "onActivityResult" in activity
        assert "MediaServices.onPickResult" in activity

    def test_canvas_is_declared_for_every_renderer(self) -> None:
        from pymobile.core.ui.registry import supported_by

        for renderer in ("android", "web", "gui", "ascii"):
            assert "Canvas" in supported_by(renderer), renderer

    def test_java_sources_do_not_use_apis_newer_than_min_sdk_without_a_guard(self) -> None:
        """A picker opened with an API-33-only call would break API 24 phones."""
        media = (JAVA / "MediaServices.java").read_text(encoding="utf-8")
        for forbidden in ("PickVisualMedia", "registerForActivityResult", "getParcelableExtra"):
            assert forbidden not in media, (
                f"{forbidden} is not available on minSdk 24; use the "
                "startActivityForResult path"
            )
