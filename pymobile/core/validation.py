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
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..errors import PyMobileError

__all__ = [
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

#: A validator: takes a value and returns None (ok) or an error message.
ValidatorFn = Callable[[Any], str | None]

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
    return "is required" if _is_empty(value) else None


def optional(value: Any) -> str | None:
    """Allow the field to be left empty; its other rules run only when filled.

    Handled by :class:`Validator`: a field WITHOUT ``optional`` is validated
    even when empty, so ``["email"]`` rejects ``""``. Never fails itself.
    """
    return None


def email(value: Any) -> str | None:
    """A value must look like an email address."""
    if not isinstance(value, str) or not _EMAIL_RE.match(value.strip()):
        return "must be a valid email address"
    return None


def length(minimum: int | None = None, maximum: int | None = None) -> ValidatorFn:
    """A string's length must fall within [minimum, maximum]."""

    def _check(value: Any) -> str | None:
        if value is None:
            return None
        size = len(value)
        if minimum is not None and size < minimum:
            return f"must be at least {minimum} characters"
        if maximum is not None and size > maximum:
            return f"must be at most {maximum} characters"
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
        return "must be an integer"
    if isinstance(value, int):
        return None
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return None
    return "must be an integer"


def number(value: Any) -> str | None:
    """A value must be a number (int or float)."""
    if isinstance(value, bool):
        return "must be a number"
    if isinstance(value, int):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else "must be a number"
    if isinstance(value, str) and _NUMBER_RE.fullmatch(value.strip()):
        return None
    return "must be a number"


def between(low: float, high: float) -> ValidatorFn:
    """A numeric value must lie within [low, high]."""

    def _check(value: Any) -> str | None:
        if value is None:
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "must be a number"
        if not (low <= num <= high):
            return f"must be between {low} and {high}"
        return None

    return _check


def min(low: float) -> ValidatorFn:
    """A numeric value must be at least ``low``."""
    return between(low, float("inf"))


def max(high: float) -> ValidatorFn:
    """A numeric value must be at most ``high``."""
    return between(float("-inf"), high)


def matches(other: str) -> ValidatorFn:
    """A string must equal ``other`` (useful for "confirm password").

    When used directly, ``other`` is compared as a literal value::

        matches("expected")(actual)  # returns None or error

    Inside :class:`Validator`, use ``{"matches": "field_name"}`` to compare
    against another field's value at validation time.
    """

    def _check(value: Any) -> str | None:
        if value != other:
            return "does not match"
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
            return f"does not match {self.field_name!r}"
        return None


def one_of(choices: Sequence[Any]) -> ValidatorFn:
    """A value must be one of ``choices``."""
    allowed = tuple(choices)

    def _check(value: Any) -> str | None:
        if value not in allowed:
            rendered = ", ".join(str(c) for c in allowed)
            return f"must be one of: {rendered}"
        return None

    return _check


def regex(pattern: str, message: str | None = None) -> ValidatorFn:
    """A string must match ``pattern``."""
    compiled = re.compile(pattern)

    def _check(value: Any) -> str | None:
        if not isinstance(value, str) or not compiled.fullmatch(value.strip()):
            return message or f"must match {pattern!r}"
        return None

    return _check


def boolean(value: Any) -> str | None:
    """A value must be a boolean (or a recognised boolean-like string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return None
    return "must be a boolean"


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

    Empty values (None, ``""``, whitespace, empty collections):

    * ``required`` reports ``"is required"``;
    * ``optional`` lets the field stay empty — its other rules are skipped,
      except ``matches``, which still fails while the other field is filled
      (a password with an empty confirmation is not valid);
    * a field with neither is validated as is, so ``["email"]`` rejects ``""``.
    """

    __slots__ = ("_fields",)

    def __init__(self, fields: FieldRules = ()) -> None:
        self._fields = [
            (name, [self._resolve(rule) for rule in rules])
            for name, rules in self.normalize(fields).items()
        ]

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
                    errors[name] = message
                    break
        return errors

    def validate_or_raise(self, data: Mapping[str, Any]) -> None:
        """Like :meth:`validate` but raises :class:`ValidationError` on failure."""
        errors = self.validate(data)
        if errors:
            raise ValidationError(errors)
