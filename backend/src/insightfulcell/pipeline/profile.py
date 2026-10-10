"""Stage 2, ``profile``: per-column statistics, missing-value patterns, duplicates, outliers.

Writes the ``profile`` artifact and the run's ``run_columns`` rows (meaning, personal-data
flag, profile). Columns flagged as personal data are profiled by counts only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.ports import ColumnRecord
from insightfulcell.engine.profiling import Profile, profile_table
from insightfulcell.engine.semantics import ColumnSemantics, semantics_from_output
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class ProfileStage:
    name = "profile"
    version = "1"
    uses_run_inputs = True  # the run's reference date decides what is "in the future"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"sha256": source.sha256}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "ingest", "data", "data.parquet")
        semantics = semantics_from_output(await fetch_json(ctx, "ingest", "semantics"))

        def compute() -> Profile:
            with table(parquet) as con:
                return profile_table(con, semantics, today=ctx.inputs.today)

        profile = await asyncio.to_thread(compute)
        key = await put_json(ctx, self.name, "profile.json", profile.model_dump(mode="json"))
        await ctx.repository.save_columns(column_records(semantics, profile))
        t = profile.table
        return StageResult(
            output={
                "rows": t.rows,
                "columns": t.columns,
                "duplicate_rows": t.duplicate_rows,
                "complete_rows": t.complete_rows,
                "missing_cells": t.missing_cells,
                "constant_columns": len(t.constant_columns),
                "columns_with_issues": sum(1 for c in profile.columns if c.flags),
                "personal_data_columns": sum(1 for c in profile.columns if c.personal_data),
            },
            artifacts={"profile": key},
        )


def column_records(
    semantics: list[ColumnSemantics],
    profile: Profile,
    roles: Mapping[str, tuple[str, str | None, str | None]] | None = None,
) -> list[ColumnRecord]:
    """``roles`` maps a column to (role, display name, unit) once a plan exists."""
    by_name = {p.name: p for p in profile.columns}
    records = []
    for s in semantics:
        p = by_name[s.name]
        role, display, unit = (roles or {}).get(s.name, (None, None, s.unit))
        records.append(
            ColumnRecord(
                position=s.position,
                name=s.name,
                physical_type=s.physical_type,
                meaning=s.meaning.value,
                confidence=s.confidence.value,
                personal_data=s.personal_data.value if s.personal_data else None,
                unit=unit,
                display_name=display,
                role=role,
                flags=list(p.flags),
                profile={
                    **p.model_dump(
                        mode="json",
                        exclude={"name", "position", "physical_type", "meaning", "flags"},
                    ),
                    "missing": p.missing,
                    "missing_share": round(p.missing_share, 4),
                    "reasons": list(s.reasons),
                    "parse": s.parse.model_dump(mode="json") if s.parse else None,
                },
            )
        )
    return records
