"""Native APK backend — produces a real, installable, signed APK.

Pipeline of this backend:

1. compile the JNI bridge with the NDK          → ``libpymobile.so``
2. compile the launcher activity with ``javac`` → ``.class`` files
3. convert them with ``d8``                     → ``classes.dex``
4. compile resources with ``aapt2``             → ``resources.arsc`` + manifest
5. assemble assets (CPython stdlib + user code) → ``assets/``
6. align with ``zipalign`` and sign with ``apksigner``

Every external command goes through :func:`_run`, which turns a non-zero exit
code into a :class:`~pymobile.errors.PyMobileError` carrying the tool's own
error output — the user sees what ``aapt2`` said, not a Python traceback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ...core.config import ProjectConfig
from ...errors import PyMobileError, ResourceError
from ...log import get_logger
from ...resources import resource_path
from ..manifest import build_manifest
from ..packager import FIXED_TIMESTAMP
from ..toolchain import Toolchain

__all__ = ["NativeBackend", "NativeBuildResult"]

_log = get_logger("compiler.native")

#: Default keystore used for debug builds.
DEBUG_KEYSTORE_NAME = "pymobile-debug.jks"
DEBUG_KEY_ALIAS = "pymobile"
DEBUG_PASSWORD = "android"

#: Environment variables read for release-signing passwords, so they never
#: have to appear on the command line (shell history, ``ps``).
KS_PASS_ENV = "PYMOBILE_KS_PASS"
KEY_PASS_ENV = "PYMOBILE_KEY_PASS"
#: Directory for per-app debug keystores (default ``~/.pymobile/keystores``).
KEYSTORE_DIR_ENV = "PYMOBILE_KEYSTORE_DIR"

#: Framework files that only the desktop tooling uses. They used to be copied
#: into every APK: the build system, the CLI, the Java/JNI sources and prebuilt
#: artefacts (already packaged separately) and the Tk/browser previews.
_DESKTOP_ONLY_FRAMEWORK = (
    "compiler/",
    "resources/",
    "tests/",
    "cli.py",
    "__main__.py",
    "core/watcher.py",
    "core/ui/gui.py",
    "core/ui/web.py",
    "core/ui/preview.py",
    "core/ui/extras_preview.py",
)


def debug_keystore_path(package: str) -> Path:
    """Where the debug key for ``package`` lives: outside the build directory.

    Android only installs an update signed with the same key. The key used to
    be created in ``build/``, so ``pymobile clean`` / ``build --clean`` (or a
    fresh CI checkout) produced a new one and the next APK could not be
    installed over the previous build without uninstalling it — and losing
    the app's data.
    """
    base = os.environ.get(KEYSTORE_DIR_ENV)
    folder = Path(base).expanduser() if base else Path.home() / ".pymobile" / "keystores"
    return folder / f"{package}-debug.jks"

#: Parts of the standard library that are never needed on a phone.
#:
#: ``config-*`` is matched by prefix rather than by name: the directory is
#: really ``config-3.14-aarch64-linux-android``, so the old exact match never
#: fired and 262 KB of build headers shipped in every APK.
STDLIB_EXCLUDES = (
    "test",
    "tests",
    "idlelib",
    "tkinter",
    "turtledemo",
    "lib2to3",
    "ensurepip",
    "distutils",
    "__pycache__",
    "site-packages",
)

#: Prefixes of stdlib directories that are never needed on a phone.
STDLIB_EXCLUDE_PREFIXES = ("config-",)

#: Dropped by ``--minimal-stdlib``: development and desktop-only machinery
#: that an application is very unlikely to import on a phone. Roughly 1.7 MB.
MINIMAL_STDLIB_EXCLUDES = (
    "pydoc_data",
    "unittest",
    "_pyrepl",
    "xmlrpc",
    "wsgiref",
    "curses",
    "venv",
    "turtle.py",
    "doctest.py",
    "pdb.py",
    "profile.py",
    "cProfile.py",
    "pstats.py",
    "pydoc.py",
    "this.py",
    "antigravity.py",
)

#: Dropped by ``--no-ssl`` alongside the OpenSSL shared libraries.
SSL_STDLIB_EXCLUDES = ("ssl.py",)

#: Extension modules dropped by ``--no-ssl``.
SSL_DYNLOAD_PREFIXES = ("_ssl.", "_hashlib.")


@dataclass(slots=True)
class NativeBuildResult:
    """Outcome of a native build."""

    apk: Path
    size: int
    signed: bool
    abis: tuple[str, ...]

    @property
    def size_mb(self) -> float:
        """Artifact size in megabytes."""
        return self.size / (1024 * 1024)


def _run(
    command: list[str | Path],
    *,
    cwd: Path | None = None,
    step: str = "",
    java_home: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run an external tool, converting failures into framework errors.

    ``apksigner`` and ``d8`` are shell wrappers that need a JDK on PATH, so the
    discovered ``java_home`` is injected rather than relying on the caller's
    environment being set up correctly.
    """
    text_command = [str(part) for part in command]
    _log.debug("$ %s", " ".join(text_command))
    environment = dict(os.environ)
    if extra_env:
        environment.update(extra_env)
    if java_home is not None:
        environment["JAVA_HOME"] = str(java_home)
        environment["PATH"] = f"{java_home / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    completed = subprocess.run(
        text_command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PyMobileError(
            f"{step or text_command[0]} failed (exit {completed.returncode})",
            hint=detail[:800] or "The tool produced no output.",
        )
    return completed.stdout


class NativeBackend:
    """Builds a real APK from a project configuration."""

    def __init__(
        self,
        config: ProjectConfig,
        toolchain: Toolchain,
        python_runtime: Path,
        *,
        keystore: Path | None = None,
        keystore_password: str | None = None,
        key_alias: str | None = None,
        key_password: str | None = None,
        abi: str = "arm64-v8a",
    ) -> None:
        self.config = config
        self.toolchain = toolchain
        self.python_runtime = python_runtime
        #: True when the caller supplied a keystore path (release signing).
        self._release_keystore = keystore is not None
        # Passwords may come from the environment instead of the command line.
        if keystore_password is None:
            keystore_password = os.environ.get(KS_PASS_ENV) or None
        if key_password is None:
            key_password = os.environ.get(KEY_PASS_ENV) or None
        #: True when a password was given; never silently reuse the debug one.
        self._keystore_password_given = keystore_password is not None
        self.keystore = keystore
        self.keystore_password = keystore_password or DEBUG_PASSWORD
        self.key_alias = key_alias or DEBUG_KEY_ALIAS
        self.key_password = key_password or keystore_password or DEBUG_PASSWORD
        #: The target ABI (e.g. "arm64-v8a" or "x86_64").
        self.abi = abi
        #: Non-fatal problems worth surfacing to the user.
        self.warnings: list[str] = []

    # -- 1. native library -------------------------------------------------
    def compile_jni(self, workdir: Path) -> Path:
        """Provide ``libpymobile.so``, compiling it only when an NDK is present.

        The bridge contains no project-specific data, so the binary is
        byte-identical for every application. A prebuilt copy ships with the
        package, which lets users skip the 2 GB NDK download entirely.

        Set ``PYMOBILE_BUILD_JNI=1`` to compile it from source with the NDK.
        """
        output_dir = workdir / "lib" / self.abi
        output_dir.mkdir(parents=True, exist_ok=True)
        libdir = self.python_runtime / "lib"

        want_source_build = os.environ.get("PYMOBILE_BUILD_JNI") == "1"
        if not want_source_build or self.toolchain.clang_for(self.abi) is None:
            self._use_prebuilt_bridge(output_dir, libdir)
            return output_dir

        try:
            return self._compile_jni_with_ndk(workdir, output_dir, libdir)
        except PyMobileError as error:
            self.warnings.append(f"falling back to the prebuilt JNI bridge: {error}")
            _log.warning("NDK build failed, using the prebuilt bridge: %s", error)
            self._use_prebuilt_bridge(output_dir, libdir)
            return output_dir

    def _use_prebuilt_bridge(self, output_dir: Path, libdir: Path) -> None:
        """Copy the packaged ``libpymobile.so`` and the interpreter libraries."""
        prebuilt = resource_path("android", "prebuilt", self.abi, "libpymobile.so")
        shutil.copy2(prebuilt, output_dir / "libpymobile.so")
        _log.debug("using the prebuilt JNI bridge")
        self._copy_runtime_libraries(libdir, output_dir)

    def _copy_runtime_libraries(self, libdir: Path, output_dir: Path) -> None:
        """Ship the interpreter and its shared dependencies next to the bridge.

        The official runtime carries each support library twice — libcrypto.so
        and libcrypto_python.so are byte-identical, and so are the ssl and
        sqlite3 pairs. Only the ``_python`` copies are named in the extension
        modules' DT_NEEDED entries, so the plain ones are dead weight: about
        5 MB of a 21 MB APK. They are skipped unless something actually links
        against them.

        With ``--no-ssl`` the TLS libraries are left out altogether, which
        saves a further ~4 MB for an app that makes no HTTPS requests.
        """
        wanted = ["libpython3.14", "libsqlite3"]
        if not self.config.no_ssl:
            wanted += ["libssl", "libcrypto"]

        for library in sorted(libdir.glob("*.so")):
            name = library.name
            if not name.startswith(tuple(wanted)):
                continue
            # Prefer the _python variant; drop the duplicate when both exist.
            if not name.startswith("libpython3.14"):
                stem = name[: -len(".so")]
                if not stem.endswith("_python") and (libdir / f"{stem}_python.so").exists():
                    _log.debug("skipping duplicate runtime library %s", name)
                    continue
            shutil.copy2(library, output_dir / name)

    def _compile_jni_with_ndk(self, workdir: Path, output_dir: Path, libdir: Path) -> Path:
        """Build the JNI bridge from source with the NDK."""
        clang = self.toolchain.clang_for(self.abi)
        assert clang is not None  # checked by the caller

        source = resource_path("android", "jni", "pymobile_jni.c")
        include = self.python_runtime / "include" / "python3.14"
        output = output_dir / "libpymobile.so"

        _run(
            [
                clang,
                "-shared",
                "-fPIC",
                "-O2",
                f"-I{include}",
                str(source),
                f"-L{libdir}",
                "-lpython3.14",
                "-llog",
                "-o",
                output,
            ],
            step="clang (JNI bridge)",
        )
        self._copy_runtime_libraries(libdir, output_dir)
        return output_dir

    # -- 2/3. java → dex ---------------------------------------------------
    def compile_java(self, workdir: Path) -> Path:
        """Provide ``classes.dex``.

        The launcher classes carry no project-specific data — the app id lives
        in the manifest — so the packaged prebuilt dex is used by default. It
        is byte-identical to a freshly compiled one, but costs no time and
        cannot fail, which matters because ``d8`` is fragile on some hosts.

        Set ``PYMOBILE_BUILD_JAVA=1`` to compile from source instead; that path
        is meant for people changing the Java layer of the framework itself.
        """
        want_source_build = os.environ.get("PYMOBILE_BUILD_JAVA") == "1"
        if not want_source_build or not self.toolchain.javac.exists():
            return self._use_prebuilt_dex(workdir)
        try:
            return self._compile_java_from_source(workdir)
        except PyMobileError as error:
            detail = f" {error.hint}" if error.hint else ""
            self.warnings.append(f"falling back to the prebuilt dex:{detail}")
            _log.warning("java build failed, using the prebuilt dex: %s", error)
            return self._use_prebuilt_dex(workdir)

    def _use_prebuilt_dex(self, workdir: Path) -> Path:
        """Copy the packaged launcher dex into the work directory.

        ``classes.dex`` is Dalvik bytecode, not native machine code, so one
        launcher dex is valid for every ABI. It is stored once, next to the
        arm64 bridge; x86_64 builds reuse it (the x86_64 directory carries only
        its own ``libpymobile.so``).
        """
        try:
            prebuilt = resource_path("android", "prebuilt", self.abi, "classes.dex")
        except ResourceError:
            prebuilt = resource_path("android", "prebuilt", "arm64-v8a", "classes.dex")
        target = workdir / "dex" / "classes.dex"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prebuilt, target)
        _log.debug("using the prebuilt launcher dex")
        return target

    def _compile_java_from_source(self, workdir: Path) -> Path:
        """Compile the launcher classes and convert them with ``d8``."""
        src = workdir / "java"
        src.mkdir(parents=True, exist_ok=True)
        for name in (
            "Native.java",
            "DeviceServices.java",
            "ViewBuilder.java",
            "AdvancedViews.java",
            "PythonRuntime.java",
            "MainActivity.java",
        ):
            shutil.copy2(resource_path("android", "java", name), src / name)

        classes = workdir / "classes"
        classes.mkdir(parents=True, exist_ok=True)
        _run(
            [
                self.toolchain.javac,
                "-source",
                "8",
                "-target",
                "8",
                # Sources may contain non-ASCII UI strings; javac defaults to
                # the platform encoding, which is often US-ASCII in containers.
                "-encoding",
                "UTF-8",
                "-nowarn",
                "-bootclasspath",
                self.toolchain.platform_jar,
                "-classpath",
                self.toolchain.platform_jar,
                "-d",
                classes,
                *sorted(src.glob("*.java")),
            ],
            step="javac",
            java_home=self.toolchain.java_home,
        )

        # Feed d8 a single jar rather than every .class path: it keeps the
        # command line short (Windows caps it at ~32k characters) and lets d8
        # see the classes as one unit, which avoids inner-class resolution bugs.
        archive = workdir / "classes.jar"
        _run(
            [self.toolchain.java_home / "bin" / "jar", "cf", archive, "-C", classes, "."],
            step="jar",
            java_home=self.toolchain.java_home,
        )

        dex_dir = workdir / "dex"
        dex_dir.mkdir(parents=True, exist_ok=True)
        _run(
            [
                self.toolchain.d8,
                "--min-api",
                str(self.config.effective_min_sdk),
                "--lib",
                self.toolchain.platform_jar,
                "--output",
                dex_dir,
                archive,
            ],
            step="d8",
            java_home=self.toolchain.java_home,
        )
        return dex_dir / "classes.dex"

    # -- 4. resources ------------------------------------------------------
    def link_resources(self, workdir: Path, icons: dict[str, Path]) -> Path:
        """Compile resources and link the base APK with a binary manifest."""
        res = workdir / "res"
        for density, icon in icons.items():
            target = res / f"mipmap-{density}" / "icon.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            # The icon stage may already have written straight into res/.
            if icon.resolve() != target.resolve():
                shutil.copy2(icon, target)

        values = res / "values"
        values.mkdir(parents=True, exist_ok=True)
        (values / "strings.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<resources>\n"
            f'    <string name="app_name">{_xml_escape(self.config.name)}</string>\n'
            "</resources>\n",
            encoding="utf-8",
        )

        flat = workdir / "flat"
        flat.mkdir(parents=True, exist_ok=True)
        _run([self.toolchain.aapt2, "compile", "--dir", res, "-o", flat], step="aapt2 compile")

        manifest = workdir / "AndroidManifest.xml"
        manifest.write_text(
            build_manifest(self.config, activity="org.pymobile.app.MainActivity"),
            encoding="utf-8",
        )

        base = workdir / "base.apk"
        _run(
            [
                self.toolchain.aapt2,
                "link",
                "-o",
                base,
                "-I",
                self.toolchain.platform_jar,
                "--manifest",
                manifest,
                *sorted(flat.glob("*.flat")),
                "--min-sdk-version",
                str(self.config.effective_min_sdk),
                "--target-sdk-version",
                str(self.config.target_sdk),
                "--auto-add-overlay",
            ],
            step="aapt2 link",
        )
        return base

    # -- 5. assets ---------------------------------------------------------
    def _is_excluded(self, relative: Path) -> bool:
        """Whether a stdlib path is dropped by the current build options."""
        parts = relative.parts
        if any(part in STDLIB_EXCLUDES for part in parts):
            return True
        if any(part.startswith(STDLIB_EXCLUDE_PREFIXES) for part in parts):
            return True
        if self.config.minimal_stdlib and any(part in MINIMAL_STDLIB_EXCLUDES for part in parts):
            return True
        if self.config.no_ssl:
            if any(part in SSL_STDLIB_EXCLUDES for part in parts):
                return True
            if parts[0] == "lib-dynload" and relative.name.startswith(SSL_DYNLOAD_PREFIXES):
                return True
        return False

    def collect_assets(self, sources: list[tuple[str, Path]]) -> dict[str, Path]:
        """Map archive paths to files for the stdlib and the application."""
        assets: dict[str, Path] = {}

        stdlib = self.python_runtime / "lib" / "python3.14"
        for path in stdlib.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(stdlib)
            if self._is_excluded(relative):
                continue
            if path.suffix in (".pyc", ".pyo", ".a", ".exe"):
                continue
            assets[f"assets/python/lib/python3.14/{relative.as_posix()}"] = path

        # Android has no OpenSSL cert bundle at the path CPython expects, so
        # HTTPS fails with CERTIFICATE_VERIFY_FAILED unless we ship one.
        if self.config.no_ssl:
            _log.debug("--no-ssl: skipping the CA bundle")
        else:
            bundle = _find_ca_bundle()
            if bundle is not None:
                assets["assets/python/etc/ssl/cert.pem"] = bundle
            else:  # pragma: no cover - only on hosts without any CA store
                self.warnings.append(
                    "no CA bundle found, so HTTPS will fail on device — "
                    "fix it with: pip install certifi"
                )

        for name, path in sources:
            assets[f"assets/app/{name}"] = path

        # Bundle the framework itself: the app imports `pymobile` on device.
        assets.update(self._framework_assets())
        return assets

    def _framework_assets(self) -> dict[str, Path]:
        """Map the installed ``pymobile`` package into the APK assets."""
        package_root = Path(__file__).resolve().parent.parent.parent
        assets: dict[str, Path] = {}
        for path in package_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(package_root)
            parts = relative.parts
            if any(part in ("__pycache__", "tests") for part in parts):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            posix = relative.as_posix()
            if any(
                posix.startswith(item) if item.endswith("/") else posix == item
                for item in _DESKTOP_ONLY_FRAMEWORK
            ):
                continue
            assets[f"assets/app/pymobile/{posix}"] = path
        return assets

    # -- 6. package --------------------------------------------------------
    def package(
        self,
        base_apk: Path,
        dex: Path,
        native_dir: Path,
        assets: dict[str, Path],
        output: Path,
        workdir: Path,
    ) -> Path:
        """Add dex, native libs and assets, then align and sign.

        Every entry is written with a fixed timestamp, and the resources APK
        produced by ``aapt2`` is copied entry by entry rather than appended to,
        so the same sources always yield the same bytes. Without this the zip
        carried the wall-clock time of the build and two identical builds
        differed.
        """
        staged = workdir / "unsigned.apk"

        def entry(name: str, *, stored: bool = False, mode: int | None = None) -> zipfile.ZipInfo:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            return info

        with zipfile.ZipFile(base_apk) as resources:
            base_entries = [(i, resources.read(i.filename)) for i in resources.infolist()]

        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for info, data in base_entries:
                archive.writestr(
                    entry(info.filename, stored=info.compress_type == zipfile.ZIP_STORED),
                    data,
                )
            archive.writestr(entry("classes.dex"), dex.read_bytes())
            for library in sorted(native_dir.glob("*.so")):
                # Native libraries must be stored uncompressed and page-aligned
                # so Android can load them directly from the APK.
                archive.writestr(
                    entry(f"lib/{self.abi}/{library.name}", stored=True, mode=0o755),
                    library.read_bytes(),
                )
            for name, path in sorted(assets.items()):
                archive.writestr(entry(name), path.read_bytes())

        aligned = workdir / "aligned.apk"
        _run(
            [self.toolchain.zipalign, "-f", "-p", "4", staged, aligned],
            step="zipalign",
        )

        keystore = self.keystore or self._ensure_debug_keystore(workdir)
        if self._release_keystore and not self._keystore_password_given:
            raise PyMobileError(
                "A release keystore was given without a password",
                hint=(
                    f"Set {KS_PASS_ENV} (and {KEY_PASS_ENV} if the key has its own "
                    "password) or pass --ks-pass. The debug password is not used."
                ),
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                self.toolchain.apksigner,
                "sign",
                "--ks",
                keystore,
                "--ks-key-alias",
                self.key_alias,
                # env: keeps the passwords out of the process list.
                "--ks-pass",
                "env:PYMOBILE_SIGN_KS_PASS",
                "--key-pass",
                "env:PYMOBILE_SIGN_KEY_PASS",
                "--out",
                output,
                aligned,
            ],
            step="apksigner",
            java_home=self.toolchain.java_home,
            extra_env={
                "PYMOBILE_SIGN_KS_PASS": self.keystore_password,
                "PYMOBILE_SIGN_KEY_PASS": self.key_password,
            },
        )
        return output

    def _ensure_debug_keystore(self, workdir: Path) -> Path:
        """Create (once) the app's debug keystore outside the build directory.

        A key left in ``build/`` by an older version is adopted, so existing
        installs keep accepting updates.
        """
        keystore = debug_keystore_path(self.config.package)
        if keystore.exists():
            return keystore
        keystore.parent.mkdir(parents=True, exist_ok=True)
        legacy = self.config.output_path / DEBUG_KEYSTORE_NAME
        if legacy.exists():
            shutil.copy2(legacy, keystore)
            _log.info("moved the debug keystore out of the build directory: %s", keystore)
            return keystore
        self.warnings.append(
            f"created a new debug signing key at {keystore}. Keep this file: APKs signed "
            "with another key cannot be installed over this build (set "
            f"{KEYSTORE_DIR_ENV} to share it, e.g. on CI)"
        )
        _run(
            [
                self.toolchain.keytool,
                "-genkeypair",
                "-keystore",
                keystore,
                "-storepass",
                DEBUG_PASSWORD,
                "-keypass",
                DEBUG_PASSWORD,
                "-alias",
                DEBUG_KEY_ALIAS,
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "10000",
                "-dname",
                "CN=PyMobile Debug, OU=PyMobile, O=PyMobile, C=UA",
            ],
            cwd=workdir,
            step="keytool",
            java_home=self.toolchain.java_home,
        )
        return keystore

    def verify(self, apk: Path) -> bool:
        """Check the signature with ``apksigner verify``."""
        output = _run(
            [self.toolchain.apksigner, "verify", "--verbose", apk],
            step="apksigner verify",
            java_home=self.toolchain.java_home,
        )
        return "Verifies" in output


def _find_ca_bundle(cache_dir: Path | None = None) -> Path | None:
    """Locate a PEM bundle of root certificates on the build machine.

    Android ships no OpenSSL cert file, so one has to travel inside the APK or
    every HTTPS request fails with CERTIFICATE_VERIFY_FAILED. Sources are tried
    in order of reliability:

    1. ``certifi``, when installed;
    2. the paths OpenSSL was compiled with (typical on Linux and macOS);
    3. well-known distribution paths;
    4. the Windows system trust store, exported to PEM on the fly — Windows
       keeps certificates in a registry-backed store rather than a file, so
       steps 2 and 3 find nothing there.
    """
    try:
        import certifi

        candidate = Path(certifi.where())
        if candidate.exists():
            return candidate
    except ImportError:
        pass

    import ssl

    paths = ssl.get_default_verify_paths()
    for value in (paths.cafile, paths.openssl_cafile):
        if value and Path(value).exists():
            return Path(value)

    for fallback in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
    ):
        candidate = Path(fallback)
        if candidate.exists():
            return candidate

    return _export_windows_trust_store(cache_dir)


def _export_windows_trust_store(cache_dir: Path | None = None) -> Path | None:
    """Write the Windows root store to a PEM file and return its path."""
    import ssl

    enumerate_certs = getattr(ssl, "enum_certificates", None)
    if enumerate_certs is None:
        return None

    chunks: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            entries = enumerate_certs(store)
        except (OSError, PermissionError):  # pragma: no cover - locked-down hosts
            continue
        for der, encoding, trust in entries:
            # `trust` is True for "all purposes" or a set of allowed OIDs;
            # 1.3.6.1.5.5.7.3.1 is TLS server authentication.
            usable = trust is True or (isinstance(trust, set) and "1.3.6.1.5.5.7.3.1" in trust)
            if encoding == "x509_asn" and usable:
                chunks.append(ssl.DER_cert_to_PEM_cert(der))

    if not chunks:
        return None

    target_dir = cache_dir or (Path.home() / ".cache" / "pymobile")
    target_dir.mkdir(parents=True, exist_ok=True)
    bundle = target_dir / "windows-cacert.pem"
    bundle.write_text("".join(chunks), encoding="ascii")
    _log.debug("exported %d certificates from the Windows trust store", len(chunks))
    return bundle


def _xml_escape(text: str) -> str:
    """Escape a string for inclusion in an XML resource."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "\\'")
    )
