"""Facts and chart data: what the analysis found, as immutable records with provenance.

A fact is one statement about the cleaned data, with every number it contains computed by the
engine (DuckDB), the columns and rows it rests on, and the method in plain words. Reports cite
facts by id; a narrated sentence may only contain numbers that appear in the facts it cites.

A fact that rests on a hypothesis test carries the test (effect size, 95% interval, p-value and
the q-value after the run's false-discovery control) and a tier: ``strong`` (q at most 0.01),
``supported`` (q at most 0.05) or ``inconclusive``; a fact without a test is ``descriptive``
(a count, a share, a median). Its wording follows the tier, so an unreliable difference is
never stated as a finding.

Numbers are formatted here, once, so the statement, the verifier and the page all agree on how
a value is written ("41%", "1,234", "3.72").
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from insightfulcell.engine.types import Meaning

MIN_ROWS: Final = 10  # below this, nothing is said about a group or a column
STRENGTH_WORDS: Final = ((0.1, "negligible"), (0.3, "weak"), (0.5, "moderate"), (1.01, "strong"))
_CURRENCY_SYMBOLS: Final = {"$", "€", "£", "¥"}


Tier = Literal["descriptive", "strong", "supported", "inconclusive"]
STRONG_Q: Final = 0.01


class FactTest(BaseModel):
    """The hypothesis test a fact rests on (one row of the run's test log)."""

    model_config = ConfigDict(frozen=True)

    hypothesis: str
    method: str
    n: int
    effect_name: str
    effect: float
    ci_low: float | None
    ci_high: float | None
    p_value: float
    q_value: float
    size: float = 0.0  # the effect on a 0-to-1 scale, for ranking


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    analysis_id: str
    kind: str
    columns: tuple[str, ...]
    rows: int  # rows the numbers rest on
    method: str  # plain words: how the numbers were computed
    statement: str  # one sentence with the formatted numbers
    numbers: tuple[str, ...]  # every formatted number in the statement
    labels: tuple[str, ...] = ()  # category or period labels the statement names
    values: dict[str, Any] = Field(default_factory=dict)  # the raw numbers
    tier: Tier = "descriptive"
    test: FactTest | None = None
    #: Where the numbers come from: the cleaned data's plan hash, stage and code versions, the
    #: reference set for places.
    provenance: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"id"}), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ChartPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str  # category, period (ISO date) or bin ("10 to 20")
    value: float  # bar length, line height, bin count, or y of a scatter point
    x: float | None = None  # scatter x, or a bin's start
    x2: float | None = None  # a bin's end
    n: int | None = None  # rows behind the point


class ChartData(BaseModel):
    """A chart as data: the page draws it (with a table view), the recipe can redraw it."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["bar", "line", "scatter", "histogram"]
    title: str
    x_label: str
    y_label: str
    value_format: Literal["number", "percent", "count", "strength", "signed_percent"]
    unit: str | None = None
    points: tuple[ChartPoint, ...]
    note: str | None = None


# --------------------------------------------------------------------------- formatting


def strength(value: float) -> str:
    """Words for an association's size (absolute value of a correlation, eta or Cramér's V)."""
    size = abs(value)
    return next(word for limit, word in STRENGTH_WORDS if size < limit)


def count(n: int | float) -> str:
    return f"{round(n):,}"


def share(p: float) -> str:
    """A share of rows: whole percent, one decimal below ten percent ("4.5%", "41%")."""
    pct = p * 100
    if 0 < abs(pct) < 10:
        return f"{pct:.1f}%"
    return f"{round(pct)}%"


def signed_share(p: float) -> str:
    text = share(abs(p))
    return f"+{text}" if p > 0 else (f"-{text}" if p < 0 else text)


def coefficient(value: float) -> str:
    """A correlation or strength, two decimals ("0.42", "-0.18"); never "-0.00"."""
    text = f"{value:.2f}"
    return "0.00" if text == "-0.00" else text


def interval(low: float | None, high: float | None, write: Any = coefficient) -> str | None:
    """ "95% interval 0.35 to 0.49", or ``None`` without an interval."""
    if low is None or high is None:
        return None
    return f"95% interval {write(low)} to {write(high)}"


def plain(value: float) -> str:
    """A measure: whole numbers with thousands separators from 100 up, otherwise three
    significant figures ("1,234", "37.5", "3.72", "0.0412")."""
    if not math.isfinite(value):
        return "n/a"
    if abs(value) >= 100 or float(value).is_integer():
        return f"{round(value):,}"
    digits = max(0, 2 - math.floor(math.log10(abs(value)))) if value else 0
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def measure(value: float, meaning: Meaning | str, unit: str | None) -> str:
    """A value of a column, with its unit: percentages (stored as fractions) as percent,
    currency symbols in front, other units after ("$1,234", "41%", "12.5 km")."""
    if Meaning(meaning) is Meaning.PERCENTAGE:
        return share(value)
    text = plain(value)
    if not unit or unit == "%":
        return text
    if unit in _CURRENCY_SYMBOLS:
        return f"-{unit}{text[1:]}" if text.startswith("-") else f"{unit}{text}"
    return f"{text} {unit}"
