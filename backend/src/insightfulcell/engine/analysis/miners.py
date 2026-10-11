"""The nine miners: each planned question answered with facts, a chart and a hypothesis test.

Input: the analysis table as view ``a`` (the cleaned data plus resolved regions and the
features that passed the signal gate) and the resolved plan. Output, per analysis, a
``Finding``: its facts (numbers with provenance), a chart, or the reason it was skipped; and for
the run, every test it made.

How a finding becomes a fact:

1. Each miner computes descriptive numbers in DuckDB and, where the question is a hypothesis
   ("do these differ?", "are these linked?"), one primary test (``stats``): rank-based where a
   parametric test would assume normality, with an effect size, a 95% interval and a p-value.
2. Benjamini-Hochberg at 5% runs across every test of the run, so the share of false
   discoveries among everything reported stays within 5%.
3. Only then is each fact worded: a difference that does not survive is stated as "no reliable
   difference", never as a finding, and the fact's tier records the strength of evidence.

Effect sizes are put into words by fixed thresholds (``facts.strength``). Groups with fewer than
ten rows are never reported on their own (they are pooled as "other groups"), which keeps
numbers stable and keeps small groups of people from being singled out. Nothing here claims a
cause. A driver is reported only after a gradient-boosting model shows skill on rows it did not
see, and a column that is almost the outcome itself is flagged as possible leakage instead.

Everything is deterministic for a given run: samples are chosen by hashing row values, random
draws use generators seeded from the run id and the hypothesis, and the stage gives DuckDB one
thread, so a retry computes exactly the same facts. Analyses run one after another: measured,
threads were no faster (the resampling holds the GIL and the boosting model already uses every
core), and a process pool would copy the table into each process.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import partial
from typing import Any, Final, Literal

import duckdb
import numpy as np

from insightfulcell.engine.analysis import facts as fmt
from insightfulcell.engine.analysis import stats
from insightfulcell.engine.analysis.claims import (
    BinClaim,
    Claim,
    CorrelationClaim,
    EtaClaim,
    GroupClaim,
    MissingClaim,
    OutlierClaim,
    PeriodClaim,
    PointClaim,
    ShareClaim,
    Value,
)
from insightfulcell.engine.analysis.facts import (
    MIN_ROWS,
    STRONG_Q,
    ChartData,
    ChartPoint,
    Fact,
    FactTest,
    Tier,
)
from insightfulcell.engine.analysis.stats import FloatArray, IntArray, TestResult
from insightfulcell.engine.planning.language import MinerKind, ResolvedAnalysis, ResolvedPlan
from insightfulcell.engine.semantics import name_tokens
from insightfulcell.engine.sql import ident, literal
from insightfulcell.engine.types import NUMERIC_MEANINGS, Meaning

logger = logging.getLogger(__name__)

TABLE: Final = "a"
TOP_GROUPS: Final = 12
SCATTER_POINTS: Final = 300
HISTOGRAM_BINS: Final = 16
MAX_DRIVER_CANDIDATES: Final = 30
DRIVER_FACTS: Final = 5
DRIVER_SAMPLE_ROWS: Final = 100_000
DRIVER_MIN_ROWS: Final = 200
DRIVER_MIN_R2: Final = 0.02
DRIVER_MIN_AUC: Final = 0.55
LEAKAGE: Final = 0.95
MIN_PERIODS: Final = 3
OTHER: Final = "Other groups"
_COORDINATES: Final = frozenset({"lat", "latitude", "lon", "lng", "long", "longitude"})
_YES: Final = frozenset({"true", "yes", "y", "t", "1"})
_NO: Final = frozenset({"false", "no", "n", "f", "0"})
_SIGNIFICANT_DIGITS: Final = 12


@dataclass(frozen=True, slots=True)
class Derived:
    """A column the analysis adds to the cleaned data: a region resolved from place names or
    coordinates (``replaces`` names the place column whose groups it standardises), or a
    feature that passed the signal gate."""

    name: str
    label: str
    meaning: Meaning
    unit: str | None = None
    replaces: str | None = None
    origin: tuple[str, ...] = ()  # the columns it was derived from


@dataclass(frozen=True, slots=True)
class Note:
    """A descriptive fact an earlier stage worked out (how well a place column matched the
    reference set), stated under one analysis."""

    kind: str
    columns: tuple[str, ...]
    rows: int
    method: str
    statement: str
    numbers: tuple[str, ...]
    labels: tuple[str, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    label: str
    meaning: Meaning
    unit: str | None
    kind: str  # numeric, categorical, datetime, text
    read: str = ""  # the column holding its groups (a standardised region), if not itself
    derived: bool = False  # added by the analysis (a feature, a region from coordinates)

    @property
    def sql(self) -> str:
        return ident(self.name)

    @property
    def groups(self) -> str:
        """The analysis-table column holding its groups."""
        return self.read or self.name

    @property
    def groups_sql(self) -> str:
        return ident(self.groups)

    @property
    def drill(self) -> str | None:
        """The plan column a drill-down filters on (none for derived columns)."""
        return None if self.derived else self.name


@dataclass(slots=True)
class Finding:
    analysis: ResolvedAnalysis
    facts: list[Fact] = field(default_factory=list)
    chart: ChartData | None = None
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class LoggedTest:
    """One row of the test log: the test, and the fact it supports (if any)."""

    test: TestResult
    fact_id: str | None


@dataclass(frozen=True, slots=True)
class Mined:
    findings: list[Finding]
    tests: list[LoggedTest]
    #: The largest p-value that passed Benjamini-Hochberg (0 when none did).
    threshold: float


@dataclass(slots=True)
class _Draft:
    """A fact before the run's false-discovery control decides its wording."""

    kind: str
    columns: tuple[str, ...]
    rows: int
    method: str
    statement: str
    numbers: tuple[str, ...]
    labels: tuple[str, ...] = ()
    values: dict[str, Any] = field(default_factory=dict)
    test: TestResult | None = None
    #: Wording (statement, numbers) when the test does not survive.
    unreliable: tuple[str, tuple[str, ...]] | None = None
    #: Leave the fact out entirely when its test does not survive (driver candidates).
    omit_if_unreliable: bool = False


@dataclass(slots=True)
class _Work:
    finding: Finding
    drafts: list[_Draft] = field(default_factory=list)
    run_seed: str = ""  # tests draw from (run, hypothesis); the driver model from the analysis


class _Skip(Exception):
    """The question cannot be answered on this data; the message says why, in plain words."""


def mine(  # noqa: PLR0913  (keyword-only)
    con: duckdb.DuckDBPyConnection,
    plan: ResolvedPlan,
    *,
    seed: str = "",
    derived: Sequence[Derived] = (),
    extra: Sequence[ResolvedAnalysis] = (),
    only: frozenset[str] | None = None,
    notes: Mapping[str, Sequence[Note]] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> Mined:
    """Answer every planned analysis (plus ``extra``, e.g. the geographic ones), or only those
    in ``only``. ``notes`` are stated first under their analysis; an analysis with no columns
    holds only its notes. Fact ids (F1, F2, ...) follow the analyses' order."""
    columns = _columns(con, plan, derived)
    target = plan.target.column if plan.target else None
    analyses = [a for a in (*plan.analyses, *extra) if only is None or a.id in only]

    def work(analysis: ResolvedAnalysis) -> _Work:
        item = _Work(Finding(analysis), run_seed=seed)
        stated = [_noted(n) for n in (notes or {}).get(analysis.id, ())]
        if analysis.columns:
            try:
                _answer(con, item, columns, target)
            except _Skip as reason:
                item.drafts, item.finding.chart, item.finding.skipped = [], None, str(reason)
            except (duckdb.Error, ValueError, FloatingPointError):
                logger.warning("miner failed", extra={"analysis": analysis.kind.value})
                item.drafts, item.finding.chart = [], None
                item.finding.skipped = "It could not be computed on this data."
        if stated:
            item.drafts = [*stated, *item.drafts]
            item.finding.skipped = None
        elif not analysis.columns:
            item.finding.skipped = "There was nothing to report."
        return item

    return _finish([work(a) for a in analyses], dict(provenance or {}))


def _finish(done: Sequence[_Work], provenance: dict[str, Any]) -> Mined:
    # One primary test per hypothesis: a comparison asked twice (a segment and a benchmark of
    # the same columns) is the same test, counted once in the false-discovery control.
    unique: dict[str, TestResult] = {}
    for item in done:
        for draft in item.drafts:
            if draft.test is not None:
                unique.setdefault(draft.test.hypothesis, draft.test)
    controlled, threshold = stats.benjamini_hochberg(list(unique.values()))
    by_hypothesis = {t.hypothesis: t for t in controlled}
    number = 0
    logged: list[LoggedTest] = []
    seen: set[str] = set()

    def log(test: TestResult, fact_id: str | None) -> None:
        if test.hypothesis not in seen:
            seen.add(test.hypothesis)
            logged.append(LoggedTest(test, fact_id))

    for item in done:
        item.finding.facts = []
        drivers_kept = 0
        for draft in item.drafts:
            test = by_hypothesis.get(draft.test.hypothesis) if draft.test else None
            passed = test is not None and test.passed
            if draft.omit_if_unreliable:
                if not passed or drivers_kept >= DRIVER_FACTS:
                    continue
                drivers_kept += 1
            number += 1
            fact = _fact(f"F{number}", item.finding.analysis.id, draft, test, provenance)
            item.finding.facts.append(fact)
            if test is not None:
                log(test, fact.id)
    for test in controlled:  # tests no fact rests on (driver candidates that did not survive)
        log(test, None)
        if not item.finding.facts and item.finding.skipped is None:
            item.finding.chart = None
            item.finding.skipped = "No column showed a reliable link with it."
    return Mined([w.finding for w in done], logged, threshold)


def _fact(
    fid: str,
    analysis_id: str,
    draft: _Draft,
    test: TestResult | None,
    provenance: dict[str, Any],
) -> Fact:
    tier: Tier = "descriptive"
    statement, numbers = draft.statement, draft.numbers
    if test is not None:
        q = test.q_value if test.q_value is not None else 1.0
        tier = "strong" if q <= STRONG_Q else ("supported" if test.passed else "inconclusive")
        if not test.passed and draft.unreliable is not None:
            statement, numbers = draft.unreliable
    return Fact(
        id=fid,
        analysis_id=analysis_id,
        kind=draft.kind,
        columns=draft.columns,
        rows=int(draft.rows),
        method=draft.method,  # the test, if any, is named in ``test``
        statement=statement,
        numbers=numbers,
        labels=draft.labels,
        values={k: _json(v) for k, v in draft.values.items()},
        tier=tier,
        test=_fact_test(test) if test is not None else None,
        provenance=provenance,
    )


def _fact_test(test: TestResult) -> FactTest:
    return FactTest(
        hypothesis=test.hypothesis,
        method=test.method,
        n=test.n,
        effect_name=test.effect_name,
        effect=_canonical(test.effect),
        ci_low=_canonical(test.ci_low) if test.ci_low is not None else None,
        ci_high=_canonical(test.ci_high) if test.ci_high is not None else None,
        p_value=_canonical(test.p_value),
        q_value=_canonical(test.q_value if test.q_value is not None else 1.0),
        size=_canonical(test.size),
    )


def _noted(note: Note) -> _Draft:
    return _Draft(
        kind=note.kind,
        columns=note.columns,
        rows=note.rows,
        method=note.method,
        statement=note.statement,
        numbers=note.numbers,
        labels=note.labels,
        values=dict(note.values),
    )


def _canonical(value: float) -> float:
    """Floats rounded to 12 significant digits, so the stored fact (and its content hash) does
    not depend on the last bits of a floating-point sum."""
    if not math.isfinite(value) or value == 0:
        return float(value) if math.isfinite(value) else 0.0
    return float(f"{value:.{_SIGNIFICANT_DIGITS}g}")


def _answer(
    con: duckdb.DuckDBPyConnection,
    item: _Work,
    columns: dict[str, Column],
    target: str | None,
) -> None:
    analysis = item.finding.analysis
    miner = _MINERS.get(analysis.kind)
    if miner is None:
        raise _Skip("This kind of question is not answered yet.")
    if any(c not in columns for c in analysis.columns):
        raise _Skip("A column of this question is not in the cleaned data.")
    miner(_Ctx(con, item, columns, target), [columns[c] for c in analysis.columns])
    if not item.drafts:
        raise _Skip("There was not enough data to answer it.")


@dataclass(slots=True)
class _Ctx:
    con: duckdb.DuckDBPyConnection
    item: _Work
    every: dict[str, Column]
    target: str | None

    @property
    def rng(self) -> np.random.Generator:
        """Draws for this analysis (the driver model's split)."""
        return np.random.default_rng(
            stats.seed_for(self.item.run_seed, self.item.finding.analysis.id)
        )

    def rng_for(self, hypothesis: str) -> np.random.Generator:
        """Draws for one test: the same hypothesis gets the same draws in any analysis."""
        return np.random.default_rng(stats.seed_for(self.item.run_seed, hypothesis))

    @staticmethod
    def hypothesis(family: str, *columns: str) -> str:
        """What is tested, by the data it reads: ``groups:region|sales``."""
        return f"{family}:{'|'.join(columns)}"

    def add(self, draft: _Draft) -> None:
        self.item.drafts.append(draft)

    def chart(self, chart: ChartData | None) -> None:
        self.item.finding.chart = chart


# --------------------------------------------------------------------------- columns


def _columns(
    con: duckdb.DuckDBPyConnection, plan: ResolvedPlan, derived: Sequence[Derived]
) -> dict[str, Column]:
    present = {row[0] for row in con.execute(f"DESCRIBE {ident(TABLE)}").fetchall()}
    regions = {d.replaces: d.name for d in derived if d.replaces and d.name in present}
    out: dict[str, Column] = {}
    for entry in plan.dictionary:
        if entry.column not in present or entry.personal_data:
            continue
        meaning = Meaning(entry.meaning)
        kind = _kind(con, entry.column, meaning)
        out[entry.column] = Column(
            name=entry.column,
            label=entry.display_name or entry.column,
            meaning=meaning,
            unit=entry.unit,
            kind=kind,
            read=regions.get(entry.column, "") if kind == "categorical" else "",
        )
    for d in derived:
        if d.name not in present or d.name in out or d.replaces:
            continue
        out[d.name] = Column(
            name=d.name,
            label=d.label,
            meaning=d.meaning,
            unit=d.unit,
            kind=_kind(con, d.name, d.meaning),
            derived=True,
        )
    return out


def _kind(con: duckdb.DuckDBPyConnection, name: str, meaning: Meaning) -> str:
    if meaning in NUMERIC_MEANINGS:
        return "numeric"
    if meaning is Meaning.DATETIME:
        return "datetime"
    if meaning is Meaning.TEXT:
        return "text"
    if meaning is Meaning.GEOGRAPHY and set(name_tokens(name)) & _COORDINATES:
        c = ident(name)
        row = con.execute(
            f"SELECT count({c}), count(TRY_CAST({c} AS DOUBLE)) FROM {ident(TABLE)}"
        ).fetchone()
        if row and row[0] and row[1] / row[0] >= 0.95:
            return "numeric"
    # Places (states, postcodes) are groups even when written as numbers.
    return "categorical"


def _level(col: Column, value: str) -> str:
    """How a group or level reads in a sentence: yes/no values carry their column's name."""
    folded = value.casefold()
    if folded in _YES:
        return f"{col.label} (yes)"
    if folded in _NO:
        return f"{col.label} (no)"
    return value


def _num(col: Column) -> str:
    return f"TRY_CAST({col.sql} AS DOUBLE)"


def _cat(col: Column) -> str:
    return f"NULLIF(trim(CAST({col.groups_sql} AS VARCHAR)), '')"


def _when(col: Column) -> str:
    return f"TRY_CAST({col.sql} AS TIMESTAMP)"


def _value(col: Column, value: float) -> str:
    return fmt.measure(value, col.meaning, col.unit)


def _json(value: Any) -> Any:
    if isinstance(value, float | np.floating):
        number = float(value)
        return _canonical(number) if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _with_interval(
    text: str, test: TestResult | None, write: Callable[[float], str]
) -> tuple[str, list[str]]:
    """``text`` plus "; 95% interval a to b" when the test has one, and the numbers added."""
    if test is None or test.ci_low is None or test.ci_high is None:
        return text, []
    low, high = write(test.ci_low), write(test.ci_high)
    return f"{text}; 95% interval {low} to {high}", [low, high]


# --------------------------------------------------------------------------- data access


def _pairs(
    con: duckdb.DuckDBPyConnection, x: str, y: str, where: str = "TRUE"
) -> tuple[FloatArray, FloatArray]:
    """Rows with both numeric values, in a fixed order."""
    data = con.execute(
        f"SELECT x, y FROM (SELECT {x} AS x, {y} AS y FROM {ident(TABLE)} WHERE {where}) "
        "WHERE x IS NOT NULL AND y IS NOT NULL AND isfinite(x) AND isfinite(y) ORDER BY x, y"
    ).fetchnumpy()
    return np.asarray(data["x"], dtype=float), np.asarray(data["y"], dtype=float)


@dataclass(frozen=True, slots=True)
class _Groups:
    labels: list[str]  # by code; the pooled small groups are OTHER
    codes: IntArray
    values: FloatArray
    summary: list[tuple[str, int, float]]  # (label, rows, mean), highest mean first
    grand: float

    @property
    def real(self) -> list[tuple[str, int, float]]:
        return [g for g in self.summary if g[0] != OTHER]

    @property
    def compared(self) -> list[tuple[str, int, float]]:
        """The groups a sentence names as highest and lowest: the real groups, or, when only
        one has ten rows, that group against the pooled other groups."""
        return self.real if len(self.real) >= 2 else self.summary

    @property
    def rows(self) -> int:
        return len(self.values)

    def shown(self) -> list[tuple[str, int, float]]:
        """For the chart: at most ``TOP_GROUPS``, the largest groups, highest mean first."""
        if len(self.summary) <= TOP_GROUPS:
            return self.summary
        biggest = sorted(self.summary, key=lambda g: (-g[1], g[0]))[:TOP_GROUPS]
        return sorted(biggest, key=lambda g: -g[2])


def _grouped(con: duckdb.DuckDBPyConnection, group: str, value: str) -> _Groups | None:
    """Values per group for groups with at least ten rows; the rest pooled as "Other groups"
    when the pool itself has ten rows, otherwise left out."""
    data = con.execute(
        f"SELECT g, v FROM (SELECT {group} AS g, {value} AS v FROM {ident(TABLE)}) "
        "WHERE g IS NOT NULL AND v IS NOT NULL AND isfinite(v) ORDER BY g, v"
    ).fetchnumpy()
    raw = np.asarray(data["g"]).astype(str)
    values = np.asarray(data["v"], dtype=float)
    if len(values) == 0:
        return None
    names, inverse, counts = np.unique(raw, return_inverse=True, return_counts=True)
    keep = counts >= MIN_ROWS
    pooled = int(counts[~keep].sum())
    labels = [str(n) for n in names[keep]]
    code_of = np.full(len(names), -1, dtype=np.int64)
    code_of[np.flatnonzero(keep)] = np.arange(len(labels))
    if pooled >= MIN_ROWS:
        code_of[np.flatnonzero(~keep)] = len(labels)
        labels.append(OTHER)
    codes = code_of[inverse]
    mask = codes >= 0
    codes, values = codes[mask], values[mask]
    if len(values) == 0:
        return None
    n = np.bincount(codes, minlength=len(labels))
    means = np.bincount(codes, weights=values, minlength=len(labels)) / np.maximum(n, 1)
    summary = sorted(
        ((labels[i], int(n[i]), float(means[i])) for i in range(len(labels))),
        key=lambda g: (-g[2], g[0]),
    )
    return _Groups(labels, codes, values, summary, float(values.mean()))


def _binary_levels(con: duckdb.DuckDBPyConnection, col: Column) -> tuple[str, str] | None:
    """(positive, other) when the column has exactly two values. The positive level is the
    one that reads as yes/true, else the less common one."""
    rows = con.execute(
        f"SELECT v, count(*) FROM (SELECT {_cat(col)} AS v FROM {ident(TABLE)}) "
        "WHERE v IS NOT NULL GROUP BY v ORDER BY count(*) DESC, v"
    ).fetchall()
    if len(rows) != 2:
        return None
    values = [str(v) for v, _ in rows]
    yes = [v for v in values if v.casefold() in _YES]
    positive = yes[0] if yes else values[1]
    other = values[0] if positive == values[1] else values[1]
    return positive, other


def _indicator(col: Column, positive: str) -> str:
    c = _cat(col)
    return f"CASE WHEN {c} IS NULL THEN NULL WHEN {c} = {literal(positive)} THEN 1.0 ELSE 0.0 END"


def _crosstab(
    con: duckdb.DuckDBPyConnection, a: Column, b: Column
) -> tuple[list[tuple[str, str, int]], IntArray, IntArray]:
    """Counts per pair of categories, and per-row category codes for the test."""
    data = con.execute(
        f"SELECT x, y FROM (SELECT {_cat(a)} AS x, {_cat(b)} AS y FROM {ident(TABLE)}) "
        "WHERE x IS NOT NULL AND y IS NOT NULL ORDER BY x, y"
    ).fetchnumpy()
    xs, ys = np.asarray(data["x"]).astype(str), np.asarray(data["y"]).astype(str)
    x_names, xi = np.unique(xs, return_inverse=True)
    y_names, yi = np.unique(ys, return_inverse=True)
    pairs, counts = np.unique(xi * len(y_names) + yi, return_counts=True)
    cells = [
        (str(x_names[p // len(y_names)]), str(y_names[p % len(y_names)]), int(c))
        for p, c in zip(pairs.tolist(), counts.tolist(), strict=True)
    ]
    return cells, xi.astype(np.int64), yi.astype(np.int64)


def _bar(  # noqa: PLR0913, PLR0917  (chart fields)
    title: str,
    x_label: str,
    y_label: str,
    points: Sequence[tuple[str, float, int | None]],
    value_format: str,
    unit: str | None = None,
    *,
    note: str | None = None,
    claims: Sequence[Claim] | None = None,
    keys: Sequence[str | None] | None = None,
    drill: str | None = None,
) -> ChartData:
    """A bar chart; ``claims`` (one per point) say what each bar measures, ``keys`` which group
    each bar is, for a drill-down on ``drill``."""
    claimed = list(claims) if claims is not None else [None] * len(points)
    keyed = list(keys) if keys is not None and drill else [None] * len(points)
    if len(claimed) != len(points) or len(keyed) != len(points):
        raise ValueError("one claim and one key per bar")
    return ChartData(
        kind="bar",
        title=title,
        x_label=x_label,
        y_label=y_label,
        value_format=value_format,
        unit=unit,
        points=tuple(
            ChartPoint(label=label, value=value, n=n, claim=claim, key=key)
            for (label, value, n), claim, key in zip(points, claimed, keyed, strict=True)
        ),
        note=note,
        drill_column=drill if any(k is not None for k in keyed) else None,
    )


def _group_claim(
    group: Column, value: Value, label: str, measure: Literal["mean", "gap"] = "mean"
) -> GroupClaim:
    return GroupClaim(
        group=group.groups, value=value, key=None if label == OTHER else label, measure=measure
    )


def _group_key(label: str) -> str | None:
    return None if label == OTHER else label


def _value_format(col: Column) -> str:
    return "percent" if col.meaning is Meaning.PERCENTAGE else "number"


# --------------------------------------------------------------------------- distribution


def _distribution(ctx: _Ctx, cols: list[Column]) -> None:
    col = cols[0]
    if col.kind == "numeric":
        _numeric_distribution(ctx, col)
    elif col.kind == "datetime":
        _date_distribution(ctx, col)
    elif col.kind == "categorical":
        _category_distribution(ctx, col)
    else:
        raise _Skip("Free text is not summarised yet.")


def _numeric_distribution(ctx: _Ctx, col: Column) -> None:
    v = _num(col)
    row = ctx.con.execute(
        f"SELECT count(x), median(x), quantile_cont(x, 0.05), quantile_cont(x, 0.95), avg(x), "
        f"min(x), max(x), count(*) FROM (SELECT {v} AS x FROM {ident(TABLE)})"
    ).fetchone()
    assert row is not None  # noqa: S101
    n, median, p05, p95, mean, lo, hi, total = row
    if not n or n < MIN_ROWS:
        raise _Skip(f"{col.label} has too few numeric values to describe.")
    med, a, b = _value(col, median), _value(col, p05), _value(col, p95)
    numbers: tuple[str, ...] = (med, a, b)
    statement = f"The typical {col.label} is {med}, and nine in ten values lie between {a} and {b}."
    if a == b:  # a nearly constant column
        statement, numbers = (
            f"The typical {col.label} is {med}; nine in ten values are {a}.",
            (med, a),
        )
    ctx.add(
        _Draft(
            kind="distribution",
            columns=(col.name,),
            rows=n,
            method="Median and the 5th and 95th percentiles of the values present.",
            statement=statement,
            numbers=numbers,
            values={"median": median, "p05": p05, "p95": p95, "mean": mean, "min": lo, "max": hi},
        )
    )
    missing = int(total) - int(n)
    if missing >= MIN_ROWS or (total and missing / total >= 0.05):
        part = fmt.share(missing / total)
        ctx.add(
            _Draft(
                kind="missing_share",
                columns=(col.name,),
                rows=int(total),
                method="Rows with no usable value, out of all rows.",
                statement=f"{part} of rows have no {col.label}.",
                numbers=(part,),
                values={"missing": missing, "rows": total},
            )
        )
    ctx.chart(_histogram(ctx.con, col, f"Distribution of {col.label}"))


def _histogram(con: duckdb.DuckDBPyConnection, col: Column, title: str) -> ChartData | None:
    v = _num(col)
    lo, hi = con.execute(
        f"SELECT quantile_cont(x, 0.01), quantile_cont(x, 0.99) "
        f"FROM (SELECT {v} AS x FROM {ident(TABLE)}) WHERE x IS NOT NULL"
    ).fetchone() or (None, None)
    if lo is None or hi is None or hi <= lo:
        return None
    width = (hi - lo) / HISTOGRAM_BINS
    rows = con.execute(
        f"SELECT least({HISTOGRAM_BINS - 1}, greatest(0, floor((x - ?) / ?)))::INTEGER AS b, "
        f"count(*) FROM (SELECT {v} AS x FROM {ident(TABLE)}) WHERE x IS NOT NULL "
        "GROUP BY b ORDER BY b",
        [lo, width],
    ).fetchall()
    counts = dict(rows)
    points = []
    for i in range(HISTOGRAM_BINS):
        start, end = lo + i * width, lo + (i + 1) * width
        label = f"{_value(col, start)} to {_value(col, end)}"
        claim = BinClaim(column=col.name, low=lo, width=width, index=i, bins=HISTOGRAM_BINS)
        points.append(
            ChartPoint(label=label, value=float(counts.get(i, 0)), x=start, x2=end, claim=claim)
        )
    return ChartData(
        kind="histogram",
        title=title,
        x_label=col.label,
        y_label="Rows",
        value_format="count",
        unit=col.unit,
        points=tuple(points),
        note="The lowest and highest one percent are counted in the first and last bars.",
    )


def _date_distribution(ctx: _Ctx, col: Column) -> None:
    w = _when(col)
    first, last, n = ctx.con.execute(
        f"SELECT min(d), max(d), count(d) FROM (SELECT {w} AS d FROM {ident(TABLE)})"
    ).fetchone() or (None, None, 0)
    if not n or n < MIN_ROWS or first is None or last is None:
        raise _Skip(f"{col.label} has too few dates to describe.")
    grain = _grain(first, last)
    series = _period_series(ctx.con, w, "count(*)", grain, where="TRUE")
    busiest = max(series, key=lambda p: p[1])
    a, b = _period_label(first, "day"), _period_label(last, "day")
    peak, peak_n = _period_label(busiest[0], grain), fmt.count(busiest[1])
    ctx.add(
        _Draft(
            kind="date_range",
            columns=(col.name,),
            rows=int(n),
            method=f"Earliest and latest {col.label}, and rows per {grain}.",
            statement=f"{col.label} runs from {a} to {b}; the busiest {grain} is {peak} with "
            f"{peak_n} rows.",
            numbers=(peak_n,),
            labels=(a, b, peak),
            values={"first": first, "last": last, "peak_rows": busiest[1]},
        )
    )
    ctx.chart(
        _line(series, grain, f"Rows per {grain}", col.label, "Rows", "count", None,
              claim=lambda p: PeriodClaim(column=col.name, grain=grain, start=_iso(p)))
    )  # fmt: skip


def _category_distribution(ctx: _Ctx, col: Column) -> None:
    rows = ctx.con.execute(
        f"SELECT v, count(*) FROM (SELECT {_cat(col)} AS v FROM {ident(TABLE)}) "
        "WHERE v IS NOT NULL GROUP BY v ORDER BY count(*) DESC, v"
    ).fetchall()
    total = sum(int(n) for _, n in rows)
    if total < MIN_ROWS:
        raise _Skip(f"{col.label} has too few values to describe.")
    shown = [(str(v), int(n)) for v, n in rows if n >= MIN_ROWS][:TOP_GROUPS]
    if not shown:
        raise _Skip(f"No value of {col.label} appears in at least ten rows.")
    top, top_n = shown[0]
    part, kinds = fmt.share(top_n / total), fmt.count(len(rows))
    ctx.add(
        _Draft(
            kind="category_share",
            columns=(col.name,),
            rows=total,
            method="Rows per value, out of rows with a value.",
            statement=f"The most common {col.label} is {top}, in {part} of rows; there are "
            f"{kinds} different values.",
            numbers=(part, kinds),
            labels=(top,),
            values={"top": top, "top_rows": top_n, "distinct": len(rows)},
        )
    )
    rest = total - sum(n for _, n in shown)
    points = [(label, n / total, n) for label, n in shown]
    claims: list[Claim] = [ShareClaim(column=col.groups, key=label) for label, _ in shown]
    keys: list[str | None] = [label for label, _ in shown]
    if rest >= MIN_ROWS:
        points.append((OTHER, rest / total, rest))
        claims.append(
            ShareClaim(column=col.groups, key=None, exclude=tuple(label for label, _ in shown))
        )
        keys.append(None)
    ctx.chart(_bar(f"Share of rows by {col.label}", col.label, "Share of rows", points, "percent",
                   claims=claims, keys=keys, drill=col.drill))  # fmt: skip


# --------------------------------------------------------------------------- relationships


def _relationship(ctx: _Ctx, cols: list[Column]) -> None:
    a, b = cols
    kinds = {a.kind, b.kind}
    if kinds == {"numeric"}:
        _numeric_pair(ctx, a, b)
    elif kinds == {"numeric", "categorical"}:
        cat, num = (a, b) if a.kind == "categorical" else (b, a)
        _segment_numeric(ctx, cat, num)
    elif kinds == {"categorical"}:
        _category_pair(ctx, a, b)
    elif kinds == {"datetime", "numeric"}:
        when, num = (a, b) if a.kind == "datetime" else (b, a)
        _trend_of(ctx, when, num)
    else:
        raise _Skip("These columns cannot be compared yet.")


def _numeric_pair(ctx: _Ctx, a: Column, b: Column) -> None:
    x, y = _pairs(ctx.con, _num(a), _num(b))
    if len(x) < MIN_ROWS:
        raise _Skip(f"Too few rows have both {a.label} and {b.label}.")
    key = ctx.hypothesis("correlation", *sorted((a.name, b.name)))
    test = stats.spearman(key, (a.name, b.name), x, y)
    if test is None:
        raise _Skip(f"{a.label} or {b.label} has the same value in every row.")
    rho = test.effect
    word, value = fmt.strength(rho), fmt.coefficient(rho)
    direction = "rise together" if rho > 0 else "move in opposite directions"
    if word == "negligible":
        head = f"{a.label} and {b.label} have only a negligible link (rank correlation {value}"
    else:
        head = f"{a.label} and {b.label} {direction}: the link is {word} (rank correlation {value}"
    text, extra = _with_interval(head, test, fmt.coefficient)
    ctx.add(
        _Draft(
            kind="correlation",
            columns=(a.name, b.name),
            rows=len(x),
            method="Spearman rank correlation over rows with both values (from -1 to 1).",
            statement=f"{text}).",
            numbers=(value, *extra),
            values={"spearman": rho, "strength": word},
            test=test,
            unreliable=(
                f"{a.label} and {b.label} show no reliable link in this data "
                f"(rank correlation {value}).",
                (value,),
            ),
        )
    )
    points = ctx.con.execute(
        f"SELECT x, y FROM (SELECT {_num(a)} AS x, {_num(b)} AS y FROM {ident(TABLE)}) "
        "WHERE x IS NOT NULL AND y IS NOT NULL AND isfinite(x) AND isfinite(y) "
        f"ORDER BY hash(x, y), x, y LIMIT {SCATTER_POINTS}"
    ).fetchall()
    ctx.chart(
        ChartData(
            kind="scatter",
            title=f"{b.label} against {a.label}",
            x_label=a.label,
            y_label=b.label,
            value_format=_value_format(b),
            unit=b.unit,
            points=tuple(
                ChartPoint(
                    label=f"{_value(a, px)}, {_value(b, py)}",
                    value=float(py),
                    x=float(px),
                    claim=PointClaim(x=a.name, y=b.name),
                )
                for px, py in sorted(points)
            ),
            note=f"A sample of {SCATTER_POINTS} rows." if len(x) > SCATTER_POINTS else None,
        )
    )


def _category_pair(ctx: _Ctx, a: Column, b: Column) -> None:
    cells, xi, yi = _crosstab(ctx.con, a, b)
    n = len(xi)
    if n < MIN_ROWS:
        raise _Skip(f"Too few rows have both {a.label} and {b.label}.")
    key = ctx.hypothesis("association", *sorted((a.name, b.name)))
    test = stats.association(key, (a.name, b.name), xi, yi, ctx.rng_for(key))
    if test is None:
        raise _Skip(f"{a.label} or {b.label} has a single value.")
    v = test.effect
    word, value = fmt.strength(v), fmt.coefficient(v)
    text, extra = _with_interval(
        f"{a.label} and {b.label} have a {word} link (Cramér's V {value}", test, fmt.coefficient
    )
    ctx.add(
        _Draft(
            kind="association",
            columns=(a.name, b.name),
            rows=n,
            method="Cramér's V over rows with both values (from 0, no link, to 1).",
            statement=f"{text}).",
            numbers=(value, *extra),
            values={"cramers_v": v, "strength": word},
            test=test,
            unreliable=(
                f"{a.label} and {b.label} show no reliable link in this data (Cramér's V {value}).",
                (value,),
            ),
        )
    )
    totals: dict[str, int] = {}
    for _, y, c in cells:
        totals[y] = totals.get(y, 0) + c
    common = max(sorted(totals), key=lambda k: totals[k])
    by_x: dict[str, list[int]] = {}
    for x, y, c in cells:
        pair = by_x.setdefault(x, [0, 0])
        pair[1] += c
        if y == common:
            pair[0] += c
    points = sorted(
        ((x, hit / size, size) for x, (hit, size) in by_x.items() if size >= MIN_ROWS),
        key=lambda t: (-t[1], t[0]),
    )[:TOP_GROUPS]
    if len(points) >= 2:
        claims = [
            ShareClaim(column=b.groups, key=common, given=a.groups, given_key=x, n="base")
            for x, _, _ in points
        ]
        ctx.chart(_bar(f"Share with {b.label} {common}, by {a.label}", a.label,
                       f"Share {common}", points, "percent", claims=claims,
                       keys=[x for x, _, _ in points], drill=a.drill))  # fmt: skip


# --------------------------------------------------------------------------- trends


def _trend(ctx: _Ctx, cols: list[Column]) -> None:
    when = next((c for c in cols if c.kind == "datetime"), None)
    num = next((c for c in cols if c.kind == "numeric"), None)
    if when is None or num is None:
        raise _Skip("A trend needs a date column and a measure.")
    _trend_of(ctx, when, num)


def _trend_of(ctx: _Ctx, when: Column, num: Column) -> None:
    w, v = _when(when), _num(num)
    first, last = ctx.con.execute(
        f"SELECT min(d), max(d) FROM (SELECT {w} AS d, {v} AS x FROM {ident(TABLE)}) "
        "WHERE d IS NOT NULL AND x IS NOT NULL"
    ).fetchone() or (None, None)
    if first is None or last is None:
        raise _Skip(f"No row has both {when.label} and {num.label}.")
    grain = _grain(first, last)
    series = _period_series(ctx.con, w, f"avg({v})", grain, where=f"isfinite({v})")
    if len(series) < MIN_PERIODS:
        raise _Skip(f"{when.label} covers too few {grain}s to show a trend.")
    rows = sum(n for _, _, n in series)
    (p0, m0, _), (p1, m1, _) = series[0], series[-1]
    top = max(series, key=lambda p: p[1])
    a, b, peak = _value(num, m0), _value(num, m1), _value(num, top[1])
    l0, l1, lp = _period_label(p0, grain), _period_label(p1, grain), _period_label(top[0], grain)
    numbers = [a, b, peak]
    change = ""
    if m0:
        rel = (m1 - m0) / abs(m0)
        pct = fmt.signed_share(rel)
        change = f" ({pct})"
        numbers.append(pct)
    base = (
        f"Average {num.label} went from {a} in {l0} to {b} in {l1}{change}; the highest "
        f"{grain} was {lp} at {peak}."
    )
    test = stats.kendall_trend(
        ctx.hypothesis("trend", when.name, num.name),
        (when.name, num.name),
        np.array([m for _, m, _ in series]),
    )
    statement, unreliable = base, None
    if test is not None:
        tau = fmt.coefficient(test.effect)
        periods = fmt.count(len(series))
        way = "upward" if test.effect > 0 else "downward"
        statement = (
            f"{base} Across the {periods} {grain}s the trend is {way} (Kendall's tau {tau})."
        )
        numbers += [periods, tau]
        unreliable = (
            f"{base} Across the {periods} {grain}s there is no reliable upward or downward "
            f"trend (Kendall's tau {tau}).",
            (*numbers,),
        )
    ctx.add(
        _Draft(
            kind="trend",
            columns=(when.name, num.name),
            rows=rows,
            method=f"Average {num.label} per {grain} of {when.label}.",
            statement=statement,
            numbers=tuple(numbers),
            labels=(l0, l1, lp),
            values={"first": m0, "last": m1, "peak": top[1], "periods": len(series)},
            test=test,
            unreliable=unreliable,
        )
    )
    value = Value(column=num.name)
    ctx.chart(_line(series, grain, f"Average {num.label} per {grain}", when.label,
                    f"Average {num.label}", _value_format(num), num.unit,
                    claim=lambda p: PeriodClaim(column=when.name, grain=grain, start=_iso(p),
                                                value=value)))  # fmt: skip


def _grain(first: Any, last: Any) -> str:
    span = (last - first).days if hasattr(last - first, "days") else 0
    if span <= 120:
        return "week"
    if span <= 1100:
        return "month"
    return "quarter"


def _period_series(
    con: duckdb.DuckDBPyConnection, when: str, aggregate: str, grain: str, *, where: str
) -> list[tuple[Any, float, int]]:
    rows = con.execute(
        f"SELECT date_trunc('{grain}', d) AS p, {aggregate}, count(*) "
        f"FROM (SELECT {when} AS d, * FROM {ident(TABLE)} WHERE {where}) "
        "WHERE d IS NOT NULL GROUP BY p ORDER BY p"
    ).fetchall()
    return [(p, float(m), int(n)) for p, m, n in rows if m is not None]


def _period_label(value: Any, grain: str) -> str:
    moment = value if isinstance(value, datetime | date) else datetime.fromisoformat(str(value))
    if grain == "quarter":
        return f"Q{(moment.month - 1) // 3 + 1} {moment.year}"
    if grain == "month":
        return moment.strftime("%b %Y")
    if grain == "week":
        return "week of " + moment.strftime("%-d %b %Y")
    return moment.strftime("%-d %b %Y")


def _line(  # noqa: PLR0913, PLR0917
    series: Sequence[tuple[Any, float, int]], grain: str, title: str, x_label: str,
    y_label: str, value_format: str, unit: str | None, *, claim: Callable[[Any], Claim],
) -> ChartData:  # fmt: skip
    return ChartData(
        kind="line",
        title=title,
        x_label=x_label,
        y_label=y_label,
        value_format=value_format,
        unit=unit,
        points=tuple(
            ChartPoint(label=_period_label(p, grain), value=m, n=n, claim=claim(p))
            for p, m, n in series
        ),
    )


def _iso(value: Any) -> str:
    """A period's start as ISO text (what the claim compares against)."""
    return value.isoformat() if isinstance(value, datetime | date) else str(value)


# --------------------------------------------------------------------------- groups


def _segment(ctx: _Ctx, cols: list[Column]) -> None:
    group, other = cols
    if group.kind != "categorical":
        group, other = other, group
    if group.kind != "categorical":
        raise _Skip("A comparison needs a column of groups.")
    if other.kind == "numeric":
        _segment_numeric(ctx, group, other)
    elif other.kind == "categorical":
        _segment_category(ctx, group, other)
    else:
        raise _Skip("These columns cannot be compared yet.")


def _difference_test(ctx: _Ctx, family: str, columns: tuple[str, str], g: _Groups) -> TestResult:
    key = ctx.hypothesis(family, *columns)
    test = stats.group_difference(key, columns, g.codes, g.values, ctx.rng_for(key))
    if test is None:
        raise _Skip("Every row has the same value.")
    return test


def _segment_numeric(ctx: _Ctx, group: Column, num: Column) -> None:
    g = _grouped(ctx.con, _cat(group), _num(num))
    if g is None or len(g.summary) < 2 or not g.real:
        raise _Skip(f"Fewer than two {group.label} groups have at least ten rows.")
    test = _difference_test(ctx, "groups", (group.name, num.name), g)
    real = [(_level(group, label), n, m) for label, n, m in g.compared]
    hi, lo = real[0], real[-1]
    a, b, overall = _value(num, hi[2]), _value(num, lo[2]), _value(num, g.grand)
    word, eta_text = fmt.strength(test.effect), fmt.coefficient(test.effect)
    text, extra = _with_interval(
        f"Average {num.label} is highest for {hi[0]} at {a} and lowest for {lo[0]} at {b}, "
        f"against {overall} overall; the difference between groups is {word} (eta {eta_text}",
        test,
        fmt.coefficient,
    )
    ctx.add(
        _Draft(
            kind="group_comparison",
            columns=(group.name, num.name),
            rows=g.rows,
            method=f"Average {num.label} per {group.label} group with at least ten rows; eta "
            "measures how much of the variation the groups account for (0 to 1).",
            statement=f"{text}).",
            numbers=(a, b, overall, eta_text, *extra),
            labels=(hi[0], lo[0]),
            values={"highest": hi[2], "lowest": lo[2], "overall": g.grand, "eta": test.effect,
                    "groups": len(g.summary)},
            test=test,
            unreliable=(
                f"Average {num.label} ranges from {b} for {lo[0]} to {a} for {hi[0]}, but the "
                f"{group.label} groups do not differ reliably (eta {eta_text}).",
                (b, a, eta_text),
            ),
        )
    )  # fmt: skip
    groups = g.shown()
    shown = [(_level(group, label), m, n) for label, n, m in groups]
    value = Value(column=num.name)
    ctx.chart(_bar(f"Average {num.label} by {group.label}", group.label, f"Average {num.label}",
                   shown, _value_format(num), num.unit,
                   claims=[_group_claim(group, value, label) for label, _, _ in groups],
                   keys=[_group_key(label) for label, _, _ in groups],
                   drill=group.drill))  # fmt: skip


def _segment_category(ctx: _Ctx, group: Column, cat: Column) -> None:
    levels = _binary_levels(ctx.con, cat)
    if levels is None:
        _category_pair(ctx, group, cat)
        return
    positive, _ = levels
    g = _grouped(ctx.con, _cat(group), _indicator(cat, positive))
    if g is None or len(g.summary) < 2 or not g.real:
        raise _Skip(f"Fewer than two {group.label} groups have at least ten rows.")
    test = _difference_test(ctx, "rates", (group.name, cat.name), g)
    real = [(_level(group, label), n, m) for label, n, m in g.compared]
    hi, lo = real[0], real[-1]
    a, b, overall = fmt.share(hi[2]), fmt.share(lo[2]), fmt.share(g.grand)
    word, eta_text = fmt.strength(test.effect), fmt.coefficient(test.effect)
    what = _level(cat, positive) if positive.casefold() in _YES else f"{cat.label} {positive}"
    text, extra = _with_interval(
        f"{what} is most common for {hi[0]} at {a} and least common for {lo[0]} at {b}, "
        f"against {overall} overall; the difference is {word} (eta {eta_text}",
        test,
        fmt.coefficient,
    )
    ctx.add(
        _Draft(
            kind="rate_comparison",
            columns=(group.name, cat.name),
            rows=g.rows,
            method=f"Share of rows with {what}, per {group.label} group with at least ten rows.",
            statement=f"{text}).",
            numbers=(a, b, overall, eta_text, *extra),
            labels=(hi[0], lo[0], positive),
            values={"highest": hi[2], "lowest": lo[2], "overall": g.grand, "eta": test.effect},
            test=test,
            unreliable=(
                f"{what} ranges from {b} for {lo[0]} to {a} for {hi[0]}, but the "
                f"{group.label} groups do not differ reliably (eta {eta_text}).",
                (b, a, eta_text),
            ),
        )
    )
    groups = g.shown()
    value = Value(column=cat.groups, positive=positive)
    ctx.chart(_bar(f"Share with {what}, by {group.label}", group.label, f"Share {what}",
                   [(_level(group, label), m, n) for label, n, m in groups], "percent",
                   claims=[_group_claim(group, value, label) for label, _, _ in groups],
                   keys=[_group_key(label) for label, _, _ in groups],
                   drill=group.drill))  # fmt: skip


def _benchmark(ctx: _Ctx, cols: list[Column]) -> None:
    group, num = cols
    if group.kind != "categorical":
        group, num = num, group
    if group.kind != "categorical" or num.kind != "numeric":
        raise _Skip("A benchmark needs a column of groups and a measure.")
    g = _grouped(ctx.con, _cat(group), _num(num))
    if g is None or len(g.real) < 2 or not g.grand:
        raise _Skip(f"Fewer than two {group.label} groups have at least ten rows.")
    test = _difference_test(ctx, "groups", (group.name, num.name), g)
    gaps = [(_level(group, label), (m - g.grand) / abs(g.grand), n) for label, n, m in g.real]
    raw = [label for label, _, _ in g.real]  # the group each gap is, as the data writes it
    above = max(gaps, key=lambda t: (t[1], t[0]))
    below = min(gaps, key=lambda t: (t[1], t[0]))
    overall, up, down = _value(num, g.grand), fmt.signed_share(above[1]), fmt.signed_share(below[1])
    eta_text = fmt.coefficient(test.effect)
    ctx.add(
        _Draft(
            kind="benchmark",
            columns=(group.name, num.name),
            rows=g.rows,
            method=f"Each {group.label} group's average {num.label} against the average of all "
            "rows, as a percentage difference.",
            statement=f"Against the overall average {num.label} of {overall}, {above[0]} is "
            f"furthest above ({up}) and {below[0]} furthest below ({down}).",
            numbers=(overall, up, down),
            labels=(above[0], below[0]),
            values={"overall": g.grand, "above": above[1], "below": below[1]},
            test=test,
            unreliable=(
                f"Against the overall average {num.label} of {overall}, groups range from "
                f"{down} ({below[0]}) to {up} ({above[0]}), but they do not differ reliably "
                f"(eta {eta_text}).",
                (overall, down, up, eta_text),
            ),
        )
    )
    order = sorted(range(len(gaps)), key=lambda i: (-gaps[i][1], gaps[i][0]))
    if len(order) > TOP_GROUPS:
        half = TOP_GROUPS // 2
        order = order[:half] + order[-half:]
    value = Value(column=num.name)
    ctx.chart(_bar(f"{num.label} against the overall average, by {group.label}", group.label,
                   "Difference from average", [gaps[i] for i in order], "signed_percent",
                   claims=[_group_claim(group, value, raw[i], "gap") for i in order],
                   keys=[raw[i] for i in order], drill=group.drill))  # fmt: skip


# --------------------------------------------------------------------------- unusual values


def _anomaly(ctx: _Ctx, cols: list[Column]) -> None:
    counted: list[tuple[str, float, int]] = []
    fenced: list[Claim] = []
    for col in cols:
        if col.kind != "numeric":
            continue
        v = _num(col)
        row = ctx.con.execute(
            f"SELECT quantile_cont(x, 0.25), quantile_cont(x, 0.75), count(x), min(x), max(x) "
            f"FROM (SELECT {v} AS x FROM {ident(TABLE)})"
        ).fetchone()
        assert row is not None  # noqa: S101
        q1, q3, n, smallest, largest = row
        if not n or n < MIN_ROWS or q1 is None or q3 is None:
            continue
        spread = q3 - q1
        lo, hi = q1 - 1.5 * spread, q3 + 1.5 * spread
        outside = int(
            ctx.con.execute(
                f"SELECT count(*) FROM (SELECT {v} AS x FROM {ident(TABLE)}) WHERE x < ? OR x > ?",
                [lo, hi],
            ).fetchone()[0]  # type: ignore[index]
        )
        c, part, a, b = fmt.count(outside), fmt.share(outside / n), _value(col, lo), _value(col, hi)
        # Name only the fences a value can actually cross.
        sides = [f"below {a}"] * (smallest < lo) + [f"above {b}"] * (largest > hi)
        shown = [a] * (smallest < lo) + [b] * (largest > hi)
        rows_word = "row has" if outside == 1 else "rows have"
        low, high = _value(col, smallest), _value(col, largest)
        ctx.add(
            _Draft(
                kind="outliers",
                columns=(col.name,),
                rows=int(n),
                method="Values more than one and a half interquartile ranges beyond the middle "
                "half (Tukey's fences). Flagged, never removed.",
                statement=f"{c} {rows_word} an unusual {col.label} ({part} of rows), "
                f"{' or '.join(sides)}."
                if outside
                else f"No {col.label} value is unusual: all lie between {low} and {high}.",
                numbers=(c, part, *shown) if outside else (low, high),
                values={"outside": outside, "lower_fence": lo, "upper_fence": hi},
            )
        )
        counted.append((col.label, float(outside), int(n)))
        fenced.append(OutlierClaim(column=col.name))
    if not counted:
        raise _Skip("None of these columns has enough numeric values.")
    if len(counted) == 1:
        ctx.chart(_histogram(ctx.con, next(c for c in cols if c.kind == "numeric"),
                             f"Distribution of {counted[0][0]}"))  # fmt: skip
    else:
        ctx.chart(_bar("Rows with unusual values", "Column", "Rows", counted, "count",
                       claims=fenced))  # fmt: skip


# --------------------------------------------------------------------------- drivers


def _driver(ctx: _Ctx, cols: list[Column]) -> None:
    goal = cols[0]
    levels = _binary_levels(ctx.con, goal) if goal.kind == "categorical" else None
    if goal.kind == "numeric":
        y = _num(goal)
    elif levels is not None:
        y = _indicator(goal, levels[0])
    else:
        raise _Skip(f"{goal.label} needs to be a measure or a yes/no column to be explained.")
    what = goal.label
    if levels:
        positive = levels[0]
        what = _level(goal, positive) if positive.casefold() in _YES else f"{what} {positive}"
    candidates = sorted(
        (
            c
            for c in ctx.every.values()
            if c.name != goal.name
            and c.kind in {"numeric", "categorical"}
            and c.meaning is not Meaning.IDENTIFIER
        ),
        key=lambda c: c.name,
    )[:MAX_DRIVER_CANDIDATES]
    scored: list[tuple[Column, TestResult]] = []
    leaks: list[tuple[Column, TestResult]] = []
    for cand in candidates:
        test = _candidate_test(ctx, cand, goal, binary=levels is not None)
        if test is None:
            continue
        (leaks if test.size >= LEAKAGE else scored).append((cand, test))
    if not scored and not leaks:
        raise _Skip(f"No column could be compared with {goal.label}.")
    for cand, test in leaks:
        value = fmt.coefficient(test.size)
        ctx.add(
            _Draft(
                kind="leakage",
                columns=(cand.name, goal.name),
                rows=test.n,
                method="A column whose association with the outcome is 0.95 or more (0 to 1) "
                "is set aside: it is probably recorded after the outcome or derived from it.",
                statement=f"{cand.label} is almost identical to {what} (strength {value}), so "
                "it may be recorded after it or derived from it; it is left out of the drivers.",
                numbers=(value,),
                values={"strength": test.size},
            )
        )
    skill = _model_skill(ctx, goal, y, [c for c, _ in scored], binary=levels is not None)
    if skill is not None:
        ctx.add(skill)
    if skill is None or not skill.values.get("skilful"):
        ctx.chart(None)
        return
    scored.sort(key=lambda t: (-t[1].size, t[0].name))
    for cand, test in scored:
        value, word = fmt.coefficient(test.size), fmt.strength(test.size)
        text, extra = _with_interval(
            f"{cand.label} has a {word} association with {what}, with a strength of {value}",
            test if test.effect_name == "eta" else _absolute(test),
            fmt.coefficient,
        )
        ctx.add(
            _Draft(
                kind="driver",
                columns=(cand.name, goal.name),
                rows=test.n,
                method="Strength of association with the outcome, from 0 to 1: rank correlation "
                "(size only) for measures, eta for groups.",
                statement=f"{text}.",
                numbers=(value, *extra),
                values={"strength": test.size, "word": word, "effect": test.effect},
                test=test,
                omit_if_unreliable=True,
            )
        )
    strongest = [(c.label, t.size, t.n) for c, t in scored[:10]]
    note = "Before the false-discovery check; the text names only the reliable ones."
    goal_positive = levels[0] if levels else None
    claims = [_candidate_claim(c, goal, goal_positive) for c, _ in scored[:10]]
    ctx.chart(_bar(f"How strongly each column is associated with {what}", "Column",
                   "Strength (0 to 1)", strongest, "strength", note=note,
                   claims=claims))  # fmt: skip


def _candidate_claim(cand: Column, goal: Column, positive: str | None) -> Claim:
    """What ``_candidate_test`` measured, as a claim on the size of the association."""
    if cand.kind == "numeric" and positive is None:
        return CorrelationClaim(x=cand.name, y=goal.name, absolute=True)
    if cand.kind == "numeric":
        return EtaClaim(group=goal.groups, value=Value(column=cand.name))
    if positive is not None:
        return EtaClaim(group=cand.groups, value=Value(column=goal.groups, positive=positive))
    return EtaClaim(group=cand.groups, value=Value(column=goal.name))


def _absolute(test: TestResult) -> TestResult:
    """A correlation's interval as a size: |rho| (an interval that spans zero starts at 0)."""
    low, high = test.ci_low, test.ci_high
    if low is None or high is None:
        return test
    if low <= 0 <= high:
        return replace(test, ci_low=0.0, ci_high=max(abs(low), abs(high)))
    a, b = sorted((abs(low), abs(high)))
    return replace(test, ci_low=a, ci_high=b)


def _candidate_test(ctx: _Ctx, cand: Column, goal: Column, *, binary: bool) -> TestResult | None:
    """The same test the matching planned question would run, under the same key, so a
    driver and a planned comparison of the same columns are one hypothesis."""
    if cand.kind == "numeric" and not binary:
        x, v = _pairs(ctx.con, _num(cand), _num(goal))
        if len(x) < MIN_ROWS:
            return None
        key = ctx.hypothesis("correlation", *sorted((cand.name, goal.name)))
        return stats.spearman(key, (cand.name, goal.name), x, v)
    if cand.kind == "numeric":  # a yes/no outcome: do its two groups differ in the measure?
        g = _grouped(ctx.con, _cat(goal), _num(cand))
        family, columns = "groups", (goal.name, cand.name)
    elif binary:
        positive = _binary_levels(ctx.con, goal)
        if positive is None:
            return None
        g = _grouped(ctx.con, _cat(cand), _indicator(goal, positive[0]))
        family, columns = "rates", (cand.name, goal.name)
    else:
        g = _grouped(ctx.con, _cat(cand), _num(goal))
        family, columns = "groups", (cand.name, goal.name)
    if g is None or len(g.summary) < 2:
        return None
    key = ctx.hypothesis(family, *columns)
    return stats.group_difference(key, columns, g.codes, g.values, ctx.rng_for(key))


def _model_skill(
    ctx: _Ctx, goal: Column, y: str, candidates: Sequence[Column], *, binary: bool
) -> _Draft | None:
    """Gradient boosting on the candidates, judged on a quarter of the rows it never saw: a
    driver is reported only when the model beats guessing there (R-squared of at least 0.02
    for a measure, area under the ROC curve of at least 0.55 for a yes/no outcome, and better
    than the baseline beyond chance)."""
    if not candidates:
        return None
    exprs = [_num(c) if c.kind == "numeric" else _cat(c) for c in candidates]
    select = ", ".join(f"{e} AS c{i}" for i, e in enumerate(exprs))
    names = ", ".join(f"c{i}" for i in range(len(exprs)))
    # A sample chosen by hashing the values: the same rows on every run of the same data.
    data = ctx.con.execute(
        f"SELECT y, {names} FROM (SELECT {y} AS y, {select} FROM {ident(TABLE)}) "
        f"WHERE y IS NOT NULL ORDER BY hash(y, {names}), y LIMIT {DRIVER_SAMPLE_ROWS}"
    ).fetchnumpy()
    target = _floats(data["y"])
    n = len(target)
    if n < DRIVER_MIN_ROWS or np.ptp(target) == 0:
        return _Draft(
            kind="driver_model",
            columns=(goal.name,),
            rows=n,
            method="A gradient-boosting model is trained on three quarters of the rows and "
            "checked on the rest before any driver is reported.",
            statement=f"There are too few rows with {goal.label} to check which columns go "
            "with it, so no driver is reported.",
            numbers=(),
            values={"skilful": False, "rows": n},
        )
    from sklearn.ensemble import (  # noqa: PLC0415  (heavy; only drivers need it)
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
    )

    matrix = np.column_stack([_encode(data[f"c{i}"], c.kind) for i, c in enumerate(candidates)])
    categorical = np.array([c.kind == "categorical" for c in candidates])
    rng = ctx.rng
    order = rng.permutation(n)
    cut = (3 * n) // 4
    train, held = order[:cut], order[cut:]
    seed = int(rng.integers(0, 2**31 - 1))
    common: dict[str, Any] = {
        "categorical_features": categorical,
        "max_iter": 100,
        "random_state": seed,
        "early_stopping": False,
    }
    if binary:
        if len(np.unique(target[train])) < 2 or len(np.unique(target[held])) < 2:
            return None
        clf = HistGradientBoostingClassifier(**common).fit(matrix[train], target[train])
        scores = clf.predict_proba(matrix[held])[:, 1]
        score, skilful = _auc_skill(target[held], scores)
        shown, measure = fmt.coefficient(score), "area under the ROC curve"
        scale = ", where 0.5 is guessing and 1 is perfect"
    else:
        reg = HistGradientBoostingRegressor(**common).fit(matrix[train], target[train])
        predicted = reg.predict(matrix[held])
        score, skilful = _r2_skill(target[held], predicted, float(target[train].mean()))
        shown, measure = fmt.share(max(score, 0.0)), "share of variation explained"
        scale = ""
    method = (
        "A gradient-boosting model is trained on three quarters of the rows (at most "
        f"{fmt.count(DRIVER_SAMPLE_ROWS)}) and checked on the rest, which it never saw."
    )
    if skilful:
        statement = (
            f"Together, the other columns predict {goal.label} on rows held out for checking "
            f"({measure} {shown}{scale})."
        )
    else:
        statement = (
            f"The other columns do not predict {goal.label} on rows held out for checking "
            f"({measure} {shown}{scale}), so no driver is reported."
        )
    return _Draft(
        kind="driver_model",
        columns=(goal.name, *(c.name for c in candidates)),
        rows=n,
        method=method,
        statement=statement,
        numbers=(shown,),
        values={"skilful": skilful, "score": score, "measure": measure, "rows": n,
                "held_out": len(held)},
    )  # fmt: skip


def _floats(values: Any) -> FloatArray:
    """A DuckDB column as floats, NULL as NaN (``fetchnumpy`` masks NULLs)."""
    if isinstance(values, np.ma.MaskedArray):
        return np.asarray(np.ma.filled(values.astype(float), np.nan), dtype=float)
    return np.asarray(values, dtype=float)


def _encode(values: Any, kind: str) -> FloatArray:
    """A model input: numbers as they are, categories as codes ordered by how common they are
    (HistGradientBoosting takes at most 255 categories, so the rarest share the last code)."""
    if kind == "numeric":
        return _floats(values)
    missing = np.ma.getmaskarray(values) | np.asarray(
        [v is None for v in np.asarray(np.ma.getdata(values), dtype=object)]
    )
    text = np.asarray(np.ma.getdata(values), dtype=object).astype(str)
    names, codes, counts = np.unique(text, return_inverse=True, return_counts=True)
    order = np.argsort(np.argsort(-counts, kind="stable"), kind="stable")
    out = np.minimum(order[codes], 254).astype(float)
    out[missing] = np.nan
    del names
    return out


def _r2_skill(actual: FloatArray, predicted: FloatArray, baseline: float) -> tuple[float, bool]:
    from scipy import stats as sps  # noqa: PLC0415

    model_err = (actual - predicted) ** 2
    base_err = (actual - baseline) ** 2
    total = float(np.sum(base_err))
    r2 = 1 - float(np.sum(model_err)) / total if total > 0 else 0.0
    gain = base_err - model_err
    better = (
        float(sps.ttest_1samp(gain, 0.0, alternative="greater").pvalue) < stats.ALPHA
        if np.ptp(gain) > 0
        else False
    )
    return r2, r2 >= DRIVER_MIN_R2 and better


def _auc_skill(actual: FloatArray, scores: FloatArray) -> tuple[float, bool]:
    from scipy import stats as sps  # noqa: PLC0415

    positives = actual == 1
    n1, n0 = int(positives.sum()), int((~positives).sum())
    ranks = sps.rankdata(scores)
    auc = (float(ranks[positives].sum()) - n1 * (n1 + 1) / 2) / (n1 * n0)
    # Hanley and McNeil's standard error: skill means the interval's lower end beats guessing.
    q1, q2 = auc / (2 - auc), 2 * auc * auc / (1 + auc)
    se = math.sqrt(
        (auc * (1 - auc) + (n1 - 1) * (q1 - auc * auc) + (n0 - 1) * (q2 - auc * auc)) / (n1 * n0)
    )
    return auc, auc >= DRIVER_MIN_AUC and auc - stats.Z95 * se > 0.5


# --------------------------------------------------------------------------- missing values


def _missing(ctx: _Ctx, cols: list[Column]) -> None:
    col = cols[0]
    present = f"{col.sql} IS NOT NULL" if col.kind != "categorical" else f"{_cat(col)} IS NOT NULL"
    total, missing = ctx.con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE NOT ({present})) FROM {ident(TABLE)}"
    ).fetchone() or (0, 0)
    if not total:
        raise _Skip("The table is empty.")
    part = fmt.share(missing / total)
    ctx.add(
        _Draft(
            kind="missing_share",
            columns=(col.name,),
            rows=int(total),
            method="Rows with no value, out of all rows.",
            statement=f"{part} of rows have no {col.label}.",
            numbers=(part,),
            values={"missing": missing, "rows": total},
        )
    )
    other = cols[1] if len(cols) > 1 else (ctx.every.get(ctx.target) if ctx.target else None)
    if other is None or other.name == col.name or missing < MIN_ROWS or total - missing < MIN_ROWS:
        return
    levels = _binary_levels(ctx.con, other) if other.kind == "categorical" else None
    show: Callable[[float], str]
    write: Callable[[float], str]
    if other.kind == "numeric":
        y, label = _num(other), f"average {other.label}"
        show = write = partial(_value, other)
        measured = Value(column=other.name)
    elif levels is not None:
        y, label = _indicator(other, levels[0]), f"share with {other.label} {levels[0]}"
        show, write = fmt.share, fmt.signed_share
        measured = Value(column=other.groups, positive=levels[0])
    else:
        return
    data = ctx.con.execute(
        f"SELECT has, v FROM (SELECT ({present}) AS has, {y} AS v FROM {ident(TABLE)}) "
        "WHERE v IS NOT NULL AND isfinite(v) ORDER BY has, v"
    ).fetchnumpy()
    has = np.asarray(data["has"], dtype=bool)
    values = np.asarray(data["v"], dtype=float)
    absent, kept = values[~has], values[has]
    if len(absent) < MIN_ROWS or len(kept) < MIN_ROWS:
        return
    hypothesis = ctx.hypothesis("missing", col.name, other.name)
    names = (col.name, other.name)
    if levels is not None:
        test = stats.proportion_difference(
            hypothesis,
            names,
            (int(absent.sum()), int(kept.sum())),
            (len(absent), len(kept)),
        )
    else:
        test = stats.mean_difference(hypothesis, names, absent, kept)
    without, with_v = show(float(absent.mean())), show(float(kept.mean()))
    head = (
        f"When {col.label} is missing, the {label} is {without}, against {with_v} when it is "
        "present"
    )
    if test is not None:
        diff = write(test.effect)
        text, extra = _with_interval(f"{head} (difference {diff}", test, write)
        statement, numbers = f"{text}).", (without, with_v, diff, *extra)
    else:
        statement, numbers = f"{head}.", (without, with_v)
    ctx.add(
        _Draft(
            kind="missing_effect",
            columns=names,
            rows=len(values),
            method=f"The {label}, for rows with and without {col.label}.",
            statement=statement,
            numbers=numbers,
            values={"missing": float(absent.mean()), "present": float(kept.mean())},
            test=test,
            unreliable=(f"{head}; the difference is not reliable.", (without, with_v)),
        )
    )
    grouped = col.kind == "categorical"
    where = col.groups if grouped else col.name
    ctx.chart(_bar(f"{label[:1].upper()}{label[1:]} with and without {col.label}", col.label,
                   label[:1].upper() + label[1:],
                   [(f"{col.label} missing", float(absent.mean()), len(absent)),
                    (f"{col.label} present", float(kept.mean()), len(kept))],
                   "percent" if levels else _value_format(other),
                   None if levels else other.unit,
                   claims=[MissingClaim(column=where, group=grouped, value=measured, missing=True),
                           MissingClaim(column=where, group=grouped, value=measured,
                                        missing=False)]))  # fmt: skip


# --------------------------------------------------------------------------- within groups


def _conditional(ctx: _Ctx, cols: list[Column]) -> None:
    group = next((c for c in reversed(cols) if c.kind == "categorical"), None)
    pair = [c for c in cols if c is not group and c.kind == "numeric"]
    if group is None or len(pair) != 2:
        raise _Skip("This question needs two measures and one column of groups.")
    a, b = pair
    x, y = _pairs(ctx.con, _num(a), _num(b))
    overall_test = stats.spearman("overall", (a.name, b.name), x, y) if len(x) >= MIN_ROWS else None
    if overall_test is None:
        raise _Skip(f"Too few rows have both {a.label} and {b.label}.")
    rho = overall_test.effect
    sizes = ctx.con.execute(
        f"SELECT g, count(*) FROM (SELECT {_cat(group)} AS g FROM {ident(TABLE)}) "
        f"WHERE g IS NOT NULL GROUP BY g HAVING count(*) >= {MIN_ROWS} "
        f"ORDER BY count(*) DESC, g LIMIT {TOP_GROUPS}"
    ).fetchall()
    within: list[tuple[str, float, int]] = []
    for g, _ in sizes:
        gx, gy = _pairs(ctx.con, _num(a), _num(b), where=f"{_cat(group)} = {literal(str(g))}")
        if len(gx) < MIN_ROWS:
            continue
        inner = stats.spearman("group", (a.name, b.name), gx, gy)
        if inner is not None:
            within.append((str(g), inner.effect, len(gx)))
    if len(within) < 2:
        raise _Skip(f"Fewer than two {group.label} groups have enough rows with both values.")
    test = stats.pooled_within_groups(
        ctx.hypothesis("within", *sorted((a.name, b.name)), group.name),
        (a.name, b.name, group.name),
        [(r, m) for _, r, m in within],
    )
    same = sum(1 for _, r, _ in within if (r > 0) == (rho > 0))
    overall, k, total = fmt.coefficient(rho), fmt.count(same), fmt.count(len(within))
    pooled = fmt.coefficient(test.effect) if test else overall
    text, extra = _with_interval(
        f"Overall, {a.label} and {b.label} have a rank correlation of {overall}; within "
        f"{group.label} groups it is {pooled} (",
        test,
        fmt.coefficient,
    )
    text = text.replace(" (; ", " (") + ")" if extra else text.removesuffix(" (")
    ctx.add(
        _Draft(
            kind="conditional",
            columns=(a.name, b.name, group.name),
            rows=len(x),
            method=f"Spearman rank correlation of {a.label} and {b.label}, overall and within "
            f"each {group.label} group with at least ten rows, pooled on Fisher's z scale.",
            statement=f"{text}, with the same direction in {k} of {total} groups.",
            numbers=(overall, pooled, *extra, k, total),
            values={"overall": rho, "within": test.effect if test else None,
                    "same_direction": same, "groups": len(within)},
            test=test,
            unreliable=(
                f"Overall, {a.label} and {b.label} have a rank correlation of {overall}; within "
                f"{group.label} groups there is no reliable link ({pooled}).",
                (overall, pooled),
            ),
        )
    )  # fmt: skip
    ordered = sorted(within, key=lambda t: (-t[1], t[0]))
    ctx.chart(_bar(f"Link between {a.label} and {b.label}, within each {group.label}",
                   group.label, "Rank correlation", ordered, "strength",
                   claims=[CorrelationClaim(x=a.name, y=b.name, group=group.groups, key=g)
                           for g, _, _ in ordered],
                   keys=[g for g, _, _ in ordered], drill=group.drill))  # fmt: skip


_Miner = Callable[[_Ctx, list[Column]], None]
_MINERS: Final[dict[MinerKind, _Miner]] = {
    MinerKind.DISTRIBUTION: _distribution,
    MinerKind.RELATIONSHIP: _relationship,
    MinerKind.TREND: _trend,
    MinerKind.SEGMENT: _segment,
    MinerKind.BENCHMARK: _benchmark,
    MinerKind.ANOMALY: _anomaly,
    MinerKind.DRIVER: _driver,
    MinerKind.MISSING_AS_SIGNAL: _missing,
    MinerKind.CONDITIONAL: _conditional,
}
