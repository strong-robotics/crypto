"""
Simple PnL backtest skeleton for ETA model decisions.

Inputs:
- predictions DataFrame with columns: token_id, entry_ts, p_hit, eta_bin
- price series from token_metrics_seconds to simulate entry at entry_ts and exit at +20% or timeout 120s

This is a minimal evaluator to tune p_threshold.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from _v3_db_pool import get_db_pool


TARGET_RET = 0.20
T_MAX = 120


async def load_prices_for_entries(entries: pd.DataFrame) -> Dict[int, pd.DataFrame]:
    pool = await get_db_pool()
    out: Dict[int, pd.DataFrame] = {}
    async with pool.acquire() as conn:
        for token_id, g in entries.groupby("token_id"):
            ts_min = int(g["entry_ts"].min())
            ts_max = int(g["entry_ts"].max()) + T_MAX + 2
            rows = await conn.fetch(
                """
                SELECT ts::bigint as ts, usd_price::double precision as usd_price
                FROM token_metrics_seconds
                WHERE token_id=$1 AND ts BETWEEN $2 AND $3
                ORDER BY ts ASC
                """,
                int(token_id), ts_min, ts_max,
            )
            if rows:
                out[int(token_id)] = pd.DataFrame([dict(r) for r in rows])
    return out


def simulate_pnl(entries: pd.DataFrame, prices: Dict[int, pd.DataFrame], p_threshold: float = 0.6) -> Dict:
    # assume $5 entry per token when p_hit>=threshold
    try:
        from config import config
        stake = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
    except Exception:
        stake = 0.0
    
    pnl = 0.0
    taken = 0
    hits = 0
    for _, r in entries.iterrows():
        if float(r["p_hit"]) < p_threshold:
            continue
        taken += 1
        token_id = int(r["token_id"])
        entry_ts = int(r["entry_ts"])
        pdf = prices.get(token_id)
        if pdf is None or pdf.empty:
            continue
        row = pdf[pdf["ts"] == entry_ts]
        if row.empty:
            continue
        entry_price = float(row.iloc[0]["usd_price"])
        target = entry_price * (1.0 + TARGET_RET)
        future = pdf[pdf["ts"] > entry_ts].head(T_MAX)
        exit_price = None
        for _, fr in future.iterrows():
            if float(fr["usd_price"]) >= target:
                exit_price = float(fr["usd_price"])
                break
        if exit_price is None:
            # timeout exit: no PnL
            continue
        hits += 1
        pnl += stake * ((exit_price / entry_price) - 1.0)
    return {
        "taken": taken,
        "hits": hits,
        "hit_rate": (hits / taken) if taken else 0.0,
        "pnl_usd": pnl,
        "avg_pnl_per_trade": (pnl / taken) if taken else 0.0,
    }


async def backtest(pred_csv: str, p_threshold: float = 0.6) -> Dict:
    entries = pd.read_csv(pred_csv)
    prices = await load_prices_for_entries(entries)
    return simulate_pnl(entries, prices, p_threshold=p_threshold)


if __name__ == "__main__":
    # Example: python -m ai.backtest.pnl_backtest preds.csv
    import sys
    pred = sys.argv[1] if len(sys.argv) > 1 else "preds.csv"
    res = asyncio.run(backtest(pred))
    print(res)
