"""JSON Schema for ``pymobile.toml``, generated from :class:`ProjectConfig`.

Editors with TOML support (Even Better TOML, Taplo, PyCharm) read the schema
and offer completion, type checks and hover text for every ``[app]`` key. It
is generated from the dataclass rather than written by hand, so a new config
field appears in the schema the next time it is generated, with no second list
to keep in step. Descriptions come from the ``#:`` comments above each field.

Python code is not covered by a JSON Schema: ``main.py`` gets its completion
and type checks from the typed package (``py.typed``) that editors already read.
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

from ..core.config import _ABI_CHOICES, ProjectConfig

__all__ = ["SCHEMA_FILENAME", "build_config_schema", "write_config_schema"]

#: Name the schema gets inside a project; ``init`` writes it next to pymobile.toml.
SCHEMA_FILENAME = "pymobile.schema.json"

_FIELD_LINE = re.compile(r"^    ([A-Za-z_]\w*)\s*:")
_SKIPPED_FIELDS = frozenset({"root"})  # set from the file location, not written by users


def _field_docs() -> dict[str, str]:
    """``#:`` comments above each field of :class:`ProjectConfig`, keyed by field name."""
    try:
        source = inspect.getsource(ProjectConfig)
    except (OSError, TypeError):  # source not shipped: schema without descriptions
        return {}
    docs: dict[str, str] = {}
    pending: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#:"):
            pending.append(stripped[2:].strip())
            continue
        match = _FIELD_LINE.match(line)
        if match and pending:
            docs[match.group(1)] = " ".join(pending)
        pending = []
    return docs


def _schema_for(annotation: Any) -> dict[str, Any]:
    """JSON Schema for one dataclass field annotation."""
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is list:
        return {"type": "array", "items": _schema_for(args[0]) if args else {}}
    if origin is typing.Union or origin is types.UnionType:
        options = [arg for arg in args if arg is not type(None)]
        if len(options) == 1 and len(options) != len(args):
            return {"anyOf": [_schema_for(options[0]), {"type": "null"}]}
    return {}  # anything else is accepted as written


def _default_of(spec: Any) -> Any:
    if spec.default is not MISSING:
        return spec.default
    if spec.default_factory is not MISSING:
        return spec.default_factory()
    return None


def build_config_schema() -> dict[str, Any]:
    """Return the schema as a dict; :func:`write_config_schema` stores it as JSON."""
    hints = typing.get_type_hints(ProjectConfig)
    docs = _field_docs()
    properties: dict[str, Any] = {}
    for spec in fields(ProjectConfig):
        if spec.name in _SKIPPED_FIELDS:
            continue
        entry = _schema_for(hints[spec.name])
        if spec.name in docs:
            entry["description"] = docs[spec.name]
        if spec.name == "abis":
            entry["items"] = {"enum": list(_ABI_CHOICES)}
        default = _default_of(spec)
        if default is not None:
            entry["default"] = str(default) if isinstance(default, Path) else default
        properties[spec.name] = entry

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "pymobile.toml",
        "description": "PyMobile project configuration. Keys belong under [app].",
        "type": "object",
        "properties": {
            "app": {
                "type": "object",
                "description": "Application settings.",
                "properties": properties,
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }


def write_config_schema(path: str | Path) -> Path:
    """Write the schema to ``path`` as UTF-8 JSON and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_config_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target
