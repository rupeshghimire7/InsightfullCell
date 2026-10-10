"""Phase B, stage ``rank``: which findings to show first.

Scores every fact by evidence, effect size, the plan's relevance for its columns, novelty and
the sensitivity outcome, marks exact repeats as duplicates, and picks the highlights the
report's summary is written from (``engine.analysis.rank``). Ranking changes what is shown
first, never whether a finding is true.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from insightfulcell.engine.analysis import rank as ranking
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.pipeline.artifacts import fetch_json, put_json
from insightfulcell.pipeline.mine import findings_from_json
from insightfulcell.pipeline.sensitivity import rechecks_from_json
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class RankStage:
    name = "rank"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"weights": ranking.WEIGHTS, "highlights": ranking.HIGHLIGHTS}

    async def run(self, ctx: StageContext) -> StageResult:
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))
        facts = [
            f
            for finding in findings_from_json(await fetch_json(ctx, "mine", "findings"))
            for f in finding.facts
        ]
        rechecks = {
            r.fact_id: r
            for r in rechecks_from_json(await fetch_json(ctx, "sensitivity", "rechecks"))
        }
        ranked = ranking.rank(facts, plan, rechecks)
        best = ranking.highlights(ranked, {f.id: f for f in facts})
        key = await put_json(
            ctx,
            self.name,
            "ranking.json",
            {"ranked": [asdict(r) for r in ranked], "highlights": best},
        )
        return StageResult(
            output={
                "facts": len(ranked),
                "duplicates": sum(1 for r in ranked if r.duplicate_of is not None),
                "highlights": len(best),
            },
            artifacts={"ranking": key},
        )
