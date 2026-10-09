"""The run state machine (IC-007): where a run is in its life, kept apart from what it is doing.

*Status* says where a run is in its life; *stage* says what it is doing now. Every status change
is a compare-and-set update in the database (``UPDATE ... WHERE status = :from``), so two actors
(a worker and a person pressing Cancel) can never both move the same run. This module holds the
rules those updates follow; it knows nothing about SQL.

    queued ──worker claims──▶ running ──Phase B done──▶ succeeded
      │                        │  │
      │                        │  └──retries exhausted──▶ failed ──you retry──▶ queued
      │                        └──lossy change──▶ awaiting_review ──you approve──▶ queued
      │                                              │      └──7 days──▶ expired
      └──────────────── you cancel (queued, running, awaiting_review) ──▶ cancelled
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from insightfulcell.domain.errors import RunNotInState


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATUSES

    @property
    def active(self) -> bool:
        """Queued or running: a job should exist for the run."""
        return self in ACTIVE_STATUSES


TERMINAL_STATUSES: Final = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.EXPIRED}
)
ACTIVE_STATUSES: Final = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})


class Phase(StrEnum):
    """A run executes as two jobs, split at the one point where a person may need to decide."""

    A = "A"  # ingest -> ... -> clean_plan; ends at the review gate
    B = "B"  # clean_apply -> ... -> assemble; ends with the report


class Queue(StrEnum):
    STANDARD = "standard"
    LARGE = "large"  # after repeated out-of-memory failures
    MAINTENANCE = "maintenance"  # reconciler, reaper, expiry, clean-up


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EventType(StrEnum):
    """What the browser is told. Payloads never carry cell values."""

    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_WAITING = "run.waiting"  # the organisation is at its concurrent-run limit
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_REUSED = "stage.reused"  # a checkpoint matched: the stage did not run again
    STAGE_FAILED = "stage.failed"
    STAGE_RETRYING = "stage.retrying"
    RUN_MOVED_TO_LARGE_QUEUE = "run.moved_to_large_queue"
    PHASE_COMPLETED = "phase.completed"
    REVIEW_REQUIRED = "review.required"
    REVIEW_REMINDER = "review.reminder"
    REVIEW_APPROVED = "review.approved"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_EXPIRED = "run.expired"
    RUN_RETRIED = "run.retried"


TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_REVIEW,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.AWAITING_REVIEW: frozenset(
        {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.EXPIRED}
    ),
    RunStatus.FAILED: frozenset({RunStatus.QUEUED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.EXPIRED: frozenset(),
}

CANCELLABLE: Final = frozenset(
    source for source, targets in TRANSITIONS.items() if RunStatus.CANCELLED in targets
)


def can_transition(source: RunStatus, target: RunStatus) -> bool:
    return target in TRANSITIONS[source]


def sources_for(target: RunStatus) -> frozenset[RunStatus]:
    """Every status a run may move to ``target`` from (the ``WHERE status IN`` of a CAS)."""
    return frozenset(source for source, targets in TRANSITIONS.items() if target in targets)


def require_transition(source: RunStatus, target: RunStatus) -> None:
    if not can_transition(source, target):
        raise RunNotInState(
            f"A {_label(source)} run cannot become {_label(target)}.",
            hint="Reload the page to see the run's current state.",
        )


def require_status(current: RunStatus, allowed: Iterable[RunStatus], action: str) -> None:
    allowed_set = frozenset(allowed)
    if current not in allowed_set:
        raise RunNotInState(
            f"This run is {_label(current)}, so it cannot be {action}.",
            hint="Reload the page to see the run's current state.",
        )


def _label(status: RunStatus) -> str:
    return status.value.replace("_", " ")
