"""Maps of the resolved places: rows per region, and the outcome per region.

For every place column (or coordinate pair) the ``geo`` stage resolved, one map shows where the
rows are (each region's share of the rows that have a region) and, when the outcome is a
measure, a second shows its average per region. Values come from one grouped query per map;
each region also carries a claim, so ``visualize`` checks the map the same way it checks charts.

Regions with fewer than ten rows (ten rows with a value, for the average) are drawn hatched and
without their number, like small groups everywhere else. Rates per resident and tests of spatial
clustering need population and neighbour data the reference set does not hold (ADR-035).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import duckdb

from insightfulcell.engine.analysis.claims import (
    POOL_BELOW,
    GroupClaim,
    RowsClaim,
    ShareClaim,
    Value,
)
from insightfulcell.engine.analysis.facts import MapData, MapRegion
from insightfulcell.engine.analysis.miners import Derived
from insightfulcell.engine.geo.maps import outlines
from insightfulcell.engine.geo.resolve import Resolution
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import NUMERIC_MEANINGS, Meaning

CODE_SUFFIX: Final = "__code"
_FEW_NOTE: Final = "Hatched: fewer than ten rows, too few to show on their own."


@dataclass(frozen=True, slots=True)
class _Scope:
    level: Literal["country", "state"]
    country: str | None
    drill_column: str | None
    reference_set: str


@dataclass(frozen=True, slots=True)
class PlaceMap:
    """A map and the analysis it belongs under (the place column's own question)."""

    analysis_id: str
    map: MapData


def place_maps(
    con: duckdb.DuckDBPyConnection,
    plan: ResolvedPlan,
    resolutions: Sequence[Resolution],
    derived: Sequence[Derived],
    table: str = "a",
) -> list[PlaceMap]:
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    meanings = {c.column: Meaning(c.meaning) for c in plan.dictionary}
    target = plan.target.column if plan.target else None
    present = {row[0] for row in con.execute(f"DESCRIBE {ident(table)}").fetchall()}
    maps: list[PlaceMap] = []
    for resolution in resolutions:
        found = next((d for d in derived if d.origin == resolution.columns), None)
        if not resolution.resolved or found is None:
            continue
        code = found.name + CODE_SUFFIX
        if code not in present:
            continue
        key = "|".join(resolution.columns)
        label = found.label
        rows = _rows_per_region(con, table, code)
        if not rows:
            continue
        base = _Scope(
            level="state" if resolution.level == "state" else "country",
            country=resolution.scope.split(":", 1)[1] if ":" in resolution.scope else None,
            drill_column=resolution.columns[0] if resolution.kind != "points" else None,
            reference_set=resolution.reference_set,
        )
        maps.append(PlaceMap(f"places:{key}", _share_map(rows, code, label, base)))
        if (
            target
            and target in present
            and target not in resolution.columns
            and meanings.get(target) in NUMERIC_MEANINGS
            and meanings.get(target) is not Meaning.PERCENTAGE
        ):
            means = _mean_per_region(con, table, code, target)
            unit = next((c.unit for c in plan.dictionary if c.column == target), None)
            mapped = _mean_map(rows, means, code, target, names.get(target, target), label, base,
                               unit)  # fmt: skip
            if mapped is not None:
                maps.append(PlaceMap(f"places:{key}", mapped))
    return maps


def _rows_per_region(con: duckdb.DuckDBPyConnection, table: str, code: str) -> dict[str, int]:
    found = con.execute(
        f"SELECT c, count(*) FROM (SELECT NULLIF(trim(CAST({ident(code)} AS VARCHAR)), '') AS c "
        f"FROM {ident(table)}) WHERE c IS NOT NULL GROUP BY c"
    ).fetchall()
    return {str(c): int(n) for c, n in found}


def _mean_per_region(
    con: duckdb.DuckDBPyConnection, table: str, code: str, target: str
) -> dict[str, tuple[float, int]]:
    found = con.execute(
        f"SELECT c, avg(v), count(*) FROM (SELECT NULLIF(trim(CAST({ident(code)} AS VARCHAR)), "
        f"'') AS c, TRY_CAST({ident(target)} AS DOUBLE) AS v FROM {ident(table)}) "
        "WHERE c IS NOT NULL AND isfinite(v) GROUP BY c"
    ).fetchall()
    return {str(c): (float(m), int(n)) for c, m, n in found}


def _regions(
    rows: Mapping[str, int],
    scope: _Scope,
    value: Mapping[str, tuple[float, int] | None],
    claims: Mapping[str, GroupClaim | ShareClaim | RowsClaim],
) -> tuple[float, float, tuple[MapRegion, ...]]:
    drawn = outlines(scope.level, scope.country, focus=set(rows))
    regions: list[MapRegion] = []
    for shape in drawn.shapes:
        measured = value.get(shape.code)
        if shape.code not in rows:
            status = "no_rows"
        elif measured is None:
            status = "few_rows"
        else:
            status = "value"
        regions.append(
            MapRegion(
                code=shape.code,
                name=shape.name,
                path=shape.path,
                status=status,
                value=measured[0] if measured is not None else None,
                n=measured[1] if measured is not None else None,
                key=shape.name if status == "value" else None,
                claim=claims.get(shape.code) if status != "no_rows" else None,
            )
        )
    return drawn.width, drawn.height, tuple(regions)


def _share_map(rows: Mapping[str, int], code: str, label: str, base: _Scope) -> MapData:
    total = sum(rows.values())
    values: dict[str, tuple[float, int] | None] = {}
    claims: dict[str, GroupClaim | ShareClaim | RowsClaim] = {}
    for region, n in rows.items():
        if n >= POOL_BELOW:
            values[region] = (n / total, n)
            claims[region] = ShareClaim(column=code, key=region)
        else:
            values[region] = None
            claims[region] = RowsClaim(column=code, key=region)
    width, height, regions = _regions(rows, base, values, claims)
    return MapData(
        title=f"Share of rows by {label}",
        value_label="Share of rows",
        value_format="percent",
        place_label=label,
        width=width,
        height=height,
        regions=regions,
        note=_note(values),
        level=base.level,
        country=base.country,
        drill_column=base.drill_column,
        reference_set=base.reference_set,
    )


def _mean_map(  # noqa: PLR0913, PLR0917  (map fields)
    rows: Mapping[str, int],
    means: Mapping[str, tuple[float, int]],
    code: str,
    target: str,
    target_label: str,
    label: str,
    base: _Scope,
    unit: str | None,
) -> MapData | None:
    value = Value(column=target)
    values: dict[str, tuple[float, int] | None] = {}
    claims: dict[str, GroupClaim | ShareClaim | RowsClaim] = {}
    for region in rows:
        mean, n = means.get(region, (0.0, 0))
        if n >= POOL_BELOW:
            values[region] = (mean, n)
            claims[region] = GroupClaim(group=code, value=value, key=region)
        else:
            values[region] = None
            claims[region] = RowsClaim(column=code, key=region, value=value)
    if not any(v is not None for v in values.values()):
        return None
    width, height, regions = _regions(rows, base, values, claims)
    return MapData(
        title=f"Average {target_label} by {label}",
        value_label=f"Average {target_label}",
        value_format="number",
        unit=unit,
        place_label=label,
        width=width,
        height=height,
        regions=regions,
        note=_note(values),
        level=base.level,
        country=base.country,
        drill_column=base.drill_column,
        reference_set=base.reference_set,
    )


def _note(values: Mapping[str, tuple[float, int] | None]) -> str | None:
    return _FEW_NOTE if any(v is None for v in values.values()) else None
