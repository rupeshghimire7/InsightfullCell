"""Deleting a whole account's data: "Delete my account" (ADR-043).

- ``app.delete_workspace(member, purge_after)``: ``SECURITY DEFINER``. For the organisation set
  in the transaction (``app.current_org()``), deletes every dataset (``app.delete_dataset``, so
  each with its versions, uploads and analyses), any run or upload left over, the idempotency
  keys, the usage counters and the person's activity row, then the person's user mirror. It
  refuses (``busy``) while an analysis is running. The organisation mirror stays only as a
  nameless tombstone (``deleted_at``) while its files wait to be purged, because the purge rows
  refer to it.
- ``app.forget_deleted_organizations()``: removes tombstones whose files are all gone; the
  worker calls it after each purge sweep.
- The model-spend ledger (``llm_spend``, migration 0011) is kept, as for any delete: it holds
  no personal data (ids, model, tokens, cost), and spending must never leave the cap's total.
  The person's own running total and any open hold are removed.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

DEFINER_FUNCTIONS = (
    "app.delete_workspace(uuid, timestamptz)",
    "app.forget_deleted_organizations()",
)


def upgrade() -> None:
    op.execute("ALTER TABLE app.organizations ADD COLUMN deleted_at timestamptz")

    op.execute(
        """
        CREATE FUNCTION app.delete_workspace(member uuid, purge_after timestamptz)
            RETURNS TABLE (outcome text, run_ids uuid[])
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                org uuid := app.current_org();
                all_runs uuid[];
                left_runs uuid[];
                dataset record;
            BEGIN
                IF org IS NULL THEN
                    RAISE EXCEPTION 'no organisation is set'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                PERFORM 1 FROM app.organizations o WHERE o.id = org FOR UPDATE;
                SELECT coalesce(array_agg(r.id), '{}') INTO all_runs
                  FROM app.runs r WHERE r.org_id = org;
                PERFORM 1 FROM app.runs r WHERE r.org_id = org FOR UPDATE;
                IF EXISTS (SELECT 1 FROM app.runs r WHERE r.org_id = org AND r.status = 'running')
                   OR EXISTS (SELECT 1 FROM procrastinate.procrastinate_jobs j
                               WHERE j.lock = ANY (all_runs::text[]) AND j.status = 'doing')
                THEN
                    RETURN QUERY SELECT 'busy'::text, all_runs;
                    RETURN;
                END IF;

                FOR dataset IN SELECT d.id FROM app.datasets d WHERE d.org_id = org LOOP
                    PERFORM app.delete_dataset(dataset.id, delete_workspace.purge_after);
                END LOOP;

                -- Nothing should be left, but nothing may be either.
                SELECT coalesce(array_agg(r.id), '{}') INTO left_runs
                  FROM app.runs r WHERE r.org_id = org;
                IF cardinality(left_runs) > 0 THEN
                    INSERT INTO app.storage_purges (org_id, key, is_prefix, purge_after)
                    SELECT org, 'gold/' || org || '/' || t.id || '/', true,
                           delete_workspace.purge_after
                      FROM unnest(left_runs) AS t (id)
                        ON CONFLICT (org_id, key) DO NOTHING;
                    PERFORM app.erase_runs(org, left_runs);
                END IF;
                -- Uploads that never became a dataset (abandoned or still open).
                INSERT INTO app.storage_purges (org_id, key, is_prefix, purge_after)
                SELECT org, u.staging_key, false, delete_workspace.purge_after
                  FROM app.uploads u WHERE u.org_id = org
                    ON CONFLICT (org_id, key) DO NOTHING;
                DELETE FROM app.uploads u WHERE u.org_id = org;
                PERFORM set_config('app.erasing', 'on', true);
                DELETE FROM app.llm_calls c WHERE c.org_id = org;
                PERFORM set_config('app.erasing', 'off', true);

                DELETE FROM app.idempotency_keys k WHERE k.org_id = org;
                DELETE FROM app.usage_counters c WHERE c.org_id = org;
                DELETE FROM app.user_activity a WHERE a.org_id = org OR a.user_id = member;
                DELETE FROM app.llm_spend_totals t WHERE t.scope = member;
                DELETE FROM app.llm_spend_holds h WHERE h.user_id = member;

                UPDATE app.organizations o
                   SET name = 'Deleted workspace', deleted_at = now()
                 WHERE o.id = org;
                BEGIN
                    DELETE FROM app.users u WHERE u.id = member;
                EXCEPTION WHEN foreign_key_violation THEN
                    NULL;  -- still named by another organisation's rows (not possible yet)
                END;
                IF NOT EXISTS (SELECT 1 FROM app.storage_purges p WHERE p.org_id = org) THEN
                    DELETE FROM app.organizations o WHERE o.id = org;
                END IF;
                RETURN QUERY SELECT 'deleted'::text, all_runs;
            END
            $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION app.forget_deleted_organizations() RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                tombstone record;
                forgotten integer := 0;
            BEGIN
                FOR tombstone IN
                    SELECT o.id FROM app.organizations o
                     WHERE o.deleted_at IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM app.storage_purges p WHERE p.org_id = o.id)
                LOOP
                    BEGIN
                        DELETE FROM app.organizations o WHERE o.id = tombstone.id;
                        forgotten := forgotten + 1;
                    EXCEPTION WHEN foreign_key_violation THEN
                        NULL;  -- used again after the delete (a token still valid): keep it
                    END;
                END LOOP;
                RETURN forgotten;
            END
            $$
        """
    )
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.delete_workspace(uuid, timestamptz) TO ic_app")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.forget_deleted_organizations() TO ic_app, ic_maintenance"
    )


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("ALTER TABLE app.organizations DROP COLUMN deleted_at")
