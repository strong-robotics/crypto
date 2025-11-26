#!/bin/bash
# Strict early-entry predictor (first 15 seconds -> +20% in 60s)

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_BIN="$ROOT_DIR/server/venv/bin/python"
SCRIPT="$ROOT_DIR/early_predict.py"

if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN=python3
fi

echo "Running: $PY_BIN $SCRIPT $*" >&2
"$PY_BIN" "$SCRIPT" "$@"

