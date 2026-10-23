"""Read-side queries shared by the upload and dataset services."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import Dataset, DatasetVersion, Run
from insightfulcell.domain.errors import DatasetNotFound
from insightfulcell.domain.security import Principal
from insightfulcell.services.dto import DatasetInfo, LatestRun, VersionInfo


def to_version_info(row: DatasetVersion) -> VersionInfo:
    return VersionInfo(
        id=row.id,
        dataset_id=row.dataset_id,
        sha256=row.sha256.hex(),
        bytes=row.bytes,
        format=row.format,
        created_at=row.created_at,
    )


async def load_dataset_info(
    session: AsyncSession, principal: Principal, dataset_id: UUID
) -> DatasetInfo:
    """One dataset with its version count and newest version."""
    dataset = (
        await session.execute(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == principal.org_id)
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise DatasetNotFound("No dataset with this id exists in your workspace.")
    infos = await load_dataset_infos(session, principal, [dataset])
    return infos[0]


async def load_dataset_infos(
    session: AsyncSession, principal: Principal, datasets: list[Dataset]
) -> list[DatasetInfo]:
    """Attach version count, latest version and latest run to a page of datasets in three
    queries (never one per dataset)."""
    if not datasets:
        return []
    ids = [d.id for d in datasets]
    count_rows = await session.execute(
        select(DatasetVersion.dataset_id, func.count())
        .where(DatasetVersion.org_id == principal.org_id, DatasetVersion.dataset_id.in_(ids))
        .group_by(DatasetVersion.dataset_id)
    )
    counts = {dataset_id: count for dataset_id, count in count_rows.all()}  # noqa: C416
    latest = {
        row.dataset_id: row
        for row in (
            await session.execute(
                select(DatasetVersion)
                .where(
                    DatasetVersion.org_id == principal.org_id, DatasetVersion.dataset_id.in_(ids)
                )
                .ext(distinct_on(DatasetVersion.dataset_id))
                .order_by(
                    DatasetVersion.dataset_id,
                    DatasetVersion.created_at.desc(),
                    DatasetVersion.id.desc(),
                )
            )
        ).scalars()
    }
    runs = {
        row.dataset_id: row
        for row in (
            await session.execute(
                select(Run)
                # A dataset's latest analysis is a full one, not an exploration of a subset.
                .where(
                    Run.org_id == principal.org_id,
                    Run.dataset_id.in_(ids),
                    Run.parent_run_id.is_(None),
                )
                .ext(distinct_on(Run.dataset_id))
                .order_by(Run.dataset_id, Run.created_at.desc(), Run.id.desc())
            )
        ).scalars()
    }
    return [
        DatasetInfo(
            id=d.id,
            name=d.name,
            created_at=d.created_at,
            version_count=counts.get(d.id, 0),
            latest_version=to_version_info(latest[d.id]) if d.id in latest else None,
            latest_run=_latest_run(runs[d.id]) if d.id in runs else None,
        )
        for d in datasets
    ]


def _latest_run(run: Run) -> LatestRun:
    return LatestRun(
        id=run.id,
        status=run.status,
        stage=run.stage,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
