"""Phase B, stage ``geo``: place names and coordinates resolved to standard regions.

Every place column of the plan (and every latitude and longitude pair) is matched against the
reference set the engine ships with (``engine.geo``). A column counts as places when at least
80% of its distinct values match; its mapping (value to ISO region code) is the artifact the
``mine`` stage joins onto the cleaned data. Personal-data columns are never read here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.geo.reference import REFERENCE_SET, REFERENCE_SHA256
from insightfulcell.engine.geo.resolve import Resolution, resolve_plan, to_json
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class GeoStage:
    name = "geo"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"reference_set": REFERENCE_SET, "reference_sha256": REFERENCE_SHA256}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "clean_apply", "data", "cleaned.parquet")
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))

        def compute() -> list[Resolution]:
            with table(parquet) as con:
                return resolve_plan(con, "t", plan)

        places = await asyncio.to_thread(compute)
        key = await put_json(
            ctx,
            self.name,
            "places.json",
            {"reference_set": REFERENCE_SET, "places": [to_json(p) for p in places]},
        )
        return StageResult(
            output={
                "reference_set": REFERENCE_SET,
                "places": [
                    {
                        "columns": list(p.columns),
                        "kind": p.kind,
                        "scope": p.scope,
                        "resolved": p.resolved,
                        "match_rate": round(p.rate, 4),
                        "fuzzy": p.fuzzy,
                    }
                    for p in places
                ],
            },
            artifacts={"places": key},
        )
