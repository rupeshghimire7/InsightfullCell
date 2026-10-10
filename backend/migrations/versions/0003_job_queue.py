"""Job queue: the Procrastinate 3.10.0 schema in its own ``procrastinate`` schema (IC-007).

The SQL is Procrastinate's own ``schema.sql``, vendored byte for byte as
``migrations/sql/procrastinate-3.10.0.sql`` (Procrastinate's documented approach when another
tool owns migrations). Its digest is checked here, so the file cannot drift after this migration
has been applied anywhere. Upgrading Procrastinate means a *new* migration that applies its
``sql/migrations/*.sql`` files for the versions in between (ADR-030).

The queue is not tenant data: jobs carry only a run id and an organisation id, never file
contents, so the schema has no row-level security. Procrastinate's SQL uses unqualified names,
so every connection that uses the queue sets ``search_path`` to ``procrastinate``
(``queue/procrastinate_queue.py``), and every function in the schema has its own pinned path.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "sql" / "procrastinate-3.10.0.sql"
SCHEMA_SHA256 = "c70ec4b400a60ad9592787653aae5ae77ae41bf801d56b5bd2712751a07f2009"


def _schema_sql() -> str:
    raw = SCHEMA_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
        raise RuntimeError(f"{SCHEMA_FILE.name} was modified; add a new migration instead.")
    return raw.decode("utf-8")


def upgrade() -> None:
    op.execute("CREATE SCHEMA procrastinate")
    # SET LOCAL: the vendored SQL creates its objects in the first schema on the path, and the
    # setting ends with this migration's transaction.
    op.execute("SET LOCAL search_path TO procrastinate")
    op.execute(_schema_sql())
    op.execute("SET LOCAL search_path TO DEFAULT")
    # Procrastinate's functions and triggers name its tables without a schema, so they would
    # resolve against the *caller's* search_path. Pin it on every function: triggers then work
    # from any connection (an operator's DELETE, maintenance), and nothing earlier on a caller's
    # path can shadow a queue table.
    op.execute(
        """
        DO $$
        DECLARE fn regprocedure;
        BEGIN
            FOR fn IN
                SELECT p.oid::regprocedure FROM pg_proc p
                  JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'procrastinate'
            LOOP
                EXECUTE format('ALTER FUNCTION %s SET search_path = procrastinate, pg_catalog', fn);
            END LOOP;
        END
        $$
        """
    )

    # The API defers jobs and the worker runs them, both as the application role.
    op.execute("GRANT USAGE ON SCHEMA procrastinate TO ic_app, ic_readonly, ic_maintenance")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA procrastinate "
        "TO ic_app, ic_maintenance"
    )
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA procrastinate TO ic_readonly")
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA procrastinate TO ic_app, ic_maintenance"
    )
    op.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA procrastinate TO ic_app, ic_maintenance")


def downgrade() -> None:
    op.execute("DROP SCHEMA procrastinate CASCADE")
