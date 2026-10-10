"""The analysis table: the cleaned data plus what Phase B derives from it.

- The features that passed the signal gate, as columns computed from the cleaned ones.
- For each resolved place column, two columns: the standard region's name (the groups the
  miners compare, so "NSW" and "New South Wales" are one group) and its code (ISO 3166). Values
  that did not match get no region.

Built the same way on the minimally cleaned data for the sensitivity check, so a re-test reads
the same columns. The source is never changed; the result is a new table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import duckdb

from insightfulcell.engine.analysis.miners import Derived
from insightfulcell.engine.analysis.signal import GateResult
from insightfulcell.engine.geo.resolve import COORDINATE_DECIMALS, Resolution, region_names
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import Meaning

_BASE = "__ic_base"


def build(  # noqa: PLR0913  (keyword-only)
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    resolutions: Sequence[Resolution],
    features: Sequence[GateResult],
    labels: Mapping[str, str],
    out: str = "a",
) -> list[Derived]:
    """Create table ``out`` and return the columns it adds."""
    present = [row[0] for row in con.execute(f"DESCRIBE {ident(source)}").fetchall()]
    taken = set(present)
    derived: list[Derived] = []
    computed: list[str] = []
    for feature in features:
        if feature.name in taken:
            continue
        computed.append(f"{feature.expression} AS {ident(feature.name)}")
        taken.add(feature.name)
        derived.append(
            Derived(feature.name, feature.label, feature.meaning, origin=feature.columns)
        )
    extra = "".join(f", {c}" for c in computed)
    # Expressions from the feature catalogue over quoted names.
    con.execute(f"CREATE OR REPLACE TABLE {_BASE} AS SELECT *{extra} FROM {ident(source)}")

    names = region_names()
    joins: list[str] = []
    selects: list[str] = []
    for i, resolution in enumerate(r for r in resolutions if r.resolved):
        if any(c not in taken for c in resolution.columns):
            continue
        name, code = _region_columns(resolution, taken)
        taken |= {name, code}
        lookup = f"__ic_geo_{i}"
        on = _lookup(con, lookup, resolution, names)
        joins.append(f"LEFT JOIN {lookup} ON {on}")
        selects += [f"{lookup}.__ic_name AS {ident(name)}", f"{lookup}.__ic_code AS {ident(code)}"]
        if resolution.kind == "points":
            lat, lon = (labels.get(c, c) for c in resolution.columns)
            level = "State" if resolution.level == "state" else "Country"
            derived.append(
                Derived(
                    name,
                    f"{level} from {lat} and {lon}",
                    Meaning.GEOGRAPHY,
                    origin=resolution.columns,
                )
            )
        else:
            column = resolution.columns[0]
            derived.append(
                Derived(
                    name,
                    labels.get(column, column),
                    Meaning.GEOGRAPHY,
                    replaces=column,
                    origin=resolution.columns,
                )
            )
    added = "".join(f", {s}" for s in selects)
    con.execute(
        f"CREATE OR REPLACE TABLE {ident(out)} AS SELECT b.*{added} FROM {_BASE} b "
        + " ".join(joins)
    )
    con.execute(f"DROP TABLE {_BASE}")
    return derived


def _region_columns(resolution: Resolution, taken: set[str]) -> tuple[str, str]:
    """``region__{columns}`` and ``region__{columns}__code``, numbered if already taken."""
    slug = "_".join(
        "".join(ch if ch.isalnum() else "_" for ch in c.lower()).strip("_") or "col"
        for c in resolution.columns
    )[:80]
    number = 0
    while True:
        name = f"region__{slug}" + (f"_{number}" if number else "")
        if name not in taken and f"{name}__code" not in taken:
            return name, f"{name}__code"
        number += 1


def _lookup(
    con: duckdb.DuckDBPyConnection, table: str, resolution: Resolution, names: Mapping[str, str]
) -> str:
    """Create the lookup table for one resolution; return the join condition."""
    if resolution.kind == "points":
        ys = [y for y, _, _ in resolution.points]
        xs = [x for _, x, _ in resolution.points]
        codes = [c for _, _, c in resolution.points]
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT unnest(?::DOUBLE[]) AS __ic_y, "
            "unnest(?::DOUBLE[]) AS __ic_x, unnest(?::VARCHAR[]) AS __ic_code, "
            "unnest(?::VARCHAR[]) AS __ic_name",
            [ys, xs, codes, [names.get(c, c) for c in codes]],
        )
        lat, lon = (ident(c) for c in resolution.columns)
        d = COORDINATE_DECIMALS
        return (
            f"round(TRY_CAST(b.{lat} AS DOUBLE), {d}) = {table}.__ic_y AND "
            f"round(TRY_CAST(b.{lon} AS DOUBLE), {d}) = {table}.__ic_x"
        )
    keys = [k for k, _ in resolution.mapping]
    codes = [c for _, c in resolution.mapping]
    con.execute(
        f"CREATE OR REPLACE TABLE {table} AS SELECT unnest(?::VARCHAR[]) AS __ic_key, "
        "unnest(?::VARCHAR[]) AS __ic_code, unnest(?::VARCHAR[]) AS __ic_name",
        [keys, codes, [names.get(c, c) for c in codes]],
    )
    column = ident(resolution.columns[0])
    return f"NULLIF(trim(CAST(b.{column} AS VARCHAR)), '') = {table}.__ic_key"
