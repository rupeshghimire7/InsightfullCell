#!/usr/bin/env bash
# Shared helpers for the check scripts. Source it; do not execute it.
# Portable to bash 3.2 (macOS): no associative arrays, no mapfile.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

RESULTS=()

need() {
  # need <command> <how to install>
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '%sMissing tool:%s %s\n  %s\n' "$RED" "$RESET" "$1" "$2" >&2
    exit 127
  fi
}

# run_step <label> <command...>: run, record the result, keep going unless FAIL_FAST=1.
run_step() {
  local label="$1"; shift
  printf '\n%s▶ %s%s\n' "$BOLD" "$label" "$RESET"
  local started=$SECONDS
  if "$@"; then
    RESULTS+=("PASS|$label|$((SECONDS - started))s")
  else
    RESULTS+=("FAIL|$label|$((SECONDS - started))s")
    if [ "${FAIL_FAST:-0}" = "1" ]; then
      print_summary || true
      exit 1
    fi
  fi
}

skip_step() {
  # skip_step <label> <reason>
  printf '\n%s▶ %s%s %s(skipped: %s)%s\n' "$BOLD" "$1" "$RESET" "$YELLOW" "$2" "$RESET"
  RESULTS+=("SKIP|$1|$2")
}

print_summary() {
  local failed=0 line status label detail
  printf '\n%sSummary%s\n' "$BOLD" "$RESET"
  for line in "${RESULTS[@]}"; do
    status="${line%%|*}"; line="${line#*|}"; label="${line%%|*}"; detail="${line#*|}"
    case "$status" in
      PASS) printf '  %s✔ pass%s  %-44s %s%s%s\n' "$GREEN" "$RESET" "$label" "$DIM" "$detail" "$RESET" ;;
      SKIP) printf '  %s– skip%s  %-44s %s%s%s\n' "$YELLOW" "$RESET" "$label" "$DIM" "$detail" "$RESET" ;;
      FAIL) printf '  %s✘ FAIL%s  %-44s %s%s%s\n' "$RED" "$RESET" "$label" "$DIM" "$detail" "$RESET"; failed=1 ;;
    esac
  done
  if [ "$failed" -eq 1 ]; then
    printf '\n%sChecks failed.%s Fix the items above (re-run with --fix for formatting).\n' "$RED" "$RESET"
    return 1
  fi
  printf '\n%sAll checks passed.%s\n' "$GREEN" "$RESET"
}

# Default location of the throwaway PostgreSQL used by integration tests (docker-compose.yml).
DEFAULT_ADMIN_URL="postgresql+psycopg://ic_owner:ic_owner@localhost:5432/postgres"

db_reachable() {
  # db_reachable <sqlalchemy-style url>: true when a TCP connection to the host:port succeeds.
  local url="$1" hostport host port
  hostport="${url#*@}"; hostport="${hostport%%/*}"
  host="${hostport%%:*}"; port="${hostport##*:}"
  [ "$port" = "$hostport" ] && port=5432
  (exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1
}

# url_with_db <url> <database>: the same URL pointing at another database.
url_with_db() {
  printf '%s/%s' "${1%/*}" "$2"
}

# Run a SQL statement against the admin database (maintenance database in the admin URL).
admin_sql() {
  (cd "$BACKEND" && uv run --quiet python - "$1" "$2" <<'PY'
import sys
import psycopg
url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute(sys.argv[2])
PY
  )
}
