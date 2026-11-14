"""What the phone does with the system, not just with the widget tree.

Six audit items share one root cause: the Android side either ignored something
the system told it (a light/dark switch, the keyboard height, the text
direction) or did it on the wrong thread (image decoding). None of them can be
observed in a desktop test, so these tests check the contract between the
pieces that can be read: the manifest the build writes, the Java the APK ships,
and the events Python answers.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pymobile import App, Image
from pymobile.compiler.manifest import build_manifest
from pymobile.core.bridge.stub import StubBridge
from pymobile.core.config import ProjectConfig

ANDROID = "{http://schemas.android.com/apk/res/android}"
JAVA = Path(__file__).resolve().parents[1] / "resources/android/java"


def _activity() -> str:
    return (JAVA / "MainActivity.java").read_text(encoding="utf-8")


def _styles() -> str:
    return (JAVA / "ViewStyles.java").read_text(encoding="utf-8")


def _controls() -> str:
    return (JAVA / "ViewControls.java").read_text(encoding="utf-8")


class _NativeStub(StubBridge):
    """A stub that claims the native renderer, so device-only checks run."""

    native_widgets = True


def _manifest(config: ProjectConfig | None = None) -> ET.Element:
    return ET.fromstring(build_manifest(config or ProjectConfig(name="Themed")))


@pytest.fixture
def app() -> App:
    application = App("Themed", bridge=StubBridge())
    yield application
    application.stop()


# -- A2: system bars and the keyboard ------------------------------------------
def test_the_window_pads_itself_for_bars_and_keyboard() -> None:
    activity = _activity()
    assert "onApplyWindowInsets" in activity
    # The IME has its own inset type; a padding that only knew about the
    # system bars let the keyboard cover the field being typed into.
    assert "WindowInsets.Type.ime()" in activity
    assert "SOFT_INPUT_ADJUST_RESIZE" in activity
    assert "setFitsSystemWindows(true)" in activity


def test_the_old_inset_calls_are_guarded_by_version() -> None:
    """getSystemWindowInset* is deprecated on API 30+, minSdk is 24."""
    styles = _styles()
    for name in ("insetLeft", "insetTop", "insetRight", "insetBottom"):
        assert name in styles
    assert "Build.VERSION.SDK_INT >= 30" in styles


def test_a_keyboard_height_reaches_the_application(app: App) -> None:
    seen: list[str] = []
    app.on("app:insets", lambda event: seen.append(event.source))
    app.handle_ui_event("", "insets", "612")
    assert seen == ["612"]


# -- A4: right-to-left ---------------------------------------------------------
def test_the_manifest_opts_into_mirrored_layouts() -> None:
    application = _manifest().find("application")
    assert application is not None
    assert application.get(f"{ANDROID}supportsRtl") == "true"


def test_margins_are_expressed_as_start_and_end() -> None:
    styles = _styles()
    body = styles[styles.index("void applyMargin(") :]
    body = body[: body.index("\n    }")]
    assert "setMarginStart" in body and "setMarginEnd" in body
    # leftMargin/rightMargin would pin the spacing to one side in Arabic.
    assert "leftMargin" not in body and "rightMargin" not in body


# -- A5: the system light/dark switch ------------------------------------------
def test_the_startup_screen_follows_the_system_theme() -> None:
    activity = _activity()
    assert "UI_MODE_NIGHT_YES" in activity
    # A hard-coded white background flashed white in dark mode.
    assert "setBackgroundColor(Color.WHITE)" not in activity
    assert "startupBackground()" in activity


def test_a_theme_change_reaches_the_application(app: App) -> None:
    seen: list[str] = []
    app.on("app:system-theme", lambda event: seen.append(event.source))
    app.handle_ui_event("", "theme", "dark")
    app.handle_ui_event("", "theme", "light")
    assert seen == ["dark", "light"]
    assert app.system_is_dark is False


def test_ui_mode_is_reported_although_the_activity_survives() -> None:
    activity = _activity()
    # uiMode is in configChanges, so without this dispatch Python never heard
    # about the switch at all.
    assert 'Native.dispatchEvent("", "theme"' in activity
    assert "CONFIG_UI_MODE" in activity


def test_an_app_can_follow_the_system_palette() -> None:
    following = App("Themed", bridge=StubBridge(), follow_system_theme=True)
    try:
        following.handle_ui_event("", "theme", "dark")
        assert following.theme.name == "dark"
        following.handle_ui_event("", "theme", "light")
        assert following.theme.name == "light"
    finally:
        following.stop()


def test_an_app_that_did_not_ask_keeps_its_palette() -> None:
    app = App("Themed", bridge=StubBridge())
    try:
        before = app.theme.name
        app.handle_ui_event("", "theme", "dark")
        assert app.theme.name == before
    finally:
        app.stop()


# -- A9: the back gesture -----------------------------------------------
def test_the_back_callback_is_released_on_the_root_screen() -> None:
    activity = _activity()
    assert "setCanGoBack" in activity
    assert "unregisterOnBackInvokedCallback" in activity
    # On the root screen the platform's own "leave the app" is correct.
    assert "if (!canGoBack) {" in activity
    assert "super.onBackPressed()" in activity


def test_the_window_learns_the_depth_from_the_frame(app: App) -> None:
    from pymobile import Column, Label, Screen

    class Home(Screen):
        def build(self) -> Column:
            return Column(Label("home"))

    bridge = app.bridge
    app.run(Home())
    assert isinstance(bridge, StubBridge)
    assert bridge.last_tree["chrome"]["can_go_back"] is False


# -- A3: image decoding --------------------------------------------------------
def test_images_are_decoded_off_the_ui_thread_and_scaled() -> None:
    controls = _controls()
    body = controls[controls.index("void loadLocalImage(final ImageView") :]
    assert "new Thread(" in body
    assert "decodeScaled(" in body
    # A 48 MP camera photo decoded whole is ~190 MB of ARGB: an OOM, not a stutter.
    assert "inSampleSize" in controls
    assert "inJustDecodeBounds" in controls
    assert "OutOfMemoryError" in controls


def test_the_subsample_step_keeps_the_image_at_least_view_sized() -> None:
    """sampleSize doubles while the image still covers the view."""
    controls = _controls()
    body = controls[controls.index("static int sampleSize(") :]
    body = body[: body.index("\n    }")]
    assert "sample *= 2" in body
    assert "width / (sample * 2) >= targetWidth" in body


def test_exif_rotation_is_applied() -> None:
    controls = _controls()
    assert "ExifInterface" in controls
    assert "postRotate(degrees)" in controls


def test_a_late_decode_does_not_replace_a_newer_source() -> None:
    controls = _controls()
    assert "source.equals(owner.imageSources.get(image))" in controls


# -- A18: deprecated calls -----------------------------------------------------
def test_no_unused_deprecated_audio_or_text_calls_remain() -> None:
    media = (JAVA / "MediaServices.java").read_text(encoding="utf-8")
    # A comment naming the call is fine; a live call is not.
    assert ".setAudioStreamType(" not in media
    assert "setAudioAttributes" in media
    advanced = (JAVA / "AdvancedViews.java").read_text(encoding="utf-8")
    # Mentions in a comment are fine; a live call is not.
    assert ".scaledDensity" not in advanced
    assert "fontScale" in advanced


# -- A19: images the device cannot draw ---------------------------------------
def test_a_remote_image_is_reported_instead_of_drawn_blank(app: App) -> None:
    from pymobile import Column, Screen

    class Photos(Screen):
        def build(self) -> Column:
            return Column(Image("https://example.com/a.png", accessibility_label="photo"))

    messages: list[str] = []

    import logging

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("pymobile.app")
    handler = Capture()
    logger.addHandler(handler)
    try:
        native = App("Themed", bridge=_NativeStub())
        try:
            native.run(Photos())
        finally:
            native.stop()
    finally:
        logger.removeHandler(handler)
    assert any("cannot be drawn on Android" in message for message in messages)


def test_a_local_image_is_not_reported(app: App, tmp_path: Path) -> None:
    from pymobile import Column, Screen

    picture = tmp_path / "local.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")

    class Photos(Screen):
        def build(self) -> Column:
            return Column(Image(str(picture), accessibility_label="photo"))

    messages: list[str] = []
    import logging

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("pymobile.app")
    handler = Capture()
    logger.addHandler(handler)
    try:
        app.run(Photos())
    finally:
        logger.removeHandler(handler)
    assert not any("cannot be drawn on Android" in message for message in messages)


# -- K11: migration keys ------------------------------------------------------
def test_both_stores_describe_migration_keys_the_same_way(tmp_path: Path) -> None:
    from pymobile.core.api.storage import Storage
    from pymobile.core.api.typed_storage import ModelStore

    with pytest.raises(ValueError) as storage_error:
        Storage(tmp_path / "a.json", schema_version=2, migrations={3: lambda data: data})
    with pytest.raises(ValueError) as model_error:
        ModelStore(
            Storage(tmp_path / "b.json"),
            dict,
            key="rows",
            to_record=lambda item: dict(item),
            from_record=lambda record: dict(record),
            schema_version=2,
            migrations={3: lambda rows: rows},
        )
    # Both messages point at the other store, so one convention is visible
    # whichever of the two a developer hits first (K11: they used to disagree).
    assert "ModelStore" in str(storage_error.value)
    assert "Storage" in str(model_error.value)
