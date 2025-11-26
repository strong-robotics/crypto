#!/usr/bin/env python3
"""Risk scoring based on token audit flags and recent behavior.

Produces ai_risk_assessments rows with risk_score (0..1), risk_tier and flags.
"""

import asyncio
from typing import Dict, Tuple

from _v3_db_pool import get_db_pool


async def _fetch_token_audit(conn, token_id: int) -> Dict:
    row = await conn.fetchrow(
        """
        SELECT blockaid_rugpull, mint_authority_disabled, freeze_authority_disabled,
               top_holders_percentage, dev_balance_percentage
        FROM tokens WHERE id=$1
        """,
        token_id,
    )
    return dict(row) if row else {}


async def _recent_liquidity_slope(conn, token_id: int, window: int = 15) -> float:
    rows = await conn.fetch(
        """
        SELECT liquidity FROM token_metrics_seconds
        WHERE token_id=$1 ORDER BY ts DESC LIMIT $2
        """,
        token_id, window,
    )
    vals = [float(r["liquidity"] or 0) for r in rows][::-1]
    if len(vals) < 2:
        return 0.0
    start, end = vals[0], vals[-1]
    avg = sum(vals) / len(vals) if vals else 1.0
    return (end - start) / max(1.0, len(vals)) / max(1e-9, avg)


def _tier(score: float) -> str:
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "mid"
    return "low"


async def compute_risk_for_token(token_id: int) -> Tuple[float, str, Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        audit = await _fetch_token_audit(conn, token_id)
        slope = await _recent_liquidity_slope(conn, token_id)
        flags: Dict[str, bool] = {}
        score = 0.0

        if audit.get("blockaid_rugpull") is True:
            flags["rugpull"] = True
            score += 0.5
        if audit.get("mint_authority_disabled") is False:
            flags["mint_enabled"] = True
            score += 0.2
        if audit.get("freeze_authority_disabled") is False:
            flags["freeze_enabled"] = True
            score += 0.1

        th = float(audit.get("top_holders_percentage") or 0.0)
        if th >= 75.0:
            flags["top_holders_concentration"] = True
            score += 0.2

        devp = float(audit.get("dev_balance_percentage") or 0.0)
        if devp >= 10.0:
            flags["dev_concentration"] = True
            score += 0.1

        if slope < -0.02:  # fast liquidity drop
            flags["liq_drop"] = True
            score += 0.2

        score = min(1.0, score)
        return score, _tier(score), flags


async def save_risk(token_id: int, model_id: int = None) -> Dict:
    score, tier, flags = await compute_risk_for_token(token_id)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ai_risk_assessments(token_id, model_id, risk_score, risk_tier, risk_flags)
            VALUES ($1,$2,$3,$4,$5)
            """,
            token_id, model_id, score, tier, flags,
        )
    return {"token_id": token_id, "risk_score": score, "risk_tier": tier, "risk_flags": flags}


if __name__ == "__main__":
    import sys
    tid = int(sys.argv[1])
    res = asyncio.run(save_risk(tid))
    print(res)

