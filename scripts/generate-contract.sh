#!/usr/bin/env bash
# The API contract: backend/openapi.json is generated from the FastAPI code, and the frontend's
# typed client (frontend/src/lib/api/schema.d.ts) is generated from that file. Both are committed.
#   scripts/generate-contract.sh           regenerate both
#   scripts/generate-contract.sh --check   fail if either is stale (used by CI)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

(cd "$BACKEND" && uv run --quiet python -m insightfulcell.api.export_openapi > "$TMP/openapi.json")

if [ "$CHECK" -eq 1 ]; then
  if ! diff -u "$BACKEND/openapi.json" "$TMP/openapi.json" >/dev/null; then
    echo "backend/openapi.json is stale. Run scripts/generate-contract.sh and commit the result." >&2
    diff -u "$BACKEND/openapi.json" "$TMP/openapi.json" | head -40 >&2 || true
    exit 1
  fi
  if command -v pnpm >/dev/null 2>&1 && [ -d "$FRONTEND/node_modules" ]; then
    (cd "$FRONTEND" && pnpm exec openapi-typescript "$BACKEND/openapi.json" -o "$TMP/schema.d.ts" >/dev/null)
    if ! diff -u "$FRONTEND/src/lib/api/schema.d.ts" "$TMP/schema.d.ts" >/dev/null; then
      echo "frontend/src/lib/api/schema.d.ts is stale. Run scripts/generate-contract.sh." >&2
      exit 1
    fi
  fi
  echo "API contract is up to date."
  exit 0
fi

cp "$TMP/openapi.json" "$BACKEND/openapi.json"
need pnpm "Install pnpm: corepack enable"
(cd "$FRONTEND" && pnpm exec openapi-typescript "$BACKEND/openapi.json" -o src/lib/api/schema.d.ts >/dev/null)
echo "Regenerated backend/openapi.json and frontend/src/lib/api/schema.d.ts."
