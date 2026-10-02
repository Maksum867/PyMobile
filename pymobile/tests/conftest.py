"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pymobile.core.bridge import StubBridge, active_bridge, reset_bridge, set_bridge
from pymobile.core.config import ProjectConfig


@pytest.fixture(autouse=True)
def _isolated_debug_keystores(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep debug keys created by native builds out of the real ~/.pymobile."""
    monkeypatch.setenv("PYMOBILE_KEYSTORE_DIR", str(tmp_path_factory.mktemp("keystores")))


@pytest.fixture(autouse=True)
def _restore_active_bridge() -> Iterator[None]:
    """Leave the process-wide bridge selection exactly as the test found it.

    Tests that install a :class:`~pymobile.core.bridge.WebBridge` used to leak
    it into every later test in the same pytest process: ``pymobile run`` then
    picked the web bridge up, and a test asserting on the console output failed
    — but only when those files ran together.
    """
    previous = active_bridge()
    yield
    set_bridge(previous)


@pytest.fixture
def bridge() -> Iterator[StubBridge]:
    """A recording stub bridge installed as the active platform bridge."""
    stub = StubBridge(verbose=False)
    set_bridge(stub)
    yield stub
    reset_bridge()


@pytest.fixture
def project(tmp_path: Path) -> ProjectConfig:
    """A minimal, valid project on disk."""
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return ProjectConfig(
        name="Demo App",
        package="com.example.demo",
        root=tmp_path,
        output_dir="build",
    )
