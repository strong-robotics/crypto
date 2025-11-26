#!/usr/bin/env bash
set -euo pipefail

echo "🧹 Resetting database tables (tokens, trades, token_metrics_seconds)..."

# Pick a Python interpreter that can import server/cli.py
PY=""
if [[ -x "./.venv/bin/python" ]]; then
  PY="./.venv/bin/python"
elif [[ -x "server/venv/bin/python" ]]; then
  PY="server/venv/bin/python"
else
  PY="python3"
fi

# Truncate main tables via existing CLI helper
$PY server/cli.py db reset --confirm yes

echo "✅ TRUNCATE completed. Verifying counts..."

$PY - <<'PY'
import asyncio, sys
sys.path.append('server')
from _v3_db_pool import get_db_pool

async def main():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tokens = await conn.fetchval('SELECT COUNT(*) FROM tokens')
        trades = await conn.fetchval('SELECT COUNT(*) FROM trades')
        metrics = await conn.fetchval('SELECT COUNT(*) FROM token_metrics_seconds')
        print(f"tokens={tokens}, trades={trades}, metrics_seconds={metrics}")

asyncio.run(main())
PY

echo "Done."

