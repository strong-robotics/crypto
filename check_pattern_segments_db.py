#!/usr/bin/env python3
"""
Check if pattern_segment data from manual_patterns_raw.txt exists in tokens_history table.
"""

import asyncio
import sys
from pathlib import Path

# Add server directory to path
ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.append(str(SERVER_DIR))

from _v3_db_pool import get_db_pool


async def check_pattern_segments_in_db():
    """Check if pattern_segment data exists in tokens_history"""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        # 1. Check if pattern_segment columns exist in tokens_history
        print("=" * 60)
        print("1. Checking if pattern_segment columns exist in tokens_history...")
        print("=" * 60)
        
        columns_check = await conn.fetch("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'tokens_history' 
            AND column_name LIKE 'pattern_segment%'
            ORDER BY column_name
        """)
        
        if columns_check:
            print("✅ Found pattern_segment columns:")
            for col in columns_check:
                print(f"   - {col['column_name']}: {col['data_type']}")
        else:
            print("❌ No pattern_segment columns found in tokens_history")
            return
        
        # 2. Count tokens with pattern_segment data
        print("\n" + "=" * 60)
        print("2. Counting tokens with pattern_segment data...")
        print("=" * 60)
        
        count_query = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(pattern_segment_1) as has_seg1,
                COUNT(pattern_segment_2) as has_seg2,
                COUNT(pattern_segment_3) as has_seg3,
                COUNT(CASE WHEN pattern_segment_1 IS NOT NULL 
                           AND pattern_segment_2 IS NOT NULL 
                           AND pattern_segment_3 IS NOT NULL 
                      THEN 1 END) as has_all_three
            FROM tokens_history
        """)
        
        print(f"Total tokens in tokens_history: {count_query['total']}")
        print(f"Tokens with pattern_segment_1: {count_query['has_seg1']}")
        print(f"Tokens with pattern_segment_2: {count_query['has_seg2']}")
        print(f"Tokens with pattern_segment_3: {count_query['has_seg3']}")
        print(f"Tokens with all three segments: {count_query['has_all_three']}")
        
        # 3. Check sample data
        print("\n" + "=" * 60)
        print("3. Sample tokens with pattern_segment data (first 10):")
        print("=" * 60)
        
        sample_query = await conn.fetch("""
            SELECT id, pattern_segment_1, pattern_segment_2, pattern_segment_3, pattern_segment_decision
            FROM tokens_history
            WHERE pattern_segment_1 IS NOT NULL 
              AND pattern_segment_2 IS NOT NULL 
              AND pattern_segment_3 IS NOT NULL
            ORDER BY id
            LIMIT 10
        """)
        
        if sample_query:
            print(f"Found {len(sample_query)} sample tokens:")
            for row in sample_query:
                print(f"   Token {row['id']}: [{row['pattern_segment_1']}, {row['pattern_segment_2']}, {row['pattern_segment_3']}] -> {row['pattern_segment_decision']}")
        else:
            print("❌ No tokens with all three segments found")
        
        # 4. Check if data from manual_patterns_raw.txt exists
        print("\n" + "=" * 60)
        print("4. Checking if data from manual_patterns_raw.txt exists...")
        print("=" * 60)
        
        # Read manual_patterns_raw.txt
        manual_file = ROOT / "manual_patterns_raw.txt"
        if not manual_file.exists():
            print(f"❌ File {manual_file} not found")
            return
        
        # Parse manual_patterns_raw.txt
        manual_tokens = {}
        with open(manual_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Format: "token_id - segment1, segment2, segment3 - decision"
                parts = line.split(' - ')
                if len(parts) >= 2:
                    try:
                        token_id = int(parts[0])
                        segments_part = parts[1].split(' - ')[0] if len(parts) > 1 else parts[1]
                        segments = [s.strip() for s in segments_part.split(',')]
                        if len(segments) == 3:
                            manual_tokens[token_id] = {
                                'seg1': segments[0],
                                'seg2': segments[1],
                                'seg3': segments[2],
                            }
                    except (ValueError, IndexError):
                        continue
        
        print(f"Found {len(manual_tokens)} tokens in manual_patterns_raw.txt")
        
        # Check how many of these tokens exist in tokens_history with matching data
        if manual_tokens:
            token_ids = list(manual_tokens.keys())
            db_tokens = await conn.fetch("""
                SELECT id, pattern_segment_1, pattern_segment_2, pattern_segment_3
                FROM tokens_history
                WHERE id = ANY($1)
            """, token_ids)
            
            matched = 0
            not_found = 0
            no_data = 0
            
            db_dict = {row['id']: row for row in db_tokens}
            
            for token_id, manual_data in manual_tokens.items():
                if token_id not in db_dict:
                    not_found += 1
                else:
                    db_data = db_dict[token_id]
                    if (db_data['pattern_segment_1'] and 
                        db_data['pattern_segment_2'] and 
                        db_data['pattern_segment_3']):
                        # Normalize for comparison (lowercase)
                        db_seg1 = (db_data['pattern_segment_1'] or '').lower()
                        db_seg2 = (db_data['pattern_segment_2'] or '').lower()
                        db_seg3 = (db_data['pattern_segment_3'] or '').lower()
                        manual_seg1 = manual_data['seg1'].lower()
                        manual_seg2 = manual_data['seg2'].lower()
                        manual_seg3 = manual_data['seg3'].lower()
                        
                        if (db_seg1 == manual_seg1 and 
                            db_seg2 == manual_seg2 and 
                            db_seg3 == manual_seg3):
                            matched += 1
                        else:
                            print(f"   ⚠️  Token {token_id}: DB=[{db_seg1}, {db_seg2}, {db_seg3}] vs Manual=[{manual_seg1}, {manual_seg2}, {manual_seg3}]")
                    else:
                        no_data += 1
            
            print(f"\n📊 Comparison results:")
            print(f"   ✅ Matched: {matched}/{len(manual_tokens)}")
            print(f"   ❌ Not found in DB: {not_found}")
            print(f"   ⚠️  No data in DB: {no_data}")
        
        # 5. Check tokens table too (for active tokens)
        print("\n" + "=" * 60)
        print("5. Checking tokens table (active tokens)...")
        print("=" * 60)
        
        active_count = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN pattern_segment_1 IS NOT NULL 
                           AND pattern_segment_2 IS NOT NULL 
                           AND pattern_segment_3 IS NOT NULL 
                      THEN 1 END) as has_all_three
            FROM tokens
        """)
        
        print(f"Total tokens in tokens table: {active_count['total']}")
        print(f"Tokens with all three segments: {active_count['has_all_three']}")


if __name__ == "__main__":
    asyncio.run(check_pattern_segments_in_db())

