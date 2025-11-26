"""Feature builder helpers for AI forecasting.

Stage 1: minimal set — fetch last K seconds of usd_price and compute simple aggregates.
"""

from typing import List, Dict, Tuple
import numpy as np
from _v3_db_pool import get_db_pool
from ai.config import ENCODER_SEC


async def fetch_window_prices(token_id: int, origin_ts: int, k: int = ENCODER_SEC) -> List[float]:
    """Fetch last K market cap points from token_metrics_seconds table for token.
    
    ВИПРАВЛЕНО: Беремо історичні дані ДО моменту origin_ts для прогнозування.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # ВИПРАВЛЕНО: Беремо історичні дані ДО моменту origin_ts
        rows = await conn.fetch(
            """
            SELECT ts, mcap
            FROM token_metrics_seconds
            WHERE token_id=$1 AND ts <= $2 AND mcap IS NOT NULL AND mcap > 0
            ORDER BY ts DESC
            LIMIT $3
            """,
            token_id, origin_ts, k,
        )
        
        if not rows:
            return []
            
        # Extract market cap values
        prices = []
        for r in rows:
            try:
                mcap = float(r["mcap"] or 0)
                if mcap > 0:
                    prices.append(mcap)
            except Exception:
                continue
                
        # Take last k prices and reverse to chronological order
        return list(reversed(prices[-k:])) if prices else []


def make_basic_features(prices: List[float]) -> Tuple[np.ndarray, Dict[str, float]]:
    if not prices:
        return np.zeros((1, 3), dtype=float), {"p0": 0.0, "slope": 0.0}
    p0 = float(prices[-1])
    avg_p = float(np.mean(prices)) if prices else 0.0
    std_p = float(np.std(prices)) if prices else 0.0
    slope = (p0 - float(prices[0])) / max(1.0, len(prices)) / (avg_p or 1.0)
    x = np.array([[avg_p, std_p, slope]], dtype=float)
    meta = {"p0": p0, "slope": slope}
    return x, meta
