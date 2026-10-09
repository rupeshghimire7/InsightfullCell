"""``Idempotency-Key`` handling shared by every mutation that creates something.

A key is claimed inside the same transaction as the work it protects: the first request inserts
an ``in_progress`` row and does the work; a repeat of the same request reads the stored response
instead; a different request with the same key is refused. The stored response holds ids only
(never presigned URLs or other bearer credentials), and the caller rebuilds the full response.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.db.models import IdempotencyKey
from insightfulcell.domain.errors import IdempotencyInProgress, IdempotencyKeyReused
from insightfulcell.domain.security import Principal


def request_hash(fields: Mapping[str, Any]) -> bytes:
    """Canonical hash of what the request asked for, to tell a replay from a different request."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).digest()


async def claim(
    session: AsyncSession, principal: Principal, *, key: str, path: str, digest: bytes
) -> dict[str, Any] | None:
    """Claim ``key`` for this request. Returns the stored response body of the original
    request on a replay, or ``None`` when this request owns the key and must do the work."""
    inserted = (
        await session.execute(
            pg_insert(IdempotencyKey)
            .values(
                org_id=principal.org_id,
                key=key,
                method="POST",
                path=path,
                request_hash=digest,
                status="in_progress",
            )
            .on_conflict_do_nothing(index_elements=["org_id", "key"])
            .returning(IdempotencyKey.key)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return None
    row = (
        await session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.org_id == principal.org_id, IdempotencyKey.key == key
            )
        )
    ).scalar_one()
    if row.request_hash != digest or row.path != path:
        raise IdempotencyKeyReused(
            "This Idempotency-Key was already used for a different request.",
            hint="Use a new key for a new request.",
        )
    if row.status != "completed" or not row.response_body:
        raise IdempotencyInProgress(
            "A request with this Idempotency-Key is still being processed.",
            hint="Retry in a few seconds.",
        )
    return dict(row.response_body)


async def complete(
    session: AsyncSession,
    principal: Principal,
    *,
    key: str,
    status_code: int,
    body: Mapping[str, Any],
) -> None:
    """Record the response for replays. ``body`` must hold ids only."""
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.org_id == principal.org_id, IdempotencyKey.key == key)
        .values(status="completed", response_code=status_code, response_body=dict(body))
    )
