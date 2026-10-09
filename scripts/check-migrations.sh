#!/usr/bin/env bash
# Prove the migrations are reversible and repeatable: upgrade -> downgrade to base -> upgrade.
# Runs in a throwaway database so nothing you care about is touched. Needs PostgreSQL.
#   scripts/check-migrations.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
ADMIN_URL="${TEST_DATABASE_ADMIN_URL:-$DEFAULT_ADMIN_URL}"
db_reachable "$ADMIN_URL" || { echo "No PostgreSQL at $ADMIN_URL (run \`make up\`)." >&2; exit 1; }

SCRATCH="ic_migcheck_$$"
cleanup() { admin_sql "$ADMIN_URL" "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT

admin_sql "$ADMIN_URL" "CREATE DATABASE $SCRATCH"
export MIGRATION_DATABASE_URL="$(url_with_db "$ADMIN_URL" "$SCRATCH")"
cd "$BACKEND"
for step in "upgrade head" "downgrade base" "upgrade head"; do
  echo "alembic $step"
  uv run --quiet alembic $step
done
# One head only: two branches would make deploy order ambiguous.
heads="$(uv run --quiet alembic heads | grep -c '(head)' || true)"
[ "$heads" = "1" ] || { echo "Expected exactly one migration head, found $heads." >&2; exit 1; }
echo "Migrations are reversible and linear."
