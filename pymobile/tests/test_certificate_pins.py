"""Certificate pinning: the app trusts only the keys you listed.

The audit's S7 was two things, and the second one is the one that matters: a
pinned app also stops trusting the *device's* user-installed CAs, which is what
a corporate proxy, a "Wi-Fi inspector" app or a malicious profile installs.
Android reads that from a network security config referenced by the manifest,
so a build without one has no pinning at all — no Python-side check can stand
in for it.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pymobile.compiler.manifest import build_manifest, network_security_config
from pymobile.core.config import ProjectConfig, load_config
from pymobile.errors import ConfigError

ANDROID = "{http://schemas.android.com/apk/res/android}"
PIN = "sha256/" + base64.b64encode(bytes(range(32))).decode()


def _config(tmp_path: Path, pins: dict[str, list[str]]) -> ProjectConfig:
    return ProjectConfig(root=tmp_path, name="Pinned", network_pins=pins)


# -- the resource Android actually reads ---------------------------------------
def test_the_config_pins_the_domain_and_denies_cleartext() -> None:
    document = ET.fromstring(network_security_config({"api.example.com": [PIN]}))
    assert document.find("base-config").get("cleartextTrafficPermitted") == "false"
    domain_config = document.find("domain-config")
    assert domain_config is not None
    domain = domain_config.find("domain")
    assert domain is not None and domain.text == "api.example.com"
    pin = domain_config.find("pin-set").find("pin")
    assert pin is not None
    # Android wants the base64 digest without the "sha256/" prefix openssl
    # prints; the two forms must not be confused.
    assert pin.get("digest") == "SHA-256"
    assert pin.text == PIN[len("sha256/") :]


def test_every_domain_and_every_pin_is_written() -> None:
    document = ET.fromstring(
        network_security_config({"a.example.com": [PIN], "b.example.com": [PIN, PIN]})
    )
    domains = [node.text for node in document.findall("domain-config/domain")]
    assert domains == ["a.example.com", "b.example.com"]
    assert len(document.findall("domain-config")[1].findall("pin-set/pin")) == 2


def test_subdomains_are_covered() -> None:
    document = ET.fromstring(network_security_config({"example.com": [PIN]}))
    assert document.find("domain-config/domain").get("includeSubdomains") == "true"


def test_the_manifest_points_at_the_config() -> None:
    config = _config(Path.cwd() / "does-not-matter", {"api.example.com": [PIN]})
    application = ET.fromstring(build_manifest(config)).find("application")
    assert application is not None
    meta = application.find("meta-data")
    assert meta is not None
    assert meta.get(f"{ANDROID}name") == "android.security.networkSecurityConfig"
    assert meta.get(f"{ANDROID}resource") == "@xml/network_security_config"


def test_a_build_without_pins_has_no_meta_data(tmp_path: Path) -> None:
    application = ET.fromstring(build_manifest(_config(tmp_path, {}))).find("application")
    assert application is not None
    assert application.find("meta-data") is None


# -- the configuration a developer writes --------------------------------------
@pytest.mark.parametrize(
    "pins, message",
    [
        ({"api.example.com": ["not a pin"]}, "base64"),
        ({"api.example.com": [PIN[:10]]}, "base64"),
        ({"https://api.example.com/": [PIN]}, "is not a domain"),
        ({"api.example.com": PIN}, "non-empty list"),
        ({"api.example.com": []}, "non-empty list"),
        # Valid base64 that is not 32 bytes: caught after decoding, and the
        # message has to say the length, not just "not base64".
        ({"api.example.com": ["sha256/" + base64.b64encode(b"x" * 16).decode()]}, "expected 32"),
        ({"api.example.com": ["sha256/!!!"]}, "base64"),
        ({"api.example.com": [b"bytes"]}, "is not a pin"),
    ],
)
def test_a_malformed_pin_is_refused_when_the_config_is_read(
    tmp_path: Path, pins: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        _config(tmp_path, pins)  # type: ignore[arg-type]
    assert message in str(caught.value)


def test_a_pin_table_is_read_from_the_project_file(tmp_path: Path) -> None:
    (tmp_path / "pymobile.toml").write_text(
        f'name = "Pinned"\n\n[network.pins]\n"api.example.com" = ["{PIN}"]\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.network_pins == {"api.example.com": [PIN]}


def test_the_generated_schema_documents_the_field() -> None:
    from pymobile.compiler.schema import build_config_schema

    properties = build_config_schema()["properties"]["app"]["properties"]
    assert "network_pins" in properties
    assert "sha256" in properties["network_pins"]["description"]
