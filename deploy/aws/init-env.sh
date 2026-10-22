#!/usr/bin/env bash
# First-time setup on the server: copy the *.example files and fill every secret with a fresh
# random value. Never overwrites a file that already exists. Edit the copies afterwards for the
# bucket names and any LLM keys (README.md, step 9).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fill() {  # fill <file> <key> <value>: set key=value where the line is still "key="
  sed -i.bak "s|^$2=\$|$2=$3|" "$1" && rm -f "$1.bak"
}

for f in .env backend.env web.env; do
  if [[ -f $f ]]; then
    echo "deploy/aws/$f exists, left as it is."
    continue
  fi
  cp "$f.example" "$f"
  chmod 600 "$f"
  echo "Created deploy/aws/$f"
done

fill .env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
fill .env IC_AUTH_DB_PASSWORD "$(openssl rand -hex 24)"
fill .env IC_APP_DB_PASSWORD "$(openssl rand -hex 24)"
fill web.env BETTER_AUTH_SECRET "$(openssl rand -base64 48 | tr -d '\n=+/')"
admin_key="$(openssl rand -hex 32)"
grep -q '^ADMIN_DASHBOARD_KEY=$' backend.env && grep -q '^ADMIN_DASHBOARD_KEY=$' web.env && {
  fill backend.env ADMIN_DASHBOARD_KEY "$admin_key"
  fill web.env ADMIN_DASHBOARD_KEY "$admin_key"
  echo "Admin dashboard key (keep it in your password manager): $admin_key"
}

echo "Now edit the bucket names (and LLM keys) in backend.env, web.env and .env."
