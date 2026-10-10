"""Stage 4, ``clean_plan``: rule and planner proposals become typed operations, each lossless or
lossy, measured on every row, with a starting decision under the organisation's policy.

The run pauses at the review gate only when an operation needs a person's decision (a lossy
change under ``review_lossy``, anything under ``always_review``). Under ``auto_safe`` lossy
changes are simply not applied. Nothing is applied here: Phase B's ``clean_apply`` applies the
approved plan to the original bytes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.cleaning.operations import Decision, Operation, Policy, needs_review
from insightfulcell.engine.cleaning.proposals import propose
from insightfulcell.engine.planning.language import ResolvedPlan, content_hash
from insightfulcell.engine.ports import CleaningRecord
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.semantics import semantics_from_output
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class CleanPlanStage:
    name = "clean_plan"
    version = "1"
    uses_run_inputs = True

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {"sha256": source.sha256}

    async def run(self, ctx: StageContext) -> StageResult:
        parquet = await fetch(ctx, "ingest", "data", "data.parquet")
        semantics = semantics_from_output(await fetch_json(ctx, "ingest", "semantics"))
        profile = Profile.model_validate(await fetch_json(ctx, "profile", "profile"))
        plan = ResolvedPlan.model_validate(await fetch_json(ctx, "understand", "plan"))
        policy = Policy(ctx.inputs.cleaning_policy)

        def compute() -> list[Operation]:
            with table(parquet) as con:
                return propose(
                    con,
                    semantics=semantics,
                    profile=profile,
                    plan=plan,
                    policy=policy,
                    today=ctx.inputs.today,
                )

        operations = await asyncio.to_thread(compute)
        review = needs_review(operations)
        dumped = [op.model_dump(mode="json") for op in operations]
        await ctx.repository.save_cleaning_plan(
            CleaningRecord(
                policy=policy.value,
                status="proposed" if review else "approved",
                plan_hash=content_hash(dumped),
                operations=dumped,
            )
        )
        key = await put_json(ctx, self.name, "cleaning_plan.json", dumped)
        pending = [op for op in operations if op.decision is Decision.PENDING]
        return StageResult(
            output={
                "policy": policy.value,
                "operations": len(operations),
                "applied_automatically": sum(
                    1 for op in operations if op.decision is Decision.ACCEPTED
                ),
                "waiting_for_review": len(pending),
                "lossy": sum(1 for op in operations if op.lossy),
                "not_applied": sum(1 for op in operations if op.decision is Decision.REJECTED),
                "duplicate_rows": profile.table.duplicate_rows,
            },
            artifacts={"cleaning_plan": key},
            needs_review=review,
        )
