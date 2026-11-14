"""What a build writes and what it warns about.

Two artifacts used to share one file name, so a structural build left a
non-installable ``demo-0.1.0.apk`` where ``pymobile install`` looked for the
APK; and ``target_sdk`` above the platform the toolchain installs was accepted
without a word. Both are observable from a build, so they are tested there.
"""

from __future__ import annotations

from pathlib import Path

from pymobile.compiler.pipeline import BuildPipeline
from pymobile.compiler.scaffold import create_project
from pymobile.compiler.sdk_installer import SDK_PLATFORM
from pymobile.core.config import ProjectConfig
from pymobile.core.ui import Button


def test_structural_and_native_apks_have_different_names(tmp_path: Path) -> None:
    config = ProjectConfig(name="demo", package="com.example.demo", root=tmp_path)
    assert config.apk_name == "demo-0.1.0.apk"
    assert config.apk_name_for("native") == "demo-0.1.0.apk"
    assert config.apk_name_for("native+java") == "demo-0.1.0.apk"
    assert config.apk_name_for("structural") == "demo-0.1.0-debug.apk"


def test_the_abi_suffix_survives_the_mode_suffix(tmp_path: Path) -> None:
    config = ProjectConfig(name="demo", package="com.example.demo", root=tmp_path, abis=["x86_64"])
    assert config.apk_name_for("native") == "demo-0.1.0-x86_64.apk"
    assert config.apk_name_for("structural") == "demo-0.1.0-x86_64-debug.apk"


def test_a_structural_build_writes_the_debug_name(tmp_path: Path) -> None:
    create_project(tmp_path, "Naming")
    config = ProjectConfig(root=tmp_path, name="naming", package="com.example.naming")
    result = BuildPipeline(config, use_cache=False).run()
    assert result.apk.name == "naming-0.1.0-debug.apk"


def test_a_target_sdk_above_the_installed_platform_warns(tmp_path: Path) -> None:
    create_project(tmp_path, "Ahead")
    config = ProjectConfig(
        root=tmp_path,
        name="ahead",
        package="com.example.ahead",
        target_sdk=SDK_PLATFORM + 1,
    )
    result = BuildPipeline(config, use_cache=False).run()
    matching = [w for w in result.warnings if "target_sdk" in w]
    assert matching, result.warnings
    warning = matching[0]
    assert f"android-{SDK_PLATFORM}" in warning
    assert "compiles" in warning


def test_the_stock_target_sdk_does_not_warn(tmp_path: Path) -> None:
    create_project(tmp_path, "Quiet")
    config = ProjectConfig(root=tmp_path, name="quiet", package="com.example.quiet")
    assert config.target_sdk == SDK_PLATFORM
    result = BuildPipeline(config, use_cache=False).run()
    assert not [w for w in result.warnings if "target_sdk = " in w]


def test_buttons_keep_their_documented_handler() -> None:
    """Guard for the tests above: the sample tree still builds."""
    calls: list[int] = []
    button = Button("Go", on_press=lambda: calls.append(1))
    button.press()
    assert calls == [1]
