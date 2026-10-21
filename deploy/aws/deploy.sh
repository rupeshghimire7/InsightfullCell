#!/usr/bin/env bash
# Build, migrate and (re)start InsightfulCell on this host. Safe to run again on every update:
#   git pull && deploy/aws/deploy.sh
# Steps: build images -> database roles -> API migrations -> app login -> sign-in tables -> start.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

for f in .env backend.env web.env; do
  [[ -f $f ]] || { echo "Missing deploy/aws/$f: run ./init-env.sh first." >&2; exit 1; }
done
docker network inspect edge >/dev/null 2>&1 || {
  echo "No 'edge' network: start the shared proxy first (README.md, step 8)." >&2
  exit 1
}
set -a; source .env; set +a

psql_owner() {  # psql_owner <sql on stdin>: run as the table owner, passwords as psql variables
  docker compose exec -T postgres psql -U ic_owner -d insightfulcell -q -v ON_ERROR_STOP=1 \
    -v auth_pw="$IC_AUTH_DB_PASSWORD" -v app_pw="$IC_APP_DB_PASSWORD"
}

echo "==> Building images"
docker compose --profile tools build

echo "==> Starting PostgreSQL"
docker compose up -d --wait postgres

echo "==> Web app role and its auth schema"
# What scripts/db_bootstrap.py does locally, with real passwords (it refuses non-local hosts).
psql_owner <<'SQL'
SELECT format('CREATE ROLE ic_auth LOGIN NOBYPASSRLS PASSWORD %L', :'auth_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_auth') \gexec
ALTER ROLE ic_auth PASSWORD :'auth_pw';
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION ic_auth;
REVOKE ALL ON SCHEMA public FROM ic_auth;
GRANT CONNECT ON DATABASE insightfulcell TO ic_auth;
SQL

echo "==> API migrations"
docker compose run --rm migrate

echo "==> API login role (member of ic_app, created by the first migration)"
psql_owner <<'SQL'
SELECT format('CREATE ROLE ic_app_prod LOGIN NOBYPASSRLS PASSWORD %L', :'app_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ic_app_prod') \gexec
ALTER ROLE ic_app_prod PASSWORD :'app_pw';
GRANT ic_app TO ic_app_prod;
SQL

echo "==> Sign-in tables"
docker compose run --rm auth-migrate

echo "==> Starting API, worker and web app"
docker compose up -d --wait --wait-timeout 180 api worker web

docker image prune -f >/dev/null
docker compose ps
echo "Done: https://app.ic.rgproject.me"
