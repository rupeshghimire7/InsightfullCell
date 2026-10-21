"""Model spend cap, lifetime usage per person, and the admin overview.

- ``llm_spend``: one row per model attempt with what it cost (exact, or an upper estimate when
  the provider reported no usage), who caused it and under which label. Append-only, and kept
  when runs, datasets or people are deleted, so spending can never disappear from the total.
- ``llm_spend_holds``: reservations of an attempt's worst-case cost, made before the call and
  released when it ends (or when they expire, for a worker that died mid-call).
- ``llm_spend_totals``: running totals (everyone: the nil UUID; and per person), so a check
  reads one row instead of summing the ledger.
- ``user_activity``: files and runs each person ever started, kept by triggers on ``uploads``
  and ``runs``, so deleting a dataset does not erase what was used.
- ``app.llm_spend_reserve`` / ``app.llm_spend_record``: the only way in. The application role
  has no grant on these tables; the reservation runs under one advisory lock, so two calls
  cannot both fit into the last of the cap.
- ``app.admin_overview(days)``: counts and sums across every organisation for the admin
  dashboard (no file names, no row content, no prompts).

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

NIL = "'00000000-0000-0000-0000-000000000000'::uuid"

DEFINER_FUNCTIONS = (
    "app.llm_spend_reserve(uuid, uuid, numeric, numeric, numeric, integer)",
    "app.llm_spend_record(uuid, uuid, uuid, text, text, text, text, text, boolean, integer,"
    " integer, integer, numeric, boolean, integer)",
    "app.admin_overview(integer)",
)
TRIGGER_FUNCTIONS = (
    "app.track_upload_started()",
    "app.track_upload_completed()",
    "app.track_run_started()",
)


def upgrade() -> None:
    _tables()
    _activity_triggers()
    _spend_functions()
    _admin_overview()
    _backfill()
    for signature in (*DEFINER_FUNCTIONS, *TRIGGER_FUNCTIONS):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO ic_app")


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE app.llm_spend (
            id             uuid PRIMARY KEY DEFAULT app.uuidv7(),
            created_at     timestamptz NOT NULL DEFAULT now(),
            label          text NOT NULL CHECK (label ~ '^[a-z0-9._-]{1,40}$'),
            -- No foreign keys: a row outlives the run, the dataset and the person.
            org_id         uuid NOT NULL,
            user_id        uuid,
            run_id         uuid,
            purpose        text NOT NULL CHECK (length(purpose) BETWEEN 1 AND 20),
            provider       text NOT NULL CHECK (length(provider) BETWEEN 1 AND 50),
            model          text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
            outcome        text NOT NULL CHECK (length(outcome) BETWEEN 1 AND 40),
            fallback       boolean NOT NULL DEFAULT false,
            attempt        smallint NOT NULL CHECK (attempt >= 0),
            input_tokens   integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens  integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd       numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),
            estimated      boolean NOT NULL DEFAULT false,
            latency_ms     integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0)
        )
        """
    )
    op.execute("CREATE INDEX llm_spend_recent ON app.llm_spend (created_at DESC)")
    op.execute(
        "CREATE INDEX llm_spend_user ON app.llm_spend (user_id, created_at DESC) "
        "WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE TRIGGER llm_spend_append_only BEFORE UPDATE OR DELETE ON app.llm_spend "
        "FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_change()"
    )
    op.execute(
        """
        CREATE TABLE app.llm_spend_holds (
            id          uuid PRIMARY KEY DEFAULT app.uuidv7(),
            user_id     uuid,
            amount_usd  numeric(12, 6) NOT NULL CHECK (amount_usd >= 0),
            expires_at  timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.llm_spend_totals (
            scope     uuid PRIMARY KEY,
            cost_usd  numeric(14, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            calls     bigint NOT NULL DEFAULT 0 CHECK (calls >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.user_activity (
            user_id            uuid PRIMARY KEY,
            org_id             uuid NOT NULL,
            uploads            integer NOT NULL DEFAULT 0 CHECK (uploads >= 0),
            uploads_completed  integer NOT NULL DEFAULT 0 CHECK (uploads_completed >= 0),
            upload_bytes       bigint NOT NULL DEFAULT 0 CHECK (upload_bytes >= 0),
            runs               integer NOT NULL DEFAULT 0 CHECK (runs >= 0),
            last_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The dashboard counts per person: without these, each person's counts scan the tables.
    op.execute("CREATE INDEX runs_created_by ON app.runs (created_by, created_at)")
    op.execute("CREATE INDEX datasets_created_by ON app.datasets (created_by)")
    # Not for the application role at all: only the functions below (running as the owner,
    # like the other maintenance functions) touch them. Forced row-level security with no
    # policy is a second lock on the same door for every other role.
    for table in ("llm_spend", "llm_spend_holds", "llm_spend_totals", "user_activity"):
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON app.{table} FROM PUBLIC")


def _activity_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION app.track_upload_started() RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            BEGIN
                INSERT INTO app.user_activity AS a (user_id, org_id, uploads, upload_bytes)
                VALUES (NEW.created_by, NEW.org_id, 1, NEW.declared_bytes)
                ON CONFLICT (user_id) DO UPDATE SET
                    uploads = a.uploads + 1,
                    upload_bytes = a.upload_bytes + EXCLUDED.upload_bytes,
                    last_at = now();
                RETURN NULL;
            END
            $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.track_upload_completed() RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            BEGIN
                UPDATE app.user_activity
                   SET uploads_completed = uploads_completed + 1, last_at = now()
                 WHERE user_id = NEW.created_by;
                RETURN NULL;
            END
            $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.track_run_started() RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            BEGIN
                INSERT INTO app.user_activity AS a (user_id, org_id, runs)
                VALUES (NEW.created_by, NEW.org_id, 1)
                ON CONFLICT (user_id) DO UPDATE SET runs = a.runs + 1, last_at = now();
                RETURN NULL;
            END
            $$
        """
    )
    op.execute(
        "CREATE TRIGGER uploads_track_started AFTER INSERT ON app.uploads "
        "FOR EACH ROW EXECUTE FUNCTION app.track_upload_started()"
    )
    op.execute(
        "CREATE TRIGGER uploads_track_completed AFTER UPDATE OF status ON app.uploads "
        "FOR EACH ROW WHEN (NEW.status = 'completed' AND OLD.status IS DISTINCT FROM 'completed') "
        "EXECUTE FUNCTION app.track_upload_completed()"
    )
    op.execute(
        "CREATE TRIGGER runs_track_started AFTER INSERT ON app.runs "
        "FOR EACH ROW EXECUTE FUNCTION app.track_run_started()"
    )


def _spend_functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION app.llm_spend_reserve(
                org uuid, run uuid, amount numeric, cap numeric, user_cap numeric,
                hold_seconds integer)
            RETURNS TABLE (hold_id uuid, refused text)
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                who    uuid;
                spent  numeric;
                held   numeric;
                id_    uuid;
            BEGIN
                IF amount IS NULL OR amount < 0 OR cap IS NULL OR cap < 0 OR user_cap < 0
                   OR hold_seconds < 1 THEN
                    RAISE EXCEPTION 'invalid spend reservation' USING ERRCODE = 'check_violation';
                END IF;
                -- One reservation at a time, so two calls cannot both fit into the last of it.
                PERFORM pg_advisory_xact_lock(hashtext('app.llm_spend'));
                DELETE FROM app.llm_spend_holds WHERE expires_at <= now();

                IF run IS NOT NULL THEN
                    SELECT r.created_by INTO who FROM app.runs r
                     WHERE r.org_id = org AND r.id = run;
                END IF;

                SELECT coalesce(max(t.cost_usd), 0) INTO spent
                  FROM app.llm_spend_totals t WHERE t.scope = {NIL};
                SELECT coalesce(sum(h.amount_usd), 0) INTO held FROM app.llm_spend_holds h;
                IF spent + held + amount > cap THEN
                    RETURN QUERY SELECT NULL::uuid, 'spend_cap'::text;
                    RETURN;
                END IF;

                IF user_cap > 0 AND who IS NOT NULL THEN
                    SELECT coalesce(max(t.cost_usd), 0) INTO spent
                      FROM app.llm_spend_totals t WHERE t.scope = who;
                    SELECT coalesce(sum(h.amount_usd), 0) INTO held
                      FROM app.llm_spend_holds h WHERE h.user_id = who;
                    IF spent + held + amount > user_cap THEN
                        RETURN QUERY SELECT NULL::uuid, 'spend_cap_user'::text;
                        RETURN;
                    END IF;
                END IF;

                INSERT INTO app.llm_spend_holds (user_id, amount_usd, expires_at)
                VALUES (who, amount, now() + make_interval(secs => hold_seconds))
                RETURNING id INTO id_;
                RETURN QUERY SELECT id_, NULL::text;
            END
            $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.llm_spend_record(
                hold uuid, org uuid, run uuid, label text, purpose text, provider text,
                model text, outcome text, fallback boolean, attempt integer,
                input_tokens integer, output_tokens integer, cost numeric, estimated boolean,
                latency_ms integer)
            RETURNS numeric
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                who    uuid;
                total  numeric;
            BEGIN
                IF hold IS NOT NULL THEN
                    DELETE FROM app.llm_spend_holds h WHERE h.id = hold;
                END IF;
                IF run IS NOT NULL THEN
                    SELECT r.created_by INTO who FROM app.runs r
                     WHERE r.org_id = org AND r.id = run;
                END IF;
                INSERT INTO app.llm_spend (label, org_id, user_id, run_id, purpose, provider,
                                           model, outcome, fallback, attempt, input_tokens,
                                           output_tokens, cost_usd, estimated, latency_ms)
                VALUES (label, org, who, run, purpose, provider, model, outcome, fallback,
                        attempt, input_tokens, output_tokens, cost, estimated, latency_ms);
                INSERT INTO app.llm_spend_totals AS t (scope, cost_usd, calls)
                VALUES ({NIL}, cost, 1)
                ON CONFLICT (scope) DO UPDATE
                    SET cost_usd = t.cost_usd + EXCLUDED.cost_usd, calls = t.calls + 1
                RETURNING t.cost_usd INTO total;
                IF who IS NOT NULL THEN
                    INSERT INTO app.llm_spend_totals AS t (scope, cost_usd, calls)
                    VALUES (who, cost, 1)
                    ON CONFLICT (scope) DO UPDATE
                        SET cost_usd = t.cost_usd + EXCLUDED.cost_usd, calls = t.calls + 1;
                END IF;
                RETURN total;
            END
            $$
        """
    )


def _admin_overview() -> None:
    op.execute(
        f"""
        CREATE FUNCTION app.admin_overview(days integer DEFAULT 30)
            RETURNS jsonb
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            WITH since AS (
                SELECT date_trunc('day', now()) - make_interval(days => greatest(days, 1) - 1) AS t
            ),
            people AS (
                SELECT u.id AS user_id, u.created_at AS first_seen,
                       coalesce(a.uploads, 0) AS uploads,
                       coalesce(a.uploads_completed, 0) AS uploads_completed,
                       coalesce(a.upload_bytes, 0) AS upload_bytes,
                       coalesce(a.runs, 0) AS runs_started,
                       a.last_at,
                       (SELECT count(*) FROM app.datasets d
                         WHERE d.created_by = u.id AND d.archived_at IS NULL) AS datasets,
                       (SELECT coalesce(sum(v.bytes), 0) FROM app.dataset_versions v
                         WHERE v.created_by = u.id AND v.purged_at IS NULL) AS stored_bytes,
                       (SELECT count(*) FROM app.runs r
                         WHERE r.created_by = u.id AND r.status = 'succeeded') AS runs_succeeded,
                       (SELECT count(*) FROM app.runs r
                         WHERE r.created_by = u.id AND r.status = 'failed') AS runs_failed,
                       (SELECT count(*) FROM app.runs r
                         WHERE r.created_by = u.id AND r.status IN ('queued', 'running'))
                           AS runs_active,
                       (SELECT max(r.created_at) FROM app.runs r WHERE r.created_by = u.id)
                           AS last_run_at,
                       coalesce(t.cost_usd, 0) AS cost_usd,
                       coalesce(t.calls, 0) AS model_calls,
                       (SELECT count(*) FROM app.llm_spend s
                         WHERE s.user_id = u.id AND s.fallback) AS backup_calls,
                       (SELECT coalesce(sum(s.input_tokens + s.output_tokens), 0)
                          FROM app.llm_spend s WHERE s.user_id = u.id) AS tokens
                  FROM app.users u
                  LEFT JOIN app.user_activity a ON a.user_id = u.id
                  LEFT JOIN app.llm_spend_totals t ON t.scope = u.id
            )
            SELECT jsonb_build_object(
                'generated_at', now(),
                'days', greatest(days, 1),
                'spend', jsonb_build_object(
                    'total_usd', (SELECT coalesce(max(cost_usd), 0) FROM app.llm_spend_totals
                                   WHERE scope = {NIL}),
                    'held_usd', (SELECT coalesce(sum(amount_usd), 0) FROM app.llm_spend_holds
                                  WHERE expires_at > now()),
                    'estimated_usd', (SELECT coalesce(sum(cost_usd), 0) FROM app.llm_spend
                                       WHERE estimated),
                    'calls', (SELECT count(*) FROM app.llm_spend),
                    'backup_calls', (SELECT count(*) FROM app.llm_spend WHERE fallback),
                    'input_tokens', (SELECT coalesce(sum(input_tokens), 0) FROM app.llm_spend),
                    'output_tokens', (SELECT coalesce(sum(output_tokens), 0) FROM app.llm_spend),
                    'by_day', (SELECT coalesce(jsonb_agg(d ORDER BY d.day), '[]'::jsonb) FROM (
                        SELECT date_trunc('day', s.created_at)::date AS day,
                               sum(s.cost_usd) AS cost_usd, count(*) AS calls,
                               count(*) FILTER (WHERE s.fallback) AS backup_calls,
                               sum(s.input_tokens + s.output_tokens) AS tokens
                          FROM app.llm_spend s, since
                         WHERE s.created_at >= since.t
                         GROUP BY 1) d),
                    'by_model', (SELECT coalesce(jsonb_agg(m ORDER BY m.cost_usd DESC), '[]'::jsonb)
                                   FROM (
                        SELECT s.provider, s.model, count(*) AS calls,
                               count(*) FILTER (WHERE s.outcome = 'ok') AS ok_calls,
                               count(*) FILTER (WHERE s.fallback) AS backup_calls,
                               sum(s.input_tokens) AS input_tokens,
                               sum(s.output_tokens) AS output_tokens,
                               sum(s.cost_usd) AS cost_usd,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms)
                                   FILTER (WHERE s.outcome = 'ok') AS latency_p50_ms,
                               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms)
                                   FILTER (WHERE s.outcome = 'ok') AS latency_p95_ms
                          FROM app.llm_spend s GROUP BY 1, 2) m),
                    'by_outcome', (SELECT coalesce(jsonb_agg(o ORDER BY o.calls DESC), '[]'::jsonb)
                                     FROM (
                        SELECT s.provider, s.outcome, count(*) AS calls
                          FROM app.llm_spend s GROUP BY 1, 2) o),
                    'period_by_provider', (SELECT coalesce(jsonb_object_agg(x.provider, x.cost_usd),
                                                           '{{}}'::jsonb) FROM (
                        SELECT s.provider, sum(s.cost_usd) AS cost_usd
                          FROM app.llm_spend s, since
                         WHERE s.created_at >= since.t
                         GROUP BY 1) x),
                    'by_purpose', (SELECT coalesce(jsonb_agg(p ORDER BY p.cost_usd DESC), '[]'::jsonb)
                                     FROM (
                        SELECT s.purpose, count(*) AS calls, sum(s.cost_usd) AS cost_usd
                          FROM app.llm_spend s GROUP BY 1) p),
                    'by_label', (SELECT coalesce(jsonb_agg(l ORDER BY l.first_at), '[]'::jsonb)
                                   FROM (
                        SELECT s.label, count(*) AS calls, sum(s.cost_usd) AS cost_usd,
                               min(s.created_at) AS first_at, max(s.created_at) AS last_at
                          FROM app.llm_spend s GROUP BY 1) l)
                ),
                'users', (SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.first_seen), '[]'::jsonb)
                            FROM people p),
                'runs', jsonb_build_object(
                    'by_status', (SELECT coalesce(jsonb_object_agg(s.status, s.n), '{{}}'::jsonb)
                                    FROM (SELECT status, count(*) AS n FROM app.runs
                                           GROUP BY 1) s),
                    'by_day', (SELECT coalesce(jsonb_agg(d ORDER BY d.day), '[]'::jsonb) FROM (
                        SELECT date_trunc('day', r.created_at)::date AS day, count(*) AS runs,
                               count(*) FILTER (WHERE r.status = 'succeeded') AS succeeded,
                               count(*) FILTER (WHERE r.status = 'failed') AS failed
                          FROM app.runs r, since
                         WHERE r.created_at >= since.t
                         GROUP BY 1) d),
                    'duration_p50_seconds', (
                        SELECT percentile_cont(0.5) WITHIN GROUP (
                                   ORDER BY extract(epoch FROM r.finished_at - r.started_at))
                          FROM app.runs r
                         WHERE r.status = 'succeeded' AND r.started_at IS NOT NULL),
                    'duration_p95_seconds', (
                        SELECT percentile_cont(0.95) WITHIN GROUP (
                                   ORDER BY extract(epoch FROM r.finished_at - r.started_at))
                          FROM app.runs r
                         WHERE r.status = 'succeeded' AND r.started_at IS NOT NULL),
                    'failures', (SELECT coalesce(jsonb_agg(f ORDER BY f.runs DESC), '[]'::jsonb)
                                   FROM (
                        SELECT r.failure_code AS code, count(*) AS runs FROM app.runs r
                         WHERE r.status = 'failed' AND r.failure_code IS NOT NULL
                         GROUP BY 1 ORDER BY 2 DESC LIMIT 10) f)
                ),
                'storage', jsonb_build_object(
                    'datasets', (SELECT count(*) FROM app.datasets WHERE archived_at IS NULL),
                    'stored_bytes', (SELECT coalesce(sum(bytes), 0) FROM app.dataset_versions
                                      WHERE purged_at IS NULL),
                    'uploads_by_status', (SELECT coalesce(jsonb_object_agg(s.status, s.n),
                                                          '{{}}'::jsonb)
                                            FROM (SELECT status, count(*) AS n FROM app.uploads
                                                   GROUP BY 1) s),
                    'purges_pending', (SELECT count(*) FROM app.storage_purges)
                ),
                'queue', (SELECT coalesce(jsonb_object_agg(j.status, j.n), '{{}}'::jsonb)
                            FROM (SELECT status::text AS status, count(*) AS n
                                    FROM procrastinate.procrastinate_jobs
                                   WHERE status IN ('todo', 'doing')
                                   GROUP BY 1) j),
                'organizations', (SELECT count(*) FROM app.organizations)
            )
            $$
        """
    )


def _backfill() -> None:
    # What was spent before this migration, so the cap counts it too.
    op.execute(
        """
        INSERT INTO app.llm_spend (created_at, label, org_id, user_id, run_id, purpose, provider,
                                   model, outcome, attempt, input_tokens, output_tokens,
                                   cost_usd, latency_ms)
        SELECT c.created_at, 'before-cap', c.org_id, r.created_by, c.run_id, c.purpose,
               c.provider, c.model, c.outcome, c.attempt, c.input_tokens, c.output_tokens,
               coalesce(c.cost_usd, 0), c.latency_ms
          FROM app.llm_calls c
          LEFT JOIN app.runs r ON r.org_id = c.org_id AND r.id = c.run_id
        """
    )
    op.execute(
        f"""
        INSERT INTO app.llm_spend_totals (scope, cost_usd, calls)
        SELECT {NIL}, coalesce(sum(cost_usd), 0), count(*) FROM app.llm_spend
        UNION ALL
        SELECT user_id, sum(cost_usd), count(*) FROM app.llm_spend
         WHERE user_id IS NOT NULL GROUP BY user_id
        """
    )
    op.execute(
        """
        INSERT INTO app.user_activity (user_id, org_id, uploads, uploads_completed, upload_bytes,
                                       runs, last_at)
        SELECT u.created_by, min(u.org_id::text)::uuid, count(*),
               count(*) FILTER (WHERE u.status = 'completed'), sum(u.declared_bytes), 0,
               max(u.created_at)
          FROM app.uploads u GROUP BY u.created_by
        """
    )
    op.execute(
        """
        INSERT INTO app.user_activity AS a (user_id, org_id, runs, last_at)
        SELECT r.created_by, min(r.org_id::text)::uuid, count(*), max(r.created_at)
          FROM app.runs r GROUP BY r.created_by
        ON CONFLICT (user_id) DO UPDATE
            SET runs = EXCLUDED.runs, last_at = greatest(a.last_at, EXCLUDED.last_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER runs_track_started ON app.runs")
    op.execute("DROP TRIGGER uploads_track_completed ON app.uploads")
    op.execute("DROP TRIGGER uploads_track_started ON app.uploads")
    for signature in (*DEFINER_FUNCTIONS, *TRIGGER_FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
    for table in ("user_activity", "llm_spend_totals", "llm_spend_holds", "llm_spend"):
        op.execute(f"DROP TABLE app.{table}")
    op.execute("DROP INDEX app.datasets_created_by")
    op.execute("DROP INDEX app.runs_created_by")
