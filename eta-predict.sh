#!/bin/bash

# Wrapper to run predictive ETA (ML) from project root
# Examples:
#   ./eta-predict.sh --token 132
#   ./eta-predict.sh --pair 3ngL...tWd --prob 0.6

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_BIN="$ROOT_DIR/server/venv/bin/python"
SCRIPT="$ROOT_DIR/eta_predict.py"

if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi

echo "Running: $PY_BIN $SCRIPT $*" >&2
"$PY_BIN" "$SCRIPT" "$@"

