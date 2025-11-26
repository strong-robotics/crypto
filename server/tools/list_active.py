#!/usr/bin/env python3
"""
List active tokens (history_ready = false) with basic fields.

Usage:
  python -m server.tools.list_active
"""

import asyncio
import os
import asyncpg


async def list_active() -> int:
    host = os.getenv('DB_HOST', 'localhost')
    try:
        port = int(os.getenv('DB_PORT', '5433'))
    except Exception:
        port = 5433
    user = os.getenv('DB_USER', 'postgres')
    password = os.getenv('DB_PASSWORD', '')
    database = os.getenv('DB_NAME', 'crypto_db')

    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=database)
    try:
        rows = await conn.fetch(
            """
            SELECT t.id, t.token_address, COALESCE(t.name,'') AS name, 
                   COALESCE(wh.wallet_id, 0) AS wallet_id
            FROM tokens t
            LEFT JOIN wallet_history wh ON wh.token_id = t.id AND wh.exit_iteration IS NULL
            WHERE t.history_ready = FALSE
            ORDER BY COALESCE(wh.wallet_id, 999999), t.id
            LIMIT 500
            """
        )
        if not rows:
            print("No active tokens (history_ready=false) found.")
            return 0
        print(f"Active tokens: {len(rows)}")
        for r in rows:
            wid = int(r['wallet_id'] or 0)
            print(f"id={r['id']}, wallet={wid}, addr={r['token_address']}, name={r['name'] or 'Unknown'}")
        return 0
    finally:
        await conn.close()


def main() -> None:
    try:
        rc = asyncio.run(list_active())
    except KeyboardInterrupt:
        rc = 1
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
