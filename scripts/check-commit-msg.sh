#!/usr/bin/env bash
# Conventional Commits: "<type>(<scope>)?: <subject>" so history reads as a changelog.
#   scripts/check-commit-msg.sh <message-file>      (used by the commit-msg hook)
#   scripts/check-commit-msg.sh --range <git-range>  (used by CI on a pull request)
#   scripts/check-commit-msg.sh --text "<subject>"   (used by CI on the PR title)
set -euo pipefail

TYPES='feat|add|update|delete|fix|docs|style|refactor|perf|test|build|ci|chore|revert'
PATTERN="^(${TYPES})(\([a-z0-9._/-]+\))?!?: [^ ].{2,}$"
MAX=100

check_subject() {
  local subject="$1"
  case "$subject" in
    Merge\ *|Revert\ \"*|fixup!\ *|squash!\ *) return 0 ;;
  esac
  if [ "${#subject}" -gt "$MAX" ]; then
    echo "Subject is ${#subject} characters; keep it under $MAX: $subject" >&2; return 1
  fi
  if ! printf '%s' "$subject" | grep -Eq "$PATTERN"; then
    cat >&2 <<MSG
Commit subject does not follow Conventional Commits:
  $subject
Expected: <type>(<scope>): <what changed>   e.g.  feat(ingest): add CSV delimiter sniffing
Types: ${TYPES//|/, }
MSG
    return 1
  fi
}

case "${1:-}" in
  --range)
    status=0
    while IFS= read -r subject; do check_subject "$subject" || status=1; done \
      < <(git log --format=%s "$2")
    exit "$status" ;;
  --text) check_subject "$2" ;;
  "") echo "Usage: $0 <message-file> | --range <range> | --text <subject>" >&2; exit 2 ;;
  *) check_subject "$(grep -v '^#' "$1" | sed -n '/./{p;q;}')" ;;
esac
