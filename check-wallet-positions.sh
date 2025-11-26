#!/usr/bin/env bash
# Check wallet positions and token bindings in database.
#
# Usage:
#   ./check-wallet-positions.sh

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

cd "$SCRIPT_DIR/server" && PYTHONPATH=. $PY tools/check_wallet_positions.py

