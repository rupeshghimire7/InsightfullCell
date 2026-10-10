"""Column meanings and personal-data flags (stage 1, ``ingest``).

The parse gives physical types (BIGINT, VARCHAR, ...). This module decides what each column
*means* (continuous, nominal, datetime, currency, percentage, identifier, text, geography),
which text columns hold numbers, dates or booleans in disguise, and which columns hold personal
data. Evidence comes from the column name and from counts over every value, computed in DuckDB.
Reasons are written for people and never quote a value.

Personal-data detection is deliberately conservative: a name that says "email" or "phone" is
enough. A false positive hides a column from the model; a false negative could send a person's
data to it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import duckdb
from pydantic import BaseModel, ConfigDict

from insightfulcell.engine import expressions as ex
from insightfulcell.engine.sql import ident, literal, literal_list
from insightfulcell.engine.types import (
    Confidence,
    Meaning,
    PersonalData,
    is_boolean_type,
    is_numeric_type,
    is_temporal_type,
    is_text_type,
)


class ParseKind(StrEnum):
    NUMBERS = "numbers"
    PERCENTAGES = "percentages"
    DATES = "dates"
    BOOLEANS = "booleans"


class ParseHint(BaseModel):
    """A text column whose values are really numbers, dates, percentages or booleans."""

    model_config = ConfigDict(frozen=True)

    kind: ParseKind
    share: float  # of non-missing values that parse
    day_first: bool | None = None
    ambiguous_day_month: int = 0  # values that read differently day-first and month-first
    mixed_day_month: bool = False  # some values only parse day-first, others month-first
    mixed_representation: bool = False  # e.g. percentages with and without a % sign


class ColumnSemantics(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    position: int
    physical_type: str
    meaning: Meaning
    confidence: Confidence
    personal_data: PersonalData | None = None
    unit: str | None = None
    unit_source: str | None = None  # "name" or "values": a unit is never guessed
    parse: ParseHint | None = None
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------- name evidence

_SPLIT: Final = re.compile(r"[^0-9a-zA-Z²°%#]+|(?<=[a-z])(?=[A-Z])")


def name_tokens(name: str) -> tuple[str, ...]:
    """``Labour Cost (AUD)`` -> ``('labour', 'cost', 'aud')``; ``signupDate`` -> ``('signup',
    'date')``."""
    return tuple(t.lower() for t in _SPLIT.split(name) if t)


_EMAIL_HINTS: Final = {"email", "emailaddress", "mail"}
_PHONE_HINTS: Final = {"phone", "mobile", "tel", "telephone", "cell", "cellphone", "fax"}
_ID_DOC_HINTS: Final = {"ssn", "tfn", "passport", "licence", "license", "medicare", "nhs", "nin"}
_DOB_HINTS: Final = {"dob", "birth", "birthday", "birthdate"}
_ADDRESS_HINTS: Final = {"address", "street", "addr"}
_PERSON_HINTS: Final = {
    "firstname",
    "lastname",
    "surname",
    "fullname",
    "givenname",
    "forename",
    "username",
}
#: "customer name", "full name": a person, whatever the values look like.
_PERSON_QUALIFIERS: Final = {
    "customer",
    "client",
    "patient",
    "employee",
    "staff",
    "contact",
    "person",
    "user",
    "member",
    "owner",
    "full",
    "first",
    "last",
    "given",
    "family",
    "student",
    "driver",
    "tenant",
    "guest",
    "applicant",
    "resident",
    "buyer",
    "seller",
    "agent",
    "technician",
    "recipient",
    "sender",
    "holder",
    "account",
    "parent",
    "child",
    "spouse",
    "manager",
    "worker",
}
#: A bare "name" is a person only if the values look like it; these words say it is not.
_NOT_A_PERSON: Final = {
    "product",
    "store",
    "company",
    "business",
    "brand",
    "item",
    "category",
    "city",
    "suburb",
    "town",
    "state",
    "country",
    "region",
    "street",
    "file",
    "plan",
    "service",
    "team",
    "project",
    "model",
    "app",
    "device",
    "campaign",
    "event",
    "job",
    "course",
    "school",
    "org",
    "organisation",
    "organization",
    "department",
    "dept",
    "branch",
    "site",
    "venue",
    "hotel",
    "shop",
    "merchant",
    "vendor",
    "supplier",
    "sku",
    "tag",
    "host",
    "server",
    "domain",
    "column",
    "field",
    "display",
    "package",
    "tier",
    "location",
    "place",
}
_GEO_HINTS: Final = {
    "country",
    "state",
    "city",
    "suburb",
    "town",
    "postcode",
    "postal",
    "zip",
    "zipcode",
    "region",
    "county",
    "province",
    "lga",
    "council",
    "territory",
    "municipality",
    "locality",
    "district",
    "latitude",
    "lat",
    "longitude",
    "lon",
    "lng",
}
_ID_HINTS: Final = {"id", "uuid", "guid", "hash", "key", "ref", "reference", "sku", "#"}
_CURRENCY_HINTS: Final = {
    "price",
    "cost",
    "revenue",
    "amount",
    "salary",
    "income",
    "sales",
    "spend",
    "spent",
    "fee",
    "fees",
    "paid",
    "balance",
    "budget",
    "profit",
    "ltv",
    "arr",
    "mrr",
    "aud",
    "usd",
    "eur",
    "gbp",
    "nzd",
    "cad",
    "dollar",
    "dollars",
    "wage",
    "wages",
    "charge",
    "charges",
}
_VALUE_WORDS: Final = {"contract", "lifetime", "order", "deal", "transaction", "invoice", "sale"}
_PERCENT_HINTS: Final = {"pct", "percent", "percentage", "%"}
_RATE_HINTS: Final = {"rate", "ratio", "share", "proportion"}
_CURRENCY_CODES_IN_NAMES: Final = {"aud", "usd", "eur", "gbp", "nzd", "cad", "jpy", "inr", "sgd"}
_UNIT_WORDS: Final[dict[str, str]] = {
    "km": "km",
    "kms": "km",
    "kilometres": "km",
    "kilometers": "km",
    "m2": "m²",
    "m²": "m²",
    "sqm": "m²",
    "kg": "kg",
    "kgs": "kg",
    "hours": "hours",
    "hrs": "hours",
    "hr": "hours",
    "minutes": "minutes",
    "mins": "minutes",
    "min": "minutes",
    "seconds": "seconds",
    "secs": "seconds",
    "days": "days",
    "weeks": "weeks",
    "months": "months",
    "years": "years",
    "yrs": "years",
    "celsius": "°C",
    "°c": "°C",
    "fahrenheit": "°F",
    "°f": "°F",
    "mm": "mm",
    "cm": "cm",
    "mb": "MB",
    "gb": "GB",
}

_EMAIL_RE: Final = r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"
_PHONE_RE: Final = r"\+?[0-9][0-9 ().\-]{6,19}[0-9]"
_SSN_RE: Final = r"[0-9]{3}-[0-9]{2}-[0-9]{4}"
_UK_NI_RE: Final = r"[A-CEGHJ-PR-TW-Z]{2}[0-9]{6}[A-D]"
_IP_RE: Final = r"[0-9]{1,3}(\.[0-9]{1,3}){3}"
_UUID_RE: Final = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_HEX_RE: Final = r"[0-9a-fA-F]{12,64}"
# Two to four capitalised words, letters only (accents, apostrophes and hyphens allowed).
_PERSON_RE: Final = r"[A-Z][\p{L}'\x{2019}\-]+( [A-Z][\p{L}'\x{2019}\-]+){1,3}"


@dataclass(frozen=True, slots=True)
class _Stats:
    rows: int
    non_null: int
    missing_tokens: int
    distinct: int
    values: dict[str, Any]

    @property
    def valid(self) -> int:
        """Non-null values that are not a missing token: the denominator of every share."""
        return self.non_null - self.missing_tokens

    def share(self, key: str) -> float:
        return (self.values.get(key) or 0) / self.valid if self.valid else 0.0

    @property
    def distinct_ratio(self) -> float:
        return self.distinct / self.valid if self.valid else 0.0


# --------------------------------------------------------------------------- statistics


def _text_stats(con: duckdb.DuckDBPyConnection, column: str) -> _Stats:
    c = ident(column)
    valid = f"({c} IS NOT NULL AND NOT {ex.is_missing_token(c)})"
    s = ex.clean_text(c)
    day_first = ex.parses_with(c, ex.DAY_FIRST_FORMATS)
    month_first = ex.parses_with(c, ex.MONTH_FIRST_FORMATS)
    probes = {
        "email": f"regexp_full_match({s}, {literal(_EMAIL_RE)})",
        "phone": (
            f"regexp_full_match({s}, {literal(_PHONE_RE)}) AND "
            f"length(regexp_replace({s}, '[^0-9]', '', 'g')) BETWEEN 8 AND 15"
        ),
        # Formatted like a phone number (a +, brackets, spaces or dashes between digits): a
        # plain digit string is far more often an id or a code.
        "phone_formatted": f"regexp_matches({s}, '^\\+|[() \\-][0-9]')",
        "national_id": f"regexp_full_match({s}, {literal(_SSN_RE + '|' + _UK_NI_RE)})",
        "ip": f"regexp_full_match({s}, {literal(_IP_RE)})",
        "uuid": f"regexp_full_match({s}, {literal(_UUID_RE)})",
        "hex": f"regexp_full_match({s}, {literal(_HEX_RE)})",
        "person": f"regexp_full_match({s}, {literal(_PERSON_RE)})",
        "euro": f"contains({s}, '€')",
        "pound": f"contains({s}, '£')",
        "number": f"{ex.number_from_text(c)} IS NOT NULL",
        "currency": ex.has_currency_marker(c),
        "percent": ex.has_percent_sign(c),
        "boolean": f"{ex.boolean_from_text(c)} IS NOT NULL",
        "iso": ex.parses_with(c, ex.ISO_DATE_FORMATS),
        "named": ex.parses_with(c, ex.NAMED_MONTH_FORMATS),
        "day_first": day_first,
        "month_first": month_first,
        "day_first_only": f"{day_first} AND NOT {month_first}",
        "month_first_only": f"{month_first} AND NOT {day_first}",
        "ambiguous": (
            f"{day_first} AND {month_first} AND "
            f"try_strptime({s}, {literal_list(ex.DAY_FIRST_FORMATS)}) "
            f"<> try_strptime({s}, {literal_list(ex.MONTH_FIRST_FORMATS)})"
        ),
        "date_any": f"{ex.datetime_from_text(c, ex.date_formats(day_first=True))} IS NOT NULL",
    }
    aggregates = ", ".join(
        f"count(*) FILTER (WHERE {valid} AND ({expr})) AS {key}" for key, expr in probes.items()
    )
    query = f"""
        SELECT count(*), count({c}),
               count(*) FILTER (WHERE {c} IS NOT NULL AND {ex.is_missing_token(c)}),
               count(DISTINCT {c}) FILTER (WHERE {valid}),
               avg(length({s})) FILTER (WHERE {valid}),
               avg(len(string_split({s}, ' '))) FILTER (WHERE {valid}),
               {aggregates}
        FROM t
    """
    row = con.execute(query).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    values = dict(zip(probes, row[6:], strict=True))
    values["avg_length"] = float(row[4] or 0)
    values["avg_words"] = float(row[5] or 0)
    return _Stats(
        rows=row[0], non_null=row[1], missing_tokens=row[2], distinct=row[3], values=values
    )


def _typed_stats(con: duckdb.DuckDBPyConnection, column: str, physical: str) -> _Stats:
    c = ident(column)
    numeric = is_numeric_type(physical)
    extra = (
        f", min({c})::DOUBLE, max({c})::DOUBLE, "
        f"count(*) FILTER (WHERE {c} IS NOT NULL AND {c}::DOUBLE <> floor({c}::DOUBLE))"
        if numeric
        else ", NULL, NULL, NULL"
    )
    row = con.execute(f"SELECT count(*), count({c}), count(DISTINCT {c}){extra} FROM t").fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    values = {"min": row[3], "max": row[4], "fractional": row[5] or 0}
    return _Stats(rows=row[0], non_null=row[1], missing_tokens=0, distinct=row[2], values=values)


# --------------------------------------------------------------------------- decisions


def detect(
    con: duckdb.DuckDBPyConnection, columns: Sequence[tuple[str, str]]
) -> list[ColumnSemantics]:
    """Meaning, unit and personal-data flag for every column of the table ``t``."""
    out: list[ColumnSemantics] = []
    for position, (name, physical) in enumerate(columns):
        if is_text_type(physical):
            stats = _text_stats(con, name)
        else:
            stats = _typed_stats(con, name, physical)
        out.append(_decide(name, position, physical, stats))
    return out


def _decide(name: str, position: int, physical: str, stats: _Stats) -> ColumnSemantics:
    tokens = set(name_tokens(name))
    if "#" in name:
        tokens.add("#")
    pii, pii_reason = _personal_data(name, tokens, physical, stats)
    meaning, confidence, parse, found = _meaning(tokens, physical, stats, pii)
    reasons = tuple(found)
    unit, unit_source = _unit(tokens, meaning, stats, physical)
    if pii is not None:
        reasons = (pii_reason, *reasons)
    return ColumnSemantics(
        name=name,
        position=position,
        physical_type=physical,
        meaning=meaning,
        confidence=confidence,
        personal_data=pii,
        unit=unit,
        unit_source=unit_source,
        parse=parse,
        reasons=tuple(reasons),
    )


def _personal_data(  # noqa: PLR0911  (one return per kind reads best)
    name: str, tokens: set[str], physical: str, stats: _Stats
) -> tuple[PersonalData | None, str]:
    text = is_text_type(physical)
    if tokens & {"ip"} and (tokens & _ADDRESS_HINTS or stats.share("ip") >= 0.5):
        return PersonalData.IP_ADDRESS, "Named as an IP address."
    if tokens & _EMAIL_HINTS or (text and stats.share("email") >= 0.6):
        return PersonalData.EMAIL, _why(tokens & _EMAIL_HINTS, "email addresses", stats, "email")
    if tokens & _PHONE_HINTS or (
        text and stats.share("phone") >= 0.7 and stats.share("phone_formatted") >= 0.5
    ):
        return PersonalData.PHONE, _why(tokens & _PHONE_HINTS, "phone numbers", stats, "phone")
    if (
        tokens & _ID_DOC_HINTS
        or {"national", "id"} <= tokens
        or {"tax", "id"} <= tokens
        or (text and stats.share("national_id") >= 0.7)
    ):
        return PersonalData.NATIONAL_ID, _why(
            tokens & _ID_DOC_HINTS, "national identity numbers", stats, "national_id"
        )
    if (tokens & {"card"} and tokens & {"number", "no", "num"}) or tokens & {"creditcard", "pan"}:
        return PersonalData.PAYMENT_CARD, "Named as a payment card number."
    if tokens & _DOB_HINTS or {"date", "of", "birth"} <= tokens:
        return PersonalData.DATE_OF_BIRTH, "Named as a date of birth."
    if tokens & _ADDRESS_HINTS and not tokens & {"email", "ip", "web", "url"}:
        return PersonalData.ADDRESS, "Named as a street address."
    if text and (tokens & _PERSON_HINTS or ("name" in tokens and tokens & _PERSON_QUALIFIERS)):
        return PersonalData.PERSON_NAME, "Named as a person's name."
    if text and "name" in tokens and not tokens & _NOT_A_PERSON and stats.share("person") >= 0.3:
        return (
            PersonalData.PERSON_NAME,
            f"Named 'name', and {_pct(stats.share('person'))} of values look like people's names.",
        )
    if (
        text
        and stats.share("person") >= 0.6
        and stats.distinct_ratio >= 0.4
        and stats.values.get("avg_words", 0) <= 4
        and not tokens & (_NOT_A_PERSON | _GEO_HINTS)
    ):
        return (
            PersonalData.PERSON_NAME,
            f"{_pct(stats.share('person'))} of values look like people's names.",
        )
    del name
    return None, ""


def _why(hits: set[str], what: str, stats: _Stats, key: str) -> str:
    if hits:
        return f"Named as {what}."
    return f"{_pct(stats.share(key))} of values look like {what}."


def _meaning(  # noqa: PLR0911, PLR0912  (a decision table)
    tokens: set[str], physical: str, stats: _Stats, pii: PersonalData | None
) -> tuple[Meaning, Confidence, ParseHint | None, list[str]]:
    if pii is not None:
        if pii is PersonalData.DATE_OF_BIRTH:
            return Meaning.DATETIME, Confidence.HIGH, _date_hint(stats, physical), []
        if pii is PersonalData.ADDRESS:
            return Meaning.TEXT, Confidence.MEDIUM, None, []
        return Meaning.IDENTIFIER, Confidence.HIGH, None, []

    if stats.valid == 0:
        return Meaning.NOMINAL, Confidence.LOW, None, ["Every value is missing."]
    if is_temporal_type(physical):
        return Meaning.DATETIME, Confidence.HIGH, None, ["Stored as a date or time."]
    if is_boolean_type(physical):
        return Meaning.NOMINAL, Confidence.HIGH, None, ["Stored as true/false."]

    geo = bool(tokens & _GEO_HINTS)
    id_hint = bool(tokens & _ID_HINTS)
    if is_numeric_type(physical):
        return _numeric_meaning(tokens, stats, geo=geo, id_hint=id_hint)

    # ---- text
    if stats.share("uuid") >= 0.9 or (stats.share("hex") >= 0.9 and stats.distinct_ratio > 0.9):
        return Meaning.IDENTIFIER, Confidence.HIGH, None, ["Values are unique codes."]
    avg_len, avg_words = stats.values["avg_length"], stats.values["avg_words"]
    if id_hint and stats.distinct_ratio >= 0.8:
        return Meaning.IDENTIFIER, Confidence.HIGH, None, ["Named as an id; values are unique."]
    if id_hint and avg_words <= 1.2 and stats.distinct >= 3:
        return (
            Meaning.IDENTIFIER,
            Confidence.MEDIUM,
            None,
            ["Named as an id; values are codes that refer to something else."],
        )
    date_share = stats.share("date_any")
    if date_share >= 0.8 and stats.share("number") < 0.5:
        hint = _date_hint(stats, physical)
        return (
            Meaning.DATETIME,
            Confidence.HIGH if date_share >= 0.95 else Confidence.MEDIUM,
            hint,
            [f"{_pct(date_share)} of values read as dates."],
        )
    if geo:
        return Meaning.GEOGRAPHY, Confidence.MEDIUM, None, ["Named as a place."]
    named_percent = bool(tokens & _PERCENT_HINTS)
    percent_like = stats.share("percent") + stats.share("number")
    if (stats.share("percent") >= 0.5 or named_percent) and percent_like >= 0.9:
        share = stats.share("percent")
        mixed = share < 0.99
        hint = ParseHint(
            kind=ParseKind.PERCENTAGES,
            share=_round(_parse_share_percent(stats)),
            mixed_representation=mixed,
        )
        reason = (
            f"{_pct(share)} of values end in %."
            if share >= 0.5
            else "Named as a percentage; values are numbers."
        )
        return Meaning.PERCENTAGE, Confidence.HIGH, hint, [reason]
    number_share = stats.share("number")
    if number_share >= 0.8 and stats.share("boolean") < 0.95:
        hint = ParseHint(kind=ParseKind.NUMBERS, share=_round(number_share))
        money_name = bool(tokens & _CURRENCY_HINTS) or bool(
            tokens & {"value"} and tokens & _VALUE_WORDS
        )
        if stats.share("currency") >= 0.3 or money_name:
            return (
                Meaning.CURRENCY,
                Confidence.HIGH,
                hint,
                [f"{_pct(number_share)} of values are amounts written as text."],
            )
        return (
            Meaning.CONTINUOUS,
            Confidence.MEDIUM,
            hint,
            [f"{_pct(number_share)} of values are numbers written as text."],
        )
    if stats.share("boolean") >= 0.9:
        hint = ParseHint(kind=ParseKind.BOOLEANS, share=_round(stats.share("boolean")))
        return Meaning.NOMINAL, Confidence.HIGH, hint, ["Values are yes/no answers."]
    if ((avg_len >= 30 and avg_words >= 2) or avg_words >= 4) and stats.distinct_ratio >= 0.5:
        return Meaning.TEXT, Confidence.HIGH, None, ["Values are free text."]
    if stats.distinct_ratio >= 0.95 and stats.valid >= 10 and avg_words <= 2:
        return Meaning.IDENTIFIER, Confidence.MEDIUM, None, ["Almost every value is unique."]
    return (
        Meaning.NOMINAL,
        Confidence.HIGH if stats.distinct_ratio < 0.5 else Confidence.MEDIUM,
        None,
        [],
    )


def _numeric_meaning(
    tokens: set[str], stats: _Stats, *, geo: bool, id_hint: bool
) -> tuple[Meaning, Confidence, ParseHint | None, list[str]]:
    low, high = stats.values.get("min"), stats.values.get("max")
    integral = stats.values.get("fractional", 0) == 0
    if geo:
        return Meaning.GEOGRAPHY, Confidence.HIGH, None, ["Named as a place or coordinate."]
    if id_hint and integral and stats.distinct_ratio >= 0.8:
        return Meaning.IDENTIFIER, Confidence.HIGH, None, ["Named as an id; values are unique."]
    if tokens & _CURRENCY_HINTS or (tokens & {"value"} and tokens & _VALUE_WORDS):
        return Meaning.CURRENCY, Confidence.MEDIUM, None, ["Named as an amount of money."]
    if (tokens & (_PERCENT_HINTS | _RATE_HINTS)) and low is not None and high is not None:
        if low >= 0 and high <= 1:
            return (
                Meaning.PERCENTAGE,
                Confidence.HIGH,
                None,
                ["Named as a rate; values are 0 to 1."],
            )
        if low >= 0 and high <= 100 and tokens & _PERCENT_HINTS:
            return Meaning.PERCENTAGE, Confidence.MEDIUM, None, ["Named as a percentage."]
    return Meaning.CONTINUOUS, Confidence.HIGH, None, []


def _date_hint(stats: _Stats, physical: str) -> ParseHint | None:
    if not is_text_type(physical):
        return None
    day_only = stats.values.get("day_first_only", 0) or 0
    month_only = stats.values.get("month_first_only", 0) or 0
    day_first = not (month_only > 0 and day_only == 0)
    return ParseHint(
        kind=ParseKind.DATES,
        share=_round(stats.share("date_any")),
        day_first=day_first,
        ambiguous_day_month=int(stats.values.get("ambiguous", 0) or 0),
        mixed_day_month=day_only > 0 and month_only > 0,
    )


def _parse_share_percent(stats: _Stats) -> float:
    return min(1.0, stats.share("percent") + max(0.0, stats.share("number")))


def _unit(  # noqa: PLR0911
    tokens: set[str], meaning: Meaning, stats: _Stats, physical: str
) -> tuple[str | None, str | None]:
    if meaning is Meaning.PERCENTAGE:
        return "%", "values" if is_text_type(physical) else "name"
    if meaning is Meaning.CURRENCY:
        codes = tokens & _CURRENCY_CODES_IN_NAMES
        if codes:
            return sorted(codes)[0].upper(), "name"
        if is_text_type(physical) and stats.share("currency") >= 0.5:
            # The symbol shown in the data; which dollar (AUD, USD) is never guessed.
            if stats.share("euro") >= 0.5:
                return "€", "values"
            if stats.share("pound") >= 0.5:
                return "£", "values"
            return "$", "values"
        return None, None
    if meaning is Meaning.CONTINUOUS:
        for token in sorted(tokens):
            if token in _UNIT_WORDS:
                return _UNIT_WORDS[token], "name"
    return None, None


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def _round(value: float) -> float:
    return round(value, 4)


def semantics_from_output(rows: Sequence[dict[str, Any]]) -> list[ColumnSemantics]:
    """Rebuild semantics stored in the ``ingest`` stage's output."""
    return [ColumnSemantics.model_validate(row) for row in rows]
