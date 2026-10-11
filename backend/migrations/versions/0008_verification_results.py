"""Narration: the verifier's verdict on every sentence the model proposed.

- ``verification_results``: one row per proposed sentence and attempt (the first draft, the
  repair), with the report revision it belongs to, whether it was accepted, the first check
  that failed and why. It measures the verifier (rejection rate per check and prompt version)
  and lets anyone audit a report's words. Append-only, partitioned by month like
  ``test_log``; a tenant table with forced row-level security.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

DEFINER_FUNCTIONS = ("app.ensure_verification_partitions(integer)",)
CHECKS = (
    "'form', 'citation', 'stray_number', 'direction', 'strength', 'causal', 'condition', 'judge'"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE app.verification_results (
            id               uuid NOT NULL DEFAULT app.uuidv7(),
            org_id           uuid NOT NULL,
            run_id           uuid NOT NULL,
            report_revision  integer NOT NULL CHECK (report_revision >= 1),
            part             text NOT NULL CHECK (length(part) BETWEEN 1 AND 4000),
            position         smallint NOT NULL CHECK (position >= 0),
            attempt          smallint NOT NULL CHECK (attempt IN (1, 2)),
            accepted         boolean NOT NULL,
            "check"          text CHECK ("check" IN ({CHECKS})),
            detail           text CHECK (length(detail) <= 500),
            citations        text[] NOT NULL DEFAULT '{{}}',
            sentence         text NOT NULL CHECK (length(sentence) <= 2000),
            prompt_version   text NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 50),
            created_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id),
            CHECK (accepted = ("check" IS NULL))
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE TABLE app.verification_results_default "
        "PARTITION OF app.verification_results DEFAULT"
    )
    op.execute(
        "CREATE INDEX verification_results_run "
        "ON app.verification_results (org_id, run_id, report_revision)"
    )
    op.execute(
        "CREATE TRIGGER verification_results_append_only "
        "BEFORE UPDATE OR DELETE ON app.verification_results "
        "FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_change()"
    )
    op.execute(
        """
        CREATE FUNCTION app.ensure_verification_partitions(months_ahead integer DEFAULT 2)
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
                PERFORM pg_advisory_xact_lock(hashtext('app.verification_results partitions'));
                FOR i IN 0..months_ahead LOOP
                    month_start := (first_month + make_interval(months => i))::date;
                    month_end := (month_start + interval '1 month')::date;
                    part := format('verification_results_y%sm%s',
                                   to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
                    CONTINUE WHEN to_regclass(format('app.%I', part)) IS NOT NULL;
                    EXECUTE format(
                        'CREATE TABLE app.%I (LIKE app.verification_results '
                        'INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',
                        part);
                    PERFORM set_config('app.partition_maintenance', 'on', true);
                    EXECUTE format(
                        'WITH moved AS (DELETE FROM app.verification_results_default '
                        'WHERE created_at >= %L AND created_at < %L RETURNING *) '
                        'INSERT INTO app.%I SELECT * FROM moved',
                        month_start, month_end, part);
                    PERFORM set_config('app.partition_maintenance', 'off', true);
                    EXECUTE format(
                        'ALTER TABLE app.verification_results ATTACH PARTITION app.%I '
                        'FOR VALUES FROM (%L) TO (%L)',
                        part, month_start, month_end);
                    created := created + 1;
                END LOOP;
                RETURN created;
            END
            $$
        """
    )
    op.execute("SELECT app.ensure_verification_partitions(2)")
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO ic_app, ic_maintenance")

    op.execute("ALTER TABLE app.verification_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.verification_results FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON app.verification_results
            USING (org_id = app.current_org())
            WITH CHECK (org_id = app.current_org())
        """
    )
    op.execute("GRANT SELECT, INSERT ON app.verification_results TO ic_app")
    op.execute("GRANT SELECT ON app.verification_results TO ic_readonly, ic_maintenance")


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP TABLE app.verification_results")  # drops every partition with it
