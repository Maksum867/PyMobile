"""Validator messages can be localised (roadmap item: no more hardcoded English).

A message is resolved, most specific first, from ``Validator(messages=…)``, the
translation catalogue (``validation.<rule id>``) and the English defaults.
"""

from __future__ import annotations

import gc
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from pymobile import App, Label, Screen
from pymobile.core.bridge import StubBridge
from pymobile.core.i18n import Translations, t, translations
from pymobile.core.validation import (
    DEFAULT_MESSAGES,
    RuleMessage,
    Validator,
    between,
    boolean,
    email,
    integer,
    length,
    matches,
    max,
    min,
    number,
    one_of,
    regex,
    required,
)


class Collector(logging.Handler):
    """Keeps the text of every warning it is handed."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def pymobile_log() -> Iterator[list[str]]:
    """What the catalogue and the validator log during the test.

    ``caplog`` cannot be used for this, and neither can a handler on the ``pymobile``
    logger or on the root: ``configure()``, which every running ``App`` calls, switches
    propagation off and removes the handlers of ``pymobile``. So whether such a check
    hears anything depends on what ran before it (alone it passes vacuously). Handlers
    on the two child loggers survive all of that.
    """
    collector = Collector()
    loggers = [logging.getLogger(name) for name in ("pymobile.i18n", "pymobile.validation")]
    for logger in loggers:
        logger.addHandler(collector)
    yield collector.messages
    for logger in loggers:
        logger.removeHandler(collector)


@pytest.fixture
def catalogue() -> Iterator[Translations]:
    """The global catalogue, emptied before and after the test.

    An ``App`` that some other test started and never stopped stays subscribed to the
    catalogue until the garbage collector frees it (the listener is a weak reference),
    and re-renders its screens on every ``use()``. Collecting first keeps such leftovers
    out of these tests; when one is still alive, they must not depend on it (see below).
    """
    gc.collect()
    translations.clear()
    yield translations
    translations.clear()


# --------------------------------------------------------------------------
# The English defaults did not move
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("check", "value", "expected"),
    [
        (required, "", "is required"),
        (email, "nope", "must be a valid email address"),
        (length(minimum=2), "a", "must be at least 2 characters"),
        (length(maximum=2), "abc", "must be at most 2 characters"),
        (integer, "x", "must be an integer"),
        (number, "x", "must be a number"),
        (between(1, 5), 9, "must be between 1 and 5"),
        (between(1, 5), "x", "must be a number"),
        (min(5), 1, "must be between 5 and inf"),
        (max(5), 9, "must be between -inf and 5"),
        (matches("a"), "b", "does not match"),
        (one_of(["a", "b"]), "c", "must be one of: a, b"),
        (regex(r"\d+"), "x", r"must match '\\d+'"),
        (boolean, "maybe", "must be a boolean"),
    ],
)
def test_default_english_text_is_byte_identical(check: Any, value: Any, expected: str) -> None:
    assert check(value) == expected


def test_rule_functions_still_return_plain_strings() -> None:
    message = required("")
    assert isinstance(message, str)
    assert message == "is required"
    assert message.upper() == "IS REQUIRED"
    assert isinstance(message, RuleMessage)
    assert message.key == "required"


def test_validate_reports_plain_str_not_the_subclass() -> None:
    errors = Validator({"a": ["required"]}).validate({})
    assert type(errors["a"]) is str
    errors = Validator({"a": ["required"]}, messages={"required": "x"}).validate({})
    assert type(errors["a"]) is str


def test_every_default_has_only_known_placeholders() -> None:
    """A stray placeholder would crash a translator's copy-paste, so pin them."""
    allowed = {
        "min_length": {"minimum", "maximum"},
        "max_length": {"minimum", "maximum"},
        "between": {"low", "high"},
        "min": {"low", "high"},
        "max": {"low", "high"},
        "matches_field": {"field"},
        "one_of": {"choices"},
        "regex": {"pattern"},
    }
    import string

    for rule_id, template in DEFAULT_MESSAGES.items():
        names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        assert names <= allowed.get(rule_id, set()), rule_id


# --------------------------------------------------------------------------
# Validator(messages=…)
# --------------------------------------------------------------------------
def test_messages_replace_a_rule_for_every_field() -> None:
    validator = Validator(
        {"name": ["required"], "city": ["required"]},
        messages={"required": "обов'язкове поле"},
    )
    assert validator.validate({}) == {
        "name": "обов'язкове поле",
        "city": "обов'язкове поле",
    }


def test_a_field_specific_message_beats_a_rule_wide_one() -> None:
    validator = Validator(
        {"name": ["required"], "city": ["required"]},
        messages={"required": "is needed", "city.required": "pick a city"},
    )
    assert validator.validate({}) == {"name": "is needed", "city": "pick a city"}


def test_templates_receive_the_rule_parameters() -> None:
    validator = Validator(
        {
            "name": [{"min_length": 3}],
            "age": [{"between": [1, 120]}],
            "role": [{"one_of": ["admin", "user"]}],
            "confirm": [{"matches": "password"}],
        },
        messages={
            "min_length": "не менше {minimum} символів",
            "between": "від {low:g} до {high:g}",
            "one_of": "одне з: {choices}",
            "matches": "інше, ніж у {field}",
        },
    )
    errors = validator.validate({"name": "ab", "age": 300, "role": "x", "confirm": "1"})
    assert errors == {
        "name": "не менше 3 символів",
        "age": "від 1 до 120",
        "role": "одне з: admin, user",
        "confirm": "інше, ніж у password",
    }


def test_a_callable_message_gets_keyword_parameters() -> None:
    def plural(*, minimum: int, **_: Any) -> str:
        return f"мінімум {minimum} {'символ' if minimum == 1 else 'символи'}"

    validator = Validator(
        {"a": [{"min_length": 1}], "b": [{"min_length": 2}]}, messages={"min_length": plural}
    )
    assert validator.validate({"a": "", "b": "x"}) == {
        "a": "мінімум 1 символ",
        "b": "мінімум 2 символи",
    }


def test_length_covers_both_min_and_max_and_the_specific_id_wins() -> None:
    validator = Validator(
        {"a": [{"length": {"min": 2, "max": 4}}]},
        messages={"length": "довжина {minimum}-{maximum}"},
    )
    assert validator.validate({"a": "x"}) == {"a": "довжина 2-4"}
    assert validator.validate({"a": "xxxxxx"}) == {"a": "довжина 2-4"}
    specific = Validator(
        {"a": [{"length": {"min": 2, "max": 4}}]},
        messages={"length": "any", "max_length": "too long"},
    )
    assert specific.validate({"a": "x"}) == {"a": "any"}
    assert specific.validate({"a": "xxxxxx"}) == {"a": "too long"}


def test_min_and_max_can_be_worded_apart_from_between() -> None:
    validator = Validator(
        {"a": [{"min": 5}], "b": [{"max": 5}], "c": [{"between": [1, 2]}]},
        messages={"min": "не менше {low:g}", "max": "не більше {high:g}"},
    )
    assert validator.validate({"a": 1, "b": 9, "c": 9}) == {
        "a": "не менше 5",
        "b": "не більше 5",
        "c": "must be between 1.0 and 2.0",
    }
    # "between" alone is the fallback for min and max.
    fallback = Validator({"a": [{"min": 5}]}, messages={"between": "out of range"})
    assert fallback.validate({"a": 1}) == {"a": "out of range"}


def test_matches_override_covers_the_field_form_and_the_literal_form() -> None:
    validator = Validator(
        {"confirm": [{"matches": "password"}]}, messages={"matches": "паролі різні"}
    )
    assert validator.validate({"password": "a", "confirm": "b"}) == {"confirm": "паролі різні"}
    literal = Validator({"x": [matches("a")]}, messages={"matches": "інше"})
    assert literal.validate({"x": "b"}) == {"x": "інше"}
    specific = Validator(
        {"confirm": [{"matches": "password"}]},
        messages={"matches": "generic", "matches_field": "vs {field}"},
    )
    assert specific.validate({"password": "a", "confirm": "b"}) == {"confirm": "vs password"}


def test_messages_do_not_touch_a_custom_validators_own_text() -> None:
    def even(value: Any) -> str | None:
        return None if int(value) % 2 == 0 else "must be even"

    validator = Validator({"n": [even]}, messages={"required": "x", "integer": "y"})
    assert validator.validate({"n": 3}) == {"n": "must be even"}


def test_regex_keeps_its_explicit_message_and_can_be_overridden_otherwise() -> None:
    explicit = Validator({"z": [regex(r"\d{5}", "five digits")]}, messages={"regex": "ignored"})
    assert explicit.validate({"z": "1"}) == {"z": "five digits"}
    default = Validator({"z": [{"regex": r"\d{5}"}]}, messages={"regex": "формат {pattern}"})
    assert default.validate({"z": "1"}) == {"z": r"формат \d{5}"}


def test_an_unknown_message_key_fails_fast() -> None:
    with pytest.raises(ValueError, match="unknown validation message key 'requird'"):
        Validator({"a": ["required"]}, messages={"requird": "typo"})
    with pytest.raises(ValueError, match=r"unknown validation message key 'a\.nope'"):
        Validator({"a": ["required"]}, messages={"a.nope": "typo"})


def test_a_message_must_be_text_or_callable() -> None:
    with pytest.raises(TypeError, match="template string or a callable"):
        Validator({"a": ["required"]}, messages={"required": 5})  # type: ignore[dict-item]


def test_a_broken_template_degrades_to_its_own_text(pymobile_log: list[str]) -> None:
    validator = Validator({"a": [{"min_length": 3}]}, messages={"min_length": "нужно {nope}"})
    assert validator.validate({"a": "x"}) == {"a": "нужно {nope}"}
    assert any("could not format" in message for message in pymobile_log)


def test_messages_apply_to_fields_added_later() -> None:
    validator = Validator(messages={"required": "потрібно"})
    validator.add("name", "required")
    assert validator.validate({}) == {"name": "потрібно"}


def test_validate_or_raise_carries_the_localised_text() -> None:
    from pymobile.core.validation import ValidationError

    validator = Validator({"a": ["required"]}, messages={"required": "потрібно"})
    with pytest.raises(ValidationError) as caught:
        validator.validate_or_raise({})
    assert caught.value.errors == {"a": "потрібно"}
    assert "потрібно" in str(caught.value)


# --------------------------------------------------------------------------
# The translation catalogue
# --------------------------------------------------------------------------
UK = {
    "validation.required": "обов'язкове поле",
    "validation.email": "невірна адреса пошти",
    "validation.min_length": "не менше {minimum} символів",
}


def test_catalogue_keys_translate_every_validator(catalogue: Translations) -> None:
    catalogue.load(UK, language="uk")
    catalogue.use("uk")
    validator = Validator({"e": ["required", "email"], "n": [{"min_length": 3}]})
    assert validator.validate({"e": "", "n": "ab"}) == {
        "e": "обов'язкове поле",
        "n": "не менше 3 символів",
    }
    assert validator.validate({"e": "nope", "n": "abc"}) == {"e": "невірна адреса пошти"}


def test_the_message_follows_the_language_at_validation_time(catalogue: Translations) -> None:
    catalogue.load(UK, language="uk")
    validator = Validator({"e": ["required"]})  # built before the switch
    assert validator.validate({}) == {"e": "is required"}
    catalogue.use("uk")
    assert validator.validate({}) == {"e": "обов'язкове поле"}
    catalogue.use("en")
    assert validator.validate({}) == {"e": "is required"}


class NeedsGreeting(Screen):
    """A screen whose key the catalogue of these tests does not have."""

    def build(self) -> Label:
        return Label(t("greeting"))


def test_the_log_probe_sees_a_missing_validation_key(
    catalogue: Translations, pymobile_log: list[str]
) -> None:
    """Without this, "nothing was reported" below could be a probe that hears nothing."""
    catalogue.load({"other": "x"}, language="uk")
    catalogue.use("uk")
    t("validation.no_such_rule")
    assert any("validation.no_such_rule" in message for message in pymobile_log)


@pytest.mark.parametrize("leftover_app", [False, True], ids=["alone", "with-a-leftover-app"])
def test_an_untranslated_rule_stays_english_and_is_not_reported_missing(
    catalogue: Translations, pymobile_log: list[str], bridge: StubBridge, leftover_app: bool
) -> None:
    # A test that starts an App and never stops it leaves it subscribed to the catalogue
    # until the garbage collector frees it; it then re-renders, and reports its own
    # missing keys, whenever the language is switched. This test must not care.
    leftover = App("Leftover", bridge=bridge) if leftover_app else None
    if leftover is not None:
        leftover.run(NeedsGreeting())
    try:
        catalogue.load({"validation.required": "обов'язкове поле"}, language="uk")
        catalogue.use("uk")
        validator = Validator({"n": [{"min_length": 3}], "i": ["integer"]})
        errors = validator.validate({"n": "a", "i": "x"})
    finally:
        if leftover is not None:
            leftover.stop()
    assert errors == {"n": "must be at least 3 characters", "i": "must be an integer"}
    if leftover is not None:  # the point of the parameter: the noise really was there
        assert "missing translation for 'greeting' in 'uk'" in pymobile_log
    # Only the validator's own keys count; the leftover screen's 'greeting' does not.
    assert [message for message in pymobile_log if "validation." in message] == []


def test_messages_beat_the_catalogue(catalogue: Translations) -> None:
    catalogue.load(UK, language="uk")
    catalogue.use("uk")
    validator = Validator({"e": ["required"], "f": ["required"]}, messages={"f.required": "поле F"})
    assert validator.validate({}) == {"e": "обов'язкове поле", "f": "поле F"}


def test_the_catalogue_supports_the_general_id_for_the_specific_rule(
    catalogue: Translations,
) -> None:
    catalogue.load({"validation.matches": "не збігається"}, language="uk")
    catalogue.use("uk")
    validator = Validator({"c": [{"matches": "p"}]})
    assert validator.validate({"p": "1", "c": "2"}) == {"c": "не збігається"}


def test_bare_rule_functions_are_translated_too(catalogue: Translations) -> None:
    catalogue.load(UK, language="uk")
    catalogue.use("uk")
    assert required("") == "обов'язкове поле"
    assert length(minimum=5)("ab") == "не менше 5 символів"


def test_the_default_language_catalogue_is_the_fallback(catalogue: Translations) -> None:
    catalogue.load({"validation.required": "Please fill this in"}, language="en")
    catalogue.use("uk")  # no uk catalogue at all: falls back to en
    assert Validator({"a": ["required"]}).validate({}) == {"a": "Please fill this in"}
