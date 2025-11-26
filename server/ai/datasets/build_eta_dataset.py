"""
Build ETA dataset for early-entry classification and ETA prediction.

Creates sliding-window samples from token_metrics_seconds:
- Window length: 30 seconds of history per sample (encoder)
- Labels per sample @t: 
  * y_hit = 1 if price reaches >= 1.2x within next 120s, else 0
  * ETA_seconds = smallest Δt (1..120) achieving +20% (None if not hit)
  * ETA_bin = binned ETA (e.g., {30,35,...,240}) — here we use {30,40,60,90,120,180,240}

Output: Parquet at data/eta_dataset.parquet with columns:
- token_id, entry_ts, entry_idx, window_sec (15)
- features: dict with time-series arrays and derived metrics
- static: dict with token static fields (pattern_code, organic_score, etc.)
- labels: dict with y_hit, eta_seconds, eta_bin

Note: kept minimal and DB-agnostic. Uses server/_v3_db_pool for asyncpg pool.
"""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _v3_db_pool import get_db_pool


# Config
ENCODER_SEC = int(os.getenv("ETA_ENCODER_SEC", 30))
T_MAX = int(os.getenv("ETA_T_MAX", 120))
TARGET_RET = float(os.getenv("ETA_TARGET_RET", 0.20))
ETA_BINS = [30, 40, 60, 90, 120, 180, 240]
OUT_DIR = os.getenv("ETA_DATA_DIR", "data")
OUT_PATH = os.path.join(OUT_DIR, "eta_dataset.parquet")


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _eta_to_bin(eta: Optional[int]) -> Optional[int]:
    if eta is None:
        return None
    for b in ETA_BINS:
        if eta <= b:
            return b
    return ETA_BINS[-1]


def _derived_features(prices: np.ndarray) -> Dict[str, float]:
    # works on length=ENCODER_SEC window
    eps = 1e-9
    ln_p = np.log(np.clip(prices, eps, None))
    dln = np.diff(ln_p, prepend=ln_p[0])

    def slope_k(k: int) -> float:
        k = min(k, len(ln_p))
        x = np.arange(k)
        y = ln_p[-k:]
        if k < 2:
            return 0.0
        x_mean = x.mean()
        y_mean = y.mean()
        num = ((x - x_mean) * (y - y_mean)).sum()
        den = ((x - x_mean) ** 2).sum() + eps
        return float(num / den)

    def r2_k(k: int) -> float:
        k = min(k, len(ln_p))
        x = np.arange(k)
        y = ln_p[-k:]
        if k < 2:
            return 0.0
        x_mean = x.mean()
        y_mean = y.mean()
        num = ((x - x_mean) * (y - y_mean)).sum()
        den = ((x - x_mean) ** 2).sum() + eps
        beta = num / den
        y_hat = (x - x_mean) * beta + y_mean
        ss_res = ((y - y_hat) ** 2).sum()
        ss_tot = ((y - y_mean) ** 2).sum() + eps
        return float(1.0 - ss_res / ss_tot)

    feats = {
        "slope_5": slope_k(5),
        "slope_10": slope_k(10),
        "slope_15": slope_k(15),
        "accel": slope_k(10) - slope_k(5),
        "vol_dln": float(np.std(dln[-15:])) if len(dln) >= 2 else 0.0,
        "r2_10": r2_k(10),
        "r2_15": r2_k(15),
        "run_up": float(np.max(prices[-15:]) / (prices[-15] + eps) - 1.0),
        "drawdown": float(1.0 - np.min(prices[-15:]) / (prices[-15] + eps)),
    }
    return feats


@dataclass
class Sample:
    token_id: int
    entry_ts: int
    entry_idx: int
    features: Dict[str, Any]
    static: Dict[str, Any]
    labels: Dict[str, Any]


async def _load_tokens(conn) -> pd.DataFrame:
    rows = await conn.fetch(
        """
        SELECT id as token_id,
               token_address,
               COALESCE(pattern_code, 'unknown') AS pattern_code,
               organic_score,
               created_at,
               holder_count AS holder_count_now
        FROM tokens_history
        ORDER BY id ASC
        """
    )
    return pd.DataFrame([dict(r) for r in rows])


async def _load_metrics(conn, token_id: int) -> pd.DataFrame:
    rows = await conn.fetch(
        """
        SELECT ts::bigint AS ts,
               usd_price::double precision AS usd_price,
               liquidity::double precision AS liquidity,
               mcap::double precision AS mcap,
               holder_count::double precision AS holder_count,
               COALESCE(buy_count,0)::double precision AS buy_count,
               COALESCE(sell_count,0)::double precision AS sell_count
        FROM token_metrics_seconds
        WHERE token_id = $1 AND usd_price IS NOT NULL AND usd_price > 0
        ORDER BY ts ASC
        """,
        token_id,
    )
    return pd.DataFrame([dict(r) for r in rows])


def _make_samples_for_token(token_row: pd.Series, mdf: pd.DataFrame) -> List[Sample]:
    if mdf is None or mdf.empty or len(mdf) < ENCODER_SEC + 1:
        return []

    prices = mdf["usd_price"].values.astype(float)
    liquidity = mdf["liquidity"].values.astype(float)
    mcap = mdf["mcap"].values.astype(float)
    holders = mdf["holder_count"].fillna(method="ffill").fillna(0.0).values.astype(float)
    buys = mdf["buy_count"].values.astype(float)
    sells = mdf["sell_count"].values.astype(float)
    ts_arr = mdf["ts"].values.astype(int)

    samples: List[Sample] = []
    n = len(mdf)
    # Sliding windows: t_idx from ENCODER_SEC-1 .. n-2 (we need at least 1s future to evaluate label)
    for t_idx in range(ENCODER_SEC - 1, n - 1):
        start = t_idx - (ENCODER_SEC - 1)
        end = t_idx + 1
        p_win = prices[start:end]
        liq_win = liquidity[start:end]
        mcap_win = mcap[start:end]
        holders_win = holders[start:end]
        buys_win = buys[start:end]
        sells_win = sells[start:end]

        entry_price = p_win[-1]
        # Lookahead for label within T_MAX
        future_stop = min(n, t_idx + 1 + T_MAX)
        future_prices = prices[t_idx + 1:future_stop]
        if future_prices.size == 0:
            continue
        target_price = entry_price * (1.0 + TARGET_RET)
        hit_idx_rel = None
        for k, fp in enumerate(future_prices, start=1):
            if fp >= target_price:
                hit_idx_rel = k
                break

        y_hit = 1 if hit_idx_rel is not None else 0
        eta_seconds = int(hit_idx_rel) if hit_idx_rel is not None else None
        eta_bin = _eta_to_bin(eta_seconds)

        # Derived features from price
        feats = _derived_features(p_win)
        features: Dict[str, Any] = {
            "price": p_win.tolist(),
            "liquidity": liq_win.tolist(),
            "mcap": mcap_win.tolist(),
            "holders": holders_win.tolist(),
            "buy_count": buys_win.tolist(),
            "sell_count": sells_win.tolist(),
            **feats,
        }

        static = {
            "pattern_code": token_row.get("pattern_code", "unknown"),
            "organic_score": _safe_float(token_row.get("organic_score")) or 0.0,
        }
        labels = {
            "y_hit": int(y_hit),
            "eta_seconds": eta_seconds,
            "eta_bin": eta_bin,
            "t_max": T_MAX,
            "target_ret": TARGET_RET,
        }

        samples.append(
            Sample(
                token_id=int(token_row["token_id"]),
                entry_ts=int(ts_arr[t_idx]),
                entry_idx=int(t_idx),
                features=features,
                static=static,
                labels=labels,
            )
        )

    return samples


async def build_eta_dataset() -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    pool = await get_db_pool()
    all_samples: List[Sample] = []
    async with pool.acquire() as conn:
        tokens_df = await _load_tokens(conn)
        if tokens_df.empty:
            raise RuntimeError("No tokens found in tokens_history table")

        for _, trow in tokens_df.iterrows():
            mdf = await _load_metrics(conn, int(trow["token_id"]))
            if mdf is None or mdf.empty:
                continue
            smp = _make_samples_for_token(trow, mdf)
            if smp:
                all_samples.extend(smp)

    if not all_samples:
        raise RuntimeError("No samples generated for ETA dataset")

    # Convert to DataFrame for Parquet
    recs = [
        {
            "token_id": s.token_id,
            "entry_ts": s.entry_ts,
            "entry_idx": s.entry_idx,
            "window_sec": ENCODER_SEC,
            "features": s.features,
            "static": s.static,
            "labels": s.labels,
        }
        for s in all_samples
    ]

    df = pd.DataFrame.from_records(recs)
    df.to_parquet(OUT_PATH, index=False)
    return OUT_PATH


if __name__ == "__main__":
    path = asyncio.run(build_eta_dataset())
    print(f"✅ ETA dataset written: {path}")
