#!/usr/bin/env bash
# Everything CI runs, locally. Run it before you push.
#   scripts/check.sh [--backend] [--frontend] [--e2e] [--fast] [--fix] [--fail-fast]
# With no area flag, both backend and frontend are checked. --e2e adds the browser journey.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

AREAS=""; PASS_THROUGH=(); E2E=0
for arg in "$@"; do
  case "$arg" in
    --backend) AREAS="$AREAS backend" ;;
    --frontend) AREAS="$AREAS frontend" ;;
    --e2e) E2E=1 ;;
    --fast|--fix|--fail-fast) PASS_THROUGH+=("$arg") ;;
    -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done
[ -z "$AREAS" ] && AREAS="backend frontend"

STATUS=0
for area in $AREAS; do
  # ${arr[@]+"${arr[@]}"} keeps bash 3.2 happy when the array is empty under `set -u`.
  "$ROOT/scripts/check-$area.sh" ${PASS_THROUGH[@]+"${PASS_THROUGH[@]}"} || STATUS=1
done
if [ "$E2E" -eq 1 ]; then
  "$ROOT/scripts/e2e.sh" || STATUS=1
fi
"$ROOT/scripts/check-secrets.sh" || STATUS=1

if [ "$STATUS" -ne 0 ]; then
  printf '\n%sNot ready to push.%s\n' "$RED" "$RESET"
  exit 1
fi
printf '\n%sReady to push.%s CI runs these same scripts.\n' "$GREEN" "$RESET"
