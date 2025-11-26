#!/usr/bin/env python3
"""
Predictive ETA (ML) for a token using only last 15 seconds of metrics.

Uses the baseline model trained by server/ai/training/train_entry_baseline.py
to estimate:
 - p_success: probability to reach +20% (≈ +$1 from $5) within ~120s
 - eta_pred: predicted seconds to target (from regressor; informative if p is high)

Usage examples (run from project root):
  server/venv/bin/python eta_predict.py --token 132
  server/venv/bin/python eta_predict.py --pair 3ngLnB5EEam3SWx8GecfGQ2tALmGLgnMXdDNk6EtPtWd
  server/venv/bin/python eta_predict.py --prob 0.6
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import joblib

# Ensure server/ is importable
ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "server")
if SERVER not in sys.path:
    sys.path.append(SERVER)

from _v3_db_pool import get_db_pool  # type: ignore
from ai.config import ENCODER_SEC, MODELS_DIR  # type: ignore


ETA_MAX_FOR_Y = 120  # should match training script


def _nan_to_num(arr: np.ndarray) -> np.ndarray:
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def _fe_last(values: np.ndarray) -> float:
    return float(values[-1]) if values.size else 0.0


def _fe_slope(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    x = np.arange(values.size, dtype=np.float32)
    y = values.astype(np.float32)
    try:
        m, c = np.polyfit(x, y, 1)
        return float(m)
    except Exception:
        return 0.0


def _fe_vol(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    return float(np.std(values))


def _pick_price(row: Dict) -> Optional[float]:
    mp = row.get("median_token_price")
    if mp not in (None, ""):
        try:
            return float(mp)
        except Exception:
            pass
    up = row.get("usd_price")
    if up not in (None, ""):
        try:
            return float(up)
        except Exception:
            pass
    return None


async def _resolve_token_id(pair: Optional[str], token_id: Optional[int]) -> Optional[int]:
    if token_id is not None:
        return int(token_id)
    if not pair:
        return None
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tid = await conn.fetchval("SELECT id FROM tokens WHERE token_pair=$1", pair)
        return int(tid) if tid else None


async def _load_token_static(token_id: int) -> Dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT holder_count, liquidity, organic_score,
                   top_holders_percentage, dev_balance_percentage,
                   EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_seconds
            FROM tokens WHERE id=$1
            """,
            token_id,
        )
        return dict(row) if row else {}


async def _load_recent_metrics(token_id: int, k: int) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, usd_price, liquidity, fdv, mcap,
                   median_amount_usd, median_amount_sol, median_token_price
            FROM token_metrics_seconds
            WHERE token_id=$1
            ORDER BY ts DESC
            LIMIT $2
            """,
            token_id, k,
        )
        return [dict(r) for r in rows][::-1]


def _build_features(window: List[Dict], static: Dict) -> np.ndarray:
    prices = np.array([_pick_price(r) or 0.0 for r in window], dtype=np.float64)
    liqs = np.array([float(r.get("liquidity") or 0.0) for r in window], dtype=np.float64)
    mcaps = np.array([float(r.get("mcap") or 0.0) for r in window], dtype=np.float64)
    med_usd = np.array([float(r.get("median_amount_usd") or 0.0) if r.get("median_amount_usd") not in (None, "") else 0.0 for r in window], dtype=np.float64)
    med_sol = np.array([float(r.get("median_amount_sol") or 0.0) if r.get("median_amount_sol") not in (None, "") else 0.0 for r in window], dtype=np.float64)

    p_last = _fe_last(prices)
    p_slope = _fe_slope(prices)
    p_vol = _fe_vol(prices)
    p_ret = float(prices[-1] / prices[0] - 1.0) if prices[0] > 0 else 0.0

    l_last = _fe_last(liqs)
    l_slope = _fe_slope(liqs)
    l_vol = _fe_vol(liqs)

    m_last = _fe_last(mcaps)
    m_slope = _fe_slope(mcaps)

    mu_last = _fe_last(med_usd)
    mu_med15 = float(np.median(med_usd)) if med_usd.size else 0.0
    mu_nonzero_share = float(np.mean(med_usd > 0)) if med_usd.size else 0.0

    ms_last = _fe_last(med_sol)

    feats = np.array([
        p_last, p_slope, p_vol, p_ret,
        l_last, l_slope, l_vol,
        m_last, m_slope,
        mu_last, mu_med15, mu_nonzero_share,
        ms_last,
        float(static.get("holder_count") or 0),
        float(static.get("top_holders_percentage") or 0),
        float(static.get("dev_balance_percentage") or 0),
        float(static.get("organic_score") or 0),
        float(static.get("age_seconds") or 0),
        float(static.get("liquidity") or 0),
    ], dtype=np.float64)
    return _nan_to_num(feats)


def _load_models() -> Tuple[object, Optional[object]]:
    # Try to use latest paths from ai_models; fallback to default files
    cls_path = os.path.join(MODELS_DIR, "entry_baseline_cls.pkl")
    eta_path = os.path.join(MODELS_DIR, "entry_baseline_eta.pkl")
    try:
        # lazy import to avoid DB before loop
        import asyncio
        from _v3_db_pool import get_db_pool  # type: ignore
        async def get_paths():
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT hyperparams FROM ai_models WHERE name='entry_baseline' ORDER BY trained_on DESC NULLS LAST, id DESC LIMIT 1"
                )
                if row and row.get('hyperparams'):
                    hp = row['hyperparams']
                    p1 = hp.get('cls_path')
                    p2 = hp.get('eta_path')
                    return p1 or cls_path, p2 or eta_path
                return cls_path, eta_path
        cls_path_db, eta_path_db = asyncio.get_event_loop().run_until_complete(get_paths())
        if os.path.exists(cls_path_db):
            cls_path = cls_path_db
        if eta_path_db and os.path.exists(eta_path_db):
            eta_path = eta_path_db
    except Exception:
        pass

    cls = joblib.load(cls_path)
    rgr = joblib.load(eta_path) if os.path.exists(eta_path) else None
    return cls, rgr


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=int, default=None)
    ap.add_argument("--pair", type=str, default=None)
    ap.add_argument("--prob", type=float, default=0.6, help="probability threshold to suggest entry")
    args = ap.parse_args()

    token_id = await _resolve_token_id(args.pair, args.token)
    if token_id is None:
        print(json.dumps({"success": False, "error": "provide --token or --pair"}))
        return

    window = await _load_recent_metrics(token_id, ENCODER_SEC)
    if len(window) < ENCODER_SEC:
        print(json.dumps({"success": False, "error": f"need {ENCODER_SEC} seconds, have {len(window)}"}))
        return
    static = await _load_token_static(token_id)
    feats = _build_features(window, static).reshape(1, -1)

    cls, rgr = _load_models()
    proba = float(cls.predict_proba(feats)[0, 1])
    eta_pred = float(rgr.predict(feats)[0]) if rgr is not None else None

    suggestion = "enter" if proba >= args.prob else "wait"
    print(json.dumps({
        "success": True,
        "token_id": token_id,
        "prob_reach_+1_in_120s": round(proba, 4),
        "eta_pred_sec": (round(eta_pred, 1) if eta_pred is not None else None),
        "decision": suggestion,
        "threshold": args.prob,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

