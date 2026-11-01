"""Guard the shipped JNI bridge against the C source it claims to mirror.

The prebuilt ``libpymobile.so`` is what a wheel-only install actually ships,
and it can silently fall behind ``pymobile_jni.c``: two releases carried 15
of the 36 module methods, so audio, clipboard, file pickers, sharing and
recording failed on every phone built without ``PYMOBILE_BUILD_JNI=1``
(PM-10), and the native ``wake`` queued a truncated ``"__wake_"`` event
length that the Python event loop never matched (PM-11).

These tests read the real binaries and the real C source — the same pair the
release audit compares — and fail the moment they diverge.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JNI_SOURCE = ROOT / "pymobile" / "resources" / "android" / "jni" / "pymobile_jni.c"
PREBUILT = ROOT / "pymobile" / "resources" / "android" / "prebuilt"


def _declared_methods() -> list[str]:
    """Method names from the ``module_methods`` table of the C source."""
    source = JNI_SOURCE.read_text(encoding="utf-8")
    table = source.split("static PyMethodDef module_methods[] = {", 1)[1].split("};", 1)[0]
    return re.findall(r'\{\s*"([a-z_]+)"\s*,', table)


class TestPrebuiltNativeApi:
    def test_the_c_source_declares_the_expected_surface(self) -> None:
        """Adding methods here means rebuilding both prebuilts as well."""
        methods = _declared_methods()
        assert len(methods) == len(set(methods)), "duplicate entries in module_methods"
        # 36 as of the audio/clipboard/picker/share/recorder landing; bump the
        # number *and* rebuild pymobile/resources/android/prebuilt/* together.
        assert len(methods) == 36, methods
        for name in (
            "audio_open",
            "audio_play",
            "audio_duration",
            "clipboard_text",
            "copy_to_clipboard",
            "pick_file",
            "pick_image",
            "share",
            "recorder_start",
            "recorder_stop",
            "wake",
            "render",
        ):
            assert name in methods

    @pytest.mark.parametrize("abi", ["arm64-v8a", "x86_64"])
    def test_prebuilt_exposes_native_api_declared_by_its_source(self, abi: str) -> None:
        """Every method the C source declares must exist in the shipped .so.

        The names are string literals in the PyMethodDef table, so their
        absence from the binary is proof the binary predates the source.
        """
        library = (PREBUILT / abi / "libpymobile.so").read_bytes()
        missing = [name for name in _declared_methods() if f"{name}\0".encode() not in library]
        assert not missing, (
            f"the prebuilt {abi} libpymobile.so is missing {len(missing)} methods that "
            f"pymobile_jni.c declares: {missing} — rebuild both prebuilts from the "
            "current source (NDK r27c; PYMOBILE_BUILD_JNI=1 pymobile build)"
        )

    @pytest.mark.parametrize("abi", ["arm64-v8a", "x86_64"])
    def test_the_prebuilt_still_carries_the_wake_event(self, abi: str) -> None:
        library = (PREBUILT / abi / "libpymobile.so").read_bytes()
        assert b"__wake__\0" in library
        assert b"Wake the thread blocked in next_event()" in library


class TestWakeEventContract:
    def test_the_wake_length_is_derived_from_the_literal(self) -> None:
        """PM-11: a hardcoded 7 queued "__wake_" (7 bytes), which the Python
        event loop never matches by kind == "__wake__", so every dispatch(),
        timer and job completion waiting behind the wake stayed stuck."""
        from pymobile.core.app import WAKE_EVENT

        source = JNI_SOURCE.read_text(encoding="utf-8")
        assert len(WAKE_EVENT) == 8
        assert f'sizeof("{WAKE_EVENT}") - 1' in source
        # The exact regression: "__wake__" spelled out with a wrong length.
        assert f'"{WAKE_EVENT}", 7' not in source
