"""Execute one phase of one run in the worker (IC-007).

Per phase job:

1. **Claim.** Re-read the run inside a transaction scoped to its organisation (a forged job
   argument finds nothing). A queued run becomes running, unless the organisation is already at
   its concurrent-run limit, in which case the job is deferred again shortly. A run that is not
   queued or running (cancelled, waiting for review, finished) is left alone.
2. **Stages.** For each stage of the phase, compute its checkpoint key. A stage already done
   with the same key is reused, not run again. Otherwise the run must still be running (this
   is where a cancel takes effect: at the next stage boundary), the stage runs with no
   transaction open, and its result is stored with the key.
3. **Finish.** Phase A ends at the review gate (``awaiting_review``) when a stage asked for
   review, otherwise it defers Phase B. Phase B ends the run as ``succeeded``.

Failures: a ``DomainError`` about the data (parse failure, integrity mismatch, limits) fails
the run with that error's own, user-safe message. Infrastructure trouble and unexpected errors
are retried with a growing delay, up to ``run_max_attempts``. Out-of-memory (in-process, or a
worker that died and was reaped) counts as a crash; after ``run_crashes_before_large_queue``
crashes the run moves to the ``large`` queue.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.config import Settings
from insightfulcell.db.models import DatasetVersion, Organization, Run, RunEvent, RunStage
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import DomainError, ErrorKind
from insightfulcell.domain.runs import EventType, Phase, Queue, RunStatus, StageStatus
from insightfulcell.ingestion.formats import FileFormat
from insightfulcell.logging_config import exception_summary
from insightfulcell.pipeline.definition import Pipeline
from insightfulcell.pipeline.stage import (
    SourceFile,
    Stage,
    StageContext,
    StageResult,
    checkpoint_key,
)
from insightfulcell.queue.port import JobQueue, PhaseJob
from insightfulcell.services import run_store
from insightfulcell.storage.port import ObjectNotFoundError, ObjectStore

logger = logging.getLogger(__name__)

#: Stored in a stage's output when it asked for the review gate, so a reused checkpoint
#: still asks.
REVIEW_FLAG = "review_required"


class Outcome(StrEnum):
    NOT_FOUND = "not_found"  # no such run in that organisation
    SKIPPED = "skipped"  # the run is not in a state this job can act on (or the job is stale)
    WAITING = "waiting"  # the organisation is at its concurrent-run limit; deferred again
    STOPPED = "stopped"  # the run was cancelled (or moved by someone else) at a boundary
    RETRYING = "retrying"  # a transient failure; deferred again with a delay
    FAILED = "failed"
    AWAITING_REVIEW = "awaiting_review"
    PHASE_COMPLETED = "phase_completed"  # Phase A done, Phase B deferred
    SUCCEEDED = "succeeded"


@dataclass(frozen=True, slots=True)
class Failure:
    """Why a run failed, in words safe to show the user (never cell values)."""

    code: str
    detail: str
    hint: str


@dataclass(slots=True)
class _Claim:
    run_id: UUID
    org_id: UUID
    phase: Phase
    queue: Queue
    attempts: int
    source: SourceFile
    stages: dict[str, RunStage] = field(default_factory=dict)


class _Stopped(Exception):
    """The run is no longer running: stop at this boundary."""


WORKER_CRASHED = Failure(
    code="worker-crashed",
    detail="The analysis stopped unexpectedly several times while working on this file.",
    hint="The file may be too large or unusual for the analysis. Try a smaller extract, or "
    "contact support with the run id.",
)
SOURCE_MISSING = Failure(
    code="source-missing",
    detail="The uploaded file is no longer in storage.",
    hint="Upload the file again, then start a new analysis.",
)
PIPELINE_CHANGED = Failure(
    code="pipeline-changed",
    detail="This run was started by an earlier version of the analysis and cannot continue.",
    hint="Start a new analysis of the same dataset.",
)


def internal_failure(stage: str | None) -> Failure:
    where = f" during the {stage} step" if stage else ""
    return Failure(
        code="internal-error",
        detail=f"Something went wrong on our side{where}, and retrying did not help.",
        hint="Try again later. If it keeps happening, contact support with the run id.",
    )


class RunExecutor:
    def __init__(
        self,
        db: TenantDatabase,
        store: ObjectStore,
        queue: JobQueue,
        pipeline: Pipeline,
        settings: Settings,
    ) -> None:
        self._db = db
        self._store = store
        self._queue = queue
        self._pipeline = pipeline
        self._settings = settings

    # ================================================================== entry point

    async def execute(self, run_id: UUID, org_id: UUID, phase: Phase) -> Outcome:
        claim = await self._claim(run_id, org_id, phase)
        if isinstance(claim, Outcome):
            if claim is Outcome.WAITING:
                await self._defer(
                    PhaseJob(run_id, org_id, phase, await self._queue_of(run_id, org_id)),
                    delay=self._settings.run_capacity_wait_seconds,
                )
            return claim
        try:
            return await self._run_phase(claim)
        except _Stopped:
            logger.info("run stopped at a stage boundary", extra={"run_id": str(run_id)})
            return Outcome.STOPPED

    # ================================================================== claim

    async def _claim(self, run_id: UUID, org_id: UUID, phase: Phase) -> _Claim | Outcome:
        async with self._db.system_transaction(org_id) as session:
            run = await run_store.load_run(session, org_id, run_id, lock=True)
            if run is None:
                logger.warning("phase job for an unknown run", extra={"run_id": str(run_id)})
                return Outcome.NOT_FOUND
            status = RunStatus(run.status)
            if run.phase != phase.value or status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return Outcome.SKIPPED

            if status is RunStatus.QUEUED:
                if not await self._has_capacity(session, org_id, run_id):
                    await self._note_waiting(session, org_id, run_id)
                    return Outcome.WAITING
                await run_store.transition(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    target=RunStatus.RUNNING,
                    sources={RunStatus.QUEUED},
                    values={"started_at": func.coalesce(Run.started_at, func.now())},
                )
                await run_store.append_event(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    type_=EventType.RUN_STARTED,
                    data={"phase": phase.value},
                )

            attempts = run.attempts + 1
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.org_id == org_id)
                .values(attempts=attempts, updated_at=func.now())
            )
            if attempts > self._settings.run_max_attempts:
                await self._fail_in(session, org_id, run_id, run.stage, WORKER_CRASHED)
                return Outcome.FAILED

            version = (
                await session.execute(
                    select(DatasetVersion).where(
                        DatasetVersion.id == run.dataset_version_id,
                        DatasetVersion.org_id == org_id,
                    )
                )
            ).scalar_one()
            stages = await run_store.load_stages(session, org_id, run_id)
            planned = {(p.stage.name, p.phase.value, p.position) for p in self._pipeline.planned()}
            if {(s.name, s.phase, s.position) for s in stages} != planned:
                # The run was planned by a different version of the pipeline (a deploy changed
                # the stages in between): its rows cannot record this pipeline's work.
                await self._fail_in(session, org_id, run_id, None, PIPELINE_CHANGED)
                return Outcome.FAILED
            return _Claim(
                run_id=run_id,
                org_id=org_id,
                phase=phase,
                queue=Queue(run.queue),
                attempts=attempts,
                source=SourceFile(
                    version_id=version.id,
                    bronze_key=version.bronze_key,
                    sha256=version.sha256.hex(),
                    format=FileFormat(version.format),
                    bytes=version.bytes,
                ),
                stages={stage.name: stage for stage in stages},
            )

    async def _has_capacity(self, session: AsyncSession, org_id: UUID, run_id: UUID) -> bool:
        """At most ``max_concurrent_runs`` running runs per organisation. The advisory lock
        serialises claims within one organisation until this transaction commits."""
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"runs:{org_id}"},
        )
        limit = (
            await session.execute(
                select(Organization.max_concurrent_runs).where(Organization.id == org_id)
            )
        ).scalar_one()
        running = (
            await session.execute(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.org_id == org_id,
                    Run.status == RunStatus.RUNNING.value,
                    Run.id != run_id,
                )
            )
        ).scalar_one()
        return int(running) < int(limit)

    async def _note_waiting(self, session: AsyncSession, org_id: UUID, run_id: UUID) -> None:
        """Tell the user once (not on every re-check) that the run waits for a free slot."""
        last = (
            await session.execute(
                select(RunEvent.type)
                .where(RunEvent.run_id == run_id, RunEvent.org_id == org_id)
                .order_by(RunEvent.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last != EventType.RUN_WAITING.value:
            await run_store.append_event(
                session, org_id=org_id, run_id=run_id, type_=EventType.RUN_WAITING
            )

    # ================================================================== stages

    async def _run_phase(self, claim: _Claim) -> Outcome:
        results: dict[str, StageResult] = {}
        keys: list[str] = []
        for planned in self._pipeline.planned():
            if planned.phase is Phase.B and claim.phase is Phase.A:
                break
            stage = planned.stage
            key = checkpoint_key(stage, claim.source, keys)
            row = claim.stages.get(stage.name)
            done = row is not None and row.status == StageStatus.SUCCEEDED.value
            if planned.phase is not claim.phase:
                # Phase B reads the results Phase A stored.
                if not done or row is None or row.checkpoint_key != key:
                    return await self._fail(claim, None, PIPELINE_CHANGED)
                results[stage.name] = _result_from_row(row)
            elif done and row is not None and row.checkpoint_key == key:
                await self._reuse_stage(claim, stage.name)
                results[stage.name] = _result_from_row(row)
            else:
                outcome = await self._run_stage(claim, stage, key, results)
                if isinstance(outcome, Outcome):
                    return outcome
                results[stage.name] = outcome
            keys.append(key)
        return await self._finish_phase(claim, results)

    async def _run_stage(
        self, claim: _Claim, stage: Stage, key: str, upstream: Mapping[str, StageResult]
    ) -> StageResult | Outcome:
        await self._start_stage(claim, stage.name)
        started = time.monotonic()
        try:
            with self._workdir(claim.run_id) as workdir:
                result = await stage.run(
                    StageContext(
                        org_id=claim.org_id,
                        run_id=claim.run_id,
                        source=claim.source,
                        workdir=workdir,
                        store=self._store,
                        checkpoint_key=key,
                        upstream=dict(upstream),
                    )
                )
        except asyncio.CancelledError:
            raise  # the worker is shutting down; the reconciler re-defers the run
        except MemoryError:
            logger.warning("stage ran out of memory", extra=self._log_extra(claim, stage.name))
            return await self.record_crash(
                claim.org_id, claim.run_id, reason="out_of_memory", defer=True
            )
        except ObjectNotFoundError:
            return await self._fail(claim, stage.name, SOURCE_MISSING)
        except DomainError as exc:
            if exc.kind is ErrorKind.UPSTREAM:
                return await self._retry_or_fail(claim, stage.name, exc)
            return await self._fail(
                claim,
                stage.name,
                Failure(code=exc.code, detail=exc.detail, hint=exc.hint or ""),
            )
        except Exception as exc:
            # Not logger.exception: the message could quote a cell value (exception_summary).
            logger.error(  # noqa: TRY400
                "stage failed unexpectedly",
                extra={**self._log_extra(claim, stage.name), **exception_summary(exc)},
            )
            return await self._retry_or_fail(claim, stage.name, exc)
        await self._complete_stage(claim, stage, key, result, time.monotonic() - started)
        return result

    async def _start_stage(self, claim: _Claim, name: str) -> None:
        async with self._db.system_transaction(claim.org_id) as session:
            # The stage boundary: a cancelled (or otherwise moved) run stops here.
            touched = (
                await session.execute(
                    update(Run)
                    .where(
                        Run.id == claim.run_id,
                        Run.org_id == claim.org_id,
                        Run.status == RunStatus.RUNNING.value,
                        Run.phase == claim.phase.value,
                    )
                    .values(stage=name, updated_at=func.now())
                    .returning(Run.id)
                )
            ).scalar_one_or_none()
            if touched is None:
                raise _Stopped
            await session.execute(
                update(RunStage)
                .where(RunStage.run_id == claim.run_id, RunStage.name == name)
                .values(
                    status=StageStatus.RUNNING.value,
                    started_at=func.now(),
                    finished_at=None,
                    attempts=RunStage.attempts + 1,
                    reused=False,
                )
            )
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.STAGE_STARTED,
                stage=name,
                data={"attempt": claim.attempts},
            )

    async def _complete_stage(
        self, claim: _Claim, stage: Stage, key: str, result: StageResult, seconds: float
    ) -> None:
        output = dict(result.output)
        if result.needs_review:
            output[REVIEW_FLAG] = True
        async with self._db.system_transaction(claim.org_id) as session:
            # Recorded even if the run was cancelled meanwhile: the work was done, and its
            # checkpoint is valid. The next boundary decides whether to go on.
            await session.execute(
                update(RunStage)
                .where(RunStage.run_id == claim.run_id, RunStage.name == stage.name)
                .values(
                    status=StageStatus.SUCCEEDED.value,
                    checkpoint_key=key,
                    code_version=stage.version,
                    output=output,
                    artifacts=dict(result.artifacts),
                    finished_at=func.now(),
                )
            )
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.STAGE_COMPLETED,
                stage=stage.name,
                data={**event_summary(output), "duration_ms": round(seconds * 1000)},
            )

    async def _reuse_stage(self, claim: _Claim, name: str) -> None:
        async with self._db.system_transaction(claim.org_id) as session:
            await session.execute(
                update(RunStage)
                .where(RunStage.run_id == claim.run_id, RunStage.name == name)
                .values(reused=True)
            )
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.STAGE_REUSED,
                stage=name,
            )

    # ================================================================== finish

    async def _finish_phase(self, claim: _Claim, results: Mapping[str, StageResult]) -> Outcome:
        if claim.phase is Phase.A:
            needs_review = any(
                results[stage.name].needs_review
                for stage in self._pipeline.stages(Phase.A)
                if stage.name in results
            )
            if needs_review:
                return await self._request_review(claim)
            return await self._complete_phase_a(claim)
        return await self._succeed(claim)

    async def _request_review(self, claim: _Claim) -> Outcome:
        async with self._db.system_transaction(claim.org_id) as session:
            moved = await run_store.transition(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                target=RunStatus.AWAITING_REVIEW,
                sources={RunStatus.RUNNING},
                values={"review_requested_at": func.now(), "stage": None},
            )
            if moved is None:
                raise _Stopped
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.REVIEW_REQUIRED,
            )
        return Outcome.AWAITING_REVIEW

    async def _complete_phase_a(self, claim: _Claim) -> Outcome:
        async with self._db.system_transaction(claim.org_id) as session:
            queue = (
                await session.execute(
                    update(Run)
                    .where(
                        Run.id == claim.run_id,
                        Run.org_id == claim.org_id,
                        Run.status == RunStatus.RUNNING.value,
                        Run.phase == Phase.A.value,
                    )
                    .values(phase=Phase.B.value, stage=None, attempts=0, updated_at=func.now())
                    .returning(Run.queue)
                )
            ).scalar_one_or_none()
            if queue is None:
                raise _Stopped
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.PHASE_COMPLETED,
                data={"phase": Phase.A.value},
            )
        await self._defer(PhaseJob(claim.run_id, claim.org_id, Phase.B, Queue(queue)))
        return Outcome.PHASE_COMPLETED

    async def _succeed(self, claim: _Claim) -> Outcome:
        async with self._db.system_transaction(claim.org_id) as session:
            moved = await run_store.transition(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                target=RunStatus.SUCCEEDED,
                sources={RunStatus.RUNNING},
                values={"finished_at": func.now(), "stage": None},
            )
            if moved is None:
                raise _Stopped
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.PHASE_COMPLETED,
                data={"phase": Phase.B.value},
            )
            await run_store.append_event(
                session, org_id=claim.org_id, run_id=claim.run_id, type_=EventType.RUN_SUCCEEDED
            )
        return Outcome.SUCCEEDED

    # ================================================================== failure

    async def _retry_or_fail(self, claim: _Claim, stage: str, exc: Exception) -> Outcome:
        if claim.attempts >= self._settings.run_max_attempts:
            failure = (
                Failure(code=exc.code, detail=exc.detail, hint=exc.hint or "")
                if isinstance(exc, DomainError)
                else internal_failure(stage)
            )
            return await self._fail(claim, stage, failure)
        delay = self._settings.run_retry_delay_seconds * claim.attempts
        async with self._db.system_transaction(claim.org_id) as session:
            await _stage_back_to_pending(session, claim.run_id, stage)
            await run_store.append_event(
                session,
                org_id=claim.org_id,
                run_id=claim.run_id,
                type_=EventType.STAGE_RETRYING,
                stage=stage,
                data={
                    "attempt": claim.attempts,
                    "max_attempts": self._settings.run_max_attempts,
                    "retry_in_seconds": delay,
                },
            )
        await self._defer(PhaseJob(claim.run_id, claim.org_id, claim.phase, claim.queue), delay)
        return Outcome.RETRYING

    async def record_crash(
        self, org_id: UUID, run_id: UUID, *, reason: str, defer: bool
    ) -> Outcome:
        """Count a crash (out of memory, or a worker that died mid-stage) and decide where the
        run goes next. Shared by in-process ``MemoryError`` handling and the stalled-job reaper
        (which re-queues the stalled job itself, so passes ``defer=False``)."""
        async with self._db.system_transaction(org_id) as session:
            run = await run_store.load_run(session, org_id, run_id, lock=True)
            if run is None or RunStatus(run.status) is not RunStatus.RUNNING:
                return Outcome.STOPPED
            crashes = run.crash_count + 1
            queue = Queue(run.queue)
            if crashes >= self._settings.run_crashes_before_large_queue and queue is Queue.STANDARD:
                queue = Queue.LARGE
                await run_store.append_event(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    type_=EventType.RUN_MOVED_TO_LARGE_QUEUE,
                    data={"crashes": crashes},
                )
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.org_id == org_id)
                .values(crash_count=crashes, queue=queue.value, updated_at=func.now())
            )
            if run.stage:
                await _stage_back_to_pending(session, run_id, run.stage)
            await run_store.append_event(
                session,
                org_id=org_id,
                run_id=run_id,
                type_=EventType.STAGE_RETRYING,
                stage=run.stage,
                data={
                    "reason": reason,
                    "attempt": run.attempts,
                    "max_attempts": self._settings.run_max_attempts,
                },
            )
            phase = Phase(run.phase)
        if defer:
            await self._defer(PhaseJob(run_id, org_id, phase, queue))
        return Outcome.RETRYING

    async def _fail(self, claim: _Claim, stage: str | None, failure: Failure) -> Outcome:
        async with self._db.system_transaction(claim.org_id) as session:
            return await self._fail_in(session, claim.org_id, claim.run_id, stage, failure)

    async def _fail_in(
        self,
        session: AsyncSession,
        org_id: UUID,
        run_id: UUID,
        stage: str | None,
        failure: Failure,
    ) -> Outcome:
        moved = await run_store.transition(
            session,
            org_id=org_id,
            run_id=run_id,
            target=RunStatus.FAILED,
            sources={RunStatus.RUNNING},
            values={
                "failure_code": failure.code,
                "failure_detail": failure.detail,
                "failure_hint": failure.hint or None,
                "finished_at": func.now(),
            },
        )
        if moved is None:
            return Outcome.STOPPED
        if stage:
            await session.execute(
                update(RunStage)
                .where(RunStage.run_id == run_id, RunStage.name == stage)
                .values(status=StageStatus.FAILED.value, finished_at=func.now())
            )
            await run_store.append_event(
                session,
                org_id=org_id,
                run_id=run_id,
                type_=EventType.STAGE_FAILED,
                stage=stage,
                data={"code": failure.code},
            )
        await run_store.append_event(
            session,
            org_id=org_id,
            run_id=run_id,
            type_=EventType.RUN_FAILED,
            stage=stage,
            data={"code": failure.code, "detail": failure.detail, "hint": failure.hint},
        )
        logger.info("run failed", extra={"run_id": str(run_id), "code": failure.code})
        return Outcome.FAILED

    # ================================================================== helpers

    async def _defer(self, job: PhaseJob, delay: int = 0) -> None:
        try:
            await self._queue.defer_phase(job, delay_seconds=delay)
        except Exception as exc:
            # The run is committed; the reconciler re-defers runs that have no job.
            logger.warning(
                "could not defer phase job; the reconciler will retry",
                extra={"run_id": str(job.run_id), **exception_summary(exc)},
            )

    async def _queue_of(self, run_id: UUID, org_id: UUID) -> Queue:
        async with self._db.system_transaction(org_id) as session:
            run = await run_store.load_run(session, org_id, run_id)
            return Queue(run.queue) if run is not None else Queue.STANDARD

    def _workdir(self, run_id: UUID) -> _PathTempDir:
        scratch = self._settings.worker_scratch_dir
        if scratch is not None:
            scratch.mkdir(parents=True, exist_ok=True)
        return _PathTempDir(prefix=f"run-{run_id}-", dir=scratch)

    @staticmethod
    def _log_extra(claim: _Claim, stage: str) -> dict[str, Any]:
        return {"run_id": str(claim.run_id), "stage": stage, "attempt": claim.attempts}


class _PathTempDir(tempfile.TemporaryDirectory[str]):
    """``TemporaryDirectory`` whose context value is a ``Path``."""

    def __enter__(self) -> Path:  # type: ignore[override]
        return Path(super().__enter__())


async def _stage_back_to_pending(session: AsyncSession, run_id: UUID, name: str) -> None:
    await session.execute(
        update(RunStage)
        .where(RunStage.run_id == run_id, RunStage.name == name)
        .values(status=StageStatus.PENDING.value, finished_at=None)
    )


def _result_from_row(row: RunStage) -> StageResult:
    output = dict(row.output)
    return StageResult(
        output=output,
        artifacts={str(k): str(v) for k, v in row.artifacts.items()},
        needs_review=bool(output.get(REVIEW_FLAG, False)),
    )


def event_summary(output: Mapping[str, Any]) -> dict[str, Any]:
    """The scalar facts of a stage's output, for its ``stage.completed`` event. Lists become
    counts and long strings are left out, so an event stays small."""
    summary: dict[str, Any] = {}
    for key, value in output.items():
        if isinstance(value, bool | int | float) or (isinstance(value, str) and len(value) <= 64):
            summary[key] = value
        elif isinstance(value, list | tuple):
            summary[f"{key}_count"] = len(value)
    return summary
