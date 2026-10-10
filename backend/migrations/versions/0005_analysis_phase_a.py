"""Analysis Phase A: run columns, analysis and cleaning plans, LLM accounting (Phase 3).

- ``run_columns``: one row per column of a run (meaning, personal-data flag, profile). Derived
  values belong to the run, not the file.
- ``analysis_plans``: every planner proposal and every resolved plan revision, append-only.
  Model proposals double as the plan cache (keyed by schema fingerprint and prompt version).
- ``cleaning_plans`` + ``cleaning_ops``: immutable plan revisions; who decided and when.
  Changing a decision appends a revision.
- ``dataset_preferences``: a person's choices for a dataset (names, units, target, features
  switched off), carried into its later runs.
- ``llm_calls``: one row per model attempt (no prompt text), append-only, partitioned by
  month like ``run_events``; ``usage_counters``: monthly totals per organisation (budgets).

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "run_columns",
    "analysis_plans",
    "cleaning_plans",
    "cleaning_ops",
    "dataset_preferences",
    "llm_calls",
    "usage_counters",
)
APPEND_ONLY = ("analysis_plans", "cleaning_plans", "cleaning_ops", "llm_calls")
DEFINER_FUNCTIONS = ("app.ensure_llm_call_partitions(integer)",)


def upgrade() -> None:
    # What a run brings besides the file (dataset name, cleaning policy, a person's saved
    # choices, reference date), fixed when the run is created so both phases and every retry
    # see the same inputs, even after a review saves new choices for the dataset.
    op.execute(
        "ALTER TABLE app.runs ADD COLUMN inputs jsonb "
        "CHECK (inputs IS NULL OR jsonb_typeof(inputs) = 'object')"
    )
    op.execute(
        """
        CREATE TABLE app.run_columns (
            org_id         uuid NOT NULL,
            run_id         uuid NOT NULL,
            position       smallint NOT NULL CHECK (position >= 0),
            name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 1000),
            physical_type  text NOT NULL CHECK (length(physical_type) BETWEEN 1 AND 200),
            meaning        text NOT NULL CHECK (meaning IN ('continuous', 'nominal', 'datetime',
                               'currency', 'percentage', 'identifier', 'text', 'geography')),
            confidence     text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
            personal_data  text CHECK (personal_data IN ('email', 'phone', 'person_name',
                               'national_id', 'address', 'date_of_birth', 'ip_address',
                               'payment_card')),
            unit           text CHECK (length(unit) <= 32),
            display_name   text CHECK (length(display_name) <= 200),
            role           text CHECK (role IN ('measure', 'dimension', 'time', 'identifier',
                               'text', 'geography', 'excluded')),
            flags          text[] NOT NULL DEFAULT '{}',
            profile        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(profile) = 'object'),
            PRIMARY KEY (run_id, position),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE app.analysis_plans (
            id                  uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id              uuid NOT NULL,
            run_id              uuid NOT NULL,
            dataset_id          uuid NOT NULL,
            kind                text NOT NULL CHECK (kind IN ('proposal', 'resolved')),
            revision            integer NOT NULL CHECK (revision >= 1),
            source              text NOT NULL CHECK (source IN ('model', 'cache', 'rules', 'person')),
            schema_fingerprint  text NOT NULL CHECK (schema_fingerprint ~ '^[0-9a-f]{64}$'),
            prompt_version      text CHECK (length(prompt_version) <= 100),
            provider            text CHECK (length(provider) <= 50),
            model               text CHECK (length(model) <= 200),
            plan                jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
            plan_hash           text NOT NULL CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
            rejected            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(rejected) = 'array'),
            input_card          jsonb CHECK (jsonb_typeof(input_card) = 'object'),
            fallback_reason     text CHECK (length(fallback_reason) <= 100),
            created_by          uuid REFERENCES app.users (id),
            created_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, kind, revision),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id),
            FOREIGN KEY (org_id, dataset_id) REFERENCES app.datasets (org_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX analysis_plans_cache ON app.analysis_plans "
        "(org_id, schema_fingerprint, prompt_version, created_at DESC) "
        "WHERE kind = 'proposal' AND source = 'model'"
    )

    op.execute(
        """
        CREATE TABLE app.cleaning_plans (
            id          uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id      uuid NOT NULL,
            run_id      uuid NOT NULL,
            revision    integer NOT NULL CHECK (revision >= 1),
            policy      text NOT NULL CHECK (policy IN ('always_review', 'review_lossy', 'auto_safe')),
            status      text NOT NULL CHECK (status IN ('proposed', 'approved')),
            decided_by  uuid REFERENCES app.users (id),
            decided_at  timestamptz,
            plan_hash   text NOT NULL CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, revision),
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id),
            CHECK ((decided_by IS NULL) OR (decided_at IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.cleaning_ops (
            org_id         uuid NOT NULL,
            plan_id        uuid NOT NULL,
            position       integer NOT NULL CHECK (position >= 0),
            op_id          text NOT NULL CHECK (length(op_id) BETWEEN 1 AND 1100),
            kind           text NOT NULL CHECK (length(kind) BETWEEN 1 AND 50),
            column_name    text CHECK (length(column_name) <= 1000),
            params         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object'),
            lossy          boolean NOT NULL,
            default_mode   text NOT NULL CHECK (default_mode IN ('auto', 'review', 'opt_in')),
            decision       text NOT NULL CHECK (decision IN ('accepted', 'rejected', 'pending')),
            reason         text NOT NULL CHECK (length(reason) <= 2000),
            source         text NOT NULL CHECK (source IN ('rules', 'model', 'person')),
            rows_affected  bigint NOT NULL DEFAULT 0 CHECK (rows_affected >= 0),
            values_lost    bigint NOT NULL DEFAULT 0 CHECK (values_lost >= 0),
            preview        jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(preview) = 'array'),
            notice         text CHECK (length(notice) <= 2000),
            PRIMARY KEY (plan_id, position),
            UNIQUE (plan_id, op_id),
            FOREIGN KEY (org_id, plan_id) REFERENCES app.cleaning_plans (org_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE app.dataset_preferences (
            org_id       uuid NOT NULL,
            dataset_id   uuid PRIMARY KEY,
            data         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(data) = 'object'),
            row_version  integer NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            updated_by   uuid NOT NULL REFERENCES app.users (id),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, dataset_id) REFERENCES app.datasets (org_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE app.llm_calls (
            id                   uuid NOT NULL DEFAULT app.uuidv7(),
            org_id               uuid NOT NULL REFERENCES app.organizations (id),
            run_id               uuid,
            purpose              text NOT NULL CHECK (purpose IN ('plan', 'narrate', 'route', 'agent', 'judge')),
            provider             text NOT NULL CHECK (length(provider) BETWEEN 1 AND 50),
            model                text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            prompt_id            text NOT NULL CHECK (length(prompt_id) BETWEEN 1 AND 100),
            prompt_version       text NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 100),
            attempt              smallint NOT NULL CHECK (attempt >= 0),
            outcome              text NOT NULL CHECK (outcome IN ('ok', 'invalid', 'timeout',
                                     'rate_limited', 'provider_error', 'refused', 'blocked')),
            input_tokens         integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens        integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd             numeric(12, 6) CHECK (cost_usd >= 0),
            latency_ms           integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
            provider_request_id  text CHECK (length(provider_request_id) <= 200),
            created_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE TABLE app.llm_calls_default PARTITION OF app.llm_calls DEFAULT")
    op.execute(
        "CREATE INDEX llm_calls_run ON app.llm_calls (org_id, run_id) WHERE run_id IS NOT NULL"
    )
    op.execute("CREATE INDEX llm_calls_org_recent ON app.llm_calls (org_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE app.usage_counters (
            org_id         uuid NOT NULL REFERENCES app.organizations (id),
            month          date NOT NULL CHECK (extract(day FROM month) = 1),
            input_tokens   bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens  bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd       numeric(14, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            calls          integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
            PRIMARY KEY (org_id, month)
        )
        """
    )

    # --- append-only, by trigger (deletes only while moving rows between partitions)
    op.execute(
        """
        CREATE FUNCTION app.reject_append_only_change() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' AND current_setting('app.partition_maintenance', true) = 'on'
                THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION '%.% is append-only', TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END
            $$
        """
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON app.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_change()"
        )

    op.execute(
        """
        CREATE FUNCTION app.ensure_llm_call_partitions(months_ahead integer DEFAULT 2)
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
                PERFORM pg_advisory_xact_lock(hashtext('app.llm_calls partitions'));
                FOR i IN 0..months_ahead LOOP
                    month_start := (first_month + make_interval(months => i))::date;
                    month_end := (month_start + interval '1 month')::date;
                    part := format('llm_calls_y%sm%s',
                                   to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
                    CONTINUE WHEN to_regclass(format('app.%I', part)) IS NOT NULL;
                    EXECUTE format(
                        'CREATE TABLE app.%I (LIKE app.llm_calls INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',
                        part);
                    PERFORM set_config('app.partition_maintenance', 'on', true);
                    EXECUTE format(
                        'WITH moved AS (DELETE FROM app.llm_calls_default '
                        'WHERE created_at >= %L AND created_at < %L RETURNING *) '
                        'INSERT INTO app.%I SELECT * FROM moved',
                        month_start, month_end, part);
                    PERFORM set_config('app.partition_maintenance', 'off', true);
                    EXECUTE format(
                        'ALTER TABLE app.llm_calls ATTACH PARTITION app.%I FOR VALUES FROM (%L) TO (%L)',
                        part, month_start, month_end);
                    created := created + 1;
                END LOOP;
                RETURN created;
            END
            $$
        """
    )
    op.execute("SELECT app.ensure_llm_call_partitions(2)")
    for signature in DEFINER_FUNCTIONS:
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

    # --- privileges
    op.execute(
        "GRANT SELECT, INSERT ON app.analysis_plans, app.cleaning_plans, app.cleaning_ops, "
        "app.llm_calls TO ic_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app.run_columns, app.dataset_preferences TO ic_app"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.usage_counters TO ic_app")
    tables = ", ".join(f"app.{t}" for t in TENANT_TABLES)
    op.execute(f"GRANT SELECT ON {tables} TO ic_readonly, ic_maintenance")


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP TABLE app.usage_counters")
    op.execute("DROP TABLE app.llm_calls")  # drops every partition with it
    op.execute("DROP TABLE app.dataset_preferences")
    op.execute("DROP TABLE app.cleaning_ops")
    op.execute("DROP TABLE app.cleaning_plans")
    op.execute("DROP TABLE app.analysis_plans")
    op.execute("DROP TABLE app.run_columns")
    op.execute("DROP FUNCTION app.reject_append_only_change()")
    op.execute("ALTER TABLE app.runs DROP COLUMN inputs")
