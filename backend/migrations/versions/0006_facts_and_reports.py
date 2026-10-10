"""Analysis Phase B: facts and reports.

- ``run_facts``: every fact the analysis computed (a statement, its numbers, the columns and
  rows it rests on, the method), immutable. Report sentences cite them by id.
- ``run_reports``: the report document of a run (answers, charts, how it was made),
  append-only; a retry adds a revision.

Both are tenant tables with forced row-level security, like every table since 0002.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("run_facts", "run_reports")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.run_facts (
            org_id        uuid NOT NULL,
            run_id        uuid NOT NULL,
            fact_id       text NOT NULL CHECK (fact_id ~ '^F[0-9]{1,6}$'),
            analysis_id   text NOT NULL CHECK (length(analysis_id) BETWEEN 1 AND 4000),
            kind          text NOT NULL CHECK (length(kind) BETWEEN 1 AND 50),
            columns       text[] NOT NULL,
            rows          bigint NOT NULL CHECK (rows >= 0),
            method        text NOT NULL CHECK (length(method) <= 2000),
            statement     text NOT NULL CHECK (length(statement) BETWEEN 1 AND 4000),
            numbers       text[] NOT NULL DEFAULT '{}',
            labels        text[] NOT NULL DEFAULT '{}',
            value         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(value) = 'object'),
            content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, fact_id),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.run_reports (
            id                uuid PRIMARY KEY DEFAULT app.uuidv7(),
            org_id            uuid NOT NULL,
            run_id            uuid NOT NULL,
            revision          integer NOT NULL CHECK (revision >= 1),
            document          jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
            narration_source  text NOT NULL CHECK (narration_source IN ('model', 'engine', 'mixed')),
            provider          text CHECK (length(provider) <= 50),
            model             text CHECK (length(model) <= 200),
            prompt_version    text CHECK (length(prompt_version) <= 100),
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, revision),
            FOREIGN KEY (org_id, run_id) REFERENCES app.runs (org_id, id)
        )
        """
    )
    op.execute("CREATE INDEX run_facts_org_run ON app.run_facts (org_id, run_id)")

    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON app.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_change()"
        )
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON app.{table}
                USING (org_id = app.current_org())
                WITH CHECK (org_id = app.current_org())
            """
        )
    op.execute("GRANT SELECT, INSERT ON app.run_facts, app.run_reports TO ic_app")
    op.execute("GRANT SELECT ON app.run_facts, app.run_reports TO ic_readonly, ic_maintenance")


def downgrade() -> None:
    op.execute("DROP TABLE app.run_reports")
    op.execute("DROP TABLE app.run_facts")
