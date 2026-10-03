"""New project generation.

Renders the packaged templates into a directory. The substitution syntax is
intentionally trivial (``{{key}}``) — a template engine would be another
dependency for something that needs three replacements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError
from ..log import get_logger
from ..resources import read_template

__all__ = ["create_project", "ScaffoldResult", "slugify", "default_package"]

_log = get_logger("compiler.scaffold")

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: template file -> generated file
_FILES = {
    "main.py.template": "main.py",
    "pymobile.toml.template": "pymobile.toml",
    "README.md.template": "README.md",
    "gitignore.template": ".gitignore",
}


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    """Files created for a new project."""

    directory: Path
    files: tuple[Path, ...]


#: Ukrainian/Russian letters so non-Latin app names do not all collapse to "app".
_CYRILLIC = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "h",
        "ґ": "g",
        "д": "d",
        "е": "e",
        "є": "ie",
        "ж": "zh",
        "з": "z",
        "и": "y",
        "і": "i",
        "ї": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ь": "",
        "ю": "iu",
        "я": "ia",
        "ъ": "",
        "ы": "y",
        "э": "e",
    }
)


def slugify(name: str) -> str:
    """Turn a display name into a lowercase identifier fragment.

    ASCII names keep their letters and digits only (``My Cool App!`` →
    ``mycoolapp``). Names written in another script are transliterated so
    ``Скарбничка`` becomes ``skarbnychka`` instead of the generic ``app``.

    The step order matters: Cyrillic is transliterated *before* NFKD, because
    NFKD decomposes ``й`` into ``и`` + combining breve and the table would
    then read it as ``и`` (``y``) instead of ``й`` (``i``). And a slug that
    would start with a digit gets an ``app`` prefix, because a Java package
    segment must begin with a letter (``Мій додаток 2`` → ``miidodatok2``,
    ``2048`` → ``app2048``).
    """
    import unicodedata

    # Transliterate first, before NFKD can decompose «й»/«ї» into
    # base letters + a diacritic mark the table would misread.
    transliterated = name.lower().translate(_CYRILLIC)
    # Then drop the diacritics from whatever Latin is left (é → e).
    ascii_only = (
        unicodedata.normalize("NFKD", transliterated)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "", ascii_only)
    if not slug:
        return "app"
    # A package segment cannot start with a digit (see _PACKAGE_RE in core).
    if slug[0].isdigit():
        return f"app{slug}"
    return slug


def default_package(name: str) -> str:
    """Derive a reverse-DNS package id from an application name."""
    return f"org.pymobile.{slugify(name)}"


def render(template: str, values: dict[str, str]) -> str:
    """Replace ``{{key}}`` placeholders, leaving unknown ones untouched."""
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def create_project(
    directory: str | Path,
    name: str,
    *,
    package: str | None = None,
    force: bool = False,
) -> ScaffoldResult:
    """Create a runnable PyMobile project in ``directory``."""
    target = Path(directory).resolve()
    if target.exists() and any(target.iterdir()) and not force:
        raise ConfigError(
            f"Directory is not empty: {target}",
            hint="Pass --force to write into it anyway, or choose another path.",
        )

    values = {"name": name, "package": package or default_package(name)}
    target.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for template_name, output_name in _FILES.items():
        content = render(read_template(template_name), values)
        path = target / output_name
        path.write_text(content, encoding="utf-8")
        created.append(path)

    assets = target / "assets"
    assets.mkdir(exist_ok=True)
    (assets / ".gitkeep").write_text("", encoding="utf-8")

    _log.debug("scaffolded %d files in %s", len(created), target)
    return ScaffoldResult(directory=target, files=tuple(created))
