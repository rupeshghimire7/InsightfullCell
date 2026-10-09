"""Run persistence shared by the API service, the worker and maintenance tasks.

Every function takes a session that is already scoped to the run's organisation (row-level
security applies), and also filters by ``org_id`` explicitly for clear query plans.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import Dataset, Run, RunEvent, RunStage
from insightfulcell.domain.errors import RunNotFound
from insightfulcell.domain.runs import EventType, RunStatus
from insightfulcell.services.dto import (
    RunDetail,
    RunEventInfo,
    RunFailure,
    RunSummary,
    StageInfo,
)

#: Events read per query; a stream simply asks again for the next batch.
EVENT_BATCH = 200

_APPEND_EVENT = text(
    """
    WITH bumped AS (
        UPDATE app.runs SET event_seq = event_seq + 1
         WHERE id = :run_id AND org_id = :org_id
        RETURNING org_id, id, event_seq
    )
    INSERT INTO app.run_events (org_id, run_id, seq, type, stage, data)
    SELECT org_id, id, event_seq, :type, :stage, CAST(:data AS jsonb) FROM bumped
    RETURNING seq
    """
)


async def append_event(  # noqa: PLR0913  (keyword-only)
    session: AsyncSession,
    *,
    org_id: UUID,
    run_id: UUID,
    type_: EventType,
    stage: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> int:
    """Append an event with the run's next sequence number; returns that number.

    The sequence comes from ``runs.event_seq`` in the same statement, so it is gap-free per run
    and ordered by commit. An insert trigger sends ``NOTIFY`` (delivered when this transaction
    commits). ``data`` must never hold cell values.
    """
    seq = (
        await session.execute(
            _APPEND_EVENT,
            {
                "run_id": run_id,
                "org_id": org_id,
                "type": type_.value,
                "stage": stage,
                "data": json.dumps(dict(data or {}), default=str),
            },
        )
    ).scalar_one_or_none()
    if seq is None:
        raise RunNotFound("No run with this id exists in your organisation.")
    return int(seq)


async def transition(  # noqa: PLR0913  (keyword-only)
    session: AsyncSession,
    *,
    org_id: UUID,
    run_id: UUID,
    target: RunStatus,
    sources: Iterable[RunStatus],
    values: Mapping[str, Any] | None = None,
) -> Run | None:
    """Compare-and-set: move the run to ``target`` only if its status is one of ``sources``.

    Returns the updated run, or ``None`` when another actor moved it first. Bumps
    ``row_version`` (the ETag for ``If-Match``); progress updates do not.
    """
    stmt = (
        update(Run)
        .where(
            Run.id == run_id,
            Run.org_id == org_id,
            Run.status.in_([source.value for source in sources]),
        )
        .values(
            status=target.value,
            row_version=Run.row_version + 1,
            updated_at=func.now(),
            **dict(values or {}),
        )
        .returning(Run)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_run(
    session: AsyncSession, org_id: UUID, run_id: UUID, *, lock: bool = False
) -> Run | None:
    stmt = select(Run).where(Run.id == run_id, Run.org_id == org_id)
    if lock:
        stmt = stmt.with_for_update()
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def require_run(
    session: AsyncSession, org_id: UUID, run_id: UUID, *, lock: bool = False
) -> Run:
    run = await load_run(session, org_id, run_id, lock=lock)
    if run is None:
        raise RunNotFound("No run with this id exists in your organisation.")
    return run


async def load_stages(session: AsyncSession, org_id: UUID, run_id: UUID) -> list[RunStage]:
    return list(
        (
            await session.execute(
                select(RunStage)
                .where(RunStage.run_id == run_id, RunStage.org_id == org_id)
                .order_by(RunStage.position)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def dataset_names(
    session: AsyncSession, org_id: UUID, dataset_ids: Iterable[UUID]
) -> dict[UUID, str]:
    ids = list(set(dataset_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(Dataset.id, Dataset.name).where(Dataset.org_id == org_id, Dataset.id.in_(ids))
    )
    return {dataset_id: name for dataset_id, name in rows.all()}  # noqa: C416


async def run_detail(session: AsyncSession, org_id: UUID, run_id: UUID) -> RunDetail:
    run = await require_run(session, org_id, run_id)
    names = await dataset_names(session, org_id, [run.dataset_id])
    stages = await load_stages(session, org_id, run_id)
    return RunDetail(
        run=to_summary(run, names.get(run.dataset_id, "")),
        stages=tuple(to_stage_info(stage) for stage in stages),
    )


async def read_events(
    session: AsyncSession, org_id: UUID, run_id: UUID, *, after: int, limit: int = EVENT_BATCH
) -> list[RunEventInfo]:
    rows = (
        await session.execute(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.org_id == org_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
            .limit(limit)
        )
    ).scalars()
    return [
        RunEventInfo(
            seq=row.seq, type=row.type, stage=row.stage, data=row.data, created_at=row.created_at
        )
        for row in rows
    ]


def to_summary(run: Run, dataset_name: str) -> RunSummary:
    failure = (
        RunFailure(code=run.failure_code, detail=run.failure_detail, hint=run.failure_hint)
        if run.failure_code
        else None
    )
    return RunSummary(
        id=run.id,
        dataset_id=run.dataset_id,
        dataset_version_id=run.dataset_version_id,
        dataset_name=dataset_name,
        status=run.status,
        phase=run.phase,
        stage=run.stage,
        queue=run.queue,
        attempts=run.attempts,
        row_version=run.row_version,
        last_event_seq=run.event_seq,
        failure=failure,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        review_requested_at=run.review_requested_at,
    )


def to_stage_info(stage: RunStage) -> StageInfo:
    return StageInfo(
        name=stage.name,
        phase=stage.phase,
        position=stage.position,
        status=stage.status,
        reused=stage.reused,
        attempts=stage.attempts,
        started_at=stage.started_at,
        finished_at=stage.finished_at,
        output=dict(stage.output),
    )
