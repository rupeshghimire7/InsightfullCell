#!/usr/bin/env bash
# One-time setup of a development machine, safe to re-run.
#   scripts/bootstrap.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need node "Install Node.js 22 or newer: https://nodejs.org"
need pnpm "Enable it with: corepack enable (or: corepack enable --install-directory ~/.local/bin pnpm)"
if [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
  echo "${RED}Node.js 22 or newer is required${RESET} (found $(node --version)): https://nodejs.org" >&2
  exit 1
fi
command -v docker >/dev/null 2>&1 || echo "${YELLOW}Docker not found:${RESET} 'make up' needs it for PostgreSQL and S3 storage." >&2

echo "• backend dependencies"; (cd "$BACKEND" && uv sync)
echo "• frontend dependencies"; (cd "$FRONTEND" && pnpm install --frozen-lockfile)

for pair in "backend/.env.example:backend/.env" "frontend/.env.example:frontend/.env.local"; do
  src="$ROOT/${pair%%:*}"; dst="$ROOT/${pair##*:}"
  if [ -f "$dst" ]; then echo "• ${pair##*:} already exists (left alone)"; continue; fi
  cp "$src" "$dst"
  # A usable local secret, generated per machine, so nobody runs with a shared default.
  if grep -q '^BETTER_AUTH_SECRET=' "$dst"; then
    secret="$(node -e 'process.stdout.write(require("crypto").randomBytes(32).toString("base64url"))')"
    sed -i.bak "s|^BETTER_AUTH_SECRET=.*|BETTER_AUTH_SECRET=$secret|" "$dst" && rm -f "$dst.bak"
  fi
  echo "• created ${pair##*:}"
done

if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then "$ROOT/scripts/install-hooks.sh"; fi

cat <<MSG

Next:
  make up        start PostgreSQL and S3 storage (needs Docker)
  make migrate   create the database schema
  make api       run the API      (terminal 1)
  make web       run the web app  (terminal 2)  -> http://localhost:3000
Everything optional (Google sign-in, real email) is listed in SETUP_MANUAL.md.
MSG
