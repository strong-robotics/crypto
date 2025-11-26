#!/usr/bin/env python3
"""
Check wallet_history for recent buy/sell transactions.

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_wallet_history.py
"""
from __future__ import annotations

import asyncio
from _v3_db_pool import get_db_pool


async def check_wallet_history():
    """Check recent transactions in wallet_history."""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print("🔍 CHECKING WALLET_HISTORY FOR RECENT TRANSACTIONS")
        print("=" * 80)
        print()
        
        # Get all records ordered by most recent first
        all_history = await conn.fetch(
            """
            SELECT 
                h.id,
                h.wallet_id,
                h.token_id,
                h.entry_iteration,
                h.entry_amount_usd,
                h.entry_price_usd,
                h.entry_token_amount,
                h.entry_signature,
                h.exit_iteration,
                h.exit_amount_usd,
                h.exit_price_usd,
                h.exit_token_amount,
                h.exit_signature,
                h.profit_usd,
                h.outcome,
                h.reason,
                h.created_at,
                h.updated_at,
                t.name AS token_name,
                t.token_address
            FROM wallet_history h
            LEFT JOIN tokens t ON t.id = h.token_id
            ORDER BY h.id DESC
            LIMIT 50
            """
        )
        
        if not all_history:
            print("✅ No records in wallet_history")
            return
        
        print(f"📊 Found {len(all_history)} recent records (showing last 50):")
        print()
        
        for i, record in enumerate(all_history, 1):
            print(f"[{i}] History ID: {record['id']}")
            print(f"    Token: {record.get('token_name') or 'N/A'} (ID: {record['token_id']})")
            print(f"    Address: {record.get('token_address') or 'N/A'}")
            print(f"    Wallet ID: {record['wallet_id']}")
            print(f"    Entry: iter={record.get('entry_iteration')}, amount=${record.get('entry_amount_usd') or 0:.2f}, price=${record.get('entry_price_usd') or 0:.6f}, tokens={record.get('entry_token_amount') or 0:.6f}")
            if record.get('entry_signature'):
                print(f"    Entry signature: {record['entry_signature']}")
            
            if record.get('exit_iteration'):
                print(f"    Exit: iter={record.get('exit_iteration')}, amount=${record.get('exit_amount_usd') or 0:.2f}, price=${record.get('exit_price_usd') or 0:.6f}, tokens={record.get('exit_token_amount') or 0:.6f}")
                if record.get('exit_signature'):
                    print(f"    Exit signature: {record['exit_signature']}")
                print(f"    Profit: ${record.get('profit_usd') or 0:.2f}")
                print(f"    Outcome: {record.get('outcome') or 'N/A'}")
                print(f"    Reason: {record.get('reason') or 'N/A'}")
            else:
                print(f"    Status: ⏸️  OPEN POSITION (not sold yet)")
            
            print(f"    Created: {record.get('created_at')}")
            print(f"    Updated: {record.get('updated_at')}")
            print()
        
        # Summary
        open_positions = await conn.fetchval(
            "SELECT COUNT(*) FROM wallet_history WHERE exit_iteration IS NULL"
        )
        closed_positions = await conn.fetchval(
            "SELECT COUNT(*) FROM wallet_history WHERE exit_iteration IS NOT NULL"
        )
        total_profit = await conn.fetchval(
            "SELECT SUM(profit_usd) FROM wallet_history WHERE profit_usd IS NOT NULL"
        )
        
        # Check today's records
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        today_records = await conn.fetch(
            """
            SELECT id, token_id, wallet_id, entry_iteration, entry_amount_usd, 
                   entry_signature, exit_iteration, created_at
            FROM wallet_history
            WHERE DATE(created_at) = $1
            ORDER BY id DESC
            """,
            today
        )
        
        print("=" * 80)
        print(f"📅 RECORDS FROM TODAY ({today}): {len(today_records)}")
        if today_records:
            for r in today_records:
                status = "OPEN" if not r.get('exit_iteration') else "CLOSED"
                print(f"   ID={r['id']}, token_id={r['token_id']}, wallet_id={r['wallet_id']}, entry_iter={r['entry_iteration']}, amount=${r['entry_amount_usd']:.2f}, status={status}, created={r['created_at']}")
        print()
        
        # Check open positions
        open_pos = await conn.fetch(
            """
            SELECT id, token_id, wallet_id, entry_iteration, entry_amount_usd, 
                   entry_signature, created_at
            FROM wallet_history 
            WHERE exit_iteration IS NULL 
            ORDER BY id DESC
            """
        )
        print(f"⏸️  OPEN POSITIONS: {len(open_pos)}")
        if open_pos:
            for r in open_pos:
                print(f"   ID={r['id']}, token_id={r['token_id']}, wallet_id={r['wallet_id']}, entry_iter={r['entry_iteration']}, amount=${r['entry_amount_usd']:.2f}, signature={r.get('entry_signature') or 'N/A'}, created={r['created_at']}")
        print()
        
        print("=" * 80)
        print("📊 SUMMARY:")
        print(f"   Open positions: {open_positions or 0}")
        print(f"   Closed positions: {closed_positions or 0}")
        print(f"   Total profit: ${total_profit or 0:.2f}")
        print("=" * 80)


async def main():
    await check_wallet_history()


if __name__ == "__main__":
    asyncio.run(main())

