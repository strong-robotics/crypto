#!/usr/bin/env python3
"""
Debug script: compare chart construction for a given token pair.

Usage (from repo root, with venv active):
  python server/debug_chart_compare.py \
      --pair 7fB3J25XSc78tveQRjGxUwhcGMkTJuuTypQhBjz1Nsri \
      --min-usd 0.0 \
      --preview 20

Nothing is written to DB; only reads trades and prints diagnostics.
"""

import argparse
import asyncio
from collections import defaultdict
from statistics import mean
from typing import Dict, List, Tuple

from _v3_db_pool import get_db_pool


def f2(v, d=8):
    try:
        return float(f"{float(v):.{d}f}")
    except Exception:
        return 0.0


async def load_token_by_pair(pair: str) -> Tuple[int, str]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, token_address FROM tokens WHERE token_pair=$1",
            pair,
        )
        if not row:
            raise SystemExit(f"Token by pair not found: {pair}")
        return int(row["id"]), row["token_address"]


async def load_trades(token_id: int) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT timestamp, direction, amount_tokens, amount_sol, amount_usd, token_price_usd
            FROM trades
            WHERE token_id=$1
            ORDER BY timestamp ASC
            """,
            token_id,
        )
        trades = []
        for r in rows:
            try:
                amt_tokens = float(r["amount_tokens"]) if r["amount_tokens"] is not None else 0.0
                amt_sol = float(r["amount_sol"]) if r["amount_sol"] is not None else 0.0
                amt_usd = float(r["amount_usd"]) if r["amount_usd"] is not None else 0.0
                p_usd = float(r["token_price_usd"]) if r["token_price_usd"] is not None else 0.0
            except Exception:
                # Колонки amount_* у нас TEXT — защитимся от мусора
                try:
                    amt_tokens = float(str(r["amount_tokens"]))
                except Exception:
                    amt_tokens = 0.0
                try:
                    amt_sol = float(str(r["amount_sol"]))
                except Exception:
                    amt_sol = 0.0
                try:
                    amt_usd = float(str(r["amount_usd"]))
                except Exception:
                    amt_usd = 0.0
                try:
                    p_usd = float(str(r["token_price_usd"]))
                except Exception:
                    p_usd = 0.0

            price_in_sol = (amt_sol / amt_tokens) if amt_tokens > 0 else 0.0
            if p_usd == 0 and amt_tokens > 0:
                # пересчёт на случай отсутствия значения
                p_usd = (amt_usd / amt_tokens) if amt_tokens > 0 else 0.0

            trades.append(
                {
                    "ts": int(r["timestamp"]),
                    "dir": r["direction"],
                    "amt_tokens": amt_tokens,
                    "amt_sol": amt_sol,
                    "amt_usd": amt_usd,
                    "p_usd": p_usd,
                    "p_sol": price_in_sol,
                }
            )
        return trades


def aggregate_series(trades: List[Dict], min_usd: float = 0.0) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Return (per_second_usd, per_second_sol) series as (ts, avg_price)."""
    buckets_usd: Dict[int, List[float]] = defaultdict(list)
    buckets_sol: Dict[int, List[float]] = defaultdict(list)
    for t in trades:
        if min_usd and t["amt_usd"] < min_usd:
            continue  # фильтр микросделок
        sec = t["ts"]
        if t["p_usd"] > 0:
            buckets_usd[sec].append(t["p_usd"])
        if t["p_sol"] > 0:
            buckets_sol[sec].append(t["p_sol"])

    per_sec_usd = sorted(((ts, mean(vs)) for ts, vs in buckets_usd.items()), key=lambda x: x[0])
    per_sec_sol = sorted(((ts, mean(vs)) for ts, vs in buckets_sol.items()), key=lambda x: x[0])
    return per_sec_usd, per_sec_sol


def summarize_series(name: str, series: List[Tuple[int, float]], preview: int = 10):
    if not series:
        print(f"{name}: EMPTY")
        return
    vals = [v for _, v in series]
    print(f"{name}: points={len(series)} min={f2(min(vals))} max={f2(max(vals))}")
    print(f"  first {preview}: {[f2(v) for _, v in series[:preview]]}")
    print(f"  last  {preview}: {[f2(v) for _, v in series[-preview:]]}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", required=True, help="Token pair address")
    parser.add_argument("--min-usd", type=float, default=0.0, help="Filter micro trades by USD amount")
    parser.add_argument("--preview", type=int, default=10, help="How many points to preview from head/tail")
    args = parser.parse_args()

    token_id, token_addr = await load_token_by_pair(args.pair)
    print(f"Token: id={token_id} addr={token_addr} pair={args.pair}")
    trades = await load_trades(token_id)
    print(f"Trades loaded: {len(trades)} (min_usd filter={args.min_usd})")

    # Aggregate per second
    per_sec_usd, per_sec_sol = aggregate_series(trades, min_usd=args.min_usd)
    summarize_series("USD/second", per_sec_usd, args.preview)
    summarize_series("SOL/second", per_sec_sol, args.preview)

    # Aggregate per minute (optional)
    # Convert to minutes buckets
    by_min_usd: Dict[int, List[float]] = defaultdict(list)
    by_min_sol: Dict[int, List[float]] = defaultdict(list)
    for ts, v in per_sec_usd:
        by_min_usd[ts // 60].append(v)
    for ts, v in per_sec_sol:
        by_min_sol[ts // 60].append(v)
    per_min_usd = sorted(((m, mean(vs)) for m, vs in by_min_usd.items()), key=lambda x: x[0])
    per_min_sol = sorted(((m, mean(vs)) for m, vs in by_min_sol.items()), key=lambda x: x[0])
    summarize_series("USD/minute", per_min_usd, args.preview)
    summarize_series("SOL/minute", per_min_sol, args.preview)


if __name__ == "__main__":
    asyncio.run(main())

