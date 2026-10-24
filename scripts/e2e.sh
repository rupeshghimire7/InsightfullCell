#!/usr/bin/env bash
# Browser end-to-end test of the whole stack: sign in -> upload a CSV -> analyse it live, and
# reset a forgotten password.
# Starts a fresh database, the API, the worker, an S3 endpoint and the production build of the
# web app, runs Playwright, then tears everything down.
#   scripts/e2e.sh [--skip-build]
#
# Environment (all optional):
#   TEST_DATABASE_ADMIN_URL  admin connection to PostgreSQL (default: docker-compose's)
#   E2E_S3_ENDPOINT          S3 endpoint to use (default: RustFS from `make up` if running, else
#                            the built-in emulator; CI uses RustFS;
#                            credentials must be dev / devdevdev, as in docker-compose.yml)
#   E2E_API_PORT E2E_WEB_PORT E2E_S3_PORT   default 8000 / 3000 / 9000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKIP_BUILD=0
[ "${1:-}" = "--skip-build" ] && SKIP_BUILD=1

need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need pnpm "Install pnpm: corepack enable"
need node "Install Node.js 22+"

ADMIN_URL="${TEST_DATABASE_ADMIN_URL:-$DEFAULT_ADMIN_URL}"
db_reachable "$ADMIN_URL" || { echo "No PostgreSQL at $ADMIN_URL (run \`make up\`)." >&2; exit 1; }

API_PORT="${E2E_API_PORT:-8000}"; WEB_PORT="${E2E_WEB_PORT:-3000}"; S3_PORT="${E2E_S3_PORT:-9000}"
# Prefer the real S3 server from `make up` (it checks signatures); fall back to the emulator.
if [ -z "${E2E_S3_ENDPOINT:-}" ] && curl -fsS -o /dev/null "http://127.0.0.1:$S3_PORT/health" 2>/dev/null; then
  E2E_S3_ENDPOINT="http://127.0.0.1:$S3_PORT"
fi
DB="ic_e2e_$$"
# The admin dashboard is on for the browser test, with a throwaway key for this run only.
export ADMIN_DASHBOARD_KEY="e2e-admin-key-$(openssl rand -hex 24)"
STATE="$ROOT/.e2e"; mkdir -p "$STATE"
WEB_URL="http://localhost:$WEB_PORT"
S3_URL="${E2E_S3_ENDPOINT:-http://127.0.0.1:$S3_PORT}"
HOSTPORT="${ADMIN_URL#*@}"; HOSTPORT="${HOSTPORT%%/*}"
PIDS=()

cleanup() {
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill "$pid" 2>/dev/null || true; done
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do wait "$pid" 2>/dev/null || true; done
  admin_sql "$ADMIN_URL" "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for() {
  # wait_for <url> <label> [seconds]
  local i
  for ((i = 0; i < ${3:-60}; i++)); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  echo "$2 did not become ready at $1. See logs in $STATE." >&2
  return 1
}

PORTS="$API_PORT $WEB_PORT"
[ -z "${E2E_S3_ENDPOINT:-}" ] && PORTS="$PORTS $S3_PORT"
for port in $PORTS; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "Port $port is already in use; stop that process or set E2E_*_PORT." >&2; exit 1
  fi
done

printf '%sEnd-to-end test%s (database %s)\n' "$BOLD" "$RESET" "$DB"
cd "$BACKEND"; uv sync --quiet
cd "$FRONTEND"; pnpm install --frozen-lockfile --silent

echo "• database"
run() { (cd "$BACKEND" && uv run --quiet python "$ROOT/scripts/db_bootstrap.py" "$@" --admin-url "$ADMIN_URL" --database "$DB"); }
run create-db
(cd "$BACKEND" && MIGRATION_DATABASE_URL="$(url_with_db "$ADMIN_URL" "$DB")" uv run --quiet alembic upgrade head)
run create-logins
AUTH_DB_URL="postgresql://ic_auth:ic_auth_dev@$HOSTPORT/$DB"
(cd "$FRONTEND" && AUTH_DATABASE_URL="$AUTH_DB_URL" pnpm --silent auth:migrate)

echo "• object storage"
if [ -z "${E2E_S3_ENDPOINT:-}" ]; then
  (cd "$BACKEND" && exec .venv/bin/python "$ROOT/scripts/dev-s3.py" --port "$S3_PORT") >"$STATE/s3.log" 2>&1 &
  PIDS+=($!)
  wait_for "$S3_URL" "S3 emulator" 30 || true  # moto answers 4xx on / ; reachability is enough below
fi
# --add: when reusing the RustFS from `make up`, keep the origin `make web` uploads from.
(cd "$BACKEND" && .venv/bin/python "$ROOT/scripts/s3_setup.py" --endpoint "$S3_URL" --origin "$WEB_URL" --add)

backend_env() {
  export ENVIRONMENT=local
  export DATABASE_URL="postgresql+psycopg://ic_app_dev:ic_app_dev@$HOSTPORT/$DB"
  export AUTH_JWKS_URL="$WEB_URL/api/auth/jwks" AUTH_ISSUER="$WEB_URL"
  export S3_ENDPOINT_URL="$S3_URL" S3_ACCESS_KEY_ID=dev S3_SECRET_ACCESS_KEY=devdevdev
  export S3_FORCE_PATH_STYLE=true S3_BUCKET=insightfulcell-dev
  # The browser opens live-progress streams on the API directly (with a run-scoped ticket).
  export EVENTS_ALLOWED_ORIGINS="$WEB_URL"
  # Plan with the built-in rules: deterministic, and a key in backend/.env never spends money.
  export LLM_PROVIDER=none
}

echo "• api"
(
  cd "$BACKEND"
  backend_env
  exec .venv/bin/python -m uvicorn insightfulcell.api.main:app --host 127.0.0.1 --port "$API_PORT" \
    --timeout-graceful-shutdown 2
) >"$STATE/api.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:$API_PORT/healthz" "API" 30

echo "• worker"
(
  cd "$BACKEND"
  backend_env
  exec .venv/bin/python -m insightfulcell.worker --name e2e-worker
) >"$STATE/worker.log" 2>&1 &
PIDS+=($!)

echo "• web"
export APP_ENV=local BETTER_AUTH_URL="$WEB_URL" AUTH_DATABASE_URL="$AUTH_DB_URL"
export BETTER_AUTH_SECRET="e2e-only-secret-$(printf '%032d' "$$")"
export API_BASE_URL="http://127.0.0.1:$API_PORT" S3_PUBLIC_ORIGIN="$S3_URL"
# Sign-in links stay off whatever frontend/.env.local says: the journey checks that state.
export EMAIL_LINK_SIGN_IN=off EMAIL_LINK_ALLOWED_EMAILS= EMAIL_TRANSPORT=console AUTH_MAGIC_LINK_RATE_MAX=200
# "Forgot password?" is on: reset links are printed to web.log, where the browser test reads them.
export PASSWORD_RESET=on EMAIL_RESET_FROM=
if [ "$SKIP_BUILD" -eq 0 ]; then (cd "$FRONTEND" && pnpm --silent build >"$STATE/build.log" 2>&1) || { tail -30 "$STATE/build.log" >&2; exit 1; }; fi
(cd "$FRONTEND" && PORT="$WEB_PORT" HOSTNAME=127.0.0.1 exec node scripts/start.mjs) >"$STATE/web.log" 2>&1 &
PIDS+=($!)
wait_for "$WEB_URL/sign-in" "Web app" 60

echo "• browser"
cd "$FRONTEND"
export E2E_BASE_URL="$WEB_URL" E2E_WEB_LOG="$STATE/web.log"
if ! pnpm exec playwright test; then
  echo "Logs: $STATE/{api,worker,web,s3}.log. Missing browser? Run: pnpm --dir frontend exec playwright install chromium" >&2
  exit 1
fi
