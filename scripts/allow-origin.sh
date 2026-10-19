#!/usr/bin/env bash
# Open the local app from another address too: an ngrok tunnel, a LAN address, a second domain.
#   scripts/allow-origin.sh https://abcd.ngrok-free.app      (or: make allow-origin ORIGIN=…)
# Adds the origin to AUTH_TRUSTED_ORIGINS (frontend/.env.local: sign-in and the backend proxy),
# EVENTS_ALLOWED_ORIGINS (backend/.env: live progress) and the local bucket's CORS (uploads).
# Restart `make web` and `make api` (or `make dev`) afterwards. ADR-038.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

origin="${1:-}"
origin="${origin%/}"
if [[ ! "$origin" =~ ^https?://[^/?#@]+$ ]]; then
  echo "Usage: scripts/allow-origin.sh https://abcd.ngrok-free.app (scheme://host[:port], no path)" >&2
  exit 2
fi

WEB_ENV="$FRONTEND/.env.local"
API_ENV="$BACKEND/.env"
for f in "$WEB_ENV" "$API_ENV"; do
  [ -f "$f" ] || { echo "$f is missing: run 'make bootstrap' first." >&2; exit 1; }
done

current() { # current <file> <VAR>: the variable's value, empty if unset
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d "\"'"
}

add_origin() { # add_origin <file> <VAR> <value if unset>
  local file="$1" var="$2" value
  value="$(current "$file" "$var")"
  if [[ ",$value," == *",$origin,"* ]]; then
    printf '  %s already allows %s (%s)\n' "$var" "$origin" "${file#"$ROOT"/}"
    return
  fi
  value="${value:-$3}"
  value="${value:+$value,}$origin"
  if grep -q "^$var=" "$file"; then
    local tmp
    tmp="$(mktemp)"
    awk -v var="$var" -v line="$var=$value" '$0 ~ "^" var "=" { print line; next } { print }' "$file" >"$tmp"
    cat "$tmp" >"$file" && rm -f "$tmp"
  else
    printf '\n%s=%s\n' "$var" "$value" >>"$file"
  fi
  printf '  %s=%s (%s)\n' "$var" "$value" "${file#"$ROOT"/}"
}

app_url="$(current "$WEB_ENV" BETTER_AUTH_URL)"
app_url="${app_url%/}"
if [ "$origin" = "$app_url" ]; then
  echo "$origin is BETTER_AUTH_URL already; nothing to add."
  exit 0
fi
if [[ "$app_url" == https://* && "$origin" != https://* ]]; then
  echo "$origin must be https while BETTER_AUTH_URL ($app_url) is: session cookies are Secure." >&2
  exit 2
fi

printf '%sAllowing %s%s\n' "$BOLD" "$origin" "$RESET"
add_origin "$WEB_ENV" AUTH_TRUSTED_ORIGINS ""
# Unset, the API allows only http://localhost:3000; keep the app's own origin in the list.
add_origin "$API_ENV" EVENTS_ALLOWED_ORIGINS "${app_url:-http://localhost:3000}"

s3_endpoint="$(current "$API_ENV" S3_ENDPOINT_URL)"
if [ -n "$s3_endpoint" ]; then
  if (cd "$BACKEND" && uv run --quiet python ../scripts/s3_setup.py --endpoint "$s3_endpoint" \
    --bucket "$(current "$API_ENV" S3_BUCKET)" --add --origin "$origin" >/dev/null); then
    printf '  bucket CORS allows %s (%s)\n' "$origin" "$s3_endpoint"
  else
    printf '%s  Could not update the bucket at %s:%s run "make up", then this again.\n' \
      "$YELLOW" "$s3_endpoint" "$RESET"
  fi
fi

cat <<EOF

${BOLD}Next:${RESET} restart the web app and the API (make web + make api, or make dev).
${DIM}Tokens are still issued as $app_url, so AUTH_ISSUER stays as it is.
Only the web app needs a tunnel: while storage and the API are on localhost, pages opened at
$origin send uploads and live progress through it (ADR-039). Keep a worker running.${RESET}
EOF
