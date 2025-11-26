#!/usr/bin/env python3
"""
Check when a token's pattern changed from bad to good.

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_token_pattern_evolution.py <token_id>
"""
from __future__ import annotations

import asyncio
import sys
from _v3_db_pool import get_db_pool
from config import config
from ai.patterns.full_series_classifier import compute_full_features, choose_best_pattern


async def check_pattern_evolution(token_id: int):
    """Check at which second the token's pattern changed."""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print(f"🔍 CHECKING PATTERN EVOLUTION FOR TOKEN {token_id}")
        print("=" * 80)
        print()
        
        # Get token info
        token = await conn.fetchrow(
            """
            SELECT id, name, token_address, pattern_code, created_at
            FROM tokens
            WHERE id=$1
            """,
            token_id
        )
        
        if not token:
            print(f"❌ Token {token_id} not found in database")
            return
        
        print(f"1️⃣ TOKEN INFO:")
        print(f"   ID: {token['id']}")
        print(f"   Name: {token.get('name') or 'N/A'}")
        print(f"   Address: {token.get('token_address') or 'N/A'}")
        print(f"   Current pattern: {token.get('pattern_code') or 'N/A'}")
        print(f"   Created at: {token.get('created_at')}")
        print()
        
        # Get total iterations
        iterations = await conn.fetchval(
            """
            SELECT COUNT(*) FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
            """,
            token_id
        )
        iterations = int(iterations or 0)
        
        print(f"2️⃣ TOTAL ITERATIONS: {iterations}")
        print()
        
        # Check pattern at different seconds
        bad_patterns = ['rug_prequel', 'black_hole', 'flatliner', 'death_spike', 'smoke_bomb', 'mirage_rise', 'panic_sink', 'tug_of_war']
        AI_PREVIEW_ENTRY_SEC = int(getattr(config, 'AI_PREVIEW_ENTRY_SEC', 100))
        
        print(f"3️⃣ PATTERN EVOLUTION (checking every 5 seconds from {AI_PREVIEW_ENTRY_SEC}s to {iterations}s):")
        print("-" * 80)
        
        # Check pattern at multiple points: every 5 seconds from 100s to current iteration
        check_points = []
        start_sec = AI_PREVIEW_ENTRY_SEC
        end_sec = iterations
        
        for sec in range(start_sec, end_sec + 1, 5):
            check_points.append(sec)
        
        # Also check the exact current iteration if not already included
        if iterations not in check_points:
            check_points.append(iterations)
        
        pattern_history = []
        first_good_sec = None
        first_bad_sec = None
        
        for check_sec in check_points:
            try:
                check_rows = await conn.fetch(
                    """
                    SELECT usd_price, liquidity, mcap, holder_count, buy_count, sell_count
                    FROM token_metrics_seconds
                    WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
                    ORDER BY ts ASC
                    LIMIT $2
                    """,
                    token_id, check_sec
                )
                
                if check_rows and len(check_rows) >= 3:
                    series_at_check = {
                        "price": [float(r.get("usd_price") or 0.0) for r in check_rows],
                        "liquidity": [float(r.get("liquidity") or 0.0) for r in check_rows],
                        "mcap": [float(r.get("mcap") or 0.0) for r in check_rows],
                        "holders": [float(r.get("holder_count") or 0.0) for r in check_rows],
                        "buy_count": [float(r.get("buy_count") or 0.0) for r in check_rows],
                        "sell_count": [float(r.get("sell_count") or 0.0) for r in check_rows],
                    }
                    feats_at_check = compute_full_features(series_at_check)
                    pattern_at_check, conf_at_check = choose_best_pattern(feats_at_check)
                    
                    is_bad = pattern_at_check and pattern_at_check.lower() != 'unknown' and pattern_at_check.lower() in bad_patterns
                    is_good = pattern_at_check and pattern_at_check.lower() != 'unknown' and not is_bad
                    
                    pattern_history.append({
                        'sec': check_sec,
                        'pattern': pattern_at_check,
                        'confidence': conf_at_check,
                        'is_bad': is_bad,
                        'is_good': is_good
                    })
                    
                    status = "❌ BAD" if is_bad else ("✅ GOOD" if is_good else "❓ UNKNOWN")
                    print(f"   [{check_sec:4d}s] Pattern: {pattern_at_check or 'N/A':15s} (conf={conf_at_check:5.2f}) {status}")
                    
                    # Track first good pattern
                    if first_good_sec is None and is_good:
                        first_good_sec = check_sec
                    
                    # Track first bad pattern
                    if first_bad_sec is None and is_bad:
                        first_bad_sec = check_sec
                        
            except Exception as e:
                print(f"   [{check_sec:4d}s] ⚠️  Error: {e}")
        
        print()
        
        # Summary
        print("4️⃣ SUMMARY:")
        print("-" * 80)
        if first_bad_sec:
            print(f"   First bad pattern detected at: {first_bad_sec}s")
        if first_good_sec:
            print(f"   First good pattern detected at: {first_good_sec}s")
        
        # Find when it became rising_phoenix specifically
        rising_phoenix_sec = None
        for entry in pattern_history:
            if entry['pattern'] and entry['pattern'].lower() == 'rising_phoenix':
                rising_phoenix_sec = entry['sec']
                break
        
        if rising_phoenix_sec:
            print(f"   First 'rising_phoenix' pattern detected at: {rising_phoenix_sec}s")
        else:
            print(f"   ⚠️  'rising_phoenix' pattern not found in checked range")
        
        print()
        
        # Check pattern history from database
        print("5️⃣ PATTERN HISTORY FROM DATABASE (ai_token_patterns):")
        print("-" * 80)
        db_history = await conn.fetch(
            """
            SELECT 
                atp.created_at,
                ap.code AS pattern_code,
                ap.name AS pattern_name,
                atp.confidence,
                ap.score AS pattern_score
            FROM ai_token_patterns atp
            JOIN ai_patterns ap ON ap.id = atp.pattern_id
            WHERE atp.token_id = $1
            ORDER BY atp.created_at ASC
            """,
            token_id
        )
        
        if db_history:
            print(f"   Found {len(db_history)} records:")
            for i, rec in enumerate(db_history, 1):
                pattern_code = rec.get('pattern_code', '').lower()
                is_bad = pattern_code in bad_patterns
                status = "❌ BAD" if is_bad else ("✅ GOOD" if pattern_code != 'unknown' else "❓ UNKNOWN")
                print(f"   [{i}] {rec.get('created_at')} - {rec.get('pattern_code')} ({rec.get('pattern_name')})")
                print(f"       Confidence: {rec.get('confidence', 0):.2f}, Score: {rec.get('pattern_score')} {status}")
        else:
            print("   ✅ No pattern history in database")
        
        print()
        print("=" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/check_token_pattern_evolution.py <token_id>")
        sys.exit(1)
    
    try:
        token_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: Invalid token_id: {sys.argv[1]}")
        sys.exit(1)
    
    await check_pattern_evolution(token_id)


if __name__ == "__main__":
    asyncio.run(main())

