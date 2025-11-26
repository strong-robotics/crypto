#!/usr/bin/env python3
"""
Check why a specific token was not entered (auto-buy skipped).

Usage:
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/check_token_entry.py <token_id>
"""
from __future__ import annotations

import asyncio
import sys
from _v3_db_pool import get_db_pool
from config import config
from ai.patterns.catalog import PATTERN_SEED


async def check_token_entry(token_id: int):
    """Check why token was not entered."""
    pool = await get_db_pool()
    
    # Build pattern score map
    pattern_score_map = {}
    for item in PATTERN_SEED:
        code = item.get('code')
        score = int(item.get('score', 0) or 0)
        if code is None:
            continue
        code_str = getattr(code, 'value', str(code))
        if code_str.strip().lower() == 'unknown':
            score = 0
        pattern_score_map[code_str] = score
    
    AUTO_BUY_ENTRY_SEC = int(getattr(config, 'AUTO_BUY_ENTRY_SEC', 80))
    AI_PREVIEW_ENTRY_SEC = int(getattr(config, 'AI_PREVIEW_ENTRY_SEC', 60))
    PATTERN_MIN_SCORE = int(getattr(config, 'PATTERN_MIN_SCORE', 80))
    bad_patterns = ['rug_prequel', 'black_hole', 'flatliner', 'death_spike', 'smoke_bomb', 'mirage_rise', 'panic_sink', 'tug_of_war']
    
    async with pool.acquire() as conn:
        print("=" * 80)
        print(f"🔍 CHECKING TOKEN {token_id} FOR AUTO-BUY CONDITIONS")
        print("=" * 80)
        print()
        
        # 1. Get token info
        token = await conn.fetchrow(
            """
            SELECT id, name, token_address, pattern_code, history_ready, wallet_id
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
        print(f"   Pattern: {token.get('pattern_code') or 'N/A'}")
        print(f"   history_ready: {token.get('history_ready')}")
        print(f"   wallet_id: {token.get('wallet_id')}")
        print()
        
        # 2. Check iterations
        iterations = await conn.fetchval(
            """
            SELECT COUNT(*) FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
            """,
            token_id
        )
        iterations = int(iterations or 0)
        
        print(f"2️⃣ ITERATIONS:")
        print(f"   Current iterations: {iterations}")
        print(f"   AUTO_BUY_ENTRY_SEC: {AUTO_BUY_ENTRY_SEC}")
        print(f"   AI_PREVIEW_ENTRY_SEC: {AI_PREVIEW_ENTRY_SEC}")
        print(f"   ✅ Iterations >= AUTO_BUY_ENTRY_SEC: {iterations >= AUTO_BUY_ENTRY_SEC}")
        if iterations < AUTO_BUY_ENTRY_SEC:
            print(f"   ⚠️  Token has not reached AUTO_BUY_ENTRY_SEC yet (needs {AUTO_BUY_ENTRY_SEC - iterations} more iterations)")
        print()
        
        # 3. Check open position
        open_position = await conn.fetchrow(
            """
            SELECT id, wallet_id, entry_iteration, entry_amount_usd
            FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            LIMIT 1
            """,
            token_id
        )
        
        print(f"3️⃣ OPEN POSITION:")
        if open_position:
            print(f"   ❌ Open position exists: history_id={open_position['id']}, wallet_id={open_position['wallet_id']}")
            print(f"   Entry iteration: {open_position.get('entry_iteration')}")
            print(f"   Entry amount: ${open_position.get('entry_amount_usd') or 0:.2f}")
        else:
            print(f"   ✅ No open position (can enter)")
        print()
        
        # 4. Check enabled wallets
        enabled_wallets = await conn.fetchval(
            "SELECT COUNT(*) FROM wallets WHERE entry_amount_usd IS NOT NULL AND entry_amount_usd > 0"
        )
        enabled_wallets = int(enabled_wallets or 0)
        
        print(f"4️⃣ ENABLED WALLETS:")
        print(f"   Enabled wallets (entry_amount_usd > 0): {enabled_wallets}")
        if enabled_wallets == 0:
            print(f"   ⚠️  All wallets are disabled (entry_amount_usd = 0)")
        print()
        
        # 5. Check pattern
        pattern_code = token.get('pattern_code')
        print(f"5️⃣ PATTERN CHECK:")
        if pattern_code:
            pattern_lower = pattern_code.lower()
            pattern_score = int(pattern_score_map.get(pattern_lower, 0))
            is_bad = pattern_lower in bad_patterns
            is_good = pattern_score >= PATTERN_MIN_SCORE and not is_bad
            
            print(f"   Pattern code: {pattern_code}")
            print(f"   Pattern score: {pattern_score}")
            print(f"   PATTERN_MIN_SCORE: {PATTERN_MIN_SCORE}")
            print(f"   Is bad pattern: {is_bad}")
            print(f"   ✅ Pattern score >= PATTERN_MIN_SCORE: {pattern_score >= PATTERN_MIN_SCORE}")
            print(f"   ✅ Pattern is good (not bad): {not is_bad}")
            print(f"   ✅ Overall pattern check: {is_good}")
        else:
            print(f"   ⚠️  No pattern code set")
        print()
        
        # 6. Check pattern at AI_PREVIEW_ENTRY_SEC (100s) and later seconds
        print(f"6️⃣ PATTERN AT {AI_PREVIEW_ENTRY_SEC}s AND LATER CHECK:")
        pattern_at_100s_bad = False
        if iterations > AI_PREVIEW_ENTRY_SEC:
            try:
                from ai.patterns.full_series_classifier import compute_full_features, choose_best_pattern
                
                # Check pattern at multiple points: 100s, 101s, 102s, etc. (up to 105s or current iteration)
                check_points = [AI_PREVIEW_ENTRY_SEC]
                # Also check a few seconds after 100s to catch tokens that become good at 101-105s
                for offset in range(1, min(6, iterations - AI_PREVIEW_ENTRY_SEC + 1)):
                    check_points.append(AI_PREVIEW_ENTRY_SEC + offset)
                
                pattern_at_check_points = {}
                
                for check_sec in check_points:
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
                        pattern_at_check_points[check_sec] = (pattern_at_check, conf_at_check)
                        
                        is_bad = pattern_at_check and pattern_at_check.lower() != 'unknown' and pattern_at_check.lower() in bad_patterns
                        status = "❌ BAD" if is_bad else ("✅ GOOD" if pattern_at_check and pattern_at_check.lower() != 'unknown' else "❓ UNKNOWN")
                        print(f"   [{check_sec}s] Pattern: {pattern_at_check or 'N/A'} (confidence: {conf_at_check:.2f}) {status}")
                    else:
                        print(f"   [{check_sec}s] ⚠️  Not enough data (need at least 3 records, got {len(check_rows) if check_rows else 0})")
                
                # Use pattern at AI_PREVIEW_ENTRY_SEC (100s) for blocking decision
                if AI_PREVIEW_ENTRY_SEC in pattern_at_check_points:
                    pattern_at_100s, conf_at_100s = pattern_at_check_points[AI_PREVIEW_ENTRY_SEC]
                    
                    if pattern_at_100s and pattern_at_100s.lower() != 'unknown' and pattern_at_100s.lower() in bad_patterns:
                        pattern_at_100s_bad = True
                        print(f"   ⚠️  Token was bad at {AI_PREVIEW_ENTRY_SEC}s ({pattern_at_100s}) - will be skipped forever")
                    elif pattern_at_100s and pattern_at_100s.lower() == 'unknown':
                        # Pattern was unknown at 100s - check later seconds (101-105s)
                        found_bad_after_100s = False
                        for later_sec in sorted(pattern_at_check_points.keys()):
                            if later_sec > AI_PREVIEW_ENTRY_SEC:
                                later_pattern, _ = pattern_at_check_points[later_sec]
                                if later_pattern and later_pattern.lower() != 'unknown' and later_pattern.lower() in bad_patterns:
                                    found_bad_after_100s = True
                                    pattern_at_100s_bad = True
                                    print(f"   ⚠️  Token was bad at {later_sec}s (after {AI_PREVIEW_ENTRY_SEC}s) - will be skipped forever")
                                    break
                        
                        if not found_bad_after_100s:
                            print(f"   ✅ Pattern at {AI_PREVIEW_ENTRY_SEC}s was unknown, checked up to {max(pattern_at_check_points.keys())}s - all unknown/good")
                else:
                    print(f"   ⚠️  Could not determine pattern at {AI_PREVIEW_ENTRY_SEC}s (not enough data)")
            except Exception as e:
                print(f"   ⚠️  Error checking pattern at {AI_PREVIEW_ENTRY_SEC}s: {e}")
        else:
            print(f"   ⚠️  Token has only {iterations} iterations (need > {AI_PREVIEW_ENTRY_SEC} to check pattern at {AI_PREVIEW_ENTRY_SEC}s)")
        print()
        
        # 7. Summary
        print("=" * 80)
        print("📊 AUTO-BUY CHECK SUMMARY:")
        print("=" * 80)
        
        checks = []
        checks.append(("Iterations >= AUTO_BUY_ENTRY_SEC", iterations >= AUTO_BUY_ENTRY_SEC))
        checks.append(("No open position", open_position is None))
        checks.append(("Has enabled wallets", enabled_wallets > 0))
        checks.append(("Pattern is good", is_good if pattern_code else False))
        checks.append((f"Pattern at {AI_PREVIEW_ENTRY_SEC}s was not bad", not pattern_at_100s_bad))
        
        all_passed = all(check[1] for check in checks)
        
        for check_name, check_result in checks:
            status = "✅" if check_result else "❌"
            print(f"   {status} {check_name}: {check_result}")
        
        print()
        if all_passed:
            print("✅ ALL CHECKS PASSED - Token SHOULD be entered!")
        else:
            print("❌ SOME CHECKS FAILED - Token will NOT be entered")
            print()
            print("Reasons:")
            for check_name, check_result in checks:
                if not check_result:
                    print(f"   - {check_name}")
        print("=" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/check_token_entry.py <token_id>")
        sys.exit(1)
    
    try:
        token_id = int(sys.argv[1])
    except ValueError:
        print(f"Error: Invalid token_id: {sys.argv[1]}")
        sys.exit(1)
    
    await check_token_entry(token_id)


if __name__ == "__main__":
    asyncio.run(main())

