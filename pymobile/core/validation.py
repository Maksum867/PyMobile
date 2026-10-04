"""Dependency-free form/input validation.

A small set of composable validators plus a :class:`Validator` that combines
them, so an app can validate a login form, a settings field or any input
without pulling in a validation library. The validators are pure and produce a
list of human-readable error messages — they never mutate state, so the same
validator can be reused for many fields.

Example::

    v = Validator(
        ("email", [required, email]),
        ("age", [integer, between(0, 120)]),
    )
    errors = v.validate({"email": "x@y.com", "age": 30})
    assert errors == {}

**Localizing the messages.** Every rule has a stable id (``"required"``,
``"min_length"``, ``"between"`` …) and an English default in
:data:`DEFAULT_MESSAGES`. A message is looked up, most specific first, in:

1. the ``messages=`` mapping of the :class:`Validator` (``"email.required"`` for
   one field, ``"required"`` for every field);
2. the application's translation catalogue under ``validation.<id>``
   (``validation.required``), so ``translations.use("uk")`` translates the
   messages of every validator at once, at the moment they are produced;
3. :data:`DEFAULT_MESSAGES`.

Templates use ``str.format`` placeholders named after the rule's parameters —
``"must be at least {minimum} characters"``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..errors import PyMobileError
from ..log import get_logger
from .i18n import translations

__all__ = [
    "DEFAULT_MESSAGES",
    "RuleMessage",
    "Validator",
    "ValidationError",
    "required",
    "optional",
    "email",
    "length",
    "min_length",
    "max_length",
    "integer",
    "number",
    "between",
    "min",
    "max",
    "matches",
    "one_of",
    "regex",
    "boolean",
]

_log = get_logger("validation")

#: A validator: takes a value and returns None (ok) or an error message.
ValidatorFn = Callable[[Any], str | None]

#: The English text of every built-in rule, by rule id. ``{name}`` marks a
#: parameter of the rule. Translate them with ``validation.<id>`` entries in a
#: catalogue, or replace them for one validator with ``Validator(messages=…)``.
DEFAULT_MESSAGES: Mapping[str, str] = {
    "required": "is required",
    "email": "must be a valid email address",
    "min_length": "must be at least {minimum} characters",
    "max_length": "must be at most {maximum} characters",
    "integer": "must be an integer",
    "number": "must be a number",
    "between": "must be between {low} and {high}",
    "min": "must be between {low} and {high}",
    "max": "must be between {low} and {high}",
    "matches": "does not match",
    "matches_field": "does not match {field!r}",
    "one_of": "must be one of: {choices}",
    "regex": "must match {pattern!r}",
    "boolean": "must be a boolean",
}

#: Ids that only exist to override several rules at once (``"length"`` covers
#: ``min_length`` and ``max_length``); they have no text of their own.
_GROUP_IDS = frozenset({"length"})

#: A ``messages=`` value: a ``str.format`` template, or a callable that takes the
#: rule's parameters as keyword arguments and returns the message.
MessageOverride = str | Callable[..., str]


def _lookup_override(
    overrides: Mapping[str, MessageOverride], ids: Sequence[str], field: str | None
) -> MessageOverride | None:
    """The override for the first of ``ids``: ``field.id`` beats ``id``."""
    for rule_id in ids:
        if field is not None and f"{field}.{rule_id}" in overrides:
            return overrides[f"{field}.{rule_id}"]
    for rule_id in ids:
        if rule_id in overrides:
            return overrides[rule_id]
    return None


def _fill(template: str, params: Mapping[str, Any], rule_id: str) -> str:
    """``template.format(**params)`` that never takes a form down."""
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError, AttributeError):
        _log.warning("could not format the %r validation message %r", rule_id, template)
        return template


def render_message(
    key: str,
    params: Mapping[str, Any],
    *,
    fallback: str | None = None,
    overrides: Mapping[str, MessageOverride] | None = None,
    field: str | None = None,
) -> str:
    """Produce the text of rule ``key``: overrides, then the catalogue, then English.

    ``fallback`` is the more general id (``matches`` for ``matches_field``):
    it is consulted after the specific id at each step, so overriding
    ``"matches"`` covers both spellings of the rule.
    """
    ids = (key, fallback) if fallback else (key,)
    if overrides:
        override = _lookup_override(overrides, ids, field)
        if override is not None:
            if callable(override):
                return str(override(**params))
            return _fill(override, params, key)
    for rule_id in ids:
        # ``has`` first: ``get`` logs a "missing translation" warning, and an
        # application that does not translate validation is not making a mistake.
        if translations.has(f"validation.{rule_id}"):
            return translations.get(f"validation.{rule_id}", **params)
    return _fill(DEFAULT_MESSAGES[key], params, key)


class RuleMessage(str):
    """The error text of a built-in rule, which remembers which rule it came from.

    It *is* the message — ``required("") == "is required"`` — so callers of the
    bare rule functions see an ordinary string. :class:`Validator` reads
    :attr:`key` and :attr:`params` to apply its ``messages=`` overrides before
    it reports the error as a plain ``str``.
    """

    key: str
    fallback: str | None
    params: dict[str, Any]

    def __new__(cls, key: str, /, *, fallback: str | None = None, **params: Any) -> RuleMessage:
        self = super().__new__(cls, render_message(key, params, fallback=fallback))
        self.key = key
        self.fallback = fallback
        self.params = params
        return self

#: Well-known regex for email addresses (pragmatic, not RFC-perfect).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: What a user can type as an integer / a decimal number. Python's int() and
#: float() are more permissive than a form should be: they accept "4_2",
#: "nan", "inf" and non-ASCII digits.
_INTEGER_RE = re.compile(r"[+-]?[0-9]+", re.ASCII)
_NUMBER_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


def _is_empty(value: Any) -> bool:
    """None, an empty collection or a whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict, tuple, set)) and not value

#: Rules that only exist with an argument; a bare string spelling of one of
#: these is a mistake we can name precisely instead of "unknown rule".
_ARGUMENT_RULES = frozenset(
    {"length", "min_length", "max_length", "between", "min", "max", "matches", "one_of", "regex"}
)


class ValidationError(PyMobileError):
    """Raised by :meth:`Validator.validate_or_raise` when validation fails."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        message = ", ".join(f"{k}: {v}" for k, v in errors.items())
        super().__init__(message)
        self.errors = dict(errors)


# --------------------------------------------------------------------------
# Individual validators (return None on success, a message on failure)
# --------------------------------------------------------------------------
def required(value: Any) -> str | None:
    """A value must be present: not None, not empty, not only whitespace."""
    return RuleMessage("required") if _is_empty(value) else None


def optional(value: Any) -> str | None:
    """Allow the field to be left empty; its other rules run only when filled.

    Handled by :class:`Validator`: a field WITHOUT ``optional`` is validated
    even when empty, so ``["email"]`` rejects ``""``. Never fails itself.
    """
    return None


def email(value: Any) -> str | None:
    """A value must look like an email address."""
    if not isinstance(value, str) or not _EMAIL_RE.match(value.strip()):
        return RuleMessage("email")
    return None


def length(minimum: int | None = None, maximum: int | None = None) -> ValidatorFn:
    """A string's length must fall within [minimum, maximum]."""

    def _check(value: Any) -> str | None:
        if value is None:
            return None
        size = len(value)
        if minimum is not None and size < minimum:
            return RuleMessage("min_length", fallback="length", minimum=minimum, maximum=maximum)
        if maximum is not None and size > maximum:
            return RuleMessage("max_length", fallback="length", minimum=minimum, maximum=maximum)
        return None

    return _check


def min_length(n: int) -> ValidatorFn:
    """A string must be at least ``n`` characters long."""
    return length(minimum=n)


def max_length(n: int) -> ValidatorFn:
    """A string must be at most ``n`` characters long."""
    return length(maximum=n)


def integer(value: Any) -> str | None:
    """A value must be an integer (or a string of digits)."""
    if isinstance(value, bool):
        return RuleMessage("integer")
    if isinstance(value, int):
        return None
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return None
    return RuleMessage("integer")


def number(value: Any) -> str | None:
    """A value must be a number (int or float)."""
    if isinstance(value, bool):
        return RuleMessage("number")
    if isinstance(value, int):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else RuleMessage("number")
    if isinstance(value, str) and _NUMBER_RE.fullmatch(value.strip()):
        return None
    return RuleMessage("number")


def _in_range(low: float, high: float, key: str, fallback: str | None) -> ValidatorFn:
    """The check behind :func:`between`, :func:`min` and :func:`max`.

    They fail with the same English text, but their own rule ids, so a
    translation can say "at least {low}" for ``min`` and "between … and …" for
    ``between``.
    """

    def _check(value: Any) -> str | None:
        if value is None:
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return RuleMessage("number")
        if not (low <= num <= high):
            return RuleMessage(key, fallback=fallback, low=low, high=high)
        return None

    return _check


def between(low: float, high: float) -> ValidatorFn:
    """A numeric value must lie within [low, high]."""
    return _in_range(low, high, "between", None)


def min(low: float) -> ValidatorFn:
    """A numeric value must be at least ``low``."""
    return _in_range(low, float("inf"), "min", "between")


def max(high: float) -> ValidatorFn:
    """A numeric value must be at most ``high``."""
    return _in_range(float("-inf"), high, "max", "between")


def matches(other: str) -> ValidatorFn:
    """A string must equal ``other`` (useful for "confirm password").

    When used directly, ``other`` is compared as a literal value::

        matches("expected")(actual)  # returns None or error

    Inside :class:`Validator`, use ``{"matches": "field_name"}`` to compare
    against another field's value at validation time.
    """

    def _check(value: Any) -> str | None:
        if value != other:
            return RuleMessage("matches")
        return None

    return _check


class _MatchesField:
    """Internal wrapper: resolves the other field's value at validation time."""

    __slots__ = ("field_name",)

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name

    def __call__(self, value: Any, data: Mapping[str, Any] | None = None) -> str | None:
        if data is None:
            return None
        other_value = data.get(self.field_name)
        if _is_empty(value) and _is_empty(other_value):
            return None  # both left empty: nothing to confirm
        if value != other_value:
            return RuleMessage("matches_field", fallback="matches", field=self.field_name)
        return None


def one_of(choices: Sequence[Any]) -> ValidatorFn:
    """A value must be one of ``choices``."""
    allowed = tuple(choices)

    def _check(value: Any) -> str | None:
        if value not in allowed:
            rendered = ", ".join(str(c) for c in allowed)
            return RuleMessage("one_of", choices=rendered)
        return None

    return _check


def regex(pattern: str, message: str | None = None) -> ValidatorFn:
    """A string must match ``pattern``; ``message`` replaces the built-in text."""
    compiled = re.compile(pattern)

    def _check(value: Any) -> str | None:
        if not isinstance(value, str) or not compiled.fullmatch(value.strip()):
            return message or RuleMessage("regex", pattern=pattern)
        return None

    return _check


def boolean(value: Any) -> str | None:
    """A value must be a boolean (or a recognised boolean-like string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return None
    return RuleMessage("boolean")


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------
RuleSpec = ValidatorFn | str | Mapping[str, Any]
#: One rule or several: `"email"`, `["required", "email"]`, a callable, or a
#: one-key mapping such as `{"between": [1, 120]}`.
RuleSet = RuleSpec | Sequence[RuleSpec]
FieldRules = Mapping[str, RuleSet] | Sequence[tuple[str, RuleSet]]


def _as_rule_sequence(field: str, rules: RuleSet) -> Sequence[RuleSpec]:
    """Wrap a single rule in a list; keep a sequence as it is.

    Strings and mappings are sequences too, which is exactly how a bare
    ``"email"`` turned into the rules ``"e"``, ``"m"``, ``"a"``, ``"i"``,
    ``"l"``; a callable used to raise ``TypeError: not iterable``.
    """
    if isinstance(rules, (str, bytes, Mapping)) or callable(rules):
        return [rules]
    if not isinstance(rules, Sequence):
        raise ValueError(
            f"rules for {field!r} must be a rule or a sequence of rules, "
            f"got {type(rules).__name__!r}"
        )
    return rules


class Validator:
    """Combine per-field validators and run them over a data mapping.

    The constructor accepts either callable validators or the compact rule DSL
    used in the public documentation::

        Validator({
            "email": ["required", "email"],
            "age": ["optional", "integer", {"between": [1, 120]}],
        })

    Callable validators remain supported for advanced cases. The result maps
    every invalid field to its first human-readable error.

    ``messages`` replaces the text of built-in rules for this validator — the
    key is a rule id (``"required"``) or ``"field.rule"`` for a single field,
    the value a ``str.format`` template or a callable taking the rule's
    parameters::

        Validator(
            {"email": ["required", "email"], "name": [{"min_length": 2}]},
            messages={
                "required": "обов'язкове поле",
                "email.email": "невірна адреса пошти",
                "min_length": "не менше {minimum} символів",
            },
        )

    Without an override the text comes from the translation catalogue
    (``validation.<rule id>``) and finally from :data:`DEFAULT_MESSAGES`, so an
    application that translates once with ``translations`` gets every form
    translated. An unknown rule id is an error at construction, not a message
    that silently never shows.

    Empty values (None, ``""``, whitespace, empty collections):

    * ``required`` reports ``"is required"``;
    * ``optional`` lets the field stay empty — its other rules are skipped,
      except ``matches``, which still fails while the other field is filled
      (a password with an empty confirmation is not valid);
    * a field with neither is validated as is, so ``["email"]`` rejects ``""``.
    """

    __slots__ = ("_fields", "_messages")

    def __init__(
        self,
        fields: FieldRules = (),
        *,
        messages: Mapping[str, MessageOverride] | None = None,
    ) -> None:
        self._messages = self._check_messages(messages)
        self._fields = [
            (name, [self._resolve(rule) for rule in rules])
            for name, rules in self.normalize(fields).items()
        ]

    @staticmethod
    def _check_messages(
        messages: Mapping[str, MessageOverride] | None,
    ) -> dict[str, MessageOverride]:
        """Reject an override that could never apply (a typo in a rule id)."""
        checked: dict[str, MessageOverride] = {}
        known = sorted({*DEFAULT_MESSAGES, *_GROUP_IDS})
        for key, template in (messages or {}).items():
            rule_id = key.rsplit(".", 1)[-1]
            if rule_id not in DEFAULT_MESSAGES and rule_id not in _GROUP_IDS:
                raise ValueError(
                    f"unknown validation message key {key!r}: the rule id is {rule_id!r}; "
                    f"known ids: {', '.join(known)} (prefix one with 'field.' for a single field)"
                )
            if not isinstance(template, str) and not callable(template):
                raise TypeError(
                    f"the message for {key!r} must be a template string or a callable, "
                    f"got {type(template).__name__}"
                )
            checked[key] = template
        return checked

    @staticmethod
    def normalize(fields: FieldRules = ()) -> dict[str, list[RuleSpec]]:
        """Expand the accepted rule shapes into ``{field: [rule, …]}``.

        A single rule needs no list — ``Validator({"email": "email"})`` and
        ``Validator({"age": {"between": [1, 120]}})`` mean the same as their
        one-element list forms. Without this, a bare string was iterated
        character by character and failed with ``unknown validation rule: 'e'``.
        """
        entries = fields.items() if isinstance(fields, Mapping) else fields
        return {name: list(_as_rule_sequence(name, rules)) for name, rules in entries}

    @staticmethod
    def _resolve(rule: RuleSpec) -> ValidatorFn:
        if callable(rule):
            return rule
        if isinstance(rule, str):
            if ":" in rule:
                name, _separator, argument = rule.partition(":")
                if name in _ARGUMENT_RULES:
                    parsed: object = int(argument) if argument.isdecimal() else argument
                    raise ValueError(
                        f"validation rule {rule!r} uses colon syntax; rules with an argument "
                        f"must be a one-key mapping, e.g. {{{name!r}: {parsed!r}}}, "
                        f"not {rule!r}"
                    )
            lookup: dict[str, ValidatorFn] = {
                "required": required,
                "optional": optional,
                "email": email,
                "integer": integer,
                "number": number,
                "boolean": boolean,
            }
            try:
                return lookup[rule]
            except KeyError as exc:
                if rule in _ARGUMENT_RULES:
                    raise ValueError(
                        f"validation rule {rule!r} requires an argument; write it as a "
                        f"one-key mapping like {{{rule!r}: value}} — bare strings only "
                        f"work for: {', '.join(sorted(lookup))}"
                    ) from exc
                raise ValueError(f"unknown validation rule: {rule!r}") from exc
        if not isinstance(rule, Mapping) or len(rule) != 1:
            raise ValueError("a validation rule must be a callable, string, or one-key mapping")
        name, argument = next(iter(rule.items()))
        if name == "length":
            if isinstance(argument, (int, float)):
                return length(int(argument), int(argument))
            if not isinstance(argument, Mapping):
                raise ValueError(
                    "length rule expects an integer (exact length) or "
                    "{min: ..., max: ...} mapping; "
                    f"got {type(argument).__name__!r}"
                )
            return length(argument.get("min"), argument.get("max"))
        if name == "min_length":
            return min_length(int(argument))
        if name == "max_length":
            return max_length(int(argument))
        if name == "between":
            low, high = argument
            return between(float(low), float(high))
        if name == "min":
            return min(float(argument))
        if name == "max":
            return max(float(argument))
        if name == "matches":
            return _MatchesField(str(argument))
        if name == "one_of":
            if not isinstance(argument, Sequence) or isinstance(argument, str):
                raise ValueError("one_of rule expects a sequence")
            return one_of(argument)
        if name == "regex":
            return regex(str(argument))
        raise ValueError(f"unknown validation rule: {name!r}")

    def add(self, name: str, *validators: RuleSpec) -> None:
        """Register more validators for a field."""
        resolved = [self._resolve(rule) for rule in validators]
        for existing in self._fields:
            if existing[0] == name:
                existing[1].extend(resolved)
                return
        self._fields.append((name, resolved))

    def validate(self, data: Mapping[str, Any]) -> dict[str, str]:
        """Return a mapping of field name to first error message (empty when OK)."""
        errors: dict[str, str] = {}
        for name, validators in self._fields:
            value = data.get(name)
            skip_empty = _is_empty(value) and any(fn is optional for fn in validators)
            skip_empty = skip_empty and not any(fn is required for fn in validators)
            for fn in validators:
                if fn is optional:
                    continue
                if skip_empty and not isinstance(fn, _MatchesField):
                    continue
                message = (
                    fn(value, data) if isinstance(fn, _MatchesField) else fn(value)
                )
                if message is not None:
                    errors[name] = self._finish(name, message)
                    break
        return errors

    def _finish(self, field: str, message: str) -> str:
        """Apply this validator's overrides to a built-in rule's message."""
        if self._messages and isinstance(message, RuleMessage):
            return render_message(
                message.key,
                message.params,
                fallback=message.fallback,
                overrides=self._messages,
                field=field,
            )
        return str(message)  # always a plain str, never the RuleMessage subclass

    def validate_or_raise(self, data: Mapping[str, Any]) -> None:
        """Like :meth:`validate` but raises :class:`ValidationError` on failure."""
        errors = self.validate(data)
        if errors:
            raise ValidationError(errors)
