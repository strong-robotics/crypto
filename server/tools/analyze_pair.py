#!/usr/bin/env python3

"""
Ad-hoc analyzer for a Solana token pair stored in Postgres (crypto_db).

Goals:
- Resolve token by `token_pair`
- Pull trades in a time window
- Compute two shapes:
  1) Current app series proxy (second-average of token_price_usd)
  2) SOL-denominated minute bars (o/h/l/c + VWAP)
- Print simple diagnostics to understand shape mismatches.

Does not modify DB or server behavior. Pure read-only analysis.
"""

import asyncio
import argparse
import json
import time
from decimal import Decimal
from typing import Dict, List

import sys
sys.path.append('server')

from _v3_db_pool import get_db_pool


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    if pct <= 0:
        return vs[0]
    if pct >= 100:
        return vs[-1]
    k = (len(vs) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    if f == c:
        return vs[int(k)]
    return vs[f] * (c - k) + vs[c] * (k - f)


def _group_second_avg_usd(trades: List[Dict]) -> List[float]:
    by_sec: Dict[int, List[float]] = {}
    for tr in trades:
        ts = int(tr['timestamp'])
        usd = float(tr.get('token_price_usd') or 0.0)
        if usd > 0:
            by_sec.setdefault(ts, []).append(usd)
    series = []
    prev = None
    for sec in sorted(by_sec.keys()):
        prices = by_sec[sec]
        if prices:
            avg = sum(prices) / len(prices)
            val = round(avg, 10)
            series.append(val)
            prev = val
        elif prev is not None:
            series.append(prev)
    return series


def _minute_bars_sol(
    trades: List[Dict],
    drop_withdraw: bool = True,
    drop_pct: float = 0.0,
    iqr_k: float = None,
    weight_by: str = "tokens",
) -> List[Dict]:
    by_min: Dict[int, List] = {}
    for tr in trades:
        if drop_withdraw and tr.get('direction') == 'withdraw':
            continue
        ts = int(tr['timestamp'])
        m = (ts // 60) * 60
        try:
            tok = Decimal(str(tr['amount_tokens']))
        except Exception:
            tok = Decimal(0)
        sol = Decimal(str(tr['amount_sol']))
        if tok <= 0 or sol <= 0:
            continue
        p = sol / tok
        by_min.setdefault(m, []).append((ts, p, tok, sol))
    out: List[Dict] = []
    for m in sorted(by_min.keys()):
        arr = sorted(by_min[m])
        # volume percentile filter (by tokens or sol)
        if drop_pct and drop_pct > 0 and arr:
            vols = [float(a[2] if weight_by == "tokens" else a[3]) for a in arr]
            cut = _percentile(vols, float(drop_pct))
            arr = [a for a in arr if (float(a[2]) if weight_by == "tokens" else float(a[3])) >= cut]
        # IQR filter on price in a minute
        if iqr_k is not None and arr:
            prices = [float(a[1]) for a in arr]
            q1 = _percentile(prices, 25)
            q3 = _percentile(prices, 75)
            iqr = q3 - q1
            lo = q1 - float(iqr_k) * iqr
            hi = q3 + float(iqr_k) * iqr
            arr = [a for a in arr if lo <= float(a[1]) <= hi]
        o = arr[0][1]; c = arr[-1][1]
        h = max(x[1] for x in arr); l = min(x[1] for x in arr)
        vtok = sum(x[2] for x in arr)
        vsol = sum(x[3] for x in arr)
        # VWAP by configured weight
        if weight_by == "sol":
            den = vsol
            num = sum(x[1]*x[3] for x in arr)
        else:
            den = vtok
            num = sum(x[1]*x[2] for x in arr)
        vwap = (num/den) if den > 0 else c
        out.append({
            't': m,
            'o': float(o), 'h': float(h), 'l': float(l), 'c': float(c),
            'vwap': float(vwap),
            'volume_tokens': float(vtok), 'volume_sol': float(vsol),
            'trades_count': len(arr)
        })
    return out


async def main():
    ap = argparse.ArgumentParser(description='Analyze pair shapes from DB')
    ap.add_argument('--pair', required=True, help='token_pair address')
    ap.add_argument('--hours', type=int, default=24, help='lookback window in hours')
    ap.add_argument('--include-withdraw', action='store_true', help='include withdraw in SOL bars')
    ap.add_argument('--drop-pct', type=float, default=0.0, help='drop lowest p%% by volume per minute (0-5)')
    ap.add_argument('--iqr-k', type=float, default=None, help='IQR k for price filtering in minute (e.g., 1.5)')
    ap.add_argument('--weight-by', choices=['tokens','sol'], default='tokens', help='VWAP weight: tokens or sol')
    ap.add_argument('--json', action='store_true', help='print JSON result only')
    args = ap.parse_args()

    end = int(time.time())
    start = end - int(args.hours * 3600)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tok = await conn.fetchrow('SELECT id, token_address, name, symbol FROM tokens WHERE token_pair = $1', args.pair)
        if not tok:
            print(json.dumps({'success': False, 'error': 'pair not found'}, ensure_ascii=False))
            return
        token_id = int(tok['id'])
        rows = await conn.fetch(
            'SELECT timestamp, direction, amount_tokens, amount_sol, token_price_usd, signature\n'
            'FROM trades WHERE token_id=$1 AND timestamp BETWEEN $2 AND $3 ORDER BY timestamp ASC',
            token_id, start, end
        )
        trades = [dict(r) for r in rows]

    # Build both shapes
    usd_second_series = _group_second_avg_usd(trades)
    sol_minute_bars_inc_withdraw = _minute_bars_sol(
        trades,
        drop_withdraw=not args.include_withdraw and False,
        drop_pct=args.drop_pct,
        iqr_k=args.iqr_k,
        weight_by=args.weight_by,
    )
    sol_minute_bars = _minute_bars_sol(
        trades,
        drop_withdraw=not args.include_withdraw,
        drop_pct=args.drop_pct,
        iqr_k=args.iqr_k,
        weight_by=args.weight_by,
    )

    # Quick diagnostics
    n = len(trades)
    n_withdraw = sum(1 for t in trades if t.get('direction') == 'withdraw')
    # Extreme price_sol movers
    def psol(t):
        try:
            tok = Decimal(str(t['amount_tokens']))
            sol = Decimal(str(t['amount_sol']))
            return float(sol / tok) if tok > 0 and sol > 0 else 0.0
        except Exception:
            return 0.0
    worst = sorted(trades, key=psol)[:5]
    top = sorted(trades, key=psol)[-5:]

    result = {
        'success': True,
        'token': {
            'id': token_id,
            'address': tok['token_address'],
            'name': tok.get('name'),
            'symbol': tok.get('symbol'),
        },
        'window': {'start': start, 'end': end, 'hours': args.hours},
        'counts': {
            'trades_total': n,
            'withdraw_records': n_withdraw,
            'usd_second_points': len(usd_second_series),
            'sol_minute_bars_withdraw_included': len(sol_minute_bars_inc_withdraw),
            'sol_minute_bars': len(sol_minute_bars),
        },
        'sol_bars': sol_minute_bars,
        'sol_bars_withdraw_included': sol_minute_bars_inc_withdraw,
        'usd_second': usd_second_series[-120:],  # last 2 minutes worth of seconds if dense
        'extremes': {
            'lowest_price_sol_trades': [
                {
                    'timestamp': int(t['timestamp']),
                    'direction': t['direction'],
                    'price_sol': round(psol(t), 12),
                    'amount_tokens': float(t['amount_tokens']),
                    'amount_sol': float(t['amount_sol']),
                    'signature': t['signature'],
                } for t in worst
            ],
            'highest_price_sol_trades': [
                {
                    'timestamp': int(t['timestamp']),
                    'direction': t['direction'],
                    'price_sol': round(psol(t), 12),
                    'amount_tokens': float(t['amount_tokens']),
                    'amount_sol': float(t['amount_sol']),
                    'signature': t['signature'],
                } for t in top
            ],
        },
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
