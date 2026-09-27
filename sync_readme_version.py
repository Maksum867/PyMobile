#!/usr/bin/env python3
"""Keep the version pins in README.md in sync with pymobile/__init__.py.

The README names the current version twice:

- the alpha-stage warning — ``pip install pymobile-framework==<version>``
  (pin an exact version),
- the diagnostics example — ``"framework_version": "<version>"`` in the
  ``get_diagnostics()`` output.

Neither is edited by hand on a release: the script reads ``__version__``
from ``pymobile/__init__.py`` (the value ``get_diagnostics()`` actually
reports) and rewrites both references::

    python sync_readme_version.py            # update README.md
    python sync_readme_version.py --check    # exit 1 when out of sync (CI)

Run it after bumping ``__version__`` (and the version in ``pyproject.toml``)
so the committed README, the GitHub page and the PyPI description all show
the same version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"

PIN = re.compile(r"pymobile-framework==[\d.]+")
DIAG = re.compile(r'"framework_version": "[\d.]+"')


def current_version() -> str:
    """The package's runtime version, from pymobile/__init__.py."""
    sys.path.insert(0, str(ROOT))
    from pymobile import __version__

    return __version__


def main() -> int:
    check = "--check" in sys.argv[1:]
    version = current_version()
    text = README.read_text(encoding="utf-8")
    updated, n_pins = PIN.subn(f"pymobile-framework=={version}", text)
    updated, n_diag = DIAG.subn(f'"framework_version": "{version}"', updated)
    found = n_pins + n_diag
    if found < 2:
        print(
            f"error: expected 2 version references in README.md "
            f"(pin + diagnostics example), found {found}",
            file=sys.stderr,
        )
        return 1
    if updated == text:
        print(f"README.md is in sync ({version})")
        return 0
    if check:
        print(
            f"error: README.md is out of sync with pymobile/__init__.py "
            f"(__version__ = {version})\nrun: python sync_readme_version.py",
            file=sys.stderr,
        )
        return 1
    README.write_text(updated, encoding="utf-8")
    print(f"README.md updated to {version} ({found} reference(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
