"""Drill-downs: the subset of rows a sub-run analyses, chosen from a finished report.

A subset is one column and the groups to keep, as the report shows them: the column's own
values, or, for a place column the analysis resolved, the standard region names ("NSW" and "New
South Wales" are both "New South Wales"). It is applied right after the approved cleaning, so
every later step (places, features, mining, the sensitivity check) sees only those rows.
"""

from __future__ import annotations

from typing import Annotated, Final

import duckdb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from insightfulcell.engine.geo.resolve import is_coordinate, region_names, resolve_names
from insightfulcell.engine.planning.language import ResolvedPlan, Role
from insightfulcell.engine.sql import ident
from insightfulcell.engine.types import CATEGORICAL_MEANINGS, Meaning

MAX_VALUES: Final = 20
#: Fewer rows than this leave nothing worth testing (groups of ten, two of them, and a test).
MIN_ROWS: Final = 30

GroupValue = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Subset(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    values: Annotated[tuple[GroupValue, ...], Field(min_length=1, max_length=MAX_VALUES)]
    #: The run whose report the subset was chosen from (not part of the fingerprint: the same
    #: rows of the same cleaned data are the same subset).
    parent_run_id: str | None = None

    def fingerprint(self) -> dict[str, object]:
        return {"column": self.column, "values": sorted(set(self.values))}


def explorable(plan: ResolvedPlan, column: str) -> bool:
    """Whether a drill-down may filter on ``column``: a column of groups in the plan, not
    personal data, not left out of the analysis."""
    entry = next((c for c in plan.dictionary if c.column == column), None)
    if entry is None or entry.personal_data is not None or entry.role is Role.EXCLUDED:
        return False
    return Meaning(entry.meaning) in CATEGORICAL_MEANINGS and not is_coordinate(column)


def keep(con: duckdb.DuckDBPyConnection, table: str, plan: ResolvedPlan, subset: Subset) -> str:
    """A condition (over ``table``'s rows) selecting the subset. Place columns that resolve to
    regions are matched by region name, as the report grouped them."""
    entry = next((c for c in plan.dictionary if c.column == subset.column), None)
    value = f"NULLIF(trim(CAST({ident(subset.column)} AS VARCHAR)), '')"
    wanted = sorted(set(subset.values))
    is_place = entry is not None and (
        Meaning(entry.meaning) is Meaning.GEOGRAPHY or entry.role is Role.GEOGRAPHY
    )
    if is_place and not is_coordinate(subset.column):
        resolution = resolve_names(con, table, subset.column)
        if resolution is not None and resolution.resolved:
            names = region_names()
            keys = sorted(k for k, code in resolution.mapping if names.get(code) in wanted)
            con.execute(
                "CREATE OR REPLACE TEMP TABLE __ic_subset AS SELECT unnest(?::VARCHAR[]) AS k",
                [keys],
            )
            return f"{value} IN (SELECT k FROM __ic_subset)"
    con.execute(
        "CREATE OR REPLACE TEMP TABLE __ic_subset AS SELECT unnest(?::VARCHAR[]) AS k", [wanted]
    )
    return f"{value} IN (SELECT k FROM __ic_subset)"
