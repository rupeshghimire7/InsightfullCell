"""Procrastinate tasks: thin adapters from queue jobs to the executor and maintenance services.

Task names are stored in queued jobs, so they never change. Periodic tasks run on the
``maintenance`` queue; Procrastinate defers each tick once even with several workers running.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

import procrastinate

from insightfulcell.domain.errors import DatabaseUnavailable, StorageUnavailable
from insightfulcell.domain.runs import Phase, Queue
from insightfulcell.queue.procrastinate_queue import PHASE_TASK
from insightfulcell.services.maintenance import Maintenance
from insightfulcell.services.run_executor import RunExecutor

logger = logging.getLogger(__name__)

MAINTENANCE: Final = Queue.MAINTENANCE.value
OLD_JOB_HOURS: Final = 72

#: The executor handles failures inside a run itself; this only covers the database or storage
#: being unreachable before a run could even be claimed.
_INFRA_RETRY = procrastinate.RetryStrategy(
    max_attempts=3, wait=10, retry_exceptions={DatabaseUnavailable, StorageUnavailable}
)


def register_tasks(app: procrastinate.App, executor: RunExecutor, maintenance: Maintenance) -> None:
    @app.task(name=PHASE_TASK, retry=_INFRA_RETRY)
    async def run_phase(run_id: str, org_id: str, phase: str) -> str:
        outcome = await executor.execute(UUID(run_id), UUID(org_id), Phase(phase))
        logger.info("phase job finished", extra={"run_id": run_id, "outcome": outcome.value})
        return outcome.value

    @app.periodic(cron="* * * * *", periodic_id="reconcile")
    @app.task(
        name="insightfulcell.reconcile_runs", queue=MAINTENANCE, queueing_lock="reconcile_runs"
    )
    async def reconcile_runs(timestamp: int) -> int:
        return await maintenance.reconcile_runs()

    @app.periodic(cron="* * * * *", periodic_id="reap")
    @app.task(
        name="insightfulcell.reap_stalled_jobs", queue=MAINTENANCE, queueing_lock="reap_stalled"
    )
    async def reap_stalled_jobs(timestamp: int) -> int:
        return await maintenance.reap_stalled_jobs()

    @app.periodic(cron="*/15 * * * *", periodic_id="reviews")
    @app.task(name="insightfulcell.sweep_reviews", queue=MAINTENANCE, queueing_lock="reviews")
    async def sweep_reviews(timestamp: int) -> dict[str, int]:
        sweep = await maintenance.sweep_reviews()
        return {"reminded": sweep.reminded, "expired": sweep.expired}

    @app.periodic(cron="17 3 * * *", periodic_id="partitions")
    @app.task(
        name="insightfulcell.ensure_event_partitions",
        queue=MAINTENANCE,
        queueing_lock="partitions",
    )
    async def ensure_event_partitions(timestamp: int) -> int:
        return await maintenance.ensure_event_partitions()

    @app.periodic(cron="7 * * * *", periodic_id="uploads")
    @app.task(
        name="insightfulcell.expire_abandoned_uploads",
        queue=MAINTENANCE,
        queueing_lock="abandoned_uploads",
    )
    async def expire_abandoned_uploads(timestamp: int) -> int:
        return await maintenance.expire_abandoned_uploads()

    @app.periodic(cron="29 * * * *", periodic_id="purges")
    @app.task(name="insightfulcell.purge_deleted_files", queue=MAINTENANCE, queueing_lock="purges")
    async def purge_deleted_files(timestamp: int) -> int:
        return await maintenance.purge_deleted_files()

    @app.periodic(cron="23 4 * * *", periodic_id="idempotency")
    @app.task(
        name="insightfulcell.purge_idempotency_keys",
        queue=MAINTENANCE,
        queueing_lock="idempotency_keys",
    )
    async def purge_idempotency_keys(timestamp: int) -> int:
        return await maintenance.purge_idempotency_keys()

    @app.periodic(cron="41 4 * * *", periodic_id="old_jobs")
    @app.task(name="insightfulcell.delete_old_jobs", queue=MAINTENANCE, queueing_lock="old_jobs")
    async def delete_old_jobs(timestamp: int) -> None:
        await app.job_manager.delete_old_jobs(
            nb_hours=OLD_JOB_HOURS,
            include_failed=True,
            include_cancelled=True,
            include_aborted=True,
        )
