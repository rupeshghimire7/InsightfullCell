"""Baseline: roles, schema, tenant helper functions (IC-004).

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Roles are cluster-wide, so creation is idempotent (07-database-design.md section 3).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_migrator') THEN
                CREATE ROLE ic_migrator NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_app') THEN
                CREATE ROLE ic_app NOLOGIN NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_readonly') THEN
                CREATE ROLE ic_readonly NOLOGIN NOBYPASSRLS;
            END IF;
            -- BYPASSRLS is a role attribute and is not inherited through membership, so the
            -- maintenance role is itself the login role. Set its credentials out of band.
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_maintenance') THEN
                CREATE ROLE ic_maintenance LOGIN BYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("GRANT USAGE ON SCHEMA app TO ic_app, ic_readonly, ic_maintenance")

    # UUIDv7 default that works on PostgreSQL 16/17/18. The built-in pg_catalog.uuidv7()
    # arrives in 18; the application also generates ids itself (domain/ids.py).
    op.execute(
        """
        CREATE FUNCTION app.uuidv7() RETURNS uuid
            LANGUAGE sql VOLATILE PARALLEL SAFE
            AS $$
                SELECT encode(
                    set_bit(
                        set_bit(
                            overlay(
                                uuid_send(gen_random_uuid())
                                PLACING substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
                                FROM 1 FOR 6
                            ),
                            52, 1
                        ),
                        53, 1
                    ),
                    'hex'
                )::uuid
            $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.uuidv7() TO ic_app, ic_readonly, ic_maintenance")

    # The current tenant, or NULL when the setting is absent or empty: RLS fails closed.
    op.execute(
        """
        CREATE FUNCTION app.current_org() RETURNS uuid
            LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.current_org() TO ic_app, ic_readonly, ic_maintenance")

    # Lets the API's /readyz confirm migrations are current without owner privileges.
    op.execute("GRANT SELECT ON public.alembic_version TO ic_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.alembic_version FROM ic_app")
    op.execute("DROP FUNCTION app.current_org()")
    op.execute("DROP FUNCTION app.uuidv7()")
    op.execute("DROP SCHEMA app CASCADE")
    # Roles are cluster-wide and may be shared by other databases: deliberately kept.
