"""Deleting runs and datasets (ADR-037).

- ``storage_purges``: object keys (or per-run / per-version prefixes) whose rows were deleted
  and whose files are removed from storage once ``purge_after`` has passed. A tenant table with
  forced row-level security; the key must sit under its own organisation's prefix.
- ``app.delete_run(run, purge_after)`` and ``app.delete_dataset(dataset, purge_after)``:
  ``SECURITY DEFINER`` functions that delete a run (with its drill-downs) or a dataset (with its
  versions, uploads and every run of it) and everything that refers to them, at once, for the
  organisation set in the transaction (``app.current_org()``). They refuse (``busy``) while any
  of those runs is running, and schedule the files for purging.
- The append-only triggers allow a ``DELETE`` while ``app.erasing`` is on. Only the owner can
  set it to any effect: the application role has no ``DELETE`` grant on those tables, and the
  helper that sets it (``app.erase_runs``) is not executable by it.
- ``app.storage_purges_due(limit)``: ids of purges that are due, for the worker's sweep.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

#: Executable by the application; ``app.erase_runs`` is internal (owner only).
DEFINER_FUNCTIONS = (
    "app.delete_run(uuid, timestamptz)",
    "app.delete_dataset(uuid, timestamptz)",
    "app.storage_purges_due(integer)",
)

_TRIGGER_FUNCTION = """
    CREATE OR REPLACE FUNCTION app.{name}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND ({allowed})
            THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION {message}
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
"""
_PARTITIONS = "current_setting('app.partition_maintenance', true) = 'on'"
_ERASING = "current_setting('app.erasing', true) = 'on'"
_EVENT_MESSAGE = "'app.run_events is append-only'"
_APPEND_ONLY_MESSAGE = "'%.% is append-only', TG_TABLE_SCHEMA, TG_TABLE_NAME"


def _trigger_functions(allowed: str) -> None:
    op.execute(
        _TRIGGER_FUNCTION.format(
            name="reject_event_change", allowed=allowed, message=_EVENT_MESSAGE
        )
    )
    op.execute(
        _TRIGGER_FUNCTION.format(
            name="reject_append_only_change", allowed=allowed, message=_APPEND_ONLY_MESSAGE
        )
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.storage_purges (
            id           uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id       uuid NOT NULL REFERENCES app.organizations (id),
            key          text NOT NULL CHECK (length(key) BETWEEN 1 AND 1024),
            is_prefix    boolean NOT NULL,
            purge_after  timestamptz NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, key),
            -- Never outside the organisation's own area, and a prefix names at least one more
            -- level (a run or a version), so no purge can reach a whole zone or organisation.
            CHECK (split_part(key, '/', 1) IN ('bronze', 'silver', 'gold')),
            CHECK (split_part(key, '/', 2) = org_id::text),
            CHECK (length(split_part(key, '/', 3)) > 0),
            CHECK (NOT is_prefix OR right(key, 1) = '/')
        )
        """
    )
    op.execute("CREATE INDEX storage_purges_due ON app.storage_purges (purge_after)")
    op.execute("ALTER TABLE app.storage_purges ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.storage_purges FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON app.storage_purges
            USING (org_id = app.current_org())
            WITH CHECK (org_id = app.current_org())
        """
    )
    op.execute("GRANT SELECT, DELETE ON app.storage_purges TO ic_app, ic_maintenance")
    op.execute("GRANT SELECT ON app.storage_purges TO ic_readonly")

    _trigger_functions(f"{_PARTITIONS} OR {_ERASING}")

    # Deletes runs and everything that refers to them, children before parents. The owner may
    # bypass row-level security, so every statement names the organisation itself.
    op.execute(
        """
        CREATE FUNCTION app.erase_runs(org uuid, run_ids uuid[]) RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            BEGIN
                PERFORM set_config('app.erasing', 'on', true);
                DELETE FROM app.test_log WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.verification_results
                 WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.run_reports WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.run_facts WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.run_events WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.llm_calls WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.cleaning_ops
                 WHERE org_id = org
                   AND plan_id IN (SELECT id FROM app.cleaning_plans
                                    WHERE org_id = org AND run_id = ANY (run_ids));
                DELETE FROM app.cleaning_plans WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.analysis_plans WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.run_columns WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.run_stages WHERE org_id = org AND run_id = ANY (run_ids);
                DELETE FROM app.runs WHERE org_id = org AND id = ANY (run_ids);
                PERFORM set_config('app.erasing', 'off', true);
            END
            $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION app.erase_runs(uuid, uuid[]) FROM PUBLIC")

    op.execute(
        """
        CREATE FUNCTION app.delete_run(target uuid, purge_after timestamptz)
            RETURNS TABLE (outcome text, run_ids uuid[])
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                org uuid := app.current_org();
                tree uuid[];
            BEGIN
                IF org IS NULL THEN
                    RAISE EXCEPTION 'no organisation is set'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                PERFORM 1 FROM app.runs r WHERE r.org_id = org AND r.id = target FOR UPDATE;
                IF NOT FOUND THEN
                    RETURN QUERY SELECT 'not_found'::text, '{}'::uuid[];
                    RETURN;
                END IF;
                -- The run and its drill-downs (and theirs).
                WITH RECURSIVE family (id) AS (
                    SELECT target
                    UNION
                    SELECT r.id FROM app.runs r JOIN family f ON r.parent_run_id = f.id
                     WHERE r.org_id = org)
                SELECT array_agg(family.id) INTO tree FROM family;
                PERFORM 1 FROM app.runs r WHERE r.org_id = org AND r.id = ANY (tree) FOR UPDATE;
                IF EXISTS (SELECT 1 FROM app.runs r
                            WHERE r.org_id = org AND r.id = ANY (tree) AND r.status = 'running')
                   OR EXISTS (SELECT 1 FROM procrastinate.procrastinate_jobs j
                               WHERE j.lock = ANY (tree::text[]) AND j.status = 'doing')
                THEN
                    RETURN QUERY SELECT 'busy'::text, tree;
                    RETURN;
                END IF;
                INSERT INTO app.storage_purges (org_id, key, is_prefix, purge_after)
                SELECT org, 'gold/' || org || '/' || t.id || '/', true, delete_run.purge_after
                  FROM unnest(tree) AS t (id)
                    ON CONFLICT (org_id, key) DO NOTHING;
                PERFORM app.erase_runs(org, tree);
                RETURN QUERY SELECT 'deleted'::text, tree;
            END
            $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION app.delete_dataset(target uuid, purge_after timestamptz)
            RETURNS TABLE (outcome text, run_ids uuid[])
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
            DECLARE
                org uuid := app.current_org();
                run_list uuid[];
                upload_ids uuid[];
            BEGIN
                IF org IS NULL THEN
                    RAISE EXCEPTION 'no organisation is set'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                PERFORM 1 FROM app.datasets d WHERE d.org_id = org AND d.id = target FOR UPDATE;
                IF NOT FOUND THEN
                    RETURN QUERY SELECT 'not_found'::text, '{}'::uuid[];
                    RETURN;
                END IF;
                SELECT coalesce(array_agg(r.id), '{}') INTO run_list
                  FROM app.runs r WHERE r.org_id = org AND r.dataset_id = target;
                PERFORM 1 FROM app.runs r WHERE r.org_id = org AND r.id = ANY (run_list) FOR UPDATE;
                IF EXISTS (SELECT 1 FROM app.runs r
                            WHERE r.org_id = org AND r.id = ANY (run_list) AND r.status = 'running')
                   OR EXISTS (SELECT 1 FROM procrastinate.procrastinate_jobs j
                               WHERE j.lock = ANY (run_list::text[]) AND j.status = 'doing')
                THEN
                    RETURN QUERY SELECT 'busy'::text, run_list;
                    RETURN;
                END IF;

                -- An upload that created the dataset is not linked to it, only to its version.
                SELECT coalesce(array_agg(DISTINCT u.id), '{}') INTO upload_ids
                  FROM app.uploads u
                 WHERE u.org_id = org
                   AND (u.dataset_id = target
                        OR u.id IN (SELECT v.upload_id FROM app.dataset_versions v
                                     WHERE v.org_id = org AND v.dataset_id = target));

                INSERT INTO app.storage_purges (org_id, key, is_prefix, purge_after)
                SELECT org, k.key, k.is_prefix, delete_dataset.purge_after
                  FROM (
                      SELECT 'gold/' || org || '/' || r.id || '/' AS key, true AS is_prefix
                        FROM unnest(run_list) AS r (id)
                      UNION ALL
                      SELECT 'silver/' || org || '/' || v.id || '/', true
                        FROM app.dataset_versions v
                       WHERE v.org_id = org AND v.dataset_id = target
                      UNION ALL
                      SELECT v.bronze_key, false
                        FROM app.dataset_versions v
                       WHERE v.org_id = org AND v.dataset_id = target
                      UNION ALL
                      SELECT u.staging_key, false
                        FROM app.uploads u WHERE u.org_id = org AND u.id = ANY (upload_ids)
                  ) AS k
                    ON CONFLICT (org_id, key) DO NOTHING;

                PERFORM app.erase_runs(org, run_list);
                PERFORM set_config('app.erasing', 'on', true);
                DELETE FROM app.analysis_plans p WHERE p.org_id = org AND p.dataset_id = target;
                PERFORM set_config('app.erasing', 'off', true);
                DELETE FROM app.dataset_preferences p
                 WHERE p.org_id = org AND p.dataset_id = target;
                DELETE FROM app.dataset_versions v WHERE v.org_id = org AND v.dataset_id = target;
                DELETE FROM app.uploads u WHERE u.org_id = org AND u.id = ANY (upload_ids);
                DELETE FROM app.datasets d WHERE d.org_id = org AND d.id = target;
                RETURN QUERY SELECT 'deleted'::text, run_list;
            END
            $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION app.storage_purges_due(max_rows integer)
            RETURNS TABLE (org_id uuid, purge_id uuid)
            LANGUAGE sql STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, app
            AS $$
                SELECT p.org_id, p.id
                  FROM app.storage_purges p
                 WHERE p.purge_after <= now()
                 ORDER BY p.purge_after
                 LIMIT max_rows
            $$
        """
    )
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.delete_run(uuid, timestamptz) TO ic_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.delete_dataset(uuid, timestamptz) TO ic_app")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.storage_purges_due(integer) TO ic_app, ic_maintenance"
    )


def downgrade() -> None:
    for signature in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP FUNCTION app.erase_runs(uuid, uuid[])")
    _trigger_functions(_PARTITIONS)
    op.execute("DROP TABLE app.storage_purges")
