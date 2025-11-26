#!/usr/bin/env python3

import asyncio
from statistics import median
from typing import List, Dict, Tuple

from _v3_db_pool import get_db_pool


async def build_dex_like_price_series(token_id: int, debug: bool = False) -> List[Dict]:
    """Build a DexScreener-like USD/second price series for a token.

    Uses token_metrics_seconds (usd_price, liquidity, fdv, mcap) as a base
    and adjusts by median trade price per second when trades exist.
    Returns a list of {ts, price} points ordered by ts.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        metrics = await conn.fetch(
            """
            SELECT ts, usd_price, liquidity, fdv, mcap
            FROM token_metrics_seconds
            WHERE token_id = $1
            ORDER BY ts ASC
            """,
            token_id,
        )
        if not metrics:
            if debug:
                print(f"⚠️ No token_metrics_seconds for token_id={token_id}")
            return []

        start_ts, end_ts = int(metrics[0]["ts"]), int(metrics[-1]["ts"])
        trades = await conn.fetch(
            """
            SELECT timestamp, token_price_usd
            FROM trades
            WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
            ORDER BY timestamp ASC
            """,
            token_id,
            start_ts,
            end_ts,
        )

        trade_by_second: Dict[int, List[float]] = {}
        for t in trades:
            ts = int(t["timestamp"])
            try:
                price = float(t["token_price_usd"] or 0)
            except Exception:
                price = 0.0
            if price > 0:
                trade_by_second.setdefault(ts, []).append(price)

        series: List[Dict] = []
        prev_price = None
        for row in metrics:
            ts = int(row["ts"])  # seconds
            try:
                usd_price_metric = float(row["usd_price"] or 0)
                fdv = float(row["fdv"] or 0)
                mcap = float(row["mcap"] or 0)
            except Exception:
                usd_price_metric = 0.0
                fdv = 0.0
                mcap = 0.0

            if fdv > 0 and mcap > 0 and usd_price_metric > 0:
                dex_price = (mcap / fdv) * usd_price_metric
            else:
                dex_price = usd_price_metric

            if ts in trade_by_second:
                real_price = median(trade_by_second[ts])
                dex_price = 0.7 * dex_price + 0.3 * real_price

            if (dex_price is None or dex_price <= 0) and prev_price:
                dex_price = prev_price

            prev_price = dex_price
            series.append({"ts": ts, "price": round(float(dex_price or 0.0), 10)})

        if debug:
            print(f"✅ Built Dex-like series for token_id={token_id}: {len(series)} points")
        return series


async def build_our_trade_series(token_id: int, start_ts: int = 0, end_ts: int = 0, debug: bool = False) -> List[Dict]:
    """Build our current USD/second series from trades (avg per second).

    Returns a list of {ts, price} for seconds where we have trades.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if start_ts and end_ts and start_ts < end_ts:
            rows = await conn.fetch(
                """
                SELECT timestamp, token_price_usd
                FROM trades
                WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                ORDER BY timestamp ASC
                """,
                token_id,
                start_ts,
                end_ts,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT timestamp, token_price_usd
                FROM trades
                WHERE token_id = $1
                ORDER BY timestamp ASC
                """,
                token_id,
            )
        if not rows:
            return []

        by_sec: Dict[int, List[float]] = {}
        for r in rows:
            ts = int(r["timestamp"])
            try:
                price = float(r["token_price_usd"] or 0)
            except Exception:
                price = 0.0
            if price > 0:
                by_sec.setdefault(ts, []).append(price)

        series: List[Dict] = []
        for ts in sorted(by_sec.keys()):
            vals = by_sec[ts]
            avgp = sum(vals) / len(vals)
            series.append({"ts": ts, "price": round(float(avgp), 10)})
        if debug:
            print(f"✅ Built OUR series for token_id={token_id}: {len(series)} points")
        return series

