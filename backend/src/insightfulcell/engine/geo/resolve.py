"""Places to standard regions (system map: Places and maps).

- **Names** ("NSW", "New South Wales", "Queensand"): normalised, then matched exactly against
  names and codes, then fuzzily against full names only (a short code is never guessed). Each
  column is matched against every scope (all countries; each country's states) and resolved in
  the one where most of its distinct values match, so "WA" is Western Australia in a column of
  Australian states and Washington in a column of US states.
- **Australian postcodes**, in a column named as one: by Australia Post's published ranges.
- **Coordinates** (a latitude and a longitude column): assigned to the country whose outline
  contains them, through a spatial index; to states when at least 80% fall in one country the
  reference set has states for.

A column is resolved only when at least 80% of its distinct values (rows, for coordinates)
match; otherwise it gets no region, and a data-quality note names the values that did not.
Unmatched values never get a guessed region: they are left out of the place analysis.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import duckdb
import numpy as np
from rapidfuzz import fuzz, process
from shapely import points as make_points

from insightfulcell.engine.geo.reference import Gazetteer, Scope, load, normalise, outline_index
from insightfulcell.engine.planning.language import ResolvedPlan, Role
from insightfulcell.engine.semantics import name_tokens
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import Meaning

MATCH_RATE: Final = 0.8
MAX_DISTINCT: Final = 5_000
MAX_POINTS: Final = 250_000
FUZZY_SCORE: Final = 90
FUZZY_MIN_LENGTH: Final = 5
FUZZY_MARGIN: Final = 3
UNMATCHED_EXAMPLES: Final = 10
COORDINATE_DECIMALS: Final = 3  # about 100 m, like the reference outlines
COAST_DEGREES: Final = 0.05  # about 5 km: the simplification's error at a coast, and a margin
_LAT: Final = frozenset({"lat", "latitude"})
_LON: Final = frozenset({"lon", "lng", "long", "longitude"})
_POSTCODE: Final = frozenset({"postcode", "postcodes", "postal", "zip", "zipcode", "pcode"})
_DIGITS: Final = re.compile(r"^\d{3,4}(\.0+)?$")
# Australia Post's postcode ranges by state and territory.
_AU_POSTCODES: Final = (
    (200, 299, "AU-ACT"),
    (800, 999, "AU-NT"),
    (1000, 2599, "AU-NSW"),
    (2600, 2618, "AU-ACT"),
    (2619, 2899, "AU-NSW"),
    (2900, 2920, "AU-ACT"),
    (2921, 2999, "AU-NSW"),
    (3000, 3999, "AU-VIC"),
    (4000, 4999, "AU-QLD"),
    (5000, 5999, "AU-SA"),
    (6000, 6999, "AU-WA"),
    (7000, 7999, "AU-TAS"),
    (8000, 8999, "AU-VIC"),
    (9000, 9999, "AU-QLD"),
)

Kind = Literal["names", "postcodes", "points"]


@dataclass(frozen=True, slots=True)
class Resolution:
    """How one place column (or a latitude and longitude pair) maps to standard regions."""

    columns: tuple[str, ...]
    kind: Kind
    scope: str  # country, state:AU, postcode:AU
    level: str  # country or state
    resolved: bool
    distinct: int
    matched_distinct: int
    rows: int
    matched_rows: int
    fuzzy: int  # distinct values matched only by spelling similarity
    reference_set: str
    #: The most common values that did not match, with their rows (names and postcodes).
    unmatched: tuple[tuple[str, int], ...] = ()
    #: value -> region code (names, postcodes); empty for coordinates.
    mapping: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    #: (latitude, longitude, region code), coordinates rounded as ``COORDINATE_DECIMALS``.
    points: tuple[tuple[float, float, str], ...] = field(default=(), repr=False)

    @property
    def rate(self) -> float:
        if self.kind == "points":
            return self.matched_rows / self.rows if self.rows else 0.0
        return self.matched_distinct / self.distinct if self.distinct else 0.0


@dataclass(frozen=True, slots=True)
class PlaceColumn:
    name: str
    tokens: frozenset[str]


def coordinate_pairs(names: Iterable[str]) -> list[tuple[str, str]]:
    """Latitude and longitude columns that belong together: the same name with the other
    word ("pickup_lat" and "pickup_lon"), or the only two when there is one of each."""
    columns = [PlaceColumn(n, frozenset(name_tokens(n))) for n in names]
    lats = [c for c in columns if c.tokens & _LAT]
    lons = [c for c in columns if c.tokens & _LON and not c.tokens & _LAT]
    pairs: list[tuple[str, str]] = []
    for lat in lats:
        rest = lat.tokens - _LAT
        match = [lon for lon in lons if lon.tokens - _LON == rest]
        if len(match) == 1:
            pairs.append((lat.name, match[0].name))
    if not pairs and len(lats) == 1 and len(lons) == 1:
        pairs.append((lats[0].name, lons[0].name))
    return pairs


def is_coordinate(name: str) -> bool:
    return bool(set(name_tokens(name)) & (_LAT | _LON))


# --------------------------------------------------------------------------- names


def resolve_names(con: duckdb.DuckDBPyConnection, table: str, column: str) -> Resolution | None:
    """The best scope for a column of place names (or postcodes). ``None`` when it has too
    many distinct values to be a column of regions, or none at all."""
    rows = con.execute(
        f"SELECT v, count(*) FROM (SELECT NULLIF(trim(CAST({ident(column)} AS VARCHAR)), '') "
        f"AS v FROM {ident(table)}) WHERE v IS NOT NULL GROUP BY v ORDER BY count(*) DESC, v "
        f"LIMIT {MAX_DISTINCT + 1}"
    ).fetchall()
    if not rows or len(rows) > MAX_DISTINCT:
        return None
    counts = [(str(v), int(n)) for v, n in rows]
    gazetteer = load()
    candidates = [_match_scope(gazetteer, scope, column, counts) for scope in gazetteer.scopes()]
    if set(name_tokens(column)) & _POSTCODE:
        candidates.append(_match_postcodes(gazetteer, column, counts))
    # Most distinct values matched, then most rows, then fewest guesses, then the scope order.
    best = max(
        enumerate(candidates),
        key=lambda c: (c[1].matched_distinct, c[1].matched_rows, -c[1].fuzzy, -c[0]),
    )[1]
    return best


def _aliases(gazetteer: Gazetteer, scope: Scope) -> tuple[dict[str, str], dict[str, str]]:
    """(exact: normalised name or code -> region code, fuzzy: normalised full name -> code).
    A name two regions share is kept only for the region it is the main name of."""
    regions = gazetteer.in_scope(scope)
    owners: dict[str, set[str]] = {}
    for r in regions:
        for text in (*r.names, *r.codes):
            key = normalise(text)
            if key:
                owners.setdefault(key, set()).add(r.code)
    main = {normalise(r.name): r.code for r in regions}
    exact: dict[str, str] = {}
    for key, codes in owners.items():
        if len(codes) == 1:
            exact[key] = next(iter(codes))
        elif key in main:
            exact[key] = main[key]
    fuzzy = {
        normalise(n): r.code
        for r in regions
        for n in r.names
        if exact.get(normalise(n)) == r.code and len(normalise(n)) >= FUZZY_MIN_LENGTH
    }
    return exact, fuzzy


def _match_scope(
    gazetteer: Gazetteer, scope: Scope, column: str, counts: Sequence[tuple[str, int]]
) -> Resolution:
    exact, fuzzy_names = _aliases(gazetteer, scope)
    choices = sorted(fuzzy_names)
    mapping: list[tuple[str, str]] = []
    unmatched: list[tuple[str, int]] = []
    guessed = 0
    for value, n in counts:
        key = normalise(value)
        code = exact.get(key)
        if code is None and len(key) >= FUZZY_MIN_LENGTH and choices:
            code = _fuzzy(key, choices, fuzzy_names)
            guessed += code is not None
        if code is None:
            unmatched.append((value, n))
        else:
            mapping.append((value, code))
    return _resolution(gazetteer, (column,), "names", scope, counts, mapping, unmatched, guessed)


def _fuzzy(key: str, choices: Sequence[str], codes: dict[str, str]) -> str | None:
    """A spelling match only when it is close (ratio 90+) and clearly better than the next
    region's best match."""
    found = process.extract(key, choices, scorer=fuzz.ratio, limit=3)
    if not found or found[0][1] < FUZZY_SCORE:
        return None
    best_code = codes[found[0][0]]
    rivals = [score for name, score, _ in found[1:] if codes[name] != best_code]
    if rivals and found[0][1] - max(rivals) < FUZZY_MARGIN:
        return None
    return best_code


def _match_postcodes(
    gazetteer: Gazetteer, column: str, counts: Sequence[tuple[str, int]]
) -> Resolution:
    mapping: list[tuple[str, str]] = []
    unmatched: list[tuple[str, int]] = []
    for value, n in counts:
        code = au_postcode_state(value)
        if code is None:
            unmatched.append((value, n))
        else:
            mapping.append((value, code))
    scope = Scope("postcode", "AU")
    return _resolution(gazetteer, (column,), "postcodes", scope, counts, mapping, unmatched, 0)


def au_postcode_state(value: str) -> str | None:
    text = value.strip()
    if not _DIGITS.match(text):
        return None
    number = int(float(text))
    return next((code for low, high, code in _AU_POSTCODES if low <= number <= high), None)


def _resolution(  # noqa: PLR0913, PLR0917  (the parts of a resolution)
    gazetteer: Gazetteer,
    columns: tuple[str, ...],
    kind: Kind,
    scope: Scope,
    counts: Sequence[tuple[str, int]],
    mapping: list[tuple[str, str]],
    unmatched: list[tuple[str, int]],
    guessed: int,
) -> Resolution:
    total_rows = sum(n for _, n in counts)
    missed_rows = sum(n for _, n in unmatched)
    level = "state" if scope.level in {"state", "postcode"} else "country"
    result = Resolution(
        columns=columns,
        kind=kind,
        scope=scope.key,
        level=level,
        resolved=False,
        distinct=len(counts),
        matched_distinct=len(mapping),
        rows=total_rows,
        matched_rows=total_rows - missed_rows,
        fuzzy=guessed,
        reference_set=gazetteer.reference_set,
        unmatched=tuple(unmatched[:UNMATCHED_EXAMPLES]),
        mapping=tuple(mapping),
    )
    return _settle(result)


def _settle(result: Resolution) -> Resolution:
    resolved = result.rate >= MATCH_RATE
    return Resolution(
        columns=result.columns,
        kind=result.kind,
        scope=result.scope,
        level=result.level,
        resolved=resolved,
        distinct=result.distinct,
        matched_distinct=result.matched_distinct,
        rows=result.rows,
        matched_rows=result.matched_rows,
        fuzzy=result.fuzzy,
        reference_set=result.reference_set,
        unmatched=result.unmatched,
        mapping=result.mapping if resolved else (),
        points=result.points if resolved else (),
    )


# --------------------------------------------------------------------------- coordinates


def resolve_points(
    con: duckdb.DuckDBPyConnection, table: str, lat: str, lon: str
) -> Resolution | None:
    la, lo = f"TRY_CAST({ident(lat)} AS DOUBLE)", f"TRY_CAST({ident(lon)} AS DOUBLE)"
    total = int(con.execute(f"SELECT count(*) FROM {ident(table)}").fetchone()[0])  # type: ignore[index]
    data = con.execute(
        f"SELECT round(y, {COORDINATE_DECIMALS}) AS y, round(x, {COORDINATE_DECIMALS}) AS x, "
        f"count(*) AS n FROM (SELECT {la} AS y, {lo} AS x FROM {ident(table)}) "
        "WHERE y BETWEEN -90 AND 90 AND x BETWEEN -180 AND 180 GROUP BY 1, 2 ORDER BY 1, 2 "
        f"LIMIT {MAX_POINTS + 1}"
    ).fetchnumpy()
    ys, xs = np.asarray(data["y"], dtype=float), np.asarray(data["x"], dtype=float)
    counts = np.asarray(data["n"], dtype=np.int64)
    if len(ys) == 0 or len(ys) > MAX_POINTS:
        return None
    gazetteer = load()
    countries = _locate(xs, ys, "country")
    found = countries >= 0
    by_country: dict[str, int] = {}
    for position, n in zip(countries[found].tolist(), counts[found].tolist(), strict=True):
        code = gazetteer.regions[position].country
        by_country[code] = by_country.get(code, 0) + n
    level, scope, located = "country", Scope("country", None), countries
    if by_country:
        top = max(sorted(by_country), key=lambda c: by_country[c])
        has_states = any(r.level == "state" and r.country == top for r in gazetteer.regions)
        if has_states and by_country[top] >= MATCH_RATE * sum(by_country.values()):
            states = _locate(xs, ys, "state")
            in_top = np.array(
                [p >= 0 and gazetteer.regions[p].country == top for p in states.tolist()],
                dtype=bool,
            )
            level, scope, located = "state", Scope("state", top), np.where(in_top, states, -1)
    hit = located >= 0
    found_points = tuple(
        (y, x, gazetteer.regions[p].code)
        for y, x, p in zip(ys[hit].tolist(), xs[hit].tolist(), located[hit].tolist(), strict=True)
    )
    result = Resolution(
        columns=(lat, lon),
        kind="points",
        scope=scope.key,
        level=level,
        resolved=False,
        distinct=len(ys),
        matched_distinct=int(hit.sum()),
        rows=total,
        matched_rows=int(counts[hit].sum()),
        fuzzy=0,
        reference_set=gazetteer.reference_set,
        points=found_points,
    )
    return _settle(result)


def _locate(xs: np.ndarray, ys: np.ndarray, level: str) -> np.ndarray:
    """For each point, the position of the region containing it (the first, on a border); a
    point just outside every outline (a harbour the simplified coast cuts off) takes the nearest
    region within ``COAST_DEGREES``; otherwise -1."""
    tree, positions = outline_index(level)
    located = np.full(len(xs), -1, dtype=np.int64)
    geoms = make_points(xs, ys)
    _first(located, tree.query(geoms, predicate="intersects"), positions)
    outside = np.flatnonzero(located < 0)
    if len(outside):
        near = tree.query_nearest(geoms[outside], max_distance=COAST_DEGREES, all_matches=False)
        mapped = np.vstack([outside[near[0]], near[1]]) if near.size else near
        _first(located, mapped, positions)
    return located


def _first(located: np.ndarray, pairs: np.ndarray, positions: tuple[int, ...]) -> None:
    """Rows of ``pairs``: point index, tree index. The lowest tree index wins on a border."""
    if pairs.size == 0:
        return
    order = np.lexsort((pairs[1], pairs[0]))
    for point, entry in zip(pairs[0][order].tolist(), pairs[1][order].tolist(), strict=True):
        if located[point] < 0:
            located[point] = positions[entry]


# --------------------------------------------------------------------------- the analysis table


def region_names() -> dict[str, str]:
    return {r.code: r.name for r in load().regions}


def resolve_plan(
    con: duckdb.DuckDBPyConnection, table: str, plan: ResolvedPlan
) -> list[Resolution]:
    """Every place column of the plan (not personal data, not left out), and every latitude and
    longitude pair, resolved against the reference set."""
    present = {row[0] for row in con.execute(f"DESCRIBE {ident(table)}").fetchall()}
    usable = [
        c
        for c in plan.dictionary
        if c.column in present and c.personal_data is None and c.role is not Role.EXCLUDED
    ]
    out: list[Resolution] = []
    for entry in usable:
        is_place = entry.meaning is Meaning.GEOGRAPHY or entry.role is Role.GEOGRAPHY
        if not is_place or is_coordinate(entry.column):
            continue
        found = resolve_names(con, table, entry.column)
        if found is not None:
            out.append(found)
    for lat, lon in coordinate_pairs(c.column for c in usable):
        found = resolve_points(con, table, lat, lon)
        if found is not None:
            out.append(found)
    return out


def to_json(resolution: Resolution) -> dict[str, Any]:
    return {
        "columns": list(resolution.columns),
        "kind": resolution.kind,
        "scope": resolution.scope,
        "level": resolution.level,
        "resolved": resolution.resolved,
        "distinct": resolution.distinct,
        "matched_distinct": resolution.matched_distinct,
        "rows": resolution.rows,
        "matched_rows": resolution.matched_rows,
        "fuzzy": resolution.fuzzy,
        "reference_set": resolution.reference_set,
        "unmatched": [list(u) for u in resolution.unmatched],
        "mapping": [list(m) for m in resolution.mapping],
        "points": [list(p) for p in resolution.points],
    }


def from_json(data: Mapping[str, Any]) -> Resolution:
    return Resolution(
        columns=tuple(data["columns"]),
        kind=data["kind"],
        scope=data["scope"],
        level=data["level"],
        resolved=bool(data["resolved"]),
        distinct=int(data["distinct"]),
        matched_distinct=int(data["matched_distinct"]),
        rows=int(data["rows"]),
        matched_rows=int(data["matched_rows"]),
        fuzzy=int(data["fuzzy"]),
        reference_set=data["reference_set"],
        unmatched=tuple((str(v), int(n)) for v, n in data.get("unmatched", [])),
        mapping=tuple((str(k), str(c)) for k, c in data.get("mapping", [])),
        points=tuple((float(y), float(x), str(c)) for y, x, c in data.get("points", [])),
    )
