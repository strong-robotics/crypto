#!/usr/bin/env python3
"""Import mapping token_address -> pattern_code into ai_token_patterns."""

import argparse
import asyncio
import csv
from typing import Dict

from _v3_db_pool import get_db_pool


async def import_file(path: str, source: str = "manual") -> Dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        added = 0
        skipped = 0
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = (row.get("token_address") or row.get("address") or "").strip()
                code = (row.get("pattern_code") or row.get("pattern") or "").strip()
                notes = (row.get("notes") or "").strip()
                if not addr or not code:
                    skipped += 1
                    continue
                token_id = await conn.fetchval("SELECT id FROM tokens WHERE token_address=$1", addr)
                pattern_id = await conn.fetchval("SELECT id FROM ai_patterns WHERE code=$1", code)
                if not token_id or not pattern_id:
                    skipped += 1
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO ai_token_patterns (token_id, pattern_id, source, confidence, notes)
                        VALUES ($1,$2,$3,$4,$5)
                        ON CONFLICT (token_id, pattern_id, source) DO UPDATE SET notes=EXCLUDED.notes
                        """,
                        token_id, pattern_id, source, 1.0, notes,
                    )
                    added += 1
                except Exception:
                    skipped += 1
        return {"added": added, "skipped": skipped}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import token patterns from CSV")
    p.add_argument("--file", required=True, help="CSV with columns: token_address, pattern_code, [notes]")
    p.add_argument("--source", default="manual")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    res = asyncio.run(import_file(args.file, args.source))
    print(res)

