#!/usr/bin/env bash
# Clear wallet bindings for tokens where positions are closed.
# This script finds all tokens with wallet_id set and clears the binding
# if the position is closed (exit_iteration IS NOT NULL in wallet_history).
#
# Usage:
#   ./clear-wallet-bindings.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Try to use the same venv as start.sh (server/venv)
if [ -f "$SCRIPT_DIR/server/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/server/venv/bin/activate"
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

cd "$SCRIPT_DIR/server" && PYTHONPATH=. $PY tools/clear_wallet_bindings.py

