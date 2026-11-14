"""The release artifact audit: hashes, CycloneDX components, safe paths."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

# tools/ is not part of the published repository; skip rather than break
# collection for everyone who clones it.
audit = pytest.importorskip("tools.release_audit").audit


def wheel(path: Path, *names: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "demo-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nRequires-Dist: certifi >= 2024\n",
        )
        for name in names:
            archive.writestr(name, "content")


def test_audit_outputs_hashes_and_cyclonedx_component(tmp_path: Path) -> None:
    wheel(tmp_path / "demo-1.0-py3-none-any.whl", "demo/__init__.py")
    report = audit(tmp_path)
    assert report["bomFormat"] == "CycloneDX"
    assert report["artifacts"][0]["sha256"]
    assert report["components"][0]["name"] == "demo"


@pytest.mark.parametrize("unsafe", ["demo/__pycache__/x.pyc", "keys/release.pem", "../escape.py"])
def test_audit_rejects_unsafe_packaged_paths(tmp_path: Path, unsafe: str) -> None:
    wheel(tmp_path / "demo-1.0-py3-none-any.whl", unsafe)
    with pytest.raises(ValueError, match="unsafe packaged path"):
        audit(tmp_path)


def test_audit_output_is_json_serialisable(tmp_path: Path) -> None:
    wheel(tmp_path / "demo-1.0-py3-none-any.whl", "demo/__init__.py")
    assert (
        json.loads(json.dumps(audit(tmp_path), sort_keys=True))["schema"]
        == "pymobile-release-audit/v1"
    )
