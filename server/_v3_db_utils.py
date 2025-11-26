#!/usr/bin/env python3
"""
Database Utilities Module
Common database operations used across multiple modules.

This module provides reusable functions to eliminate code duplication.
"""

from typing import Optional, Dict, Any, List
import asyncpg
from config import config

async def has_open_position(conn: asyncpg.Connection, token_id: int) -> bool:
    """Check if token has open position in wallet_history.
    
    Args:
        conn: Database connection
        token_id: Token ID to check
        
    Returns:
        True if token has open position, False otherwise
    """
    row = await conn.fetchrow(
        "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
        token_id
    )
    return row is not None


async def get_open_position(conn: asyncpg.Connection, token_id: int) -> Optional[Dict[str, Any]]:
    """Get open position details from wallet_history.
    
    Args:
        conn: Database connection
        token_id: Token ID to check
        
    Returns:
        Dictionary with position details (id, wallet_id, entry_token_amount, etc.) or None
    """
    row = await conn.fetchrow(
        """
        SELECT id, wallet_id, entry_token_amount, entry_amount_usd, entry_price_usd, entry_iteration
        FROM wallet_history
        WHERE token_id=$1 AND exit_iteration IS NULL
        LIMIT 1
        """,
        token_id
    )
    if row:
        return dict(row)
    return None


async def get_token_iterations_count(conn: asyncpg.Connection, token_id: int) -> int:
    """Get count of iterations (records with valid price) for token.
    
    Args:
        conn: Database connection
        token_id: Token ID
        
    Returns:
        Number of iterations (records with usd_price IS NOT NULL AND usd_price > 0)
    """
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0",
            token_id
        ) or 0
    )


async def update_token_updated_at(conn: asyncpg.Connection, token_id: int) -> None:
    """Update token_updated_at timestamp for a token.
    
    Args:
        conn: Database connection
        token_id: Token ID
    """
    await conn.execute(
        "UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1",
        token_id
    )


async def save_token_metrics(
    conn: asyncpg.Connection,
    token_id: int,
    ts: int,
    usd_price: Optional[float],
    liquidity: Optional[float],
    fdv: Optional[float],
    mcap: Optional[float],
    price_block_id: Optional[int],
    jupiter_slot: Optional[int] = None,
    holder_count: Optional[int] = None
) -> None:
    """Save token metrics to token_metrics_seconds table.
    
    Args:
        conn: Database connection
        token_id: Token ID
        ts: Timestamp (Unix seconds)
        usd_price: USD price
        liquidity: Liquidity
        fdv: Fully Diluted Valuation
        mcap: Market Cap
        price_block_id: Price block ID
        jupiter_slot: Jupiter slot (optional, defaults to price_block_id)
        holder_count: Holder count (optional)
    """
    if jupiter_slot is None:
        jupiter_slot = price_block_id
    
    if holder_count is not None:
        # With holder_count
        await conn.execute(
            """
            INSERT INTO token_metrics_seconds (
                token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot, holder_count
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (token_id, ts) DO UPDATE SET
                usd_price = EXCLUDED.usd_price,
                liquidity = EXCLUDED.liquidity,
                fdv = EXCLUDED.fdv,
                mcap = EXCLUDED.mcap,
                price_block_id = EXCLUDED.price_block_id,
                jupiter_slot = EXCLUDED.jupiter_slot,
                holder_count = EXCLUDED.holder_count
            """,
            token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot, holder_count
        )
    else:
        # Without holder_count
        await conn.execute(
            """
            INSERT INTO token_metrics_seconds (
                token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (token_id, ts) DO UPDATE SET
                usd_price = EXCLUDED.usd_price,
                liquidity = EXCLUDED.liquidity,
                fdv = EXCLUDED.fdv,
                mcap = EXCLUDED.mcap,
                price_block_id = EXCLUDED.price_block_id,
                jupiter_slot = EXCLUDED.jupiter_slot
            """,
            token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
        )


async def evaluate_holder_momentum(
    conn: asyncpg.Connection,
    token_id: int,
    check_iter: Optional[int] = None
) -> Dict[str, Any]:
    """Evaluate holder growth momentum for a token using holder_count snapshots.
    
    Returns:
        dict with:
            ok (bool): whether momentum requirements satisfied
            reason (str): description when ok=False
            metrics (dict): snapshot values and deltas
    """
    check_iter_val = int(check_iter or getattr(config, 'HOLDER_MOMENTUM_CHECK_ITER', 500))
    min_at_check = int(getattr(config, 'HOLDER_MOMENTUM_MIN_AT_CHECK', 500))
    min_at_400 = int(getattr(config, 'HOLDER_MOMENTUM_MIN_AT_400', 350))
    min_delta = int(getattr(config, 'HOLDER_MOMENTUM_MIN_DELTA100', 120))
    min_rate = float(getattr(config, 'HOLDER_MOMENTUM_MIN_RATE', 0.8))
    rate_lookback = int(getattr(config, 'HOLDER_MOMENTUM_RATE_LOOKBACK', 200))
    
    sample_points: List[int] = []
    step = 50
    iter_point = check_iter_val
    while iter_point > 0 and len(sample_points) < 20:
        sample_points.append(iter_point)
        iter_point -= step
        if iter_point < check_iter_val - 500:
            break
    baseline_iter = max(1, check_iter_val - rate_lookback)
    if baseline_iter not in sample_points:
        sample_points.append(baseline_iter)
    if 200 not in sample_points and check_iter_val >= 200:
        sample_points.append(200)
    sample_points = sorted({pt for pt in sample_points if pt > 0})
    
    rows = await conn.fetch(
        """
        WITH ordered AS (
            SELECT holder_count,
                   ROW_NUMBER() OVER (PARTITION BY token_id ORDER BY ts) AS iteration
            FROM token_metrics_seconds
            WHERE token_id=$1
        )
        SELECT iteration, holder_count
        FROM ordered
        WHERE iteration = ANY($2::int[])
        """,
        token_id, sample_points
    )
    counts = {int(row["iteration"]): (int(row["holder_count"]) if row["holder_count"] is not None else None) for row in rows}
    
    def get_count(iteration: int) -> Optional[int]:
        value = counts.get(iteration)
        if value is None:
            return None
        return value
    
    holders_check = get_count(check_iter_val)
    holders_prev_50 = get_count(check_iter_val - 50)
    holders_prev_100 = get_count(check_iter_val - 100)
    holders_400 = get_count(400) if check_iter_val >= 400 else None
    holders_rate_base = get_count(baseline_iter)
    
    metrics = {
        "holders_check": holders_check,
        "holders_prev_50": holders_prev_50,
        "holders_prev_100": holders_prev_100,
        "holders_400": holders_400,
        "holders_rate_base": holders_rate_base,
        "baseline_iter": baseline_iter,
        "check_iter": check_iter_val,
    }
    
    if holders_check is None:
        return {"ok": False, "reason": "missing_holder_check", "metrics": metrics}
    
    if holders_check < min_at_check:
        return {"ok": False, "reason": "insufficient_total_holders", "metrics": metrics}
    
    if holders_400 is None or holders_400 < min_at_400:
        return {"ok": False, "reason": "insufficient_400_holders", "metrics": metrics}
    
    if holders_prev_100 is None:
        return {"ok": False, "reason": "missing_prev_window", "metrics": metrics}
    
    delta_last_100 = holders_check - holders_prev_100
    metrics["delta_last_100"] = delta_last_100
    
    if delta_last_100 < min_delta:
        return {"ok": False, "reason": "weak_last100_growth", "metrics": metrics}
    
    if holders_rate_base is None:
        return {"ok": False, "reason": "missing_rate_base", "metrics": metrics}
    
    rate_window = check_iter_val - baseline_iter
    avg_rate = (holders_check - holders_rate_base) / rate_window if rate_window > 0 else 0.0
    metrics["avg_rate"] = avg_rate
    
    if avg_rate < min_rate:
        return {"ok": False, "reason": "weak_avg_growth", "metrics": metrics}
    
    return {"ok": True, "reason": "ok", "metrics": metrics}


def safe_numeric(value: Any, max_val: float = 999999.9999) -> Optional[float]:
    """Convert value to float with max value limit.
    
    Used for protecting against overflow in numeric fields.
    
    Args:
        value: Value to convert
        max_val: Maximum allowed value (default: 999999.9999)
        
    Returns:
        Float value (limited to max_val) or None if conversion fails
    """
    try:
        v = float(value) if value is not None else None
        if v is None:
            return None
        if abs(v) > max_val:
            return max_val if v > 0 else -max_val
        return v
    except (ValueError, TypeError):
        return None


async def update_token_stats(
    conn: asyncpg.Connection,
    token_id: int,
    stats: Dict[str, Any],
    period: str,
    convert_func = None
) -> None:
    """Update token stats for a specific period (5m, 1h, 6h, 24h).
    
    Args:
        conn: Database connection
        token_id: Token ID
        stats: Stats dictionary from Jupiter API
        period: Period suffix ('5m', '1h', '6h', '24h')
        convert_func: Function to convert values (default: safe_numeric)
    """
    if convert_func is None:
        convert_func = safe_numeric
    
    period_suffix = f"_{period}"
    
    await conn.execute(
        f"""
        UPDATE tokens SET
            price_change{period_suffix} = $2,
            holder_change{period_suffix} = $3,
            liquidity_change{period_suffix} = $4,
            volume_change{period_suffix} = $5,
            buy_volume{period_suffix} = $6,
            sell_volume{period_suffix} = $7,
            buy_organic_volume{period_suffix} = $8,
            sell_organic_volume{period_suffix} = $9,
            num_buys{period_suffix} = $10,
            num_sells{period_suffix} = $11,
            num_traders{period_suffix} = $12
        WHERE id = $1
        """,
        token_id,
        convert_func(stats.get('priceChange')),
        convert_func(stats.get('holderChange')),
        convert_func(stats.get('liquidityChange')),
        convert_func(stats.get('volumeChange')),
        convert_func(stats.get('buyVolume')),
        convert_func(stats.get('sellVolume')),
        convert_func(stats.get('buyOrganicVolume')),
        convert_func(stats.get('sellOrganicVolume')),
        stats.get('numBuys'),
        stats.get('numSells'),
        stats.get('numTraders')
    )


async def update_token_audit(
    conn: asyncpg.Connection,
    token_id: int,
    audit: Dict[str, Any],
    convert_func = None
) -> None:
    """Update token audit fields.
    
    Args:
        conn: Database connection
        token_id: Token ID
        audit: Audit dictionary from Jupiter API
        convert_func: Function to convert numeric values (default: safe_numeric)
    """
    if convert_func is None:
        convert_func = safe_numeric
    
    await conn.execute(
        """
        UPDATE tokens SET
            mint_authority_disabled = $2,
            freeze_authority_disabled = $3,
            top_holders_percentage = $4,
            dev_balance_percentage = $5,
            blockaid_rugpull = $6
        WHERE id = $1
        """,
        token_id,
        audit.get('mintAuthorityDisabled'),
        audit.get('freezeAuthorityDisabled'),
        convert_func(audit.get('topHoldersPercentage')),
        convert_func(audit.get('devBalancePercentage')),
        audit.get('blockaidRugpull')
    )


async def update_pair_resolve_attempts(
    conn: asyncpg.Connection,
    token_id: int,
    is_valid_pair: bool
) -> None:
    """Update pair_resolve_attempts counter.
    
    Args:
        conn: Database connection
        token_id: Token ID
        is_valid_pair: True if pair is valid (reset counter), False if invalid (increment counter)
    """
    if is_valid_pair:
        await conn.execute(
            "UPDATE tokens SET pair_resolve_attempts = 0 WHERE id = $1",
            token_id
        )
    else:
        await conn.execute(
            "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1",
            token_id
        )
