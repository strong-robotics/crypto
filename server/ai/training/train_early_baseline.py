#!/usr/bin/env python3
"""
Early-entry baseline trainer: first 15 seconds -> +20% within 60 seconds.

Builds one sample per token (strict first 15 seconds from min(ts)) and trains:
- binary classifier: success within 60s (sklearn HistGradientBoosting or fallback)
- ETA regressor on positive samples only

Registers the model in ai_models as 'early_baseline'.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score, mean_absolute_error
from sklearn.model_selection import GroupKFold
from sklearn.experimental import enable_hist_gradient_boosting  # noqa: F401
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, GradientBoostingRegressor
import joblib

from _v3_db_pool import get_db_pool
from ai.config import ENCODER_SEC, MODELS_DIR
from ai.models.registry import register_model


HORIZON_SEC = 60
TARGET_RET = 0.20
HOLDERS_MIN = 50
LIQ_MIN = 10000.0


def pick_price(row: Dict) -> Optional[float]:
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


async def load_tokens() -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, holder_count, liquidity, organic_score,
                   top_holders_percentage, dev_balance_percentage,
                   created_at
            FROM tokens
            WHERE token_pair IS NOT NULL AND token_pair <> token_address
            ORDER BY id
            """
        )
        return [dict(r) for r in rows]


async def load_metrics(token_id: int) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, usd_price, liquidity, mcap, fdv,
                   median_amount_usd, median_amount_sol, median_token_price
            FROM token_metrics_seconds
            WHERE token_id=$1
            ORDER BY ts ASC
            """,
            token_id,
        )
        return [dict(r) for r in rows]


def fe(values: np.ndarray) -> Tuple[float, float, float, float, float, float]:
    # last, slope, vol, ret, min, max
    last = float(values[-1]) if values.size else 0.0
    if values.size >= 2:
        x = np.arange(values.size, dtype=np.float32)
        try:
            slope, _ = np.polyfit(x, values.astype(np.float32), 1)
        except Exception:
            slope = 0.0
        vol = float(np.std(values))
        ret = float(values[-1] / values[0] - 1.0) if values[0] > 0 else 0.0
    else:
        slope = 0.0
        vol = 0.0
        ret = 0.0
    return last, float(slope), float(vol), float(ret), float(np.min(values) if values.size else 0.0), float(np.max(values) if values.size else 0.0)


def build_feats(window: List[Dict], static: Dict) -> np.ndarray:
    prices = np.array([pick_price(r) or 0.0 for r in window], dtype=np.float64)
    liqs = np.array([float(r.get("liquidity") or 0.0) for r in window], dtype=np.float64)
    mcaps = np.array([float(r.get("mcap") or 0.0) for r in window], dtype=np.float64)
    musd = np.array([float(r.get("median_amount_usd") or 0.0) if r.get("median_amount_usd") not in (None, "") else 0.0 for r in window], dtype=np.float64)
    msol = np.array([float(r.get("median_amount_sol") or 0.0) if r.get("median_amount_sol") not in (None, "") else 0.0 for r in window], dtype=np.float64)

    p_last, p_slope, p_vol, p_ret, p_min, p_max = fe(prices)
    l_last, l_slope, l_vol, l_ret, _, _ = fe(liqs)
    m_last, m_slope, _, _, _, _ = fe(mcaps)
    mu_last, mu_slope, _, _, _, _ = fe(musd)
    ms_last, _, _, _, _, _ = fe(msol)

    mu_nonzero_share = float(np.mean(musd > 0)) if musd.size else 0.0
    runup = float((p_last - p_min) / p_min) if p_min > 0 else 0.0
    drawdown = float((p_max - p_last) / p_max) if p_max > 0 else 0.0

    feats = np.array([
        p_last, p_slope, p_vol, p_ret, runup, drawdown,
        l_last, l_slope, l_vol, l_ret,
        m_last, m_slope,
        mu_last, mu_slope, mu_nonzero_share,
        ms_last,
        float(static.get('holder_count') or 0),
        float(static.get('top_holders_percentage') or 0),
        float(static.get('dev_balance_percentage') or 0),
        float(static.get('organic_score') or 0),
        float(static.get('liquidity') or 0),
    ], dtype=np.float64)
    return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)


async def build_dataset() -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[int]]:
    X: List[np.ndarray] = []
    y: List[int] = []
    eta_list: List[float] = []
    groups: List[int] = []

    tokens = await load_tokens()
    for t in tokens:
        holders = float(t.get('holder_count') or 0)
        liq = float(t.get('liquidity') or 0)
        if holders < HOLDERS_MIN or liq < LIQ_MIN:
            continue
        token_id = int(t['id'])
        series = await load_metrics(token_id)
        if len(series) < ENCODER_SEC + 2:
            continue
        # strict first window
        window = series[:ENCODER_SEC]
        entry = series[ENCODER_SEC-1]
        entry_price = pick_price(entry)
        if entry_price is None or entry_price <= 0:
            continue
        entry_ts = int(entry['ts'])
        future = [r for r in series[ENCODER_SEC:] if int(r['ts']) <= entry_ts + HORIZON_SEC]
        if not future:
            continue
        # label
        target = entry_price * (1.0 + TARGET_RET)
        eta = None
        for r in future:
            p = pick_price(r)
            if p is not None and p >= target:
                eta = int(r['ts']) - entry_ts
                break
        y_val = 1 if (eta is not None and eta >= 0) else 0
        feats = build_feats(window, t)
        X.append(feats); y.append(y_val); eta_list.append(float(eta) if eta is not None else np.nan); groups.append(token_id)

    if not X:
        return np.zeros((0,1)), np.zeros((0,)), np.zeros((0,)), []
    return np.vstack(X), np.array(y, dtype=np.int32), np.array(eta_list, dtype=np.float64), groups


async def main() -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    X, y, eta, groups = await build_dataset()
    n = X.shape[0]
    print(f"Samples: {n}, features: {X.shape[1] if n>0 else 0}")
    if n < 10:
        print("Too few samples; aborting")
        return

    # GroupKFold CV
    uniq = len(set(groups))
    splits = 2 if uniq < 5 else 5
    gkf = GroupKFold(n_splits=splits)
    aucs=[]; prs=[]; maes=[]

    for fold,(tr,va) in enumerate(gkf.split(X,y,groups)):
        cls = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.1, max_iter=300)
        cls.fit(X[tr], y[tr])
        proba = cls.predict_proba(X[va])[:,1]
        try:
            aucs.append(roc_auc_score(y[va], proba))
            prs.append(average_precision_score(y[va], proba))
        except Exception:
            pass

    print(f"CV ROC-AUC: {np.mean(aucs) if aucs else float('nan'):.3f}")
    print(f"CV PR-AUC : {np.mean(prs)  if prs  else float('nan'):.3f}")

    # Fit final models
    cls_final = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.1, max_iter=300)
    cls_final.fit(X,y)
    cls_path = os.path.join(MODELS_DIR, 'early_baseline_cls.pkl')
    joblib.dump(cls_final, cls_path)
    print(f"Saved classifier: {cls_path}")

    # ETA regressor on positives
    pos = (~np.isnan(eta)) & (eta>=0)
    if pos.sum() >= 5:
        try:
            rgr = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.1, max_iter=300)
        except Exception:
            rgr = GradientBoostingRegressor(random_state=42)
        for fold,(tr,va) in enumerate(gkf.split(X[pos], eta[pos], np.array(groups)[pos])):
            rgr_cv = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.1, max_iter=300) if isinstance(rgr, HistGradientBoostingRegressor) else GradientBoostingRegressor(random_state=fold+1)
            rgr_cv.fit(X[pos][tr], eta[pos][tr])
            pred = rgr_cv.predict(X[pos][va])
            maes.append(mean_absolute_error(eta[pos][va], pred))
        rgr.fit(X[pos], eta[pos])
        rgr_path = os.path.join(MODELS_DIR, 'early_baseline_eta.pkl')
        joblib.dump(rgr, rgr_path)
        print(f"Saved ETA regressor: {rgr_path}")
    else:
        rgr_path = None
        print("Not enough positives for ETA regressor")

    # Register
    def _safe(x):
        try:
            xf = float(x)
            if np.isnan(xf) or np.isinf(xf):
                return None
            return xf
        except Exception:
            return None

    mid = await register_model(
        name='early_baseline', version='v1', model_type='sklearn', framework='sklearn',
        hyperparams={'cls_path': cls_path, 'eta_path': rgr_path, 'encoder_sec': ENCODER_SEC, 'horizon_sec': HORIZON_SEC},
        train_window_sec=ENCODER_SEC, predict_horizons_sec=[HORIZON_SEC], path=cls_path,
        metrics={'roc_auc_cv': _safe(np.mean(aucs)) if aucs else None,
                 'pr_auc_cv': _safe(np.mean(prs)) if prs else None,
                 'eta_mae_cv': _safe(np.mean(maes)) if maes else None,
                 'samples': int(n)}
    )
    print(f"Registered model id={mid}")


if __name__ == '__main__':
    asyncio.run(main())
