"""Tenant-scoped transactions: the single place ``app.org_id`` is set (ADR-016).

Every query that touches tenant data must run inside ``tenant_transaction``. The tenant id is
set with ``set_config(..., true)``: a *bound parameter* (``SET`` cannot take one) that is
*transaction-local*, so it vanishes at commit and can never leak to the next request that
borrows the pooled connection. Rows are then filtered by RLS even if application code forgets
a ``WHERE org_id = ...``; the explicit filters stay in the code for clear plans and indexes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


@asynccontextmanager
async def tenant_transaction(engine: AsyncEngine, org_id: UUID) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to one organisation; commits on success, rolls back on error."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_id)}
        )
        yield session
