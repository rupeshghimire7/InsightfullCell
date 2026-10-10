"""Per-column statistics, missing-value patterns, duplicates and outliers (stage 2, ``profile``).

Everything is computed in DuckDB over every row of the typed Parquet. Text columns that hold
numbers, percentages or dates are profiled *as* those (the way cleaning will read them), so the
profile already shows what parsing would lose.

Columns flagged as personal data get counts only: no values, ranges, shapes or categories are
kept for them, here or anywhere downstream.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

import duckdb
from pydantic import BaseModel, ConfigDict

from insightfulcell.engine import expressions as ex
from insightfulcell.engine.semantics import ColumnSemantics, ParseKind
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import Meaning, is_numeric_type, is_temporal_type, is_text_type

TOP_CATEGORIES: Final = 12
TOP_SHAPES: Final = 5
HISTOGRAM_BINS: Final = 16
MISSING_PATTERN_COLUMNS: Final = 30
MISSING_PATTERNS: Final = 6
SENTINELS: Final = (-99999.0, -9999.0, -999.0, -99.0, 999.0, 9999.0, 99999.0, 999999.0)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class NumericProfile(_Model):
    count: int
    mean: float | None
    std: float | None
    min: float | None
    max: float | None
    p01: float | None
    p05: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p95: float | None
    p99: float | None
    skewness: float | None
    kurtosis: float | None
    zero_share: float
    negative_share: float
    integer_share: float
    round_share: float  # of non-zero integers, the share that are multiples of 10 (heaping)
    outliers: int  # beyond 1.5 IQR from the quartiles
    extreme_outliers: int  # beyond 3 IQR
    sentinels: tuple[float, ...] = ()  # codes such as -999 that sit far outside the data
    sentinel_rows: int = 0
    histogram: tuple[int, ...] = ()  # equal-width bins between p01 and p99
    histogram_range: tuple[float, float] | None = None


class CategoryCount(_Model):
    value: str
    count: int


class ShapeCount(_Model):
    shape: str
    count: int


class CategoricalProfile(_Model):
    top: tuple[CategoryCount, ...] = ()
    other_count: int = 0
    singletons: int = 0
    case_variant_values: int = 0  # distinct spellings that collapse under case and spacing
    whitespace_rows: int = 0
    non_nfc_rows: int = 0
    length_min: int | None = None
    length_median: float | None = None
    length_max: int | None = None
    shapes: tuple[ShapeCount, ...] = ()


class DatetimeProfile(_Model):
    count: int
    min: str | None
    max: str | None
    p05: str | None
    p95: str | None
    span_days: float | None
    distinct_days: int
    granularity: str  # year, month, day, hour, minute, second
    weekday_counts: tuple[int, ...] = ()  # Monday first
    month_counts: tuple[int, ...] = ()
    future_count: int = 0


class TextProfile(_Model):
    length_p50: float | None
    length_p95: float | None
    words_p50: float | None
    multiline_share: float


class ColumnProfile(_Model):
    name: str
    position: int
    physical_type: str
    meaning: Meaning
    personal_data: str | None = None
    rows: int
    nulls: int
    missing_tokens: int
    distinct: int
    unparsed: int = 0  # non-missing values a parse would turn into empty values
    numeric: NumericProfile | None = None
    categorical: CategoricalProfile | None = None
    datetime: DatetimeProfile | None = None
    text: TextProfile | None = None
    flags: tuple[str, ...] = ()

    @property
    def missing(self) -> int:
        return self.nulls + self.missing_tokens

    @property
    def missing_share(self) -> float:
        return self.missing / self.rows if self.rows else 0.0

    @property
    def present(self) -> int:
        return self.rows - self.missing


class MissingPattern(_Model):
    columns: tuple[str, ...]
    rows: int


class TableProfile(_Model):
    rows: int
    columns: int
    duplicate_rows: int
    complete_rows: int
    missing_cells: int
    constant_columns: tuple[str, ...] = ()
    candidate_keys: tuple[str, ...] = ()
    missing_patterns: tuple[MissingPattern, ...] = ()


class Profile(_Model):
    table: TableProfile
    columns: tuple[ColumnProfile, ...]

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


# --------------------------------------------------------------------------- expressions


def numeric_expression(sem: ColumnSemantics) -> str | None:
    """The column as DOUBLE the way cleaning will read it, or ``None`` if it is not numeric."""
    c = ident(sem.name)
    if is_numeric_type(sem.physical_type):
        return f"CAST({c} AS DOUBLE)"
    if sem.physical_type.upper() == "TIME":
        return f"(extract('epoch' FROM {c}) / 60.0)"  # minutes after midnight
    if sem.parse is not None and sem.parse.kind is ParseKind.NUMBERS:
        return ex.number_from_text(c)
    if sem.parse is not None and sem.parse.kind is ParseKind.PERCENTAGES:
        return ex.percentage_from_text(c)
    return None


def datetime_expression(sem: ColumnSemantics) -> str | None:
    c = ident(sem.name)
    physical = sem.physical_type.upper()
    if is_temporal_type(physical) and physical != "TIME":
        return f"CAST({c} AS TIMESTAMP)"
    if sem.parse is not None and sem.parse.kind is ParseKind.DATES:
        return ex.datetime_from_text(c, ex.date_formats(sem.parse.day_first is not False))
    return None


def missing_condition(sem: ColumnSemantics) -> str:
    c = ident(sem.name)
    if is_text_type(sem.physical_type):
        return f"({c} IS NULL OR {ex.is_missing_token(c)})"
    return f"({c} IS NULL)"


# --------------------------------------------------------------------------- profiling


def profile_table(
    con: duckdb.DuckDBPyConnection, semantics: Sequence[ColumnSemantics], *, today: str
) -> Profile:
    """Profile table ``t``. ``today`` (ISO date) decides which dates lie in the future, so a
    re-run on another day reproduces the same profile from the same inputs."""
    columns = tuple(_profile_column(con, sem, today) for sem in semantics)
    return Profile(table=_table(con, semantics, columns), columns=columns)


def _profile_column(
    con: duckdb.DuckDBPyConnection, sem: ColumnSemantics, today: str
) -> ColumnProfile:
    c = ident(sem.name)
    missing = missing_condition(sem)
    token_count = (
        f"count(*) FILTER (WHERE {c} IS NOT NULL AND {ex.is_missing_token(c)})"
        if is_text_type(sem.physical_type)
        else "0"
    )
    row = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE {c} IS NULL), {token_count}, "
        f"count(DISTINCT {c}) FILTER (WHERE NOT {missing}) FROM t"
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    rows, nulls, tokens, distinct = (int(v or 0) for v in row)
    base = {
        "name": sem.name,
        "position": sem.position,
        "physical_type": sem.physical_type,
        "meaning": sem.meaning,
        "rows": rows,
        "nulls": nulls,
        "missing_tokens": tokens,
        "distinct": distinct,
    }
    if sem.personal_data is not None:
        # Masked from here on: counts only.
        masked = _flags_basic(rows, nulls + tokens, distinct, tokens)
        return ColumnProfile(**base, personal_data=sem.personal_data.value, flags=masked)

    present = rows - nulls - tokens
    numeric = datetime = categorical = text = None
    unparsed = 0
    num_expr, dt_expr = numeric_expression(sem), datetime_expression(sem)
    if sem.meaning is Meaning.TEXT:
        text = _text(con, c, missing)
    elif num_expr is not None and sem.meaning is not Meaning.IDENTIFIER:
        numeric = _numeric(con, num_expr, missing)
        unparsed = present - numeric.count if sem.parse is not None else 0
    elif dt_expr is not None:
        datetime = _datetime(con, dt_expr, missing, today)
        unparsed = present - datetime.count if sem.parse is not None else 0
    if is_text_type(sem.physical_type) and sem.meaning is not Meaning.TEXT:
        categorical = _categorical(
            con, c, missing, with_values=sem.meaning is not Meaning.IDENTIFIER
        )
    elif sem.meaning in {Meaning.NOMINAL, Meaning.GEOGRAPHY} and numeric is None:
        categorical = _categorical(con, f"CAST({c} AS VARCHAR)", missing, with_values=True)

    flags = [*_flags_basic(rows, nulls + tokens, distinct, tokens)]
    flags += _flags_detail(sem, numeric, categorical, datetime, unparsed, present, distinct)
    return ColumnProfile(
        **base,
        unparsed=max(0, unparsed),
        numeric=numeric,
        categorical=categorical,
        datetime=datetime,
        text=text,
        flags=tuple(flags),
    )


def _numeric(con: duckdb.DuckDBPyConnection, expr: str, missing: str) -> NumericProfile:
    x = f"CASE WHEN NOT {missing} THEN {expr} END"
    row = con.execute(
        f"""
        WITH v AS (SELECT {x} AS x FROM t), s AS (SELECT x FROM v WHERE x IS NOT NULL
                                                  AND isfinite(x))
        SELECT count(x), avg(x), stddev_samp(x), min(x), max(x),
               quantile_cont(x, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]),
               skewness(x), kurtosis(x),
               count(*) FILTER (WHERE x = 0), count(*) FILTER (WHERE x < 0),
               count(*) FILTER (WHERE x = floor(x)),
               count(*) FILTER (WHERE x = floor(x) AND x <> 0 AND x % 10 = 0),
               count(*) FILTER (WHERE x = floor(x) AND x <> 0)
        FROM s
        """
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    count = int(row[0] or 0)
    if count == 0:
        return NumericProfile(
            count=0, mean=None, std=None, min=None, max=None, p01=None, p05=None, p25=None,
            p50=None, p75=None, p95=None, p99=None, skewness=None, kurtosis=None,
            zero_share=0, negative_share=0, integer_share=0, round_share=0, outliers=0,
            extreme_outliers=0,
        )  # fmt: skip
    q = [_f(v) for v in (row[5] or [None] * 7)]
    p01, p05, p25, p50, p75, p95, p99 = q
    iqr = (p75 - p25) if p75 is not None and p25 is not None else 0.0
    out = (
        con.execute(
            f"""
        WITH s AS (SELECT {x} AS x FROM t)
        SELECT count(*) FILTER (WHERE x < ? OR x > ?), count(*) FILTER (WHERE x < ? OR x > ?)
        FROM s WHERE x IS NOT NULL
        """,
            [p25 - 1.5 * iqr, p75 + 1.5 * iqr, p25 - 3 * iqr, p75 + 3 * iqr],
        ).fetchone()
        if p25 is not None and p75 is not None
        else (0, 0)
    )
    assert out is not None  # noqa: S101  (an aggregate query always returns a row)
    sentinels, sentinel_rows = _sentinels(con, x, p25, p75, iqr)
    histogram, hist_range = _histogram(con, x, p01, p99)
    nonzero_int = int(row[12] or 0)
    return NumericProfile(
        count=count,
        mean=_f(row[1]),
        std=_f(row[2]),
        min=_f(row[3]),
        max=_f(row[4]),
        p01=p01,
        p05=p05,
        p25=p25,
        p50=p50,
        p75=p75,
        p95=p95,
        p99=p99,
        skewness=_f(row[6]),
        kurtosis=_f(row[7]),
        zero_share=int(row[8] or 0) / count,
        negative_share=int(row[9] or 0) / count,
        integer_share=int(row[10] or 0) / count,
        round_share=(int(row[11] or 0) / nonzero_int) if nonzero_int else 0.0,
        outliers=int(out[0] or 0),
        extreme_outliers=int(out[1] or 0),
        sentinels=sentinels,
        sentinel_rows=sentinel_rows,
        histogram=histogram,
        histogram_range=hist_range,
    )


def _sentinels(
    con: duckdb.DuckDBPyConnection,
    x: str,
    p25: float | None,
    p75: float | None,
    iqr: float,
) -> tuple[tuple[float, ...], int]:
    """Placeholder codes (-999, 9999, ...) far outside the middle half of the data. Measured
    from the quartiles: a placeholder in 8% of rows would already be the 5th percentile."""
    if p25 is None or p75 is None:
        return (), 0
    spread = max(iqr, 1.0)
    candidates = [s for s in SENTINELS if s < p25 - 5 * spread or s > p75 + 5 * spread]
    if not candidates:
        return (), 0
    rows = con.execute(
        f"SELECT x, count(*) FROM (SELECT {x} AS x FROM t) "
        f"WHERE x IN ({', '.join(repr(s) for s in candidates)}) GROUP BY x ORDER BY x"
    ).fetchall()
    return tuple(float(v) for v, _ in rows), sum(int(n) for _, n in rows)


def _histogram(
    con: duckdb.DuckDBPyConnection, x: str, low: float | None, high: float | None
) -> tuple[tuple[int, ...], tuple[float, float] | None]:
    if low is None or high is None or not high > low:
        return (), None
    width = (high - low) / HISTOGRAM_BINS
    rows = con.execute(
        f"""
        SELECT least({HISTOGRAM_BINS - 1}, CAST(floor((x - ?) / ?) AS INTEGER)) AS b, count(*)
        FROM (SELECT {x} AS x FROM t) WHERE x >= ? AND x <= ? GROUP BY b
        """,
        [low, width, low, high],
    ).fetchall()
    bins = [0] * HISTOGRAM_BINS
    for b, n in rows:
        bins[int(b)] = int(n)
    return tuple(bins), (low, high)


def _datetime(
    con: duckdb.DuckDBPyConnection, expr: str, missing: str, today: str
) -> DatetimeProfile:
    x = f"CASE WHEN NOT {missing} THEN {expr} END"
    row = con.execute(
        f"""
        WITH s AS (SELECT {x} AS x FROM t), v AS (SELECT x FROM s WHERE x IS NOT NULL)
        SELECT count(x), min(x), max(x),
               quantile_disc(x, 0.05), quantile_disc(x, 0.95),
               count(DISTINCT CAST(x AS DATE)),
               count(*) FILTER (WHERE second(x) <> 0),
               count(*) FILTER (WHERE minute(x) <> 0),
               count(*) FILTER (WHERE hour(x) <> 0),
               count(*) FILTER (WHERE day(x) <> 1),
               count(*) FILTER (WHERE month(x) <> 1),
               count(*) FILTER (WHERE CAST(x AS DATE) > CAST(? AS DATE))
        FROM v
        """,
        [today],
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    count = int(row[0] or 0)
    if count == 0:
        return DatetimeProfile(
            count=0, min=None, max=None, p05=None, p95=None, span_days=None, distinct_days=0,
            granularity="day",
        )  # fmt: skip
    if row[6]:
        granularity = "second"
    elif row[7]:
        granularity = "minute"
    elif row[8]:
        granularity = "hour"
    elif row[9]:
        granularity = "day"
    elif row[10]:
        granularity = "month"
    else:
        granularity = "year"
    weekdays = dict(
        con.execute(
            f"SELECT isodow(x), count(*) FROM (SELECT {x} AS x FROM t) WHERE x IS NOT NULL "
            "GROUP BY 1"
        ).fetchall()
    )
    months = dict(
        con.execute(
            f"SELECT month(x), count(*) FROM (SELECT {x} AS x FROM t) WHERE x IS NOT NULL "
            "GROUP BY 1"
        ).fetchall()
    )
    span = (row[2] - row[1]).total_seconds() / 86_400 if row[1] and row[2] else None
    return DatetimeProfile(
        count=count,
        min=row[1].isoformat() if row[1] else None,
        max=row[2].isoformat() if row[2] else None,
        p05=row[3].isoformat() if row[3] else None,
        p95=row[4].isoformat() if row[4] else None,
        span_days=round(span, 3) if span is not None else None,
        distinct_days=int(row[5] or 0),
        granularity=granularity,
        weekday_counts=tuple(int(weekdays.get(d, 0)) for d in range(1, 8)),
        month_counts=tuple(int(months.get(m, 0)) for m in range(1, 13)),
        future_count=int(row[11] or 0),
    )


def _categorical(
    con: duckdb.DuckDBPyConnection, c: str, missing: str, *, with_values: bool
) -> CategoricalProfile:
    s = ex.clean_text(c)
    row = con.execute(
        f"""
        SELECT count(DISTINCT {c}) - count(DISTINCT lower({s})),
               count(*) FILTER (WHERE {c} <> trim({c})),
               count(*) FILTER (WHERE {c} <> nfc_normalize({c})),
               min(length({c})), median(length({c})), max(length({c}))
        FROM t WHERE NOT {missing}
        """
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    top: tuple[CategoryCount, ...] = ()
    other = singletons = 0
    if with_values:
        counts = con.execute(
            f"SELECT {c} AS v, count(*) AS n FROM t WHERE NOT {missing} "
            "GROUP BY v ORDER BY n DESC, v LIMIT ?",
            [TOP_CATEGORIES],
        ).fetchall()
        top = tuple(CategoryCount(value=str(v), count=int(n)) for v, n in counts)
        totals = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE n = 1) FROM "
            f"(SELECT {c} AS v, count(*) AS n FROM t WHERE NOT {missing} GROUP BY v)"
        ).fetchone()
        assert totals is not None  # noqa: S101  (an aggregate query always returns a row)
        present = con.execute(f"SELECT count(*) FROM t WHERE NOT {missing}").fetchone()
        assert present is not None  # noqa: S101  (an aggregate query always returns a row)
        other = int(present[0]) - sum(item.count for item in top)
        singletons = int(totals[1] or 0)
    shapes = con.execute(
        f"SELECT {ex.value_shape(c)} AS s, count(*) AS n FROM t WHERE NOT {missing} "
        "GROUP BY s ORDER BY n DESC, s LIMIT ?",
        [TOP_SHAPES],
    ).fetchall()
    return CategoricalProfile(
        top=top,
        other_count=max(0, other),
        singletons=singletons,
        case_variant_values=int(row[0] or 0),
        whitespace_rows=int(row[1] or 0),
        non_nfc_rows=int(row[2] or 0),
        length_min=_i(row[3]),
        length_median=_f(row[4]),
        length_max=_i(row[5]),
        shapes=tuple(ShapeCount(shape=str(s), count=int(n)) for s, n in shapes),
    )


def _text(con: duckdb.DuckDBPyConnection, c: str, missing: str) -> TextProfile:
    row = con.execute(
        f"""
        SELECT quantile_cont(length({c}), 0.5), quantile_cont(length({c}), 0.95),
               quantile_cont(len(string_split(trim({c}), ' ')), 0.5),
               avg(CASE WHEN contains({c}, chr(10)) THEN 1.0 ELSE 0.0 END)
        FROM t WHERE NOT {missing}
        """
    ).fetchone()
    assert row is not None  # noqa: S101  (an aggregate query always returns a row)
    return TextProfile(
        length_p50=_f(row[0]),
        length_p95=_f(row[1]),
        words_p50=_f(row[2]),
        multiline_share=float(row[3] or 0),
    )


# --------------------------------------------------------------------------- flags


def _flags_basic(rows: int, missing: int, distinct: int, tokens: int) -> tuple[str, ...]:
    flags = []
    if tokens:
        flags.append("missing_tokens")
    if rows and missing / rows > 0.3:
        flags.append("high_missing")
    if distinct <= 1 and rows - missing > 0:
        flags.append("constant")
    return tuple(flags)


def _flags_detail(  # noqa: PLR0912, PLR0913, PLR0917  (one check per flag)
    sem: ColumnSemantics,
    numeric: NumericProfile | None,
    categorical: CategoricalProfile | None,
    datetime: DatetimeProfile | None,
    unparsed: int,
    present: int,
    distinct: int,
) -> list[str]:
    flags: list[str] = []
    if unparsed > 0:
        flags.append("unparsed_values")
    if sem.parse is not None:
        if sem.parse.ambiguous_day_month:
            flags.append("ambiguous_day_month")
        if sem.parse.mixed_day_month:
            flags.append("mixed_day_month")
        if sem.parse.mixed_representation:
            flags.append("mixed_representation")
    if numeric is not None and numeric.count:
        if numeric.outliers / numeric.count > 0.01:
            flags.append("outliers")
        if numeric.sentinels:
            flags.append("sentinel_values")
        if 0 < numeric.negative_share < 0.1:
            flags.append("rare_negatives")
        if numeric.skewness is not None and abs(numeric.skewness) > 2:
            flags.append("skewed")
        if numeric.integer_share == 1 and numeric.round_share > 0.6 and numeric.count >= 20:
            flags.append("heaping")
    if categorical is not None:
        if categorical.case_variant_values:
            flags.append("case_variants")
        if categorical.whitespace_rows:
            flags.append("whitespace")
        if categorical.non_nfc_rows:
            flags.append("non_nfc_unicode")
        if sem.meaning in {Meaning.NOMINAL, Meaning.GEOGRAPHY} and distinct > 50:
            flags.append("high_cardinality")
        if sem.meaning is Meaning.IDENTIFIER and distinct < present:
            flags.append("repeated_identifiers")
    if datetime is not None and datetime.future_count:
        flags.append("future_dates")
    return flags


# --------------------------------------------------------------------------- table


def _table(
    con: duckdb.DuckDBPyConnection,
    semantics: Sequence[ColumnSemantics],
    columns: Sequence[ColumnProfile],
) -> TableProfile:
    rows = columns[0].rows if columns else 0
    dup = con.execute("SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM t)) FROM t")
    duplicate_rows = int((dup.fetchone() or (0,))[0] or 0)
    complete_cond = " AND ".join(f"NOT {missing_condition(s)}" for s in semantics) or "true"
    complete = con.execute(f"SELECT count(*) FROM t WHERE {complete_cond}").fetchone()
    assert complete is not None  # noqa: S101  (an aggregate query always returns a row)
    with_missing = sorted(
        (c for c in columns if c.missing), key=lambda c: (-c.missing, c.position)
    )[:MISSING_PATTERN_COLUMNS]
    patterns: list[MissingPattern] = []
    if with_missing:
        by_name = {s.name: s for s in semantics}
        parts = ", ".join(
            f"CASE WHEN {missing_condition(by_name[c.name])} THEN {i} END"
            for i, c in enumerate(with_missing)
        )
        found = con.execute(
            f"SELECT list_filter([{parts}], x -> x IS NOT NULL) AS p, count(*) AS n FROM t "
            "GROUP BY p HAVING len(p) > 0 ORDER BY n DESC LIMIT ?",
            [MISSING_PATTERNS],
        ).fetchall()
        patterns = [
            MissingPattern(columns=tuple(with_missing[i].name for i in p), rows=int(n))
            for p, n in found
        ]
    keys = tuple(
        c.name
        for c in columns
        if rows
        and c.distinct == rows
        and c.missing == 0
        and c.personal_data is None
        and not (c.numeric and c.numeric.integer_share < 1)
    )
    return TableProfile(
        rows=rows,
        columns=len(columns),
        duplicate_rows=duplicate_rows,
        complete_rows=int(complete[0] or 0),
        missing_cells=sum(c.missing for c in columns),
        constant_columns=tuple(c.name for c in columns if "constant" in c.flags),
        candidate_keys=keys,
        missing_patterns=tuple(patterns),
    )


def _f(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


def _i(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]
