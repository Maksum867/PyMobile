"""Automatic Android SDK/NDK provisioning.

Native builds need ~4 GB of Google tooling. Asking a Python developer to
install Android Studio is a poor first experience, so ``pymobile setup-sdk``
fetches exactly the packages required — command-line tools, platform,
build-tools and NDK — into a private directory.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from ..errors import PyMobileError
from ..log import get_logger
from .toolchain import normalise_java_home

__all__ = ["install_sdk", "default_sdk_home", "REQUIRED_PACKAGES"]

_log = get_logger("compiler.sdk")

#: Everything needed to build an APK using the prebuilt JNI bridge (~450 MB).
MINIMAL_PACKAGES = (
    "platforms;android-35",
    "build-tools;35.0.0",
)

#: Adds the NDK, needed only to rebuild the native bridge from source (~2 GB).
REQUIRED_PACKAGES = (*MINIMAL_PACKAGES, "ndk;27.3.13750724")

# URL plus a pinned SHA-256. A host without a pinned hash is deliberately
# refused instead of extracting an unauthenticated developer toolchain.
# Linux/Windows keep the 11076708 pin; Darwin uses the current command-line
# tools (Homebrew android-commandlinetools 15859902) because 11076708 was
# never checksummed for macOS in this project.
_CMDLINE_TOOLS = {
    "Linux": (
        "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip",
        "2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258",
    ),
    "Windows": (
        "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
        "4d6931209eebb1bfb7c7e8b240a6a3cb3ab24479ea294f3539429574b1eec862",
    ),
    "Darwin": (
        "https://dl.google.com/android/repository/commandlinetools-mac_x86_64-15859902_latest.zip",
        "c5a6378ab5cf7e0d5701921405115befff13e9ff7417fb588389338f8bd050f3",
    ),
}

_CMDLINE_TOOLS_ARM = {
    "Darwin": (
        "https://dl.google.com/android/repository/commandlinetools-mac_arm64-15859902_latest.zip",
        "835b62a26162b229b441d1f6d4680383815a270809eb33522c0d480fa5002c4e",
    ),
    "Linux": (
        "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip",
        "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583",
    ),
}

_JDK_BASE = "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.13%2B11/"
#: host system -> (archive name, is_zip, sha256). x64 defaults; ARM is in
#: ``_JDK_ARCHIVES_ARM``. Tests assert these three keys exist.
_JDK_ARCHIVES = {
    "Linux": (
        "OpenJDK17U-jdk_x64_linux_hotspot_17.0.13_11.tar.gz",
        False,
        "8682892fc02965930b9022c066fa164dd6f458ef4a5dc262016aa28333b30f49",
    ),
    "Windows": (
        "OpenJDK17U-jdk_x64_windows_hotspot_17.0.13_11.zip",
        True,
        "6b64255e1bd690b09a135d44ac6b0d6bd4490728a8bad81904941d2789d394c0",
    ),
    "Darwin": (
        "OpenJDK17U-jdk_x64_mac_hotspot_17.0.13_11.tar.gz",
        False,
        "840535070200a944a6b582d258ee84608bd25c9f2b5d1cdddb58dfadb019675a",
    ),
}

_JDK_ARCHIVES_ARM = {
    "Linux": (
        "OpenJDK17U-jdk_aarch64_linux_hotspot_17.0.13_11.tar.gz",
        False,
        "0c17fa4f14c0d2cc9e9334f996fccdddc5da4459d768f3105c7ff0283c47bf62",
    ),
    "Darwin": (
        "OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.13_11.tar.gz",
        False,
        "d8b2f77f755d06e81a540834c5be22ed86f3c8a51a20396606c074303f8f9e2d",
    ),
}


def _is_arm() -> bool:
    return platform.machine().lower() in {"arm64", "aarch64"}


def _cmdline_tools_entry() -> tuple[str, str] | None:
    system = platform.system()
    if _is_arm() and system in _CMDLINE_TOOLS_ARM:
        return _CMDLINE_TOOLS_ARM[system]
    return _CMDLINE_TOOLS.get(system)


def _jdk_archive_for_host() -> tuple[str, bool, str] | None:
    system = platform.system()
    if _is_arm() and system in _JDK_ARCHIVES_ARM:
        return _JDK_ARCHIVES_ARM[system]
    return _JDK_ARCHIVES.get(system)


def default_sdk_home() -> Path:
    """Directory PyMobile installs the toolchain into."""
    override = os.environ.get("PYMOBILE_SDK_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".andro"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path, description: str, expected_sha256: str) -> Path:
    """Fetch and verify a pinned archive before it is ever extracted."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size == 0:
        _log.info("downloading %s…", description)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with (
                urllib.request.urlopen(url, timeout=300) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            os.replace(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PyMobileError(
                f"Could not download {description}: {error}",
                hint=f"Download {url} manually and verify SHA-256 {expected_sha256}.",
            ) from error
    actual = _sha256(target)
    if actual.casefold() != expected_sha256.casefold():
        target.unlink(missing_ok=True)
        raise PyMobileError(
            f"Checksum verification failed for {description}",
            hint=f"Expected SHA-256 {expected_sha256}, got {actual}. The archive was deleted.",
        )
    return target


def _first_jdk_home(home: Path) -> Path | None:
    """The newest usable JDK under ``home``, with macOS bundles normalised.

    ``jdk-17.0.13+11/`` on macOS contains ``Contents/Home/bin/javac``; the old
    code returned that outer directory from the *existing install* branch (the
    download branch normalised it), so a second build on such a machine found
    the JDK but then failed at every ``javac``/``keytool`` call.
    """
    for candidate in sorted(home.glob("jdk-17*"), reverse=True):
        resolved = normalise_java_home(candidate)
        if resolved is not None:
            return resolved
    return None


def _ensure_jdk(home: Path) -> Path:
    """Return a JDK 17 path, downloading Temurin when necessary."""
    existing = _first_jdk_home(home)
    if existing is not None:
        return existing

    java_home = normalise_java_home(os.environ.get("JAVA_HOME"))
    if java_home is not None:
        return java_home

    entry = _jdk_archive_for_host()
    if entry is None:
        raise PyMobileError(
            f"No automatic JDK download for {platform.system()} {platform.machine()}",
            hint="Install Temurin/OpenJDK 17 and set JAVA_HOME.",
        )
    filename, is_zip, checksum = entry
    archive = _download(_JDK_BASE + filename, home / filename, "JDK 17 (~190 MB)", checksum)

    _log.info("extracting the JDK…")
    if is_zip:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(home)
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(home, filter="data")
    archive.unlink(missing_ok=True)

    found = _first_jdk_home(home)
    if found is None:
        raise PyMobileError(
            "JDK extraction produced no directory with bin/javac",
            hint="Delete the cache in ~/.andro and run `pymobile setup-sdk` again.",
        )
    return found


def install_sdk(
    home: Path | None = None,
    *,
    packages: tuple[str, ...] = MINIMAL_PACKAGES,
    with_ndk: bool = False,
) -> Path:
    """Install the Android toolchain and return the SDK root.

    Defaults to the minimal set; pass ``with_ndk=True`` to also fetch the NDK,
    which is only required for rebuilding the native bridge.

    Safe to re-run: existing downloads and packages are reused.
    """
    if with_ndk:
        packages = REQUIRED_PACKAGES
    root = Path(home) if home else default_sdk_home()
    root.mkdir(parents=True, exist_ok=True)
    sdk = root / "sdk"

    jdk = _ensure_jdk(root)
    _log.debug("using JDK at %s", jdk)

    tools_bin = sdk / "cmdline-tools" / "latest" / "bin"
    script = "sdkmanager.bat" if platform.system() == "Windows" else "sdkmanager"
    sdkmanager = tools_bin / script
    if not sdkmanager.exists():
        entry = _cmdline_tools_entry()
        if entry is None:
            raise PyMobileError(
                f"No pinned command-line-tools checksum for {platform.system()}",
                hint=(
                    "Set ANDROID_HOME to a verified existing SDK, "
                    "or contribute a verified pinned checksum."
                ),
            )
        url, checksum = entry
        archive = _download(
            url, root / "cmdline-tools.zip", "Android command-line tools (~150 MB)", checksum
        )
        _log.info("extracting the command-line tools…")
        staging = root / "_cmdline"
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(staging)
        destination = sdk / "cmdline-tools" / "latest"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(destination, ignore_errors=True)
        shutil.move(str(staging / "cmdline-tools"), str(destination))
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)
        if platform.system() != "Windows":
            sdkmanager.chmod(0o755)

    environment = {
        **os.environ,
        "JAVA_HOME": str(jdk),
        "PATH": f"{jdk / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    }

    _log.info("accepting SDK licences…")
    subprocess.run(
        [str(sdkmanager), f"--sdk_root={sdk}", "--licenses"],
        input="y\n" * 30,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    _log.info("installing %s (this can take several minutes)…", ", ".join(packages))
    completed = subprocess.run(
        [str(sdkmanager), f"--sdk_root={sdk}", *packages],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PyMobileError(
            "sdkmanager failed to install the required packages",
            hint=detail[-800:] or "Check your connection and available disk space (~5 GB).",
        )
    return sdk
