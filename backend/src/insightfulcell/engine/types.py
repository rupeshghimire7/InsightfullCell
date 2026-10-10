"""Vocabulary shared by every part of the engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Meaning(StrEnum):
    """What a column *means*, beyond its physical type (system map: Ingest and profile)."""

    CONTINUOUS = "continuous"
    NOMINAL = "nominal"
    DATETIME = "datetime"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"  # stored as a fraction once parsed
    IDENTIFIER = "identifier"
    TEXT = "text"
    GEOGRAPHY = "geography"


NUMERIC_MEANINGS: Final = frozenset({Meaning.CONTINUOUS, Meaning.CURRENCY, Meaning.PERCENTAGE})
CATEGORICAL_MEANINGS: Final = frozenset({Meaning.NOMINAL, Meaning.GEOGRAPHY})


class PersonalData(StrEnum):
    """Kinds of personal data. A flagged column is masked from ingest onwards: no values,
    shapes, categories or ranges leave the worker, and the gateway blocks any payload that
    contains one of its values."""

    EMAIL = "email"
    PHONE = "phone"
    PERSON_NAME = "person_name"
    NATIONAL_ID = "national_id"
    ADDRESS = "address"
    DATE_OF_BIRTH = "date_of_birth"
    IP_ADDRESS = "ip_address"
    PAYMENT_CARD = "payment_card"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: Text that means "no value" in exported data. Compared after trim and upper-casing.
MISSING_TOKENS: Final = (
    "",
    "NA",
    "N/A",
    "N.A.",
    "NAN",
    "NULL",
    "NONE",
    "NIL",
    "-",
    "--",
    "?",
    "UNKNOWN",
    "MISSING",
    "#N/A",
    "(BLANK)",
)

PHYSICAL_NUMERIC_PREFIXES: Final = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "REAL",
)
PHYSICAL_TEMPORAL_PREFIXES: Final = ("DATE", "TIMESTAMP", "TIME")


def is_numeric_type(physical: str) -> bool:
    return physical.upper().startswith(PHYSICAL_NUMERIC_PREFIXES)


def is_temporal_type(physical: str) -> bool:
    return physical.upper().startswith(PHYSICAL_TEMPORAL_PREFIXES)


def is_text_type(physical: str) -> bool:
    return physical.upper() == "VARCHAR"


def is_boolean_type(physical: str) -> bool:
    return physical.upper() == "BOOLEAN"
