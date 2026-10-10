"""Phase B, stage ``features``: the plan's features, kept only if they pass the signal gate.

Each enabled feature is computed on the cleaned data and kept only if its bias-corrected mutual
information with the outcome beats all 19 shuffled copies, and it is not a near copy of one of
its own columns or of a feature already kept (``engine.analysis.signal``). Without an outcome
there is nothing to gate on, and no feature is added.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.analysis.signal import SHUFFLES, GateResult, gate, to_json
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class FeaturesStage:
    name = "features"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"shuffles": SHUFFLES}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "clean_apply", "data", "cleaned.parquet")
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))

        def compute() -> list[GateResult]:
            with table(parquet, threads=1) as con:
                return gate(con, "t", plan, seed=str(ctx.run_id))

        results = await asyncio.to_thread(compute)
        key = await put_json(ctx, self.name, "features.json", [to_json(r) for r in results])
        outcomes = Counter(r.outcome for r in results)
        return StageResult(
            output={
                "candidates": len(results),
                "kept": outcomes["kept"],
                "no_signal": outcomes["no_signal"],
                "redundant": outcomes["redundant"],
                "not_computed": outcomes["not_computed"],
                "outcome": plan.target.column if plan.target else None,
                "kept_features": [r.label for r in results if r.outcome == "kept"],
            },
            artifacts={"features": key},
        )
