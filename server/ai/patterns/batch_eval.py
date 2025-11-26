#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import csv
from typing import Dict, Any, List, Tuple

from _v3_db_pool import get_db_pool
from config import config
from ai.patterns.full_series_classifier import compute_full_features, choose_best_pattern


async def _list_candidate_tokens(history_only: bool = True, min_points: int = 30, limit: int = 10000) -> List[Dict[str, Any]]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # For history_only, use tokens_history table; otherwise use tokens table
        tokens_table = "tokens_history" if history_only else "tokens"
        metrics_table = "token_metrics_seconds_history" if history_only else "token_metrics_seconds"
        where_hist = ""
        rows = await conn.fetch(
            f"""
            WITH mc AS (
              SELECT token_id, COUNT(*) AS cnt
              FROM {metrics_table}
              WHERE usd_price IS NOT NULL AND usd_price>0
              GROUP BY token_id
            )
            SELECT t.id AS token_id, t.token_address,
                   COALESCE(t.pattern_code,'unknown') AS prev_code,
                   mc.cnt AS points,
                   (t.archived_at IS NOT NULL) AS is_archived,
                   wh_entry.entry_iteration, wh_entry.entry_price_usd, wh_entry.entry_token_amount,
                   wh_exit.exit_iteration, wh_exit.exit_price_usd, wh_exit.exit_token_amount
            FROM {tokens_table} t
            JOIN mc ON mc.token_id=t.id
            LEFT JOIN (
                SELECT DISTINCT ON (token_id) token_id, entry_iteration, entry_price_usd, entry_token_amount
                FROM wallet_history WHERE exit_iteration IS NULL
                ORDER BY token_id, id DESC
            ) wh_entry ON wh_entry.token_id = t.id
            LEFT JOIN (
                SELECT DISTINCT ON (token_id) token_id, exit_iteration, exit_price_usd, exit_token_amount
                FROM wallet_history WHERE exit_iteration IS NOT NULL
                ORDER BY token_id, id DESC
            ) wh_exit ON wh_exit.token_id = t.id
            {where_hist}
            AND mc.cnt >= $1
            ORDER BY t.id ASC
            LIMIT $2
            """,
            int(min_points), int(limit)
        )
        return [dict(r) for r in rows]


async def _load_full_series(conn, token_id: int) -> Dict[str, List[float]]:
    rows = await conn.fetch(
        """
        SELECT ts, usd_price, liquidity, mcap, holder_count, buy_count, sell_count
        FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
        ORDER BY ts ASC
        """,
        token_id,
    )
    full = [dict(r) for r in rows]
    return {
        "price": [float(r.get("usd_price") or 0.0) for r in full],
        "liquidity": [float(r.get("liquidity") or 0.0) for r in full],
        "mcap": [float(r.get("mcap") or 0.0) for r in full],
        "holders": [float(r.get("holder_count") or 0.0) for r in full],
        "buy_count": [float(r.get("buy_count") or 0.0) for r in full],
        "sell_count": [float(r.get("sell_count") or 0.0) for r in full],
    }


async def batch_eval(history_only: bool = True, min_points: int = 30, limit: int = 10000) -> Dict[str, Any]:
    tokens = await _list_candidate_tokens(history_only, min_points, limit)
    if not tokens:
        return {"success": False, "message": "No tokens found to evaluate"}

    pool = await get_db_pool()
    out_dir = os.path.join("data", "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "pattern_eval_full_series.csv")

    summary = {"total": 0, "mismatch": 0, "per_code": {}, "prev_code": {}}

    async with pool.acquire() as conn:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["token_id", "token_address", "points", "is_archived",
                        "prev_code", "new_code", "score",
                        "buy_iter", "sell_iter", "buy_price", "sell_price", "buy_amt", "sell_amt",
                        "slope", "r2", "mdd", "recovery", "run_up", "vol", "monotone", "tx_total", "sell_share"])
            for t in tokens:
                token_id = int(t["token_id"])  # type: ignore
                series = await _load_full_series(conn, token_id)
                feats = compute_full_features(series)
                code, score = choose_best_pattern(feats)
                prev = (t.get("prev_code") or "unknown").lower()
                mismatch = (code != prev)
                summary["total"] += 1
                if mismatch:
                    summary["mismatch"] += 1
                summary["per_code"][code] = summary["per_code"].get(code, 0) + 1
                summary["prev_code"][prev] = summary["prev_code"].get(prev, 0) + 1
                w.writerow([
                    token_id,
                    t.get("token_address", ""),
                    int(t.get("points") or 0),
                    bool(t.get("is_archived")),
                    prev,
                    code,
                    round(score, 2),
                    (t.get("entry_iteration") if t.get("entry_iteration") is not None else ""),
                    (t.get("exit_iteration") if t.get("exit_iteration") is not None else ""),
                    (float(t.get("entry_price_usd") or 0.0) if t.get("entry_iteration") is not None else ""),
                    (float(t.get("exit_price_usd") or 0.0) if t.get("exit_iteration") is not None else ""),
                    (float(t.get("entry_token_amount") or 0.0) if t.get("entry_iteration") is not None else ""),
                    (float(t.get("exit_token_amount") or 0.0) if t.get("exit_iteration") is not None else ""),
                    round(feats.get("slope_total", 0.0), 6),
                    round(feats.get("r2_total", 0.0), 4),
                    round(feats.get("max_drawdown", 0.0), 4),
                    round(feats.get("recovery_ratio", 0.0), 4),
                    round(feats.get("run_up_total", 0.0), 4),
                    round(feats.get("volatility", 0.0), 6),
                    round(feats.get("monotonicity", 0.0), 4),
                    int(feats.get("tx_total", 0.0) or 0),
                    round(feats.get("sell_share", 0.0), 4),
                ])

    return {"success": True, "out": out_path, "summary": summary}


if __name__ == "__main__":
    async def _run():
        res = await batch_eval(history_only=True, min_points=30, limit=10000)
        if res.get("success"):
            print("✅ Full-series pattern eval written:", res["out"])  # type: ignore
            print("Summary:", res["summary"])  # type: ignore
        else:
            print("❌", res.get("message"))

    asyncio.run(_run())
