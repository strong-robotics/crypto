#!/usr/bin/env python3
"""
Baseline trainer for entry decision (5$ -> +1$ target) using only metrics data.

What it does
- Builds samples from token_metrics_seconds (no trades used directly)
- Window = last 15 seconds per token as features + token static fields
- Label y = 1 if target (+20% over entry price) is reached within ETA_MAX seconds
- Optional ETA regressor trained on positives only

Artifacts
- Saves sklearn models under MODELS_DIR:
  - entry_baseline_cls.pkl (RandomForestClassifier)
  - entry_baseline_eta.pkl (GradientBoostingRegressor)
- Registers a row in ai_models with basic metadata

Usage
  python server/ai/training/train_entry_baseline.py
"""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, average_precision_score, mean_absolute_error
import joblib

from _v3_db_pool import get_db_pool
from ai.config import ENCODER_SEC, MODELS_DIR
from ai.models.registry import register_model


# ----------------------------- Config (simple) ----------------------------- #

TARGET_RETURN = 0.20            # +20% (≈ +1$ from 5$)
HORIZON_MAX_SEC = 300           # how long we look ahead for target
ETA_MAX_FOR_Y = 120             # y=1 if we can reach target within 120s

# Simple safety filters (can be tuned)
HOLDERS_MIN = 50
LIQ_MIN = 10000.0


@dataclass
class TokenStatic:
    holders: float
    top_holders_pct: float
    dev_balance_pct: float
    organic: float
    age_sec: float
    liq_now: float


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
    # prefer median_token_price; fallback to usd_price
    mp = row.get("median_token_price")
    if mp is not None:
        try:
            return float(mp)
        except Exception:
            pass
    up = row.get("usd_price")
    try:
        return float(up) if up is not None else None
    except Exception:
        return None


async def _load_valid_tokens() -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, holder_count, liquidity, organic_score,
                   top_holders_percentage, dev_balance_percentage,
                   EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_seconds
            FROM tokens
            WHERE token_pair IS NOT NULL AND token_pair <> token_address
            ORDER BY id
            """
        )
        return [dict(r) for r in rows]


async def _load_metrics_for_token(token_id: int) -> List[Dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, token_id, ts, usd_price, liquidity, fdv, mcap,
                   median_amount_usd, median_amount_sol, median_token_price
            FROM token_metrics_seconds
            WHERE token_id = $1
            ORDER BY ts ASC
            """,
            token_id,
        )
        return [dict(r) for r in rows]


def _build_features(window: List[Dict], tstat: TokenStatic) -> np.ndarray:
    # arrays (length = ENCODER_SEC)
    prices = np.array([_pick_price(r) or 0.0 for r in window], dtype=np.float64)
    liqs = np.array([float(r.get("liquidity") or 0.0) for r in window], dtype=np.float64)
    mcaps = np.array([float(r.get("mcap") or 0.0) for r in window], dtype=np.float64)
    med_usd = np.array([float(r.get("median_amount_usd") or 0.0) if r.get("median_amount_usd") not in (None, "") else 0.0 for r in window], dtype=np.float64)
    med_sol = np.array([float(r.get("median_amount_sol") or 0.0) if r.get("median_amount_sol") not in (None, "") else 0.0 for r in window], dtype=np.float64)

    # simple engineered features
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

    # static features
    feats = np.array([
        p_last, p_slope, p_vol, p_ret,
        l_last, l_slope, l_vol,
        m_last, m_slope,
        mu_last, mu_med15, mu_nonzero_share,
        ms_last,
        # token statics
        tstat.holders,
        tstat.top_holders_pct,
        tstat.dev_balance_pct,
        tstat.organic,
        tstat.age_sec,
        tstat.liq_now,
    ], dtype=np.float64)
    return _nan_to_num(feats)


def _first_cross_eta(future_rows: List[Dict], entry_price: float, target_ret: float) -> Optional[int]:
    target = entry_price * (1.0 + target_ret)
    start_ts = int(future_rows[0]["ts"]) if future_rows else None
    for r in future_rows:
        p = _pick_price(r)
        if p is None:
            continue
        if p >= target:
            return int(r["ts"]) - int(future_rows[0]["ts"]) if start_ts is not None else 0
    return None


async def build_dataset() -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[int]]:
    """Return X, y_cls, y_eta, groups(token_id)"""
    tokens = await _load_valid_tokens()
    X: List[np.ndarray] = []
    y_cls: List[int] = []
    y_eta: List[float] = []
    groups: List[int] = []

    for t in tokens:
        holders = float(t.get("holder_count") or 0)
        liq_static = float(t.get("liquidity") or 0)
        if holders < HOLDERS_MIN or liq_static < LIQ_MIN:
            continue
        tstat = TokenStatic(
            holders=holders,
            top_holders_pct=float(t.get("top_holders_percentage") or 0),
            dev_balance_pct=float(t.get("dev_balance_percentage") or 0),
            organic=float(t.get("organic_score") or 0),
            age_sec=float(t.get("age_seconds") or 0),
            liq_now=liq_static,
        )
        series = await _load_metrics_for_token(int(t["id"]))
        if len(series) < ENCODER_SEC + 5:
            continue

        # iterate over windows
        for i in range(ENCODER_SEC, len(series)):
            window = series[i-ENCODER_SEC:i]
            entry_row = series[i]
            entry_price = _pick_price(entry_row)
            if entry_price is None or entry_price <= 0:
                continue
            # future window by TS horizon
            entry_ts = int(entry_row["ts"])
            future_rows = [r for r in series[i+1:] if int(r["ts"]) <= entry_ts + HORIZON_MAX_SEC]
            if not future_rows:
                continue

            eta = _first_cross_eta(future_rows, entry_price, TARGET_RETURN)
            y = 1 if (eta is not None and eta <= ETA_MAX_FOR_Y) else 0

            feats = _build_features(window, tstat)
            X.append(feats)
            y_cls.append(y)
            y_eta.append(float(eta) if eta is not None else np.nan)
            groups.append(int(t["id"]))

    if not X:
        return np.zeros((0, 1)), np.zeros((0,)), np.zeros((0,)), []
    X_arr = np.vstack(X)
    y_cls_arr = np.array(y_cls, dtype=np.int32)
    y_eta_arr = np.array(y_eta, dtype=np.float64)
    return X_arr, y_cls_arr, y_eta_arr, groups


async def main() -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    print("📊 Building dataset from DB (metrics-only)...")
    X, y, eta, groups = await build_dataset()
    n = X.shape[0]
    print(f"✅ Samples: {n}, features: {X.shape[1] if n>0 else 0}")
    if n < 100:
        print("⚠️ Too few samples for training (need >= 100). Aborting.")
        return

    # Train/test by GroupKFold (by token_id)
    gkf = GroupKFold(n_splits=5 if len(set(groups)) >= 5 else max(2, len(set(groups))))
    aucs: List[float] = []
    aps: List[float] = []
    maes: List[float] = []

    # Classifier
    cls = RandomForestClassifier(
        n_estimators=300,
        max_depth=None,
        n_jobs=-1,
        class_weight='balanced',
        random_state=42,
    )

    # Fit on full data after CV metrics
    # CV for reporting
    for fold, (tr, va) in enumerate(gkf.split(X, y, groups)):
        cls_cv = RandomForestClassifier(
            n_estimators=300, max_depth=None, n_jobs=-1, class_weight='balanced', random_state=fold+1
        )
        cls_cv.fit(X[tr], y[tr])
        proba = cls_cv.predict_proba(X[va])[:, 1]
        try:
            aucs.append(roc_auc_score(y[va], proba))
            aps.append(average_precision_score(y[va], proba))
        except Exception:
            pass

    print(f"📈 CV ROC-AUC (mean): {np.mean(aucs) if aucs else float('nan'):.3f}")
    print(f"📈 CV PR-AUC  (mean): {np.mean(aps)  if aps  else float('nan'):.3f}")

    cls.fit(X, y)
    cls_path = os.path.join(MODELS_DIR, 'entry_baseline_cls.pkl')
    joblib.dump(cls, cls_path)
    print(f"💾 Saved classifier to {cls_path}")

    # ETA regressor on positives only
    pos_mask = (~np.isnan(eta)) & (eta >= 0)
    eta_pos = eta[pos_mask]
    X_pos = X[pos_mask]
    if len(eta_pos) >= 50:
        rgr = GradientBoostingRegressor(random_state=42)
        # quick CV MAE
        for fold, (tr, va) in enumerate(gkf.split(X_pos, eta_pos, np.array(groups)[pos_mask])):
            rgr_cv = GradientBoostingRegressor(random_state=fold+1)
            rgr_cv.fit(X_pos[tr], eta_pos[tr])
            pred = rgr_cv.predict(X_pos[va])
            maes.append(mean_absolute_error(eta_pos[va], pred))
        rgr.fit(X_pos, eta_pos)
        rgr_path = os.path.join(MODELS_DIR, 'entry_baseline_eta.pkl')
        joblib.dump(rgr, rgr_path)
        print(f"💾 Saved ETA regressor to {rgr_path}")
    else:
        rgr_path = None
        print("ℹ️ Not enough positive samples for ETA regressor (need >= 50)")

    # Register model in ai_models
    metrics = {
        "roc_auc_cv": float(np.mean(aucs)) if aucs else None,
        "pr_auc_cv": float(np.mean(aps)) if aps else None,
        "eta_mae_cv": float(np.mean(maes)) if maes else None,
        "samples": int(n),
        "features": int(X.shape[1]),
        "target_return": TARGET_RETURN,
        "eta_max_for_y": ETA_MAX_FOR_Y,
        "holders_min": HOLDERS_MIN,
        "liq_min": LIQ_MIN,
    }
    model_json = {
        "cls_path": cls_path,
        "eta_path": rgr_path,
        "encoder_sec": ENCODER_SEC,
    }
    mid = await register_model(
        name="entry_baseline",
        version="v1",
        model_type="sklearn",
        framework="sklearn",
        hyperparams=model_json,
        train_window_sec=ENCODER_SEC,
        predict_horizons_sec=[HORIZON_MAX_SEC],
        path=cls_path,
        metrics=metrics,
    )
    print(f"✅ Registered model id={mid}")


if __name__ == "__main__":
    asyncio.run(main())

