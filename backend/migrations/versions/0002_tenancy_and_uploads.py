"""Tenancy mirrors, datasets, uploads, dataset versions, idempotency keys (IC-004/005/006).

Implements 07-database-design.md sections 4.1, 4.2, 4.8 (idempotency) and 5 (RLS). Deviations
from the document, each deliberate (see ADR-026):
- ``uploads`` gains ``dataset_id`` (add a version to an existing dataset), ``format``,
  ``part_size_bytes`` and ``part_count`` (validated again when the upload completes).
- ``dataset_versions`` gains an ``(org_id, sha256)`` index: identical bytes are deduplicated
  across the organisation, not only within one dataset (FR-INGEST-06).
- ``DEFAULT app.uuidv7()`` replaces the built-in ``uuidv7()`` until PostgreSQL 18 is the floor.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("datasets", "uploads", "dataset_versions", "idempotency_keys")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.organizations (
            id                    uuid PRIMARY KEY,
            name                  text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            plan                  text NOT NULL DEFAULT 'free'
                                  CHECK (plan IN ('free', 'team', 'enterprise')),
            cleaning_policy       text NOT NULL DEFAULT 'review_lossy'
                                  CHECK (cleaning_policy IN ('always_review', 'review_lossy', 'auto_safe')),
            raw_retention_days    integer NOT NULL DEFAULT 90 CHECK (raw_retention_days BETWEEN 1 AND 3650),
            monthly_token_budget  bigint NOT NULL DEFAULT 20000000 CHECK (monthly_token_budget >= 0),
            max_concurrent_runs   integer NOT NULL DEFAULT 3 CHECK (max_concurrent_runs BETWEEN 1 AND 100),
            settings              jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(settings) = 'object'),
            created_at            timestamptz NOT NULL DEFAULT now(),
            deletion_requested_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.users (
            id          uuid PRIMARY KEY,
            settings    jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(settings) = 'object'),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )  # no email or name: data minimisation, the web app owns profile data

    op.execute(
        """
        CREATE TABLE app.datasets (
            id          uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id      uuid NOT NULL REFERENCES app.organizations (id),
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            created_by  uuid NOT NULL REFERENCES app.users (id),
            created_at  timestamptz NOT NULL DEFAULT now(),
            archived_at timestamptz,
            UNIQUE (org_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX datasets_org_recent ON app.datasets (org_id, created_at DESC, id DESC)"
    )

    op.execute(
        """
        CREATE TABLE app.uploads (
            id               uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id           uuid NOT NULL REFERENCES app.organizations (id),
            dataset_id       uuid,
            created_by       uuid NOT NULL REFERENCES app.users (id),
            filename         text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
            format           text NOT NULL CHECK (format IN ('csv', 'tsv', 'xlsx', 'parquet', 'json')),
            declared_bytes   bigint NOT NULL CHECK (declared_bytes BETWEEN 1 AND 262144000),
            part_size_bytes  integer NOT NULL CHECK (part_size_bytes >= 5242880),
            part_count       integer NOT NULL CHECK (part_count BETWEEN 1 AND 10000),
            staging_key      text NOT NULL,
            s3_upload_id     text,
            status           text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'completed', 'aborted', 'expired')),
            created_at       timestamptz NOT NULL DEFAULT now(),
            completed_at     timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, dataset_id) REFERENCES app.datasets (org_id, id),
            CHECK ((status = 'completed') = (completed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX uploads_pending ON app.uploads (org_id, created_at) WHERE status = 'pending'"
    )

    op.execute(
        """
        CREATE TABLE app.dataset_versions (
            id           uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id       uuid NOT NULL,
            dataset_id   uuid NOT NULL,
            upload_id    uuid,
            sha256       bytea NOT NULL CHECK (length(sha256) = 32),
            bytes        bigint NOT NULL CHECK (bytes > 0),
            format       text NOT NULL CHECK (format IN ('csv', 'tsv', 'xlsx', 'parquet', 'json')),
            bronze_key   text NOT NULL,
            created_by   uuid NOT NULL REFERENCES app.users (id),
            created_at   timestamptz NOT NULL DEFAULT now(),
            purged_at    timestamptz,
            FOREIGN KEY (org_id, dataset_id) REFERENCES app.datasets (org_id, id),
            FOREIGN KEY (org_id, upload_id)  REFERENCES app.uploads (org_id, id),
            UNIQUE (dataset_id, sha256),
            UNIQUE (org_id, id)
        )
        """
    )
    op.execute("CREATE INDEX dataset_versions_org_sha ON app.dataset_versions (org_id, sha256)")
    op.execute(
        "CREATE INDEX dataset_versions_dataset ON app.dataset_versions (dataset_id, created_at DESC, id DESC)"
    )

    op.execute(
        """
        CREATE TABLE app.idempotency_keys (
            org_id         uuid NOT NULL REFERENCES app.organizations (id),
            key            text NOT NULL CHECK (length(key) BETWEEN 8 AND 200),
            method         text NOT NULL,
            path           text NOT NULL,
            request_hash   bytea NOT NULL,
            status         text NOT NULL CHECK (status IN ('in_progress', 'completed')),
            response_code  integer,
            response_body  jsonb,
            created_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, key)
        )
        """
    )
    op.execute("CREATE INDEX idempotency_keys_expiry ON app.idempotency_keys (created_at)")

    # --- row-level security: forced, keyed on a transaction-local setting, fails closed.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON app.{table}
                USING (org_id = app.current_org())
                WITH CHECK (org_id = app.current_org())
            """
        )
    op.execute("ALTER TABLE app.organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_self ON app.organizations
            USING (id = app.current_org()) WITH CHECK (id = app.current_org())
        """
    )

    # --- privileges: least privilege per role (07-database-design.md section 3).
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.organizations, app.users TO ic_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.datasets, app.uploads TO ic_app")
    # Dataset versions describe immutable bytes: no UPDATE or DELETE for the application.
    op.execute("GRANT SELECT, INSERT ON app.dataset_versions TO ic_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.idempotency_keys TO ic_app")
    op.execute(
        "GRANT SELECT ON app.organizations, app.users, app.datasets, app.uploads, "
        "app.dataset_versions TO ic_readonly"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app.organizations, app.users, app.datasets, "
        "app.uploads, app.dataset_versions, app.idempotency_keys TO ic_maintenance"
    )


def downgrade() -> None:
    for table in (
        "idempotency_keys",
        "dataset_versions",
        "uploads",
        "datasets",
        "users",
        "organizations",
    ):
        op.execute(f"DROP TABLE app.{table}")
