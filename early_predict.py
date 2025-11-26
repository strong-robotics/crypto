#!/usr/bin/env python3
"""
Early-entry predictor: strictly first 15 seconds -> predict +20% within 60s.

Loads model saved by server/ai/training/train_early_baseline.py and runs
prediction for a given token (by id or pair) exactly when it has 15 seconds.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import joblib

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, 'server')
if SERVER not in sys.path:
    sys.path.append(SERVER)

from _v3_db_pool import get_db_pool  # type: ignore
from ai.config import ENCODER_SEC, MODELS_DIR  # type: ignore


def pick_price(row: Dict) -> Optional[float]:
    mp = row.get('median_token_price')
    if mp not in (None, ''):
        try: return float(mp)
        except Exception: pass
    up = row.get('usd_price')
    if up not in (None, ''):
        try: return float(up)
        except Exception: pass
    return None


async def resolve_token(pair: Optional[str], token: Optional[int]) -> Optional[int]:
    if token is not None:
        return int(token)
    if not pair:
        return None
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tid = await conn.fetchval("SELECT id FROM tokens WHERE token_pair=$1", pair)
        return int(tid) if tid else None


async def load_first_window(token_id: int) -> Optional[List[Dict]]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, usd_price, liquidity, mcap, fdv, median_amount_usd, median_amount_sol, median_token_price FROM token_metrics_seconds WHERE token_id=$1 ORDER BY ts ASC LIMIT $2",
            token_id, ENCODER_SEC,
        )
        return [dict(r) for r in rows] if rows and len(rows) == ENCODER_SEC else None


async def load_static(token_id: int) -> Dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT holder_count, liquidity, organic_score, top_holders_percentage, dev_balance_percentage FROM tokens WHERE id=$1",
            token_id,
        )
        return dict(row) if row else {}


def build_feats(window: List[Dict], static: Dict) -> np.ndarray:
    def fe(values: np.ndarray):
        last = float(values[-1]) if values.size else 0.0
        if values.size >= 2:
            x = np.arange(values.size, dtype=np.float32)
            try:
                slope,_ = np.polyfit(x, values.astype(np.float32), 1)
            except Exception:
                slope=0.0
            vol = float(np.std(values))
            ret = float(values[-1]/values[0]-1.0) if values[0]>0 else 0.0
        else:
            slope=0.0; vol=0.0; ret=0.0
        return last, float(slope), float(vol), float(ret), float(np.min(values) if values.size else 0.0), float(np.max(values) if values.size else 0.0)

    prices = np.array([pick_price(r) or 0.0 for r in window], dtype=np.float64)
    liqs = np.array([float(r.get('liquidity') or 0.0) for r in window], dtype=np.float64)
    mcaps = np.array([float(r.get('mcap') or 0.0) for r in window], dtype=np.float64)
    musd = np.array([float(r.get('median_amount_usd') or 0.0) if r.get('median_amount_usd') not in (None,'') else 0.0 for r in window], dtype=np.float64)
    msol = np.array([float(r.get('median_amount_sol') or 0.0) if r.get('median_amount_sol') not in (None,'') else 0.0 for r in window], dtype=np.float64)

    p_last,p_slope,p_vol,p_ret,p_min,p_max = fe(prices)
    l_last,l_slope,l_vol,l_ret,_,_ = fe(liqs)
    m_last,m_slope,_,_,_,_ = fe(mcaps)
    mu_last,mu_slope,_,_,_,_ = fe(musd)
    ms_last,_,_,_,_,_ = fe(msol)
    mu_nonzero_share = float(np.mean(musd>0)) if musd.size else 0.0
    runup = float((p_last - p_min)/p_min) if p_min>0 else 0.0
    drawdown = float((p_max - p_last)/p_max) if p_max>0 else 0.0

    feats = np.array([
        p_last,p_slope,p_vol,p_ret,runup,drawdown,
        l_last,l_slope,l_vol,l_ret,
        m_last,m_slope,
        mu_last,mu_slope,mu_nonzero_share,
        ms_last,
        float(static.get('holder_count') or 0),
        float(static.get('top_holders_percentage') or 0),
        float(static.get('dev_balance_percentage') or 0),
        float(static.get('organic_score') or 0),
        float(static.get('liquidity') or 0),
    ], dtype=np.float64)
    return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0).reshape(1,-1)


def load_models():
    cls_path = os.path.join(MODELS_DIR, 'early_baseline_cls.pkl')
    eta_path = os.path.join(MODELS_DIR, 'early_baseline_eta.pkl')
    cls = joblib.load(cls_path)
    rgr = joblib.load(eta_path) if os.path.exists(eta_path) else None
    return cls, rgr


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--token', type=int, default=None)
    ap.add_argument('--pair', type=str, default=None)
    ap.add_argument('--prob', type=float, default=0.6)
    args = ap.parse_args()

    token_id = await resolve_token(args.pair, args.token)
    if token_id is None:
        print(json.dumps({'success': False, 'error': 'provide --token or --pair'}))
        return
    window = await load_first_window(token_id)
    if not window:
        print(json.dumps({'success': False, 'error': 'need 15 seconds'}))
        return
    static = await load_static(token_id)
    feats = build_feats(window, static)
    cls, rgr = load_models()
    proba = float(cls.predict_proba(feats)[0,1])
    eta_pred = float(rgr.predict(feats)[0]) if rgr is not None else None
    decision = 'enter' if proba >= args.prob else 'wait'
    print(json.dumps({'success': True, 'token_id': token_id, 'prob': round(proba,4), 'eta_pred_sec': (round(eta_pred,1) if eta_pred is not None else None), 'decision': decision, 'threshold': args.prob}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())

