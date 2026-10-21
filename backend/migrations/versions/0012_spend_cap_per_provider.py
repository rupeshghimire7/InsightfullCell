"""Model spend tracked and capped per provider (Gemini, OpenAI, ...).

- ``llm_spend_provider_totals``: a running total per provider, kept by a trigger on
  ``llm_spend`` (so every recorded attempt counts, however it was recorded), backfilled from
  the ledger.
- ``llm_spend_holds.provider``: whose cap a reservation counts against.
- ``app.llm_spend_reserve`` now takes the provider and its cap. Either cap may be ``NULL``: no
  limit (the overall cap, or that provider's). Refusals: ``spend_cap`` (overall),
  ``spend_cap_provider`` and ``spend_cap_user``.
- ``app.llm_spend_by_provider()``: spent, calls and held per provider, for the dashboard and the
  cap warnings in the log.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NIL = "'00000000-0000-0000-0000-000000000000'::uuid"

OLD_RESERVE = "app.llm_spend_reserve(uuid, uuid, numeric, numeric, numeric, integer)"
RESERVE = "app.llm_spend_reserve(uuid, uuid, text, numeric, numeric, numeric, numeric, integer)"
BY_PROVIDER = "app.llm_spend_by_provider()"
TRACK = "app.track_provider_spend()"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.llm_spend_provider_totals (
            provider  text PRIMARY KEY CHECK (length(provider) BETWEEN 1 AND 50),
            cost_usd  numeric(14, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            calls     bigint NOT NULL DEFAULT 0 CHECK (calls >= 0)
        )
        """
    )
    op.execute("ALTER TABLE app.llm_spend_provider_totals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.llm_spend_provider_totals FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON app.llm_spend_provider_totals FROM PUBLIC")
    op.execute(
        "ALTER TABLE app.llm_spend_holds ADD COLUMN provider text "
        "CHECK (provider IS NULL OR length(provider) BETWEEN 1 AND 50)"
    )
    op.execute(
        """
        INSERT INTO app.llm_spend_provider_totals (provider, cost_usd, calls)
        SELECT provider, sum(cost_usd), count(*) FROM app.llm_spend GROUP BY provider
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {TRACK} RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            BEGIN
                INSERT INTO app.llm_spend_provider_totals AS t (provider, cost_usd, calls)
                VALUES (NEW.provider, NEW.cost_usd, 1)
                ON CONFLICT (provider) DO UPDATE
                    SET cost_usd = t.cost_usd + EXCLUDED.cost_usd, calls = t.calls + 1;
                RETURN NULL;
            END
            $$
        """
    )
    op.execute(
        "CREATE TRIGGER llm_spend_track_provider AFTER INSERT ON app.llm_spend "
        f"FOR EACH ROW EXECUTE FUNCTION {TRACK}"
    )

    op.execute(f"DROP FUNCTION {OLD_RESERVE}")
    op.execute(
        f"""
        CREATE FUNCTION app.llm_spend_reserve(
                org uuid, run uuid, prov text, amount numeric, cap numeric,
                user_cap numeric, provider_cap numeric, hold_seconds integer)
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
                IF amount IS NULL OR amount < 0 OR cap < 0 OR user_cap < 0 OR provider_cap < 0
                   OR prov IS NULL OR hold_seconds < 1 THEN
                    RAISE EXCEPTION 'invalid spend reservation' USING ERRCODE = 'check_violation';
                END IF;
                -- One reservation at a time, so two calls cannot both fit into the last of it.
                PERFORM pg_advisory_xact_lock(hashtext('app.llm_spend'));
                DELETE FROM app.llm_spend_holds WHERE expires_at <= now();

                IF run IS NOT NULL THEN
                    SELECT r.created_by INTO who FROM app.runs r
                     WHERE r.org_id = org AND r.id = run;
                END IF;

                -- A NULL cap is no limit.
                IF cap IS NOT NULL THEN
                    SELECT coalesce(max(t.cost_usd), 0) INTO spent
                      FROM app.llm_spend_totals t WHERE t.scope = {NIL};
                    SELECT coalesce(sum(h.amount_usd), 0) INTO held FROM app.llm_spend_holds h;
                    IF spent + held + amount > cap THEN
                        RETURN QUERY SELECT NULL::uuid, 'spend_cap'::text;
                        RETURN;
                    END IF;
                END IF;

                IF provider_cap IS NOT NULL THEN
                    SELECT coalesce(max(t.cost_usd), 0) INTO spent
                      FROM app.llm_spend_provider_totals t WHERE t.provider = prov;
                    SELECT coalesce(sum(h.amount_usd), 0) INTO held
                      FROM app.llm_spend_holds h WHERE h.provider = prov;
                    IF spent + held + amount > provider_cap THEN
                        RETURN QUERY SELECT NULL::uuid, 'spend_cap_provider'::text;
                        RETURN;
                    END IF;
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

                INSERT INTO app.llm_spend_holds (user_id, provider, amount_usd, expires_at)
                VALUES (who, prov, amount, now() + make_interval(secs => hold_seconds))
                RETURNING id INTO id_;
                RETURN QUERY SELECT id_, NULL::text;
            END
            $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {BY_PROVIDER}
            RETURNS jsonb
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            SELECT coalesce(jsonb_agg(p ORDER BY p.cost_usd DESC, p.provider), '[]'::jsonb)
              FROM (
                SELECT coalesce(t.provider, h.provider) AS provider,
                       coalesce(t.cost_usd, 0) AS cost_usd,
                       coalesce(t.calls, 0) AS calls,
                       coalesce(h.held_usd, 0) AS held_usd
                  FROM app.llm_spend_provider_totals t
                  FULL JOIN (SELECT provider, sum(amount_usd) AS held_usd
                               FROM app.llm_spend_holds
                              WHERE expires_at > now() AND provider IS NOT NULL
                              GROUP BY provider) h ON h.provider = t.provider
              ) p
            $$
        """
    )
    for signature in (RESERVE, BY_PROVIDER, TRACK):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    for signature in (RESERVE, BY_PROVIDER):
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO ic_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {BY_PROVIDER}")
    op.execute(f"DROP FUNCTION {RESERVE}")
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
    op.execute(f"REVOKE EXECUTE ON FUNCTION {OLD_RESERVE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {OLD_RESERVE} TO ic_app")
    op.execute("DROP TRIGGER llm_spend_track_provider ON app.llm_spend")
    op.execute(f"DROP FUNCTION {TRACK}")
    op.execute("ALTER TABLE app.llm_spend_holds DROP COLUMN provider")
    op.execute("DROP TABLE app.llm_spend_provider_totals")
