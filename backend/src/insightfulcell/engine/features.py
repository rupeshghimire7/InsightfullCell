"""The feature catalogue: candidate features as SQL over cleaned columns.

Phase B's ``features`` stage keeps a candidate only if it passes the signal gate; Phase A only
plans them. The expressions here are the single definition used by both and by the recipe.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from insightfulcell.engine.planning.language import DatePart, FeatureKind
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import NUMERIC_MEANINGS, Meaning


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    label: str
    arity: int
    inputs: frozenset[Meaning]  # meanings each input column may have


_TEXTUAL: Final = frozenset({Meaning.TEXT, Meaning.NOMINAL})
_ANY: Final = frozenset(Meaning)

CATALOGUE: Final[dict[FeatureKind, FeatureSpec]] = {
    FeatureKind.DATE_PART: FeatureSpec("Part of a date", 1, frozenset({Meaning.DATETIME})),
    FeatureKind.DAYS_BETWEEN: FeatureSpec(
        "Days between two dates", 2, frozenset({Meaning.DATETIME})
    ),
    FeatureKind.RATIO: FeatureSpec("Ratio of two measures", 2, NUMERIC_MEANINGS),
    FeatureKind.DIFFERENCE: FeatureSpec("Difference of two measures", 2, NUMERIC_MEANINGS),
    FeatureKind.LOG: FeatureSpec("Logarithm (for skewed measures)", 1, NUMERIC_MEANINGS),
    FeatureKind.BAND: FeatureSpec("Equal-count bands", 1, NUMERIC_MEANINGS),
    FeatureKind.TEXT_LENGTH: FeatureSpec("Length of text", 1, _TEXTUAL),
    FeatureKind.WORD_COUNT: FeatureSpec("Number of words", 1, _TEXTUAL),
    FeatureKind.IS_MISSING: FeatureSpec("Whether a value is missing", 1, _ANY),
}


def output_name(kind: FeatureKind, columns: Sequence[str], part: DatePart | None) -> str:
    slug = "__".join(re.sub(r"[^0-9a-z]+", "_", c.lower()).strip("_") or "col" for c in columns)
    suffix = f"__{part.value}" if part else ""
    return f"f_{kind.value}__{slug}{suffix}"[:120]


def expression(  # noqa: PLR0911
    kind: FeatureKind, columns: Sequence[str], part: DatePart | None = None, bins: int | None = None
) -> str:
    cols = [ident(c) for c in columns]
    if kind is FeatureKind.DATE_PART:
        x = cols[0]
        return {
            DatePart.YEAR: f"year({x})",
            DatePart.QUARTER: f"quarter({x})",
            DatePart.MONTH: f"month({x})",
            DatePart.WEEKDAY: f"isodow({x})",
            DatePart.HOUR: f"hour({x})",
            DatePart.IS_WEEKEND: f"(isodow({x}) >= 6)",
            None: f"year({x})",
        }[part]
    if kind is FeatureKind.DAYS_BETWEEN:
        return f"date_diff('day', CAST({cols[0]} AS DATE), CAST({cols[1]} AS DATE))"
    if kind is FeatureKind.RATIO:
        return f"(CAST({cols[0]} AS DOUBLE) / NULLIF(CAST({cols[1]} AS DOUBLE), 0))"
    if kind is FeatureKind.DIFFERENCE:
        return f"(CAST({cols[0]} AS DOUBLE) - CAST({cols[1]} AS DOUBLE))"
    if kind is FeatureKind.LOG:
        return f"(CASE WHEN {cols[0]} >= 0 THEN ln(1 + {cols[0]}) END)"
    if kind is FeatureKind.BAND:
        k = max(2, min(10, bins or 4))
        # Empty values form their own partition, so they do not take a share of any band.
        return (
            f"(CASE WHEN {cols[0]} IS NOT NULL THEN "
            f"ntile({k}) OVER (PARTITION BY {cols[0]} IS NULL ORDER BY {cols[0]}) END)"
        )
    if kind is FeatureKind.TEXT_LENGTH:
        return f"length(CAST({cols[0]} AS VARCHAR))"
    if kind is FeatureKind.WORD_COUNT:
        return f"len(string_split(trim(CAST({cols[0]} AS VARCHAR)), ' '))"
    return f"({cols[0]} IS NULL)"  # is_missing
