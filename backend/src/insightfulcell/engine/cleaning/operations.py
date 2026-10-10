"""The cleaning operation catalogue and its SQL.

Each operation is one SQL expression over the column as the previous operation left it, so a
column's cleaning is a chain read top to bottom. The same expressions compute the review
preview (rows affected, before/after), will build the cleaned (silver) data in Phase B, and
are written verbatim into the exported Python recipe. One definition, three uses.

System map, Cleaning:

- Trim, normalise Unicode, standardise missing tokens, fix category case: lossless, automatic.
- Parse numbers and dates: lossless if at least 99% parse (automatic), else review; an
  ambiguous day/month order always goes to review.
- Merge near-duplicate categories: lossy, review, never automatic.
- Drop exact duplicates; set invalid values to null: lossy, review.
- Exclude a column (identifier, personal data, constant, leakage): analysis only, automatic,
  with a notice.
- Impute; winsorize: lossy, off unless you ask.

No policy ever applies a lossy operation without a human decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from insightfulcell.engine import expressions as ex
from insightfulcell.engine.planning.language import CleaningKind, Source
from insightfulcell.engine.sql import ident, literal, number


class Policy(StrEnum):
    ALWAYS_REVIEW = "always_review"
    REVIEW_LOSSY = "review_lossy"
    AUTO_SAFE = "auto_safe"


class Default(StrEnum):
    AUTO = "auto"  # applied unless a person rejects it
    REVIEW = "review"  # needs a person's decision
    OPT_IN = "opt_in"  # off unless a person turns it on


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class Spec:
    label: str
    order: int  # position in a column's chain
    table_level: bool = False
    text_only: bool = False


CATALOGUE: Final[dict[CleaningKind, Spec]] = {
    CleaningKind.NORMALIZE_UNICODE: Spec("Normalise Unicode", 10, text_only=True),
    CleaningKind.TRIM_WHITESPACE: Spec("Trim spaces", 20, text_only=True),
    CleaningKind.STANDARDIZE_MISSING: Spec(
        "Treat missing-value words as empty", 30, text_only=True
    ),
    CleaningKind.FIX_CATEGORY_CASE: Spec("Unify capitalisation", 40, text_only=True),
    CleaningKind.MERGE_CATEGORIES: Spec("Merge near-duplicate categories", 50, text_only=True),
    CleaningKind.PARSE_BOOLEANS: Spec("Read yes/no answers", 60, text_only=True),
    CleaningKind.PARSE_PERCENTAGES: Spec("Read percentages as fractions", 60, text_only=True),
    CleaningKind.PARSE_NUMBERS: Spec("Read numbers written as text", 60, text_only=True),
    CleaningKind.PARSE_DATES: Spec("Read dates written as text", 60, text_only=True),
    CleaningKind.SET_INVALID_TO_NULL: Spec("Set impossible values to empty", 70),
    CleaningKind.WINSORIZE: Spec("Cap extreme values", 80),
    CleaningKind.IMPUTE: Spec("Fill empty values", 90),
    CleaningKind.EXCLUDE_COLUMN: Spec("Leave out of the analysis", 100),
    CleaningKind.DROP_DUPLICATES: Spec("Drop exact duplicate rows", 200, table_level=True),
}

PARSE_KINDS: Final = frozenset(
    {
        CleaningKind.PARSE_NUMBERS,
        CleaningKind.PARSE_PERCENTAGES,
        CleaningKind.PARSE_DATES,
        CleaningKind.PARSE_BOOLEANS,
    }
)
#: At least this share must parse for a parse to count as lossless (system map).
LOSSLESS_PARSE_SHARE: Final = 0.99


class PreviewPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: str | None
    after: str | None
    rows: int


class Operation(BaseModel):
    """One proposed change. ``id`` is stable for the same change to the same column, so a
    decision can be matched to it and carried over."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: CleaningKind
    column: str | None
    params: dict[str, Any]
    lossy: bool
    default: Default
    reason: str
    source: Source
    order: int
    rows_affected: int = 0
    values_lost: int = 0  # non-empty values this change empties
    preview: tuple[PreviewPair, ...] = ()
    decision: Decision = Decision.PENDING
    notice: str | None = None

    @property
    def label(self) -> str:
        return CATALOGUE[self.kind].label


def operation_id(kind: CleaningKind, column: str | None) -> str:
    return f"{kind.value}:{column if column is not None else '*'}"


def expression(kind: CleaningKind, value: str, params: Mapping[str, Any]) -> str:  # noqa: PLR0911
    """The SQL for one operation applied to the expression ``value``."""
    if kind is CleaningKind.NORMALIZE_UNICODE:
        return f"nfc_normalize({value})"
    if kind is CleaningKind.TRIM_WHITESPACE:
        return f"trim(replace({value}, chr(160), ' '))"
    if kind is CleaningKind.STANDARDIZE_MISSING:
        return f"(CASE WHEN {ex.is_missing_token(value)} THEN NULL ELSE {value} END)"
    if kind in {CleaningKind.FIX_CATEGORY_CASE, CleaningKind.MERGE_CATEGORIES}:
        pairs = params.get("value_map") or []
        if not pairs:
            return value
        cases = " ".join(f"WHEN {literal(a)} THEN {literal(b)}" for a, b in pairs)
        return f"(CASE {value} {cases} ELSE {value} END)"
    if kind is CleaningKind.PARSE_NUMBERS:
        return ex.number_from_text(value)
    if kind is CleaningKind.PARSE_PERCENTAGES:
        return ex.percentage_from_text(value)
    if kind is CleaningKind.PARSE_BOOLEANS:
        return ex.boolean_from_text(value)
    if kind is CleaningKind.PARSE_DATES:
        return ex.datetime_from_text(value, ex.date_formats(bool(params.get("day_first", True))))
    if kind is CleaningKind.SET_INVALID_TO_NULL:
        return _invalid_to_null(value, params)
    if kind is CleaningKind.WINSORIZE:
        low, high = number(float(params["lower"])), number(float(params["upper"]))
        return f"least(greatest({value}, {low}), {high})"
    if kind is CleaningKind.IMPUTE:
        fill = params["value"]
        fill_sql = number(float(fill)) if isinstance(fill, int | float) else literal(str(fill))
        return f"coalesce({value}, {fill_sql})"
    return value  # exclude_column and drop_duplicates do not change values


def _invalid_to_null(value: str, params: Mapping[str, Any]) -> str:
    conditions: list[str] = []
    codes = [float(v) for v in params.get("values") or []]
    if codes:
        conditions.append(f"{value} IN ({', '.join(number(v) for v in codes)})")
    if params.get("lower_bound") is not None:
        conditions.append(f"{value} < {number(float(params['lower_bound']))}")
    if params.get("upper_bound") is not None:
        conditions.append(f"{value} > {number(float(params['upper_bound']))}")
    if params.get("not_after"):
        conditions.append(f"{value} > CAST({literal(str(params['not_after']))} AS TIMESTAMP)")
    if not conditions:
        return value
    return f"(CASE WHEN {' OR '.join(conditions)} THEN NULL ELSE {value} END)"


def column_chain(column: str, operations: Sequence[Operation], *, applied: bool = True) -> str:
    """The column's cleaned expression: each accepted operation applied in catalogue order.
    With ``applied=False`` every operation is applied regardless of its decision (used to
    estimate the effect of the plan as proposed)."""
    value = ident(column)
    for op in sorted(
        (o for o in operations if o.column == column),
        key=lambda o: o.order,
    ):
        if applied and op.decision is not Decision.ACCEPTED:
            continue
        value = expression(op.kind, value, op.params)
    return value


def cleaned_select(
    columns: Sequence[str], operations: Sequence[Operation], *, table: str = "t"
) -> str:
    """``SELECT`` producing the cleaned table: every column through its accepted chain, exact
    duplicates dropped when that operation is accepted. Excluded columns stay in the data
    (exclusion is analysis-only); the plan records them."""
    select = ",\n       ".join(
        f"{column_chain(name, operations)} AS {ident(name)}" for name in columns
    )
    dedupe = any(
        o.kind is CleaningKind.DROP_DUPLICATES and o.decision is Decision.ACCEPTED
        for o in operations
    )
    return f"SELECT {'DISTINCT ' if dedupe else ''}{select}\nFROM {ident(table)}"


def initial_decision(op: Operation, policy: Policy) -> tuple[Decision, str | None]:
    """How an operation starts out under the organisation's policy, and why, if not applied."""
    if op.default is Default.OPT_IN:
        return Decision.REJECTED, "Off unless you turn it on."
    needs_person = op.lossy or op.default is Default.REVIEW
    if policy is Policy.ALWAYS_REVIEW:
        return Decision.PENDING, None
    if needs_person:
        if policy is Policy.AUTO_SAFE:
            return (
                Decision.REJECTED,
                "Not applied: your organisation applies only safe changes automatically.",
            )
        return Decision.PENDING, None
    return Decision.ACCEPTED, op.notice


def needs_review(operations: Sequence[Operation]) -> bool:
    return any(op.decision is Decision.PENDING for op in operations)
