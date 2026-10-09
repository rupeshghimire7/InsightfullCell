"""Procrastinate adapter: PostgreSQL is the queue (ADR-030).

Workers claim jobs with ``FOR UPDATE SKIP LOCKED`` and are woken by ``LISTEN/NOTIFY``. The
Procrastinate schema lives in its own ``procrastinate`` schema, so every connection in this
pool sets ``search_path`` (its SQL uses unqualified names).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import procrastinate
from procrastinate import exceptions as pexc
from procrastinate.jobs import Status

from insightfulcell.config import Settings
from insightfulcell.domain.runs import Phase, Queue
from insightfulcell.queue.port import PhaseJob, StalledJob

logger = logging.getLogger(__name__)

#: Task names are part of the queue's data: renaming one strands jobs already queued.
PHASE_TASK: Final = "insightfulcell.run_phase"
QUEUE_SEARCH_PATH: Final = "-c search_path=procrastinate"


def create_procrastinate_app(settings: Settings, *, max_pool_size: int = 4) -> procrastinate.App:
    connector = procrastinate.PsycopgConnector(
        conninfo=settings.psycopg_conninfo,
        kwargs={"options": QUEUE_SEARCH_PATH},
        # No connections are opened up front: an unreachable database must not hang start-up.
        # Deferring then fails, and the reconciler re-defers the run once the database is back.
        min_size=0,
        max_size=max_pool_size,
        timeout=5.0,
    )
    return procrastinate.App(connector=connector)


class ProcrastinateQueue:
    def __init__(self, app: procrastinate.App) -> None:
        self.app = app

    async def open(self) -> None:
        await self.app.open_async()

    async def close(self) -> None:
        await self.app.close_async()

    async def defer_phase(self, job: PhaseJob, *, delay_seconds: int = 0) -> None:
        options: dict[str, Any] = {
            "lock": str(job.run_id),
            "queueing_lock": str(job.run_id),
            "queue": job.queue.value,
        }
        if delay_seconds > 0:
            options["schedule_in"] = {"seconds": delay_seconds}
        try:
            await self.app.configure_task(PHASE_TASK, **options).defer_async(
                run_id=str(job.run_id), org_id=str(job.org_id), phase=job.phase.value
            )
        except pexc.AlreadyEnqueued:
            logger.info("phase job already queued", extra={"run_id": str(job.run_id)})

    async def stalled_phase_jobs(self, seconds_since_heartbeat: float) -> Sequence[StalledJob]:
        jobs = await self.app.job_manager.get_stalled_jobs(
            task_name=PHASE_TASK, seconds_since_heartbeat=seconds_since_heartbeat
        )
        stalled = []
        for job in jobs:
            try:
                kwargs = job.task_kwargs
                stalled.append(
                    StalledJob(
                        job_id=int(job.id or 0),
                        run_id=UUID(str(kwargs["run_id"])),
                        org_id=UUID(str(kwargs["org_id"])),
                        phase=Phase(str(kwargs["phase"])),
                    )
                )
            except (KeyError, ValueError):
                logger.warning("stalled job with unexpected arguments", extra={"job_id": job.id})
        return stalled

    async def retry_stalled(self, job_id: int, queue: Queue) -> None:
        await self.app.job_manager.retry_job_by_id_async(
            job_id=job_id, retry_at=datetime.now(UTC), queue=queue.value
        )

    async def abandon_stalled(self, job_id: int) -> None:
        await self.app.job_manager.finish_job_by_id_async(
            job_id=job_id, status=Status.FAILED, delete_job=False
        )
