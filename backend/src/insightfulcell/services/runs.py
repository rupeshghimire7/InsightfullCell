"""Run use cases for the API (IC-007): submit, read, list, cancel, retry, approve.

Submitting commits the ``queued`` run *first* and defers the job afterwards: the runs table is
the source of truth, and a job lost between the two (the queue was unreachable, the process
died) is re-deferred by the reconciler. Status changes are compare-and-set updates, and callers
may send the ``row_version`` they last saw (``If-Match``) to refuse acting on a stale view.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import Dataset, DatasetVersion, Run, RunReport, RunStage
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import (
    DatasetNotFound,
    DatasetVersionNotFound,
    DatasetVersionPurged,
    NarrationNotRetryable,
    ReportOutdated,
    RunChanged,
)
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.domain.runs import (
    CANCELLABLE,
    EventType,
    Phase,
    Queue,
    RunStatus,
    StageStatus,
    require_status,
)
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.pipeline.definition import Pipeline
from insightfulcell.queue.port import JobQueue, PhaseJob
from insightfulcell.services import idempotency, run_inputs, run_store
from insightfulcell.services.datasets import MAX_PAGE_SIZE, decode_cursor, encode_cursor
from insightfulcell.services.dto import RunAccepted, RunDetail, RunEventBatch, RunPage

logger = logging.getLogger(__name__)

REPORT_STAGE = "report"
#: A worker that has not sent a heartbeat for this long is taken to be gone (Procrastinate sends
#: one every 10 seconds).
WORKER_HEARTBEAT_SECONDS = 60.0

_IDEMPOTENCY_PATH = "/v1/runs"


class RunService:
    def __init__(self, db: TenantDatabase, queue: JobQueue, pipeline: Pipeline) -> None:
        self._db = db
        self._queue = queue
        self._pipeline = pipeline

    # ------------------------------------------------------------------ submit

    async def create_run(
        self,
        principal: Principal,
        *,
        dataset_id: UUID,
        dataset_version_id: UUID | None,
        idempotency_key: str,
        review_mode: str = "auto",
    ) -> RunAccepted:
        require(principal, Action.RUN_CREATE)
        digest = idempotency.request_hash(
            {
                "dataset_id": str(dataset_id),
                "dataset_version_id": str(dataset_version_id) if dataset_version_id else None,
                "review_mode": review_mode,
            }
        )
        async with self._db.transaction(principal) as session:
            replay = await idempotency.claim(
                session, principal, key=idempotency_key, path=_IDEMPOTENCY_PATH, digest=digest
            )
            if replay is not None:
                detail = await run_store.run_detail(
                    session, principal.org_id, UUID(str(replay["run_id"]))
                )
                return RunAccepted(detail=detail, created=False)

            version = await self._version_to_analyse(
                session, principal, dataset_id, dataset_version_id
            )
            run = Run(
                id=new_uuid7(),
                org_id=principal.org_id,
                dataset_id=dataset_id,
                dataset_version_id=version.id,
                created_by=principal.user_id,
                code_version=self._pipeline.code_version,
                inputs=await run_inputs.snapshot(
                    session,
                    org_id=principal.org_id,
                    dataset_id=dataset_id,
                    created_at=datetime.now(UTC),
                    review_mode=review_mode,
                ),
            )
            session.add(run)
            await session.flush()
            session.add_all(
                RunStage(
                    org_id=principal.org_id,
                    run_id=run.id,
                    name=planned.stage.name,
                    phase=planned.phase.value,
                    position=planned.position,
                )
                for planned in self._pipeline.planned()
            )
            await session.flush()
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=run.id,
                type_=EventType.RUN_QUEUED,
                data={"dataset_version_id": str(version.id)},
            )
            await idempotency.complete(
                session,
                principal,
                key=idempotency_key,
                status_code=202,
                body={"run_id": str(run.id)},
            )
            detail = await run_store.run_detail(session, principal.org_id, run.id)

        await self._defer(PhaseJob(run.id, principal.org_id, Phase.A, Queue.STANDARD))
        return RunAccepted(detail=detail, created=True)

    async def _version_to_analyse(
        self,
        session: AsyncSession,
        principal: Principal,
        dataset_id: UUID,
        version_id: UUID | None,
    ) -> DatasetVersion:
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.org_id == principal.org_id,
                    Dataset.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if dataset is None:
            raise DatasetNotFound("No dataset with this id exists in your organisation.")
        stmt = select(DatasetVersion).where(
            DatasetVersion.org_id == principal.org_id, DatasetVersion.dataset_id == dataset_id
        )
        if version_id is not None:
            stmt = stmt.where(DatasetVersion.id == version_id)
        else:
            stmt = stmt.order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
        version = (await session.execute(stmt.limit(1))).scalar_one_or_none()
        if version is None:
            raise DatasetVersionNotFound(
                "This dataset has no such version."
                if version_id is not None
                else "This dataset has no uploaded file yet.",
                hint="Upload a file to the dataset first.",
            )
        if version.purged_at is not None:
            raise DatasetVersionPurged(
                "The original file of this version was deleted under the retention policy.",
                hint="Upload the file again to analyse it.",
            )
        return version

    # ------------------------------------------------------------------ read

    async def get_run(self, principal: Principal, run_id: UUID) -> RunDetail:
        require(principal, Action.RUN_READ)
        async with self._db.transaction(principal) as session:
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        if detail.run.status == RunStatus.QUEUED.value:
            alive = await self._queue.workers_alive(WORKER_HEARTBEAT_SECONDS)
            if alive is False:
                detail = replace(detail, waiting_for_worker=True)
        return detail

    async def list_runs(
        self,
        principal: Principal,
        *,
        dataset_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> RunPage:
        require(principal, Action.RUN_READ)
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        stmt = (
            select(Run)
            .where(Run.org_id == principal.org_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(limit + 1)
        )
        if dataset_id is not None:
            stmt = stmt.where(Run.dataset_id == dataset_id)
        if parent_run_id is not None:
            stmt = stmt.where(Run.parent_run_id == parent_run_id)
        if cursor is not None:
            after_ts, after_id = decode_cursor(cursor)
            stmt = stmt.where(tuple_(Run.created_at, Run.id) < tuple_(after_ts, after_id))
        async with self._db.transaction(principal) as session:
            rows = list((await session.execute(stmt)).scalars())
            page, overflow = rows[:limit], rows[limit:]
            names = await run_store.dataset_names(
                session, principal.org_id, (r.dataset_id for r in page)
            )
        items = tuple(run_store.to_summary(r, names.get(r.dataset_id, "")) for r in page)
        next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if overflow else None
        return RunPage(items=items, next_cursor=next_cursor)

    async def read_events(
        self, principal: Principal, run_id: UUID, *, after: int, limit: int = run_store.EVENT_BATCH
    ) -> RunEventBatch:
        """Events with a sequence number above ``after``, plus the run's current status."""
        require(principal, Action.RUN_READ)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id)
            events = await run_store.read_events(
                session, principal.org_id, run_id, after=after, limit=limit
            )
            return RunEventBatch(events=tuple(events), status=run.status)

    # ------------------------------------------------------------------ change

    async def cancel_run(
        self, principal: Principal, run_id: UUID, *, if_match: int | None = None
    ) -> RunDetail:
        """Cancel a queued, running or review-waiting run. A running run's worker stops at the
        next stage boundary. Cancelling a cancelled run is a no-op (safe to repeat)."""
        require(principal, Action.RUN_CANCEL)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            if RunStatus(run.status) is RunStatus.CANCELLED:
                return await run_store.run_detail(session, principal.org_id, run_id)
            _check_if_match(run, if_match)
            require_status(RunStatus(run.status), CANCELLABLE, "cancelled")
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.CANCELLED,
                sources=CANCELLABLE,
                values={"finished_at": func.now()},
            )
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                type_=EventType.RUN_CANCELLED,
                stage=run.stage,
                data={"previous_status": run.status},
            )
            return await run_store.run_detail(session, principal.org_id, run_id)

    async def retry_run(
        self, principal: Principal, run_id: UUID, *, if_match: int | None = None
    ) -> RunDetail:
        """Queue a failed run again. Stages that finished keep their checkpoints, so the run
        resumes where it stopped."""
        require(principal, Action.RUN_CREATE)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            _check_if_match(run, if_match)
            require_status(RunStatus(run.status), {RunStatus.FAILED}, "retried")
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.QUEUED,
                sources={RunStatus.FAILED},
                values={
                    "failure_code": None,
                    "failure_detail": None,
                    "failure_hint": None,
                    "finished_at": None,
                    "stage": None,
                    "attempts": 0,
                    "crash_count": 0,
                },
            )
            await _reset_unfinished_stages(session, principal.org_id, run_id)
            await run_store.append_event(
                session, org_id=principal.org_id, run_id=run_id, type_=EventType.RUN_RETRIED
            )
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        await self._defer(
            PhaseJob(run_id, principal.org_id, Phase(detail.run.phase), Queue(detail.run.queue))
        )
        return detail

    async def renarrate(
        self, principal: Principal, run_id: UUID, *, if_match: int | None = None
    ) -> RunDetail:
        """Write a finished run's report again after its narration was simplified because the
        model was unavailable. Every other stage keeps its checkpoint; the report stage runs
        again and saves a new revision of the report."""
        require(principal, Action.RUN_CREATE)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            _check_if_match(run, if_match)
            require_status(RunStatus(run.status), {RunStatus.SUCCEEDED}, "narrated again")
            if run.code_version != self._pipeline.code_version:
                # Its stages and checkpoints belong to another version of the pipeline: the
                # worker would refuse the run (and fail a finished analysis).
                raise ReportOutdated(
                    "This report was written by an earlier version of InsightfulCell, so it "
                    "cannot be narrated again.",
                    hint="Start a new analysis of the dataset to get a fresh report.",
                )
            document = (
                await session.execute(
                    select(RunReport.document)
                    .where(RunReport.run_id == run_id, RunReport.org_id == principal.org_id)
                    .order_by(RunReport.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            narration = (document or {}).get("narration") or {}
            if not narration.get("retryable"):
                raise NarrationNotRetryable(
                    "This report was not simplified by a model outage.",
                    hint="Only a report written while the language model was unavailable can "
                    "be narrated again.",
                )
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.QUEUED,
                sources={RunStatus.SUCCEEDED},
                values={"finished_at": None, "stage": None, "attempts": 0, "crash_count": 0},
            )
            await session.execute(
                update(RunStage)
                .where(
                    RunStage.run_id == run_id,
                    RunStage.org_id == principal.org_id,
                    RunStage.name == REPORT_STAGE,
                )
                .values(status=StageStatus.PENDING.value, started_at=None, finished_at=None)
            )
            await run_store.append_event(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                type_=EventType.RUN_RETRIED,
                stage=REPORT_STAGE,
                data={"reason": "renarrate"},
            )
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        await self._defer(
            PhaseJob(run_id, principal.org_id, Phase(detail.run.phase), Queue(detail.run.queue))
        )
        return detail

    async def approve_review(
        self, principal: Principal, run_id: UUID, *, if_match: int | None = None
    ) -> RunDetail:
        """Release a run from the review gate into Phase B. The cleaning-plan decision endpoint
        (Phase 3) records the decisions and then calls this."""
        require(principal, Action.RUN_CREATE)
        async with self._db.transaction(principal) as session:
            run = await run_store.require_run(session, principal.org_id, run_id, lock=True)
            _check_if_match(run, if_match)
            require_status(RunStatus(run.status), {RunStatus.AWAITING_REVIEW}, "approved")
            await run_store.transition(
                session,
                org_id=principal.org_id,
                run_id=run_id,
                target=RunStatus.QUEUED,
                sources={RunStatus.AWAITING_REVIEW},
                values={"phase": Phase.B.value, "stage": None, "attempts": 0},
            )
            await run_store.append_event(
                session, org_id=principal.org_id, run_id=run_id, type_=EventType.REVIEW_APPROVED
            )
            detail = await run_store.run_detail(session, principal.org_id, run_id)
        await self._defer(PhaseJob(run_id, principal.org_id, Phase.B, Queue(detail.run.queue)))
        return detail

    # ------------------------------------------------------------------ helpers

    async def _defer(self, job: PhaseJob) -> None:
        """Best effort: the run is already committed, and the reconciler re-defers any active
        run that has no job, so a queue outage delays the run instead of losing it."""
        try:
            await self._queue.defer_phase(job)
        except Exception:
            logger.warning(
                "could not defer phase job; the reconciler will retry",
                extra={"run_id": str(job.run_id), "phase": job.phase.value},
                exc_info=True,
            )


def _check_if_match(run: Run, if_match: int | None) -> None:
    if if_match is not None and if_match != run.row_version:
        raise RunChanged(
            "Someone or something changed this run after you loaded it.",
            hint="Reload the page, check the run's state, and try again.",
        )


async def _reset_unfinished_stages(session: AsyncSession, org_id: UUID, run_id: UUID) -> None:
    await session.execute(
        update(RunStage)
        .where(
            RunStage.run_id == run_id,
            RunStage.org_id == org_id,
            RunStage.status.in_([StageStatus.FAILED.value, StageStatus.RUNNING.value]),
        )
        .values(status=StageStatus.PENDING.value, started_at=None, finished_at=None)
    )
