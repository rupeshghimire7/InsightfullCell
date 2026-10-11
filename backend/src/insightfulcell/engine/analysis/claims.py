"""Claims: what each chart value says about the data, in a form a machine can check.

Every point of a chart (and every region of a map) carries one claim: which rows it measures
and how (a count, a share, a group's average, a correlation...). The ``visualize`` stage rebuilds
each claim's query from these fields, independently of the miner that drew the chart (one
filtered query per point instead of one grouped query per chart), runs it on the analysis table
and rejects the chart if any value differs. A document never carries SQL: names are quoted and
values bound as literals when the query is built.

Column names refer to the analysis table (the cleaned data plus resolved regions and kept
features). Group columns are read the way the miners read groups: trimmed text, blanks as
missing.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

#: A group with fewer rows is never shown on its own; such groups are pooled (see miners).
POOL_BELOW: Final = 10


class _Claim(BaseModel):
    model_config = ConfigDict(frozen=True)


class Value(_Claim):
    """A measured value: a column read as a number, or, with ``positive``, a yes/no indicator
    of a group column (1 when it equals ``positive``, 0 for any other value)."""

    column: str
    positive: str | None = None


class GroupClaim(_Claim):
    """A group's average of ``value`` (``mean``) or its difference from the average of all
    groups, as a share of that average (``gap``). Groups with fewer than ten rows are pooled
    into one group (``key`` = None) when the pool itself has ten rows, otherwise left out."""

    type: Literal["group"] = "group"
    group: str
    value: Value
    key: str | None
    measure: Literal["mean", "gap"] = "mean"


class EtaClaim(_Claim):
    """How much of the variation in ``value`` the groups account for (eta, 0 to 1), with the
    same pooling as ``GroupClaim``."""

    type: Literal["eta"] = "eta"
    group: str
    value: Value


class CorrelationClaim(_Claim):
    """Spearman's rank correlation of two numeric columns (its size when ``absolute``), over
    rows with both values, within one group when ``group`` is set."""

    type: Literal["correlation"] = "correlation"
    x: str
    y: str
    absolute: bool = False
    group: str | None = None
    key: str | None = None


class ShareClaim(_Claim):
    """The share of rows whose ``column`` is ``key`` (or, with ``key`` = None, any value not in
    ``exclude``) among rows with a value, within the rows whose ``given`` column is
    ``given_key`` when set. ``n`` is the matching rows (``hits``) or the rows considered
    (``base``)."""

    type: Literal["share"] = "share"
    column: str
    key: str | None
    exclude: tuple[str, ...] = ()
    given: str | None = None
    given_key: str | None = None
    n: Literal["hits", "base"] = "hits"


class BinClaim(_Claim):
    """Rows whose numeric ``column`` falls in bin ``index`` of ``bins`` equal bins starting at
    ``low`` (values below the first bin count in it, values above the last in that one)."""

    type: Literal["bin"] = "bin"
    column: str
    low: float
    width: float
    index: int
    bins: int


class PeriodClaim(_Claim):
    """Rows whose date ``column`` falls in the ``grain`` starting at ``start`` (ISO), or, with
    ``value``, their average of it."""

    type: Literal["period"] = "period"
    column: str
    grain: Literal["day", "week", "month", "quarter", "year"]
    start: str
    value: Value | None = None


class PointClaim(_Claim):
    """At least one row has exactly this pair of numeric values (a scatter point)."""

    type: Literal["point"] = "point"
    x: str
    y: str


class OutlierClaim(_Claim):
    """Rows whose numeric ``column`` lies beyond Tukey's fences (one and a half interquartile
    ranges outside the middle half); ``n`` is the rows with a value."""

    type: Literal["outliers"] = "outliers"
    column: str


class MissingClaim(_Claim):
    """The average of ``value`` over rows where ``column`` is missing (or present). A group
    column is missing when blank; any other column when null."""

    type: Literal["missing"] = "missing"
    column: str
    group: bool
    value: Value
    missing: bool


class RowsClaim(_Claim):
    """A group too small to show, drawn without its number: at least one row has this value
    of ``column``, and fewer than ``below`` of them have a finite ``value`` (or fewer than
    ``below`` rows at all, without ``value``)."""

    type: Literal["rows"] = "rows"
    column: str
    key: str
    value: Value | None = None
    below: int = POOL_BELOW


Claim = Annotated[
    GroupClaim
    | EtaClaim
    | CorrelationClaim
    | ShareClaim
    | BinClaim
    | PeriodClaim
    | PointClaim
    | OutlierClaim
    | MissingClaim
    | RowsClaim,
    Field(discriminator="type"),
]
