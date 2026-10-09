#!/usr/bin/env bash
# Frontend quality gate: lint (incl. accessibility), format, types, tests, production build, audit.
#   scripts/check-frontend.sh [--fix] [--fast] [--fail-fast]
# --fast skips the production build and the dependency audit.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FIX=0; FAST=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;; --fast) FAST=1 ;; --fail-fast) FAIL_FAST=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

need node "Install Node.js 22 or newer: https://nodejs.org/"
need pnpm "Install pnpm: corepack enable && corepack prepare pnpm@10.28.0 --activate"
cd "$FRONTEND"
printf '%sFrontend checks%s (%s)\n' "$BOLD" "$RESET" "$FRONTEND"
pnpm install --frozen-lockfile --silent

if [ "$FIX" -eq 1 ]; then
  run_step "prettier (fixing)" pnpm exec prettier --write .
  run_step "eslint (fixing)" pnpm exec eslint . --fix --max-warnings 0
else
  run_step "prettier --check" pnpm exec prettier --check .
  run_step "eslint (next, typescript, jsx-a11y)" pnpm exec eslint . --max-warnings 0
fi
run_step "tsc --noEmit (strict)" pnpm exec tsc --noEmit
run_step "vitest (unit, components, axe, coverage)" pnpm exec vitest run --coverage
run_step "API types match backend/openapi.json" "$ROOT/scripts/generate-contract.sh" --check

if [ "$FAST" -eq 1 ]; then
  skip_step "next build" "--fast"
  skip_step "pnpm audit (high severity)" "--fast"
else
  run_step "next build" pnpm exec next build
  run_step "pnpm audit (high severity)" pnpm audit --audit-level high --prod
fi

print_summary
