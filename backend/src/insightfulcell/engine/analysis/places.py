"""What the analysis says about places: how well each place column matched the reference set,
and the geographic questions the plan did not already ask.

For every place column (or latitude and longitude pair) the run states one data-quality fact:
the share of values that matched, and examples of those that did not. For each resolved one it
also asks where the rows are and, when there is an outcome, how the outcome differs by region.
Rates per resident and tests of spatial clustering need population and neighbour data the
reference set does not hold yet; they arrive with the maps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from insightfulcell.engine.analysis import facts as fmt
from insightfulcell.engine.analysis.miners import Derived, Note
from insightfulcell.engine.geo.resolve import MATCH_RATE, Resolution
from insightfulcell.engine.planning.language import (
    MinerKind,
    ResolvedAnalysis,
    ResolvedPlan,
    Source,
)

EXAMPLES: Final = 2
EXAMPLE_CHARS: Final = 40
_METHOD: Final = (
    "Each distinct value is normalised (case, accents and punctuation ignored) and matched "
    "exactly against names and codes in the reference set ({reference}), then by close "
    "spelling against full names; a column counts as places when at least 80% of its distinct "
    "values match."
)
_POINTS_METHOD: Final = (
    "Each latitude and longitude pair (to about 100 m) is assigned to the region whose outline "
    "contains it in the reference set ({reference}), or the nearest one within about 5 km of "
    "a coast; coordinates count as places when at least 80% of rows are assigned."
)


def place_findings(
    plan: ResolvedPlan,
    resolutions: Sequence[Resolution],
    derived: Sequence[Derived],
) -> tuple[list[ResolvedAnalysis], dict[str, list[Note]]]:
    """Extra analyses for the places, and the notes stated under them."""
    names = {c.column: c.display_name or c.column for c in plan.dictionary}
    planned = {(a.kind, frozenset(a.columns)) for a in plan.analyses}
    compared = {
        frozenset(a.columns)
        for a in plan.analyses
        if a.kind in {MinerKind.SEGMENT, MinerKind.BENCHMARK, MinerKind.RELATIONSHIP}
    }
    points = {d.name: d for d in derived if not d.replaces}
    target = plan.target.column if plan.target else None
    analyses: list[ResolvedAnalysis] = []
    notes: dict[str, list[Note]] = {}
    for resolution in resolutions:
        key = "|".join(resolution.columns)
        label = " and ".join(names.get(c, c) for c in resolution.columns)
        column = _region_column(resolution, derived)
        if resolution.resolved and column is not None:
            where = points[column].label if column in points else label
            distribution = (MinerKind.DISTRIBUTION, frozenset({column}))
            first = ResolvedAnalysis(
                id=f"places:{key}",
                kind=MinerKind.DISTRIBUTION,
                columns=(column,) if distribution not in planned else (),
                question=f"Where are the rows, by {where}?",
                priority=2,
                source=Source.RULES,
            )
            analyses.append(first)
            notes[first.id] = [_matched(resolution, label)]
            if target and target != column and frozenset({column, target}) not in compared:
                analyses.append(
                    ResolvedAnalysis(
                        id=f"places:{key}:{target}",
                        kind=MinerKind.SEGMENT,
                        columns=(column, target),
                        question=f"How does {names.get(target, target)} differ across {where}?",
                        priority=2,
                        source=Source.RULES,
                    )
                )
        else:
            only = ResolvedAnalysis(
                id=f"places:{key}",
                kind=MinerKind.DISTRIBUTION,
                columns=(),
                question=f"Can {label} be placed on a map?",
                priority=3,
                source=Source.RULES,
            )
            analyses.append(only)
            notes[only.id] = [_unmatched(resolution, label)]
    return analyses, notes


def _region_column(resolution: Resolution, derived: Sequence[Derived]) -> str | None:
    """The column the miners group by: the place column itself (it reads the standard
    names) or, for coordinates, the region column added for them."""
    found = next((d for d in derived if d.origin == resolution.columns), None)
    if found is None:
        return None
    return found.replaces or found.name


def _examples(resolution: Resolution) -> list[str]:
    return [v[:EXAMPLE_CHARS] for v, _ in resolution.unmatched[:EXAMPLES]]


def _values(resolution: Resolution) -> Mapping[str, object]:
    return {
        "scope": resolution.scope,
        "reference_set": resolution.reference_set,
        "distinct": resolution.distinct,
        "matched_distinct": resolution.matched_distinct,
        "rows": resolution.rows,
        "matched_rows": resolution.matched_rows,
        "fuzzy": resolution.fuzzy,
        "match_rate": resolution.rate,
    }


def _matched(resolution: Resolution, label: str) -> Note:
    rows = fmt.share(resolution.matched_rows / resolution.rows) if resolution.rows else "0%"
    method = (_POINTS_METHOD if resolution.kind == "points" else _METHOD).format(
        reference=resolution.reference_set
    )
    if resolution.kind == "points":
        matched, total = fmt.count(resolution.matched_rows), fmt.count(resolution.rows)
        level = "a state or territory" if resolution.level == "state" else "a country"
        rest = "" if resolution.matched_rows == resolution.rows else "; the others have no region"
        return Note(
            kind="place_match",
            columns=resolution.columns,
            rows=resolution.rows,
            method=method,
            statement=f"{rows} of rows ({matched} of {total}) have {label} inside {level}{rest}.",
            numbers=(rows, matched, total),
            values=_values(resolution),
        )
    matched, distinct = fmt.count(resolution.matched_distinct), fmt.count(resolution.distinct)
    rate = fmt.share(resolution.rate)
    if resolution.kind == "postcodes":
        what = "are Australian postcodes, placed in their state or territory"
    else:
        what = "match a " + ("country" if resolution.level == "country" else "state")
    statement = (
        f"{matched} of the {distinct} different {label} values ({rate}) {what}, covering "
        f"{rows} of rows."
    )
    examples = _examples(resolution)
    if examples:
        statement += (
            f" The rest, such as {' and '.join(examples)}, have no region and are left out of "
            "comparisons by place."
        )
    return Note(
        kind="place_match",
        columns=resolution.columns,
        rows=resolution.rows,
        method=method,
        statement=statement,
        numbers=(matched, distinct, rate, rows),
        labels=tuple(examples),
        values=_values(resolution),
    )


def _unmatched(resolution: Resolution, label: str) -> Note:
    needed = fmt.share(MATCH_RATE)
    if resolution.kind == "points":
        rows = fmt.share(resolution.rate)
        share = "No row has" if resolution.matched_rows == 0 else f"Only {rows} of rows have"
        return Note(
            kind="place_unmatched",
            columns=resolution.columns,
            rows=resolution.rows,
            method=_POINTS_METHOD.format(reference=resolution.reference_set),
            statement=f"{share} {label} inside one country or state (at least {needed} is "
            "needed), so they are not placed on a map.",
            numbers=(rows, needed),
            values=_values(resolution),
        )
    rate = fmt.share(resolution.rate)
    examples = _examples(resolution)
    share = "None" if resolution.matched_distinct == 0 else f"Only {rate}"
    statement = (
        f"{share} of the different {label} values match a country or state in the reference "
        f"set (at least {needed} is needed), so {label} is not placed on a map."
    )
    if examples:
        statement += f" For example, {' and '.join(examples)} do not match."
    return Note(
        kind="place_unmatched",
        columns=resolution.columns,
        rows=resolution.rows,
        method=_METHOD.format(reference=resolution.reference_set),
        statement=statement,
        numbers=(rate, needed),
        labels=tuple(examples),
        values=_values(resolution),
    )
