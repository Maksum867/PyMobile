"""Custom widget discovery, project scaffolding, and renderer checks.

A custom widget has a Python definition and, when it is native, a Java
renderer in ``classes.dex``. ``pymobile widget add`` scaffolds the Python class
and a project-local ``WidgetRenderer`` implementation under ``java/``; native
builds compile that overlay without touching the installed package. The module
also scans widget declarations, verifies the resulting dex, and retains
:func:`java_branch` as a guide for framework contributors who edit the shared
``ViewBuilder.java``.
"""

from __future__ import annotations

import ast
import keyword
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.ui.registry import widget_types
from ..errors import ConfigError

__all__ = [
    "CustomWidgets",
    "JavaBranch",
    "MissingRendererError",
    "WidgetProp",
    "dex_has_case",
    "dex_has_class",
    "java_branch",
    "parse_props",
    "scaffold_widget_sources",
    "scan_custom_widgets",
]


class MissingRendererError(ConfigError):
    """The ``classes.dex`` of a native build has no renderer for a widget type.

    Its own class so the command line can tell it from a missing toolchain: the
    advice for this one is about the widget, not about installing the SDK.
    """


# --------------------------------------------------------------------------
# finding a project's custom widgets
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CustomWidgets:
    """Custom widget types a project's sources define or declare."""

    #: ``type_name`` of every widget class the project defines (built-ins excluded).
    types: frozenset[str]
    #: Names passed to ``register_widget_type(...)``, whatever their settings.
    declared: frozenset[str]
    #: The declared names that say ``android=False`` (a preview-only widget).
    preview_only: frozenset[str]

    @property
    def undeclared(self) -> frozenset[str]:
        """Widget types the project defines without a ``register_widget_type``."""
        return self.types - self.declared

    @property
    def android(self) -> frozenset[str]:
        """Types the phone is expected to draw: defined or declared, not preview-only."""
        return (self.types | self.declared) - self.preview_only - widget_types()


def _builtin_widget_classes() -> frozenset[str]:
    """Names of the framework's own widget classes (what a custom one derives from)."""
    import pymobile

    from ..core.ui.widget import Widget

    names = {"Widget", "Container"}
    for name in pymobile.__all__:
        value = getattr(pymobile, name, None)
        if isinstance(value, type) and issubclass(value, Widget):
            names.add(name)
    return frozenset(names)


def _name_of(node: ast.expr) -> str | None:
    """``Widget`` for ``Widget`` and ``pymobile.Widget``; ``None`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_type_name(cls: ast.ClassDef) -> str | None:
    """The string a class body assigns to ``type_name``, if it is a literal."""
    for statement in cls.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "type_name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value
    return None


def _declaration(call: ast.Call) -> tuple[str, bool] | None:
    """``("BarChart", android)`` for a ``register_widget_type("BarChart", ...)`` call."""
    if _name_of(call.func) != "register_widget_type" or not call.args:
        return None
    first = call.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    android = True
    for keyword_argument in call.keywords:
        if (
            keyword_argument.arg == "android"
            and isinstance(keyword_argument.value, ast.Constant)
            and keyword_argument.value.value is False
        ):
            android = False
    return first.value, android


def scan_custom_widgets(files: Iterable[Path]) -> CustomWidgets:
    """Read the project's ``.py`` files for custom widget classes and declarations.

    Best-effort and syntactic, like the other build-time scans: a ``type_name``
    computed at runtime is invisible to it, and a file that does not parse is
    skipped. It errs towards *not* naming a type: only a class that derives
    (directly, or through another class of the project) from a framework widget
    counts, so an unrelated ``type_name = "sqlite"`` on a data class is left alone.
    """
    definitions: list[tuple[str, set[str], str | None]] = []
    declared: set[str] = set()
    preview_only: set[str] = set()
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            # Bytes, so that a UTF-8 BOM (Notepad, Windows PowerShell) and a
            # ``# coding:`` line are handled by the interpreter: parsed as text, a
            # file with a BOM is a SyntaxError and a cp1251 file does not decode,
            # and a widget defined there would escape the build check.
            tree = ast.parse(path.read_bytes())
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {name for base in node.bases if (name := _name_of(base)) is not None}
                definitions.append((node.name, bases, _literal_type_name(node)))
            elif isinstance(node, ast.Call) and (found := _declaration(node)) is not None:
                declared.add(found[0])
                if not found[1]:
                    preview_only.add(found[0])

    widgets = set(_builtin_widget_classes())
    changed = True
    while changed:  # a widget can derive from another widget of the project
        changed = False
        for name, bases, _ in definitions:
            if name not in widgets and bases & widgets:
                widgets.add(name)
                changed = True
    types = {
        type_name
        for name, _, type_name in definitions
        if name in widgets and type_name is not None
    } - widget_types()
    return CustomWidgets(frozenset(types), frozenset(declared), frozenset(preview_only))


# --------------------------------------------------------------------------
# asking the dex
# --------------------------------------------------------------------------
def _uleb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def dex_has_case(dex: bytes, type_name: str) -> bool:
    """Whether ``type_name`` is a string of this ``classes.dex``.

    ``case "BarChart":`` in ``ViewBuilder.java`` compiles to a comparison with
    the string constant ``"BarChart"``, and every string constant of a dex file
    is an entry of its string table: ULEB128 length, the characters, a NUL. A
    dex without that entry cannot contain the branch — which is the answer the
    build needs. (The converse is only a strong hint, not a proof: some other
    string could spell the same word.)
    """
    if not type_name:
        return False
    if type_name.isascii():
        return _uleb128(len(type_name)) + type_name.encode("ascii") + b"\0" in dex
    return type_name.encode("utf-8") in dex


def dex_has_class(dex: bytes, class_name: str) -> bool:
    """Whether a dex string table contains a Java class descriptor.

    This is used for the project-local renderer convention
    ``org.pymobile.app.widgets.<Type>Renderer``. Checking the descriptor (not
    just the widget name) proves the plugin class itself was compiled into the
    APK that is about to be signed.
    """
    if not class_name or not class_name.isascii():
        return False
    descriptor = "L" + class_name.replace(".", "/") + ";"
    encoded = descriptor.encode("ascii")
    return _uleb128(len(descriptor)) + encoded + b"\0" in dex


# --------------------------------------------------------------------------
# the Java branch
# --------------------------------------------------------------------------
_TYPE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_PROP_KINDS = ("str", "int", "float", "bool", "list")
#: Attributes every ``Widget`` already has; a prop of that name would clobber them.
_WIDGET_RESERVED = frozenset(
    {
        "id", "style", "visible", "enabled", "parent", "children", "screen",
        "type_name", "props", "on_press",
    }
)
#: Locals of ``build()`` / ``updateNode()`` a generated variable must not shadow.
_RESERVED_LOCALS = frozenset(
    {"id", "props", "view", "node", "type", "style", "context", "enabled"}
)
_JAVA_KEYWORDS = frozenset(
    (
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
        "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
        "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private", "protected", "public",
        "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
        "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false",
        "null",
    )
)


@dataclass(frozen=True, slots=True)
class WidgetProp:
    """One prop the Python widget's ``props()`` sends, and how Java should read it."""

    name: str
    kind: str = "str"

    @property
    def variable(self) -> str:
        """The Java local that holds the value (``max_length`` → ``maxLength``)."""
        head, *tail = self.name.split("_")
        camel = head + "".join(part[:1].upper() + part[1:] for part in tail)
        if camel in _JAVA_KEYWORDS or camel in _RESERVED_LOCALS:
            camel += "Value"
        return camel

    @property
    def default(self) -> str:
        """The Python default value of the constructor argument."""
        return {"int": "0", "float": "0.0", "bool": "False", "list": "()"}.get(self.kind, '""')

    @property
    def read(self) -> str:
        """The statement that reads the prop from the ``props`` JSONObject."""
        key = f'"{self.name}"'
        if self.kind == "int":
            return f"int {self.variable} = props.optInt({key}, 0);"
        if self.kind == "float":
            return f"double {self.variable} = props.optDouble({key}, 0);"
        if self.kind == "bool":
            return f"boolean {self.variable} = props.optBoolean({key}, false);"
        if self.kind == "list":
            return f"JSONArray {self.variable} = props.optJSONArray({key});  // may be null"
        return f'String {self.variable} = props.optString({key}, "");'


def parse_props(specs: Sequence[str]) -> list[WidgetProp]:
    """Turn ``["title", "value:int", "on:bool"]`` into props (default kind: ``str``)."""
    props: list[WidgetProp] = []
    seen: set[str] = set()
    for spec in specs:
        name, _, kind = spec.partition(":")
        name, kind = name.strip(), (kind.strip() or "str")
        if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
            raise ConfigError(
                f"{spec!r} is not a prop name",
                hint="A prop is a public Python identifier: title, max_length, value:int …",
            )
        if name in _WIDGET_RESERVED:
            raise ConfigError(
                f"{name!r} is already an attribute of every widget",
                hint="Pick another name for the prop (label, caption, amount …).",
            )
        if kind not in _PROP_KINDS:
            raise ConfigError(
                f"unknown prop type {kind!r} in {spec!r}",
                hint=f"Use one of: {', '.join(_PROP_KINDS)} (for example value:int).",
            )
        if name in seen:
            raise ConfigError(f"prop {name!r} is listed twice")
        seen.add(name)
        props.append(WidgetProp(name, kind))
    return props


@dataclass(frozen=True, slots=True)
class JavaBranch:
    """The pieces of the ``ViewBuilder.java`` branch for one widget type."""

    type_name: str
    python: str
    case: str
    method: str
    update: str
    register: str

    def render(self, viewbuilder: Path | None = None) -> str:
        """The whole guide: what to write where, then how to rebuild the dex."""
        where = f"\n   File: {viewbuilder}" if viewbuilder is not None else ""
        return (
            f"Android renderer for the widget type {self.type_name!r}\n"
            f"{'=' * 60}\n\n"
            "1) Your app: the Python widget (a skeleton — adapt props() to your data)\n\n"
            f"{_indent(self.python)}\n\n"
            "2) ViewBuilder.java, in build(): add this case to `switch (type)`, above "
            f"`default:`{where}\n\n{_indent(self.case)}\n\n"
            "3) ViewBuilder.java: add this method next to buildBadge() / buildLink()\n\n"
            f"{_indent(self.method)}\n\n"
            "4) ViewBuilder.java, in updateNode(): add this before the generic\n"
            "   `if (view instanceof ViewGroup)` walk, so a changed prop reaches the\n"
            "   view. `return false` rebuilds the screen instead: always correct,\n"
            "   just less smooth than patching the view.\n\n"
            f"{_indent(self.update)}\n\n"
            "5) Your app: say the branch exists, so no warning is printed for the type\n\n"
            f"{_indent(self.register)}\n\n"
            "6) Rebuild the launcher dex. A native build compiles framework Java sources\n"
            "   by default and stops if the resulting dex has no branch for your type:\n\n"
            "       pymobile build --native\n\n"
            "   This manual route changes the shared ViewBuilder.java in your source\n"
            "   checkout. For an ordinary app-local widget, `pymobile widget add TYPE`\n"
            "   creates an isolated Java renderer under your project's java/ directory.\n"
            "   To skip Java altogether, compose from existing widgets (Row, Column,\n"
            "   ProgressBar, Expanded …) — see the README, *Extending*.\n"
        )


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def java_branch(type_name: str, props: Sequence[WidgetProp] = ()) -> JavaBranch:
    """Write the ``ViewBuilder.java`` branch for a custom widget type.

    The generated ``build<Type>`` reads each prop and shows them in a plain
    ``TextView`` — enough to see the widget on a phone immediately, and a place
    to put the real view. Raises :class:`~pymobile.errors.ConfigError` for a name
    that is not a Java-friendly identifier or that is already built in.
    """
    if not _TYPE_NAME.fullmatch(type_name):
        raise ConfigError(
            f"{type_name!r} is not a usable widget type name",
            hint="Use letters and digits, starting with a letter: BarChart, Sparkline2.",
        )
    if type_name in widget_types():
        raise ConfigError(
            f"{type_name!r} is a built-in widget type; ViewBuilder.java already draws it",
            hint="Pick another type_name for the new widget.",
        )
    method = f"build{type_name}"
    reads = "\n".join(f"    {prop.read}" for prop in props)
    shown = "".join(
        f'\n            + "{", " if index else ""}{prop.name}=" + {prop.variable}'
        for index, prop in enumerate(props)
    )
    summary = f'"{type_name}: "{shown}' if shown else f'"{type_name}"'
    body = f"""\
/** {type_name}: draws the props its Python widget sends from props(). */
private View {method}(final String id, JSONObject props) {{
    // The Python props, read with a default so a missing key cannot crash the screen.
{reads or "    // (this widget sends no props of its own)"}

    // TODO: replace this placeholder with the real native view.
    TextView view = new TextView(context);
    view.setText({summary});
    view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
    view.setTextColor(colorText);

    // Send an interaction back to Python. It reaches the widget with this id: "press"
    // calls its press(), "change" calls _ui_set_value()/set_value(value).
    view.setOnClickListener(new View.OnClickListener() {{
        @Override
        public void onClick(View v) {{
            Native.dispatchEvent(id, "press", "");
        }}
    }});
    return view;
}}"""

    def stored(prop: WidgetProp) -> str:
        return f"list({prop.name})" if prop.kind == "list" else prop.name

    def serialised(prop: WidgetProp) -> str:
        return f"list(self.{prop.name})" if prop.kind == "list" else f"self.{prop.name}"

    parameters = "".join(f"\n        {prop.name}={prop.default}," for prop in props)
    signature = (
        f"    def __init__(\n        self,{parameters}\n        **kwargs,\n    ):"
        if props
        else "    def __init__(self, **kwargs):"
    )
    assignments = "".join(f"\n        self.{prop.name} = {stored(prop)}" for prop in props)
    entries = "".join(f'\n            "{prop.name}": {serialised(prop)},' for prop in props)
    python = (
        "from pymobile import Widget\n\n\n"
        f"class {type_name}(Widget):\n"
        f'    type_name = "{type_name}"\n\n'
        f"{signature}\n"
        f"        super().__init__(**kwargs){assignments}\n\n"
        "    def props(self):\n"
        f"        return {{\n            **super().props(),{entries}\n        }}"
    )
    register = f'from pymobile import register_widget_type\n\nregister_widget_type("{type_name}")'
    return JavaBranch(
        type_name=type_name,
        python=python,
        case=f'case "{type_name}":\n    view = {method}(id, props);\n    break;',
        method=body,
        update=(
            f'if ("{type_name}".equals(type)) {{\n'
            "    // Patch `view` from `props` and return true — or rebuild the screen:\n"
            "    return false;\n"
            "}"
        ),
        register=register,
    )


def scaffold_widget_sources(type_name: str, props: Sequence[WidgetProp] = ()) -> tuple[str, str]:
    """Return a ready-to-import Python widget and its project-local Java renderer.

    The generated Python class is registered as soon as it is imported; the
    Java source implements :class:`org.pymobile.app.WidgetRenderer` in the
    ``java/`` project overlay. Native builds discover, compile and fingerprint
    it automatically — no installed-package edits or environment switches.
    """
    branch = java_branch(type_name, props)
    fields = tuple(props)
    annotations = {
        "str": "str",
        "int": "int",
        "float": "float",
        "bool": "bool",
        "list": "list[object]",
    }
    parameters = ",\n        ".join(
        f"{prop.name}: {annotations[prop.kind]} = {prop.default}"
        for prop in fields
    )
    if parameters:
        signature = (
            f"        {parameters},\n"
            "        *,\n"
            "        on_press: Callable[[], None] | None = None,\n"
            "        **kwargs: Any,"
        )
    else:
        signature = (
            "        *,\n"
            "        on_press: Callable[[], None] | None = None,\n"
            "        **kwargs: Any,"
        )
    assignments = "\n".join(
        f"        self.{prop.name} = list({prop.name})" if prop.kind == "list"
        else f"        self.{prop.name} = {prop.name}"
        for prop in fields
    )
    entries = "\n".join(
        f'            "{prop.name}": list(self.{prop.name}),' if prop.kind == "list"
        else f'            "{prop.name}": self.{prop.name},'
        for prop in fields
    )
    python = (
        "from collections.abc import Callable\n"
        "from typing import Any\n\n"
        "from pymobile import Widget, register_widget_type\n\n\n"
        f"class {type_name}(Widget):\n"
        f'    type_name = "{type_name}"\n\n'
        "    def __init__(\n"
        "        self,\n"
        f"{signature}\n"
        "    ) -> None:\n"
        "        super().__init__(**kwargs)\n"
        f"{assignments + chr(10) if assignments else ''}"
        "        if on_press is not None and not callable(on_press):\n"
        "            raise TypeError(\"on_press must be callable or None\")\n"
        "        self.on_press = on_press\n\n"
        "    def props(self) -> dict[str, Any]:\n"
        "        return {\n"
        "            **super().props(),\n"
        f"{entries + chr(10) if entries else ''}"
        "        }\n\n"
        "    def press(self) -> None:\n"
        "        if self.enabled and self.on_press is not None:\n"
        "            self.on_press()\n\n\n"
        f'register_widget_type("{type_name}")\n'
    )

    reads = "\n".join("        " + prop.read for prop in fields)
    shown: list[str] = []
    for prop in fields:
        if prop.kind == "str":
            shown.append(
                f'        if (!{prop.variable}.isEmpty()) '
                f'label.append("\\n{prop.name}=").append({prop.variable});'
            )
        elif prop.kind == "list":
            shown.append(
                f'        if ({prop.variable} != null) '
                f'label.append("\\n{prop.name}=").append({prop.variable});'
            )
        else:
            shown.append(
                f'        label.append("\\n{prop.name}=").append({prop.variable});'
            )
    java = (
        "package org.pymobile.app.widgets;\n\n"
        "import android.content.Context;\n"
        "import android.graphics.Color;\n"
        "import android.util.TypedValue;\n"
        "import android.view.View;\n"
        "import android.widget.TextView;\n\n"
        "import org.json.JSONArray;\n"
        "import org.json.JSONObject;\n"
        "import org.pymobile.app.Native;\n"
        "import org.pymobile.app.WidgetRenderer;\n\n"
        f"/** Starter native renderer for the PyMobile {type_name} widget. */\n"
        f"public final class {type_name}Renderer implements WidgetRenderer {{\n"
        f'    @Override public String typeName() {{ return "{type_name}"; }}\n\n'
        "    @Override\n"
        "    public View create(Context context, final String id, JSONObject props) {\n"
        "        TextView view = new TextView(context);\n"
        "        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);\n"
        "        view.setTextColor(Color.DKGRAY);\n"
        "        bind(view, props);\n"
        "        view.setOnClickListener(new View.OnClickListener() {\n"
        "            @Override public void onClick(View view) {\n"
        '                Native.dispatchEvent(id, "press", "");\n'
        "            }\n"
        "        });\n"
        "        return view;\n"
        "    }\n\n"
        "    @Override\n"
        "    public boolean update(View view, String id, JSONObject props) {\n"
        "        if (!(view instanceof TextView)) return false;\n"
        "        bind((TextView) view, props);\n"
        "        return true;\n"
        "    }\n\n"
        "    private void bind(TextView view, JSONObject props) {\n"
        f"{reads + chr(10) if reads else ''}"
        f'        StringBuilder label = new StringBuilder("{type_name}");\n'
        f"{chr(10).join(shown) + chr(10) if shown else ''}"
        "        // TODO: replace this labelled starter view with your Android view.\n"
        "        view.setText(label.toString());\n"
        "    }\n"
        "}\n"
    )
    # Keep the variable referenced to make an accidental future divergence
    # from java_branch() visible to static checks rather than to a user.
    if branch.type_name != type_name:  # pragma: no cover - defensive
        raise AssertionError("generated Python and Java widget names diverged")
    return python, java
