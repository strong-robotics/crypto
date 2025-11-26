#!/usr/bin/env python3
"""
Utility helpers to archive finished tokens into *_history tables.
Moves data from tokens table to tokens_history table when tokens are ready to be archived.
Archived tokens are no longer in the tokens table (live tokens only).
"""

import asyncio
from typing import Optional, Dict, Any

from _v3_db_pool import get_db_pool


async def archive_token(token_id: int, *, conn=None) -> Dict[str, Any]:
    """
    Copy token + related metrics/trades into history tables and remove from live tables.
    Safe to call multiple times (subsequent calls become no-ops once token is archived).
    
    CRITICAL: This function checks for open positions before archiving to prevent
    archiving tokens with active investments.
    
    IMPORTANT: Always uses a transaction to ensure atomicity of archive operations.
    If conn is provided and already in a transaction, creates a savepoint (nested transaction).
    """
    pool = None
    own_connection = False
    if conn is None:
        pool = await get_db_pool()
        conn = await pool.acquire()
        own_connection = True
    
    # Always use transaction to ensure atomicity of archive operations
    # If conn is already in a transaction, this creates a savepoint (nested transaction)
    # If conn is not in a transaction, this creates a new transaction
    try:
        async with conn.transaction():
            return await _archive_token_impl(conn, token_id)
    finally:
        if own_connection and pool:
            await pool.release(conn)


async def _archive_token_impl(conn, token_id: int) -> Dict[str, Any]:
    """Internal implementation of archive_token (without transaction management)."""
    # CRITICAL: Check for open position before archiving
    # Never archive tokens with open positions (user has real money invested)
    open_pos_check = await conn.fetchrow(
        """
        SELECT id FROM wallet_history
        WHERE token_id=$1 AND exit_iteration IS NULL
        LIMIT 1
        """,
        token_id
    )
    if open_pos_check:
        return {"success": False, "message": "Cannot archive token with open position", "token_id": token_id}
    
    token_row = await conn.fetchrow("SELECT id FROM tokens WHERE id=$1", token_id)
    if not token_row:
        return {"success": False, "message": "Token already archived or not found", "token_id": token_id}

    # Copy token - use only columns that exist in both tokens and tokens_history
    # Get column names from both tables and find intersection
    tokens_columns = {row['column_name'] for row in await conn.fetch(
        """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'tokens' 
          AND column_name != 'archived_at'
        """
    )}
    history_columns = {row['column_name'] for row in await conn.fetch(
        """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'tokens_history'
        """
    )}
    
    # Find columns that exist in both tables
    common_columns = sorted(tokens_columns.intersection(history_columns))
    
    if common_columns:
        # Build column list dynamically using only common columns
        columns_str = ', '.join(common_columns)
        await conn.execute(
            f"""
            INSERT INTO tokens_history ({columns_str}, archived_at)
            SELECT {columns_str}, CURRENT_TIMESTAMP
            FROM tokens
            WHERE id = $1
            ON CONFLICT (id) DO UPDATE SET archived_at = CURRENT_TIMESTAMP
            """,
            token_id,
        )
    else:
        # Fallback: use SELECT * if we can't get column list
        await conn.execute(
            """
            INSERT INTO tokens_history
            SELECT t.*, CURRENT_TIMESTAMP
            FROM tokens t
            WHERE t.id = $1
            ON CONFLICT (id) DO UPDATE SET archived_at = CURRENT_TIMESTAMP
            """,
            token_id,
        )

    # Copy metrics/trades
    metrics_count = await conn.fetchval("SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id=$1", token_id) or 0
    trades_count = await conn.fetchval("SELECT COUNT(*) FROM trades WHERE token_id=$1", token_id) or 0

    if metrics_count:
        await conn.execute(
            """
            INSERT INTO token_metrics_seconds_history
            SELECT m.*, CURRENT_TIMESTAMP
            FROM token_metrics_seconds m
            WHERE m.token_id = $1
            ON CONFLICT (id) DO NOTHING
            """,
            token_id,
        )
    if trades_count:
        await conn.execute(
            """
            INSERT INTO trades_history
            SELECT tr.*, CURRENT_TIMESTAMP
            FROM trades tr
            WHERE tr.token_id = $1
            ON CONFLICT (id) DO NOTHING
            """,
            token_id,
        )

    # Remove from hot tables
    deleted_metrics = await conn.fetchval(
        "WITH d AS (DELETE FROM token_metrics_seconds WHERE token_id=$1 RETURNING 1) SELECT COUNT(*) FROM d",
        token_id,
    ) or 0
    deleted_trades = await conn.fetchval(
        "WITH d AS (DELETE FROM trades WHERE token_id=$1 RETURNING 1) SELECT COUNT(*) FROM d",
        token_id,
    ) or 0
    deleted_tokens = await conn.execute("DELETE FROM tokens WHERE id=$1", token_id)
    
    # Verify token was deleted
    token_still_exists = await conn.fetchval("SELECT id FROM tokens WHERE id=$1", token_id)
    if token_still_exists:
        raise Exception(f"Token {token_id} still exists in tokens table after DELETE")

    return {
        "success": True,
        "token_id": token_id,
        "moved_metrics": metrics_count,
        "moved_trades": trades_count,
        "deleted_metrics": deleted_metrics,
        "deleted_trades": deleted_trades,
        "deleted_tokens": deleted_tokens,
    }


async def archive_tokens(token_ids: list[int]) -> Dict[str, Any]:
    """Helper to archive multiple tokens sequentially."""
    total = 0
    ok = 0
    failed = 0
    details = []
    for tid in token_ids:
        total += 1
        try:
            res = await archive_token(tid)
            if res.get("success"):
                ok += 1
            else:
                failed += 1
            details.append(res)
        except Exception as exc:
            failed += 1
            details.append({"success": False, "token_id": tid, "error": str(exc)})
    return {"total": total, "success": ok, "failed": failed, "details": details[:10]}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Archive tokens (moves to tokens_history)")
    parser.add_argument("--token-id", type=int, help="Specific token id to archive")
    parser.add_argument("--all", action="store_true", help="Archive all tokens from tokens table (WARNING: use with caution)")
    args = parser.parse_args()

    async def _main():
        if args.token_id:
            res = await archive_token(args.token_id)
            print(res)
        elif args.all:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT id FROM tokens")
            ids = [r["id"] for r in rows]
            res = await archive_tokens(ids)
            print(res)
        else:
            print("Specify --token-id or --all")

    asyncio.run(_main())
