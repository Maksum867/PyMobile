"""Project configuration.

One dataclass describes an application completely: identity, entry point,
permissions, icon and build knobs. It is loaded from ``pymobile.toml`` (or the
``[tool.pymobile]`` table of a ``pyproject.toml``) and validated eagerly so the
build fails with a clear message instead of a broken APK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# tomllib is standard from Python 3.11 on; 3.10 gets the same parser from the
# tomli backport, which is where tomllib came from in the first place.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

from ..errors import ConfigError

__all__ = [
    "ProjectConfig",
    "load_config",
    "CONFIG_FILENAME",
    "RUNTIME_MIN_SDK",
    "DEFAULT_EXCLUDE",
]

CONFIG_FILENAME = "pymobile.toml"

_PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-.][0-9A-Za-z.]+)?$")
_ORIENTATIONS = ("portrait", "landscape", "sensor", "user")
_ABI_CHOICES = ("arm64-v8a", "x86_64")  # only ABIs the packaged runtime ships

#: Oldest Android the embedded interpreter runs on. The python.org Android
#: builds of CPython 3.14 target API 24 (Android 7.0): ``libpython3.14.so``
#: imports ``preadv``, ``pwritev`` and ``lockf``, which older bionic lacks, so
#: on Android 5-6 the app died at launch. An APK never declares less than this.
RUNTIME_MIN_SDK = 24

#: Patterns that are always excluded from the APK. ``exclude`` in
#: ``pymobile.toml`` **adds** to these instead of replacing them: a project
#: that lists one pattern of its own used to lose ``build/**`` and ship the
#: previous APK (or anything else sitting in the output directory) inside the
#: new one. ``*.md`` is documentation, not app data — it keeps the
#: scaffolded ``README.md`` out of the APK so a fresh project builds with no
#: "not packaged" warning.
#:
#: Secret-like filenames are excluded by default (П-03): shipping keystores,
#: private keys or env files inside an APK leaks credentials the moment the
#: APK is unzipped.  ``google-services.json`` is intentionally **not** here —
#: it is a Firebase config that must ship; projects that name a file
#: ``secrets.json``/``credentials.json`` intentionally must list them
#: explicitly via ``asset_suffixes``/removing the pattern.
DEFAULT_EXCLUDE: tuple[str, ...] = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "*.md",
    "**/tests/**",
    "tests/**",
    "**/test_*.py",
    ".git/**",
    ".github/**",
    ".idea/**",
    ".vscode/**",
    ".venv/**",
    "venv/**",
    "build/**",
    "dist/**",
    # secret-like files (П-03)
    ".env",
    ".env.*",
    "*.jks",
    "*.keystore",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
    "secrets/**",
    "secrets.json",
    "secrets.*.json",
    "credentials.json",
    "credentials.*.json",
)

#: File basenames that look like secrets (used to emit a warning when a
#: matching file is **not** covered by an exclude pattern, so the user notices
#: before shipping).  П-03.
SECRET_LIKE_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "secrets.toml",
        "credentials.json",
        "credentials.yaml",
        "client_secret.json",
        "service_account.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "server.pem",
        "server.key",
        "release.jks",
        "debug.keystore",
    }
)

#: Extensions that strongly suggest a secret file, used to warn when they
#: would be packaged.
SECRET_LIKE_SUFFIXES: frozenset[str] = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
)


@dataclass(slots=True)
class ProjectConfig:
    """Everything the compiler needs to build an APK."""

    # -- identity ----------------------------------------------------------
    name: str = "PyMobile App"
    package: str = "org.pymobile.app"
    version: str = "0.1.0"
    version_code: int = 1

    # -- entry point -------------------------------------------------------
    entrypoint: str = "main.py"
    source_dir: str = "."

    # -- android -----------------------------------------------------------
    #: Projects created before 0.8.0 say ``min_sdk = 21``; that still loads,
    #: but the APK declares :attr:`effective_min_sdk` and the build warns.
    min_sdk: int = RUNTIME_MIN_SDK
    target_sdk: int = 35
    orientation: str = "portrait"
    permissions: list[str] = field(default_factory=lambda: ["android.permission.INTERNET"])
    icon: str | None = None
    #: Let Android back up the app's private data (cloud backup, adb backup).
    #: Off by default: the store often holds tokens and personal data.
    allow_backup: bool = False

    # -- build -------------------------------------------------------------
    abis: list[str] = field(default_factory=lambda: ["arm64-v8a"])
    output_dir: str = "build"
    optimize: bool = False
    strip_debug: bool = True
    #: Drop desktop-only stdlib packages (pydoc, unittest, venv, ...) — ~1.7 MB.
    minimal_stdlib: bool = False
    #: Leave OpenSSL, ssl.py and the CA bundle out — ~4 MB, no HTTPS.
    no_ssl: bool = False
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    #: Extra asset extensions to package on top of the defaults (``.py``,
    #: ``.json``, ``.txt``, ``.toml``, images, fonts). A ``data.csv`` or a
    #: seed ``.db`` in the source directory was silently left out of the APK:
    #: the collector only promised "common types", and nothing said so. List
    #: them here — ``asset_suffixes = [".csv", ".db"]`` — and they ship.
    asset_suffixes: list[str] = field(default_factory=list)
    #: Replace :data:`DEFAULT_EXCLUDE` instead of adding to it. Only for
    #: projects that know exactly what they are doing — most users want the
    #: defaults (``build/**``, ``.git/**``, tests …) kept.
    exclude_only: bool = False

    #: Directory the config was loaded from; all relative paths resolve here.
    root: Path = field(default_factory=Path.cwd)

    @property
    def effective_min_sdk(self) -> int:
        """``min_sdk`` raised to :data:`RUNTIME_MIN_SDK`: what the APK declares."""
        return max(self.min_sdk, RUNTIME_MIN_SDK)

    # -- validation --------------------------------------------------------
    def __post_init__(self) -> None:
        # П-05: coerce path-like scalars to strings early so a Path value
        # (e.g. passed from the API) round-trips cleanly, then validate()
        # runs the type gates before any range/path arithmetic.
        self.root = Path(self.root).resolve()
        if isinstance(self.source_dir, Path):
            self.source_dir = str(self.source_dir)
        if isinstance(self.output_dir, Path):
            self.output_dir = str(self.output_dir)
        # П-04: "no"/"yes"/"true"/"false" strings are a common TOML typo.
        for bool_field in (
            "allow_backup",
            "optimize",
            "strip_debug",
            "minimal_stdlib",
            "no_ssl",
            "exclude_only",
        ):
            value = getattr(self, bool_field)
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("true", "yes", "on", "1"):
                    setattr(self, bool_field, True)
                elif low in ("false", "no", "off", "0", ""):
                    setattr(self, bool_field, False)
                # leave other strings for the type gate in validate()
        # П-01/П-02: normalize list fields defensively.
        for list_field in ("permissions", "abis", "asset_suffixes"):
            value = getattr(self, list_field)
            if value is None:
                setattr(self, list_field, [])
        if self.exclude is None:
            self.exclude = list(DEFAULT_EXCLUDE)
        self.validate()
        self.normalise_exclude()

    def normalise_exclude(self) -> None:
        """Merge ``exclude`` with :data:`DEFAULT_EXCLUDE` (unless ``exclude_only``).

        Both construction paths go through here — the TOML loader and a direct
        ``ProjectConfig(...)`` — so an application cannot end up shipping its
        own output directory by listing a pattern of its own.
        """
        patterns = [str(pattern) for pattern in self.exclude]
        if self.exclude_only:
            self.exclude = patterns
            return
        self.exclude = [*DEFAULT_EXCLUDE, *(p for p in patterns if p not in DEFAULT_EXCLUDE)]

    def validate(self) -> None:
        """Raise :class:`ConfigError` if any field is invalid."""
        # ---- type gates (П-01, П-02, П-04, П-05, П-15) -------------------
        # These come first so a wrong type gives a helpful message instead
        # of a raw ``TypeError`` from a downstream range/path comparison.
        self._check_type("name", self.name, str)
        self._check_type("package", self.package, str)
        self._check_type("version", self.version, str)
        self._check_type("entrypoint", self.entrypoint, str)
        self._check_type("orientation", self.orientation, str)

        for int_field in ("version_code", "min_sdk", "target_sdk"):
            self._check_type(int_field, getattr(self, int_field), int)

        for str_or_none in ("icon",):
            value = getattr(self, str_or_none)
            if value is not None and not isinstance(value, str):
                raise ConfigError(
                    f"`{str_or_none}` must be a string path (or null), got {type(value).__name__}",
                    hint=f'Write e.g. `{str_or_none} = "assets/icon.png"`.',
                )

        for str_field in ("source_dir", "output_dir"):
            value = getattr(self, str_field)
            if not isinstance(value, (str, Path)):
                raise ConfigError(
                    f"`{str_field}` must be a string path, got {type(value).__name__}",
                    hint=f'Write e.g. `{str_field} = "src"`.',
                )

        for bool_field in (
            "allow_backup",
            "optimize",
            "strip_debug",
            "minimal_stdlib",
            "no_ssl",
            "exclude_only",
        ):
            value = getattr(self, bool_field)
            if not isinstance(value, bool):
                raise ConfigError(
                    f"`{bool_field}` must be a boolean (true/false without quotes), "
                    f"got {type(value).__name__}: {value!r}",
                    hint=f"Write `{bool_field} = false` (no quotes) — "
                    f"a quoted string like \"no\" or \"pyc\" is truthy and would "
                    f"silently enable the flag.",
                )

        for list_field in ("permissions", "exclude", "abis", "asset_suffixes"):
            value = getattr(self, list_field)
            if isinstance(value, str):
                raise ConfigError(
                    f"`{list_field}` must be a list, not a string: got {value!r}",
                    hint=f"A string here would be iterated character-by-character. "
                    f"Write a TOML list, e.g. `{list_field} = [\"{value}\"]`.",
                )
            if not isinstance(value, (list, tuple)):
                raise ConfigError(
                    f"`{list_field}` must be a list of strings, got {type(value).__name__}",
                    hint=f"Write `{list_field} = [...]`.",
                )
            for item in value:
                if not isinstance(item, str):
                    raise ConfigError(
                        f"`{list_field}` entries must be strings; "
                        f"got {type(item).__name__}: {item!r}",
                    )

        # ---- value checks -------------------------------------------------
        if not self.name.strip():
            raise ConfigError("`name` must not be empty")
        if not _PACKAGE_RE.match(self.package):
            raise ConfigError(
                f"Invalid package name {self.package!r}",
                hint="Use reverse-DNS with lowercase segments, e.g. com.example.myapp",
            )
        if not _VERSION_RE.match(self.version):
            raise ConfigError(
                f"Invalid version {self.version!r}", hint="Use a numeric version such as 1.0.0"
            )
        if self.version_code < 1:
            raise ConfigError("`version_code` must be >= 1")
        if self.min_sdk < 21:
            raise ConfigError(
                f"`min_sdk` is {self.min_sdk}, but PyMobile requires at least {RUNTIME_MIN_SDK}",
                hint=f"Set min_sdk = {RUNTIME_MIN_SDK}: Android 7.0 is the oldest release "
                "the embedded CPython 3.14 runs on.",
            )
        if self.target_sdk < self.effective_min_sdk:
            raise ConfigError(
                f"`target_sdk` ({self.target_sdk}) must be >= {self.effective_min_sdk}",
                hint=f"The APK declares minSdkVersion {self.effective_min_sdk}; "
                "target_sdk cannot be lower.",
            )
        if self.orientation not in _ORIENTATIONS:
            raise ConfigError(
                f"Invalid orientation {self.orientation!r}",
                hint=f"Choose one of: {', '.join(_ORIENTATIONS)}",
            )

        # Normalise permissions now so every caller (manifest, info --json,
        # warnings) sees the fully-qualified form that actually ends up in
        # the APK. П-23.
        from ..core.api.permissions import normalize as _norm_perm

        self.permissions = [_norm_perm(p) for p in self.permissions]

        for suffix in self.asset_suffixes:
            if not str(suffix).startswith(".") or len(str(suffix)) < 2:
                raise ConfigError(
                    f"Invalid asset suffix {suffix!r}",
                    hint="Write extensions with a leading dot, e.g. asset_suffixes = ['.csv'].",
                )
        unknown_abis = [abi for abi in self.abis if abi not in _ABI_CHOICES]
        if unknown_abis:
            raise ConfigError(
                f"Unsupported ABI(s): {', '.join(unknown_abis)}",
                hint=f"Supported: {', '.join(_ABI_CHOICES)}",
            )
        if not self.abis:
            raise ConfigError("`abis` must list at least one architecture")

    @staticmethod
    def _check_type(field_name: str, value: Any, expected: type) -> None:
        """Reject booleans passed as ints (``isinstance(True, int)`` is True)."""
        if expected is int and isinstance(value, bool):
            raise ConfigError(
                f"`{field_name}` must be an integer, got a boolean ({value!r})",
                hint=f"Write `{field_name} = 1` without quotes.",
            )
        if expected is str and isinstance(value, Path):
            return  # paths are acceptable as strings
        if not isinstance(value, expected):
            raise ConfigError(
                f"`{field_name}` must be {expected.__name__}, "
                f"got {type(value).__name__}: {value!r}",
            )

    # -- derived paths -----------------------------------------------------
    @property
    def source_path(self) -> Path:
        """Absolute path to the application sources."""
        return (self.root / self.source_dir).resolve()

    @property
    def entrypoint_path(self) -> Path:
        """Absolute path to the entry-point module."""
        return (self.source_path / self.entrypoint).resolve()

    @property
    def output_path(self) -> Path:
        """Absolute path to the build output directory."""
        return (self.root / self.output_dir).resolve()

    @property
    def icon_path(self) -> Path | None:
        """Absolute path to the custom icon, or ``None`` to use the default."""
        if not self.icon:
            return None
        return (self.root / self.icon).resolve()

    @property
    def apk_name(self) -> str:
        """File name of the produced APK.

        Names written in a non-Latin script strip to nothing under the ASCII
        filter — and so do names whose Latin residue is digits only,
        ``Нотатки 2`` (com.example.notes) strips to ``2`` — so the last
        package segment is used instead of a generic or digit-only file name:
        both yield ``notes-0.1.0.apk``.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", self.name).strip("-.").lower()
        if not re.search(r"[a-z]", safe):
            safe = self.package.rsplit(".", 1)[-1]
        # An emulator build must not be mistaken for (or overwrite) the phone
        # APK: it is suffixed with its ABI. The arm64 name is unchanged.
        abi = self.abis[0] if self.abis else "arm64-v8a"
        suffix = "" if abi == "arm64-v8a" else f"-{abi}"
        return f"{safe}-{self.version}{suffix}.apk"

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view (paths as strings) for manifests and reports."""
        data: dict[str, Any] = {}
        for spec in fields(self):
            value = getattr(self, spec.name)
            data[spec.name] = str(value) if isinstance(value, Path) else value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, root: Path | None = None) -> ProjectConfig:
        """Build a config from a mapping, rejecting unknown keys."""
        known = {spec.name for spec in fields(cls)} - {"root"}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(
                f"Unknown configuration key(s): {', '.join(sorted(unknown))}",
                hint=f"Valid keys: {', '.join(sorted(known))}",
            )
        payload = dict(data)
        if root is not None:
            payload["root"] = Path(root)
        return cls(**payload)


def load_config(path: str | Path | None = None) -> ProjectConfig:
    """Load configuration from ``pymobile.toml`` or ``pyproject.toml``.

    ``path`` may point at a directory or directly at a TOML file. When it is a
    directory (or omitted) the loader looks for ``pymobile.toml`` first, then
    for a ``[tool.pymobile]`` table in ``pyproject.toml``.
    """
    start = Path(path or Path.cwd()).resolve()
    if start.is_dir():
        candidate = start / CONFIG_FILENAME
        if not candidate.exists():
            pyproject = start / "pyproject.toml"
            if pyproject.exists() and _has_tool_table(pyproject):
                candidate = pyproject
            else:
                raise ConfigError(
                    f"No {CONFIG_FILENAME} found in {start}",
                    hint="Run `pymobile init` to create a new project.",
                )
    else:
        candidate = start
        if not candidate.exists():
            raise ConfigError(f"Configuration file not found: {candidate}")

    try:
        raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{candidate.name} is not valid TOML: {exc}",
            hint="Check for missing quotes or brackets.",
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {candidate}: {exc}") from exc

    if candidate.name == "pyproject.toml":
        section = raw.get("tool", {}).get("pymobile")
        if not isinstance(section, dict):
            raise ConfigError(f"No [tool.pymobile] table in {candidate}")
    else:
        section = raw.get("app", raw)
        if not isinstance(section, dict):
            raise ConfigError(f"The [app] table in {candidate} must be a table")

    return ProjectConfig.from_dict(dict(section), root=candidate.parent)


def _has_tool_table(pyproject: Path) -> bool:
    """Whether a pyproject file contains a ``[tool.pymobile]`` table."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(data.get("tool", {}).get("pymobile"), dict)
