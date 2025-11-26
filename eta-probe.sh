#!/bin/bash

# Simple wrapper to run ETA probe from project root.
# Usage examples:
#   ./eta-probe.sh --pair 3ngL...tWd --target 1.0
#   ./eta-probe.sh --token 132 --target 1.5
#   ./eta-probe.sh --help

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_BIN="$ROOT_DIR/server/venv/bin/python"
SCRIPT="$ROOT_DIR/eta_probe.py"

print_help() {
  cat << EOF
ETA Probe
Computes earliest seconds to reach +\$X profit from \$5 entry.

Options:
  --pair <pair_addr>     Token pair (e.g. 3ngLnB5E...)
  --token <id>           Token ID
  --target <usd>         Target profit in USD (default 1.0)
  --start-db             Try to start local Postgres before running
  -h, --help             Show this help

Examples:
  ./eta-probe.sh --pair 3ngLnB5EEam3SWx8GecfGQ2tALmGLgnMXdDNk6EtPtWd --target 1.0
  ./eta-probe.sh --token 21 --target 1.5
EOF
}

PAIR=""
TOKEN=""
TARGET="1.0"
START_DB=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pair)
      PAIR="$2"; shift 2;;
    --pair=*)
      PAIR="${1#*=}"; shift;;
    --token)
      TOKEN="$2"; shift 2;;
    --token=*)
      TOKEN="${1#*=}"; shift;;
    --target)
      TARGET="$2"; shift 2;;
    --target=*)
      TARGET="${1#*=}"; shift;;
    --start-db)
      START_DB=1; shift;;
    -h|--help)
      print_help; exit 0;;
    *)
      echo "Unknown option: $1" >&2; print_help; exit 1;;
  esac
done

if [[ -z "$PAIR" && -z "$TOKEN" ]]; then
  echo "Error: provide --pair or --token" >&2
  print_help
  exit 1
fi

if [[ $START_DB -eq 1 ]]; then
  if [[ -x "$ROOT_DIR/start-postgres.sh" ]]; then
    "$ROOT_DIR/start-postgres.sh" || true
  fi
fi

if [[ ! -x "$PY_BIN" ]]; then
  # fallback to python in PATH
  PY_BIN="python3"
fi

CMD=("$PY_BIN" "$SCRIPT")
if [[ -n "$PAIR" ]]; then
  CMD+=("--pair" "$PAIR")
fi
if [[ -n "$TOKEN" ]]; then
  CMD+=("--token" "$TOKEN")
fi
CMD+=("--target_usd" "$TARGET")

echo "Running: ${CMD[*]}" >&2
"${CMD[@]}"

