#!/usr/bin/env bash
# Run everything an analysis needs, together: the API (:8000), the worker and the web app
# (:3000), with each line labelled. Ctrl-C stops all three; if one stops, the others stop too.
# Without the worker, analyses stay "queued" and no step ever starts.
#   scripts/dev.sh        (make dev)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

db_reachable "$DEFAULT_ADMIN_URL" || {
  echo "PostgreSQL is not reachable on localhost:5432. Run 'make up' (and 'make migrate') first." >&2
  exit 1
}

set -m  # each part in its own process group, so stopping it stops its children as well
pids=()
stop() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

start() {  # start <label> <colour> <make target>
  local label
  label="$(printf '\033[%sm%-6s\033[0m| ' "$2" "$1")"
  (cd "$ROOT" && make --no-print-directory "$3" 2>&1 | sed -u "s/^/$label/") &
  pids+=("$!")
}

start api 36 api
start worker 35 worker
start web 32 web
wait -n || true
