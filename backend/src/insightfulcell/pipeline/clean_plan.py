"""Stage 4, ``clean_plan``: rule and planner proposals become typed operations, each lossless or
lossy, measured on every row, with a starting decision under the organisation's policy.

The run then pauses at the review gate, where a person checks the cleaning and the plan (and
may ask for changes), unless the organisation's policy is ``auto_safe``: then lossy changes
are simply not applied and the run goes on. Decisions a person already made in this run (before
asking for the plan to be revised) are kept. Nothing is applied here: Phase B's
``clean_apply`` applies the approved plan to the original bytes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from insightfulcell.engine.cleaning.operations import Decision, Operation, Policy
from insightfulcell.engine.cleaning.proposals import propose
from insightfulcell.engine.planning.language import CleaningKind, ResolvedPlan, content_hash
from insightfulcell.engine.ports import CleaningRecord
from insightfulcell.engine.profiling import Profile
from insightfulcell.engine.semantics import semantics_from_output
from insightfulcell.pipeline.artifacts import fetch, fetch_json, put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult


class CleanPlanStage:
    name = "clean_plan"
    version = "2"
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

        operations = _keep_decisions(
            await asyncio.to_thread(compute), ctx.inputs.cleaning_decisions
        )
        # Every plan is reviewed (the person may add or remove questions), unless the
        # organisation applies safe changes automatically and never pauses.
        review = policy is not Policy.AUTO_SAFE
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


def _keep_decisions(operations: list[Operation], decided: Mapping[str, bool]) -> list[Operation]:
    """Decisions a person made before asking for a revised plan, for operations that are
    still proposed. Personal-data exclusions stay applied whatever was sent."""
    out = []
    for op in operations:
        choice = decided.get(op.id)
        locked = op.kind is CleaningKind.EXCLUDE_COLUMN and op.decision is Decision.ACCEPTED
        if choice is None or op.decision is not Decision.PENDING or locked:
            out.append(op)
            continue
        decision = Decision.ACCEPTED if choice else Decision.REJECTED
        out.append(op.model_copy(update={"decision": decision, "notice": "Decided by a person."}))
    return out
