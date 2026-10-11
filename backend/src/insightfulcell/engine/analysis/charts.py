"""Checking charts against the data: every claimed value recomputed, a mismatch rejects the chart.

The miners draw a chart with one grouped query; this module recomputes each point on its own,
from its claim (``engine.analysis.claims``): a filtered count, share, average or correlation over
the analysis table. A chart is verified only when every point has a claim and every claim holds
(counts exactly, other values within a relative tolerance far below what the page shows). A
chart that fails is not shown: the answer keeps its words and facts, and the report says a chart
was withheld. Failures are logged by claim type only, never with values.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import duckdb
import numpy as np
from scipy.stats import rankdata

from insightfulcell.engine.analysis.claims import (
    POOL_BELOW,
    BinClaim,
    Claim,
    CorrelationClaim,
    EtaClaim,
    GroupClaim,
    MissingClaim,
    OutlierClaim,
    PeriodClaim,
    PointClaim,
    RowsClaim,
    ShareClaim,
    Value,
)
from insightfulcell.engine.analysis.facts import ChartCheck, ChartData, ChartPoint, MapData
from insightfulcell.engine.sql import ident, literal

logger = logging.getLogger(__name__)

#: Far below the three significant figures a page shows, far above floating-point noise from
#: summing in a different order.
RELATIVE_TOLERANCE: Final = 1e-7
ABSOLUTE_TOLERANCE: Final = 1e-9


@dataclass(frozen=True, slots=True)
class Measured:
    value: float | None
    n: int | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    claims: int
    failed: int
    #: The claim type that failed first (never a value).
    first_failure: str | None = None

    @property
    def verified(self) -> bool:
        return self.claims > 0 and self.failed == 0


# --------------------------------------------------------------------------- public


def check_chart(con: duckdb.DuckDBPyConnection, chart: ChartData, table: str = "a") -> Verdict:
    """Recompute every point of ``chart`` from ``table``."""
    points = list(chart.points)
    if not points or any(p.claim is None for p in points):
        return Verdict(len(points), len(points) or 1, "missing")
    failed, first = 0, None
    scatter = [p for p in points if isinstance(p.claim, PointClaim)]
    if scatter:
        missing = _missing_points(con, table, scatter)
        if missing:
            failed, first = missing, "point"
    for point in points:
        claim = point.claim
        if claim is None or isinstance(claim, PointClaim):
            continue
        if not _holds(con, table, claim, point.value, point.n):
            failed += 1
            first = first or claim.type
    return Verdict(len(points), failed, first)


def check_map(con: duckdb.DuckDBPyConnection, data: MapData, table: str = "a") -> Verdict:
    """Recompute every coloured or hatched region of ``data`` from ``table``."""
    regions = [r for r in data.regions if r.status != "no_rows"]
    if not regions:
        return Verdict(0, 1, "missing")
    failed, first = 0, None
    for region in regions:
        claim = region.claim
        if claim is None:
            failed += 1
            first = first or "missing"
            continue
        if region.status == "few_rows":
            ok = isinstance(claim, RowsClaim) and _holds(con, table, claim, None, None)
        else:
            ok = region.value is not None and _holds(con, table, claim, region.value, region.n)
        if not ok:
            failed += 1
            first = first or claim.type
    return Verdict(len(regions), failed, first)


def with_check(chart: ChartData, verdict: Verdict) -> ChartData:
    return chart.model_copy(
        update={"check": ChartCheck(claims=verdict.claims, verified=verdict.verified)}
    )


def map_with_check(data: MapData, verdict: Verdict) -> MapData:
    return data.model_copy(
        update={"check": ChartCheck(claims=verdict.claims, verified=verdict.verified)}
    )


def measure(con: duckdb.DuckDBPyConnection, table: str, claim: Claim) -> Measured:
    """What the data says for one claim (also used to build maps, so a map's values and their
    check come from the same definitions). Scatter points are checked in a batch instead."""
    compute = _MEASURES.get(type(claim))
    if compute is None:
        raise TypeError(f"no measure for {claim.type}")
    return compute(con, ident(table), claim)


# --------------------------------------------------------------------------- comparison


def _holds(
    con: duckdb.DuckDBPyConnection,
    table: str,
    claim: Claim,
    value: float | None,
    n: int | None,
) -> bool:
    try:
        measured = measure(con, table, claim)
    except (duckdb.Error, ValueError, ZeroDivisionError, FloatingPointError):
        logger.warning("chart claim could not be computed", extra={"claim": claim.type})
        return False
    if isinstance(claim, RowsClaim):
        return measured.value == 1.0
    if value is None or measured.value is None or not _close(value, measured.value):
        return False
    return n is None or measured.n == n


def _close(expected: float, actual: float) -> bool:
    if not (math.isfinite(expected) and math.isfinite(actual)):
        return False
    return math.isclose(expected, actual, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


# --------------------------------------------------------------------------- expressions


def _number(column: str) -> str:
    return f"TRY_CAST({ident(column)} AS DOUBLE)"


def _text(column: str) -> str:
    return f"NULLIF(trim(CAST({ident(column)} AS VARCHAR)), '')"


def _value(value: Value) -> str:
    if value.positive is None:
        return _number(value.column)
    g, positive = _text(value.column), literal(value.positive)
    return f"CASE WHEN {g} IS NULL THEN NULL WHEN {g} = {positive} THEN 1.0 ELSE 0.0 END"


def _first(
    con: duckdb.DuckDBPyConnection, query: str, params: Sequence[Any] = ()
) -> tuple[Any, ...]:
    row = con.execute(query, list(params)).fetchone()
    if row is None:
        raise ValueError("no result")
    return tuple(row)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


# --------------------------------------------------------------------------- groups


def _buckets(t: str, group: str, value: Value) -> str:
    """Rows with a group and a finite value, each in its bucket: its own group when that has
    ``POOL_BELOW`` rows, otherwise the pool of small groups when the pool has that many, else
    none (left out). The same rule the miners group by."""
    k = POOL_BELOW
    return (
        f"WITH d AS (SELECT g, v FROM (SELECT {_text(group)} AS g, {_value(value)} AS v FROM {t}) "
        "WHERE g IS NOT NULL AND v IS NOT NULL AND isfinite(v)), "
        "s AS (SELECT g, count(*) AS n FROM d GROUP BY g), "
        f"p AS (SELECT coalesce(sum(n) FILTER (WHERE n < {k}), 0) AS pooled FROM s), "
        f"b AS (SELECT CASE WHEN s.n >= {k} THEN 'g:' || d.g WHEN p.pooled >= {k} THEN 'pool' "
        "END AS bucket, d.v FROM d JOIN s ON d.g = s.g CROSS JOIN p)"
    )


def _group(con: duckdb.DuckDBPyConnection, t: str, claim: GroupClaim) -> Measured:
    chosen = "pool" if claim.key is None else f"g:{claim.key}"
    base = _buckets(t, claim.group, claim.value)
    if claim.measure == "mean":
        mean, n = _first(
            con,
            base + " SELECT avg(v), count(*) FROM b WHERE bucket = ?",
            [chosen],
        )
        return Measured(_float(mean), int(n))
    mean, grand, n = _first(
        con,
        base + " SELECT avg(v) FILTER (WHERE bucket = ?), "
        "avg(v) FILTER (WHERE bucket IS NOT NULL), "
        "count(*) FILTER (WHERE bucket = ?) FROM b",
        [chosen, chosen],
    )
    if mean is None or grand is None or grand == 0:
        return Measured(None, int(n))
    return Measured((float(mean) - float(grand)) / abs(float(grand)), int(n))


def _eta(con: duckdb.DuckDBPyConnection, t: str, claim: EtaClaim) -> Measured:
    base = _buckets(t, claim.group, claim.value)
    ssb, sst, n = _first(
        con,
        base + ", m AS (SELECT avg(v) AS grand, count(*) AS n FROM b WHERE bucket IS NOT NULL), "
        "c AS (SELECT count(*) AS n, avg(v) AS mean FROM b WHERE bucket IS NOT NULL "
        "GROUP BY bucket) "
        "SELECT (SELECT sum(c.n * pow(c.mean - m.grand, 2)) FROM c, m), "
        "(SELECT sum(pow(b.v - m.grand, 2)) FROM b, m WHERE b.bucket IS NOT NULL), "
        "(SELECT n FROM m)",
    )
    if ssb is None or sst is None or float(sst) <= 0:
        return Measured(0.0, int(n or 0))
    return Measured(math.sqrt(max(0.0, min(1.0, float(ssb) / float(sst)))), int(n))


# --------------------------------------------------------------------------- correlation


def _correlation(con: duckdb.DuckDBPyConnection, t: str, claim: CorrelationClaim) -> Measured:
    where, params = "TRUE", []
    if claim.group is not None:
        if claim.key is None:
            raise ValueError("a correlation within a group names the group")
        where, params = f"{_text(claim.group)} = ?", [claim.key]
    data = con.execute(
        f"SELECT x, y FROM (SELECT {_number(claim.x)} AS x, {_number(claim.y)} AS y FROM {t} "
        f"WHERE {where}) WHERE x IS NOT NULL AND y IS NOT NULL AND isfinite(x) AND isfinite(y)",
        params,
    ).fetchnumpy()
    x = np.asarray(data["x"], dtype=float)
    y = np.asarray(data["y"], dtype=float)
    if len(x) < 4 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return Measured(None, len(x))
    # Pearson's correlation of the ranks (ties share their average rank): Spearman's rho.
    rho = float(np.corrcoef(rankdata(x), rankdata(y))[0, 1])
    return Measured(abs(rho) if claim.absolute else rho, len(x))


# --------------------------------------------------------------------------- counts


def _share(con: duckdb.DuckDBPyConnection, t: str, claim: ShareClaim) -> Measured:
    g = _text(claim.column)
    params: list[Any] = []
    if claim.key is not None:
        hit = f"{g} = ?"
        params.append(claim.key)
    elif claim.exclude:
        hit = f"{g} NOT IN ({', '.join('?' for _ in claim.exclude)})"
        params.extend(claim.exclude)
    else:
        hit = "TRUE"
    where = f"{g} IS NOT NULL"
    if claim.given is not None:
        if claim.given_key is None:
            raise ValueError("a share within a group names the group")
        where += f" AND {_text(claim.given)} = ?"
        params.append(claim.given_key)
    hits, base = _first(
        con, f"SELECT count(*) FILTER (WHERE {hit}), count(*) FROM {t} WHERE {where}", params
    )
    if not base:
        return Measured(None, 0)
    return Measured(int(hits) / int(base), int(hits) if claim.n == "hits" else int(base))


def _bin(con: duckdb.DuckDBPyConnection, t: str, claim: BinClaim) -> Measured:
    if claim.bins < 1 or not 0 <= claim.index < claim.bins or not claim.width > 0:
        raise ValueError("not a bin")
    (rows,) = _first(
        con,
        f"SELECT count(*) FROM (SELECT {_number(claim.column)} AS x FROM {t}) WHERE x IS NOT NULL "
        f"AND least({claim.bins - 1}, greatest(0, floor((x - ?) / ?)))::INTEGER = ?",
        [claim.low, claim.width, claim.index],
    )
    return Measured(float(rows))


def _period(con: duckdb.DuckDBPyConnection, t: str, claim: PeriodClaim) -> Measured:
    # The grain is one of five fixed words (validated by the claim's type).
    p = f"date_trunc({literal(claim.grain)}, TRY_CAST({ident(claim.column)} AS TIMESTAMP))"
    if claim.value is None:
        (rows,) = _first(
            con,
            f"SELECT count(*) FROM (SELECT {p} AS p FROM {t}) WHERE p = CAST(? AS TIMESTAMP)",
            [claim.start],
        )
        return Measured(float(rows), int(rows))
    mean, rows = _first(
        con,
        f"SELECT avg(v), count(*) FROM (SELECT {p} AS p, {_value(claim.value)} AS v FROM {t}) "
        "WHERE p = CAST(? AS TIMESTAMP) AND isfinite(v)",
        [claim.start],
    )
    return Measured(_float(mean), int(rows))


def _missing_points(
    con: duckdb.DuckDBPyConnection, table: str, points: Iterable[ChartPoint]
) -> int:
    """How many scatter points have no row with exactly their pair of values."""
    by_columns: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for point in points:
        claim = point.claim
        assert isinstance(claim, PointClaim)  # noqa: S101  (filtered by the caller)
        if point.x is None:
            by_columns.setdefault((claim.x, claim.y), []).append((math.nan, point.value))
        else:
            by_columns.setdefault((claim.x, claim.y), []).append((point.x, point.value))
    missing = 0
    for (x, y), pairs in by_columns.items():
        try:
            (found,) = _first(
                con,
                "SELECT count(*) FROM (SELECT unnest(?::DOUBLE[]) AS px, "
                "unnest(?::DOUBLE[]) AS py) p WHERE EXISTS "
                f"(SELECT 1 FROM {ident(table)} WHERE {_number(x)} = p.px "
                f"AND {_number(y)} = p.py)",
                [[a for a, _ in pairs], [b for _, b in pairs]],
            )
        except duckdb.Error:
            logger.warning("chart claim could not be computed", extra={"claim": "point"})
            found = 0
        missing += len(pairs) - int(found)
    return missing


def _outliers(con: duckdb.DuckDBPyConnection, t: str, claim: OutlierClaim) -> Measured:
    v = _number(claim.column)
    q1, q3, n = _first(
        con,
        f"SELECT quantile_cont(x, 0.25), quantile_cont(x, 0.75), count(x) "
        f"FROM (SELECT {v} AS x FROM {t})",
    )
    if q1 is None or q3 is None:
        return Measured(None, int(n or 0))
    spread = float(q3) - float(q1)
    lo, hi = float(q1) - 1.5 * spread, float(q3) + 1.5 * spread
    (outside,) = _first(
        con, f"SELECT count(*) FROM (SELECT {v} AS x FROM {t}) WHERE x < ? OR x > ?", [lo, hi]
    )
    return Measured(float(outside), int(n))


def _missing(con: duckdb.DuckDBPyConnection, t: str, claim: MissingClaim) -> Measured:
    present = (
        f"{_text(claim.column)} IS NOT NULL"
        if claim.group
        else f"{ident(claim.column)} IS NOT NULL"
    )
    mean, rows = _first(
        con,
        f"SELECT avg(v), count(*) FROM (SELECT ({present}) AS has, {_value(claim.value)} AS v "
        f"FROM {t}) WHERE v IS NOT NULL AND isfinite(v) AND has = ?",
        [not claim.missing],
    )
    return Measured(_float(mean), int(rows))


def _rows(con: duckdb.DuckDBPyConnection, t: str, claim: RowsClaim) -> Measured:
    """1.0 when the group has at least one row and fewer than ``below`` measured rows."""
    g = _text(claim.column)
    measured = f"isfinite({_value(claim.value)})" if claim.value is not None else "TRUE"
    rows, counted = _first(
        con,
        f"SELECT count(*), count(*) FILTER (WHERE {measured}) FROM {t} WHERE {g} = ?",
        [claim.key],
    )
    return Measured(1.0 if int(rows) >= 1 and int(counted) < claim.below else 0.0, int(counted))


_MEASURES: Final[dict[type, Callable[[duckdb.DuckDBPyConnection, str, Any], Measured]]] = {
    GroupClaim: _group,
    EtaClaim: _eta,
    CorrelationClaim: _correlation,
    ShareClaim: _share,
    BinClaim: _bin,
    PeriodClaim: _period,
    OutlierClaim: _outliers,
    MissingClaim: _missing,
    RowsClaim: _rows,
}
