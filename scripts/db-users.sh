#!/usr/bin/env bash
# List the users in the local Postgres container: email, sign-in method, organisations and roles.
# Never prints password hashes or tokens.
#   scripts/db-users.sh                 # the dev database (insightfulcell)
#   scripts/db-users.sh ic_qa           # another database in the same container
#   scripts/db-users.sh --list-dbs      # which databases exist
#   scripts/db-users.sh --shell [db]    # open psql
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

psql_in() { docker compose exec -T postgres psql -U ic_owner -v ON_ERROR_STOP=1 "$@"; }

case "${1:-}" in
  --list-dbs)
    psql_in -d postgres -c "select datname as database from pg_database where not datistemplate order by 1"
    exit 0 ;;
  --shell)
    exec docker compose exec postgres psql -U ic_owner -d "${2:-insightfulcell}" ;;
esac

DB="${1:-insightfulcell}"
printf '%sUsers in %s%s\n' "$BOLD" "$DB" "$RESET"
psql_in -d "$DB" <<'SQL'
select u.email,
       u.name,
       u."emailVerified"                                                    as verified,
       coalesce(string_agg(distinct a."providerId", ', '), '-')             as sign_in,
       coalesce(string_agg(distinct o.name || ' (' || m.role || ')', ', '), '-') as organisations,
       to_char(u."createdAt", 'YYYY-MM-DD HH24:MI')                         as created,
       (select to_char(max(s."updatedAt"), 'YYYY-MM-DD HH24:MI')
          from auth.session s where s."userId" = u.id)                      as last_session,
       (select count(*) from auth.session s
          where s."userId" = u.id and s."expiresAt" > now())                as live_sessions
  from auth."user" u
  left join auth.account a      on a."userId" = u.id
  left join auth.member m       on m."userId" = u.id
  left join auth.organization o on o.id = m."organizationId"
 group by u.id
 order by u."createdAt";
SQL
