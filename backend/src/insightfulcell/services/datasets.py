"""Dataset read use cases with keyset pagination (never OFFSET on growing tables)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_

from insightfulcell.db.models import Dataset, DatasetVersion
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import DatasetNotFound, InvalidCursor
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.services.dto import DatasetDetail, DatasetPage
from insightfulcell.services.queries import (
    load_dataset_infos,
    to_version_info,
)

MAX_PAGE_SIZE = 100
MAX_VERSIONS_SHOWN = 100


def encode_cursor(created_at: datetime, dataset_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(dataset_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, dataset_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(created_at), UUID(dataset_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidCursor(
            "The pagination cursor is not valid.", hint="Start again from the first page."
        ) from exc


class DatasetService:
    def __init__(self, db: TenantDatabase) -> None:
        self._db = db

    async def list_datasets(
        self, principal: Principal, *, limit: int = 25, cursor: str | None = None
    ) -> DatasetPage:
        require(principal, Action.DATASET_READ)
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        stmt = (
            select(Dataset)
            .where(Dataset.org_id == principal.org_id, Dataset.archived_at.is_(None))
            .order_by(Dataset.created_at.desc(), Dataset.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            after_ts, after_id = decode_cursor(cursor)
            stmt = stmt.where(tuple_(Dataset.created_at, Dataset.id) < tuple_(after_ts, after_id))

        async with self._db.transaction(principal) as session:
            rows = list((await session.execute(stmt)).scalars())
            page, overflow = rows[:limit], rows[limit:]
            items = await load_dataset_infos(session, principal, page)

        next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if overflow else None
        return DatasetPage(items=tuple(items), next_cursor=next_cursor)

    async def get_dataset(self, principal: Principal, dataset_id: UUID) -> DatasetDetail:
        require(principal, Action.DATASET_READ)
        async with self._db.transaction(principal) as session:
            dataset = (
                await session.execute(
                    select(Dataset).where(
                        Dataset.id == dataset_id, Dataset.org_id == principal.org_id
                    )
                )
            ).scalar_one_or_none()
            if dataset is None:
                raise DatasetNotFound("No dataset with this id exists in your organisation.")
            (info,) = await load_dataset_infos(session, principal, [dataset])
            versions = (
                await session.execute(
                    select(DatasetVersion)
                    .where(
                        DatasetVersion.org_id == principal.org_id,
                        DatasetVersion.dataset_id == dataset_id,
                    )
                    .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
                    .limit(MAX_VERSIONS_SHOWN)
                )
            ).scalars()
            return DatasetDetail(dataset=info, versions=tuple(to_version_info(v) for v in versions))
