"""What the services need from a job queue, independent of Procrastinate.

The ``runs`` table is the source of truth; a job is only a nudge to a worker to move a run
forward. Losing a job is therefore recoverable (the reconciler re-defers it), and deferring the
same run twice is harmless (``queueing_lock`` = run id keeps one waiting job per run, and
``lock`` = run id stops two jobs for one run from running at once).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from insightfulcell.domain.runs import Phase, Queue


@dataclass(frozen=True, slots=True)
class PhaseJob:
    """Run one phase of one run. Carries ids only, never data."""

    run_id: UUID
    org_id: UUID
    phase: Phase
    queue: Queue = Queue.STANDARD


@dataclass(frozen=True, slots=True)
class StalledJob:
    """A phase job whose worker stopped sending heartbeats (crashed, killed, out of memory)."""

    job_id: int
    run_id: UUID
    org_id: UUID
    phase: Phase


class JobQueue(Protocol):
    async def defer_phase(self, job: PhaseJob, *, delay_seconds: int = 0) -> None:
        """Queue a phase job. A no-op when one is already waiting for the same run."""

    async def stalled_phase_jobs(self, seconds_since_heartbeat: float) -> Sequence[StalledJob]:
        """Phase jobs marked as running by a worker that has gone silent."""

    async def retry_stalled(self, job_id: int, queue: Queue) -> None:
        """Put a stalled job back in a queue (possibly a different one) to run again."""

    async def abandon_stalled(self, job_id: int) -> None:
        """Mark a stalled job as failed so it is never picked up again."""
