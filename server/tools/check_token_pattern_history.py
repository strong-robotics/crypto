#!/usr/bin/env python3
"""
Check token pattern history from ai_token_patterns table.

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_token_pattern_history.py <token_id>
"""
from __future__ import annotations

import asyncio
import sys
from _v3_db_pool import get_db_pool


async def check_token_pattern_history(token_id: int):
    """Check token pattern history."""
    pool = await get_db_pool()
    
    bad_patterns = ['rug_prequel', 'black_hole', 'flatliner', 'death_spike', 'smoke_bomb', 'mirage_rise', 'panic_sink', 'tug_of_war']
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print(f"🔍 CHECKING PATTERN HISTORY FOR TOKEN {token_id}")
        print("=" * 80)
        print()
        
        # 1. Get current pattern from tokens table
        token = await conn.fetchrow(
            """
            SELECT id, name, token_address, pattern_code, history_ready
            FROM tokens
            WHERE id=$1
            """,
            token_id
        )
        
        if not token:
            print(f"❌ Token {token_id} not found")
            return
        
        print(f"1️⃣ CURRENT PATTERN (from tokens table):")
        print(f"   Token: {token.get('name') or 'N/A'} (ID: {token['id']})")
        print(f"   Current pattern_code: {token.get('pattern_code') or 'N/A'}")
        print()
        
        # 2. Check pattern history from ai_token_patterns
        print(f"2️⃣ PATTERN HISTORY (from ai_token_patterns table):")
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
            had_bad = False
            for i, rec in enumerate(pattern_history, 1):
                pattern_code = rec.get('pattern_code', '').lower()
                is_bad = pattern_code in bad_patterns
                if is_bad:
                    had_bad = True
                status = "❌ BAD" if is_bad else "✅ GOOD"
                print(f"   [{i}] {rec.get('created_at')} - {rec.get('pattern_code')} ({rec.get('pattern_name')})")
                print(f"       Source: {rec.get('source')}, Confidence: {rec.get('confidence', 0):.2f}, Score: {rec.get('pattern_score')}")
                print(f"       Status: {status}")
                print()
            
            if had_bad:
                print(f"   ⚠️  TOKEN HAD BAD PATTERN IN HISTORY - WILL BE BLOCKED FOREVER")
            else:
                print(f"   ✅ Token never had bad pattern in history")
        else:
            print(f"   ✅ No pattern history found in ai_token_patterns (token may be new)")
        print()
        
        # 3. Check if token would be blocked by our new logic
        print(f"3️⃣ BLOCKING CHECK:")
        print("-" * 80)
        bad_pattern_check = await conn.fetchrow(
            """
            SELECT 1
            FROM ai_token_patterns atp
            JOIN ai_patterns ap ON ap.id = atp.pattern_id
            WHERE atp.token_id = $1
              AND LOWER(ap.code) = ANY($2)
            LIMIT 1
            """,
            token_id, bad_patterns
        )
        
        if bad_pattern_check:
            print(f"   ❌ TOKEN IS BLOCKED - Had bad pattern in history")
            print(f"   Reason: Token had one of these bad patterns: {', '.join(bad_patterns)}")
        else:
            print(f"   ✅ TOKEN IS NOT BLOCKED - No bad patterns in history")
        print()
        
        print("=" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/check_token_pattern_history.py <token_id>")
        sys.exit(1)
    
    try:
        token_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: Invalid token_id: {sys.argv[1]}")
        sys.exit(1)
    
    await check_token_pattern_history(token_id)


if __name__ == "__main__":
    asyncio.run(main())

