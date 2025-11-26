#!/usr/bin/env python3
"""
Check token entry timing vs pattern changes.

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_token_timing.py <token_id>
"""
from __future__ import annotations

import asyncio
import sys
from _v3_db_pool import get_db_pool


async def check_token_timing(token_id: int):
    """Check when token was entered vs when pattern became bad."""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print(f"🔍 CHECKING TIMING FOR TOKEN {token_id}")
        print("=" * 80)
        print()
        
        # 1. Get entry time from wallet_history
        entry = await conn.fetchrow(
            """
            SELECT 
                id,
                wallet_id,
                entry_iteration,
                entry_amount_usd,
                created_at,
                exit_iteration
            FROM wallet_history
            WHERE token_id=$1
            ORDER BY id DESC
            LIMIT 1
            """,
            token_id
        )
        
        if entry:
            print(f"1️⃣ ENTRY IN wallet_history:")
            print(f"   History ID: {entry['id']}")
            print(f"   Wallet ID: {entry['wallet_id']}")
            print(f"   Entry iteration: {entry['entry_iteration']}")
            print(f"   Entry amount: ${entry['entry_amount_usd']:.2f}")
            print(f"   Entry time: {entry['created_at']}")
            print(f"   Exit iteration: {entry.get('exit_iteration') or 'NULL (still open)'}")
            print()
            
            entry_iteration = entry.get('entry_iteration')
            if entry_iteration:
                print(f"   ⚠️  Token was entered at iteration {entry_iteration}")
        else:
            print(f"1️⃣ NO ENTRY FOUND in wallet_history")
            print()
        
        # 2. Get pattern history with timestamps
        print(f"2️⃣ PATTERN HISTORY (with timestamps):")
        print("-" * 80)
        pattern_history = await conn.fetch(
            """
            SELECT 
                atp.id,
                atp.source,
                atp.confidence,
                atp.created_at,
                ap.code AS pattern_code,
                ap.name AS pattern_name,
                ap.score AS pattern_score
            FROM ai_token_patterns atp
            JOIN ai_patterns ap ON ap.id = atp.pattern_id
            WHERE atp.token_id = $1
            ORDER BY atp.created_at ASC
            """,
            token_id
        )
        
        if pattern_history:
            print(f"   Found {len(pattern_history)} pattern records:")
            bad_patterns = ['rug_prequel', 'black_hole', 'flatliner', 'death_spike', 'smoke_bomb', 'mirage_rise', 'panic_sink', 'tug_of_war']
            for i, rec in enumerate(pattern_history, 1):
                pattern_code = rec.get('pattern_code', '').lower()
                is_bad = pattern_code in bad_patterns
                status = "❌ BAD" if is_bad else "✅ GOOD"
                created_at = rec.get('created_at')
                
                print(f"   [{i}] {created_at} - {rec.get('pattern_code')} ({rec.get('pattern_name')})")
                print(f"       Source: {rec.get('source')}, Confidence: {rec.get('confidence', 0):.2f}, Score: {rec.get('pattern_score')}")
                print(f"       Status: {status}")
                
                # Compare with entry time
                if entry and entry.get('created_at') and created_at:
                    if created_at < entry['created_at']:
                        print(f"       ⚠️  Pattern was set BEFORE entry (entry was at {entry['created_at']})")
                    elif created_at > entry['created_at']:
                        print(f"       ⚠️  Pattern was set AFTER entry (entry was at {entry['created_at']})")
                    else:
                        print(f"       ℹ️  Pattern was set at the same time as entry")
                print()
        else:
            print(f"   ✅ No pattern history found in ai_token_patterns")
        print()
        
        # 3. Check current pattern
        token = await conn.fetchrow(
            """
            SELECT pattern_code, created_at
            FROM tokens
            WHERE id=$1
            """,
            token_id
        )
        
        if token:
            print(f"3️⃣ CURRENT PATTERN:")
            print(f"   Current pattern_code: {token.get('pattern_code') or 'N/A'}")
            print(f"   Token created_at: {token.get('created_at')}")
            print()
        
        print("=" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/check_token_timing.py <token_id>")
        sys.exit(1)
    
    try:
        token_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: Invalid token_id: {sys.argv[1]}")
        sys.exit(1)
    
    await check_token_timing(token_id)


if __name__ == "__main__":
    asyncio.run(main())

