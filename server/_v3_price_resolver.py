#!/usr/bin/env python3
"""
V3 Price Resolver

Task:
- Walk through the trades table and compute a USD price per second for tokens
- Upsert this price into token_metrics_seconds
- If metrics rows have zero mcap/liquidity, infer them from token data and the computed price

Notes:
- Per-second price is computed as median of trade prices within that second for robustness
- If trade.token_price_usd is missing/zero, we fall back to amount_sol * current SOL price / amount_tokens
- MCAP = price * circ_supply (fallback: token_supply or total_supply)
- FDV  = price * total_supply     (fallback: circ_supply or token_supply)
- Liquidity defaults to tokens.liquidity if available

Usage:
  python -m server._v3_price_resolver --all
  python -m server._v3_price_resolver --token-id 123
  python -m server._v3_price_resolver --limit 50
"""

import asyncio
import argparse
from statistics import median
from typing import Dict, List, Optional, Tuple

from _v3_db_pool import get_db_pool
from _v2_sol_price import get_current_sol_price
from config import config


async def _get_tokens_with_trades(limit: Optional[int] = None) -> List[Dict]:
    """Return tokens that have at least one trade."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id AS token_id, t.token_address
            FROM tokens t
            WHERE EXISTS (SELECT 1 FROM trades tr WHERE tr.token_id = t.id)
            ORDER BY t.id ASC
            LIMIT $1
            """,
            limit if limit and limit > 0 else None,
        )
        return [{"token_id": r["token_id"], "token_address": r["token_address"]} for r in rows]


async def _get_token_row(token_id: int) -> Optional[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id,
                   circ_supply, total_supply, token_supply,
                   liquidity, fdv, mcap, usd_price, price_block_id
            FROM tokens WHERE id = $1
            """,
            token_id,
        )
        if not row:
            return None
        def _f(x):
            try:
                return float(x) if x is not None else 0.0
            except Exception:
                return 0.0
        return {
            "id": int(row["id"]),
            "circ_supply": _f(row["circ_supply"]),
            "total_supply": _f(row["total_supply"]),
            "token_supply": _f(row["token_supply"]),
            "liquidity": _f(row["liquidity"]),
            "fdv": _f(row["fdv"]),
            "mcap": _f(row["mcap"]),
            "usd_price": _f(row["usd_price"]),
            "price_block_id": int(row["price_block_id"]) if row["price_block_id"] is not None else None,
        }


async def _fetch_trades(token_id: int, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if start_ts and end_ts and start_ts < end_ts:
            rows = await conn.fetch(
                """
                SELECT timestamp, token_price_usd, amount_sol, amount_tokens
                FROM trades
                WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                ORDER BY timestamp ASC
                """,
                token_id,
                int(start_ts),
                int(end_ts),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT timestamp, token_price_usd, amount_sol, amount_tokens
                FROM trades
                WHERE token_id = $1
                ORDER BY timestamp ASC
                """,
                token_id,
            )
        out: List[Dict] = []
        for r in rows:
            # Parse floats safely (columns stored as TEXT sometimes)
            def _f(x):
                try:
                    return float(x) if x is not None else 0.0
                except Exception:
                    return 0.0
            out.append(
                {
                    "timestamp": int(r["timestamp"]),
                    "token_price_usd": _f(r["token_price_usd"]),
                    "amount_sol": _f(r["amount_sol"]),
                    "amount_tokens": _f(r["amount_tokens"]),
                }
            )
        return out


def _infer_price_from_trade(tr: Dict, sol_price: float) -> float:
    """Fallback: compute price from amount_sol and amount_tokens using current SOL price."""
    amt_tokens = tr.get("amount_tokens", 0.0) or 0.0
    amt_sol = tr.get("amount_sol", 0.0) or 0.0
    if amt_tokens <= 0 or amt_sol <= 0:
        return 0.0
    try:
        usd = float(amt_sol) * float(sol_price)
        return usd / float(abs(amt_tokens)) if abs(amt_tokens) > 0 else 0.0
    except Exception:
        return 0.0


def _choose_supply_for_mcap(token: Dict) -> float:
    # Prefer circulating supply, then token_supply, then total_supply
    for key in ("circ_supply", "token_supply", "total_supply"):
        v = float(token.get(key, 0.0) or 0.0)
        if v and v > 0:
            return v
    return 0.0


def _choose_supply_for_fdv(token: Dict) -> float:
    # Prefer total_supply, then circ_supply, then token_supply
    for key in ("total_supply", "circ_supply", "token_supply"):
        v = float(token.get(key, 0.0) or 0.0)
        if v and v > 0:
            return v
    return 0.0


async def _build_price_by_second_from_trades(token_id: int) -> Dict[int, float]:
    """Aggregate trades into per-second price using median within each second."""
    trades = await _fetch_trades(token_id)
    if not trades:
        return {}
    sol_price = get_current_sol_price() or 0.0
    if sol_price <= 0:
        try:
            sol_price = float(getattr(config, 'SOL_PRICE_FALLBACK', 0.0) or 0.0)
        except Exception:
            sol_price = 0.0
    price_map: Dict[int, List[float]] = {}
    for tr in trades:
        ts = int(tr["timestamp"]) if tr.get("timestamp") is not None else None
        if not ts:
            continue
        price = float(tr.get("token_price_usd", 0.0) or 0.0)
        if price <= 0:
            # Fallback to SOL-based computation if available
            if sol_price <= 0:
                price = 0.0
            else:
                price = _infer_price_from_trade(tr, sol_price)
        if price > 0:
            price_map.setdefault(ts, []).append(price)
    out: Dict[int, float] = {}
    for ts, vals in price_map.items():
        if not vals:
            continue
        try:
            # Use LAST price in the second for market cap calculation
            # Market cap should be based on the most recent price, not median
            out[ts] = float(vals[-1])  # Last price in the second
        except Exception:
            out[ts] = float(vals[-1])  # Fallback to last price
    
            # Don't fill gaps - preserve original data structure
            # Only process existing trade timestamps
    
    return out


async def _upsert_metrics_for_seconds(
    token_id: int,
    seconds_to_price: Dict[int, float],
    token_row: Dict,
) -> int:
    """Upsert per-second rows into token_metrics_seconds with inferred metrics when missing.

    Returns number of rows upserted/updated.
    """
    if not seconds_to_price:
        return 0
    pool = await get_db_pool()
    supply_mcap = _choose_supply_for_mcap(token_row)
    supply_fdv = _choose_supply_for_fdv(token_row)
    default_liq = float(token_row.get("liquidity", 0.0) or 0.0)
    pblk = token_row.get("price_block_id")
    # Prepare batch upserts
    params: List[Tuple] = []
    for ts, price in seconds_to_price.items():
        # Calculate mcap using the correct price and supply
        supply_mcap = _choose_supply_for_mcap(token_row)
        supply_fdv = _choose_supply_for_fdv(token_row)
        default_liq = float(token_row.get("liquidity", 0.0) or 0.0)
        
        # Don't calculate mcap - preserve existing values
        # Only calculate fdv and liquidity for new records
        mcap = None  # Keep existing mcap
        fdv = float(price) * float(supply_fdv) if supply_fdv > 0 else None
        liq = default_liq if default_liq and default_liq > 0 else None
        
        params.append((token_id, int(ts), float(price), liq, fdv, mcap, pblk))

    async with pool.acquire() as conn:
        # Use ON CONFLICT to preserve existing non-zero metrics
        sql = (
            """
            INSERT INTO token_metrics_seconds (token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (token_id, ts) DO UPDATE SET
                usd_price = EXCLUDED.usd_price,
                liquidity = COALESCE(NULLIF(token_metrics_seconds.liquidity, 0), EXCLUDED.liquidity),
                fdv = COALESCE(NULLIF(token_metrics_seconds.fdv, 0), EXCLUDED.fdv),
                -- Don't update mcap - keep existing value
                price_block_id = COALESCE(EXCLUDED.price_block_id, token_metrics_seconds.price_block_id)
            """
        )
        count = 0
        # execute many in manageable chunks
        CHUNK = 1000
        for i in range(0, len(params), CHUNK):
            chunk = params[i : i + CHUNK]
            await conn.executemany(sql, chunk)
            count += len(chunk)
        return count


async def resolve_token_prices(token_id: int, verbose: bool = True) -> Dict:
    """Compute per-second prices from trades and upsert metrics for a single token."""
    token = await _get_token_row(token_id)
    if not token:
        return {"success": False, "token_id": token_id, "updated": 0, "message": "token not found"}
    seconds_to_price = await _build_price_by_second_from_trades(token_id)
    if not seconds_to_price:
        return {"success": True, "token_id": token_id, "updated": 0, "message": "no trades"}
    updated = await _upsert_metrics_for_seconds(token_id, seconds_to_price, token)
    if verbose:
        print(f"🧮 token_id={token_id}: upserted {updated} metric seconds")
    return {"success": True, "token_id": token_id, "updated": updated}


async def resolve_all_prices(limit: Optional[int] = None, verbose: bool = True) -> Dict:
    """Resolve for all tokens with trades (optionally limited)."""
    tokens = await _get_tokens_with_trades(limit=limit)
    if not tokens:
        return {"success": True, "total": 0, "updated": 0}
    total_updated = 0
    for t in tokens:
        try:
            res = await resolve_token_prices(int(t["token_id"]), verbose=verbose)
            total_updated += int(res.get("updated", 0) or 0)
        except Exception as e:
            if verbose:
                print(f"❌ Error resolving token_id={t['token_id']}: {e}")
    return {"success": True, "total": len(tokens), "updated": total_updated}


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V3 Price Resolver: build per-second prices from trades")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Process all tokens that have trades")
    g.add_argument("--token-id", type=int, help="Process a single token id")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tokens for --all")
    parser.add_argument("--quiet", action="store_true", help="Less verbose output")
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> None:
    if args.token_id:
        await resolve_token_prices(args.token_id, verbose=not args.quiet)
    else:
        await resolve_all_prices(limit=(args.limit or None), verbose=not args.quiet)


def main() -> None:
    args = _parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
