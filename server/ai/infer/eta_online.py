#!/usr/bin/env python3
"""Per-second ETA forecast loop that writes tokens.plan_sell_* fields (AI plan only).

Logic per tick:
- Find candidate tokens (valid pair, not archived, >=15 iterations, no open position)
- For each: fetch last 15 seconds metrics and compute features
- Run ETA TCN model (models/eta_tcn.pt) → (p_hit, eta_bin)
- Write plan_sell_iteration and plan_sell_price_usd to tokens (AI plan for exit)
- Entry/exit data is stored in wallet_history, not in tokens
- Note: Archived tokens are in tokens_history table, so this function only queries tokens table (live tokens)
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
import os
import numpy as np
import torch
import torch.nn as nn

from _v3_db_pool import get_db_pool
from config import config
from ai.patterns.catalog import PATTERN_SEED
# OLD pattern classification removed - using pattern segments from analyzer instead
# from ai.patterns.full_series_classifier import compute_full_features, choose_best_pattern

ETA_BINS = list(getattr(config, 'ETA_BINS', [30, 40, 60, 90, 120, 180, 240]))
# Entry iteration for auto-buy (seconds from token start)
ENTRY_SEC = getattr(config, 'AUTO_BUY_ENTRY_SEC', 80)
# AI Preview Entry: seconds to assume for preview forecast (before real entry)
AI_PREVIEW_ENTRY_SEC = getattr(config, 'AI_PREVIEW_ENTRY_SEC', 60)
# Preview forecast enabled/disabled - if False, only real positions get forecasts
AI_PREVIEW_FORECAST_ENABLED = getattr(config, 'AI_PREVIEW_FORECAST_ENABLED', True)
# Pattern segments end: all segments (0-35, 35-85, 85-170) are calculated after this point
# This is also the end of final corridor (PRICE_CORRIDOR_FINAL_END)
# Extended to 170s to detect post-entry drops (155-170s)
PATTERN_SEGMENTS_END_SEC = int(getattr(config, 'PRICE_CORRIDOR_FINAL_END', 170))
# Minimum age for pattern classification (should be visible on chart)
PATTERN_CLASSIFY_MIN_SEC = int(getattr(config, 'PATTERN_CLASSIFY_MIN_SEC', 60))
TARGET_RETURN = float(getattr(config, 'TARGET_RETURN', 0.20))
P_THRESHOLD = float(getattr(config, 'ETA_P_THRESHOLD', 0.6))
MODEL_PATH = getattr(config, 'ETA_MODEL_PATH', os.path.join("models", "eta_tcn.pt"))
MAX_TOKEN_AGE_SEC = int(getattr(config, 'ETA_MAX_TOKEN_AGE_SEC', 120))
LOG_VERBOSE = os.getenv("AI_VERBOSE", "1") not in ("0", "false", "False")
# Max cap for ETA to encourage earlier exits (sliding)
# Absolute cap (no longer used for now-age) and relative cap from entry
ETA_MAX_CAP = int(getattr(config, 'ETA_MAX_CAP', 40))
ETA_REL_CAP = int(getattr(config, 'ETA_REL_CAP', 15))  # sliding window length relative to entry


class TemporalBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, k: int, d: int, p: float = 0.1):
        super().__init__()
        pad = (k - 1) * d
        self.conv1 = nn.Conv1d(in_ch, out_ch, k, padding=pad, dilation=d)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv1d(out_ch, out_ch, k, padding=pad, dilation=d)
        self.relu2 = nn.ReLU()
        self.net = nn.Sequential(self.conv1, self.relu1, self.conv2, self.relu2)
        self.down = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()
        self.dropout = nn.Dropout(p)

    def forward(self, x):
        out = self.net(x)
        out = out[:, :, : x.size(2)]
        return self.dropout(out + self.down(x))


class SmallTCN(nn.Module):
    def __init__(self, in_ch: int, cond_dim: int, hid: int = 64, k: int = 3, num_bins: int = 6):
        super().__init__()
        self.b1 = TemporalBlock(in_ch, hid, k=k, d=1)
        self.b2 = TemporalBlock(hid, hid, k=k, d=2)
        self.b3 = TemporalBlock(hid, hid, k=k, d=4)
        self.head_p = nn.Sequential(nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, 1))
        self.head_eta = nn.Sequential(nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, num_bins))

    def forward(self, x: torch.Tensor, cond: torch.Tensor):
        h = self.b1(x)
        h = self.b2(h)
        h = self.b3(h)
        h = h[:, :, -1]
        z = torch.cat([h, cond], dim=1)
        logit_p = self.head_p(z)
        logit_eta = self.head_eta(z)
        return logit_p, logit_eta


_model: Optional[SmallTCN] = None
_eta_bins: List[int] = ETA_BINS
CODE_TO_NAME = {getattr(item.get("code"), "value", str(item.get("code"))): item.get("name") for item in PATTERN_SEED if item.get("code") and item.get("name")}


async def _load_model():
    global _model, _eta_bins
    if _model is not None:
        return _model
    # Resolve model path robustly: try CWD path, then project-root/models
    path = MODEL_PATH
    if not os.path.isabs(path) and not os.path.exists(path):
        proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        alt = os.path.join(proj_root, path)
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        raise FileNotFoundError(f"JUNO model not found at {MODEL_PATH} (resolved: {os.path.abspath(MODEL_PATH)}); tried alt relative to project root as well")

    chk = torch.load(path, map_location="cpu")
    _eta_bins = chk.get("bins", ETA_BINS)
    _model = SmallTCN(in_ch=6, cond_dim=9, num_bins=len(_eta_bins))
    _model.load_state_dict(chk["model"])
    _model.eval()
    if LOG_VERBOSE:
        print("[JUNO] model loaded:", path, "bins=", _eta_bins)
    return _model


async def _candidates(conn) -> List[Dict[str, int]]:
    # Start processing tokens at PATTERN_CLASSIFY_MIN_SEC (60s) for pattern classification.
    # ETA model (preview forecast) will run when token has >= AI_PREVIEW_ENTRY_SEC (100s) iterations.
    # Real auto-buy uses AUTO_BUY_ENTRY_SEC (10080s) - separate constant for actual trading.
    # This allows preview forecast to be visible early (100s), while auto-buy waits for 10000080s.
    rows = await conn.fetch(
        """
        WITH mc AS (
          SELECT token_id, COUNT(*) AS cnt, MIN(ts) AS t0
          FROM token_metrics_seconds
          WHERE usd_price IS NOT NULL AND usd_price > 0
          GROUP BY token_id
        )
        SELECT t.id AS token_id, mc.cnt AS iterations, mc.t0 AS t0
        FROM tokens t
        JOIN mc ON mc.token_id = t.id
        WHERE t.token_pair IS NOT NULL AND t.token_pair <> '' AND t.token_pair <> t.token_address
          AND NOT EXISTS (
              SELECT 1 FROM wallet_history wh 
              WHERE wh.token_id = t.id AND wh.exit_iteration IS NULL
          )
          AND mc.cnt >= $1
        ORDER BY t.created_at DESC
        LIMIT 200
        """,
        PATTERN_CLASSIFY_MIN_SEC,  # Start at 60s for pattern classification
    )
    out = [{"token_id": int(r["token_id"]), "iterations": int(r["iterations"]), "t0": int(r["t0"]) } for r in rows]
    if LOG_VERBOSE:
        print(f"[JUNO] candidates: {len(out)}")
    return out


def _build_features(recents: List[Dict[str, Any]], min_required: int = None) -> Optional[Dict[str, torch.Tensor]]:
    # For preview: use AI_PREVIEW_ENTRY_SEC, for real position: use ENTRY_SEC
    min_len = min_required if min_required is not None else ENTRY_SEC
    if not recents or len(recents) < min_len:
        return None
    prices = np.array([float(r["usd_price"] or 0.0) for r in recents], dtype=np.float32)
    liquidity = np.array([float(r["liquidity"] or 0.0) for r in recents], dtype=np.float32)
    mcap = np.array([float(r["mcap"] or 0.0) for r in recents], dtype=np.float32)
    holders = np.array([float(r["holder_count"] or 0.0) for r in recents], dtype=np.float32)
    buys = np.array([float(r["buy_count"] or 0.0) for r in recents], dtype=np.float32)
    sells = np.array([float(r["sell_count"] or 0.0) for r in recents], dtype=np.float32)

    eps = 1e-9
    ln_p = np.log(np.clip(prices, eps, None))
    dln = np.diff(ln_p, prepend=ln_p[0])

    def slope_k(k: int) -> float:
        k = min(k, len(ln_p))
        x = np.arange(k)
        y = ln_p[-k:]
        if k < 2:
            return 0.0
        xm = x.mean(); ym = y.mean()
        num = ((x - xm) * (y - ym)).sum()
        den = ((x - xm) ** 2).sum() + eps
        return float(num / den)

    def r2_k(k: int) -> float:
        k = min(k, len(ln_p))
        x = np.arange(k)
        y = ln_p[-k:]
        if k < 2:
            return 0.0
        xm = x.mean(); ym = y.mean()
        num = ((x - xm) * (y - ym)).sum()
        den = ((x - xm) ** 2).sum() + eps
        beta = num / den
        y_hat = (x - xm) * beta + ym
        ss_res = ((y - y_hat) ** 2).sum()
        ss_tot = ((y - ym) ** 2).sum() + eps
        return float(1.0 - ss_res / ss_tot)

    series = np.stack([prices, liquidity, mcap, holders, buys, sells], axis=0)
    cond = np.array([
        slope_k(5), slope_k(10), slope_k(15),
        slope_k(10) - slope_k(5),
        float(np.std(dln[-15:])) if len(dln) >= 2 else 0.0,
        r2_k(10), r2_k(15),
        float(np.max(prices[-15:]) / (prices[-15] + eps) - 1.0),
        float(1.0 - np.min(prices[-15:]) / (prices[-15] + eps)),
    ], dtype=np.float32)

    return {"x": torch.tensor(series, dtype=torch.float32).unsqueeze(0),
            "cond": torch.tensor(cond, dtype=torch.float32).unsqueeze(0)}


async def _last_window(conn, token_id: int, window: int = ENTRY_SEC) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT ts, usd_price, liquidity, mcap, holder_count, buy_count, sell_count
        FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
        ORDER BY ts DESC
        LIMIT $2
        """,
        token_id, int(max(1, window)),
    )
    recents = [dict(r) for r in rows][::-1]
    return recents


async def _full_series(conn, token_id: int) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT ts, usd_price, liquidity, mcap, holder_count, buy_count, sell_count
        FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
        ORDER BY ts ASC
        """,
        token_id,
    )
    return [dict(r) for r in rows]


async def _entry_price(conn, token_id: int, entry_sec: int = ENTRY_SEC) -> Optional[float]:
    row = await conn.fetchrow(
        """
        SELECT usd_price
        FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
        ORDER BY ts ASC
        OFFSET $2 LIMIT 1
        """,
        token_id, max(0, entry_sec - 1),
    )
    return float(row["usd_price"]) if row and row["usd_price"] is not None else None

async def _earliest_hit(conn, token_id: int, entry_sec: int, entry_price: float, target_mult: float = 1.2) -> Optional[Tuple[int, float]]:
    """Return earliest (iteration, price) after entry_sec where price >= entry_price*target_mult.
    rn is 1-based; iteration equals rn. Returns (iteration, usd_price).
    """
    try:
        row = await conn.fetchrow(
            """
            WITH o AS (
              SELECT row_number() OVER (ORDER BY ts ASC) rn, ts, usd_price
              FROM token_metrics_seconds
              WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
              ORDER BY ts ASC
            )
            SELECT rn, usd_price FROM o
            WHERE rn > $2 AND usd_price >= $3
            ORDER BY rn ASC
            LIMIT 1
            """,
            token_id, int(entry_sec), float(entry_price*target_mult)
        )
        if row and row.get("rn") is not None and row.get("usd_price") is not None:
            return int(row["rn"]), float(row["usd_price"])
    except Exception:
        pass
    return None


async def _age_now(conn, token_id: int) -> int:
    """Current age in iterations (≈ seconds of life with price>0)."""
    c = await conn.fetchval(
        """
        SELECT COUNT(*) FROM token_metrics_seconds
        WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
        """,
        token_id,
    )
    try:
        return int(c or 0)
    except Exception:
        return 0


# OLD pattern classification functions removed - using pattern segments from analyzer instead
# Pattern segments are calculated by JupiterAnalyzerV3._update_segment_predictions()
# and stored in tokens.pattern_segment_1, pattern_segment_2, pattern_segment_3, pattern_segment_decision


async def loop_once() -> None:
    model = await _load_model()
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        cand = await _candidates(conn)
        if not cand:
            if LOG_VERBOSE:
                print("[JUNO] tick: no candidates; waiting…")
            return
        for c in cand:
            token_id = c["token_id"]
            iterations = 0
            try:
                iterations = int(c.get("iterations") or 0)
            except Exception:
                iterations = 0
            try:
                # Check if token has real position first to determine window size
                has_real_pos = False
                try:
                    pos_check = await conn.fetchrow(
                        """
                        SELECT 1 FROM wallet_history
                        WHERE token_id=$1 AND exit_iteration IS NULL
                        LIMIT 1
                        """,
                        token_id
                    )
                    has_real_pos = pos_check is not None
                except Exception:
                    pass
                
                # Skip preview forecast if disabled (only process real positions)
                if not has_real_pos and not AI_PREVIEW_FORECAST_ENABLED:
                    continue
                
                # Use AI_PREVIEW_ENTRY_SEC for preview forecast (before real entry)
                # Use ENTRY_SEC for real position (though we'll use real data anyway)
                window_size = ENTRY_SEC if has_real_pos else AI_PREVIEW_ENTRY_SEC
                recents = await _last_window(conn, token_id, window_size)
                
                # NEW: Use pattern segments instead of old pattern classification
                # Pattern segments are updated by JupiterAnalyzerV3._update_segment_predictions()
                # Segments: (0-35), (35-85), (85-125) with decision: "buy" or "not"
                pattern_segment_decision = None
                pattern_segment_1 = None
                pattern_segment_2 = None
                pattern_segment_3 = None
                
                try:
                    seg_row = await conn.fetchrow(
                        """
                        SELECT pattern_segment_1, pattern_segment_2, pattern_segment_3, pattern_segment_decision
                        FROM tokens
                        WHERE id=$1
                        """,
                        token_id
                    )
                    if seg_row:
                        pattern_segment_1 = seg_row.get("pattern_segment_1")
                        pattern_segment_2 = seg_row.get("pattern_segment_2")
                        pattern_segment_3 = seg_row.get("pattern_segment_3")
                        pattern_segment_decision = seg_row.get("pattern_segment_decision")
                except Exception:
                    pass
                
                # HONEYPOT DETECTION - still important for safety
                # Check sell_share < 20% to detect tokens where we can't exit
                honeypot_detected = False
                try:
                    total_buys = float(sum(float(r.get("buy_count") or 0.0) for r in recents))
                    total_sells = float(sum(float(r.get("sell_count") or 0.0) for r in recents))
                    trades_sum = total_buys + total_sells
                    sell_share = (total_sells / trades_sum) if trades_sum > 0 else 0.0
                    
                    # Check first AI_PREVIEW_ENTRY_SEC seconds for early detection
                    if iterations >= AI_PREVIEW_ENTRY_SEC:
                        first_window = await conn.fetch(
                            """
                            SELECT buy_count, sell_count
                            FROM token_metrics_seconds
                            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
                            ORDER BY ts ASC
                            LIMIT $2
                            """,
                            token_id, AI_PREVIEW_ENTRY_SEC
                        )
                        if first_window:
                            first_buys = float(sum(float(r.get("buy_count") or 0.0) for r in first_window))
                            first_sells = float(sum(float(r.get("sell_count") or 0.0) for r in first_window))
                            first_sum = first_buys + first_sells
                            first_sell_share = (first_sells / first_sum) if first_sum >= 10 else 0.0
                            
                            if first_sum >= 10 and first_sell_share < 0.20:
                                honeypot_detected = True
                                if LOG_VERBOSE:
                                    print(f"[JUNO] token {token_id}: HONEYPOT DETECTED (first {AI_PREVIEW_ENTRY_SEC}s) - sell_share={first_sell_share:.2%} < 20%")
                    
                    # Check current window
                    if not honeypot_detected and trades_sum >= 10 and sell_share < 0.20:
                        honeypot_detected = True
                            if LOG_VERBOSE:
                            print(f"[JUNO] token {token_id}: HONEYPOT DETECTED (recent window) - sell_share={sell_share:.2%} < 20%")
                except Exception:
                    pass

                # CRITICAL: Skip forecast if pattern segments decision is "not" or honeypot detected
                # Pattern segments are calculated by analyzer: if all segments are "best" or "good" → decision = "buy"
                # If any segment is "unknown", "bad", "risk", "flat" → decision = "not"
                if honeypot_detected:
                    if LOG_VERBOSE:
                        print(f"[JUNO] token {token_id}: SKIP forecast (honeypot detected), no preview forecast, no auto-buy")
                    if not has_real_pos and iterations > AI_PREVIEW_ENTRY_SEC:
                        try:
                            await conn.execute(
                                """
                                UPDATE tokens SET
                                    plan_sell_iteration = NULL,
                                    plan_sell_price_usd = NULL,
                                    token_updated_at = CURRENT_TIMESTAMP
                                WHERE id=$1
                                """,
                                token_id
                            )
                        except Exception:
                            pass
                    continue
                
                # Check pattern segment decision from analyzer
                decision_lower = (pattern_segment_decision or "").lower() if pattern_segment_decision else None
                
                # IMPORTANT: Preview forecast (simulated entry at 20s) should work until real entry happens
                # Only block forecast if:
                # 1. Token has real position AND segments decision = "not" (real position needs good segments)
                # 2. Token is old enough (>= 35s) AND segments still not calculated (analyzer should have calculated by now)
                # 
                # For preview forecast (no real position): allow even if segments = "not" or not ready yet
                # This allows preview forecast to work early (at 20s) before segments are fully calculated
                
                if decision_lower == "not":
                    # IMPORTANT: If token has real entry (force buy or auto-buy), we MUST calculate plan_sell_*
                    # even if decision="not", because user has real money invested
                    # Only block forecast for preview tokens (no real entry) with decision="not"
                    if not has_real_pos:
                    if LOG_VERBOSE:
                            print(
                                f"[JUNO] token {token_id}: SKIP forecast (PREVIEW blocked, segments decision=not: seg1={pattern_segment_1}, seg2={pattern_segment_2}, seg3={pattern_segment_3})"
                            )
                    try:
                        await conn.execute(
                            """
                            UPDATE tokens SET
                                plan_sell_iteration = NULL,
                                plan_sell_price_usd = NULL,
                                token_updated_at = CURRENT_TIMESTAMP
                                WHERE id=$1
                            """,
                                token_id,
                        )
                    except Exception:
                        pass
                    continue
                    else:
                        # Token has real entry - calculate forecast even if decision="not"
                        # User has real money invested, so we need to show exit plan
                        if LOG_VERBOSE:
                            print(
                                f"[JUNO] token {token_id}: ALLOW forecast (REAL entry, decision=not ignored, user has real position)"
                            )
                        # Continue to forecast calculation below
                
                # If decision is None or "unknown", wait until all segments are calculated
                # Segments: Segment 1 (0-35), Segment 2 (35-85), Segment 3 (85-125)
                # All segments are ready after PATTERN_SEGMENTS_END_SEC
                if decision_lower is None or decision_lower == "unknown":
                    if iterations >= PATTERN_SEGMENTS_END_SEC:  # After all segments should be calculated
                        # Block forecast if segments still not calculated after segments end
                        if LOG_VERBOSE:
                            print(f"[JUNO] token {token_id}: SKIP forecast (segments not calculated yet after {PATTERN_SEGMENTS_END_SEC}s, iterations={iterations})")
                        continue
                    else:
                        # For tokens < PATTERN_SEGMENTS_END_SEC, wait for segments to be calculated
                        if LOG_VERBOSE:
                            print(f"[JUNO] token {token_id}: Waiting for segments (iterations={iterations} < {PATTERN_SEGMENTS_END_SEC}s, segments not ready yet)")
                        continue

                # ETA model inference only when enough history for features
                # IMPORTANT: Entry point is AI_PREVIEW_ENTRY_SEC (after all segments are calculated at PATTERN_SEGMENTS_END_SEC)
                # We need at least AI_PREVIEW_ENTRY_SEC seconds of data for model input
                min_required = AI_PREVIEW_ENTRY_SEC  # After all segments ready
                feats = _build_features(recents, min_required=min_required)
                if not feats:
                    # Not enough data for TCN yet; skip ETA planning
                    if LOG_VERBOSE:
                        print(f"[JUNO] token {token_id}: ETA pending (<{min_required}s), iterations={iterations}")
                    continue
                with torch.no_grad():
                    logit_p, logit_eta = model(feats["x"], feats["cond"])
                    p_hit = float(torch.sigmoid(logit_p).item())
                    eta_idx = int(torch.argmax(logit_eta, dim=1).item())
                    eta_bin = int(_eta_bins[eta_idx]) if 0 <= eta_idx < len(_eta_bins) else 120
                
                if LOG_VERBOSE:
                    print(f"[JUNO] token {token_id}: p_hit={p_hit:.3f}, eta_bin={eta_bin}, segments=[{pattern_segment_1}, {pattern_segment_2}, {pattern_segment_3}], decision={pattern_segment_decision}")

                # Compute entry anchor/price: prefer actual entry from wallet_history if позиция уже открыта; иначе AI_PREVIEW_ENTRY_SEC for preview
                entry_iter_effective = AI_PREVIEW_ENTRY_SEC  # Default: preview entry (before real buy)
                price_buy_plan = None
                token_amount = None
                
                # FIRST: Check if token already has entry (auto-buy from analyzer or force_buy) via wallet_history
                # Use the same check we did earlier for window_size
                if has_real_pos:
                    try:
                        brow = await conn.fetchrow(
                            """
                            SELECT entry_token_amount, entry_price_usd, entry_iteration
                            FROM wallet_history
                            WHERE token_id=$1 AND exit_iteration IS NULL
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            token_id
                        )
                        if brow and brow["entry_token_amount"] is not None:
                            # Token already has REAL entry - use it for forecasting
                            token_amount = float(brow["entry_token_amount"])
                            if brow["entry_price_usd"] is not None:
                                price_buy_plan = float(brow["entry_price_usd"])
                            if brow["entry_iteration"] is not None:
                                entry_iter_effective = int(brow["entry_iteration"])
                    except Exception:
                        pass
                
                # If no real entry yet, use PREVIEW entry price (simulated entry at AI_PREVIEW_ENTRY_SEC)
                if price_buy_plan is None:
                    price_buy_plan = await _entry_price(conn, token_id, AI_PREVIEW_ENTRY_SEC)
                    if LOG_VERBOSE and price_buy_plan:
                        print(f"[JUNO] token {token_id}: preview entry price at {AI_PREVIEW_ENTRY_SEC}s = ${price_buy_plan:.6f}")
                
                if price_buy_plan is None or price_buy_plan <= 0:
                    # not enough data yet to plan
                    if LOG_VERBOSE:
                        print(f"[JUNO] token {token_id}: no entry price available yet (preview_sec={AI_PREVIEW_ENTRY_SEC}, iterations={iterations})")
                    continue
                
                # If token_amount not set yet (no entry), compute it from planned entry
                if token_amount is None:
                    try:
                        base_usd = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
                    except Exception:
                        base_usd = 5.0
                    token_amount = float(base_usd / price_buy_plan)
                
                # Current portfolio value: token_amount * latest price
                try:
                    price_now = float(recents[-1]["usd_price"]) if recents and recents[-1].get("usd_price") is not None else None
                except Exception:
                    price_now = None
                cur_value = float(token_amount * price_now) if (price_now is not None) else None
                
                # Prefer factual earliest hit if already achieved since entry
                fact_hit = await _earliest_hit(conn, token_id, entry_iter_effective, price_buy_plan, 1.0 + TARGET_RETURN)
                eff_eta = int(min(max(1, eta_bin), max(1, ETA_REL_CAP)))
                windows = 0
                if iterations > entry_iter_effective:
                    windows = (iterations - entry_iter_effective) // ETA_REL_CAP
                anchor = entry_iter_effective + windows * ETA_REL_CAP
                plan_exit_iter = anchor + ETA_REL_CAP

                price_sell = float(price_buy_plan * (1.0 + TARGET_RETURN))

                # CRITICAL: If current price already exceeds target, find earliest iteration when target was reached
                # This updates forecast to show earlier exit time when price already pumped
                # Note: Token will be archived when appropriate (not here) - user may enter manually later or auto-buy will trigger
                if price_now and price_now >= price_sell:
                    # Current price already exceeds target - find when it first reached target
                    early_hit = await _earliest_hit(conn, token_id, entry_iter_effective, price_buy_plan, 1.0 + TARGET_RETURN)
                    if early_hit:
                        early_iter, early_price = early_hit
                        # Update plan to show earlier exit (when target was actually reached)
                        plan_exit_iter = early_iter
                        price_sell = early_price
                        if LOG_VERBOSE:
                            print(f"[JUNO] token {token_id}: price already exceeds target (current=${price_now:.6f} >= target=${price_buy_plan * (1.0 + TARGET_RETURN):.6f}), updating plan to iter={early_iter}")

                # Always refresh plan fields (for both tokens with entry and without entry)
                try:
                    result = await conn.execute(
                        """
                        UPDATE tokens SET
                          plan_sell_iteration=$2,
                          plan_sell_price_usd=$3,
                          token_updated_at = CURRENT_TIMESTAMP
                        WHERE id=$1
                        """,
                        token_id,
                        plan_exit_iter,
                        price_sell,
                    )
                    # Always log preview forecast updates (even if not verbose)
                    try:
                        affected = int((result or 'UPDATE 0').split()[-1])
                        if affected > 0:
                            mode = "REAL" if has_real_pos else "PREVIEW"
                            print(f"[JUNO] token {token_id}: {mode} plan updated iter={plan_exit_iter} price=${price_sell:.6f} (entry_iter={entry_iter_effective})")
                        elif LOG_VERBOSE:
                            # Check why update failed
                            # Check if token still exists (not archived)
                            token_exists = await conn.fetchval("SELECT id FROM tokens WHERE id=$1", token_id)
                            print(f"[JUNO] token {token_id}: plan update failed (token_exists={token_exists is not None})")
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[JUNO] token {token_id}: plan update error: {e}")
                    if LOG_VERBOSE:
                        import traceback
                        traceback.print_exc()
                    pass

                if fact_hit:
                    fact_iter, fact_price = fact_hit
                    # ETA AUTO-SELL: Update plan fields. Analyzer will check plan_sell_* and call finalize_token_sale() on next tick.
                    await conn.execute(
                        """
                        UPDATE tokens SET 
                          plan_sell_price_usd=$2,
                          plan_sell_iteration=$3,
                          token_updated_at = CURRENT_TIMESTAMP
                        WHERE id=$1
                        """,
                        token_id,
                        fact_price,
                        fact_iter,
                    )
                    if LOG_VERBOSE:
                        print(f"[JUNO] token {token_id}: EXECUTE exit plan iter={fact_iter}s price=${fact_price:.6f} amount={token_amount:.2f}")
            except Exception as e:
                if LOG_VERBOSE:
                    print(f"[JUNO] token {token_id}: error {e}")
                # skip token on any failure, keep loop running
                continue


if __name__ == "__main__":
    asyncio.run(loop_once())
