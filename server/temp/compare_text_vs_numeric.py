#!/usr/bin/env python3
"""
Compare TEXT vs NUMERIC fields in trades table
Перевіряє чи збігаються дані між старими TEXT полями та новими NUMERIC
"""

import asyncio
import asyncpg

async def compare_fields():
    conn = await asyncpg.connect(
        host='localhost',
        database='crypto_app',
        user='yevhenvasylenko'
    )
    
    try:
        # 1. Підрахунок записів з даними
        print("=" * 80)
        print("📊 COMPARING TEXT vs NUMERIC FIELDS IN TRADES TABLE")
        print("=" * 80)
        
        total_trades = await conn.fetchval("SELECT COUNT(*) FROM trades")
        print(f"\n📈 Total trades: {total_trades}")
        
        # 2. Порівняння amount_sol
        print("\n🔍 Comparing amount_sol (TEXT) vs amount_sol_numeric (NUMERIC)...")
        result = await conn.fetch("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN amount_sol_numeric IS NOT NULL THEN 1 END) as with_numeric,
                COUNT(CASE WHEN amount_sol::numeric = amount_sol_numeric THEN 1 END) as matching
            FROM trades
            WHERE amount_sol_numeric IS NOT NULL
        """)
        row = result[0]
        print(f"   Total with numeric: {row['with_numeric']}")
        print(f"   Matching values:    {row['matching']}")
        if row['with_numeric'] > 0:
            match_percent = (row['matching'] / row['with_numeric']) * 100
            print(f"   Match rate:         {match_percent:.2f}%")
        
        # 3. Порівняння amount_usd
        print("\n🔍 Comparing amount_usd (TEXT) vs amount_usd_numeric (NUMERIC)...")
        result = await conn.fetch("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN amount_usd_numeric IS NOT NULL THEN 1 END) as with_numeric,
                COUNT(CASE WHEN amount_usd::numeric = amount_usd_numeric THEN 1 END) as matching
            FROM trades
            WHERE amount_usd_numeric IS NOT NULL
        """)
        row = result[0]
        print(f"   Total with numeric: {row['with_numeric']}")
        print(f"   Matching values:    {row['matching']}")
        if row['with_numeric'] > 0:
            match_percent = (row['matching'] / row['with_numeric']) * 100
            print(f"   Match rate:         {match_percent:.2f}%")
        
        # 4. Порівняння token_price_usd
        print("\n🔍 Comparing token_price_usd (TEXT) vs token_price_usd_numeric (NUMERIC)...")
        result = await conn.fetch("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN token_price_usd_numeric IS NOT NULL THEN 1 END) as with_numeric,
                COUNT(CASE WHEN token_price_usd::numeric = token_price_usd_numeric THEN 1 END) as matching
            FROM trades
            WHERE token_price_usd_numeric IS NOT NULL
        """)
        row = result[0]
        print(f"   Total with numeric: {row['with_numeric']}")
        print(f"   Matching values:    {row['matching']}")
        if row['with_numeric'] > 0:
            match_percent = (row['matching'] / row['with_numeric']) * 100
            print(f"   Match rate:         {match_percent:.2f}%")
        
        # 5. Приклади розбіжностей (якщо є)
        print("\n🔍 Checking for mismatches...")
        mismatches = await conn.fetch("""
            SELECT 
                id, signature,
                amount_sol, amount_sol_numeric,
                amount_usd, amount_usd_numeric,
                token_price_usd, token_price_usd_numeric
            FROM trades
            WHERE 
                amount_sol_numeric IS NOT NULL
                AND (
                    amount_sol::numeric != amount_sol_numeric
                    OR amount_usd::numeric != amount_usd_numeric
                    OR token_price_usd::numeric != token_price_usd_numeric
                )
            LIMIT 5
        """)
        
        if mismatches:
            print(f"\n⚠️  Found {len(mismatches)} mismatches (showing first 5):")
            for row in mismatches:
                print(f"\n   Trade ID: {row['id']} | Signature: {row['signature'][:16]}...")
                print(f"      amount_sol:       TEXT={row['amount_sol']:<20} NUMERIC={row['amount_sol_numeric']}")
                print(f"      amount_usd:       TEXT={row['amount_usd']:<20} NUMERIC={row['amount_usd_numeric']}")
                print(f"      token_price_usd:  TEXT={row['token_price_usd']:<20} NUMERIC={row['token_price_usd_numeric']}")
        else:
            print("   ✅ No mismatches found!")
        
        print("\n" + "=" * 80)
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(compare_fields())

