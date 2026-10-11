"""Drill-downs: explore a subset of a finished analysis as a sub-run.

A person picks groups of one column from a finished report (a bar, a map region). The sub-run
is a new run of the same dataset version that starts at Phase B: it copies its parent's Phase A
checkpoints (the stages are not run again), the approved plan and the approved cleaning plan,
then keeps only the chosen rows right after the cleaning and recomputes everything else. Its
facts are labelled exploratory; its report and dashboard say which subset they cover.

All in one transaction (row-level security, ``Idempotency-Key``), then the Phase B job is
deferred; a lost job is re-deferred by the reconciler like any other queued run.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import (
    AnalysisPlan,
    CleaningOp,
    CleaningPlan,
    DatasetVersion,
    Run,
    RunColumn,
    RunStage,
)
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import (
    DatasetVersionPurged,
    DrilldownNotAllowed,
    InvalidDrilldown,
    ReportOutdated,
)
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.domain.runs import EventType, Phase, Queue, RunStatus, StageStatus
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.engine.planning.language import ResolvedPlan
from insightfulcell.engine.subset import MAX_VALUES, Subset, explorable
from insightfulcell.pipeline.definition import Pipeline
from insightfulcell.queue.port import JobQueue, PhaseJob
from insightfulcell.services import idempotency, run_store
from insightfulcell.services.dto import RunAccepted

logger = logging.getLogger(__name__)

#: Drill-downs of one run (each one is a full Phase B with its own narration).
MAX_PER_RUN: Final = 25


def drilldown_path(run_id: UUID) -> str:
    return f"/v1/runs/{run_id}/drilldowns"


class DrilldownService:
    def __init__(self, db: TenantDatabase, queue: JobQueue, pipeline: Pipeline) -> None:
        self._db = db
        self._queue = queue
        self._pipeline = pipeline

    async def create(
        self,
        principal: Principal,
        run_id: UUID,
        *,
        column: str,
        values: Sequence[str],
        idempotency_key: str,
    ) -> RunAccepted:
        require(principal, Action.DRILLDOWN_CREATE)
        kept = tuple(dict.fromkeys(values))  # order kept, repeats dropped
        try:
            subset = Subset(column=column, values=kept, parent_run_id=str(run_id))
        except ValidationError as exc:
            raise InvalidDrilldown(
                f"Choose between 1 and {MAX_VALUES} groups, each named in at most 200 characters.",
                hint="Pick a bar or a map region in the report.",
            ) from exc
        digest = idempotency.request_hash(
            {"run_id": str(run_id), "column": column, "values": sorted(kept)}
        )
        path = drilldown_path(run_id)
        async with self._db.transaction(principal) as session:
            replay = await idempotency.claim(
                session, principal, key=idempotency_key, path=path, digest=digest
            )
            if replay is not None:
                detail = await run_store.run_detail(
                    session, principal.org_id, UUID(str(replay["run_id"]))
                )
                return RunAccepted(detail=detail, created=False)

            parent = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            await self._check_parent(session, principal, parent)
            plan_row = await _latest_resolved(session, principal.org_id, parent.id)
            cleaning = await _approved_cleaning(session, principal.org_id, parent.id)
            if plan_row is None or cleaning is None:
                raise DrilldownNotAllowed(
                    "This analysis has no approved plan to explore.",
                    hint="Explore a finished analysis.",
                )
            if not explorable(ResolvedPlan.model_validate(plan_row.plan), column):
                raise InvalidDrilldown(
                    "This column cannot be explored on its own: choose a column of groups that "
                    "the analysis used.",
                    hint="Pick a bar or a map region in the report.",
                )

            child = Run(
                id=new_uuid7(),
                org_id=principal.org_id,
                dataset_id=parent.dataset_id,
                dataset_version_id=parent.dataset_version_id,
                created_by=principal.user_id,
                code_version=self._pipeline.code_version,
                phase=Phase.B.value,
                parent_run_id=parent.id,
                inputs={**(parent.inputs or {}), "drilldown": subset.model_dump(mode="json")},
            )
            session.add(child)
            await session.flush()
            await self._copy_stages(session, principal.org_id, parent.id, child.id)
            await _copy_plan(session, principal.org_id, parent.id, child.id, plan_row)
            await _copy_cleaning(session, principal.org_id, child.id, cleaning)
            await _copy_columns(session, principal.org_id, parent.id, child.id)
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=child.id,
                type_=EventType.RUN_QUEUED,
                # Never the values kept: they are cell values.
                data={"drilldown_of": str(parent.id), "column": column, "groups": len(kept)},
            )
            await idempotency.complete(
                session,
                principal,
                key=idempotency_key,
                status_code=202,
                body={"run_id": str(child.id)},
            )
            detail = await run_store.run_detail(session, principal.org_id, child.id)

        await self._defer(PhaseJob(child.id, principal.org_id, Phase.B, Queue.STANDARD))
        return RunAccepted(detail=detail, created=True)

    async def _check_parent(self, session: AsyncSession, principal: Principal, parent: Run) -> None:
        if RunStatus(parent.status) is not RunStatus.SUCCEEDED:
            raise DrilldownNotAllowed(
                "Only a finished analysis can be explored.",
                hint="Wait for the analysis to finish, then explore its report.",
            )
        if parent.parent_run_id is not None:
            raise DrilldownNotAllowed(
                "This is already an exploration of a subset; it cannot be narrowed further.",
                hint="Explore from the full analysis instead.",
            )
        if parent.code_version != self._pipeline.code_version or parent.inputs is None:
            raise ReportOutdated(
                "This analysis was made by an earlier version of InsightfulCell, so it cannot "
                "be explored.",
                hint="Start a new analysis of the dataset, then explore its report.",
            )
        purged = (
            await session.execute(
                select(DatasetVersion.purged_at).where(
                    DatasetVersion.id == parent.dataset_version_id,
                    DatasetVersion.org_id == principal.org_id,
                )
            )
        ).scalar_one_or_none()
        if purged is not None:
            raise DatasetVersionPurged(
                "The original file of this version was deleted under the retention policy.",
                hint="Upload the file again to analyse it.",
            )
        children = (
            await session.execute(
                select(func.count())
                .select_from(Run)
                .where(Run.org_id == principal.org_id, Run.parent_run_id == parent.id)
            )
        ).scalar_one()
        if int(children) >= MAX_PER_RUN:
            raise DrilldownNotAllowed(
                f"This analysis already has {MAX_PER_RUN} explorations, the most allowed.",
                hint="Open one of the existing explorations, or start a new analysis.",
            )

    async def _copy_stages(
        self, session: AsyncSession, org_id: UUID, parent_id: UUID, child_id: UUID
    ) -> None:
        """Phase A as the parent finished it (reused, not run again); Phase B pending."""
        done = {s.name: s for s in await run_store.load_stages(session, org_id, parent_id)}
        for planned in self._pipeline.planned():
            name = planned.stage.name
            if planned.phase is Phase.A:
                source = done.get(name)
                if source is None or source.status != StageStatus.SUCCEEDED.value:
                    raise ReportOutdated(
                        "This analysis does not have every step this version needs.",
                        hint="Start a new analysis of the dataset, then explore its report.",
                    )
                session.add(
                    RunStage(
                        org_id=org_id,
                        run_id=child_id,
                        name=name,
                        phase=planned.phase.value,
                        position=planned.position,
                        status=StageStatus.SUCCEEDED.value,
                        checkpoint_key=source.checkpoint_key,
                        code_version=source.code_version,
                        output=dict(source.output),
                        artifacts=dict(source.artifacts),
                        reused=True,
                        started_at=source.started_at,
                        finished_at=source.finished_at,
                    )
                )
            else:
                session.add(
                    RunStage(
                        org_id=org_id,
                        run_id=child_id,
                        name=name,
                        phase=planned.phase.value,
                        position=planned.position,
                    )
                )
        await session.flush()

    async def _defer(self, job: PhaseJob) -> None:
        try:
            await self._queue.defer_phase(job)
        except Exception:
            logger.warning(
                "could not defer phase job; the reconciler will retry",
                extra={"run_id": str(job.run_id), "phase": job.phase.value},
                exc_info=True,
            )


async def _latest_resolved(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> AnalysisPlan | None:
    return (
        await session.execute(
            select(AnalysisPlan)
            .where(
                AnalysisPlan.run_id == run_id,
                AnalysisPlan.org_id == org_id,
                AnalysisPlan.kind == "resolved",
            )
            .order_by(AnalysisPlan.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _approved_cleaning(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> tuple[CleaningPlan, list[CleaningOp]] | None:
    plan = (
        await session.execute(
            select(CleaningPlan)
            .where(CleaningPlan.run_id == run_id, CleaningPlan.org_id == org_id)
            .order_by(CleaningPlan.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if plan is None or plan.status != "approved":
        return None
    ops = list(
        (
            await session.execute(
                select(CleaningOp)
                .where(CleaningOp.plan_id == plan.id, CleaningOp.org_id == org_id)
                .order_by(CleaningOp.position)
            )
        ).scalars()
    )
    return plan, ops


async def _copy_plan(
    session: AsyncSession, org_id: UUID, parent_id: UUID, child_id: UUID, latest: AnalysisPlan
) -> None:
    """The parent's approved plan becomes the child's first (and only) revision, with the
    schema card the parent's first revision was planned from."""
    first_card = (
        await session.execute(
            select(AnalysisPlan.input_card).where(
                AnalysisPlan.run_id == parent_id,
                AnalysisPlan.org_id == org_id,
                AnalysisPlan.kind == "resolved",
                AnalysisPlan.revision == 1,
            )
        )
    ).scalar_one_or_none()
    session.add(
        AnalysisPlan(
            id=new_uuid7(),
            org_id=org_id,
            run_id=child_id,
            dataset_id=latest.dataset_id,
            kind="resolved",
            revision=1,
            source=latest.source,
            schema_fingerprint=latest.schema_fingerprint,
            prompt_version=latest.prompt_version,
            provider=latest.provider,
            model=latest.model,
            plan=dict(latest.plan),
            plan_hash=latest.plan_hash,
            rejected=list(latest.rejected),
            input_card=dict(first_card) if first_card is not None else None,
            fallback_reason=latest.fallback_reason,
            created_by=latest.created_by,
        )
    )
    await session.flush()


async def _copy_cleaning(
    session: AsyncSession,
    org_id: UUID,
    child_id: UUID,
    cleaning: tuple[CleaningPlan, list[CleaningOp]],
) -> None:
    """The approved cleaning plan, with who decided it and when (the decision is the
    parent's: nobody reviews a drill-down again)."""
    source, ops = cleaning
    plan = CleaningPlan(
        id=new_uuid7(),
        org_id=org_id,
        run_id=child_id,
        revision=1,
        policy=source.policy,
        status="approved",
        decided_by=source.decided_by,
        decided_at=source.decided_at,
        plan_hash=source.plan_hash,
    )
    session.add(plan)
    await session.flush()
    session.add_all(
        CleaningOp(
            org_id=org_id,
            plan_id=plan.id,
            position=op.position,
            op_id=op.op_id,
            kind=op.kind,
            column_name=op.column_name,
            params=dict(op.params),
            lossy=op.lossy,
            default_mode=op.default_mode,
            decision=op.decision,
            reason=op.reason,
            source=op.source,
            rows_affected=op.rows_affected,
            values_lost=op.values_lost,
            preview=list(op.preview),
            notice=op.notice,
        )
        for op in ops
    )
    await session.flush()


async def _copy_columns(
    session: AsyncSession, org_id: UUID, parent_id: UUID, child_id: UUID
) -> None:
    columns = (
        await session.execute(
            select(RunColumn).where(RunColumn.run_id == parent_id, RunColumn.org_id == org_id)
        )
    ).scalars()
    fields: tuple[str, ...] = (
        "position", "name", "physical_type", "meaning", "confidence", "personal_data", "unit",
        "display_name", "role", "flags", "profile",
    )  # fmt: skip
    copies: list[dict[str, Any]] = [{name: getattr(c, name) for name in fields} for c in columns]
    session.add_all(RunColumn(org_id=org_id, run_id=child_id, **c) for c in copies)
    await session.flush()
