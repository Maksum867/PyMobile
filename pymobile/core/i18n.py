"""Internationalisation.

Everything needed to ship an app in several languages:

* :class:`Translations` — a tiny catalogue with ``%``/``{}`` interpolation and
  plural support, loadable from a JSON file per language;
* :func:`device_language` — the language the *phone* is set to, which is the
  piece the standard library cannot provide;
* :func:`t` — the module-level shorthand applications actually call.

``gettext`` and ``.mo`` catalogues keep working — the stdlib is packaged in
full — and :meth:`Translations.install_gettext` hands over to it when a
project already has a translator workflow. The built-in JSON format exists
because a mobile app usually needs a dozen strings, not a toolchain.

**Keys are flat: a dot is part of the key, not a path.** A catalogue is one
level of ``"key": "text"`` pairs, so ``t("stats.balance")`` needs a literal
``"stats.balance"`` entry — a JSON file with sections::

    { "stats": { "balance": "Баланс" } }     # t("stats.balance") → "stats.balance"

would render the key itself. The one nested shape that *is* understood is a
plural form map (``{"one": …, "few": …, "many": …}``) — see :meth:`Translations.get`.
Loading such a file logs a warning naming the nested paths; pass
``flatten=True`` (or call :func:`flatten_catalogue`) when you would rather
keep sections in the source file.

::

    from pymobile import t, translations

    translations.load({"greeting": "Привіт, {name}!"}, language="uk")
    translations.use(device_language(default="en"))

    Label(t("greeting", name="Оксана"))
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ..log import get_logger
from .formatting import (
    Localized,
    format_currency,
    format_date,
    format_datetime,
    format_number,
    format_percent,
    format_time,
    supported_format_languages,
)

__all__ = [
    "Translations",
    "translations",
    "t",
    "flatten_catalogue",
    "device_language",
    "normalise_language",
    "plural_category",
    "format_number",
    "format_percent",
    "format_currency",
    "format_date",
    "format_time",
    "format_datetime",
    "supported_format_languages",
]

_log = get_logger("i18n")

#: File stems that do not look like language tags (П-14).  When a user passes
#: ``messages.json`` they usually meant ``uk.json``/``en.json`` and would
#: otherwise end up with a catalogue registered under ``"messages"``, which
#: every ``t(...)`` call silently misses.
_NON_LANGUAGE_STEMS: frozenset[str] = frozenset(
    {"messages", "strings", "i18n", "locale", "locales", "translations", "lang", "langs"}
)
#: Stems already warned about, so each suspicious file is reported once.
_warned_stems: set[str] = set()


def _warn_non_language_stem(stem: str) -> None:
    """Emit a warning once per stem that does not look like a language tag."""
    if stem in _warned_stems:
        return
    if stem.lower() in _NON_LANGUAGE_STEMS or (
        len(stem) > 3 and not (2 <= len(stem.split("-")[0]) <= 3)
    ):
        _warned_stems.add(stem)
        _log.warning(
            "the catalogue file %r does not look like a language tag "
            "(got %r). Rename it to a BCP-47 code like uk.json / en.json so "
            "translations.use(\"uk\") can find it, or pass language= explicitly.",
            stem + ".json",
            stem,
        )


#: Languages where "one" covers 1 only and everything else is plural.
_DEFAULT_PLURAL_KEYS = ("one", "other")

#: CLDR quantity names. A nested object made only of these is a plural form
#: map; any other nested object is a *namespace*, which a catalogue cannot
#: address — see :func:`flatten_catalogue`.
_PLURAL_FORM_KEYS = frozenset({"zero", "one", "two", "few", "many", "other"})


def _is_plural_forms(value: object) -> bool:
    """Whether ``value`` is a plural form map (``{"one": …, "other": …}``).

    The test is deliberately narrow: **every** key must be a quantity name,
    so ``{"balance": …}`` is a namespace while ``{"one": …, "few": …}`` is
    not. A section that happens to be called ``one``/``many`` is therefore
    read as plural forms — name sections after what they contain.
    """
    if not isinstance(value, Mapping):
        return False
    return bool(value) and all(str(key) in _PLURAL_FORM_KEYS for key in value)


def flatten_catalogue(messages: Mapping[str, Any], *, separator: str = ".") -> dict[str, Any]:
    """Flatten a nested catalogue into the one-level form lookups expect.

    ::

        flatten_catalogue({"stats": {"balance": "Баланс"}})
        # {"stats.balance": "Баланс"}

    Nested objects whose keys are all CLDR quantity names are kept exactly as
    they are: they are plural forms for the key above them, not a namespace.
    The default separator is ``"."`` because that is what ``t()`` keys look
    like in practice — nothing special happens to the dots afterwards, the
    result is still a flat mapping of literal keys.
    """
    flat: dict[str, Any] = {}

    def walk(mapping: Mapping[str, Any], prefix: str) -> None:
        for key, value in mapping.items():
            name = f"{prefix}{separator}{key}" if prefix else str(key)
            if not isinstance(value, Mapping) or not value or _is_plural_forms(value):
                flat[name] = value
            else:
                walk(value, name)

    walk(messages, "")
    return flat


def _nested_namespaces(messages: Mapping[str, Any]) -> list[str]:
    """Names of top-level entries that hold a nested namespace, not plural forms."""
    return [
        str(key)
        for key, value in messages.items()
        if isinstance(value, Mapping) and value and not _is_plural_forms(value)
    ]


# CLDR cardinal plural rules for whole numbers, keyed by base language. The
# category depends on the LANGUAGE, not on which forms a catalogue happens to
# contain: Polish 21 is "many" (21 plików), Ukrainian 21 is "one" (21 файл).
def _east_slavic(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _south_slavic(n: int) -> str:
    category = _east_slavic(n)
    return "other" if category == "many" else category


def _polish(n: int) -> str:
    if n == 1:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _czech(n: int) -> str:
    return "one" if n == 1 else "few" if 2 <= n <= 4 else "other"


def _lithuanian(n: int) -> str:
    if n % 10 == 1 and not 11 <= n % 100 <= 19:
        return "one"
    if 2 <= n % 10 <= 9 and not 11 <= n % 100 <= 19:
        return "few"
    return "other"


def _latvian(n: int) -> str:
    if n % 10 == 0 or 11 <= n % 100 <= 19:
        return "zero"
    return "one" if n % 10 == 1 and n % 100 != 11 else "other"


def _romanian(n: int) -> str:
    if n == 1:
        return "one"
    return "few" if n == 0 or 2 <= n % 100 <= 19 else "other"


def _slovenian(n: int) -> str:
    return {1: "one", 2: "two", 3: "few", 4: "few"}.get(n % 100, "other")


def _arabic(n: int) -> str:
    if n in (0, 1, 2):
        return ("zero", "one", "two")[n]
    if 3 <= n % 100 <= 10:
        return "few"
    return "many" if 11 <= n % 100 <= 99 else "other"


def _hebrew(n: int) -> str:
    return "one" if n == 1 else "two" if n == 2 else "other"


def _irish(n: int) -> str:
    if n in (1, 2):
        return ("one", "two")[n - 1]
    return "few" if 3 <= n <= 6 else "many" if 7 <= n <= 10 else "other"


def _welsh(n: int) -> str:
    return {0: "zero", 1: "one", 2: "two", 3: "few", 6: "many"}.get(n, "other")


def _zero_or_one(n: int) -> str:
    return "one" if n in (0, 1) else "other"


def _icelandic(n: int) -> str:
    return "one" if n % 10 == 1 and n % 100 != 11 else "other"


def _no_plural(n: int) -> str:
    return "other"


def _english(n: int) -> str:
    return "one" if n == 1 else "other"


_PLURAL_RULES: dict[str, Callable[[int], str]] = {
    **dict.fromkeys(("uk", "ru", "be"), _east_slavic),
    **dict.fromkeys(("hr", "sr", "bs", "sh"), _south_slavic),
    "pl": _polish,
    **dict.fromkeys(("cs", "sk"), _czech),
    "lt": _lithuanian,
    "lv": _latvian,
    **dict.fromkeys(("ro", "mo"), _romanian),
    "sl": _slovenian,
    "ar": _arabic,
    **dict.fromkeys(("he", "iw"), _hebrew),
    "ga": _irish,
    "cy": _welsh,
    **dict.fromkeys(("fr", "pt", "hi", "bn", "fa", "gu", "kn", "mr", "zu", "am"), _zero_or_one),
    **dict.fromkeys(("is", "mk"), _icelandic),
    **dict.fromkeys(
        ("zh", "ja", "ko", "vi", "th", "id", "ms", "lo", "my", "km", "yue"), _no_plural
    ),
}


def plural_category(count: float, language: str) -> str:
    """The CLDR plural category (zero/one/two/few/many/other) of ``count``.

    Languages without a rule here use the English one. Fractions are
    "other" (the category every one of these languages uses for them, bar a
    few "many" cases that catalogues rarely distinguish).
    """
    if isinstance(count, float) and not count.is_integer():
        return "other"
    rule = _PLURAL_RULES.get(normalise_language(language).split("-")[0], _english)
    return rule(abs(int(count)))


def normalise_language(tag: str) -> str:
    """Reduce a locale tag to a lowercase ``language`` or ``language-region``.

    ``uk_UA.UTF-8`` and ``uk-ua`` both become ``uk-ua``; ``C`` and ``POSIX``
    become ``en``.
    """
    if not tag:
        return ""
    cleaned = tag.split(".")[0].split("@")[0].replace("_", "-").strip().lower()
    if cleaned in ("c", "posix", ""):
        return "en"
    return cleaned


def device_language(*, default: str = "en") -> str:
    """The language the device (or the desktop shell) is configured to use.

    On Android the value comes from the platform bridge, so it follows the
    system setting and survives the user changing it. Elsewhere the usual
    environment variables are consulted, which is what makes the same code
    testable on a laptop.
    """
    from .bridge import get_bridge

    bridge = get_bridge()
    getter = getattr(bridge, "device_language", None)
    if callable(getter):
        try:
            tag = normalise_language(str(getter() or ""))
        except Exception:  # pragma: no cover - a broken bridge must not crash
            _log.debug("bridge could not report the device language", exc_info=True)
            tag = ""
        if tag:
            return tag

    for variable in ("PYMOBILE_LANGUAGE", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            # LANGUAGE may hold a colon-separated priority list.
            tag = normalise_language(value.split(":")[0])
            if tag:
                return tag
    return normalise_language(default)


class Translations:
    """A catalogue of message strings, one dictionary per language.

    Keys are flat — ``"stats.balance"`` is one key, not ``stats`` →
    ``balance`` (see the module docstring). Lookup falls back from the region
    to the bare language and finally to the default language, so ``pt-br``
    quietly uses ``pt`` and an untranslated key still renders as English
    rather than blowing up mid-screen.
    """

    def __init__(self, *, default_language: str = "en") -> None:
        self.default_language = normalise_language(default_language)
        self._catalogues: dict[str, dict[str, Any]] = {}
        self._language = self.default_language
        self._missing: set[str] = set()
        #: Languages already told about a nested catalogue, so a reload does
        #: not repeat the same warning on every hot reload.
        self._warned_nested: set[str] = set()
        self._listeners: list[Callable[[str], None]] = []

    # -- change notification ----------------------------------------------
    def subscribe(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Call ``listener(language)`` whenever the active language changes.

        Returns a function that removes the listener again. :class:`App` uses
        this to rebuild the visible screen, because ``t()`` is evaluated inside
        ``build()`` — the translated string is baked into the widget, so a new
        language needs a rebuild rather than a redraw.
        """
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self) -> None:
        """Tell every listener the language changed; errors never propagate."""
        for listener in tuple(self._listeners):
            try:
                listener(self._language)
            except Exception:  # pragma: no cover - a bad listener must not break i18n
                _log.exception("language listener failed")

    # -- catalogue management ---------------------------------------------
    @property
    def language(self) -> str:
        """The language currently in use."""
        return self._language

    @property
    def languages(self) -> tuple[str, ...]:
        """Every language with a loaded catalogue."""
        return tuple(sorted(self._catalogues))

    def load(
        self, messages: Mapping[str, Any], *, language: str, flatten: bool = False
    ) -> None:
        """Add or extend the catalogue for ``language``.

        ``messages`` is a flat mapping — ``"stats.balance"`` is one key whose
        name contains a dot. Pass ``flatten=True`` to accept a nested mapping
        from a JSON file with sections; it is expanded with
        :func:`flatten_catalogue` first (an unexpanded nested object is
        warned about, because it silently makes every dotted key miss).
        """
        # П-13: a Path/str passed where a mapping is expected used to surface
        # as ``AttributeError: 'PosixPath' object has no attribute 'items'``
        # — point the caller at ``load_file``/``load_dir`` instead.
        if isinstance(messages, (str, Path)):
            raise TypeError(
                f"messages must be a mapping, got {type(messages).__name__!r}; "
                "use translations.load_file(path) to load a JSON file, or "
                "translations.load_dir(directory) for a whole directory."
            )
        if not isinstance(messages, Mapping):
            raise TypeError(
                f"messages must be a mapping, got {type(messages).__name__!r}"
            )
        tag = normalise_language(language)
        if not tag:
            raise ValueError("language must not be empty")
        if flatten:
            messages = flatten_catalogue(messages)
        else:
            self._warn_nested(tag, messages)
        self._catalogues.setdefault(tag, {}).update(messages)

    def _warn_nested(self, tag: str, messages: Mapping[str, Any]) -> None:
        """Point out nested JSON once per language.

        The lookup is flat, so a catalogue written as sections does not raise
        anything — it renders bare keys on screen and the only clue is one
        "missing translation" line per key. Say what happened, where, and what
        to do instead, while the file is still being loaded.
        """
        nested = _nested_namespaces(messages)
        if not nested or tag in self._warned_nested:
            return
        self._warned_nested.add(tag)
        shown = ", ".join(repr(name) for name in nested[:3])
        more = f" (+{len(nested) - 3} more)" if len(nested) > 3 else ""
        _log.warning(
            "catalogue %r stores nested objects under %s%s, but keys are flat: "
            't("a.b") only finds a literal "a.b" entry. Flatten the file or pass '
            "flatten=True (see flatten_catalogue) to load it as it is.",
            tag,
            shown,
            more,
        )

    def load_dict(
        self, catalogues: Mapping[str, Mapping[str, Any]], *, flatten: bool = False
    ) -> tuple[str, ...]:
        """Load multiple language catalogues from a dict.

        Convenience method for in-code translations without external files::

            translations.load_dict({
                "en": {"greeting": "Hello"},
                "uk": {"greeting": "Привіт"},
            })

        Returns the normalised language tags that were loaded. ``flatten=True``
        accepts nested sections, like :meth:`load`.
        """
        if not isinstance(catalogues, Mapping):
            raise TypeError(
                f"catalogues must be a mapping, got {type(catalogues).__name__!r}"
            )
        loaded: list[str] = []
        for lang, messages in catalogues.items():
            if not isinstance(messages, Mapping):
                raise TypeError(
                    f"messages for {lang!r} must be mapping, "
                    f"got {type(messages).__name__!r}"
                )
            self.load(messages, language=lang, flatten=flatten)
            loaded.append(normalise_language(lang))
        return tuple(loaded)

    def load_file(
        self, path: str | Path, *, language: str | None = None, flatten: bool = False
    ) -> str:
        """Load a JSON catalogue; the language defaults to the file's stem.

        ``locales/uk.json`` therefore needs no arguments at all. The JSON must
        be one level of ``"key": "text"`` pairs (a plural form map is the one
        nested shape that is understood); ``flatten=True`` expands nested
        sections into dotted keys instead of warning about them.
        """
        file = Path(path)
        tag = normalise_language(language or file.stem)
        # П-14: a file named ``messages.json`` / ``strings.json`` / ``i18n.json``
        # is a common Windows/mobile convention that does NOT look like a
        # language tag — silently registering it under ``"messages"`` makes
        # every ``t(...)`` miss. Warn once per stem instead.
        _warn_non_language_stem(file.stem)
        try:
            # П-18: ``utf-8-sig`` transparently strips a UTF-8 BOM, which
            # Notepad and other Windows editors prepend; without this the
            # JSON parser raised ``ValueError: Unexpected UTF-8 BOM``.
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except OSError as error:
            raise FileNotFoundError(f"cannot read catalogue {file}: {error}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"{file} is not valid JSON: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"{file} must contain a JSON object of messages")
        self.load(data, language=tag, flatten=flatten)
        return tag

    def load_dir(self, directory: str | Path, *, flatten: bool = False) -> tuple[str, ...]:
        """Load every ``*.json`` catalogue in a directory."""
        root = Path(directory)
        loaded = [self.load_file(path, flatten=flatten) for path in sorted(root.glob("*.json"))]
        return tuple(loaded)

    def use(self, language: str) -> str:
        """Switch the active language and return the tag actually selected.

        Any screen currently on display is rebuilt, so the new language shows
        up immediately instead of on the next navigation.
        """
        tag = normalise_language(language) or self.default_language
        if tag == self._language:
            return self._language
        self._language = tag
        self._missing.clear()
        _log.debug("language set to %s", self._language)
        self._notify()
        return self._language

    def clear(self) -> None:
        """Forget every catalogue (used by tests)."""
        self._catalogues.clear()
        self._missing.clear()
        self._warned_nested.clear()
        self._language = self.default_language

    # -- lookup ------------------------------------------------------------
    def _chain(self, language: str) -> Iterable[str]:
        """Catalogues to consult, most specific first."""
        seen: list[str] = []
        for candidate in (language, language.split("-")[0], self.default_language):
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen

    def lookup(self, key: str, *, language: str | None = None) -> Any:
        """Return the raw entry for ``key``, or ``None`` when it is unknown."""
        return self._lookup(key, language)[0]

    def _lookup(self, key: str, language: str | None) -> tuple[Any, str]:
        """The entry for ``key`` and the language of the catalogue it came from."""
        requested = normalise_language(language or self._language)
        for tag in self._chain(requested):
            catalogue = self._catalogues.get(tag)
            if catalogue is not None and key in catalogue:
                return catalogue[key], tag
        return None, requested

    def has(self, key: str, *, language: str | None = None) -> bool:
        """Whether ``key`` resolves in the given (or current) language."""
        return self.lookup(key, language=language) is not None

    def get(
        self,
        key: str,
        /,
        *,
        count: int | None = None,
        language: str | None = None,
        default: str | None = None,
        **params: Any,
    ) -> str:
        """Translate ``key``, interpolating ``params``.

        A missing key returns ``default`` if given, otherwise the key itself —
        a screen with one untranslated string must still render. Each missing
        key is logged once, so a gap is visible during development without
        flooding the log from inside a render loop.
        """
        entry, found_in = self._lookup(key, language)
        if entry is None:
            if key not in self._missing:
                self._missing.add(key)
                # A dotted key that misses is usually not a typo but a
                # catalogue written as sections: the lookup is flat, so the
                # dot has to be in the key itself.
                hint = (
                    " (a dot is part of the key — nested sections need flatten=True)"
                    if "." in key
                    else ""
                )
                _log.warning("missing translation for %r in %r%s", key, self._language, hint)
            entry = key if default is None else default

        if isinstance(entry, Mapping):
            entry = self._plural(entry, count, found_in)

        text = str(entry)
        if count is not None:
            params.setdefault("count", count)
        if not params:
            return text
        # Parameters are wrapped so a catalogue can ask for locale formats:
        # "{sum:currency:UAH}", "{day:date:long}", "{n:number:2}". The formats
        # follow the language of the catalogue the entry came from.
        wrapped = {name: Localized(value, found_in) for name, value in params.items()}
        try:
            return text.format(**wrapped)
        except (KeyError, IndexError, ValueError, TypeError):
            # A malformed placeholder must not take the screen down.
            _log.warning("could not interpolate %r with %r", key, sorted(params))
            return text

    def _plural(self, forms: Mapping[str, Any], count: int | None, language: str = "en") -> Any:
        """Pick the plural form of ``count`` by the CLDR rule of ``language``.

        ``language`` is the catalogue the entry was found in, so a key that
        falls back to the default language is also pluralised by its rule.
        An explicit ``zero`` form is honoured for 0 in every language. When
        the catalogue lacks the category, ``other`` is used, then ``many``.
        """
        if count is None:
            return forms.get("other") or next(iter(forms.values()), "")
        if count == 0 and "zero" in forms:
            return forms["zero"]
        category = plural_category(count, language)
        for key in (category, "other", "many", *_DEFAULT_PLURAL_KEYS):
            if key in forms:
                return forms[key]
        return next(iter(forms.values()), "")

    # -- interoperability --------------------------------------------------
    def install_gettext(self, domain: str, localedir: str | Path) -> None:
        """Back this catalogue with standard ``.mo`` files.

        For projects that already run xgettext/msgfmt: the compiled catalogue
        for the active language is read through :mod:`gettext` and merged in,
        so ``t()`` keeps working unchanged.
        """
        import gettext as gettext_module

        try:
            translation = gettext_module.translation(
                domain, localedir=str(localedir), languages=[self._language], fallback=False
            )
        except OSError:
            _log.warning("no gettext catalogue for %r in %s", self._language, localedir)
            return
        catalogue = {
            key: value
            for key, value in translation._catalog.items()  # type: ignore[attr-defined]
            if isinstance(key, str) and key
        }
        self.load(catalogue, language=self._language)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.has(key)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Translations language={self._language!r} loaded={self.languages}>"


#: The catalogue used by :func:`t`; applications normally need only this one.
translations = Translations()


def t(key: str, /, *, count: int | None = None, default: str | None = None, **params: Any) -> str:
    """Translate ``key`` using the global :data:`translations` catalogue."""
    return translations.get(key, count=count, default=default, **params)
