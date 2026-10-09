#!/usr/bin/env bash
# Backend quality gate: format, lint, types, architecture, tests, contract, dependency audit.
#   scripts/check-backend.sh [--fix] [--fast] [--fail-fast]
# --fast skips the dependency audit; --fix formats and auto-fixes lint instead of only checking.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FIX=0; FAST=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;; --fast) FAST=1 ;; --fail-fast) FAIL_FAST=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
cd "$BACKEND"
printf '%sBackend checks%s (%s)\n' "$BOLD" "$RESET" "$BACKEND"
uv sync --quiet

if [ "$FIX" -eq 1 ]; then
  run_step "ruff format (fixing)" uv run ruff format .
  run_step "ruff check (fixing)" uv run ruff check . --fix
else
  run_step "ruff format --check" uv run ruff format --check .
  run_step "ruff check" uv run ruff check .
fi
run_step "mypy (strict)" uv run mypy
run_step "import-linter (layering)" uv run lint-imports

ADMIN_URL="${TEST_DATABASE_ADMIN_URL:-$DEFAULT_ADMIN_URL}"
if db_reachable "$ADMIN_URL"; then
  export TEST_DATABASE_ADMIN_URL="$ADMIN_URL"
  run_step "pytest unit + integration (coverage >= 85%)" uv run pytest --cov -q
  run_step "migrations: up, down, up" "$ROOT/scripts/check-migrations.sh"
else
  printf '\n%sNo PostgreSQL at %s.%s Start one with `make up` (or set TEST_DATABASE_ADMIN_URL).\n' \
    "$YELLOW" "$ADMIN_URL" "$RESET"
  run_step "pytest unit only" uv run pytest tests/unit -q
  skip_step "pytest integration (real PostgreSQL + RLS)" "no database; CI will run it"
  skip_step "migrations: up, down, up" "no database"
fi

run_step "OpenAPI contract is up to date" "$ROOT/scripts/generate-contract.sh" --check

if [ "$FAST" -eq 1 ]; then
  skip_step "pip-audit (known vulnerabilities)" "--fast"
else
  run_step "pip-audit (known vulnerabilities)" uv run pip-audit --skip-editable
fi

print_summary
