#!/usr/bin/env python3
"""
Purge specified tokens (and related data) from PostgreSQL.

Usage:
  python -m server.tools.purge_tokens --ids 22,126,353
  python -m server.tools.purge_tokens --ids 22,126 --dry-run
  python -m server.tools.purge_tokens --file ids.txt --yes
  python -m server.tools.purge_tokens --live --yes    # purge all live tokens (from tokens table)

Behavior:
  - Deletes rows from token_metrics_seconds, trades, then tokens for the given IDs.
  - By default asks for confirmation; use --yes to skip prompt.
  - --dry-run only reports counts without deleting.
"""

import argparse
import asyncio
import sys
from typing import List, Tuple

import asyncpg

try:
    from server.db_config import POSTGRES_CONFIG
except Exception:
    # Fallback for direct module run
    sys.path.append('server')
    from db_config import POSTGRES_CONFIG  # type: ignore


def parse_ids(ids_str: str) -> List[int]:
    out: List[int] = []
    for part in ids_str.split(','):
        p = part.strip()
        if not p:
            continue
        if '-' in p:
            a, b = p.split('-', 1)
            try:
                lo = int(a)
                hi = int(b)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            try:
                out.append(int(p))
            except ValueError:
                continue
    # deduplicate, keep order
    seen = set()
    result: List[int] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


async def dry_summary(conn: asyncpg.Connection, ids: List[int]) -> Tuple[int, int, int, int]:
    if not ids:
        return (0, 0, 0, 0)
    ids_arr = ids
    tokens_exist = await conn.fetchval(
        "SELECT COUNT(*) FROM tokens WHERE id = ANY($1)", ids_arr
    )
    metrics = await conn.fetchval(
        "SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id = ANY($1)", ids_arr
    )
    trades = await conn.fetchval(
        "SELECT COUNT(*) FROM trades WHERE token_id = ANY($1)", ids_arr
    )
    return int(tokens_exist or 0), int(metrics or 0), int(trades or 0), len(ids)


async def purge(conn: asyncpg.Connection, ids: List[int]) -> Tuple[int, int, int]:
    if not ids:
        return (0, 0, 0)
    async with conn.transaction():
        # Detach wallets bound to these tokens (real trading only)
        try:
            await conn.execute(
                """
                UPDATE wallets
                SET active_token_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE active_token_id = ANY($1)
                """,
                ids,
            )
        except Exception:
            # wallets table may not exist in some environments; ignore
            pass
        m = await conn.execute(
            "DELETE FROM token_metrics_seconds WHERE token_id = ANY($1)", ids
        )
        t = await conn.execute(
            "DELETE FROM trades WHERE token_id = ANY($1)", ids
        )
        x = await conn.execute(
            "DELETE FROM tokens WHERE id = ANY($1)", ids
        )
    def _num(s: str) -> int:
        try:
            return int((s or '').split()[-1])
        except Exception:
            return 0
    return _num(m), _num(t), _num(x)


async def fetch_live_ids(conn: asyncpg.Connection) -> List[int]:
    # Archived tokens are in tokens_history table, so all tokens in tokens table are live
    rows = await conn.fetch(
        "SELECT id FROM tokens ORDER BY id ASC"
    )
    return [int(r['id']) for r in rows]


async def main_async(args):
    ids: List[int] = []
    if args.ids:
        ids.extend(parse_ids(args.ids))
    if args.file:
        with open(args.file, 'r') as f:
            for line in f:
                ids.extend(parse_ids(line.strip()))
    # Connect to DB for --live or if nothing provided
    cfg = POSTGRES_CONFIG.copy()
    cfg['database'] = 'crypto_db'
    cfg.pop('min_size', None)
    cfg.pop('max_size', None)
    conn = await asyncpg.connect(**cfg)
    try:
        if args.live:
            live_ids = await fetch_live_ids(conn)
            ids.extend(live_ids)
        ids = list(dict.fromkeys(ids))  # dedup, preserve order
        if not ids:
            print("No token IDs provided (and no active tokens found).")
            return 1

        tokens_exist, metrics_cnt, trades_cnt, total_requested = await dry_summary(conn, ids)
        print(f"Requested IDs: {total_requested}")
        print(f"Existing tokens among them: {tokens_exist}")
        print(f"Rows to delete → metrics: {metrics_cnt}, trades: {trades_cnt}")
        if args.dry_run:
            print("Dry-run: nothing deleted.")
            return 0
        if not args.yes:
            answer = input("Type 'yes' to confirm deletion: ").strip().lower()
            if answer != 'yes':
                print("Aborted.")
                return 1
        m, t, x = await purge(conn, ids)
        print(f"Deleted metrics={m}, trades={t}, tokens={x}")
        return 0
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser(description="Purge specified token IDs from DB (metrics → trades → tokens)")
    p.add_argument('--ids', type=str, help='Comma-separated list of IDs (ranges ok: 10-20)')
    p.add_argument('--file', type=str, help='Text file with IDs/ranges, one line or comma-separated')
    p.add_argument('--dry-run', action='store_true', help='Report counts only, do not delete')
    p.add_argument('--live', action='store_true', help='Purge all live tokens (from tokens table, archived tokens are in tokens_history)')
    p.add_argument('--yes', action='store_true', help='Do not ask for confirmation')
    args = p.parse_args()
    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        rc = 1
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
