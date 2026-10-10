"""Alembic environment. Migrations run as the owner role, before the new code deploys."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None  # raw-SQL migrations: the DDL is the source of truth (07-database-design.md)


def _url() -> str:
    url = (
        os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL (owner role) to run migrations.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        # Fail fast instead of queueing behind a long transaction and blocking all traffic.
        connection.execute(text("SET lock_timeout = '5s'"))
        # SQLAlchemy 2.0 autobegins on the first execute; commit so Alembic owns the migration
        # transaction (otherwise it never commits). SET (not SET LOCAL) survives the commit.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
