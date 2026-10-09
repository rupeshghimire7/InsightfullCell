"""Async engine factory. The application connects as a non-owner, NOBYPASSRLS role."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from insightfulcell.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={
            # Every statement is bounded (07-database-design.md section 9), and a request that
            # stalls inside a transaction cannot hold locks for longer than 30 seconds.
            "options": (
                f"-c statement_timeout={settings.db_statement_timeout_ms} "
                "-c idle_in_transaction_session_timeout=30000"
            ),
        },
    )
