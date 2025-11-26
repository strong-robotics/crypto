#!/usr/bin/env python3
"""
Check wallets status: free wallets, balances, entry amounts.

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_wallets_status.py
"""
from __future__ import annotations

import asyncio
import json
from _v3_db_pool import get_db_pool
from config import config
from solders.keypair import Keypair


async def check_wallets_status():
    """Check wallets status."""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print("🔍 CHECKING WALLETS STATUS")
        print("=" * 80)
        print()
        
        # Check wallets table
        wallets = await conn.fetch(
            """
            SELECT id, name, cash_usd, entry_amount_usd, active_token_id
            FROM wallets
            ORDER BY id ASC
            """
        )
        
        print(f"1️⃣ WALLETS TABLE: {len(wallets)} wallets")
        for w in wallets:
            enabled = "✅ ENABLED" if (w.get('entry_amount_usd') or 0) > 0 else "❌ DISABLED"
            print(f"   Wallet ID {w['id']}: {w.get('name') or 'N/A'}, cash=${w.get('cash_usd') or 0:.2f}, entry_amount=${w.get('entry_amount_usd') or 0:.2f} {enabled}")
        print()
        
        # Check keys.json
        try:
            with open(config.WALLET_KEYS_FILE) as f:
                keys = json.load(f)
            print(f"2️⃣ KEYS.JSON: {len(keys)} keys")
            for k in keys:
                key_id = k.get('id')
                keypair = Keypair.from_bytes(bytes(k["bits"]))
                pubkey = str(keypair.pubkey())
                print(f"   Key ID {key_id}: {pubkey[:20]}...")
            print()
        except Exception as e:
            print(f"2️⃣ KEYS.JSON: ❌ Error reading: {e}")
            print()
        
        # Check which wallets are bound to tokens
        bound_wallets = await conn.fetch(
            """
            SELECT DISTINCT wallet_id
            FROM tokens
            WHERE wallet_id IS NOT NULL
            """
        )
        bound_ids = {w['wallet_id'] for w in bound_wallets}
        print(f"3️⃣ BOUND WALLETS: {len(bound_ids)} wallets bound to tokens")
        if bound_ids:
            for wid in sorted(bound_ids):
                tokens = await conn.fetch(
                    "SELECT id, name FROM tokens WHERE wallet_id=$1",
                    wid
                )
                print(f"   Wallet ID {wid}: bound to {len(tokens)} token(s)")
                for t in tokens:
                    print(f"      - Token ID {t['id']}: {t.get('name') or 'N/A'}")
        print()
        
        # Check free wallets (not bound and entry_amount_usd > 0)
        free_wallets = await conn.fetch(
            """
            SELECT w.id, w.entry_amount_usd
            FROM wallets w
            WHERE w.entry_amount_usd IS NOT NULL 
              AND w.entry_amount_usd > 0
              AND NOT EXISTS (
                  SELECT 1 FROM tokens t WHERE t.wallet_id = w.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM wallet_history h 
                  WHERE h.wallet_id = w.id AND h.exit_iteration IS NULL
              )
            ORDER BY w.id ASC
            """
        )
        print(f"4️⃣ FREE WALLETS (available for force buy): {len(free_wallets)}")
        for w in free_wallets:
            print(f"   Wallet ID {w['id']}: entry_amount=${w['entry_amount_usd']:.2f}")
        print()
        
        print("=" * 80)


async def main():
    await check_wallets_status()


if __name__ == "__main__":
    asyncio.run(main())

