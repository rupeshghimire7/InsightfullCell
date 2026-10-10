"""Analysis Phase B evidence: the test log, facts with their statistics and search, places.

- ``test_log``: every hypothesis test of a run (the primary tests that enter the run's
  false-discovery control, and the sensitivity re-tests), with effect size, interval, p- and
  q-value, whether it passed and the fact it supports. Append-only, partitioned by month like
  ``run_events`` and ``llm_calls``.
- ``run_facts`` gains its evidence as typed columns (tier, effect, interval, p, q), the
  provenance of its numbers, and a full-text search document over its statement, columns and
  labels (GIN index). Facts stay append-only.
- ``ref.geo_reference_sets``: the catalogue of geographic reference sets (global, not tenant
  data). The engine ships the files; the catalogue pins each edition's digest.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

DEFINER_FUNCTIONS = ("app.ensure_test_log_partitions(integer)",)
TIERS = "'descriptive', 'strong', 'supported', 'inconclusive'"
OUTCOMES = "'holds', 'weakens', 'flips', 'not_retested', 'same_data'"


def upgrade() -> None:
    # --- reference data (global)
    op.execute("CREATE SCHEMA ref")
    op.execute("GRANT USAGE ON SCHEMA ref TO ic_app, ic_readonly, ic_maintenance")
    op.execute(
        """
        CREATE TABLE ref.geo_reference_sets (
            id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9.-]{0,62}$'),
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            edition     text NOT NULL CHECK (length(edition) BETWEEN 1 AND 50),
            source      text NOT NULL CHECK (length(source) BETWEEN 1 AND 500),
            licence     text NOT NULL CHECK (length(licence) BETWEEN 1 AND 100),
            levels      text[] NOT NULL,
            file        text NOT NULL CHECK (length(file) BETWEEN 1 AND 200),
            sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            active      boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO ref.geo_reference_sets (id, name, edition, source, licence, levels, file, sha256)
        VALUES (
            'ne-5.1.2',
            'Countries and states (Natural Earth 1:50m)',
            '5.1.2',
            'Natural Earth admin-0 countries and admin-1 states and provinces, 1:50m, '
            'simplified to about 2 km',
            'Public domain',
            ARRAY['country', 'state'],
            'ne-5.1.2.json.gz',
            '68d70cff6deac106146b66c96b580005ee2989cea620c3c32b9e556d16db053f'
        )
        """
    )
    op.execute("GRANT SELECT ON ref.geo_reference_sets TO ic_app, ic_readonly, ic_maintenance")

    # --- facts: evidence as typed columns, provenance, full-text search
    op.execute(
        f"""
        ALTER TABLE app.run_facts
            ADD COLUMN tier         text NOT NULL DEFAULT 'descriptive' CHECK (tier IN ({TIERS})),
            ADD COLUMN hypothesis   text CHECK (length(hypothesis) <= 4000),
            ADD COLUMN test_method  text CHECK (length(test_method) <= 500),
            ADD COLUMN effect_name  text CHECK (length(effect_name) <= 50),
            ADD COLUMN effect       double precision,
            ADD COLUMN ci_low       double precision,
            ADD COLUMN ci_high      double precision,
            ADD COLUMN p_value      double precision CHECK (p_value BETWEEN 0 AND 1),
            ADD COLUMN q_value      double precision CHECK (q_value BETWEEN 0 AND 1),
            ADD COLUMN provenance   jsonb NOT NULL DEFAULT '{{}}'
                CHECK (jsonb_typeof(provenance) = 'object'),
            ADD CONSTRAINT run_facts_tested_tier
                CHECK ((tier = 'descriptive') = (p_value IS NULL))
        """
    )
    # ``array_to_string`` is only STABLE (it can format any array type); over text[] it is
    # immutable, which a generated column needs.
    op.execute(
        """
        CREATE FUNCTION app.fact_search_document(statement text, columns text[], labels text[])
            RETURNS tsvector
            LANGUAGE sql
            IMMUTABLE PARALLEL SAFE
            SET search_path = pg_catalog
            AS $$
                SELECT setweight(to_tsvector('english'::regconfig, statement), 'A')
                    || setweight(to_tsvector('simple'::regconfig,
                                             array_to_string(columns, ' ')), 'B')
                    || setweight(to_tsvector('simple'::regconfig,
                                             array_to_string(labels, ' ')), 'C')
            $$
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.fact_search_document(text, text[], text[]) "
        "TO ic_app, ic_readonly, ic_maintenance"
    )
    op.execute(
        "ALTER TABLE app.run_facts ADD COLUMN search tsvector "
        "GENERATED ALWAYS AS (app.fact_search_document(statement, columns, labels)) STORED"
    )
    op.execute("CREATE INDEX run_facts_search ON app.run_facts USING gin (search)")

    # --- the test log
    op.execute(
        f"""
        CREATE TABLE app.test_log (
            id           uuid NOT NULL DEFAULT app.uuidv7(),
            org_id       uuid NOT NULL,
            run_id       uuid NOT NULL,
            purpose      text NOT NULL CHECK (purpose IN ('primary', 'sensitivity')),
            hypothesis   text NOT NULL CHECK (length(hypothesis) BETWEEN 1 AND 4000),
            method       text NOT NULL CHECK (length(method) BETWEEN 1 AND 500),
            columns      text[] NOT NULL,
            n            bigint NOT NULL CHECK (n >= 0),
            effect_name  text NOT NULL CHECK (length(effect_name) BETWEEN 1 AND 50),
            effect       double precision,
            ci_low       double precision,
            ci_high      double precision,
            p_value      double precision CHECK (p_value BETWEEN 0 AND 1),
            q_value      double precision CHECK (q_value BETWEEN 0 AND 1),
            alpha        double precision NOT NULL CHECK (alpha > 0 AND alpha < 1),
            passed       boolean,
            outcome      text CHECK (outcome IN ({OUTCOMES})),
            fact_id      text CHECK (fact_id ~ '^F[0-9]{{1,6}}$'),
            created_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id),
            FOREIGN KEY (run_id, fact_id) REFERENCES app.run_facts (run_id, fact_id),
            CHECK ((purpose = 'primary') = (passed IS NOT NULL AND q_value IS NOT NULL)),
            CHECK ((purpose = 'sensitivity') = (outcome IS NOT NULL AND fact_id IS NOT NULL))
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE TABLE app.test_log_default PARTITION OF app.test_log DEFAULT")
    op.execute("CREATE INDEX test_log_run ON app.test_log (org_id, run_id, purpose)")
    op.execute(
        "CREATE TRIGGER test_log_append_only BEFORE UPDATE OR DELETE ON app.test_log "
        "FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_change()"
    )
    op.execute(
        """
        CREATE FUNCTION app.ensure_test_log_partitions(months_ahead integer DEFAULT 2)
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
                PERFORM pg_advisory_xact_lock(hashtext('app.test_log partitions'));
                FOR i IN 0..months_ahead LOOP
                    month_start := (first_month + make_interval(months => i))::date;
                    month_end := (month_start + interval '1 month')::date;
                    part := format('test_log_y%sm%s',
                                   to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
                    CONTINUE WHEN to_regclass(format('app.%I', part)) IS NOT NULL;
                    EXECUTE format(
                        'CREATE TABLE app.%I (LIKE app.test_log INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',
                        part);
                    PERFORM set_config('app.partition_maintenance', 'on', true);
                    EXECUTE format(
                        'WITH moved AS (DELETE FROM app.test_log_default '
                        'WHERE created_at >= %L AND created_at < %L RETURNING *) '
                        'INSERT INTO app.%I SELECT * FROM moved',
                        month_start, month_end, part);
                    PERFORM set_config('app.partition_maintenance', 'off', true);
                    EXECUTE format(
                        'ALTER TABLE app.test_log ATTACH PARTITION app.%I FOR VALUES FROM (%L) TO (%L)',
                        part, month_start, month_end);
                    created := created + 1;
                END LOOP;
                RETURN created;
            END
            $$
        """
    )
    op.execute("SELECT app.ensure_test_log_partitions(2)")
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO ic_app, ic_maintenance")

    op.execute("ALTER TABLE app.test_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.test_log FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON app.test_log
            USING (org_id = app.current_org())
            WITH CHECK (org_id = app.current_org())
        """
    )
    op.execute("GRANT SELECT, INSERT ON app.test_log TO ic_app")
    op.execute("GRANT SELECT ON app.test_log TO ic_readonly, ic_maintenance")


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP TABLE app.test_log")  # drops every partition with it
    op.execute("DROP INDEX app.run_facts_search")
    op.execute(
        """
        ALTER TABLE app.run_facts
            DROP CONSTRAINT run_facts_tested_tier,
            DROP COLUMN search,
            DROP COLUMN provenance,
            DROP COLUMN q_value,
            DROP COLUMN p_value,
            DROP COLUMN ci_high,
            DROP COLUMN ci_low,
            DROP COLUMN effect,
            DROP COLUMN effect_name,
            DROP COLUMN test_method,
            DROP COLUMN hypothesis,
            DROP COLUMN tier
        """
    )
    op.execute("DROP FUNCTION app.fact_search_document(text, text[], text[])")
    op.execute("DROP TABLE ref.geo_reference_sets")
    op.execute("DROP SCHEMA ref")
