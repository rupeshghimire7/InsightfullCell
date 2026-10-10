"""Phase B, stage ``sensitivity``: the top findings re-tested on minimally cleaned data.

The ten strongest reliable findings are re-tested on the upload with only the lossless cleaning
applied (``clean_apply``'s minimal table), through the same analysis table and the same miners,
and each is recorded as holding, weakening, flipping or not re-testable
(``engine.analysis.sensitivity``). When no lossy change was applied the two tables are the same
and nothing is re-run. The outcomes go to the test log; the facts themselves never change.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from insightfulcell.engine.analysis import sensitivity, stats
from insightfulcell.engine.analysis.facts import Fact
from insightfulcell.engine.analysis.sensitivity import Recheck
from insightfulcell.engine.geo.resolve import resolve_plan
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.engine.ports import TestRecord
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.mine import analyse, findings_from_json, load_features
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class SensitivityStage:
    name = "sensitivity"
    version = "1"

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"top": sensitivity.TOP}

    async def run(self, ctx: StageContext) -> StageResult:
        found = await fetch_json(ctx, "mine", "findings")
        facts = [f for finding in findings_from_json(found) for f in finding.facts]
        chosen = sensitivity.top_findings(facts)
        threshold = float(found.get("threshold", 0.0))
        if "minimal" not in ctx.upstream["clean_apply"].artifacts or not chosen:
            rechecks = sensitivity.unchanged(chosen)
        else:
            rechecks = await self._retest(ctx, chosen, threshold)
        await ctx.repository.save_rechecks([_record(r, facts) for r in rechecks])
        key = await put_json(
            ctx,
            self.name,
            "rechecks.json",
            {"threshold": threshold, "rechecks": [_json(r) for r in rechecks]},
        )
        outcomes = Counter(r.outcome for r in rechecks)
        return StageResult(
            output={"rechecked": len(rechecks), **{k: outcomes[k] for k in sensitivity.NOTES}},
            artifacts={"rechecks": key},
        )

    async def _retest(
        self, ctx: StageContext, chosen: Sequence[Fact], threshold: float
    ) -> list[Recheck]:
        minimal = await fetch(ctx, "clean_apply", "minimal", "minimal.parquet")
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "clean_apply", "plan"))
        features = await load_features(ctx)
        needed = frozenset(f.analysis_id for f in chosen)

        def compute() -> list[Recheck]:
            with table(minimal, name="cleaned", threads=1) as con:
                # Places are resolved again: the same rules, on the minimally cleaned values.
                places = resolve_plan(con, "cleaned", plan)
                again, _ = analyse(con, plan, places, features, seed=str(ctx.run_id), only=needed)
            return sensitivity.compare(chosen, again, threshold)

        return await asyncio.to_thread(compute)


def _json(recheck: Recheck) -> dict[str, Any]:
    return {
        "fact_id": recheck.fact_id,
        "hypothesis": recheck.hypothesis,
        "outcome": recheck.outcome,
        "note": recheck.note,
        "effect": recheck.effect,
        "p_value": recheck.p_value,
        "retest_effect": recheck.retest_effect,
        "retest_p_value": recheck.retest_p_value,
        "retest_n": recheck.retest_n,
    }


def rechecks_from_json(data: Mapping[str, Any]) -> list[Recheck]:
    return [
        Recheck(
            fact_id=r["fact_id"],
            hypothesis=r["hypothesis"],
            outcome=r["outcome"],
            effect=float(r["effect"]),
            p_value=float(r["p_value"]),
            retest_effect=r.get("retest_effect"),
            retest_p_value=r.get("retest_p_value"),
            retest_n=r.get("retest_n"),
        )
        for r in data["rechecks"]
    ]


def _record(recheck: Recheck, facts: Sequence[Fact]) -> TestRecord:
    fact = next(f for f in facts if f.id == recheck.fact_id)
    assert fact.test is not None  # noqa: S101  (only tested facts are re-checked)
    return TestRecord(
        purpose="sensitivity",
        hypothesis=recheck.hypothesis,
        method=f"{fact.test.method}, on minimally cleaned data",
        columns=fact.columns,
        n=recheck.retest_n if recheck.retest_n is not None else fact.test.n,
        effect_name=fact.test.effect_name,
        effect=recheck.retest_effect,
        ci_low=None,
        ci_high=None,
        p_value=recheck.retest_p_value,
        q_value=None,
        alpha=stats.ALPHA,
        outcome=recheck.outcome,
        fact_id=recheck.fact_id,
    )
