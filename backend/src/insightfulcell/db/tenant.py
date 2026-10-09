"""``TenantDatabase``: what services use to open a transaction for a verified principal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from insightfulcell.db.identity import IdentityProvisioner
from insightfulcell.db.session import tenant_transaction
from insightfulcell.domain.errors import DatabaseUnavailable
from insightfulcell.domain.security import Principal


class TenantDatabase:
    def __init__(self, engine: AsyncEngine, provisioner: IdentityProvisioner | None = None) -> None:
        self.engine = engine
        self._provisioner = provisioner or IdentityProvisioner()

    @asynccontextmanager
    async def transaction(self, principal: Principal) -> AsyncIterator[AsyncSession]:
        """A transaction in which RLS shows only ``principal``'s organisation, and in which the
        user and organisation mirror rows are guaranteed to exist.

        A lost or refused connection becomes ``DatabaseUnavailable`` (a 503 problem the client
        may retry) instead of an unexpected 500."""
        try:
            async with tenant_transaction(self.engine, principal.org_id) as session:
                await self._provisioner.ensure(session, principal)
                yield session
        except DBAPIError as exc:
            if not is_connection_failure(exc):
                raise
            raise DatabaseUnavailable(
                "The database could not be reached.", hint="Try again in a moment."
            ) from exc


def is_connection_failure(exc: DBAPIError) -> bool:
    """True when the driver could not connect or lost the connection, as opposed to a failing
    statement (constraint violations, timeouts and bugs keep their own handling)."""
    if exc.connection_invalidated:
        return True
    orig = exc.orig
    # psycopg's OperationalError is raised for connect failures and dropped connections; its
    # subclasses with a SQLSTATE (e.g. QueryCanceled for statement_timeout) are statement errors,
    # except class 08 (connection exception) and 57P0x (server shutting down).
    if type(orig) is psycopg.OperationalError:
        return True
    sqlstate = getattr(orig, "sqlstate", None) or ""
    return sqlstate.startswith("08") or sqlstate in {"57P01", "57P02", "57P03"}
