#!/usr/bin/env python3
"""Apply AI SQL migrations (idempotent)."""

import asyncio
import os
from typing import List

from _v3_db_pool import get_db_pool


async def apply_sql_files(paths: List[str]) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    sql = f.read()
                if sql.strip():
                    await conn.execute(sql)
                    print(f"✅ Applied: {p}")
            except Exception as e:
                print(f"❌ Migration failed for {p}: {e}")
                raise


async def main() -> None:
    base = os.path.dirname(os.path.dirname(__file__))
    mig = os.path.join(base, "sql", "migrations")
    files = sorted(os.listdir(mig))
    paths = [os.path.join(mig, x) for x in files if x.endswith(".sql")]
    await apply_sql_files(paths)


if __name__ == "__main__":
    asyncio.run(main())

