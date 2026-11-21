"""Locale-aware formatting of numbers, money, dates and times.

A mobile app shows prices, totals and dates on every other screen, and the
way they are written differs by language far more than people expect::

    format_number(1234.5)            # en "1,234.5"    uk "1 234,5"    de "1.234,5"
    format_currency(99, "UAH")       # en "₴99.00"     uk "99,00 ₴"
    format_date(date(2026, 9, 26), "long")
                                     # en "September 26, 2026"
                                     # uk "26 вересня 2026 р."
    format_percent(0.25)             # en "25%"        de "25 %"

The rules come from CLDR (the data behind Android, ICU and every browser),
trimmed to a small table built into the framework — no dependency, and the
same output on the desktop preview, in tests and on the phone. Languages
without an entry fall back to English conventions.

Everything uses the current :data:`~pymobile.core.i18n.translations` language
unless ``language=`` says otherwise, so switching ``translations.use("uk")``
switches the formats too.

The same formats are available inside translated strings through format
specs, so a catalogue entry decides where the number goes::

    translations.load({"total": "Разом: {sum:currency:UAH}, до {day:date:long}"},
                      language="uk")
    t("total", sum=1250, day=date(2026, 10, 1))
    # "Разом: 1 250,00 ₴, до 1 жовтня 2026 р."
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from ..deprecation import warn_deprecated

__all__ = [
    "format_number",
    "format_percent",
    "format_currency",
    "format_date",
    "format_time",
    "format_datetime",
    "format_bytes",
    "format_relative_time",
    "format_list",
    "supported_format_languages",
]

NBSP = "\u00a0"
NNBSP = "\u202f"  # narrow no-break space (French grouping)


@dataclass(frozen=True)
class _Locale:
    """The CLDR bits one language needs."""

    decimal: str = "."
    group: str = ","
    #: CLDR minimumGroupingDigits: 2 means 4-digit numbers are not grouped.
    min_grouping: int = 1
    #: ``{n}`` is the number, ``%`` stays literal.
    percent: str = "{n}%"
    #: ``{n}`` the amount, ``{s}`` the currency symbol.
    currency: str = "{s}{n}"
    date: Mapping[str, str] = field(default_factory=dict)
    time: Mapping[str, str] = field(default_factory=dict)
    #: ``{date}`` / ``{time}``.
    datetime: str = "{date}, {time}"
    months: tuple[str, ...] = ()
    #: Month names as used inside a date ("26 вересня"), when they differ.
    months_genitive: tuple[str, ...] = ()
    months_short: tuple[str, ...] = ()
    weekdays: tuple[str, ...] = ()  # Monday first
    am_pm: tuple[str, str] = ("AM", "PM")


_EN = _Locale(
    date={
        "short": "M/d/yy",
        "medium": "MMM d, y",
        "long": "MMMM d, y",
        "full": "EEEE, MMMM d, y",
    },
    time={"short": "h:mm a", "medium": "h:mm:ss a"},
    months=(
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    months_short=(
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ),
    weekdays=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
)

_H24 = {"short": "HH:mm", "medium": "HH:mm:ss"}

_UK = _Locale(
    decimal=",",
    group=NBSP,
    percent="{n}%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd.MM.yy",
        "medium": "d MMM y 'р.'",
        "long": "d MMMM y 'р.'",
        "full": "EEEE, d MMMM y 'р.'",
    },
    time=_H24,
    months=(
        "січень",
        "лютий",
        "березень",
        "квітень",
        "травень",
        "червень",
        "липень",
        "серпень",
        "вересень",
        "жовтень",
        "листопад",
        "грудень",
    ),
    months_genitive=(
        "січня",
        "лютого",
        "березня",
        "квітня",
        "травня",
        "червня",
        "липня",
        "серпня",
        "вересня",
        "жовтня",
        "листопада",
        "грудня",
    ),
    months_short=(
        "січ.",
        "лют.",
        "бер.",
        "квіт.",
        "трав.",
        "черв.",
        "лип.",
        "серп.",
        "вер.",
        "жовт.",
        "лист.",
        "груд.",
    ),
    weekdays=("понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"),
)

_RU = _Locale(
    decimal=",",
    group=NBSP,
    percent="{n}" + NBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd.MM.y",
        "medium": "d MMM y 'г.'",
        "long": "d MMMM y 'г.'",
        "full": "EEEE, d MMMM y 'г.'",
    },
    time=_H24,
    months=(
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    ),
    months_genitive=(
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ),
    months_short=(
        "янв.",
        "февр.",
        "мар.",
        "апр.",
        "мая",
        "июн.",
        "июл.",
        "авг.",
        "сент.",
        "окт.",
        "нояб.",
        "дек.",
    ),
    weekdays=(
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    ),
)

_PL = _Locale(
    decimal=",",
    group=NBSP,
    min_grouping=2,
    percent="{n}%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "d.MM.y",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "EEEE, d MMMM y",
    },
    time=_H24,
    months=(
        "styczeń",
        "luty",
        "marzec",
        "kwiecień",
        "maj",
        "czerwiec",
        "lipiec",
        "sierpień",
        "wrzesień",
        "październik",
        "listopad",
        "grudzień",
    ),
    months_genitive=(
        "stycznia",
        "lutego",
        "marca",
        "kwietnia",
        "maja",
        "czerwca",
        "lipca",
        "sierpnia",
        "września",
        "października",
        "listopada",
        "grudnia",
    ),
    months_short=(
        "sty",
        "lut",
        "mar",
        "kwi",
        "maj",
        "cze",
        "lip",
        "sie",
        "wrz",
        "paź",
        "lis",
        "gru",
    ),
    weekdays=("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"),
)

_CS = _Locale(
    decimal=",",
    group=NBSP,
    percent="{n}" + NBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd.MM.yy",
        "medium": "d. M. y",
        "long": "d. MMMM y",
        "full": "EEEE d. MMMM y",
    },
    time={"short": "H:mm", "medium": "H:mm:ss"},
    months=(
        "leden",
        "únor",
        "březen",
        "duben",
        "květen",
        "červen",
        "červenec",
        "srpen",
        "září",
        "říjen",
        "listopad",
        "prosinec",
    ),
    months_genitive=(
        "ledna",
        "února",
        "března",
        "dubna",
        "května",
        "června",
        "července",
        "srpna",
        "září",
        "října",
        "listopadu",
        "prosince",
    ),
    months_short=(
        "led",
        "úno",
        "bře",
        "dub",
        "kvě",
        "čvn",
        "čvc",
        "srp",
        "zář",
        "říj",
        "lis",
        "pro",
    ),
    weekdays=("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"),
)

_DE = _Locale(
    decimal=",",
    group=".",
    percent="{n}" + NBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd.MM.yy",
        "medium": "dd.MM.y",
        "long": "d. MMMM y",
        "full": "EEEE, d. MMMM y",
    },
    time=_H24,
    months=(
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ),
    months_short=(
        "Jan.",
        "Feb.",
        "März",
        "Apr.",
        "Mai",
        "Juni",
        "Juli",
        "Aug.",
        "Sept.",
        "Okt.",
        "Nov.",
        "Dez.",
    ),
    weekdays=("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
)

_FR = _Locale(
    decimal=",",
    group=NNBSP,
    percent="{n}" + NNBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd/MM/y",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "EEEE d MMMM y",
    },
    time=_H24,
    datetime="{date} {time}",
    months=(
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    months_short=(
        "janv.",
        "févr.",
        "mars",
        "avr.",
        "mai",
        "juin",
        "juil.",
        "août",
        "sept.",
        "oct.",
        "nov.",
        "déc.",
    ),
    weekdays=("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
)

_ES = _Locale(
    decimal=",",
    group=".",
    min_grouping=2,
    percent="{n}" + NBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "d/M/yy",
        "medium": "d MMM y",
        "long": "d 'de' MMMM 'de' y",
        "full": "EEEE, d 'de' MMMM 'de' y",
    },
    time={"short": "H:mm", "medium": "H:mm:ss"},
    months=(
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    months_short=(
        "ene",
        "feb",
        "mar",
        "abr",
        "may",
        "jun",
        "jul",
        "ago",
        "sept",
        "oct",
        "nov",
        "dic",
    ),
    weekdays=("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
)

_IT = _Locale(
    decimal=",",
    group=".",
    percent="{n}%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd/MM/yy",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "EEEE d MMMM y",
    },
    time=_H24,
    months=(
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ),
    months_short=(
        "gen",
        "feb",
        "mar",
        "apr",
        "mag",
        "giu",
        "lug",
        "ago",
        "set",
        "ott",
        "nov",
        "dic",
    ),
    weekdays=("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"),
)

_PT = _Locale(  # Brazilian Portuguese, the CLDR default for "pt"
    decimal=",",
    group=".",
    percent="{n}%",
    currency="{s}" + NBSP + "{n}",
    date={
        "short": "dd/MM/y",
        "medium": "d 'de' MMM 'de' y",
        "long": "d 'de' MMMM 'de' y",
        "full": "EEEE, d 'de' MMMM 'de' y",
    },
    time=_H24,
    datetime="{date} {time}",
    months=(
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    months_short=(
        "jan.",
        "fev.",
        "mar.",
        "abr.",
        "mai.",
        "jun.",
        "jul.",
        "ago.",
        "set.",
        "out.",
        "nov.",
        "dez.",
    ),
    weekdays=(
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ),
)

_NL = _Locale(
    decimal=",",
    group=".",
    percent="{n}%",
    currency="{s}" + NBSP + "{n}",
    date={
        "short": "dd-MM-y",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "EEEE d MMMM y",
    },
    time=_H24,
    datetime="{date} {time}",
    months=(
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ),
    months_short=(
        "jan",
        "feb",
        "mrt",
        "apr",
        "mei",
        "jun",
        "jul",
        "aug",
        "sep",
        "okt",
        "nov",
        "dec",
    ),
    weekdays=("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
)

_RO = _Locale(
    decimal=",",
    group=".",
    percent="{n}" + NBSP + "%",
    currency="{n}" + NBSP + "{s}",
    date={
        "short": "dd.MM.y",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "EEEE, d MMMM y",
    },
    time=_H24,
    months=(
        "ianuarie",
        "februarie",
        "martie",
        "aprilie",
        "mai",
        "iunie",
        "iulie",
        "august",
        "septembrie",
        "octombrie",
        "noiembrie",
        "decembrie",
    ),
    months_short=(
        "ian.",
        "feb.",
        "mar.",
        "apr.",
        "mai",
        "iun.",
        "iul.",
        "aug.",
        "sept.",
        "oct.",
        "nov.",
        "dec.",
    ),
    weekdays=("luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"),
)

_TR = _Locale(
    decimal=",",
    group=".",
    percent="%{n}",
    currency="{s}{n}",
    date={
        "short": "d.MM.y",
        "medium": "d MMM y",
        "long": "d MMMM y",
        "full": "d MMMM y EEEE",
    },
    time=_H24,
    datetime="{date} {time}",
    months=(
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    ),
    months_short=(
        "Oca",
        "Şub",
        "Mar",
        "Nis",
        "May",
        "Haz",
        "Tem",
        "Ağu",
        "Eyl",
        "Eki",
        "Kas",
        "Ara",
    ),
    weekdays=("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"),
    am_pm=("ÖÖ", "ÖS"),
)

_LOCALES: dict[str, _Locale] = {
    "en": _EN,
    "en-GB": replace(
        _EN,
        date={
            "short": "dd/MM/y",
            "medium": "d MMM y",
            "long": "d MMMM y",
            "full": "EEEE d MMMM y",
        },
        time=_H24,
    ),
    "en-AU": replace(
        _EN,
        date={
            "short": "d/M/yy",
            "medium": "d MMM y",
            "long": "d MMMM y",
            "full": "EEEE d MMMM y",
        },
    ),
    "en-CA": replace(
        _EN,
        date={
            "short": "y-MM-dd",
            "medium": "MMM d, y",
            "long": "MMMM d, y",
            "full": "EEEE, MMMM d, y",
        },
    ),
    "en-IE": replace(
        _EN,
        date={
            "short": "dd/MM/y",
            "medium": "d MMM y",
            "long": "d MMMM y",
            "full": "EEEE d MMMM y",
        },
        time=_H24,
    ),
    "uk": _UK,
    "ru": _RU,
    "pl": _PL,
    "cs": _CS,
    "de": _DE,
    "de-AT": replace(_DE, group=NBSP),
    "de-CH": replace(_DE, decimal=".", group="’", percent="{n}%", currency="{s}" + NBSP + "{n}"),
    "fr": _FR,
    "fr-CA": replace(
        _FR,
        group=NBSP,
        date={**_FR.date, "short": "y-MM-dd"},
    ),
    "fr-CH": replace(_FR, date={**_FR.date, "short": "dd.MM.yy"}),
    "es": _ES,
    "es-MX": replace(_ES, decimal=".", group=",", min_grouping=1, currency="{s}{n}"),
    "es-US": replace(_ES, decimal=".", group=",", min_grouping=1, currency="{s}{n}"),
    "it": _IT,
    "pt": _PT,
    "pt-PT": replace(
        _PT,
        group=NBSP,
        min_grouping=2,
        currency="{n}" + NBSP + "{s}",
        date={**_PT.date, "short": "dd/MM/yy"},
    ),
    "nl": _NL,
    "ro": _RO,
    "tr": _TR,
}

#: Narrow symbols; a currency not listed is written with its ISO code.
_SYMBOLS: dict[str, str] = {
    "UAH": "₴",
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "KRW": "₩",
    "ILS": "₪",
    "NGN": "₦",
    "PHP": "₱",
    "VND": "₫",
    "TRY": "₺",
    "PLN": "zł",
    "CZK": "Kč",
    "RUB": "₽",
    "KZT": "₸",
    "GEL": "₾",
    "BRL": "R$",
    "CAD": "$",
    "AUD": "$",
    "MXN": "$",
    "CHF": "CHF",
    "RON": "lei",
}

#: ISO 4217 minor units that are not 2.
_CURRENCY_DECIMALS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "HUF": 2,
    "KWD": 3,
    "BHD": 3,
    "JOD": 3,
    "OMR": 3,
    "TND": 3,
}


#: Lookup table keyed like normalise_language() output ("en-gb").
_BY_TAG: dict[str, _Locale] = {tag.lower(): locale for tag, locale in _LOCALES.items()}


def supported_format_languages() -> tuple[str, ...]:
    """Language tags with built-in formats (others use English conventions)."""
    return tuple(sorted(_LOCALES))


def _current_language() -> str:
    from .i18n import translations

    return translations.language


def _language_argument(language: str | None, locale_alias: str | None) -> str | None:
    """Resolve the old ``locale=`` spelling while keeping ``language=`` canonical."""
    if locale_alias is None:
        return language
    if language is not None:
        raise TypeError("pass either language= or locale=, not both")
    if not isinstance(locale_alias, str):
        raise TypeError(f"locale= must be a language tag string, got {type(locale_alias).__name__}")
    warn_deprecated(
        "locale=",
        "language=",
        since="0.9.3",
        removal="1.0.0",
        stacklevel=3,
    )
    return locale_alias


def _locale(language: str | None) -> _Locale:
    """The formats of ``language`` (``uk``, ``en-GB``, ``pt_BR`` …)."""
    from .i18n import normalise_language

    tag = normalise_language(language or _current_language())
    if tag in _BY_TAG:
        return _BY_TAG[tag]
    return _BY_TAG.get(tag.split("-")[0], _EN)


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------
def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("cannot format a bool as a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr() is the shortest string that round-trips, so 0.1 stays 0.1
        # instead of 0.1000000000000000055511151231257827.
        return Decimal(repr(value))
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise TypeError(f"cannot format {value!r} as a number") from exc


def _group(digits: str, separator: str, min_grouping: int) -> str:
    if len(digits) < 4 or len(digits) < 3 + min_grouping:
        return digits
    head, tail = digits[: len(digits) % 3 or 3], digits[len(digits) % 3 or 3 :]
    parts = [head] + [tail[i : i + 3] for i in range(0, len(tail), 3)]
    return separator.join(parts)


def _digits(
    value: Any,
    locale: _Locale,
    *,
    decimals: int | None,
    max_decimals: int = 3,
    grouping: bool = True,
) -> tuple[bool, str]:
    """``(negative, "1 234,5")`` — the unsigned number in ``locale``."""
    if decimals is not None and decimals < 0:
        raise ValueError("decimals must not be negative")
    number = _to_decimal(value)
    if number.is_nan():
        return False, "NaN"
    if number.is_infinite():
        return number < 0, "∞"
    places = decimals if decimals is not None else max_decimals
    quantum = Decimal(1).scaleb(-places)
    rounded = number.quantize(quantum, rounding=ROUND_HALF_EVEN)
    negative = rounded < 0
    text = f"{abs(rounded):f}"
    whole, _, fraction = text.partition(".")
    if decimals is None:
        fraction = fraction.rstrip("0")
    if grouping:
        whole = _group(whole, locale.group, locale.min_grouping)
    return negative, whole + (locale.decimal + fraction if fraction else "")


def format_number(
    value: float | int | Decimal,
    decimals: int | None = None,
    *,
    language: str | None = None,
    locale: str | None = None,
    grouping: bool = True,
) -> str:
    """Write a number the way ``language`` does: ``1234.5`` → ``1 234,5`` (uk).

    ``decimals`` fixes the number of fraction digits (rounded half-to-even,
    like CLDR); by default up to three are shown and trailing zeros dropped.
    ``grouping=False`` leaves out the thousands separator (for years, PINs…).
    """
    language = _language_argument(language, locale)
    formats = _locale(language)
    negative, text = _digits(value, formats, decimals=decimals, grouping=grouping)
    return ("-" if negative else "") + text


def format_percent(
    value: float | int | Decimal,
    decimals: int = 0,
    *,
    language: str | None = None,
    locale: str | None = None,
) -> str:
    """A ratio as a percentage: ``0.256`` → ``26%`` (en), ``26 %`` (de)."""
    language = _language_argument(language, locale)
    formats = _locale(language)
    negative, text = _digits(_to_decimal(value) * 100, formats, decimals=decimals)
    return ("-" if negative else "") + formats.percent.format(n=text)


def format_currency(
    amount: float | int | Decimal,
    currency: str,
    *,
    language: str | None = None,
    locale: str | None = None,
    decimals: int | None = None,
    symbol: bool = True,
) -> str:
    """Money in ``language``'s layout: ``format_currency(99, "UAH")`` → ``99,00 ₴`` (uk).

    ``currency`` is an ISO 4217 code. Its usual number of decimals is used
    (``JPY`` has none) unless ``decimals`` says otherwise. ``symbol=False``
    writes the code instead of the symbol: ``99,00 UAH``.
    """
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"currency must be an ISO 4217 code such as 'UAH', got {currency!r}")
    language = _language_argument(language, locale)
    formats = _locale(language)
    places = _CURRENCY_DECIMALS.get(code, 2) if decimals is None else decimals
    negative, text = _digits(amount, formats, decimals=places)
    sign = _SYMBOLS.get(code, code) if symbol else code
    pattern = formats.currency
    if not symbol or sign == code:
        # A code reads as a word, so it is always separated from the number.
        pattern = pattern.replace("{s}{n}", "{s}" + NBSP + "{n}").replace(
            "{n}{s}", "{n}" + NBSP + "{s}"
        )
    return ("-" if negative else "") + pattern.format(n=text, s=sign)


# ---------------------------------------------------------------------------
# dates and times
# ---------------------------------------------------------------------------
_STYLES = ("short", "medium", "long", "full")


def _tokens(pattern: str) -> list[tuple[bool, str]]:
    """Split a CLDR pattern into ``(is_field, text)`` pieces."""
    pieces: list[tuple[bool, str]] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "'":
            end = pattern.find("'", i + 1)
            if end == -1:
                end = len(pattern)
            literal = pattern[i + 1 : end]
            pieces.append((False, "'" if literal == "" else literal))
            i = end + 1
        elif ch.isalpha():
            j = i
            while j < len(pattern) and pattern[j] == ch:
                j += 1
            pieces.append((True, pattern[i:j]))
            i = j
        else:
            pieces.append((False, ch))
            i += 1
    return pieces


def _apply_pattern(pattern: str, value: date | time, locale: _Locale) -> str:
    has_day = "d" in pattern
    out: list[str] = []
    for is_field, text in _tokens(pattern):
        if not is_field:
            out.append(text)
            continue
        letter, width = text[0], len(text)
        if letter == "y" and isinstance(value, date):
            out.append(f"{value.year % 100:02d}" if width == 2 else str(value.year))
        elif letter in "ML" and isinstance(value, date):
            index = value.month - 1
            if width <= 2:
                out.append(f"{value.month:0{width}d}")
            elif width == 3:
                out.append((locale.months_short or _EN.months_short)[index])
            else:
                names = locale.months or _EN.months
                if has_day and letter == "M" and locale.months_genitive:
                    names = locale.months_genitive
                out.append(names[index])
        elif letter == "d" and isinstance(value, date):
            out.append(f"{value.day:0{width}d}")
        elif letter == "E" and isinstance(value, date):
            out.append((locale.weekdays or _EN.weekdays)[value.weekday()])
        elif letter == "H" and isinstance(value, (time, datetime)):
            out.append(f"{value.hour:0{width}d}")
        elif letter == "h" and isinstance(value, (time, datetime)):
            out.append(f"{(value.hour % 12) or 12:0{width}d}")
        elif letter == "m" and isinstance(value, (time, datetime)):
            out.append(f"{value.minute:0{width}d}")
        elif letter == "s" and isinstance(value, (time, datetime)):
            out.append(f"{value.second:0{width}d}")
        elif letter == "a" and isinstance(value, (time, datetime)):
            out.append(locale.am_pm[0 if value.hour < 12 else 1])
        else:
            raise ValueError(f"pattern field {text!r} does not apply to {type(value).__name__}")
    return "".join(out)


def _style_pattern(styles: Mapping[str, str], style: str, kind: str) -> str:
    if style in styles:
        return styles[style]
    if style in _STYLES:  # "full"/"long" time: fall back to the longest defined
        return styles.get("medium") or next(iter(styles.values()))
    raise ValueError(f"unknown {kind} style {style!r}; expected one of: short, medium, long, full")


def format_date(
    value: date | datetime,
    style: str = "medium",
    *,
    language: str | None = None,
    locale: str | None = None,
    pattern: str | None = None,
) -> str:
    """A calendar date: ``medium`` → ``Sep 26, 2026`` (en), ``26 вер. 2026 р.`` (uk).

    ``style`` is ``short`` (``26.09.26``), ``medium``, ``long``
    (``26 вересня 2026 р.``) or ``full`` (with the weekday). ``pattern`` takes
    a CLDR pattern instead (``"d MMMM"`` → ``26 вересня``); month names inside
    a pattern with a day use the grammatical form the language needs.
    """
    if not isinstance(value, date):
        raise TypeError(f"format_date needs a date or datetime, got {type(value).__name__}")
    language = _language_argument(language, locale)
    formats = _locale(language)
    chosen = pattern or _style_pattern(formats.date or _EN.date, style, "date")
    return _apply_pattern(chosen, value, formats)


def format_time(
    value: time | datetime,
    style: str = "short",
    *,
    language: str | None = None,
    locale: str | None = None,
    pattern: str | None = None,
) -> str:
    """A time of day: ``short`` → ``4:07 PM`` (en), ``16:07`` (uk); ``medium`` adds seconds."""
    if not isinstance(value, (time, datetime)):
        raise TypeError(f"format_time needs a time or datetime, got {type(value).__name__}")
    language = _language_argument(language, locale)
    formats = _locale(language)
    chosen = pattern or _style_pattern(formats.time or _EN.time, style, "time")
    return _apply_pattern(chosen, value, formats)


def format_datetime(
    value: datetime,
    date_style: str = "medium",
    time_style: str = "short",
    *,
    language: str | None = None,
    locale: str | None = None,
) -> str:
    """Date and time together: ``Sep 26, 2026, 4:07 PM`` (en), ``26 вер. 2026 р., 16:07`` (uk)."""
    if not isinstance(value, datetime):
        raise TypeError(f"format_datetime needs a datetime, got {type(value).__name__}")
    language = _language_argument(language, locale)
    formats = _locale(language)
    return formats.datetime.format(
        date=format_date(value, date_style, language=language),
        time=format_time(value, time_style, language=language),
    )


# ---------------------------------------------------------------------------
# bytes, relative time and lists
# ---------------------------------------------------------------------------
#: ``{n}`` is the amount, ``{u}`` the unit. ``{n}`` is always grouped and
#: rounded to at most ``decimals`` fraction digits.
_BYTES = {
    "en": ("B", "KB", "MB", "GB", "TB", "PB"),
    "en-GB": ("B", "kB", "MB", "GB", "TB", "PB"),
    "en-AU": ("B", "kB", "MB", "GB", "TB", "PB"),
    "en-CA": ("B", "kB", "MB", "GB", "TB", "PB"),
    "en-IE": ("B", "kB", "MB", "GB", "TB", "PB"),
    "uk": ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"),
    "ru": ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"),
    # The IEC names are the same in every language, so the binary spellings
    # are derived from the decimal ones (KB → KiB, КБ → КіБ).
    "pl": ("B", "kB", "MB", "GB", "TB", "PB"),
    "cs": ("B", "kB", "MB", "GB", "TB", "PB"),
    "de": ("B", "KB", "MB", "GB", "TB", "PB"),
    "de-AT": ("B", "KB", "MB", "GB", "TB", "PB"),
    "de-CH": ("B", "KB", "MB", "GB", "TB", "PB"),
    "fr": ("o", "Ko", "Mo", "Go", "To", "Po"),
    "fr-CA": ("o", "Ko", "Mo", "Go", "To", "Po"),
    "fr-CH": ("o", "Ko", "Mo", "Go", "To", "Po"),
    "es": ("B", "KB", "MB", "GB", "TB", "PB"),
    "es-MX": ("B", "KB", "MB", "GB", "TB", "PB"),
    "es-US": ("B", "KB", "MB", "GB", "TB", "PB"),
    "it": ("B", "kB", "MB", "GB", "TB", "PB"),
    "pt": ("B", "kB", "MB", "GB", "TB", "PB"),
    "pt-PT": ("B", "kB", "MB", "GB", "TB", "PB"),
    "nl": ("B", "kB", "MB", "GB", "TB", "PB"),
    "ro": ("B", "kB", "MB", "GB", "TB", "PB"),
    "tr": ("B", "kB", "MB", "GB", "TB", "PB"),
}


def _bytes_units(language: str | None) -> tuple[str, ...]:
    """The unit names of ``language`` (English when it has no table)."""
    from .i18n import normalise_language

    tag = normalise_language(language or _current_language())
    return _BYTES.get(tag) or _BYTES.get(tag.split("-")[0]) or _BYTES["en"]


def format_bytes(
    value: float | int | Decimal,
    *,
    language: str | None = None,
    locale: str | None = None,
    decimals: int | None = None,
    binary: bool = False,
) -> str:
    """A file or storage size the way ``language`` writes it: ``1536`` → ``1.5 KB``.

    ``decimals`` fixes the fraction digits; by default one is shown and
    ``1023``/``1024`` both read as a whole number of kilobytes. ``binary=True``
    uses 1024 bytes per step and the IEC names (``1024 B`` → ``1 KiB``), where
    the decimal units differ. ``0`` stays ``"0 B"``; a negative size keeps its
    sign.
    """
    language = _language_argument(language, locale)
    if decimals is not None and decimals < 0:
        raise ValueError("decimals must not be negative")
    units = _bytes_units(language)
    base = 1024 if binary else 1000
    number = _to_decimal(value)
    if number.is_nan():
        raise ValueError(f"cannot format {value!r} as a size")
    if number.is_infinite():
        raise ValueError("cannot format an infinite size")
    negative, magnitude = number < 0, abs(number)
    step = Decimal(base)
    index = 0
    while magnitude >= step and index < len(units) - 1:
        magnitude /= step
        index += 1
    if index:
        places = decimals if decimals is not None else min(1, max(0, 3 - len(str(int(magnitude)))))
        scaled = magnitude.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
        text = format_number(scaled, places, language=language)
    else:
        scaled = magnitude.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        text = format_number(scaled, decimals or 0, language=language)
    if not binary:
        name = units[index]
    elif not index:
        name = units[0]
    else:
        # KB → KiB, КБ → КіБ: the IEC names keep the language's own letters.
        cyrillic = units[index].endswith("Б")
        name = units[index][:-1] + ("іБ" if cyrillic else "iB")
    return ("-" if negative else "") + text + " " + name


#: ``{n}`` the count, ``{u}`` the unit (minute/day/hour). ``past`` and ``future``
#: are the words that follow the phrase: Ukrainian ``"5 хвилин тому"`` /
#: ``"через 5 хвилин"``, English ``"5 minutes ago"`` / ``"in 5 minutes"``.
#: Every quantity a language distinguishes needs a plural form map, exactly as
#: in a catalogue: ``{"one": "хвилина", "few": "хвилини", "many": "хвилин"}``.
_RELATIVE: dict[str, dict[str, tuple[Mapping[str, str], str, str]]] = {
    "en": {
        "minute": ({"one": "minute", "other": "minutes"}, "{n} minutes ago", "in {n} minutes"),
        "hour": ({"one": "hour", "other": "hours"}, "{n} hours ago", "in {n} hours"),
        "day": ({"one": "day", "other": "days"}, "{n} days ago", "in {n} days"),
        "week": ({"one": "week", "other": "weeks"}, "{n} weeks ago", "in {n} weeks"),
        "month": ({"one": "month", "other": "months"}, "{n} months ago", "in {n} months"),
        "year": ({"one": "year", "other": "years"}, "{n} years ago", "in {n} years"),
    },
    "uk": {
        "minute": (
            {"one": "хвилина", "few": "хвилини", "many": "хвилин"},
            "{n} {u} тому",
            "через {n} {u}",
        ),
        "hour": (
            {"one": "година", "few": "години", "many": "годин"},
            "{n} {u} тому",
            "через {n} {u}",
        ),
        "day": ({"one": "день", "few": "дні", "many": "днів"}, "{n} {u} тому", "через {n} {u}"),
        "week": (
            {"one": "тиждень", "few": "тижні", "many": "тижнів"},
            "{n} {u} тому",
            "через {n} {u}",
        ),
        "month": (
            {"one": "місяць", "few": "місяці", "many": "місяців"},
            "{n} {u} тому",
            "через {n} {u}",
        ),
        "year": ({"one": "рік", "few": "роки", "many": "років"}, "{n} {u} тому", "через {n} {u}"),
    },
    "ru": {
        "minute": (
            {"one": "минута", "few": "минуты", "many": "минут"},
            "{n} {u} назад",
            "через {n} {u}",
        ),
        "hour": ({"one": "час", "few": "часа", "many": "часов"}, "{n} {u} назад", "через {n} {u}"),
        "day": ({"one": "день", "few": "дня", "many": "дней"}, "{n} {u} назад", "через {n} {u}"),
        "week": (
            {"one": "неделя", "few": "недели", "many": "недель"},
            "{n} {u} назад",
            "через {n} {u}",
        ),
        "month": (
            {"one": "месяц", "few": "месяца", "many": "месяцев"},
            "{n} {u} назад",
            "через {n} {u}",
        ),
        "year": ({"one": "год", "few": "года", "many": "лет"}, "{n} {u} назад", "через {n} {u}"),
    },
}

#: Unit order and the seconds each step covers, biggest first. The final entry
#: is what the seconds under an hour fall back to.
_RELATIVE_STEPS = (
    ("year", 31_536_000),
    ("month", 2_592_000),
    ("week", 604_800),
    ("day", 86_400),
    ("hour", 3600),
    ("minute", 60),
)

#: A unit is used once the value is at least this much of the step above it,
#: which is what keeps a single unit from being spelled with the next one up:
#: "60 minutes" rather than "1 hour". The last row always applies, so a
#: 30-second difference reads "1 minute ago"/"in 1 minute".
_RELATIVE_MIN_FRACTION = 1.0


def _relative_forms(language: str | None) -> dict[str, tuple[Mapping[str, str], str, str]]:
    """The phrases of ``language``, with English as the fallback."""
    from .i18n import normalise_language

    tag = normalise_language(language or _current_language())
    return _RELATIVE.get(tag) or _RELATIVE.get(tag.split("-")[0]) or _RELATIVE["en"]


def format_relative_time(
    moment: datetime | str,
    *,
    now: datetime | None = None,
    language: str | None = None,
    locale: str | None = None,
) -> str:
    """How long ago ``moment`` was, or how long until it: ``5 хвилин тому`` / ``in 5 minutes``.

    Accepts a :class:`~datetime.datetime` or an ISO-8601 string (what a JSON
    API returns), and a number, which counts seconds from ``now``. ``now`` is
    the reference point — pass one to keep the result testable, otherwise the
    current time is used. A difference under a minute reads ``"1 minute ago"``
    (English) / ``"1 хвилина тому"``.
    """
    language = _language_argument(language, locale)
    tag = language or _current_language()
    if isinstance(moment, (int, float)) and not isinstance(moment, bool):
        if not math.isfinite(moment):
            raise ValueError(f"cannot read {moment!r} as a number of seconds")
        reference = now or datetime.now()
        moment = reference - timedelta(seconds=moment)
    elif isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError as exc:
            raise ValueError(
                f"cannot read {moment!r} as an ISO-8601 date-time (e.g. '2026-11-20T14:05:00')"
            ) from exc
    if not isinstance(moment, datetime):
        raise TypeError(
            f"format_relative_time needs a datetime or ISO-8601 string, got {type(moment).__name__}"
        )
    reference = now or datetime.now(moment.tzinfo)
    if moment.tzinfo is None and reference.tzinfo is not None:
        raise ValueError("moment has no time zone but now does; pass both naive or both aware")
    if moment.tzinfo is not None and reference.tzinfo is None:
        raise ValueError("now has no time zone but moment does; pass both naive or both aware")
    seconds = (reference - moment).total_seconds()
    forms = _relative_forms(language)
    from .i18n import plural_category

    for unit, length in _RELATIVE_STEPS:
        # A future moment is rounded *up* (a countdown must not claim less
        # time than is left), a past one *down* — 3 minutes and 10 seconds
        # ago is still "3 minutes ago". A step is used only once the value
        # reaches its second whole unit, so "in 60 minutes" is never spelled
        # "in 1 hour"; the last row always applies, which covers everything
        # under a minute.
        count = math.ceil(abs(seconds) / length) if seconds < 0 else int(abs(seconds) // length)
        if length != 60 and abs(seconds) < length:
            continue
        unit_forms, past, future = forms[unit]
        name = (
            unit_forms.get(plural_category(count, tag))
            or unit_forms.get("other")
            or next(iter(unit_forms.values()))
        )
        return (past if seconds >= 0 else future).format(
            n=format_number(count, language=language), u=name
        )
    unit_forms, past, future = forms["minute"]
    name = unit_forms.get(plural_category(1, tag)) or next(iter(unit_forms.values()))
    return (past if seconds >= 0 else future).format(n="1", u=name)


#: The joining words and separators of ``format_list``.
_LISTS = {
    "en": ("and", ", "),
    "en-GB": ("and", ", "),
    "en-AU": ("and", ", "),
    "en-CA": ("and", ", "),
    "en-IE": ("and", ", "),
    "uk": ("та", ", "),
    "ru": ("и", ", "),
    "pl": ("i", ", "),
    "cs": ("a", ", "),
    "de": ("und", ", "),
    "de-AT": ("und", ", "),
    "de-CH": ("und", ", "),
    "fr": ("et", ", "),
    "fr-CA": ("et", ", "),
    "fr-CH": ("et", ", "),
    "es": ("y", ", "),
    "es-MX": ("y", ", "),
    "es-US": ("y", ", "),
    "it": ("e", ", "),
    "pt": ("e", ", "),
    "pt-PT": ("e", ", "),
    "nl": ("en", ", "),
    "ro": ("și", ", "),
    "tr": ("ve", ", "),
}


def _list_words(language: str | None) -> tuple[str, str]:
    """``(conjunction, separator)`` for ``language`` (English when unknown)."""
    from .i18n import normalise_language

    tag = normalise_language(language or _current_language())
    return _LISTS.get(tag) or _LISTS.get(tag.split("-")[0]) or _LISTS["en"]


def format_list(
    items: Iterable[str],
    *,
    language: str | None = None,
    locale: str | None = None,
) -> str:
    """A readable list: ``["a", "b", "c"]`` → ``"a, b and c"`` (en), ``"a, b та c"`` (uk).

    One item is that item, two are joined with the conjunction alone, and an
    empty list is the empty string. Items are ``str()``-ed, so numbers and
    other values work; the conjunction and separator follow ``language``.
    """
    language = _language_argument(language, locale)
    words = [str(item) for item in items]
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    conjunction, separator = _list_words(language)
    head = separator.join(words[:-1])
    return head + separator + conjunction + " " + words[-1]


# ---------------------------------------------------------------------------
# format specs inside translated strings
# ---------------------------------------------------------------------------
_SPECS = ("number", "percent", "currency", "date", "time", "datetime", "bytes", "relative", "list")


def format_spec(value: Any, spec: str, language: str) -> str | None:
    """Apply a pymobile format spec (``currency:UAH``, ``date:long`` …).

    Returns ``None`` when ``spec`` is not one of ours, so the caller falls
    back to Python's own ``format(value, spec)``.
    """
    name, _, argument = spec.partition(":")
    if name not in _SPECS:
        return None
    if name == "number":
        return format_number(value, int(argument) if argument else None, language=language)
    if name == "percent":
        return format_percent(value, int(argument) if argument else 0, language=language)
    if name == "currency":
        if not argument:
            raise ValueError("the currency spec needs a code: {price:currency:UAH}")
        return format_currency(value, argument, language=language)
    if name == "date":
        return format_date(value, argument or "medium", language=language)
    if name == "time":
        return format_time(value, argument or "short", language=language)
    if name == "bytes":
        return format_bytes(value, language=language)
    if name == "relative":
        return format_relative_time(value, language=language)
    if name == "list":
        if isinstance(value, str):
            raise ValueError("the list spec needs a sequence of items, not a string")
        return format_list(value, language=language)  # type: ignore[arg-type]
    date_style, _, time_style = argument.partition(",")
    return format_datetime(value, date_style or "medium", time_style or "short", language=language)


class Localized:
    """Wraps a ``t()`` parameter so ``{x:currency:UAH}`` & co. work in templates.

    Everything else — ``{x}``, ``{x:.2f}``, ``{x.attr}``, ``{x[0]}``, ``{x!r}``
    — behaves exactly as with the bare value.
    """

    __slots__ = ("value", "language")

    def __init__(self, value: Any, language: str) -> None:
        self.value = value
        self.language = language

    def __format__(self, spec: str) -> str:
        if spec:
            formatted = format_spec(self.value, spec, self.language)
            if formatted is not None:
                return formatted
        return format(self.value, spec)

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return repr(self.value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.value, name)

    def __getitem__(self, key: Any) -> Any:
        return self.value[key]
