#!/usr/bin/env python3
"""Seed ai_patterns with catalog patterns.

Uses the local Python catalog for clarity; alternatively, can execute SQL seed.
"""

import asyncio
from _v3_db_pool import get_db_pool
from ai.patterns.catalog import PATTERN_SEED


async def seed() -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for p in PATTERN_SEED:
            try:
                await conn.execute(
                    """
                    INSERT INTO ai_patterns(code,name,tier,score,description)
                    VALUES ($1,$2,$3,$4,$5)
                    ON CONFLICT (code) DO UPDATE SET
                      name=EXCLUDED.name,
                      tier=EXCLUDED.tier,
                      score=EXCLUDED.score,
                      description=EXCLUDED.description
                    """,
                    p["code"], p["name"], p["tier"], p["score"], p.get("description"),
                )
            except Exception as e:
                print(f"❌ seed pattern {p['code']}: {e}")
        print(f"✅ Seeded {len(PATTERN_SEED)} patterns")


if __name__ == "__main__":
    asyncio.run(seed())
