"""The signal gate: a planned feature joins the analysis only if it carries information about
the outcome that chance does not (system map: Features and insight mining).

For each enabled feature of the plan (date parts, ratios, bands, text length, ...):

1. Compute it on the cleaned table (a feature whose inputs did not parse is not computed).
2. Measure its mutual information with the outcome, with the Miller-Madow bias correction,
   and the same for 19 copies with the outcome shuffled. Keep it only if it beats all 19:
   a feature with no signal passes one time in twenty.
3. Drop a kept feature that is nearly the same as one of its own columns or an earlier kept
   feature (rank correlation 0.95 or more): it would only repeat what they say.

Without an outcome there is nothing to gate on, so no feature is added. The planner can propose
features; it cannot get one past the gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Final, Literal

import duckdb
import numpy as np

from insightfulcell.engine import features as catalogue
from insightfulcell.engine.analysis import stats
from insightfulcell.engine.analysis.facts import MIN_ROWS
from insightfulcell.engine.planning.language import (
    DatePart,
    FeatureKind,
    ResolvedFeature,
    ResolvedPlan,
    TargetKind,
)
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import NUMERIC_MEANINGS, Meaning

SAMPLE_ROWS: Final = 200_000
SHUFFLES: Final = 19
REDUNDANT: Final = 0.95

Outcome = Literal["kept", "no_signal", "redundant", "not_computed", "no_target"]

_PARTS: Final = {
    DatePart.YEAR: "Year",
    DatePart.QUARTER: "Quarter",
    DatePart.MONTH: "Month",
    DatePart.WEEKDAY: "Weekday",
    DatePart.HOUR: "Hour",
    DatePart.IS_WEEKEND: "Weekend",
}
_GROUPS: Final = frozenset({FeatureKind.DATE_PART, FeatureKind.BAND, FeatureKind.IS_MISSING})


@dataclass(frozen=True, slots=True)
class GateResult:
    id: str
    name: str  # the column it becomes
    label: str
    kind: str
    columns: tuple[str, ...]
    meaning: Meaning
    expression: str
    outcome: Outcome
    mutual_information: float | None = None
    best_shuffle: float | None = None
    rows: int = 0
    redundant_with: str | None = None


def label(feature: ResolvedFeature, names: dict[str, str]) -> str:
    """A feature in words, from its columns' display names ("Weekday of Order date")."""
    cols = [names.get(c, c) for c in feature.columns]
    a = cols[0]
    b = cols[1] if len(cols) > 1 else ""
    kind = feature.kind
    if kind is FeatureKind.DATE_PART:
        return f"{_PARTS.get(feature.part or DatePart.YEAR, 'Year')} of {a}"
    words = {
        FeatureKind.DAYS_BETWEEN: f"Days from {a} to {b}",
        FeatureKind.RATIO: f"{a} per {b}",
        FeatureKind.DIFFERENCE: f"{a} minus {b}",
        FeatureKind.LOG: f"Log of {a}",
        FeatureKind.BAND: f"{a} band",
        FeatureKind.TEXT_LENGTH: f"Length of {a}",
        FeatureKind.WORD_COUNT: f"Words in {a}",
        FeatureKind.IS_MISSING: f"{a} is missing",
    }
    return words[kind]


def meaning(kind: FeatureKind) -> Meaning:
    """Date parts, bands and missing flags are groups; the rest are measures."""
    return Meaning.NOMINAL if kind in _GROUPS else Meaning.CONTINUOUS


def gate(
    con: duckdb.DuckDBPyConnection, source: str, plan: ResolvedPlan, *, seed: str
) -> list[GateResult]:
    present = {row[0] for row in con.execute(f"DESCRIBE {ident(source)}").fetchall()}
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    target = plan.target
    results: list[GateResult] = []
    for feature in plan.features:
        if not feature.enabled:
            continue
        expression = catalogue.expression(feature.kind, feature.columns, feature.part, feature.bins)
        base = GateResult(
            id=feature.id,
            name=feature.name,
            label=label(feature, names),
            kind=feature.kind.value,
            columns=feature.columns,
            meaning=meaning(feature.kind),
            expression=expression,
            outcome="no_target",
        )
        if target is None:
            results.append(base)
            continue
        if feature.name in present or any(c not in present for c in feature.columns):
            results.append(replace(base, outcome="not_computed"))
            continue
        results.append(_score(con, source, base, target.column, target.kind, seed))
    return _without_redundant(con, source, results, plan)


def _target_sql(column: str, kind: TargetKind) -> str:
    if kind is TargetKind.NUMERIC:
        return f"TRY_CAST({ident(column)} AS DOUBLE)"
    return f"NULLIF(trim(CAST({ident(column)} AS VARCHAR)), '')"


def _score(  # noqa: PLR0913, PLR0917  (the feature and where to read it)
    con: duckdb.DuckDBPyConnection,
    source: str,
    base: GateResult,
    target: str,
    kind: TargetKind,
    seed: str,
) -> GateResult:
    y = _target_sql(target, kind)
    try:
        data = con.execute(
            f"SELECT f, y FROM (SELECT {base.expression} AS f, {y} AS y FROM {ident(source)}) "
            f"WHERE f IS NOT NULL AND y IS NOT NULL ORDER BY hash(f, y), f, y LIMIT {SAMPLE_ROWS}"
        ).fetchnumpy()
    except duckdb.Error:  # its inputs were not parsed (left as text): nothing to compute
        return replace(base, outcome="not_computed")
    values, outcome = np.asarray(data["f"]), np.asarray(data["y"])
    if len(values) < 2 * MIN_ROWS:
        return replace(base, outcome="not_computed", rows=len(values))
    rng = np.random.default_rng(stats.seed_for(seed, "gate", base.id))
    mi, best, kept = stats.signal_gate(
        stats.codes(values), stats.codes(outcome), rng, shuffles=SHUFFLES
    )
    return replace(
        base,
        outcome="kept" if kept else "no_signal",
        mutual_information=round(mi, 6),
        best_shuffle=round(best, 6),
        rows=len(values),
    )


def _numeric(con: duckdb.DuckDBPyConnection, source: str, column: str) -> str | None:
    """A source column as a number for comparing with a feature (dates as epoch seconds)."""
    kind = con.execute(
        f"SELECT column_type FROM (DESCRIBE {ident(source)}) WHERE column_name = ?", [column]
    ).fetchone()
    physical = str(kind[0]).upper() if kind else ""
    if physical.startswith(("DATE", "TIMESTAMP")):
        return f"epoch(CAST({ident(column)} AS TIMESTAMP))"
    if physical.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "DOUBLE", "FLOAT",
                            "DECIMAL", "HUGEINT", "REAL", "U")):  # fmt: skip
        return f"CAST({ident(column)} AS DOUBLE)"
    return None


def _without_redundant(
    con: duckdb.DuckDBPyConnection,
    source: str,
    results: Sequence[GateResult],
    plan: ResolvedPlan,
) -> list[GateResult]:
    """Kept features in order of information, each checked against its own columns and the
    features kept before it."""
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    measures = {c.column for c in plan.dictionary if c.meaning in NUMERIC_MEANINGS}
    measures |= {c.column for c in plan.dictionary if c.meaning is Meaning.DATETIME}
    kept_before: list[GateResult] = []
    out = {r.id: r for r in results}
    ordered = sorted(
        (r for r in results if r.outcome == "kept"),
        key=lambda r: (-(r.mutual_information or 0.0), r.id),
    )
    for result in ordered:
        others: list[tuple[str, str]] = []
        for column in result.columns:
            expr = _numeric(con, source, column) if column in measures else None
            if expr is not None:
                others.append((names.get(column, column), expr))
        others += [(k.label, f"CAST(({k.expression}) AS DOUBLE)") for k in kept_before]
        twin = next(
            (label for label, expr in others if _similar(con, source, result.expression, expr)),
            None,
        )
        if twin is None:
            kept_before.append(result)
        else:
            out[result.id] = replace(result, outcome="redundant", redundant_with=twin)
    return [out[r.id] for r in results]


def _similar(con: duckdb.DuckDBPyConnection, source: str, feature: str, other: str) -> bool:
    try:
        data = con.execute(
            f"SELECT a, b FROM (SELECT CAST(({feature}) AS DOUBLE) AS a, {other} AS b "
            f"FROM {ident(source)}) WHERE a IS NOT NULL AND b IS NOT NULL AND isfinite(a) "
            f"AND isfinite(b) ORDER BY hash(a, b), a, b LIMIT {SAMPLE_ROWS}"
        ).fetchnumpy()
    except duckdb.Error:
        return False
    a, b = np.asarray(data["a"], dtype=float), np.asarray(data["b"], dtype=float)
    test = stats.spearman("redundancy", ("a", "b"), a, b) if len(a) >= MIN_ROWS else None
    return test is not None and abs(test.effect) >= REDUNDANT


def kept(results: Sequence[GateResult]) -> list[GateResult]:
    return [r for r in results if r.outcome == "kept"]


def to_json(result: GateResult) -> dict[str, Any]:
    data = asdict(result)
    data["columns"] = list(result.columns)
    data["meaning"] = result.meaning.value
    return data


def from_json(data: Mapping[str, Any]) -> GateResult:
    return GateResult(
        **{
            **data,
            "columns": tuple(data["columns"]),
            "meaning": Meaning(data["meaning"]),
        }
    )
