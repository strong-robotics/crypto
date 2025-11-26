#!/usr/bin/env python3
"""
Admin fixes:
 - Manually set pattern codes for provided token IDs
 - Purge all tokens with fewer than N metric iterations (default 60)

Usage (run from project root):
  cd server && source venv/bin/activate && PYTHONPATH=. python tools/admin_fix.py
"""
from __future__ import annotations

import asyncio
from typing import Dict, Tuple

from _v3_db_pool import get_db_pool


# Manual remap: token_id -> pattern_code
MANUAL_PATTERN_MAP: Dict[int, str] = {
    # examples from user
    3154: "clean_launch",
    2930: "clean_launch",
    3019: "mirage_rise",
    9852: "mirage_rise",
    1640: "mirage_rise",
    2853: "black_hole",
    2829: "echo_wave",
    2836: "tug_of_war",
    1356: "gravity_breaker",
    3987: "gravity_breaker",
    4014: "gravity_breaker",
    4359: "gravity_breaker",
    4392: "gravity_breaker",
    4786: "gravity_breaker",
    4882: "mirage_rise",
    5238: "echo_wave",
    5248: "echo_wave",
    6192: "echo_wave",
    6400: "gravity_breaker",
    7052: "gravity_breaker",
    2042: "mirage_rise",
    3486: "mirage_rise",
    3501: "bait_switch",
    3557: "mirage_rise",
    3681: "mirage_rise",
    3697: "gravity_breaker",
    3817: "gravity_breaker",
    3868: "gravity_breaker",
    3869: "echo_wave",
    3926: "gravity_breaker",
    4510: "gravity_breaker",
    4609: "echo_wave",
    4642: "echo_wave",
    4916: "gravity_breaker",
    5198: "echo_wave",
    5206: "echo_wave",
    5276: "gravity_breaker",
    5613: "gravity_breaker",
    6011: "black_hole",
    6350: "mirage_rise",
    6375: "echo_wave",
    6577: "gravity_breaker",
    6679: "gravity_breaker",
    6893: "echo_wave",
    7099: "flash_bloom",
    7558: "echo_wave",
    7605: "mirage_rise",
    7772: "gravity_breaker",
    8622: "gravity_breaker",
    8711: "gravity_breaker",
    8945: "gravity_breaker",
    9701: "gravity_breaker",
    1066: "gravity_breaker",
    1220: "mirage_rise",
    1743: "echo_wave",
    2268: "echo_wave",
    2501: "mirage_rise",
    1200: "gravity_breaker",
    2425: "mirage_rise",
    1935: "mirage_rise",
    1783: "mirage_rise",
    1316: "echo_wave",
    1764: "echo_wave",
    3435: "mirage_rise",
    4118: "mirage_rise",
    4578: "gravity_breaker",
    4998: "echo_wave",
    5334: "echo_wave",
    5983: "echo_wave",
    6139: "mirage_rise",
    6151: "mirage_rise",
    7049: "gravity_breaker",
    7084: "echo_wave",
    8239: "gravity_breaker",
    8829: "mirage_rise",
    9670: "mirage_rise",
    9688: "mirage_rise",
    1551: "gravity_breaker",
    1759: "echo_wave",
    1833: "mirage_rise",
    1172: "echo_wave",
    1374: "mirage_rise",
    1466: "mirage_rise",
    1470: "panic_sink",
    2283: "panic_sink",
    1318: "gravity_breaker",
    1346: "flash_bloom",
    1361: "mirage_rise",
    1592: "mirage_rise",
    1651: "mirage_rise",
    1692: "mirage_rise",
    1925: "gravity_breaker",
    2285: "bait_switch",
    2422: "bait_switch",
    2640: "echo_wave",
    22: "clean_launch",
    173: "clean_launch",
    2483: "gravity_breaker",
    2317: "gravity_breaker",
    2485: "gravity_breaker",
    3374: "gravity_breaker",
    3105: "gravity_breaker",
    3263: "echo_wave",
    2867: "echo_wave",
    2962: "echo_wave",
    3246: "clean_launch",
    3520: "mirage_rise",
    6477: "echo_wave",
    710: "echo_wave",
    1502: "echo_wave",
    1747: "gravity_breaker",
    2166: "gravity_breaker",
    3483: "gravity_breaker",
    3899: "mirage_rise",
    4344: "mirage_rise",
    4484: "gravity_breaker",
    4711: "gravity_breaker",
    6337: "flash_bloom",
    6746: "gravity_breaker",
    7938: "gravity_breaker",
    8236: "echo_wave",
    8746: "mirage_rise",
    2018: "gravity_breaker",
    1751: "gravity_breaker",
    1621: "gravity_breaker",
    2208: "clean_launch",
    2812: "gravity_breaker",
    3608: "bait_switch",
    3641: "flatliner",
    3974: "mirage_rise",
    4659: "mirage_rise",
    4827: "gravity_breaker",
    4989: "echo_wave",
    5357: "mirage_rise",
    6593: "mirage_rise",
    6608: "mirage_rise",
    6626: "echo_wave",
    6632: "mirage_rise",
    6682: "gravity_breaker",
    6977: "mirage_rise",
    7036: "flatliner",
    7038: "echo_wave",
    7562: "echo_wave",
    8709: "bait_switch",
    9023: "mirage_rise",
    9124: "mirage_rise",
    9364: "golden_curve",
    4631: "mirage_rise",
    4690: "gravity_breaker",
    5093: "mirage_rise",
    5122: "mirage_rise",
    5540: "golden_curve",
    5813: "mirage_rise",
    5823: "golden_curve",
    5858: "golden_curve",
    5910: "echo_wave",
    5959: "golden_curve",
    5985: "mirage_rise",
    6027: "gravity_breaker",
    6396: "echo_wave",
    6918: "gravity_breaker",
    7116: "mirage_rise",
    8411: "mirage_rise",
    8706: "mirage_rise",
    8793: "echo_wave",
    8807: "echo_wave",
    8973: "echo_wave",
    9508: "mirage_rise",
    9385: "golden_curve",
    5793: "golden_curve",
    9584: "golden_curve",
    9469: "echo_wave",
    9527: "echo_wave",
    9815: "bait_switch",
    9846: "black_hole",
}

PURGE_ITER_THRESHOLD = 60  # delete tokens with < 60 metric rows


async def _pattern_id(conn, code: str) -> int | None:
    row = await conn.fetchrow("SELECT id FROM ai_patterns WHERE code=$1", code)
    return int(row["id"]) if row else None


async def apply_manual_patterns() -> Tuple[int, int]:
    pool = await get_db_pool()
    updated = 0
    inserted_atp = 0
    async with pool.acquire() as conn:
        for tid, code in MANUAL_PATTERN_MAP.items():
            pid = await _pattern_id(conn, code)
            # Update tokens.pattern_code + pretty name
            if pid is None:
                # still update tokens.pattern_code for UI; without dictionary name
                await conn.execute(
                    "UPDATE tokens SET pattern_code=$2, pattern=$2, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
                    int(tid), code,
                )
            else:
                name_row = await conn.fetchrow("SELECT name FROM ai_patterns WHERE id=$1", pid)
                pretty = name_row["name"] if name_row and name_row["name"] else code.replace('_', ' ').title()
                await conn.execute(
                    "UPDATE tokens SET pattern_code=$2, pattern=$3, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
                    int(tid), code, pretty,
                )
                # Upsert ai_token_patterns with high confidence, source=manual
                await conn.execute(
                    """
                    INSERT INTO ai_token_patterns(token_id, pattern_id, source, confidence, notes)
                    VALUES($1,$2,'manual',0.99,'admin_fix')
                    ON CONFLICT (token_id, pattern_id, source)
                    DO UPDATE SET confidence=EXCLUDED.confidence, created_at=now()
                    """,
                    int(tid), int(pid)
                )
                inserted_atp += 1
            updated += 1
    return updated, inserted_atp


async def purge_short_tokens(threshold: int = PURGE_ITER_THRESHOLD) -> Tuple[int, int, int]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH m AS (
              SELECT token_id, COUNT(*) AS cnt
              FROM token_metrics_seconds
              GROUP BY token_id
            )
            SELECT t.id FROM tokens t
            LEFT JOIN m ON m.token_id=t.id
            WHERE COALESCE(m.cnt,0) < $1
            """,
            int(threshold)
        )
        ids = [int(r['id']) for r in rows]
        if not ids:
            return (0,0,0)
        async with conn.transaction():
            # Unbind wallets (real trading only - wallets table)
            try:
                await conn.execute("UPDATE wallets SET active_token_id=NULL WHERE active_token_id = ANY($1)", ids)
            except Exception:
                pass
            # Delete related rows
            for tbl in ("ai_token_patterns", "wallet_history", "token_metrics_seconds", "trades"):
                try:
                    await conn.execute(f"DELETE FROM {tbl} WHERE token_id = ANY($1)", ids)
                except Exception:
                    pass
            x = await conn.execute("DELETE FROM tokens WHERE id = ANY($1)", ids)
            try:
                deleted_tokens = int((x or '').split()[-1])
            except Exception:
                deleted_tokens = 0
        return (len(ids), deleted_tokens, len(ids) - deleted_tokens)


async def main():
    upd, atp = await apply_manual_patterns()
    found, deleted, _ = await purge_short_tokens(PURGE_ITER_THRESHOLD)
    print(f"Manual patterns updated: {upd}, ai_token_patterns upserts: {atp}")
    print(f"Purged short tokens: found={found}, deleted={deleted} (< {PURGE_ITER_THRESHOLD} iters)")


if __name__ == "__main__":
    asyncio.run(main())
