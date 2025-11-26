#!/usr/bin/env bash
# Archive all tokens with history_ready=true into *_history tables.
# Usage: ./archive-history.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate server virtualenv if present (same pattern as other scripts)
if [ -f "$SCRIPT_DIR/server/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/server/venv/bin/activate"
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

pushd "$SCRIPT_DIR/server" >/dev/null
$PY -m _v3_token_archiver --all-ready "$@"
popd >/dev/null
