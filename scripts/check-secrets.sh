#!/usr/bin/env bash
# Look for credentials in files that would be committed. Uses gitleaks when installed (CI always
# does); otherwise falls back to a conservative pattern scan so the check is never silently off.
#   scripts/check-secrets.sh [--staged]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

STAGED=0
[ "${1:-}" = "--staged" ] && STAGED=1

printf '\n%s▶ Secret scan%s\n' "$BOLD" "$RESET"
if command -v gitleaks >/dev/null 2>&1; then
  if [ "$STAGED" -eq 1 ]; then
    gitleaks protect --staged --redact --no-banner
  else
    gitleaks detect --no-git --source . --redact --no-banner
  fi
  echo "gitleaks: clean"
  exit 0
fi

echo "(gitleaks not installed; using the built-in pattern scan. Install gitleaks for full coverage.)"
if git rev-parse --git-dir >/dev/null 2>&1; then
  if [ "$STAGED" -eq 1 ]; then files="$(git diff --cached --name-only --diff-filter=ACM)"
  else files="$(git ls-files --cached --others --exclude-standard)"; fi
else
  files="$(find . -type f -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/.venv/*' -not -path '*/.next/*')"
fi

PATTERNS='AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}|xox[baprs]-[A-Za-z0-9-]{10,}|re_[A-Za-z0-9]{24,}|GOCSPX-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}'
status=0
while IFS= read -r file; do
  [ -f "$file" ] || continue
  case "$file" in
    *.lock|*pnpm-lock.yaml|*.png|*.jpg|*.ico|*.woff2|scripts/check-secrets.sh) continue ;;
  esac
  if grep -InEH -- "$PATTERNS" "$file" >/dev/null 2>&1; then
    echo "Possible secret in: $file" >&2
    status=1
  fi
done <<< "$files"
# A committed .env (not .env.example) is always a mistake.
if printf '%s\n' "$files" | grep -E '(^|/)\.env(\.local|\.production)?$' >/dev/null; then
  echo "A .env file is tracked or about to be: keep it out of git." >&2
  status=1
fi
[ "$status" -eq 0 ] && echo "No secrets found."
exit "$status"
