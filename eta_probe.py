#!/usr/bin/env python3
"""
ETA probe for a token: given token_id or token_pair, compute earliest seconds
to reach +$X profit from $5 entry using only metrics (median_token_price/usd_price).

Placed in project root for convenience (near start/stop scripts).

Usage examples:
  server/venv/bin/python eta_probe.py --pair 3ngLnB5EEam3SWx8GecfGQ2tALmGLgnMXdDNk6EtPtWd --target_usd 1.0
  server/venv/bin/python eta_probe.py --token 21 --target_usd 1.5
"""

import argparse
import asyncio
import os
import sys
from typing import Dict, List, Optional

# Ensure we can import server modules when running from project root
ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(ROOT, "server")
if SERVER_DIR not in sys.path:
    sys.path.append(SERVER_DIR)

from _v3_db_pool import get_db_pool  # type: ignore


HISTORY_SEC = 15
HORIZON_SEC = 300
try:
    from server.config import config
    INVEST_USD = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
except Exception:
    INVEST_USD = 5.0


def pick_price(row: Dict) -> Optional[float]:
    mp = row.get("median_token_price")
    if mp not in (None, ""):
        try:
            return float(mp)
        except Exception:
            pass
    up = row.get("usd_price")
    if up not in (None, ""):
        try:
            return float(up)
        except Exception:
            pass
    return None


def first_cross_eta(rows: List[Dict], i_entry: int, multiplier: float) -> Optional[int]:
    p0 = pick_price(rows[i_entry])
    if p0 is None or p0 <= 0:
        return None
    t0 = int(rows[i_entry]["ts"]) if rows[i_entry].get("ts") is not None else None
    target = p0 * multiplier
    for j in range(i_entry + 1, len(rows)):
        tj = int(rows[j]["ts"]) if rows[j].get("ts") is not None else None
        if t0 is None or tj is None:
            continue
        if tj - t0 > HORIZON_SEC:
            break
        pj = pick_price(rows[j])
        if pj is None:
            continue
        if pj >= target:
            return tj - t0
    return None


async def load_token_by_pair(pair: str) -> Optional[int]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tid = await conn.fetchval("SELECT id FROM tokens WHERE token_pair = $1", pair)
        return int(tid) if tid else None


async def load_metrics(token_id: int) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, usd_price, median_token_price
            FROM token_metrics_seconds
            WHERE token_id = $1
            ORDER BY ts ASC
            """,
            token_id,
        )
        return [dict(r) for r in rows]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=int, help="token_id", default=None)
    ap.add_argument("--pair", type=str, help="token_pair address", default=None)
    ap.add_argument("--target_usd", type=float, help="target profit in USD (default 1.0)", default=1.0)
    args = ap.parse_args()

    token_id = args.token
    if token_id is None and args.pair:
        token_id = await load_token_by_pair(args.pair)
    if token_id is None:
        print("❌ Provide --token or --pair")
        return

    rows = await load_metrics(token_id)
    if len(rows) < HISTORY_SEC + 1:
        print(f"❌ Not enough metrics for token {token_id}: {len(rows)} rows")
        return

    multiplier = 1.0 + float(args.target_usd) / INVEST_USD

    # Entry A: fixed first possible entry (index = 15)
    idx_entry = HISTORY_SEC
    eta_fixed = first_cross_eta(rows, idx_entry, multiplier)

    # Entry B: best entry in [15..60]
    best_eta: Optional[int] = None
    best_idx: Optional[int] = None
    upper = min(len(rows) - 1, 60)
    for i in range(HISTORY_SEC, upper + 1):
        eta_i = first_cross_eta(rows, i, multiplier)
        if eta_i is not None and (best_eta is None or eta_i < best_eta):
            best_eta = eta_i
            best_idx = i

    def ts_at(i: Optional[int]) -> Optional[int]:
        if i is None:
            return None
        try:
            return int(rows[i]["ts"]) if rows[i].get("ts") is not None else None
        except Exception:
            return None

    print("Token:", token_id)
    print("Target USD:", args.target_usd, "(multiplier:", round(multiplier, 4), ")")
    print("Fixed entry @15s: eta=", eta_fixed)
    print("Best entry in [15..60]: entry_idx=", best_idx, "entry_ts=", ts_at(best_idx), "eta=", best_eta)


if __name__ == "__main__":
    asyncio.run(main())
