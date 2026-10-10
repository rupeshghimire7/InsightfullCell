"""SQL expressions that read meaning out of text: numbers, percentages, dates, booleans.

One definition each, used by semantic detection, profiling, cleaning and the exported Python
recipe, so what the user downloads computes exactly what the engine computed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from insightfulcell.engine.sql import literal, literal_list
from insightfulcell.engine.types import MISSING_TOKENS

ISO_DATE_FORMATS: Final = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d",
)
DAY_FIRST_FORMATS: Final = (
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d.%m.%Y",
)
MONTH_FIRST_FORMATS: Final = (
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m-%d-%Y",
)
#: Only text holding a four-digit year can be a date: ``2.9.8`` (a version) is not.
_DATE_GUARD: Final = "'(^|[^0-9])[0-9]{4}([^0-9]|$)'"
NAMED_MONTH_FORMATS: Final = (
    "%d %b %Y",
    "%d %B %Y",
    "%d %b %Y %I:%M %p",
    "%d %B %Y %I:%M %p",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
)

TRUE_WORDS: Final = ("true", "t", "yes", "y", "1", "✓", "✔")
FALSE_WORDS: Final = ("false", "f", "no", "n", "0", "✗", "✘")

CURRENCY_SYMBOLS: Final = "$€£¥₹"
CURRENCY_CODES: Final = ("aud", "usd", "eur", "gbp", "nzd", "cad", "jpy", "inr", "sgd")


def clean_text(column: str) -> str:
    """Trimmed text, with non-breaking spaces treated as spaces."""
    return f"trim(replace(CAST({column} AS VARCHAR), chr(160), ' '))"


def is_missing_token(column: str) -> str:
    return f"(upper({clean_text(column)}) IN ({', '.join(literal(t) for t in MISSING_TOKENS)}))"


def number_from_text(column: str) -> str:
    """``$1,234.50``, ``AUD 85``, ``(45.00)``, ``1.2k``, ``12 500`` -> DOUBLE; else NULL."""
    codes = "|".join(CURRENCY_CODES)
    s = clean_text(column)
    # 1. currency code prefix or suffix, 2. accounting negative, 3. symbols and separators.
    s = f"regexp_replace({s}, '(?i)^({codes})\\s*|\\s*({codes})$', '')"
    s = f"regexp_replace({s}, '^\\((.*)\\)$', '-\\1')"
    s = f"regexp_replace({s}, '[{CURRENCY_SYMBOLS},\\s]', '', 'g')"
    return (
        f"(CASE WHEN regexp_full_match({s}, '(?i)[-+]?[0-9]*\\.?[0-9]+k') "
        f"THEN TRY_CAST(rtrim(lower({s}), 'k') AS DOUBLE) * 1000 "
        f"WHEN regexp_full_match({s}, '(?i)[-+]?[0-9]*\\.?[0-9]+m') "
        f"THEN TRY_CAST(rtrim(lower({s}), 'm') AS DOUBLE) * 1000000 "
        f"ELSE TRY_CAST({s} AS DOUBLE) END)"
    )


def has_currency_marker(column: str) -> str:
    codes = "|".join(CURRENCY_CODES)
    return (
        f"regexp_matches({clean_text(column)}, "
        f"'(?i)^[-+(]?\\s*([{CURRENCY_SYMBOLS}]|({codes})\\b)|\\b({codes})$')"
    )


def has_percent_sign(column: str) -> str:
    return f"regexp_full_match({clean_text(column)}, '[-+]?[0-9]*\\.?[0-9]+\\s*%')"


def percentage_from_text(column: str) -> str:
    """``10%`` -> 0.10. Without a sign: up to 1 is already a fraction, above 1 is points."""
    s = clean_text(column)
    value = f"TRY_CAST(rtrim(replace({s}, ' ', ''), '%') AS DOUBLE)"
    return (
        f"(CASE WHEN {has_percent_sign(column)} THEN {value} / 100 "
        f"WHEN {value} IS NULL THEN NULL "
        f"WHEN abs({value}) <= 1 THEN {value} ELSE {value} / 100 END)"
    )


def date_formats(day_first: bool) -> tuple[str, ...]:
    """Formats tried in order; the day/month order is decided per column, never per value."""
    slash = DAY_FIRST_FORMATS if day_first else MONTH_FIRST_FORMATS
    return ISO_DATE_FORMATS + slash + NAMED_MONTH_FORMATS


def datetime_from_text(column: str, formats: Sequence[str]) -> str:
    """Text -> TIMESTAMP by the column's formats; else NULL.

    A trailing UTC offset (``+09:30``, ``Z``) is dropped and the wall-clock time kept as
    written: converting it would make the result depend on the machine's time zone."""
    s = f"regexp_replace({clean_text(column)}, '(Z|[+-][0-9]{{2}}:?[0-9]{{2}})$', '')"
    return (
        f"(CASE WHEN regexp_matches({s}, {_DATE_GUARD}) THEN "
        f"COALESCE(try_strptime({s}, {literal_list(formats)}), "
        f"CASE WHEN regexp_matches({s}, '^[0-9]{{4}}-') THEN TRY_CAST({s} AS TIMESTAMP) END) "
        f"END)"
    )


def parses_with(column: str, formats: Sequence[str]) -> str:
    s = clean_text(column)
    return (
        f"(regexp_matches({s}, {_DATE_GUARD}) AND "
        f"try_strptime({s}, {literal_list(formats)}) IS NOT NULL)"
    )


def boolean_from_text(column: str) -> str:
    s = f"lower({clean_text(column)})"
    return (
        f"(CASE WHEN {s} IN ({', '.join(literal(w) for w in TRUE_WORDS)}) THEN true "
        f"WHEN {s} IN ({', '.join(literal(w) for w in FALSE_WORDS)}) THEN false END)"
    )


def value_shape(column: str) -> str:
    """The structure of a value with its content removed: ``AB-1234`` -> ``A-9``,
    ``Sam Brooks`` -> ``Aa Aa``. Safe to show: no characters of the value survive."""
    s = clean_text(column)
    s = f"regexp_replace({s}, '[0-9]+', '9', 'g')"
    s = f"regexp_replace({s}, '[A-Z]+', 'A', 'g')"
    s = f"regexp_replace({s}, '[a-z]+', 'a', 'g')"
    s = f"regexp_replace({s}, '[^A9a\\s[:punct:]]+', '*', 'g')"
    return f"left({s}, 24)"
