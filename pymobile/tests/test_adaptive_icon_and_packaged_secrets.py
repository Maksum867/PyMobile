"""An Adaptive icon, a debuggable build, pinned actions and no credentials in the APK.

Four gaps from the 3.1 audit that share one property: each is invisible on a
desktop preview and only shows up in the artifact, the CI configuration or the
unzipped APK — so each is checked at that level here.
"""

from __future__ import annotations

import glob
import re
import zipfile
from pathlib import Path

import pytest

from pymobile.compiler.collector import collect_sources, is_credential_file
from pymobile.compiler.icon import (
    ADAPTIVE_DENSITIES,
    prepare_icons,
    round_icon_xml,
    write_adaptive_icon,
)
from pymobile.compiler.manifest import build_manifest
from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.scaffold import create_project
from pymobile.core.config import DEFAULT_EXCLUDE, ProjectConfig
from pymobile.errors import PyMobileError
from pymobile.resources import default_icon_path

REPO = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path, name: str = "Shell") -> ProjectConfig:
    create_project(tmp_path, name)
    return ProjectConfig(root=tmp_path, name=name.lower(), package=f"com.example.{name.lower()}")


# -- A16: adaptive icon --------------------------------------------------------
def test_an_adaptive_icon_has_three_layers_and_a_manifest(tmp_path: Path) -> None:
    written = write_adaptive_icon(default_icon_path(), tmp_path / "res")

    names = {path.relative_to(tmp_path / "res").as_posix() for path in written}
    for density in ADAPTIVE_DENSITIES:
        for layer in ("background", "foreground", "monochrome"):
            assert f"mipmap-{density}/ic_launcher_{layer}.png" in names
    assert "mipmap-anydpi-v26/ic_launcher.xml" in names

    xml = (tmp_path / "res/mipmap-anydpi-v26/ic_launcher.xml").read_text(encoding="utf-8")
    assert "<adaptive-icon" in xml
    assert "@mipmap/ic_launcher_background" in xml
    assert "@mipmap/ic_launcher_foreground" in xml
    assert "<monochrome" in xml


def test_the_foreground_keeps_the_artwork_inside_the_safe_zone(tmp_path: Path) -> None:
    written = write_adaptive_icon(default_icon_path(), tmp_path / "res")
    foreground = next(
        p for p in written if p.name == "ic_launcher_foreground.png" and "xxxhdpi" in p.parent.name
    )
    assert foreground.exists()
    # The layer is a full-size canvas with transparent margins: launchers mask
    # the middle 72/108, so artwork must not touch the edge.
    from PIL import Image

    with Image.open(foreground) as image:
        assert image.size == (ADAPTIVE_DENSITIES["xxxhdpi"],) * 2
        alpha = image.convert("RGBA").getchannel("A")
        assert alpha.getpixel((0, 0)) == 0, "the corner must be transparent"
        assert alpha.getextrema()[1] > 0, "the artwork itself must be opaque"


def test_the_round_icon_is_the_same_layers(tmp_path: Path) -> None:
    assert "<adaptive-icon" in round_icon_xml()
    assert "@mipmap/ic_launcher_foreground" in round_icon_xml()


def test_the_manifest_points_at_the_adaptive_names(tmp_path: Path) -> None:
    config = _project(tmp_path)
    xml = build_manifest(config)
    assert 'android:icon="@mipmap/ic_launcher"' in xml
    assert 'android:roundIcon="@mipmap/ic_launcher_round"' in xml


def test_legacy_squares_use_the_same_resource_name(tmp_path: Path) -> None:
    # Android 7 ignores mipmap-anydpi-v26, so the square must answer to the same
    # resource name the manifest asks for.
    icons = prepare_icons(None, tmp_path / "res")
    assert {path.name for path in icons.files.values()} == {"ic_launcher.png"}


# -- A15: a debuggable build ---------------------------------------------------
def test_a_normal_build_is_not_debuggable(tmp_path: Path) -> None:
    assert "debuggable" not in build_manifest(_project(tmp_path))


def test_debug_adds_the_attribute(tmp_path: Path) -> None:
    config = _project(tmp_path)
    xml = build_manifest(config, activity="org.pymobile.app.MainActivity", debuggable=True)
    assert 'android:debuggable="true"' in xml


def test_debug_needs_a_native_build(tmp_path: Path) -> None:
    config = _project(tmp_path)
    pipeline = BuildPipeline(config, use_cache=False, debug=True)
    with pytest.raises(PyMobileError) as caught:
        pipeline.run()
    assert "only applies to a native APK" in str(caught.value)


def test_debug_refuses_a_release_keystore(tmp_path: Path) -> None:
    config = _project(tmp_path)
    keystore = tmp_path / "release.jks"
    keystore.write_bytes(b"not a real keystore")
    pipeline = BuildPipeline(config, use_cache=False, native=True, debug=True, keystore=keystore)
    with pytest.raises(PyMobileError) as caught:
        pipeline.run()
    assert "cannot be combined with a release keystore" in str(caught.value)


def test_debug_is_part_of_the_build_mode(tmp_path: Path) -> None:
    config = _project(tmp_path)
    plain = BuildPipeline(config, use_cache=False, native=True).build_mode()
    debug = BuildPipeline(config, use_cache=False, native=True, debug=True).build_mode()
    assert plain != debug
    assert debug.endswith("+debug")


# -- S5: every action is pinned to a commit SHA -------------------------------
def test_workflows_pin_every_action_to_a_sha() -> None:
    unpinned: list[str] = []
    for workflow in glob.glob(str(REPO / ".github/workflows/*.yml")):
        text = Path(workflow).read_text(encoding="utf-8")
        for match in re.finditer(r"uses:\s*([\w-]+/[\w-]+)@(\S+)", text):
            ref = match.group(2)
            if not re.fullmatch(r"[0-9a-f]{40}", ref.split("#")[0].strip()):
                unpinned.append(f"{Path(workflow).name}: {match.group(0)}")
    assert not unpinned, "actions must be pinned to a 40-character commit SHA"


def test_a_pin_says_which_release_it_is() -> None:
    # A bare hash is unreviewable; the trailing comment is what makes a
    # Dependabot diff readable.
    for workflow in glob.glob(str(REPO / ".github/workflows/*.yml")):
        for line in Path(workflow).read_text(encoding="utf-8").splitlines():
            if "uses:" in line and re.search(r"@[0-9a-f]{40}", line):
                assert "#" in line, f"no release comment: {line.strip()}"


def test_dependabot_watches_actions_and_packages() -> None:
    text = (REPO / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: github-actions" in text
    assert "package-ecosystem: pip" in text


# -- S6: credentials do not reach the APK ------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "credentials.prod.json",
        "secrets.yaml",
        "server.pem",
        "release.p12",
        "id_rsa",
    ],
)
def test_credential_files_are_recognised(name: str) -> None:
    assert is_credential_file(Path(name))


def test_app_content_is_not_a_credential() -> None:
    assert not is_credential_file(Path("notes.txt"))
    assert not is_credential_file(Path("data/settings.json"))


def test_a_secret_folder_is_excluded_by_default() -> None:
    # The audit's case: `secret/notes.txt` was packaged.
    assert "secret/**" in DEFAULT_EXCLUDE
    assert "secrets/**" in DEFAULT_EXCLUDE


def test_collect_sources_skips_credentials(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / ".netrc").write_text("machine x", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("app content", encoding="utf-8")

    result = collect_sources(tmp_path, tmp_path / "main.py")
    shipped = {path.name for path in result.files}
    assert "notes.txt" in shipped
    assert ".netrc" not in shipped
    assert "credentials.json" not in shipped


def test_the_build_says_what_it_kept_out(tmp_path: Path) -> None:
    config = _project(tmp_path)
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "notes.txt").write_text("token=1", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")

    result = BuildPipeline(config, use_cache=False).run()
    assert not [w for w in result.warnings if "will be PACKAGED" in w]

    with zipfile.ZipFile(result.apk) as archive:
        names = archive.namelist()
    assert not [n for n in names if "credential" in n or "secret" in n]
