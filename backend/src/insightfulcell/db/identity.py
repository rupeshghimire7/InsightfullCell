"""Just-in-time provisioning of identity mirrors (IC-005).

Better Auth owns users and organisations in its own schema; the API keeps thin mirror rows so
foreign keys stay inside the ``app`` schema (07-database-design.md section 4.1). Mirrors are
created from *verified* token claims with ``INSERT ... ON CONFLICT DO NOTHING``. A bounded
in-process cache avoids repeating the two inserts on every request; an identity is cached only
after the transaction that inserted it *commits*, because a request that fails later (a 404, say)
rolls the inserts back and the next request must create them again.
"""

from __future__ import annotations

from collections import OrderedDict
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from insightfulcell.domain.security import Principal

_DEFAULT_ORG_NAME = "Workspace"


class IdentityProvisioner:
    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._seen: OrderedDict[tuple[UUID, UUID], None] = OrderedDict()
        self._max = max_entries

    async def ensure(self, session: AsyncSession, principal: Principal) -> None:
        """Create the user and organisation mirror rows if absent. Call inside a tenant
        transaction: the organisation insert is allowed by RLS only for the current tenant."""
        key = (principal.user_id, principal.org_id)
        if key in self._seen:
            self._seen.move_to_end(key)
            return
        await session.execute(
            text("INSERT INTO app.users (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
            {"id": principal.user_id},
        )
        await session.execute(
            text(
                "INSERT INTO app.organizations (id, name) VALUES (:id, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": principal.org_id, "name": principal.org_name or _DEFAULT_ORG_NAME},
        )
        event.listen(
            session.sync_session, "after_commit", lambda _session: self._remember(key), once=True
        )

    def _remember(self, key: tuple[UUID, UUID]) -> None:
        self._seen[key] = None
        if len(self._seen) > self._max:
            self._seen.popitem(last=False)
