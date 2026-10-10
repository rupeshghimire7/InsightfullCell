"""Runs, stage checkpoints and the run event log (IC-007, IC-008).

- ``runs``: the source of truth for a run's life. Status changes are compare-and-set updates;
  ``row_version`` is the ETag for ``If-Match``; ``event_seq`` hands out per-run event numbers.
- ``run_stages``: one row per planned stage, with the checkpoint key (hash of inputs, config and
  code version) that lets a retry skip work already done.
- ``run_events``: append-only, partitioned by month (retention is dropping a partition). An insert
  trigger sends ``NOTIFY ic_run_events`` with the run id and sequence number only, never data.
- Maintenance functions are ``SECURITY DEFINER`` and return ids only: the worker's clean-up tasks
  find work across organisations without a login that bypasses row-level security, and then do
  the work itself inside a tenant-scoped transaction.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_TABLES = ("runs", "run_stages", "run_events")
STATUSES = "'queued', 'running', 'awaiting_review', 'succeeded', 'failed', 'cancelled', 'expired'"
TERMINAL = "'succeeded', 'failed', 'cancelled', 'expired'"

DEFINER_FUNCTIONS = (
    "app.ensure_run_event_partitions(integer)",
    "app.runs_without_live_job(interval)",
    "app.reviews_due(interval, interval)",
    "app.abandoned_uploads(interval)",
    "app.purge_idempotency_keys(interval)",
)


def upgrade() -> None:
    # A run names a dataset *and* one of its versions; this key lets a foreign key prove the
    # version belongs to that dataset and organisation.
    op.execute(
        "ALTER TABLE app.dataset_versions "
        "ADD CONSTRAINT dataset_versions_org_dataset_id_key UNIQUE (org_id, dataset_id, id)"
    )

    op.execute(
        f"""
        CREATE TABLE app.runs (
            id                   uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id               uuid NOT NULL REFERENCES app.organizations (id),
            dataset_id           uuid NOT NULL,
            dataset_version_id   uuid NOT NULL,
            created_by           uuid NOT NULL REFERENCES app.users (id),
            status               text NOT NULL DEFAULT 'queued' CHECK (status IN ({STATUSES})),
            phase                text NOT NULL DEFAULT 'A' CHECK (phase IN ('A', 'B')),
            stage                text CHECK (length(stage) BETWEEN 1 AND 64),
            queue                text NOT NULL DEFAULT 'standard'
                                 CHECK (queue IN ('standard', 'large')),
            attempts             integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            crash_count          integer NOT NULL DEFAULT 0 CHECK (crash_count >= 0),
            failure_code         text CHECK (length(failure_code) BETWEEN 1 AND 100),
            failure_detail       text CHECK (length(failure_detail) <= 2000),
            failure_hint         text CHECK (length(failure_hint) <= 2000),
            event_seq            bigint NOT NULL DEFAULT 0 CHECK (event_seq >= 0),
            row_version          integer NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            code_version         text NOT NULL CHECK (length(code_version) BETWEEN 1 AND 100),
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            started_at           timestamptz,
            finished_at          timestamptz,
            review_requested_at  timestamptz,
            review_reminded_at   timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, dataset_id) REFERENCES app.datasets (org_id, id),
            FOREIGN KEY (org_id, dataset_id, dataset_version_id)
                REFERENCES app.dataset_versions (org_id, dataset_id, id),
            CHECK ((status IN ({TERMINAL})) = (finished_at IS NOT NULL)),
            CHECK ((status = 'failed') = (failure_code IS NOT NULL)),
            CHECK (status <> 'awaiting_review' OR review_requested_at IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX runs_org_recent ON app.runs (org_id, created_at DESC, id DESC)")
    op.execute(
        "CREATE INDEX runs_dataset_recent "
        "ON app.runs (org_id, dataset_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX runs_active ON app.runs (updated_at) WHERE status IN ('queued', 'running')"
    )
    op.execute("CREATE INDEX runs_org_running ON app.runs (org_id) WHERE status = 'running'")
    op.execute(
        "CREATE INDEX runs_awaiting_review ON app.runs (review_requested_at) "
        "WHERE status = 'awaiting_review'"
    )

    op.execute(
        """
        CREATE TABLE app.run_stages (
            org_id          uuid NOT NULL,
            run_id          uuid NOT NULL,
            name            text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
            phase           text NOT NULL CHECK (phase IN ('A', 'B')),
            position        smallint NOT NULL CHECK (position >= 0),
            status          text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
            checkpoint_key  text CHECK (checkpoint_key ~ '^[0-9a-f]{64}$'),
            code_version    text CHECK (length(code_version) BETWEEN 1 AND 100),
            output          jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(output) = 'object'),
            artifacts       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(artifacts) = 'object'),
            attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            reused          boolean NOT NULL DEFAULT false,
            started_at      timestamptz,
            finished_at     timestamptz,
            PRIMARY KEY (run_id, name),
            UNIQUE (run_id, position),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id),
            CHECK (status <> 'succeeded' OR checkpoint_key IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE app.run_events (
            org_id      uuid NOT NULL,
            run_id      uuid NOT NULL,
            seq         bigint NOT NULL CHECK (seq >= 1),
            type        text NOT NULL CHECK (type ~ '^[a-z_]+\\.[a-z_]+$'),
            stage       text CHECK (length(stage) BETWEEN 1 AND 64),
            data        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(data) = 'object'),
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, seq, created_at),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id)
        ) PARTITION BY RANGE (created_at)
        """
    )
    # Safety net: an insert never fails for want of a partition. Monthly partitions are created
    # ahead of time, and rows that reach this one are moved when their month's partition appears.
    op.execute("CREATE TABLE app.run_events_default PARTITION OF app.run_events DEFAULT")

    op.execute(
        """
        CREATE FUNCTION app.reject_event_change() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' AND current_setting('app.partition_maintenance', true) = 'on'
                THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'app.run_events is append-only'
                    USING ERRCODE = 'insufficient_privilege';
            END
            $$
        """
    )
    op.execute(
        "CREATE TRIGGER run_events_append_only BEFORE UPDATE OR DELETE ON app.run_events "
        "FOR EACH ROW EXECUTE FUNCTION app.reject_event_change()"
    )
    op.execute(
        """
        CREATE FUNCTION app.notify_run_event() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                PERFORM pg_notify(
                    'ic_run_events',
                    json_build_object('run_id', NEW.run_id, 'seq', NEW.seq)::text
                );
                RETURN NULL;
            END
            $$
        """
    )
    op.execute(
        "CREATE TRIGGER run_events_notify AFTER INSERT ON app.run_events "
        "FOR EACH ROW EXECUTE FUNCTION app.notify_run_event()"
    )

    op.execute(
        """
        CREATE FUNCTION app.ensure_run_event_partitions(months_ahead integer DEFAULT 2)
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                first_month date := date_trunc('month', now())::date;
                month_start date;
                month_end   date;
                part        text;
                created     integer := 0;
            BEGIN
                IF months_ahead NOT BETWEEN 0 AND 24 THEN
                    RAISE EXCEPTION 'months_ahead must be between 0 and 24';
                END IF;
                PERFORM pg_advisory_xact_lock(hashtext('app.run_events partitions'));
                FOR i IN 0..months_ahead LOOP
                    month_start := (first_month + make_interval(months => i))::date;
                    month_end := (month_start + interval '1 month')::date;
                    part := format('run_events_y%sm%s',
                                   to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
                    CONTINUE WHEN to_regclass(format('app.%I', part)) IS NOT NULL;
                    EXECUTE format(
                        'CREATE TABLE app.%I (LIKE app.run_events INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',
                        part);
                    PERFORM set_config('app.partition_maintenance', 'on', true);
                    EXECUTE format(
                        'WITH moved AS (DELETE FROM app.run_events_default '
                        'WHERE created_at >= %L AND created_at < %L RETURNING *) '
                        'INSERT INTO app.%I SELECT * FROM moved',
                        month_start, month_end, part);
                    PERFORM set_config('app.partition_maintenance', 'off', true);
                    EXECUTE format(
                        'ALTER TABLE app.run_events ATTACH PARTITION app.%I FOR VALUES FROM (%L) TO (%L)',
                        part, month_start, month_end);
                    created := created + 1;
                END LOOP;
                RETURN created;
            END
            $$
        """
    )
    op.execute("SELECT app.ensure_run_event_partitions(2)")

    op.execute(
        """
        CREATE FUNCTION app.runs_without_live_job(idle interval)
            RETURNS TABLE (org_id uuid, run_id uuid)
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
                SELECT r.org_id, r.id
                  FROM app.runs r
                 WHERE r.status IN ('queued', 'running')
                   AND r.updated_at < now() - idle
                   AND NOT EXISTS (
                       SELECT 1 FROM procrastinate.procrastinate_jobs j
                        WHERE j.lock = r.id::text AND j.status IN ('todo', 'doing'))
                 ORDER BY r.updated_at
                 LIMIT 500
            $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.reviews_due(remind_after interval, expire_after interval)
            RETURNS TABLE (org_id uuid, run_id uuid, expire boolean)
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
                SELECT r.org_id, r.id, r.review_requested_at < now() - expire_after
                  FROM app.runs r
                 WHERE r.status = 'awaiting_review'
                   AND (r.review_requested_at < now() - expire_after
                        OR (r.review_reminded_at IS NULL
                            AND r.review_requested_at < now() - remind_after))
                 ORDER BY r.review_requested_at
                 LIMIT 500
            $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.abandoned_uploads(older_than interval)
            RETURNS TABLE (org_id uuid, upload_id uuid)
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
                SELECT u.org_id, u.id
                  FROM app.uploads u
                 WHERE u.status = 'pending' AND u.created_at < now() - older_than
                 ORDER BY u.created_at
                 LIMIT 500
            $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.purge_idempotency_keys(older_than interval)
            RETURNS integer
            LANGUAGE sql VOLATILE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
                WITH gone AS (
                    DELETE FROM app.idempotency_keys
                     WHERE created_at < now() - older_than
                    RETURNING 1)
                SELECT count(*)::integer FROM gone
            $$
        """
    )
    for signature in DEFINER_FUNCTIONS:
        # Functions are executable by PUBLIC by default; these run with the owner's rights.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO ic_app, ic_maintenance")

    # --- row-level security: forced, keyed on the transaction-local tenant (as in 0002).
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

    # --- privileges. Partitions get no grants: they are reached only through the parent, whose
    # row-level security policy then applies.
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.runs, app.run_stages TO ic_app")
    op.execute("GRANT SELECT, INSERT ON app.run_events TO ic_app")
    op.execute("GRANT SELECT ON app.runs, app.run_stages, app.run_events TO ic_readonly")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.runs, app.run_stages TO ic_maintenance")
    op.execute("GRANT SELECT, INSERT ON app.run_events TO ic_maintenance")


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP TABLE app.run_events")  # drops every partition with it
    op.execute("DROP FUNCTION app.notify_run_event()")
    op.execute("DROP FUNCTION app.reject_event_change()")
    op.execute("DROP TABLE app.run_stages")
    op.execute("DROP TABLE app.runs")
    op.execute(
        "ALTER TABLE app.dataset_versions DROP CONSTRAINT dataset_versions_org_dataset_id_key"
    )
