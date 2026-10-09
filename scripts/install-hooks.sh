#!/usr/bin/env bash
# Point git at the versioned hooks in scripts/hooks (no extra tooling, nothing to keep in sync).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"
git config core.hooksPath scripts/hooks
chmod +x scripts/hooks/*
echo "Git hooks installed: pre-commit (format, secrets), commit-msg (Conventional Commits), pre-push (full gate)."
