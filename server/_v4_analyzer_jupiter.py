#!/usr/bin/env python3

import asyncio
import aiohttp
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Sequence
import joblib
import json

from _v3_db_pool import get_db_pool
from config import config
from _v3_pair_resolver import resolve_and_update_pair
from ai.patterns.catalog import PATTERN_SEED
from ai.pattern_segments import (
    SEGMENT_BOUNDS,
    SEGMENT_FEATURE_KEYS,
    feature_vector_for_segments,
    extract_series,
)
from _v4_buy_sell import finalize_token_sale, buy_real, sell_real, _archive_or_purge_token
from _v3_db_utils import get_token_iterations_count, evaluate_holder_momentum
from _v3_trade_type_checker import check_token_has_real_trading

BASE_DIR = Path(__file__).resolve().parents[1]
SEGMENT_MODEL_PATH = (BASE_DIR / "models" / "pattern_segments.pkl").resolve()
ALLOWED_SEGMENT_LABELS = {"best", "good"}

class JupiterAnalyzerV4:
    """
    Optimized Jupiter Analyzer (V4)
    Reduces DB load from ~40 queries per token to ~5 queries per BATCH.
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_scanning = False
        self.scan_interval = getattr(config, 'JUPITER_ANALYZER_INTERVAL', 3)
        self.batch_size = getattr(config, 'JUPITER_ANALYZER_BATCH_SIZE', 100)
        self._offset: int = 0
        self._total_tokens: Optional[int] = None
        self._fallback_rps: int = int(getattr(config, 'DEXSCREENER_MAX_RPM', 240) // 60) or 4
        self.debug: bool = bool(getattr(config, 'DEBUG', False))

        if self._fallback_rps > 4:
            self._fallback_rps = 4

        self._fallback_left: int = 0
        self.pattern_min_score: int = int(getattr(config, 'PATTERN_MIN_SCORE', 80))
        self.entry_sec: int = int(getattr(config, 'AUTO_BUY_ENTRY_SEC', 150))
        self._pattern_score_map = {}
        self.holder_momentum_iter: int = int(getattr(config, 'HOLDER_MOMENTUM_CHECK_ITER', 500))
        self.auto_buy_iter: int = int(getattr(config, 'AUTO_BUY_TRIGGER_ITER', self.holder_momentum_iter + 10))

        # Liquidity withdrawal settings
        self.withdraw_check_iter: int = int(getattr(config, 'AUTO_BUY_ENTRY_SEC', 150))
        self.withdraw_window: int = int(getattr(config, 'LIQUIDITY_WITHDRAW_WINDOW', 10))
        self.withdraw_equal_eps: float = float(getattr(config, 'LIQUIDITY_WITHDRAW_EQUAL_EPS', 1e-6))
        self.segment_series_limit: int = max(1000, self.withdraw_check_iter + self.withdraw_window)

        # Track last trade-type checkpoint per token
        self.trade_check_done = {}

        self._load_patterns()
        self.segment_model = None
        self.segment_label_encoder = None
        self.segment_feature_names: List[str] = ["segment_index"] + SEGMENT_FEATURE_KEYS
        self._load_segment_model()

    def _load_patterns(self):
        try:
            for item in PATTERN_SEED:
                code = item.get('code')
                score = int(item.get('score', 0) or 0)
                if code is None:
                    continue
                code_str = getattr(code, 'value', str(code))
                if code_str.strip().lower() == 'unknown':
                    score = 0
                self._pattern_score_map[code_str] = score
        except Exception:
            self._pattern_score_map = {}

    def _load_segment_model(self):
        try:
            path = SEGMENT_MODEL_PATH
            if not path.exists():
                return
            payload = joblib.load(path)
            self.segment_model = payload.get("model")
            self.segment_label_encoder = payload.get("label_encoder")
            self.segment_feature_names = payload.get("feature_names", self.segment_feature_names)
        except Exception:
            pass

    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    def _sanitize(self, val: Any, max_val: float = 999999999999.0) -> float:
        """Prevent PostgreSQL numeric field overflow."""
        try:
            if val is None: return 0.0
            fval = float(val)
            if fval > max_val: return max_val
            if fval < -max_val: return -max_val
            return fval
        except (ValueError, TypeError):
            return 0.0

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    @staticmethod
    def _normalize_segment_label(value: Optional[str]) -> str:
        if not value: return "unknown"
        label = value.lower()
        return "best" if label == "super" else label

    def _segments_allow_entry(self, labels: List[str]) -> bool:
        normalized = [self._normalize_segment_label(lbl) for lbl in labels]
        if not normalized or any(lbl in ("unknown", "bad", "risk", "flat") for lbl in normalized):
            return False
        middle_count = sum(1 for lbl in normalized if lbl == "middle")
        if middle_count >= 2: return False
        if all(lbl in ALLOWED_SEGMENT_LABELS for lbl in normalized): return True
        if len(normalized) >= 3 and middle_count == 1:
            first, second, last = normalized[0], normalized[1], normalized[-1]
            if (first == "middle" or second == "middle") and last in ALLOWED_SEGMENT_LABELS:
                return True
        return False

    async def _update_segment_predictions(self, conn, token_id: int, iterations_count: int) -> Optional[List[str]]:
        if not self.segment_model or not self.segment_label_encoder:
            return None
        
        # Optimized: iterations_count is passed instead of queried
        rows = await conn.fetch(
            """
            SELECT usd_price, buy_count, sell_count
            FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL
            ORDER BY ts ASC
            LIMIT $2
            """,
            token_id,
            self.segment_series_limit,
        )
        if not rows:
            return None
        
        series = extract_series(rows)
        segment_dicts = feature_vector_for_segments(series)
        predicted: List[str] = []
        for idx, feats in enumerate(segment_dicts):
            segment_end = SEGMENT_BOUNDS[idx][1]
            if iterations_count < segment_end or feats is None:
                predicted.append("unknown")
                continue
            vec = [float(idx + 1)] + [float(feats.get(key, 0.0)) for key in SEGMENT_FEATURE_KEYS]
            label_idx = self.segment_model.predict([vec])[0]
            label = self.segment_label_encoder.inverse_transform([label_idx])[0]
            predicted.append(self._normalize_segment_label(label))
        
        decision = "buy" if self._segments_allow_entry(predicted) else "not"

        # Trade Type Check (Simplified/Batched logic would be better but keeping V3 logic for correctness)
        # Porting relevant parts of V3 trade check...
        try:
            check_points = [250, 700, 1000]
            current_check_point = next((cp for cp in reversed(check_points) if iterations_count >= cp), None)
            
            if current_check_point:
                last_checked = self.trade_check_done.get(token_id, 0)
                if current_check_point > last_checked:
                    already_checked = await conn.fetchval("SELECT has_real_trading FROM tokens WHERE id=$1", token_id)
                    if already_checked is None or current_check_point > last_checked:
                        token_pair = await conn.fetchval("SELECT token_pair FROM tokens WHERE id=$1", token_id)
                        if token_pair:
                            has_real = await check_token_has_real_trading(token_id, token_pair, save_to_db=True)
                            if has_real is False: decision = "not"
                            if current_check_point >= 250:
                                await conn.execute("UPDATE tokens SET no_swap_after_second_corridor = $2 WHERE id = $1", token_id, not has_real)
                        self.trade_check_done[token_id] = current_check_point
                    elif already_checked is False:
                        decision = "not"
        except Exception: pass

        # Liquidity Withdrawal Detection
        withdraw_iter = None
        if self.withdraw_check_iter > 0 and self.withdraw_window > 0 and iterations_count >= self.withdraw_check_iter:
            recent_rows = await conn.fetch(
                "SELECT usd_price, mcap FROM token_metrics_seconds WHERE token_id=$1 ORDER BY ts DESC LIMIT $2",
                token_id, self.withdraw_window
            )
            withdraw_iter = self._detect_liquidity_withdraw(iterations_count, recent_rows)
        
        if withdraw_iter is not None:
            decision = "not"

        # Update DB
        await conn.execute(
            """
            UPDATE tokens
            SET pattern_segment_1=$2, pattern_segment_2=$3, pattern_segment_3=$4, pattern_segment_decision=$5
            WHERE id=$1
            """,
            token_id,
            predicted[0] if len(predicted) > 0 else "unknown",
            predicted[1] if len(predicted) > 1 else "unknown",
            predicted[2] if len(predicted) > 2 else "unknown",
            decision,
        )
        return predicted

    def _detect_liquidity_withdraw(self, total_points: int, recent_rows: Sequence[Dict[str, Any]]) -> Optional[int]:
        if not recent_rows or len(recent_rows) < self.withdraw_window:
            return None
        window_rows = list(reversed(recent_rows))
        prices = [float(r.get("usd_price") or 0.0) for r in window_rows]
        mcaps = [float(r.get("mcap") or 0.0) for r in window_rows]
        eps = max(self.withdraw_equal_eps, 0.0)
        
        if all(val <= eps for val in prices) or all(val <= eps for val in mcaps):
            return total_points - len(prices) + 1
        
        if (max(prices) - min(prices) <= eps) or (max(mcaps) - min(mcaps) <= eps):
            return total_points - len(prices) + 1
        return None

    async def save_batch_data(self, batch_data: List[Dict[str, Any]]) -> int:
        if not batch_data: return 0
        ts = int(time.time())
        processed_count = 0
        
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    token_ids = [d['_internal_token_id'] for d in batch_data if d.get('_internal_token_id')]
                    if not token_ids: return 0

                    # 1. Validate IDs still exist (prevent FK violation after purge)
                    valid_rows = await conn.fetch("SELECT id FROM tokens WHERE id = ANY($1)", token_ids)
                    valid_ids = {r['id'] for r in valid_rows}
                    if not valid_ids: return 0

                    batch_data = [d for d in batch_data if d.get('_internal_token_id') in valid_ids]
                    token_ids = list(valid_ids)

                    # 3. Batched Iteration Counts
                    iter_counts_rows = await conn.fetch("""
                        SELECT token_id, COUNT(*) as count FROM token_metrics_seconds WHERE token_id = ANY($1) GROUP BY token_id
                    """, token_ids)
                    iter_map = {r['token_id']: r['count'] for r in iter_counts_rows}

                # We save metrics one-by-one or in a way that doesn't fail the whole batch if one token overflows
                for data in batch_data:
                    t_id = data.get('_internal_token_id')
                    if not t_id: continue
                    
                    try:
                        async with conn.transaction():
                            await conn.execute("""
                                INSERT INTO token_metrics_seconds (
                                    token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
                                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                            """, t_id, ts, self._sanitize(data.get('usdPrice')), self._sanitize(data.get('liquidity')),
                                self._sanitize(data.get('fdv')), self._sanitize(data.get('mcap')), int(data.get('priceBlockId', 0)),
                                int(data.get('priceBlockId', 0)))
                            
                            # Also update core table price (shared pool)
                            await conn.execute("""
                                UPDATE tokens 
                                SET usd_price = $1, liquidity = $2, fdv = $3, mcap = $4, price_block_id = $5, token_updated_at = CURRENT_TIMESTAMP
                                WHERE id = $6
                            """, self._sanitize(data.get('usdPrice')), self._sanitize(data.get('liquidity')),
                                self._sanitize(data.get('fdv')), self._sanitize(data.get('mcap')), int(data.get('priceBlockId', 0)), t_id)
                    except Exception as e:
                        if self.debug:
                            print(f"[AnalyzerV4] Failed to save metrics for token {t_id}: {e}")

                # 5. Analysis & Guards (Outside transaction to avoid deadlocks)
                # But inside pool.acquire() so conn is still valid
                for data in batch_data:
                    t_id = data.get('_internal_token_id')
                    try:
                        iters = iter_map.get(t_id, 0)
                        if t_id:
                            await self._process_single_token_analysis(conn, t_id, data, iters, ts)
                            processed_count += 1
                    except Exception as e:
                        if self.debug:
                            print(f"[AnalyzerV4] Analysis failed for token {t_id}: {e}")


                # 5. Median Propagation (Batch-wide)
                await conn.execute("""
                    WITH prev AS (
                        SELECT DISTINCT ON (token_id) token_id, median_amount_sol, median_amount_usd, median_token_price
                        FROM token_metrics_seconds WHERE token_id = ANY($1) AND ts < $2
                        AND (median_amount_sol IS NOT NULL OR median_amount_usd IS NOT NULL OR median_token_price IS NOT NULL)
                        ORDER BY token_id, ts DESC
                    )
                    UPDATE token_metrics_seconds SET 
                        median_amount_sol = prev.median_amount_sol, median_amount_usd = prev.median_amount_usd, median_token_price = prev.median_token_price
                    FROM prev WHERE token_metrics_seconds.token_id = prev.token_id AND token_metrics_seconds.ts = $2
                """, token_ids, ts)

        except Exception as e:
            if self.debug: print(f"[AnalyzerV4] ❌ Batch save error: {e}")
            return 0
        return processed_count

    async def _process_single_token_analysis(self, conn, token_id: int, data: Dict[str, Any], iters: int, ts: int):
        # 1. AI Segment Predictions
        await self._update_segment_predictions(conn, token_id, iters)

        # 2. Guards
        archived = await self._apply_price_corridor_guard(conn, token_id, iters)
        if archived: return

        # Post-entry Drop Guard
        await self._check_post_entry_drop(conn, token_id, iters)

        # 3. Auto-Buy/Sell Logic
        await self._run_auto_trading_logic(conn, token_id, iters)

    # MOVED TO CLEANER V4
    async def _check_zero_tail(self, conn, token_id: int, iters: int):
        pass

    async def _check_frozen_price(self, conn, token_id: int, iters: int):
        pass

    async def _check_post_entry_drop(self, conn, token_id: int, iters: int):
        post_end = int(getattr(config, 'PRICE_CORRIDOR_FINAL_END', 1000))
        if iters < post_end: return
        
        rows = await conn.fetch("""
            SELECT usd_price FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
            ORDER BY ts ASC LIMIT $2
        """, token_id, post_end)
        
        if rows:
            prices = [float(r['usd_price']) for r in rows]
            drop_threshold = float(getattr(config, 'POST_ENTRY_DROP_THRESHOLD', 0.15))
            if self._detect_post_entry_drop(prices, self.entry_sec, post_end, drop_threshold):
                await conn.execute("UPDATE tokens SET pattern_segment_decision = 'not' WHERE id = $1", token_id)

    def _detect_post_entry_drop(self, prices: List[float], entry_sec: int, post_end: int, threshold: float) -> bool:
        entry_idx = min(entry_sec - 1, len(prices) - 1)
        if entry_idx < 0: return False
        entry_p = prices[entry_idx]
        if entry_p <= 0: return False
        
        window = prices[entry_idx:min(post_end, len(prices))]
        if not window: return False
        return (entry_p - min(window)) / entry_p >= threshold

    async def _run_auto_trading_logic(self, conn, token_id: int, iters: int):
        # Auto-Sell
        await self._check_auto_sell(conn, token_id)
        # Auto-Buy
        await self._check_auto_buy(conn, token_id, iters)

    async def _check_auto_sell(self, conn, token_id: int):
        pos = await conn.fetchrow("""
            SELECT entry_token_amount, entry_amount_usd, entry_sol_price
            FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL
            ORDER BY id DESC LIMIT 1
        """, token_id)
        if not pos: return
        
        price = await conn.fetchval("SELECT usd_price FROM tokens WHERE id=$1", token_id)
        if not price or price <= 0: return
        
        from _v2_sol_price import get_current_sol_price
        sol_p = get_current_sol_price() or float(getattr(config, 'SOL_PRICE_FALLBACK', 193.0))
        
        entry_sol = float(pos['entry_amount_usd']) / float(pos['entry_sol_price'])
        curr_sol = (float(pos['entry_token_amount']) * float(price)) / sol_p if sol_p > 0 else 0
        
        target = float(getattr(config, 'TARGET_RETURN', 0.035))
        if curr_sol >= entry_sol * (1.0 + target):
            asyncio.create_task(sell_real(token_id))

    async def _check_auto_buy(self, conn, token_id: int, iters: int):
        if not getattr(config, "AUTO_BUY_ENABLED", True): return
        if iters < self.entry_sec: return
        
        # Check if already bought
        has_pos = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL)", token_id)
        if has_pos: return
        
        row = await conn.fetchrow("SELECT pattern_segment_decision, num_buys_24h, num_sells_24h FROM tokens WHERE id=$1", token_id)
        if not row or (row['pattern_segment_decision'] or "").lower() != 'buy': return
        
        min_tx = int(getattr(config, "MIN_TX_COUNT", 100))
        min_sell = float(getattr(config, "MIN_SELL_SHARE", 0.2))
        total = int(row['num_buys_24h'] or 0) + int(row['num_sells_24h'] or 0)
        
        if total < min_tx: return
        if total > 0 and (int(row['num_sells_24h'] or 0) / total) < min_sell: return
        
        # Momentum check
        momentum_ok = False
        if iters >= self.holder_momentum_iter:
            m_res = await evaluate_holder_momentum(conn, token_id, self.holder_momentum_iter)
            momentum_ok = bool(m_res.get("ok"))
        
        if momentum_ok and iters >= self.auto_buy_iter:
            asyncio.create_task(buy_real(token_id))

    async def _apply_price_corridor_guard(self, conn, token_id: int, iters: int) -> bool:
        windows = self._get_corridor_windows()
        if not windows: return False
        
        max_end = max(win['end'] for win in windows)
        if iters < min(win['end'] for win in windows): return False

        rows = await conn.fetch(
            "SELECT usd_price FROM token_metrics_seconds WHERE token_id=$1 AND usd_price IS NOT NULL ORDER BY ts ASC LIMIT $2",
            token_id, max_end
        )
        prices = [float(r['usd_price']) for r in rows if r['usd_price'] is not None]
        if not prices: return False

        for window in windows:
            if len(prices) < window['end']: continue
            drop_data = self._calc_window_drop_recovery(prices, window['start'], window['end'])
            if drop_data and drop_data[0] >= window['drop_threshold'] and drop_data[1] < window['recovery_min']:
                await self._flag_corridor_drop(conn, token_id, window['label'], window['stage'], drop_data[0], drop_data[1])
                return True
        return False

    def _get_corridor_windows(self) -> List[Dict[str, Any]]:
        if not getattr(config, 'PRICE_CORRIDOR_GUARD_ENABLED', False): return []
        prefix = getattr(config, 'PRICE_CORRIDOR_PATTERN_PREFIX', 'corridor_drop')
        windows = []
        def add(en, stg, s, e, dt, rm):
            if en and s and e and s > 0 and e > s:
                windows.append({"stage": stg, "start": int(s), "end": int(e), "drop_threshold": float(dt), "recovery_min": float(rm), "label": f"{prefix}_{stg}"})
        add(getattr(config, 'PRICE_CORRIDOR_PRE_ENABLED', False), "pre", getattr(config, 'PRICE_CORRIDOR_PRE_START', 75), getattr(config, 'PRICE_CORRIDOR_PRE_END', 730), getattr(config, 'PRICE_CORRIDOR_PRE_DROP_THRESHOLD', 0.18), getattr(config, 'PRICE_CORRIDOR_PRE_RECOVERY_MIN', 0.5))
        add(getattr(config, 'PRICE_CORRIDOR_FINAL_ENABLED', False), "final", getattr(config, 'PRICE_CORRIDOR_FINAL_START', 115), getattr(config, 'PRICE_CORRIDOR_FINAL_END', 125), getattr(config, 'PRICE_CORRIDOR_FINAL_DROP_THRESHOLD', 0.20), getattr(config, 'PRICE_CORRIDOR_FINAL_RECOVERY_MIN', 0.4))
        return windows

    @staticmethod
    def _calc_window_drop_recovery(prices: List[float], start_iter: int, end_iter: int) -> Optional[List[float]]:
        s_idx, e_idx = max(0, start_iter - 1), min(len(prices), end_iter)
        if e_idx <= s_idx: return None
        win, pref = prices[s_idx:e_idx], prices[:s_idx]
        if not win or not pref: return None
        peak, trough = max(pref), min(win)
        if peak <= 0: return None
        delta = peak - trough
        return [(peak - trough) / peak, 1.0 if delta <= 1e-9 else (win[-1] - trough) / delta]

    async def _flag_corridor_drop(self, conn, token_id: int, label: str, stage: str, drop_pct: float, recovery_pct: float):
        await conn.execute("UPDATE tokens SET pattern_code = $2, token_updated_at = CURRENT_TIMESTAMP WHERE id=$1", token_id, label)

    async def get_tokens_batch(self) -> List[Dict[str, Any]]:
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                total = await conn.fetchval("SELECT COUNT(*) FROM tokens") or 0
                if total == 0: return []
                self._offset %= total
                rows = await conn.fetch(
                    "SELECT id, token_address FROM tokens ORDER BY token_updated_at ASC NULLS FIRST, id ASC OFFSET $1 LIMIT $2",
                    self._offset, self.batch_size
                )
                self._offset = (self._offset + len(rows)) % total
                return [{"token_id": r["id"], "token_address": r["token_address"]} for r in rows]
        except Exception: return []

    async def get_jupiter_data(self, tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            await self.ensure_session()
            addresses = [t["token_address"] for t in tokens]
            url = f"{config.JUPITER_SEARCH_API}?query={','.join(addresses[:70])}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Inject token_id for save_batch_data
                    addr_to_id = {t["token_address"]: t["token_id"] for t in tokens}
                    for item in data:
                        item['_internal_token_id'] = addr_to_id.get(item.get('id'))
                    return data
                else:
                    return {"error": f"Jupiter API error {resp.status}", "retry_after": 5}
        except Exception as e:
            return {"error": str(e), "retry_after": 5}
        return []

    async def _scan_loop(self):
        while self.is_scanning:
            try:
                tokens = await self.get_tokens_batch()
                if tokens:
                    data = await self.get_jupiter_data(tokens)
                    if data: await self.save_batch_data(data)
            except Exception: pass
            await asyncio.sleep(self.scan_interval)

    async def start(self):
        if not self.is_scanning:
            self.is_scanning = True
            asyncio.create_task(self._scan_loop())
            return {"success": True}
        return {"success": False}

    async def stop(self):
        self.is_scanning = False
        return {"success": True}

_instance: Optional[JupiterAnalyzerV4] = None
async def get_analyzer() -> JupiterAnalyzerV4:
    global _instance
    if _instance is None: _instance = JupiterAnalyzerV4()
    return _instance

async def refresh_missing_jupiter_data(limit=10):
    analyzer = await get_analyzer()
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, token_address FROM tokens 
            WHERE usd_price IS NULL OR liquidity IS NULL
            ORDER BY token_updated_at ASC NULLS FIRST LIMIT $1
        """, limit)
        if not rows: return
        tokens = [{"token_id": r["id"], "token_address": r["token_address"]} for r in rows]
        data = await analyzer.get_jupiter_data(tokens)
        if data: await analyzer.save_batch_data(data)

async def refresh_until_three():
    # Helper to bootstrap new tokens
    for _ in range(3):
        await refresh_missing_jupiter_data(50)
        await asyncio.sleep(1)
