#!/usr/bin/env python3
"""
Quick entry/exit forecast from Jupiter seconds data (no Helios).

- Picks an entry iteration within [15, 20] seconds (inclusive)
  by evaluating drift over the last window (default 10s) ending at that second.
- Fits linear regression on ln(price) vs time to estimate per-second drift (mu)
  and residual std (sigma), then predicts ETA to +alpha (default 20%).
- Returns a heuristic hit probability p based on drift/vol ratio.

Usage examples:
  python -m server.ai.infer.quick_forecast --token 22
  python -m server.ai.infer.quick_forecast --tokens 22 353 1264 --alpha 0.2 --horizon 120
  python -m server.ai.infer.quick_forecast --all --limit 50

This script does NOT modify DB unless --save specified (reserved for future).
It prints a compact table to stdout.
"""

import argparse
import asyncio
import math
from typing import List, Dict, Optional, Tuple

import asyncpg

from server.db_config import POSTGRES_CONFIG


def _linreg_slope_sigma(x: List[float], y: List[float]) -> Tuple[float, float]:
    """Simple linear regression y ~ a + b*x. Returns (slope b per 1 unit x, sigma of residuals).
    Assumes len(x) == len(y) >= 2.
    """
    n = len(x)
    sx = sum(x)
    sy = sum(y)
    sxx = sum(v*v for v in x)
    sxy = sum(x[i]*y[i] for i in range(n))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, 0.0
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    # residuals
    res = [y[i] - (a + b * x[i]) for i in range(n)]
    if n > 2:
        var = sum(r*r for r in res) / (n - 2)
    else:
        var = sum(r*r for r in res) / max(1, n)
    sigma = math.sqrt(max(var, 0.0))
    return b, sigma


async def fetch_seconds(conn: asyncpg.Connection, token_id: int) -> List[Tuple[int, float]]:
    rows = await conn.fetch(
        """
        SELECT ts, usd_price
        FROM token_metrics_seconds
        WHERE token_id = $1 AND usd_price IS NOT NULL
        ORDER BY ts ASC
        """,
        token_id,
    )
    return [(int(r["ts"]), float(r["usd_price"])) for r in rows]


def pick_entry_and_forecast(
    series: List[Tuple[int, float]],
    entry_start: int = 15,
    entry_end: int = 20,
    alpha: float = 0.20,
    horizon: int = 120,
    win: int = 10,
) -> Optional[Dict]:
    """Given ordered (ts, price) per second, choose entry in [entry_start, entry_end]
    and forecast ETA to reach +alpha with a heuristic probability.
    Returns dict or None if insufficient data.
    """
    if len(series) < entry_start:
        return None
    # Build rn index starting at 1
    # series[rn-1] corresponds to rn
    best = None
    for rn in range(entry_start, min(entry_end, len(series)) + 1):
        # use up to last `win` points ending at rn for regression on ln(price)
        j0 = max(0, rn - win)  # zero-based slice start (inclusive)
        j1 = rn  # exclusive
        seg = series[j0:j1]
        if len(seg) < 3:
            continue
        # x as seconds normalized to start at 0 to avoid numeric issues
        xs = [i for i in range(len(seg))]
        lnps = [math.log(max(1e-12, p)) for (_, p) in seg]
        slope, sigma = _linreg_slope_sigma(xs, lnps)
        # entry price = last price in window
        t_entry, p_entry = series[rn - 1]
        if p_entry <= 0:
            continue
        # required log‑increase to hit alpha
        need = math.log(1.0 + alpha)
        if slope <= 0:
            eta = None
        else:
            eta = math.ceil(need / slope)
            if eta <= 0:
                eta = 1
        # heuristic probability based on drift-to-vol ratio
        # z = slope / (sigma + eps) scaled by sqrt(window)
        eps = 1e-9
        z = slope * math.sqrt(len(seg)) / (sigma + eps)
        p = 1.0 / (1.0 + math.exp(-1.2 * z))
        rec = {
            "entry_rn": rn,
            "entry_ts": t_entry,
            "price_entry": p_entry,
            "slope": slope,
            "sigma": sigma,
            "z": z,
            "p": max(0.0, min(1.0, p)),
            "alpha": alpha,
            "target_price": p_entry * (1.0 + alpha),
            "eta_pred": None if eta is None else int(eta),
            "exit_rn_pred": None if eta is None else int(rn + min(eta, horizon)),
        }
        # keep the entry with maximum p; tie-breaker: smaller eta
        if best is None:
            best = rec
        else:
            if rec["p"] > best["p"] + 1e-9 or (
                abs(rec["p"] - best["p"]) <= 1e-9
                and (rec["eta_pred"] or 1e9) < (best["eta_pred"] or 1e9)
            ):
                best = rec
    return best


async def run(tokens: List[int], alpha: float, horizon: int, win: int, limit_all: int) -> None:
    cfg = POSTGRES_CONFIG.copy()
    cfg["database"] = "crypto_db"
    # independent connection (no pool for a simple CLI)
    conn = await asyncpg.connect(**{k: v for k, v in cfg.items() if k not in ("min_size", "max_size")})
    try:
        if not tokens:
            rows = await conn.fetch(
                """
                SELECT DISTINCT token_id
                FROM token_metrics_seconds
                ORDER BY token_id ASC
                LIMIT $1
                """,
                limit_all,
            )
            tokens = [int(r["token_id"]) for r in rows]

        header = (
            "token_id,entry_rn,entry_ts,price_entry,target_price,p,eta_pred,exit_rn_pred,slope,sigma"
        )
        print(header)
        for tid in tokens:
            series = await fetch_seconds(conn, tid)
            if not series:
                continue
            res = pick_entry_and_forecast(series, 15, 20, alpha, horizon, win)
            if not res:
                continue
            print(
                f"{tid},{res['entry_rn']},{res['entry_ts']},{res['price_entry']:.8g},"
                f"{res['target_price']:.8g},{res['p']:.4f},{res['eta_pred']},{res['exit_rn_pred']},{res['slope']:.6g},{res['sigma']:.6g}"
            )
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser(description="Quick entry/exit forecast from 15–20s window")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--token", type=int, help="Single token_id")
    g.add_argument("--tokens", type=int, nargs="*", help="List of token_id")
    g.add_argument("--all", action="store_true", help="Process many tokens (limit)")
    p.add_argument("--limit", type=int, default=100, help="Limit for --all (default 100)")
    p.add_argument("--alpha", type=float, default=0.20, help="Target gain, default 0.20 (20%)")
    p.add_argument("--horizon", type=int, default=120, help="Max ETA seconds to predict")
    p.add_argument("--win", type=int, default=10, help="Regression window length (<= entry rn)")
    args = p.parse_args()

    tokens: List[int] = []
    limit_all = args.limit
    if args.token is not None:
        tokens = [args.token]
    elif args.tokens:
        tokens = list(args.tokens)
    elif args.all:
        tokens = []  # will fetch below
    else:
        p.print_help()
        return

    asyncio.run(run(tokens, args.alpha, args.horizon, args.win, limit_all))


if __name__ == "__main__":
    main()

