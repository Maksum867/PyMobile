"""Round-trip tests for the JNI bridge's UTF-8 ↔ UTF-16 conversion.

The bridge used ``GetStringUTFChars``/``NewStringUTF``, which speak *Modified*
UTF-8: U+1F600 travelled as the CESU-8 surrogate pair ``ed a0 bd ed b8 80``,
and CPython's strict UTF-8 decoder rejected it, so an emoji in a TextInput
killed the event loop. The fix hand-converts both directions; this test
extracts that code from the real ``pymobile_jni.c`` and exercises it against a
stand-in JNI function table, which needs only a C compiler.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_JNI_SOURCE = _ROOT / "pymobile" / "resources" / "android" / "jni" / "pymobile_jni.c"
_HARNESS_DIR = Path(__file__).resolve().parent / "jni"

_START = "/* Text conversion"
_END = "/* Event queue"


def _extract_conversion_code() -> str:
    source = _JNI_SOURCE.read_text(encoding="utf-8")
    start = source.index(_START)
    end = source.index(_END)
    # Keep the section's opening comment block, drop the trailing banner.
    section = source[start:end]
    return section.rsplit("/* ---", 1)[0]


def _utf8(text: str) -> str:
    # ``surrogatepass`` lets the lone-surrogate sample through as the WTF-8
    # bytes a broken producer could emit; the bridge must map them to U+FFFD.
    return text.encode("utf-8", "surrogatepass").hex()


def _utf16(text: str) -> str:
    # A Java String is a sequence of UTF-16 code units and may hold an
    # unpaired surrogate; encode it as-is rather than refusing the input.
    return text.encode("utf-16-le", "surrogatepass").hex()


@pytest.fixture(scope="module")
def converter(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no C compiler available")
    harness = tmp_path_factory.mktemp("jni") / "harness.c"
    harness.write_text(
        (_HARNESS_DIR / "harness_template.c").read_text(encoding="utf-8")
        + _extract_conversion_code()
        + (_HARNESS_DIR / "harness_suffix.c").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    binary = harness.with_suffix(".bin")
    subprocess.run(
        [compiler, "-O1", "-Wall", "-Wno-unused-function", "-o", str(binary), str(harness)],
        check=True,
        capture_output=True,
    )
    return [str(binary)]


def _run(binary: str, requests: list[str]) -> list[str]:
    completed = subprocess.run(
        [binary], input="\n".join(requests) + "\nq\n", capture_output=True, text=True, check=True
    )
    return completed.stdout.splitlines()


#: (label, text) — BMP, non-BMP, Cyrillic, combining marks, ZWJ sequences.
_SAMPLES = [
    ("ascii", "hello"),
    ("cyrillic", "Привіт"),
    ("emoji", "😀"),
    ("emoji-sequence", "👍🏽"),
    ("zwj-family", "👨‍👩‍👧‍👦"),
    ("combining", "e\u0301\u0327"),
    ("other-scripts", "日本語 και العربية"),
    ("lone-surrogate", "\ud800"),
    ("nul", "a\u0000b"),
    ("mixed", "tап 😀 42"),
]


def test_java_to_python_uses_standard_utf8(converter: list[str]) -> None:
    requests = [f"j{_utf16(text)}" for _, text in _SAMPLES]
    results = _run(converter[0], requests)
    assert len(results) == len(_SAMPLES)
    for (label, text), line in zip(_SAMPLES, results, strict=True):
        expected = text.replace("\ud800", "\ufffd").encode("utf-8").hex()
        assert line == expected, f"{label}: {line!r} != {expected!r}"


def test_python_to_java_produces_valid_utf16(converter: list[str]) -> None:
    requests = [f"u{_utf8(text)}" for _, text in _SAMPLES]
    results = _run(converter[0], requests)
    assert len(results) == len(_SAMPLES)
    for (label, text), line in zip(_SAMPLES, results, strict=True):
        expected = text.replace("\ud800", "\ufffd").encode("utf-16-le").hex()
        assert line == expected, f"{label}: {line!r} != {expected!r}"


def test_emoji_is_not_cesu8(converter: list[str]) -> None:
    """The exact regression: 😀 must be f09f9880, never eda0bdedb880."""
    (result,) = _run(converter[0], [f"j{_utf16('😀')}"])
    assert result == "f09f9880"


def test_invalid_bytes_do_not_crash(converter: list[str]) -> None:
    # U+FFFD is fd ff as little-endian UTF-16 bytes; a valid emoji still
    # decodes to its surrogate pair (D83D DE00 → 3d d8 00 de).
    results = _run(converter[0], ["u80", "uff", "ueda0bd", "uf09f9880 extra ignored"])
    assert results[0] == "fdff"
    assert results[1] == "fdff"
    assert results[2] == "fdff"
    assert results[3] == "3dd800de"
