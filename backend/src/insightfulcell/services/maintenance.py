"""Periodic upkeep run by the worker on the ``maintenance`` queue (IC-007).

Each task finds work across organisations through a ``SECURITY DEFINER`` function that returns
ids only, then does the work inside a transaction scoped to that row's organisation, so row-level
security still applies to every change.

- **Reconciler**: an active run with no live job for a minute (the defer after commit failed,
  or a job was lost) gets its phase job deferred again.
- **Reaper**: a phase job whose worker stopped sending heartbeats is counted as a crash and
  queued again (on the ``large`` queue after repeated crashes); the executor fails the run once
  attempts run out.
- **Review gate**: a reminder event after 24 hours, ``expired`` after 7 days.
- **Housekeeping**: monthly event partitions ahead of time, abandoned uploads (their multipart
  parts are aborted in storage), expired idempotency keys and old finished jobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update

from insightfulcell.config import Settings
from insightfulcell.db.models import Run, Upload
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.runs import EventType, Phase, Queue, RunStatus
from insightfulcell.logging_config import exception_summary
from insightfulcell.queue.port import JobQueue, PhaseJob
from insightfulcell.services import run_store
from insightfulcell.services.run_executor import Outcome, RunExecutor
from insightfulcell.storage.port import ObjectStore

logger = logging.getLogger(__name__)

EVENT_PARTITION_MONTHS_AHEAD = 2


@dataclass(frozen=True, slots=True)
class ReviewSweep:
    reminded: int
    expired: int


class Maintenance:
    def __init__(
        self,
        db: TenantDatabase,
        store: ObjectStore,
        queue: JobQueue,
        executor: RunExecutor,
        settings: Settings,
    ) -> None:
        self._db = db
        self._store = store
        self._queue = queue
        self._executor = executor
        self._settings = settings

    # ------------------------------------------------------------------ runs

    async def reconcile_runs(self) -> int:
        """Re-defer active runs that have had no live job for ``run_reconcile_after_seconds``."""
        async with self._db.unscoped_transaction() as session:
            rows = (
                await session.execute(
                    text("SELECT org_id, run_id FROM app.runs_without_live_job(:idle)"),
                    {"idle": timedelta(seconds=self._settings.run_reconcile_after_seconds)},
                )
            ).all()
        deferred = 0
        for org_id, run_id in rows:
            async with self._db.system_transaction(org_id) as session:
                run = await run_store.load_run(session, org_id, run_id)
                if run is None or not RunStatus(run.status).active:
                    continue
                job = PhaseJob(run_id, org_id, Phase(run.phase), Queue(run.queue))
            try:
                await self._queue.defer_phase(job)
            except Exception as exc:
                logger.warning(
                    "reconciler could not defer a run",
                    extra={"run_id": str(run_id), **exception_summary(exc)},
                )
                continue
            deferred += 1
            logger.info("reconciler re-deferred a run", extra={"run_id": str(run_id)})
        return deferred

    async def reap_stalled_jobs(self) -> int:
        """Phase jobs whose worker went silent: count the crash, then run the job again."""
        stalled = await self._queue.stalled_phase_jobs(
            seconds_since_heartbeat=self._settings.run_stalled_after_seconds
        )
        reaped = 0
        for job in stalled:
            outcome = await self._executor.record_crash(
                job.org_id, job.run_id, reason="worker_stopped", defer=False
            )
            if outcome is Outcome.RETRYING:
                queue = await self._run_queue(job.org_id, job.run_id)
                await self._queue.retry_stalled(job.job_id, queue)
            else:
                # Cancelled, finished or unknown: the job must never run again.
                await self._queue.abandon_stalled(job.job_id)
            reaped += 1
            logger.warning(
                "reaped a stalled job",
                extra={"run_id": str(job.run_id), "job_id": job.job_id, "outcome": outcome},
            )
        return reaped

    async def sweep_reviews(self) -> ReviewSweep:
        async with self._db.unscoped_transaction() as session:
            rows = (
                await session.execute(
                    text("SELECT org_id, run_id, expire FROM app.reviews_due(:remind, :expire)"),
                    {
                        "remind": timedelta(hours=self._settings.run_review_reminder_hours),
                        "expire": timedelta(days=self._settings.run_review_expiry_days),
                    },
                )
            ).all()
        reminded = expired = 0
        for org_id, run_id, expire in rows:
            async with self._db.system_transaction(org_id) as session:
                if expire:
                    moved = await run_store.transition(
                        session,
                        org_id=org_id,
                        run_id=run_id,
                        target=RunStatus.EXPIRED,
                        sources={RunStatus.AWAITING_REVIEW},
                        values={"finished_at": func.now()},
                    )
                    if moved is not None:
                        await run_store.append_event(
                            session, org_id=org_id, run_id=run_id, type_=EventType.RUN_EXPIRED
                        )
                        expired += 1
                    continue
                marked = (
                    await session.execute(
                        update(Run)
                        .where(
                            Run.id == run_id,
                            Run.org_id == org_id,
                            Run.status == RunStatus.AWAITING_REVIEW.value,
                            Run.review_reminded_at.is_(None),
                        )
                        .values(review_reminded_at=func.now())
                        .returning(Run.id)
                    )
                ).scalar_one_or_none()
                if marked is not None:
                    await run_store.append_event(
                        session,
                        org_id=org_id,
                        run_id=run_id,
                        type_=EventType.REVIEW_REMINDER,
                        data={"expires_after_days": self._settings.run_review_expiry_days},
                    )
                    reminded += 1
        return ReviewSweep(reminded=reminded, expired=expired)

    # ------------------------------------------------------------------ housekeeping

    async def ensure_event_partitions(self) -> int:
        """Monthly partitions ahead of time for the partitioned logs (run events, LLM calls,
        the test log, verification results)."""
        created = 0
        async with self._db.unscoped_transaction() as session:
            for function in (
                "ensure_run_event_partitions",
                "ensure_llm_call_partitions",
                "ensure_test_log_partitions",
                "ensure_verification_partitions",
            ):
                created += int(
                    (
                        await session.execute(
                            text(f"SELECT app.{function}(:months)"),
                            {"months": EVENT_PARTITION_MONTHS_AHEAD},
                        )
                    ).scalar_one()
                )
        return created

    async def expire_abandoned_uploads(self) -> int:
        """Pending uploads past their expiry: mark them expired and abort their multipart
        upload, so the parts already sent do not stay in storage forever."""
        async with self._db.unscoped_transaction() as session:
            rows = (
                await session.execute(
                    text("SELECT org_id, upload_id FROM app.abandoned_uploads(:age)"),
                    {"age": timedelta(hours=self._settings.upload_expiry_hours)},
                )
            ).all()
        expired = 0
        for org_id, upload_id in rows:
            async with self._db.system_transaction(org_id) as session:
                target = (
                    await session.execute(
                        update(Upload)
                        .where(
                            Upload.id == upload_id,
                            Upload.org_id == org_id,
                            Upload.status == "pending",
                        )
                        .values(status="expired")
                        .returning(Upload.staging_key, Upload.s3_upload_id)
                    )
                ).one_or_none()
            if target is None:
                continue
            staging_key, s3_upload_id = target
            expired += 1
            if s3_upload_id:
                try:
                    await self._store.abort_multipart(staging_key, s3_upload_id)
                except Exception as exc:
                    logger.warning(
                        "could not abort an abandoned multipart upload",
                        extra={"upload_id": str(upload_id), **exception_summary(exc)},
                    )
        return expired

    async def purge_idempotency_keys(self) -> int:
        async with self._db.unscoped_transaction() as session:
            purged = (
                await session.execute(
                    text("SELECT app.purge_idempotency_keys(:age)"),
                    {"age": timedelta(hours=self._settings.idempotency_key_ttl_hours)},
                )
            ).scalar_one()
        return int(purged)

    # ------------------------------------------------------------------ helpers

    async def _run_queue(self, org_id: UUID, run_id: UUID) -> Queue:
        async with self._db.system_transaction(org_id) as session:
            queue = (
                await session.execute(
                    select(Run.queue).where(Run.id == run_id, Run.org_id == org_id)
                )
            ).scalar_one_or_none()
        return Queue(queue) if queue else Queue.STANDARD
