"""The build pipeline.

The compiler is a sequence of small, independent stages:

``validate → collect → compile → icons → manifest → package``

Each stage is a plain function with an explicit input and output, timed and
logged individually. Adding a stage (signing, native libs, obfuscation) means
appending to the list — no existing stage has to change.
"""

from __future__ import annotations

import ast
import hashlib
import os
import py_compile
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import SECRET_LIKE_BASENAMES, SECRET_LIKE_SUFFIXES, ProjectConfig
from ..errors import PyMobileError
from ..log import get_logger
from .backends.native import DEBUG_KEY_ALIAS, NativeBackend, framework_asset_files
from .cache import BuildCache, fingerprint_files
from .collector import _ALWAYS_EXCLUDED_DIRS, SourceSet, collect_sources
from .icon import IconSet, prepare_icons
from .manifest import build_manifest
from .packager import ApkPackager, PackageResult
from .runtime import ensure_runtime
from .toolchain import find_toolchain
from .widgets import (
    CustomWidgets,
    MissingRendererError,
    dex_has_case,
    dex_has_class,
    scan_custom_widgets,
)

__all__ = ["BuildPipeline", "BuildResult", "StageTiming", "build_apk"]

_log = get_logger("compiler")

#: Python version of the interpreter embedded in native APKs.
DEVICE_PYTHON = (3, 14)

#: Asset extensions packaged without any configuration. Kept in sync with
#: ``collector.collect_sources``; ``asset_suffixes`` in pymobile.toml adds to it.
DEFAULT_ASSET_SUFFIXES = frozenset(
    {".py", ".json", ".txt", ".toml", ".png", ".jpg", ".jpeg", ".webp", ".ttf", ".otf"}
)

#: Directories never considered when reporting files that were not packaged.
#: Reuses the same set the collector skips so the warning does not fire on
#: cache/tag files inside .pytest_cache/.mypy_cache/.ruff_cache/node_modules
#: (П-19 — the collector already knows about them).
_IGNORED_ASSET_DIRS = frozenset(_ALWAYS_EXCLUDED_DIRS | {".pymobile", "build", "dist"})

#: Names that make ``<receiver>.notify(...)`` a PyMobile notification: ``app``,
#: ``self.app``, ``my_app``, ``app.notifications``, ``get_bridge()`` … A
#: ``threading.Condition.notify()`` or an observer's ``notify`` has none of them.
_NOTIFY_RECEIVER_TOKENS = frozenset({"app", "application", "notifications", "bridge"})
#: Classes whose mere use means the project posts notifications itself.
_NOTIFY_CLASSES = frozenset({"Notifications", "NotificationSpec"})
_NOTIFY_FALLBACK = re.compile(
    r"(?:\b(?:app|application|notifications|bridge)|_app)\s*\.\s*notify\s*\("
    r"|\b(?:Notifications|NotificationSpec)\s*\("
)


def _receiver_tokens(node: ast.expr) -> set[str]:
    """Lower-case words of a receiver expression (``self.my_app`` → my, app)."""
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func  # App.current().notify -> App.current
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            break
    words: set[str] = set()
    for part in parts:
        words.update(word for word in re.split(r"[^a-z0-9]+", part.lower()) if word)
    return words


def find_notification_use(sources: SourceSet) -> str | None:
    """``"main.py:12"`` of the first line that posts a notification, or ``None``.

    Recognises ``app.notify(...)``, ``self.app.notify(...)``,
    ``app.notifications.notify(...)``, ``App.current().notify(...)``,
    ``get_bridge().notify(...)`` and any use of ``Notifications`` /
    ``NotificationSpec``. Files that do not parse fall back to a regex.
    """
    for path in sources.files:
        if path.suffix != ".py":
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            # Bytes, not text: the interpreter then strips a UTF-8 BOM (Notepad and
            # Windows PowerShell write one) and honours a ``# coding: cp1251`` line.
            # A str that starts with a BOM is a SyntaxError, and a file that is not
            # UTF-8 would not even decode — either way the file would be skipped.
            tree = ast.parse(data)
        except (SyntaxError, ValueError):
            text = data.decode("utf-8-sig", errors="replace")
            match = _NOTIFY_FALLBACK.search(text)
            if match is not None:
                line = text.count("\n", 0, match.start()) + 1
                return f"{path.relative_to(sources.root).as_posix()}:{line}"
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                posts = node.func.attr == "notify" and bool(
                    _receiver_tokens(node.func.value) & _NOTIFY_RECEIVER_TOKENS
                )
            elif isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                posts = name in _NOTIFY_CLASSES and isinstance(node.ctx, ast.Load)
            else:
                continue
            if posts:
                return f"{path.relative_to(sources.root).as_posix()}:{node.lineno}"
    return None


@dataclass(frozen=True, slots=True)
class StageTiming:
    """How long one stage took."""

    name: str
    seconds: float


@dataclass(slots=True)
class BuildResult:
    """Everything the caller needs to know about a build."""

    apk: Path
    size: int
    entries: int
    duration: float
    cached: bool = False
    native: bool = False
    icon_is_default: bool = True
    timings: list[StageTiming] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def size_kb(self) -> float:
        """APK size in kilobytes."""
        return self.size / 1024

    def summary(self) -> str:
        """One-line human-readable summary."""
        state = "cached" if self.cached else "built"
        if self.native:
            return (
                f"{state} {self.apk.name} — {self.size / (1024 * 1024):.1f} MB, "
                f"installable, {self.duration:.1f}s"
            )
        return (
            f"{state} {self.apk.name} — {self.size_kb:.1f} KB, "
            f"{self.entries} entries, {self.duration:.2f}s"
        )


class BuildPipeline:
    """Runs the stages that turn a project into an APK."""

    def __init__(
        self,
        config: ProjectConfig,
        *,
        use_cache: bool = True,
        native: bool = False,
        on_stage: Callable[[str], None] | None = None,
        keystore: Path | None = None,
        keystore_password: str | None = None,
        key_alias: str | None = None,
        key_password: str | None = None,
    ) -> None:
        self.config = config
        self.use_cache = use_cache
        self.native = native
        self.on_stage = on_stage
        self.keystore = keystore
        self.keystore_password = keystore_password
        self.key_alias = key_alias
        self.key_password = key_password
        self.warnings: list[str] = []
        self._timings: list[StageTiming] = []
        #: The project's own widget types, found by :meth:`_check_widget_types`
        #: and checked against the packaged dex by :meth:`_verify_renderers`.
        self._custom_widgets = CustomWidgets(frozenset(), frozenset(), frozenset())

    # -- helpers -----------------------------------------------------------
    def _stage(self, name: str, action: Callable[[], Any]) -> Any:
        """Run one stage, timing it and reporting progress."""
        if self.on_stage is not None:
            self.on_stage(name)
        started = time.perf_counter()
        try:
            result = action()
        except PyMobileError:
            raise
        except Exception as exc:
            raise PyMobileError(
                f"Build stage {name!r} failed: {exc}",
                hint="Run with --verbose for the full traceback.",
            ) from exc
        elapsed = time.perf_counter() - started
        self._timings.append(StageTiming(name, elapsed))
        _log.debug("stage %s finished in %.3fs", name, elapsed)
        return result

    # -- stages ------------------------------------------------------------
    def _validate(self) -> None:
        """Re-check the configuration and warn about likely mistakes."""
        self.config.validate()
        if self.config.min_sdk < self.config.effective_min_sdk:
            self.warnings.append(
                f"min_sdk = {self.config.min_sdk} is below what the embedded CPython 3.14 "
                f"supports; the APK declares minSdkVersion {self.config.effective_min_sdk} "
                f"(Android 7.0) instead. Set min_sdk = {self.config.effective_min_sdk} in "
                "pymobile.toml to silence this warning."
            )
        permissions = {str(p) for p in self.config.permissions}
        if "android.permission.INTERNET" not in permissions and self._uses_http():
            self.warnings.append(
                "Your code uses HttpClient but android.permission.INTERNET is not "
                "declared; HTTP requests will fail on device."
            )
        # POST_NOTIFICATIONS is checked in _check_notifications(), after the
        # sources are collected: it only matters to code that posts one.
        if self.config.no_ssl and self._uses_http():
            self.warnings.append(
                "Built with --no-ssl but code uses HttpClient; HTTPS requests will fail."
            )

    def _uses_http(self) -> bool:
        """Best-effort scan of the app sources for HTTP client usage.

        Keeps the missing-INTERNET warning from firing on apps that never
        touch the network. Only ``.py`` files are inspected and any read
        error simply suppresses the warning rather than failing the build.
        """
        markers = ("HttpClient", "app.http", ".http.")
        source = self.config.source_path
        if not source.is_dir():
            return False
        for path in source.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(marker in text for marker in markers):
                return True
        return False

    def _check_notifications(self, sources: SourceSet) -> None:
        """Warn about missing POST_NOTIFICATIONS — only for code that posts one.

        From Android 13 (API 33) an app must hold ``POST_NOTIFICATIONS`` to show
        a notification, and the framework targets 35 by default, so the warning
        used to fire for *every* project with the stock config — including the
        ones that never call ``notify()``. It now looks for the calls: an app
        with no notification code gets no warning. Sources are the ones that
        ship (``exclude`` and the virtualenv folders are already applied), and
        the scan is syntactic, so a comment or a docstring mentioning
        ``app.notify`` does not count.
        """
        if self.config.target_sdk < 33:
            return
        if "android.permission.POST_NOTIFICATIONS" in {str(p) for p in self.config.permissions}:
            return
        if any("POST_NOTIFICATIONS" in warning for warning in self.warnings):
            return  # Permission.POST_NOTIFICATIONS in code: already reported
        where = find_notification_use(sources)
        if where is None:
            return
        self.warnings.append(
            "targetSdk >= 33 without POST_NOTIFICATIONS: notifications stay hidden. "
            f"{where} posts a notification; add \"android.permission.POST_NOTIFICATIONS\" to "
            "permissions in pymobile.toml (and ask for it with "
            "app.permissions.require(Permission.POST_NOTIFICATIONS))."
        )

    def _check_requested_permissions(self, sources: SourceSet) -> None:
        """Warn about permissions used in code but missing from the config.

        Android denies undeclared runtime permissions without showing a dialog,
        which is almost impossible to debug from the app side.
        """
        import re

        declared = {str(p).rsplit(".", 1)[-1] for p in self.config.permissions}
        pattern = re.compile(r"Permission\.([A-Z_]+)\b")
        used: set[str] = set()
        for path in sources.files:
            if path.suffix != ".py":
                continue
            try:
                used.update(pattern.findall(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue

        missing = sorted(used - declared)
        if missing:
            names = ", ".join(f"android.permission.{name}" for name in missing)
            self.warnings.append(
                f"requested in code but not declared in pymobile.toml: {names} — "
                "Android will deny them without showing a dialog"
            )

    def _warn_unexcluded_output(self) -> None:
        """Warn when the build output directory would be packaged into the APK.

        ``exclude`` adds to the built-in patterns, so this is the case of a
        project that turned that off (``exclude_only``) or moved ``output_dir``
        somewhere the patterns do not cover (the default list covers ``build/``
        and ``dist/``). Packaging the output directory means shipping the
        previous APK — and anything else the author keeps there.
        """
        from .collector import _is_excluded

        try:
            relative = self.config.output_path.relative_to(self.config.source_path)
        except ValueError:
            return  # output lives outside the sources: nothing can leak
        if not relative.parts:
            return
        # Test a file *inside* the directory: a pattern like "build/**" matches
        # its contents, not the bare directory name that collection never sees.
        if _is_excluded(relative / "pymobile-output-probe", self.config.exclude):
            return
        self.warnings.append(
            f"the build output directory {relative.as_posix()}/ is not excluded from the "
            f"APK; it would ship every file in it (including a previous APK). Add "
            f'"{relative.as_posix()}/**" to exclude, or set output_dir outside the sources.'
        )

    def _check_widget_types(self, sources: SourceSet) -> None:
        """Warn about custom widget types the native renderer cannot draw.

        ``ViewBuilder.java`` has one branch per built-in widget; a class with
        its own ``type_name`` and no branch there draws as a placeholder (or,
        before 0.8, an empty view) on the phone while the desktop preview prints
        ``<BarChart>`` and looks fine. The scan is best-effort (a ``type_name``
        built at runtime is invisible to it) and only names a type nothing has
        declared: ``register_widget_type("BarChart")`` in the app is the way to
        say "the Java branch exists".

        A *native* build goes further — :meth:`_verify_renderers` checks the
        ``classes.dex`` it is about to package and stops if a type is missing.
        """
        from ..core.ui.registry import known_types

        self._custom_widgets = scan_custom_widgets(sources.files)
        found = self._custom_widgets
        unknown = sorted(found.undeclared - known_types())
        if unknown:
            names = ", ".join(repr(name) for name in unknown)
            self.warnings.append(
                f"custom widget type(s) with no renderer: {names} — on Android a node "
                "whose type has no branch in ViewBuilder.java is drawn as a placeholder, "
                "not as your widget. Easiest: `pymobile widget add "
                f"{unknown[0]}` creates a Python widget and a project-local Java renderer. "
                "The older `pymobile widget-java "
                f"{unknown[0]}` command still prints a manual ViewBuilder.java guide. "
                "Declare custom renderers with register_widget_type(<name>)."
            )
        if self.native and found.preview_only:
            names = ", ".join(repr(name) for name in sorted(found.preview_only))
            self.warnings.append(
                f"widget type(s) declared preview-only (android=False): {names} — the "
                "desktop and browser previews draw them, the phone does not"
            )

    def _verify_renderers(self, dex: Path) -> None:
        """Stop a native build whose ``classes.dex`` cannot draw the app's widgets.

        A project renderer can be an explicit legacy branch in ViewBuilder.java
        or a ``<Type>Renderer`` implementation in the project's Java overlay.
        The dex that is about to be packaged is asked directly, so a stale
        prebuilt launcher, a missing class and a typo in the renderer name are
        all caught before the APK is signed.
        """
        wanted = self._custom_widgets.android
        if not wanted:
            return
        data = dex.read_bytes()
        missing = sorted(
            name
            for name in wanted
            if not dex_has_case(data, name)
            and not dex_has_class(data, f"org.pymobile.app.widgets.{name}Renderer")
        )
        if not missing:
            return
        names = ", ".join(repr(name) for name in missing)
        first = missing[0]
        raise MissingRendererError(
            f"no Android renderer for the custom widget type(s) {names}: the classes.dex "
            "that would go into this APK has no branch for them, so on the phone they "
            "would be drawn as a placeholder instead of your widget",
            hint=(
                f"Recommended: `pymobile widget add {first}` creates a Python class and "
                "project-local java/ renderer; the next normal `pymobile build --native` "
                "compiles it automatically, no environment switch or NDK. The legacy "
                f"`pymobile widget-java {first}` guide is still available for a manual "
                "ViewBuilder.java branch. You can also compose from existing widgets or "
                f"mark it preview-only: register_widget_type({first!r}, android=False)."
            ),
        )

    def _collect(self) -> SourceSet:
        """Gather the files that go into the APK.

        The packaged extension set is the collector's default plus whatever
        ``asset_suffixes`` adds, and the files that are still left out are
        reported: an app that reads ``assets/data.csv`` shipped an APK without
        it and only found out on the device.
        """
        sources = collect_sources(
            self.config.source_path,
            self.config.entrypoint_path,
            exclude=self.config.exclude,
            include_suffixes=DEFAULT_ASSET_SUFFIXES | {
                str(suffix).lower() for suffix in self.config.asset_suffixes
            },
        )
        self._warn_about_ignored_assets(sources)
        return sources

    def _warn_about_ignored_assets(self, sources: SourceSet) -> None:
        """Name the extensions found in the sources that did not get packaged.

        Files matching ``exclude`` (the defaults plus the project's own
        patterns) are skipped: a ``README.md`` the project deliberately keeps
        out of the APK is not a problem to report. Suffixes are compared
        against the allowed set (defaults + ``asset_suffixes``), not against
        what happened to be collected.
        """
        from .collector import _is_excluded

        allowed_suffixes = DEFAULT_ASSET_SUFFIXES | {
            str(suffix).lower() for suffix in self.config.asset_suffixes
        }
        ignored: dict[str, int] = {}
        secret_packaged: list[str] = []
        for path in self.config.source_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.config.source_path)
            if any(part in _IGNORED_ASSET_DIRS for part in relative.parts):
                continue
            if _is_excluded(relative, self.config.exclude):
                continue
            suffix = path.suffix.lower()
            if suffix and suffix not in allowed_suffixes:
                ignored[suffix] = ignored.get(suffix, 0) + 1
            # П-03: warn when a secret-like file IS packaged (not excluded).
            if (
                path.name in SECRET_LIKE_BASENAMES
                or suffix in SECRET_LIKE_SUFFIXES
            ):
                secret_packaged.append(relative.as_posix())
        if ignored:
            listed = ", ".join(f"{suffix} ({count})" for suffix, count in sorted(ignored.items()))
            self.warnings.append(
                f"files under {self.config.source_path.name}/ were NOT packaged: {listed}. "
                "Add the extensions you need with `asset_suffixes = ['…']` in "
                "pymobile.toml (read them on device through the packaged app dir)."
            )
        if secret_packaged:
            listed = ", ".join(sorted(secret_packaged)[:5])
            more = "" if len(secret_packaged) <= 5 else f" … (+{len(secret_packaged) - 5} more)"
            self.warnings.append(
                f"secret-like files will be PACKAGED into the APK: {listed}{more}. "
                "APKs are public ZIP archives — anything inside can be extracted. "
                "Move them out of the source directory, add them to `exclude`, or read "
                "secrets from ~/.pymobile/ or environment variables at runtime."
            )

    def _check_python_syntax(self, sources: SourceSet) -> None:
        """П-06: fail the build on a syntax error instead of shipping broken code.

        ``py_compile`` used to emit a warning and continue, producing an APK
        that installs but crashes at launch with ImportError. A plain
        ``ast.parse`` catches syntax errors in milliseconds with filename and
        line number, and respects a UTF-8 BOM (utf-8-sig) the way CPython does.
        """
        problems: list[str] = []
        for path in sources.files:
            if path.suffix != ".py":
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                problems.append(f"{path.relative_to(sources.root).as_posix()}: {exc}")
                continue
            try:
                ast.parse(data)
            except SyntaxError as exc:
                rel = path.relative_to(sources.root).as_posix()
                line = exc.lineno or 0
                # Show the offending line so the user does not have to open
                # the file to see what broke.
                snippet = ""
                try:
                    text = data.decode("utf-8-sig", errors="replace").splitlines()
                    if 0 < line <= len(text):
                        snippet = "\n    " + text[line - 1].strip()
                except Exception:  # pragma: no cover - best effort
                    pass
                problems.append(f"{rel}:{line}: {exc.msg}{snippet}")
        if problems:
            head = "\n  - ".join(problems[:10])
            more = "" if len(problems) <= 10 else f"\n  … (+{len(problems) - 10} more)"
            raise PyMobileError(
                f"Python syntax error(s) prevent this APK from running:\n  - {head}{more}",
                hint="Fix the syntax errors above, then rebuild.",
            )

    def _compile_sources(self, sources: SourceSet, workdir: Path) -> list[tuple[str, Path]]:
        """Copy sources into the work dir, optionally as bytecode only.

        Shipping ``.pyc`` instead of ``.py`` cuts both APK size and app start
        time, since the device never has to compile at runtime.
        """
        staged = workdir / "app"
        staged.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[str, Path]] = []

        for absolute in sources.files:
            relative = absolute.relative_to(sources.root)
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(absolute, target)
            entries.append((relative.as_posix(), target))

        # A native build runs CPython 3.14 on the device. .pyc files are
        # version-locked, so bytecode can only be produced by a 3.14 host;
        # anywhere else the option is ignored — loudly, not silently.
        if not self.config.optimize:
            return entries
        if self.native and sys.version_info[:2] != DEVICE_PYTHON:
            self.warnings.append(
                "optimize = true was ignored: the device runs Python "
                f"{DEVICE_PYTHON[0]}.{DEVICE_PYTHON[1]} and bytecode is version-specific, "
                f"but this build runs on {sys.version_info[0]}.{sys.version_info[1]}; "
                "shipping .py sources instead"
            )
            return entries

        optimize_level = 2 if self.config.strip_debug else 1
        for name, path in entries:
            if path.suffix != ".py":
                continue
            try:
                py_compile.compile(
                    str(path),
                    cfile=str(path.with_suffix(".pyc")),
                    dfile=f"app/{name}",
                    doraise=True,
                    optimize=optimize_level,
                    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
                )
            except py_compile.PyCompileError as exc:
                _log.debug("byte-compilation of %s failed: %s", name, exc)

        compiled: list[tuple[str, Path]] = []
        for name, path in entries:
            if path.suffix != ".py":
                compiled.append((name, path))
                continue
            bytecode = path.with_suffix(".pyc")
            if bytecode.exists():
                compiled.append((f"{name}c", bytecode))
            else:  # syntax error: keep the source so the failure is visible
                self.warnings.append(f"{name} could not be byte-compiled; shipping source")
                compiled.append((name, path))
        return compiled

    def _icons(self, workdir: Path) -> IconSet:
        """Generate launcher icons (custom or default)."""
        return prepare_icons(self.config.icon_path, workdir / "res")

    def _manifest(self) -> str:
        """Render AndroidManifest.xml."""
        return build_manifest(self.config)

    def _package(
        self,
        output: Path,
        manifest: str,
        entries: list[tuple[str, Path]],
        icons: IconSet,
        sources: SourceSet,
    ) -> PackageResult:
        """Write the final archive."""
        resources = {
            f"res/mipmap-{density}/icon.png": path for density, path in icons.files.items()
        }
        entrypoint = sources.entrypoint.relative_to(sources.root).as_posix()
        packaged = {name for name, _ in entries}
        metadata = (
            f"name={self.config.name}\n"
            f"package={self.config.package}\n"
            f"version={self.config.version}\n"
            f"entrypoint={entrypoint}\n"
            f"entrypoint_type={'pyc' if f'{entrypoint}c' in packaged else 'py'}\n"
            f"optimize={int(self.config.optimize)}\n"
        )
        packager = ApkPackager(compress=True)
        return packager.build(
            output,
            manifest=manifest,
            sources=entries,
            resources=resources,
            extra={"assets/pymobile.properties": metadata.encode("utf-8")},
        )

    # -- entry point -------------------------------------------------------
    def run(self) -> BuildResult:
        """Execute every stage and return the result."""
        started = time.perf_counter()
        self.warnings.clear()
        self._timings.clear()

        self._stage("validate", self._validate)
        self._warn_unexcluded_output()
        sources: SourceSet = self._stage("collect", self._collect)
        # П-06: fail fast on Python syntax errors rather than shipping an
        # APK that crashes at launch.
        self._stage("syntax", lambda: self._check_python_syntax(sources))
        self._check_requested_permissions(sources)
        self._check_widget_types(sources)
        self._check_notifications(sources)

        output_dir = self.config.output_path
        output_dir.mkdir(parents=True, exist_ok=True)
        apk_path = output_dir / self.config.apk_name

        cache = BuildCache(output_dir)
        fingerprint = self._fingerprint(sources)
        if self.use_cache:
            hit = cache.entry(fingerprint)
            if hit is not None and self._cache_hit_is_usable(hit):
                cached = Path(hit["artifact"])
                duration = time.perf_counter() - started
                _log.info("no changes detected; reusing %s", cached.name)
                mode = hit.get("mode", self.build_mode())
                return BuildResult(
                    apk=cached,
                    size=cached.stat().st_size,
                    entries=0,
                    duration=duration,
                    cached=True,
                    # A reused native APK is still an installable APK; the
                    # result used to take the dataclass default (native=False)
                    # and callers/automation reported it as a preview package.
                    native=mode.startswith("native"),
                    icon_is_default=hit.get("icon", "1") == "1",
                    timings=list(self._timings),
                    warnings=list(self.warnings),
                )

        with tempfile.TemporaryDirectory(prefix="pymobile-build-") as temporary:
            workdir = Path(temporary)
            entries = self._stage("compile", lambda: self._compile_sources(sources, workdir))
            icons: IconSet = self._stage("icons", lambda: self._icons(workdir))

            if self.native:
                result = self._run_native(
                    workdir, sources, entries, icons, apk_path, started, fingerprint
                )
                cache.save(
                    fingerprint,
                    result.apk,
                    mode=self.build_mode(),
                    signer=self.signer_identity(),
                    icon="1" if result.icon_is_default else "0",
                )
                return result

            manifest: str = self._stage("manifest", self._manifest)
            package: PackageResult = self._stage(
                "package", lambda: self._package(apk_path, manifest, entries, icons, sources)
            )

        cache.save(
            fingerprint,
            package.path,
            mode=self.build_mode(),
            signer=self.signer_identity(),
            icon="1" if icons.is_default else "0",
        )
        duration = time.perf_counter() - started
        return BuildResult(
            apk=package.path,
            size=package.size,
            entries=package.entries,
            duration=duration,
            cached=False,
            icon_is_default=icons.is_default,
            timings=list(self._timings),
            warnings=list(self.warnings),
        )

    def _cache_hit_is_usable(self, entry: dict[str, str]) -> bool:
        """Whether a cache entry really describes the artifact this build wants.

        Two things are verified rather than assumed: the entry was produced in
        the same mode, and — when both describe one — the signing identity
        matches. An older cache file (written before the signer was recorded)
        simply fails the check and the build runs for real, which is the safe
        direction.
        """
        recorded_mode = entry.get("mode")
        if recorded_mode is not None and recorded_mode != self.build_mode():
            _log.debug(
                "cache entry is for mode %r, this build is %r; rebuilding",
                recorded_mode,
                self.build_mode(),
            )
            return False
        recorded_signer = entry.get("signer")
        if recorded_signer is not None and recorded_signer != self.signer_identity():
            _log.warning(
                "ignoring the cached APK: it was signed as %s, this build asks for %s",
                recorded_signer,
                self.signer_identity(),
            )
            return False
        return True

    def _run_native(
        self,
        workdir: Path,
        sources: SourceSet,
        entries: list[tuple[str, Path]],
        icons: IconSet,
        apk_path: Path,
        started: float,
        fingerprint: str = "",
    ) -> BuildResult:
        """Build a real, installable APK using the Android toolchain."""
        toolchain = self._stage("toolchain", find_toolchain)
        # The NDK is optional: the prebuilt JNI bridge works for both ABIs.
        # A project-local Java renderer additionally needs javac and d8; the
        # source is compiled automatically and never falls back to a stale dex.
        project_java = self.config.root / "java"
        has_project_java = project_java.is_dir() and any(project_java.rglob("*.java"))
        toolchain.verify(
            require_ndk=False,
            require_javac=has_project_java or os.environ.get("PYMOBILE_BUILD_JAVA") != "0",
        )

        runtime = self._stage("runtime", lambda: ensure_runtime(self.config.abis[0]))
        backend = NativeBackend(
            self.config,
            toolchain,
            runtime,
            # The release-signing options used to stop here: `--keystore`
            # was accepted and then silently ignored (debug-signed APK).
            keystore=self.keystore,
            keystore_password=self.keystore_password,
            key_alias=self.key_alias,
            key_password=self.key_password,
            abi=self.config.abis[0],
        )
        if len(self.config.abis) > 1:
            # The native backend packages only the first ABI; surfacing this
            # beats silently shipping an APK that ignores the rest.
            self.warnings.append(
                "native builds package only the first requested ABI "
                f"({self.config.abis[0]} of {self.config.abis}); build one ABI at a time"
            )

        # What the launcher has to run: recorded in assets/pymobile.properties
        # and, for the prebuilt launcher, in a main.py bootstrap as well.
        entrypoint_name = sources.entrypoint.relative_to(sources.root).as_posix()
        packaged_names = {name for name, _ in entries}
        entrypoint_type = "pyc" if f"{entrypoint_name}c" in packaged_names else "py"
        backend.set_entrypoint(entrypoint_name, entrypoint_type)
        # The launcher re-extracts its bundled Python when this changes, so an
        # ``edit → build → adb install -r`` that keeps versionCode = 1 no
        # longer leaves the previous build's app code on disk (PM-29).
        backend.set_payload_digest(fingerprint)

        native_dir = self._stage("jni", lambda: backend.compile_jni(workdir))
        dex = self._stage("dex", lambda: backend.compile_java(workdir))
        self._verify_renderers(dex)
        base = self._stage("resources", lambda: backend.link_resources(workdir, icons.files))
        assets = self._stage("assets", lambda: backend.collect_assets(entries))
        signed = self._stage(
            "sign",
            lambda: backend.package(base, dex, native_dir, assets, apk_path, workdir),
        )
        verified = self._stage("verify", lambda: backend.verify(signed))
        if not verified:
            self.warnings.append("apksigner could not verify the signature")
        self.warnings.extend(backend.warnings)

        duration = time.perf_counter() - started
        return BuildResult(
            apk=signed,
            size=signed.stat().st_size,
            entries=len(assets) + 2,
            duration=duration,
            cached=False,
            native=True,
            icon_is_default=icons.is_default,
            timings=list(self._timings),
            warnings=list(self.warnings),
        )

    def signer_identity(self) -> str:
        """A stable description of the key this build will be signed with.

        It belongs in the fingerprint, and in the cache metadata: the
        fingerprint used to cover sources, config and framework files but
        *not* the signer, so requesting a release keystore after a debug build
        hit the cache and produced an APK signed with the debug certificate —
        an artifact that lies about its signing identity, which breaks the
        update/release flow. The keystore's content hash is used (never a
        password), so replacing the key file behind the same path also
        invalidates the cache.
        """
        if self.keystore is None:
            return f"debug:{self.config.package}"
        path = Path(self.keystore)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            digest = "unreadable"
        return f"release:{path.name}:{self.key_alias or DEBUG_KEY_ALIAS}:{digest}"

    def build_mode(self) -> str:
        """The artifact kind, recorded with the cache entry and fingerprint.

        Java source builds are the default so framework fixes reach the APK;
        ``PYMOBILE_BUILD_JAVA=0`` explicitly selects the prebuilt dex unless a
        project overlay is present. ``PYMOBILE_BUILD_JNI=1`` opts into building
        the bridge from source. These artifact choices belong in the cache key.
        """
        mode = "native" if self.native else "structural"
        if os.environ.get("PYMOBILE_BUILD_JNI") == "1":
            mode += "+jni"
        project_java = self.config.root / "java"
        has_project_java = project_java.is_dir() and any(project_java.rglob("*.java"))
        if self.native and (
            has_project_java or os.environ.get("PYMOBILE_BUILD_JAVA") != "0"
        ):
            mode += "+java"
        elif self.native:
            mode += "+prebuilt-java"
        return mode

    def _fingerprint(self, sources: SourceSet) -> str:
        """Hash of every input that can change the artifact.

        The configuration part uses blake2b rather than the built-in ``hash``:
        string hashing is salted per interpreter process, which would make the
        fingerprint differ on every run and defeat the cache entirely.

        The build **mode** is part of the key as well. A structural package and
        a native APK are produced from identical sources and configuration, but
        only one of them installs on a device; without the mode in the key,
        ``build`` followed by ``build --native`` would report "up to date" and
        hand back the structural artifact.

        So is the **framework** that goes into the APK — its version, the
        launcher dex, the JNI bridge and their sources — and the
        ``PYMOBILE_BUILD_JNI`` switch. Otherwise upgrading pymobile (a new
        renderer) kept answering "up to date" with an APK built by the old one.
        """
        paths = list(sources.files) + _framework_inputs()
        project_java = self.config.root / "java"
        if project_java.is_dir():
            paths.extend(path for path in project_java.rglob("*.java") if path.is_file())
        icon = self.config.icon_path
        if icon is not None and icon.exists():
            paths.append(icon)
        config_digest = hashlib.blake2b(
            repr(sorted(self.config.to_dict().items())).encode("utf-8"), digest_size=8
        ).hexdigest()
        from .. import __version__

        return (
            f"{self.build_mode()}:{__version__}:{fingerprint_files(paths)}:"
            f"{config_digest}:{self.signer_identity()}"
        )


def _framework_inputs() -> list[Path]:
    """Every framework file a build packages into the APK.

    The Android pieces (sources, JNI, launcher dex, bridge), the default
    launcher icon, **and** the Python framework that ships as
    ``assets/app/pymobile``: a fix in ``pymobile/core`` (a renderer change, a
    bug fix) used to leave the fingerprint untouched, so ``build`` answered
    "up to date" and reused an APK that did not contain the fix. The bundled
    icon is in the same position — replacing it changed nothing in the key, so
    a project without its own icon kept the old one from the cache. For a Java
    flag/fork that only changes the dex, the difference in dex SHA is enough to
    prove stale output.
    """
    from ..resources import resource_path

    try:
        android = resource_path("android")
        default_icon = resource_path("icons", "default_icon.png")
    except PyMobileError:  # an incomplete install fails later, with a hint
        android_files: list[Path] = []
        icon_files: list[Path] = []
    else:
        android_files = [
            path
            for pattern in ("java/*.java", "jni/*.c", "prebuilt/*/*")
            for path in android.glob(pattern)
            if path.is_file()
        ]
        icon_files = [default_icon] if default_icon.is_file() else []
    return sorted({*android_files, *icon_files, *framework_asset_files()})


def build_apk(
    config: ProjectConfig, *, use_cache: bool = True, native: bool = False
) -> BuildResult:
    """Build an APK from a project configuration.

    With ``native=True`` the full Android toolchain is used and the result is a
    signed, installable APK; otherwise a fast structural package is produced.
    """
    return BuildPipeline(config, use_cache=use_cache, native=native).run()
