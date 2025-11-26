#!/usr/bin/env python3
"""
Check wallet positions and token bindings in database.

This script shows:
- All tokens with wallet_id set
- All open positions in wallet_history
- All closed positions in wallet_history
- Mismatches between tokens.wallet_id and wallet_history

Usage (run from project root):
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_wallet_positions.py
"""
from __future__ import annotations

import asyncio
from _v3_db_pool import get_db_pool


async def check_wallet_positions():
    """Check wallet positions and token bindings."""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print("🔍 CHECKING WALLET POSITIONS AND TOKEN BINDINGS")
        print("=" * 80)
        print()
        
        # 1. Check tokens with wallet_id
        print("1️⃣ TOKENS WITH wallet_id SET:")
        print("-" * 80)
        tokens_with_wallet = await conn.fetch(
            """
            SELECT t.id, t.wallet_id, t.name, t.token_address, t.history_ready
            FROM tokens t
            WHERE t.wallet_id IS NOT NULL
            ORDER BY t.id DESC
            """
        )
        if tokens_with_wallet:
            print(f"   Found {len(tokens_with_wallet)} tokens with wallet_id:")
            for t in tokens_with_wallet:
                print(f"   - Token ID: {t['id']}, wallet_id: {t['wallet_id']}, name: {t.get('name') or 'N/A'}, history_ready: {t.get('history_ready')}")
        else:
            print("   ✅ No tokens with wallet_id found")
        print()
        
        # 2. Check ALL records in wallet_history first
        print("2️⃣ ALL RECORDS IN wallet_history:")
        print("-" * 80)
        all_history = await conn.fetch(
            """
            SELECT COUNT(*) as total_count
            FROM wallet_history
            """
        )
        total_history = all_history[0]['total_count'] if all_history else 0
        print(f"   Total records in wallet_history: {total_history}")
        print()
        
        # 2b. Check open positions in wallet_history
        print("2️⃣ OPEN POSITIONS IN wallet_history (exit_iteration IS NULL):")
        print("-" * 80)
        open_positions = await conn.fetch(
            """
            SELECT h.id, h.wallet_id, h.token_id, h.entry_iteration, h.entry_amount_usd,
                   t.name AS token_name, t.token_address, t.wallet_id AS token_wallet_id
            FROM wallet_history h
            LEFT JOIN tokens t ON t.id = h.token_id
            WHERE h.exit_iteration IS NULL
            ORDER BY h.id DESC
            """
        )
        if open_positions:
            print(f"   Found {len(open_positions)} open positions:")
            for pos in open_positions:
                token_wallet_match = "✅" if pos['token_wallet_id'] == pos['wallet_id'] else "❌ MISMATCH"
                print(f"   - History ID: {pos['id']}, wallet_id: {pos['wallet_id']}, token_id: {pos['token_id']}")
                print(f"     Token wallet_id: {pos['token_wallet_id']} {token_wallet_match}")
                print(f"     Entry: iter={pos['entry_iteration']}, amount=${pos['entry_amount_usd']:.2f}")
                print(f"     Token: {pos.get('token_name') or 'N/A'} ({pos.get('token_address') or 'N/A'})")
                print()
        else:
            print("   ✅ No open positions found")
        print()
        
        # 3. Check closed positions in wallet_history
        print("3️⃣ CLOSED POSITIONS IN wallet_history (exit_iteration IS NOT NULL):")
        print("-" * 80)
        closed_positions = await conn.fetch(
            """
            SELECT h.id, h.wallet_id, h.token_id, h.entry_iteration, h.exit_iteration,
                   h.entry_amount_usd, h.exit_amount_usd, h.profit_usd,
                   t.name AS token_name, t.token_address, t.wallet_id AS token_wallet_id
            FROM wallet_history h
            LEFT JOIN tokens t ON t.id = h.token_id
            WHERE h.exit_iteration IS NOT NULL
            ORDER BY h.id DESC
            LIMIT 20
            """
        )
        if closed_positions:
            print(f"   Found {len(closed_positions)} closed positions (showing last 20):")
            for pos in closed_positions:
                token_wallet_match = "✅" if pos['token_wallet_id'] is None else f"❌ STILL BOUND (wallet_id={pos['token_wallet_id']})"
                print(f"   - History ID: {pos['id']}, wallet_id: {pos['wallet_id']}, token_id: {pos['token_id']}")
                print(f"     Token wallet_id: {pos['token_wallet_id']} {token_wallet_match}")
                print(f"     Entry: iter={pos['entry_iteration']}, amount=${pos['entry_amount_usd']:.2f}")
                print(f"     Exit: iter={pos['exit_iteration']}, amount=${pos['exit_amount_usd']:.2f}, profit=${pos.get('profit_usd') or 0:.2f}")
                print(f"     Token: {pos.get('token_name') or 'N/A'} ({pos.get('token_address') or 'N/A'})")
                print()
        else:
            print("   ✅ No closed positions found")
        print()
        
        # 4. Check what wallets table shows
        print("4️⃣ WALLETS TABLE (checking active_token_id if exists):")
        print("-" * 80)
        try:
            wallets_info = await conn.fetch(
                """
                SELECT id, name, cash_usd, entry_amount_usd, active_token_id
                FROM wallets
                ORDER BY id ASC
                """
            )
            if wallets_info:
                print(f"   Found {len(wallets_info)} wallets:")
                for w in wallets_info:
                    active_info = f"active_token_id={w.get('active_token_id')}" if w.get('active_token_id') else "active_token_id=NULL"
                    print(f"   - Wallet ID: {w['id']}, name: {w.get('name') or 'N/A'}, cash=${w.get('cash_usd') or 0:.2f}, {active_info}")
            else:
                print("   ✅ No wallets found in wallets table")
        except Exception as e:
            print(f"   ⚠️  Error checking wallets table: {e} (table might not exist or have different structure)")
        print()
        
        # 5. Check mismatches: closed positions but token still has wallet_id
        print("5️⃣ MISMATCHES: Closed positions but token still has wallet_id:")
        print("-" * 80)
        mismatches = await conn.fetch(
            """
            SELECT h.id AS history_id, h.wallet_id, h.token_id, h.exit_iteration,
                   t.name AS token_name, t.token_address, t.wallet_id AS token_wallet_id
            FROM wallet_history h
            LEFT JOIN tokens t ON t.id = h.token_id
            WHERE h.exit_iteration IS NOT NULL
              AND t.wallet_id IS NOT NULL
              AND t.wallet_id = h.wallet_id
            ORDER BY h.id DESC
            """
        )
        if mismatches:
            print(f"   ⚠️  Found {len(mismatches)} mismatches (should be cleared):")
            for m in mismatches:
                print(f"   - History ID: {m['history_id']}, wallet_id: {m['wallet_id']}, token_id: {m['token_id']}")
                print(f"     Token wallet_id: {m['token_wallet_id']} (SHOULD BE NULL)")
                print(f"     Exit iteration: {m['exit_iteration']}")
                print(f"     Token: {m.get('token_name') or 'N/A'} ({m.get('token_address') or 'N/A'})")
                print()
        else:
            print("   ✅ No mismatches found")
        print()
        
        # 6. Check open positions without token wallet_id
        print("6️⃣ OPEN POSITIONS but token.wallet_id IS NULL:")
        print("-" * 80)
        open_without_binding = await conn.fetch(
            """
            SELECT h.id AS history_id, h.wallet_id, h.token_id, h.entry_iteration,
                   t.name AS token_name, t.token_address, t.wallet_id AS token_wallet_id
            FROM wallet_history h
            JOIN tokens t ON t.id = h.token_id
            WHERE h.exit_iteration IS NULL
              AND t.wallet_id IS NULL
            ORDER BY h.id DESC
            """
        )
        if open_without_binding:
            print(f"   ⚠️  Found {len(open_without_binding)} open positions without token.wallet_id:")
            for m in open_without_binding:
                print(f"   - History ID: {m['history_id']}, wallet_id: {m['wallet_id']}, token_id: {m['token_id']}")
                print(f"     Token wallet_id: NULL (SHOULD BE {m['wallet_id']})")
                print(f"     Entry iteration: {m['entry_iteration']}")
                print(f"     Token: {m.get('token_name') or 'N/A'} ({m.get('token_address') or 'N/A'})")
                print()
        else:
            print("   ✅ No open positions without binding found")
        print()
        
        print("=" * 80)
        print("✅ Check completed!")
        print("=" * 80)


async def main():
    """Main entry point."""
    await check_wallet_positions()


if __name__ == "__main__":
    asyncio.run(main())

