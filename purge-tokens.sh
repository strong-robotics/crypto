#!/usr/bin/env bash
# Purge tokens (and related rows) by IDs. Wrapper for Python tool.
# Examples:
#   ./purge-tokens.sh --ids 22,126,353
#   ./purge-tokens.sh --ids 100-120 --dry-run
#   ./purge-tokens.sh --file ids.txt --yes

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

# Translate convenience flag '-live' to '--live'
ARGS=( )
for a in "$@"; do
  if [ "$a" = "-live" ]; then
    ARGS+=("--live")
  else
    ARGS+=("$a")
  fi
done

$PY -m server.tools.purge_tokens "${ARGS[@]}"
