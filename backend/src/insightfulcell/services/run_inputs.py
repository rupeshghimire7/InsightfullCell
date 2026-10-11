"""A run's inputs besides the file, snapshotted once (when the run is created) onto the run."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import Dataset, DatasetPreferences, Organization
from insightfulcell.engine.subset import Subset
from insightfulcell.pipeline.stage import RunInputs


async def snapshot(
    session: AsyncSession, *, org_id: UUID, dataset_id: UUID, created_at: datetime
) -> dict[str, Any]:
    name = (
        await session.execute(
            select(Dataset.name).where(Dataset.id == dataset_id, Dataset.org_id == org_id)
        )
    ).scalar_one_or_none()
    policy = (
        await session.execute(select(Organization.cleaning_policy).where(Organization.id == org_id))
    ).scalar_one_or_none()
    preferences = (
        await session.execute(
            select(DatasetPreferences.data).where(
                DatasetPreferences.dataset_id == dataset_id, DatasetPreferences.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    return {
        "dataset_id": str(dataset_id),
        "dataset_name": name or "",
        "cleaning_policy": policy or "review_lossy",
        "annotations": preferences or {},
        "today": created_at.date().isoformat(),
    }


def from_snapshot(data: dict[str, Any]) -> RunInputs:
    return RunInputs(
        dataset_id=UUID(data["dataset_id"]) if data.get("dataset_id") else None,
        dataset_name=str(data.get("dataset_name") or ""),
        cleaning_policy=str(data.get("cleaning_policy") or "review_lossy"),
        annotations=dict(data.get("annotations") or {}),
        today=str(data.get("today") or "1970-01-01"),
        cleaning_decisions={
            str(k): bool(v) for k, v in dict(data.get("cleaning_decisions") or {}).items()
        },
        drilldown=Subset.model_validate(data["drilldown"]) if data.get("drilldown") else None,
    )
