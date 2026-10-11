"""Phase 6: dashboards and drill-downs.

- ``run_reports.dashboard``: the dashboard document assembled with each report revision, so a
  report and its dashboard are one append-only row and always agree. Null for reports written
  before dashboards existed.
- ``runs.parent_run_id``: a drill-down is a run of a subset chosen from a finished run's
  report; it names that run, in the same organisation (composite foreign key). The subset
  itself is part of the run's ``inputs``.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.run_reports ADD COLUMN dashboard jsonb "
        "CHECK (dashboard IS NULL OR jsonb_typeof(dashboard) = 'object')"
    )
    op.execute("ALTER TABLE app.runs ADD COLUMN parent_run_id uuid")
    op.execute(
        "ALTER TABLE app.runs ADD CONSTRAINT runs_parent_run "
        "FOREIGN KEY (org_id, parent_run_id) REFERENCES app.runs (org_id, id)"
    )
    op.execute(
        "ALTER TABLE app.runs ADD CONSTRAINT runs_parent_not_self "
        "CHECK (parent_run_id IS NULL OR parent_run_id <> id)"
    )
    op.execute(
        "CREATE INDEX runs_children ON app.runs (org_id, parent_run_id, created_at DESC) "
        "WHERE parent_run_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX app.runs_children")
    op.execute("ALTER TABLE app.runs DROP CONSTRAINT runs_parent_not_self")
    op.execute("ALTER TABLE app.runs DROP CONSTRAINT runs_parent_run")
    op.execute("ALTER TABLE app.runs DROP COLUMN parent_run_id")
    op.execute("ALTER TABLE app.run_reports DROP COLUMN dashboard")
