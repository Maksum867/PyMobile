"""Tests for project configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pymobile.core.config import ProjectConfig, load_config
from pymobile.errors import ConfigError


class TestValidation:
    def test_defaults_are_valid(self) -> None:
        assert ProjectConfig().package == "org.pymobile.app"

    @pytest.mark.parametrize("package", ["app", "Com.Example.App", "com..app", "1com.app", ""])
    def test_invalid_packages(self, package: str) -> None:
        with pytest.raises(ConfigError, match="package name"):
            ProjectConfig(package=package)

    @pytest.mark.parametrize("package", ["com.example.app", "org.a.b.c", "io.pymobile.demo_app"])
    def test_valid_packages(self, package: str) -> None:
        assert ProjectConfig(package=package).package == package

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ConfigError, match="name"):
            ProjectConfig(name="  ")

    def test_invalid_version(self) -> None:
        with pytest.raises(ConfigError, match="version"):
            ProjectConfig(version="not-a-version")

    def test_version_code_must_be_positive(self) -> None:
        with pytest.raises(ConfigError, match="version_code"):
            ProjectConfig(version_code=0)

    def test_min_sdk_floor(self) -> None:
        with pytest.raises(ConfigError, match="min_sdk"):
            ProjectConfig(min_sdk=16)

    def test_target_sdk_must_exceed_min(self) -> None:
        with pytest.raises(ConfigError, match="target_sdk"):
            ProjectConfig(min_sdk=30, target_sdk=25)

    def test_invalid_orientation(self) -> None:
        with pytest.raises(ConfigError, match="orientation"):
            ProjectConfig(orientation="upside-down")

    def test_invalid_abi(self) -> None:
        with pytest.raises(ConfigError, match="ABI"):
            ProjectConfig(abis=["mips"])

    def test_empty_abis(self) -> None:
        with pytest.raises(ConfigError, match="abis"):
            ProjectConfig(abis=[])


class TestDerivedPaths:
    def test_paths_resolve_against_root(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, entrypoint="app/main.py", output_dir="out")
        assert config.entrypoint_path == (tmp_path / "app/main.py").resolve()
        assert config.output_path == (tmp_path / "out").resolve()

    def test_icon_path_none_by_default(self) -> None:
        assert ProjectConfig().icon_path is None

    def test_icon_path_resolves(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, icon="assets/logo.png")
        assert config.icon_path == (tmp_path / "assets/logo.png").resolve()

    def test_apk_name_is_sanitised(self) -> None:
        config = ProjectConfig(name="My Cool App!", version="1.2.3")
        assert config.apk_name == "my-cool-app-1.2.3.apk"

    def test_apk_name_falls_back_to_package_for_non_latin(self) -> None:
        config = ProjectConfig(name="Нотатки", package="com.example.notes", version="1.0")
        assert config.apk_name == "notes-1.0.apk"

    def test_apk_name_falls_back_when_only_digits_remain(self) -> None:
        # "Нотатки 2" strips to the bare digit "2" under the ASCII filter —
        # a digit-only file name is no better than no name, so the package
        # segment is used there too.
        config = ProjectConfig(name="Нотатки 2", package="com.example.notes", version="1.0")
        assert config.apk_name == "notes-1.0.apk"
        config = ProjectConfig(name="2048", version="1.0")
        assert config.apk_name == "app-1.0.apk"

    def test_apk_name_never_starts_with_a_dot(self) -> None:
        config = ProjectConfig(name=".hidden", package="com.example.app")
        assert not config.apk_name.startswith(".")

    def test_to_dict_is_serialisable(self) -> None:
        data = ProjectConfig().to_dict()
        assert isinstance(data["root"], str)
        assert data["name"] == "PyMobile App"


class TestFromDict:
    def test_unknown_keys_rejected(self) -> None:
        with pytest.raises(ConfigError, match="Unknown configuration key"):
            ProjectConfig.from_dict({"nmae": "typo"})

    def test_round_trip(self, tmp_path: Path) -> None:
        original = ProjectConfig(name="X", package="com.x.y", root=tmp_path)
        data = original.to_dict()
        data.pop("root")
        assert ProjectConfig.from_dict(data, root=tmp_path).package == "com.x.y"


class TestLoading:
    def test_load_pymobile_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pymobile.toml").write_text(
            '[app]\nname = "Loaded"\npackage = "com.example.loaded"\n', encoding="utf-8"
        )
        config = load_config(tmp_path)
        assert config.name == "Loaded"
        assert config.root == tmp_path.resolve()

    def test_load_without_app_table(self, tmp_path: Path) -> None:
        (tmp_path / "pymobile.toml").write_text('name = "Flat"\n', encoding="utf-8")
        assert load_config(tmp_path).name == "Flat"

    def test_load_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pymobile]\nname = "FromPyproject"\npackage = "com.example.pp"\n',
            encoding="utf-8",
        )
        assert load_config(tmp_path).name == "FromPyproject"

    def test_pymobile_toml_wins(self, tmp_path: Path) -> None:
        (tmp_path / "pymobile.toml").write_text('[app]\nname = "Primary"\n', encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pymobile]\nname = "Secondary"\n', encoding="utf-8"
        )
        assert load_config(tmp_path).name == "Primary"

    def test_direct_file_path(self, tmp_path: Path) -> None:
        path = tmp_path / "custom.toml"
        path.write_text('[app]\nname = "Direct"\n', encoding="utf-8")
        assert load_config(path).name == "Direct"

    def test_missing_config(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match=r"No pymobile\.toml"):
            load_config(tmp_path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.toml")

    def test_malformed_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pymobile.toml").write_text("[app\nname =", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(tmp_path)

    def test_pyproject_without_table(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match=r"No pymobile\.toml"):
            load_config(tmp_path)

    def test_error_carries_hint(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as info:
            load_config(tmp_path)
        assert info.value.hint is not None


def test_version_matches_pyproject() -> None:
    """Guard against the package version drifting from pyproject.toml."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    import pymobile

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml absent (installed wheel)")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert pymobile.__version__ == data["project"]["version"]
