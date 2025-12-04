#!/usr/bin/env python3

import asyncio
import aiohttp
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Sequence

import joblib

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
from _v2_buy_sell import finalize_token_sale, buy_real, sell_real
from _v3_db_utils import get_token_iterations_count, evaluate_holder_momentum
from _v3_trade_type_checker import check_token_has_real_trading

BASE_DIR = Path(__file__).resolve().parents[1]
SEGMENT_MODEL_PATH = (BASE_DIR / "models" / "pattern_segments.pkl").resolve()
ALLOWED_SEGMENT_LABELS = {"best", "good"}

class JupiterAnalyzerV3:

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
        # Use AUTO_BUY_ENTRY_SEC for both entry point and decision freezing
        # This ensures consistency: if decision = "not" at entry point, it won't change to "buy" later
        self.entry_sec: int = int(getattr(config, 'AUTO_BUY_ENTRY_SEC', 150))
        self._pattern_score_map = {}
        self.holder_momentum_iter: int = int(getattr(config, 'HOLDER_MOMENTUM_CHECK_ITER', 500))
        self.auto_buy_iter: int = int(getattr(config, 'AUTO_BUY_TRIGGER_ITER', self.holder_momentum_iter + 10))

        # Liquidity withdrawal (flat price) detection settings
        # Use AUTO_BUY_ENTRY_SEC as check iteration (same as entry point)
        self.withdraw_check_iter: int = int(getattr(config, 'AUTO_BUY_ENTRY_SEC', 150))
        self.withdraw_window: int = int(getattr(config, 'LIQUIDITY_WITHDRAW_WINDOW', 10))
        self.withdraw_equal_eps: float = float(getattr(config, 'LIQUIDITY_WITHDRAW_EQUAL_EPS', 1e-6))
        self.segment_series_limit: int = max(1000, self.withdraw_check_iter + self.withdraw_window)

        # Track last trade-type checkpoint per token (0/35/85/170) to avoid spamming Helius
        self.trade_check_done = {}

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
        self.segment_model = None
        self.segment_label_encoder = None
        self.segment_feature_names: List[str] = ["segment_index"] + SEGMENT_FEATURE_KEYS
        self._load_segment_model()
        
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        if getattr(config, 'METRICS_SECONDS_ENABLED', False):
            try:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS token_metrics_seconds (
                            id SERIAL PRIMARY KEY,
                            token_id INTEGER NOT NULL REFERENCES tokens(id),
                            ts BIGINT NOT NULL,
                            usd_price DOUBLE PRECISION,
                            liquidity DOUBLE PRECISION,
                            fdv DOUBLE PRECISION,
                            mcap DOUBLE PRECISION,
                            price_block_id BIGINT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    await conn.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_token_metrics_seconds_unique
                        ON token_metrics_seconds(token_id, ts)
                        """
                    )
            except Exception:
                pass

    def _load_segment_model(self):
        try:
            path = SEGMENT_MODEL_PATH
            if not path.exists():
                # print(f"[Analyzer] ⚠️ Segment model not found at {path}")
                return
            payload = joblib.load(path)
            self.segment_model = payload.get("model")
            self.segment_label_encoder = payload.get("label_encoder")
            self.segment_feature_names = payload.get("feature_names", self.segment_feature_names)
            if self.segment_model is None or self.segment_label_encoder is None:
                # print("[Analyzer] ⚠️ Segment model payload missing components")
                pass
        except Exception as exc:
            # print(f"[Analyzer] ⚠️ Failed to load segment model: {exc}")
            pass

    @staticmethod
    def _normalize_segment_label(value: Optional[str]) -> str:
        if not value:
            return "unknown"
        label = value.lower()
        if label == "super":
            return "best"
        return label

    def _detect_post_entry_drop(self, prices: List[float], entry_sec: int, post_entry_end: int, drop_threshold: float = 0.15) -> bool:
        """Detect if there's a significant price drop after entry point.
        
        Args:
            prices: List of prices from token start (index = second)
            entry_sec: Entry point in seconds (AUTO_BUY_ENTRY_SEC)
            post_entry_end: End of post-entry window (final corridor end)
            drop_threshold: Minimum drop percentage to consider significant (default 15%)
        
        Returns:
            True if significant drop detected, False otherwise
        """
        if not prices or len(prices) < post_entry_end:
            return False
        
        # Get price at entry point (155s)
        entry_idx = min(entry_sec - 1, len(prices) - 1)
        if entry_idx < 0:
            return False
        
        entry_price = prices[entry_idx]
        if entry_price <= 0:
            return False
        
        # Check prices in post-entry window (155-170s)
        post_entry_start_idx = entry_idx
        post_entry_end_idx = min(post_entry_end, len(prices))
        
        if post_entry_end_idx <= post_entry_start_idx:
            return False
        
        post_entry_prices = prices[post_entry_start_idx:post_entry_end_idx]
        if not post_entry_prices:
            return False
        
        # Find minimum price in post-entry window
        min_price = min(post_entry_prices)
        
        # Calculate drop percentage
        drop_pct = (entry_price - min_price) / entry_price if entry_price > 0 else 0.0
        
        # If drop is significant (>= threshold), return True
        return drop_pct >= drop_threshold

    def _detect_liquidity_withdraw(self, total_points: int, recent_rows: Sequence[Dict[str, Any]]) -> Optional[int]:
        """Detect whether liquidity was withdrawn (flat/zero price) based on recent rows.

        Returns the iteration where the suspicious window starts, or None if everything looks fine.
        """
        if (
            self.withdraw_check_iter <= 0
            or self.withdraw_window <= 0
            or total_points < self.withdraw_check_iter
            or not recent_rows
        ):
            return None

        # Ensure we have chronological order (query returns DESC)
        window_rows = list(reversed(recent_rows))
        if len(window_rows) < self.withdraw_window:
            return None

        # Take exactly the configured window (oldest -> newest)
        window_rows = window_rows[-self.withdraw_window :]
        prices: List[float] = []
        mcaps: List[float] = []
        for row in window_rows:
            price_raw = row.get("usd_price")
            mcap_raw = row.get("mcap")
            price_val = float(price_raw) if price_raw is not None else 0.0
            mcap_val = float(mcap_raw) if mcap_raw is not None else 0.0
            prices.append(price_val)
            mcaps.append(mcap_val)

        eps = max(self.withdraw_equal_eps, 0.0)

        # Only consider window drained if все значения "нулевые" (плотная последовательность)
        all_zero_price = all(val <= eps for val in prices)
        all_zero_mcap = all(val <= eps for val in mcaps)
        if all_zero_price or all_zero_mcap:
            start_iter = total_points - len(prices) + 1
            return start_iter
        
        price_range = max(prices) - min(prices)
        mcap_range = max(mcaps) - min(mcaps)

        if price_range <= eps or mcap_range <= eps:
            start_iter = total_points - len(prices) + 1
            return start_iter

        return None

    def _segments_allow_entry(self, labels: List[str]) -> bool:
        normalized = [self._normalize_segment_label(lbl) for lbl in labels]
        if not normalized or any(lbl in ("unknown", "bad", "risk", "flat") for lbl in normalized):
            return False
        
        # Count how many "middle" segments we have
        middle_count = sum(1 for lbl in normalized if lbl == "middle")
        
        # CRITICAL: If there are two or more "middle" segments, entry is NOT allowed
        if middle_count >= 2:
            return False
        
        # Standard case: all segments are "best" or "good" (or "super")
        if all(lbl in ALLOWED_SEGMENT_LABELS for lbl in normalized):
            return True
        
        # Special case: growth trend with ONE "middle" in first or second segment
        # CRITICAL: If "middle" is in first or second segment, last segment MUST be "good" or "best"
        if len(normalized) >= 3 and middle_count == 1:
            first = normalized[0]
            second = normalized[1] if len(normalized) > 1 else None
            last = normalized[-1]
            
            # Check if "middle" is in first or second segment (only one middle allowed)
            has_middle_in_early = (first == "middle") or (second == "middle")
            
            if has_middle_in_early:
                # If "middle" is in first/second segment, last MUST be "good" or "best" (or "super")
                if last not in ALLOWED_SEGMENT_LABELS:
                    return False
                # All segments must be valid (no bad/risk/flat/unknown)
                # We already checked this above, so if we're here, all are valid
                # Allow entry if last segment shows growth
                return True
        
        return False

    async def _update_segment_predictions(self, conn, token_id: int) -> Optional[List[str]]:
        if not self.segment_model or not self.segment_label_encoder:
            return None
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
        iterations_count: Optional[int] = None
        try:
            iterations_count = await get_token_iterations_count(conn, token_id)
        except Exception:
            iterations_count = None
        segment_dicts = feature_vector_for_segments(series)
        predicted: List[str] = []
        for idx, feats in enumerate(segment_dicts):
            segment_end = SEGMENT_BOUNDS[idx][1]
            if iterations_count is None or iterations_count < segment_end or feats is None:
                predicted.append("unknown")
                continue
            vec = [float(idx + 1)] + [float(feats.get(key, 0.0)) for key in SEGMENT_FEATURE_KEYS]
            label_idx = self.segment_model.predict([vec])[0]
            label = self.segment_label_encoder.inverse_transform([label_idx])[0]
            predicted.append(self._normalize_segment_label(label))
        decision = "buy" if self._segments_allow_entry(predicted) else "not"

        # Trade Type Check: Verify real trading (SWAP) vs only transfers (TRANSFER)
        # Check at three points: after segment 1 (35s), segment 2 (85s), and segment 3 (170s)
        # This prevents entering tokens that only have transfers (no real market)
        try:
            if iterations_count is None:
                iterations_count = await get_token_iterations_count(conn, token_id)
            
            # Check points: segment boundaries (250s, 700s, 1000s)
            check_points = [250, 700, 1000]
            
            # Determine which check point we're at
            current_check_point = None
            for check_point in check_points:
                if iterations_count >= check_point:
                    current_check_point = check_point
                else:
                    break
            
            # Perform check if we're at a checkpoint and haven't checked this checkpoint yet
            if current_check_point:
                last_checked = self.trade_check_done.get(token_id, 0)
                if current_check_point > last_checked:
                    # Check if already checked (has_real_trading is not NULL)
                    already_checked = await conn.fetchval(
                        "SELECT has_real_trading FROM tokens WHERE id=$1",
                        token_id
                    )
                    
                    # Only call Helius if we have to (NULL or advancing checkpoint)
                    if already_checked is None or current_check_point > last_checked:
                        token_pair_row = await conn.fetchrow(
                            "SELECT token_pair FROM tokens WHERE id=$1",
                            token_id
                        )
                        token_pair = token_pair_row.get('token_pair') if token_pair_row else None
                        
                        has_real_trading_result = None
                        if token_pair:
                            try:
                                has_real_trading_result = await check_token_has_real_trading(token_id, token_pair, save_to_db=True)
                                if not has_real_trading_result:
                                    decision = "not"
                            except Exception:
                                decision = "not"
                        if has_real_trading_result is not None and current_check_point >= 85:
                            await conn.execute(
                                """
                                UPDATE tokens
                                SET no_swap_after_second_corridor = $2
                                WHERE id = $1
                                """,
                                token_id,
                                not has_real_trading_result
                            )
                        # Update checkpoint regardless of outcome to avoid spamming
                        self.trade_check_done[token_id] = current_check_point
                    else:
                        # Use cached result from DB
                        if already_checked is False:
                            decision = "not"
        except Exception as e:
            # if getattr(config, "DEBUG", False):
            #     print(f"[JUNO] token {token_id}: error in trade type check: {e}")
            pass

        # Liquidity withdrawal detection: if last N values are flat/zero after AUTO_BUY_ENTRY_SEC iterations
        withdraw_iter = None
        total_points = 0
        try:
            if self.withdraw_check_iter > 0 and self.withdraw_window > 0:
                total_points = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM token_metrics_seconds
                        WHERE token_id=$1
                        """,
                        token_id,
                    )
                    or 0
                )
                if total_points >= self.withdraw_check_iter:
                    recent_rows = await conn.fetch(
                        """
                        SELECT usd_price, mcap
                        FROM token_metrics_seconds
                        WHERE token_id=$1
                        ORDER BY ts DESC
                        LIMIT $2
                        """,
                        token_id,
                        self.withdraw_window,
                    )
                    withdraw_iter = self._detect_liquidity_withdraw(total_points, recent_rows)
        except Exception:
            withdraw_iter = None

        if withdraw_iter is not None:
            decision = "not"
            # if getattr(config, "DEBUG", False):
            #     print(f"[JUNO] token {token_id}: liquidity withdrawal detected (flat/zero metrics from iter {withdraw_iter}, window={self.withdraw_window})")
        
        # Post-entry drop detection: check if price drops significantly after entry point (155-170s)
        # This prevents buying tokens that look good at 155s but crash immediately after
        post_entry_drop_detected = False
        try:
            if total_points == 0:
                total_points = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM token_metrics_seconds
                        WHERE token_id=$1
                        """,
                        token_id,
                    )
                    or 0
                )
            
            # Only check post-entry drop if token has enough data (>= 170s)
            post_entry_end_sec = int(getattr(config, 'PRICE_CORRIDOR_FINAL_END', 170))
            if total_points >= post_entry_end_sec:
                # Get all prices up to post_entry_end_sec
                price_rows = await conn.fetch(
                    """
                    SELECT usd_price
                    FROM token_metrics_seconds
                    WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price > 0
                    ORDER BY ts ASC
                    LIMIT $2
                    """,
                    token_id,
                    post_entry_end_sec,
                )
                if price_rows:
                    prices = [float(row['usd_price']) for row in price_rows]
                    drop_threshold = float(getattr(config, 'POST_ENTRY_DROP_THRESHOLD', 0.15))  # 15% drop
                    post_entry_drop_detected = self._detect_post_entry_drop(
                        prices, 
                        self.entry_sec, 
                        post_entry_end_sec,
                        drop_threshold
                    )
                    
                    if post_entry_drop_detected:
                        decision = "not"
                        # if getattr(config, "DEBUG", False):
                        #     entry_price = prices[min(self.entry_sec - 1, len(prices) - 1)] if prices else 0
                        #     min_price = min(prices[self.entry_sec - 1:min(post_entry_end_sec, len(prices))]) if len(prices) > self.entry_sec else 0
                        #     drop_pct = ((entry_price - min_price) / entry_price * 100) if entry_price > 0 else 0
                        #     print(f"[JUNO] token {token_id}: post-entry drop detected (entry={self.entry_sec}s, drop={drop_pct:.1f}%, threshold={drop_threshold*100:.1f}%)")
        except Exception as e:
            # if getattr(config, "DEBUG", False):
            #     print(f"[JUNO] token {token_id}: error checking post-entry drop: {e}")
            pass
        
        # MIN_TX_COUNT and MIN_SELL_SHARE validation (after third segment completes)
        # CRITICAL: Check only AFTER third segment completes (170s) to ensure all pattern segments are analyzed
        # Uses same iteration count as auto-buy: COUNT(*) WHERE usd_price IS NOT NULL AND usd_price > 0
        try:
            # Get third segment end point (PRICE_CORRIDOR_FINAL_END, typically 170s)
            third_segment_end = int(getattr(config, 'PRICE_CORRIDOR_FINAL_END', 170))
            min_tx = float(getattr(config, "MIN_TX_COUNT", 100))
            min_sell_share = float(getattr(config, "MIN_SELL_SHARE", 0.2))
            
            # Count iterations same way as auto-buy check (only records with valid price)
            iterations_count = await get_token_iterations_count(conn, token_id)
            
            # Only check if third segment has completed (iterations >= third_segment_end)
            # This ensures all pattern segments (1, 2, 3) are analyzed before setting decision based on transaction metrics
            if iterations_count >= third_segment_end:
                # Get transaction counts from tokens table
                tx_row = await conn.fetchrow(
                    """
                    SELECT num_buys_24h, num_sells_24h
                    FROM tokens
                    WHERE id=$1
                    """,
                    token_id
                )
                
                if tx_row:
                    num_buys = float(tx_row.get("num_buys_24h") or 0)
                    num_sells = float(tx_row.get("num_sells_24h") or 0)
                    total_tx = num_buys + num_sells
                    sell_share = (num_sells / total_tx) if total_tx > 0 else 0.0
                    
                    # Check MIN_TX_COUNT
                    if total_tx < min_tx:
                        decision = "not"
                        # if getattr(config, "DEBUG", False):
                        #     print(
                        #         f"[JUNO] token {token_id}: MIN_TX_COUNT check failed (total_tx={total_tx:.0f} < {min_tx:.0f}) - setting decision=not"
                        #     )
                    # Check MIN_SELL_SHARE (only if MIN_TX_COUNT passed)
                    elif sell_share < min_sell_share:
                        decision = "not"
                        # if getattr(config, "DEBUG", False):
                        #     print(
                        #         f"[JUNO] token {token_id}: MIN_SELL_SHARE check failed (sell_share={sell_share:.2%} < {min_sell_share:.2%}, total_tx={total_tx:.0f}) - setting decision=not"
                        #     )
        except Exception as e:
            if getattr(config, "DEBUG", False):
                print(f"[JUNO] token {token_id}: error checking MIN_TX_COUNT/MIN_SELL_SHARE: {e}")
            pass
        
        # CRITICAL: If token reached AUTO_BUY_ENTRY_SEC and decision = "not" with no open position,
        # freeze the decision to prevent accidental entry after entry point
        # This ensures that if AI decided "not" at entry point, it won't change to "buy" later
        try:
            # Use total_points from liquidity withdrawal check (already calculated above)
            # If not calculated, use same query format (without price filter, as in liquidity check)
            if total_points == 0:
                total_points = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM token_metrics_seconds
                        WHERE token_id=$1
                        """,
                        token_id,
                    )
                    or 0
                )
            
            if total_points >= self.withdraw_check_iter:
                # Token reached entry point - check if decision was "not" and no open position
                current_decision = await conn.fetchval(
                    """
                    SELECT pattern_segment_decision
                    FROM tokens
                    WHERE id=$1
                    """,
                    token_id,
                )
                
                has_open_pos = await conn.fetchrow(
                    """
                    SELECT 1 FROM wallet_history
                    WHERE token_id=$1 AND exit_iteration IS NULL
                    LIMIT 1
                    """,
                    token_id,
                )
                
                # If decision was "not" at entry point and no open position → freeze it
                # This prevents decision from changing to "buy" after entry point has passed
                if (current_decision and current_decision.lower() == "not" and not has_open_pos):
                    # Keep decision as "not" - don't allow it to change to "buy" after entry point
                    decision = "not"
                    # if getattr(config, "DEBUG", False):
                    #     print(
                    #         f"[JUNO] token {token_id}: decision frozen as 'not' (reached entry point {self.withdraw_check_iter}s with decision=not, no open position)"
                    #     )
        except Exception:
            pass
 
        await conn.execute(
            """
            UPDATE tokens
            SET pattern_segment_1=$2,
                pattern_segment_2=$3,
                pattern_segment_3=$4,
                pattern_segment_decision=$5
            WHERE id=$1
            """,
            token_id,
            predicted[0] if len(predicted) > 0 else "unknown",
            predicted[1] if len(predicted) > 1 else "unknown",
            predicted[2] if len(predicted) > 2 else "unknown",
            decision,
        )
        return predicted
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def get_tokens_batch(self) -> List[Dict[str, Any]]:
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                total = await conn.fetchval("SELECT COUNT(*) FROM tokens") or 0
                if total == 0:
                    return []
                if self._total_tokens != total:
                    self._total_tokens = total
                    self._offset = self._offset % total
                
                batch_tokens = []
                current_offset = self._offset
                used_token_ids = set()
                
                all_filters = await conn.fetchval("""
                    SELECT COUNT(*) FROM tokens
                """)
                
                if all_filters == 0:
                    return []
                
                current_offset = self._offset % all_filters
                
                limit = min(self.batch_size, all_filters)
                primary_rows = await conn.fetch(
                    """
                    SELECT id, token_address
                    FROM tokens
                    ORDER BY token_updated_at ASC NULLS FIRST, id ASC
                    OFFSET $1 LIMIT $2
                    """,
                    current_offset,
                    limit,
                )
                
                for row in primary_rows:
                    if row["id"] in used_token_ids:
                        continue
                    used_token_ids.add(row["id"])
                    batch_tokens.append({
                        "token_id": row["id"], 
                        "token_address": row["token_address"]
                    })

                if len(batch_tokens) < limit and all_filters > len(batch_tokens):
                    remain = limit - len(batch_tokens)
                    secondary_rows = await conn.fetch(
                        """
                        SELECT id, token_address
                        FROM tokens
                        ORDER BY token_updated_at ASC NULLS FIRST, id ASC
                        OFFSET 0 LIMIT $1
                        """,
                        remain,
                    )
                    for row in secondary_rows:
                        if row["id"] in used_token_ids:
                            continue
                        used_token_ids.add(row["id"])
                        batch_tokens.append({
                            "token_id": row["id"], 
                            "token_address": row["token_address"]
                        })
                
                try:
                    min_request = int(getattr(config, 'JUPITER_MIN_REQUEST_SIZE', 20))
                    effective_advance = min(min_request, len(batch_tokens)) if len(batch_tokens) >= min_request else len(batch_tokens)
                    effective_advance = max(1, effective_advance)
                except Exception:
                    effective_advance = max(1, len(batch_tokens))

                self._offset = (current_offset + effective_advance) % all_filters
                
                return batch_tokens

        except Exception as e:
            return []
    
    async def get_jupiter_data(self, tokens: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            await self.ensure_session()

            addresses = [t["token_address"] for t in tokens]
            
            hard_cap = 70

            if len(addresses) > hard_cap:
                addresses = addresses[:hard_cap]

            base = f"{config.JUPITER_SEARCH_API}?query="
            budget = int(getattr(config, 'JUPITER_MAX_URL_LEN', 8000))
            min_request = int(getattr(config, 'JUPITER_MIN_REQUEST_SIZE', 20))
            
            target_count = min_request if len(addresses) >= min_request else len(addresses)
            packed: List[str] = []

            for addr in addresses:
                if len(packed) >= target_count:
                    break
                
                candidate = ",".join(packed + [addr])

                if len(base) + len(candidate) <= budget:
                    packed.append(addr)
                else:
                    break
            if not packed and addresses:
                packed = [addresses[0]]

            query = ",".join(packed)
            url = f"{config.JUPITER_SEARCH_API}?query={query}"
            
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    retry_after = None
                    try:
                        ra = resp.headers.get('Retry-After')
                        if ra is not None:
                            retry_after = float(ra)
                    except Exception:
                        pass

                    return {"error": f"HTTP {resp.status}", "retry_after": retry_after}
                    
        except Exception as e:
            return {"error": str(e)}
    
    async def save_token_data(self, token_id: int, data: Dict[str, Any]) -> bool:
        def safe_numeric(value, max_val=999999.9999):
            try:
                v = float(value) if value is not None else None
                if v is None:
                    return None
                if abs(v) > max_val:
                    return max_val if v > 0 else -max_val
                return v
            except (ValueError, TypeError):
                return None

        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE tokens SET
                        name = $2, symbol = $3, icon = $4, decimals = $5, dev = $6,
                        circ_supply = $7, total_supply = $8, token_program = $9, holder_count = $10,
                        usd_price = $11, liquidity = $12, fdv = $13, mcap = $14, price_block_id = $15,
                        organic_score = $16, organic_score_label = $17
                    WHERE id = $1
                """, 
                    token_id,
                    data.get('name'),
                    data.get('symbol'),
                    data.get('icon'),
                    data.get('decimals'),
                    data.get('dev'),
                    float(data.get('circSupply', 0)) if data.get('circSupply') else None,
                    float(data.get('totalSupply', 0)) if data.get('totalSupply') else None,
                    data.get('tokenProgram'),
                    data.get('holderCount'),
                    float(data.get('usdPrice')) if data.get('usdPrice') is not None else None,
                    float(data.get('liquidity')) if data.get('liquidity') is not None else None,
                    float(data.get('fdv')) if data.get('fdv') is not None else None,
                    float(data.get('mcap')) if data.get('mcap') is not None else None,
                    data.get('priceBlockId'),
                    safe_numeric(data.get('organicScore')),
                    data.get('organicScoreLabel')
                )

                for period in ['5m', '1h', '6h', '24h']:
                    stats = data.get(f'stats{period}', {})
                    if stats:
                        period_suffix = f"_{period}"
                        await conn.execute(f"""
                            UPDATE tokens SET
                                price_change{period_suffix} = $2,
                                holder_change{period_suffix} = $3,
                                liquidity_change{period_suffix} = $4,
                                volume_change{period_suffix} = $5,
                                buy_volume{period_suffix} = $6,
                                sell_volume{period_suffix} = $7,
                                buy_organic_volume{period_suffix} = $8,
                                sell_organic_volume{period_suffix} = $9,
                                num_buys{period_suffix} = $10,
                                num_sells{period_suffix} = $11,
                                num_traders{period_suffix} = $12
                            WHERE id = $1
                        """, 
                            token_id,
                            safe_numeric(stats.get('priceChange')),
                            safe_numeric(stats.get('holderChange')),
                            safe_numeric(stats.get('liquidityChange')),
                            safe_numeric(stats.get('volumeChange')),
                            safe_numeric(stats.get('buyVolume')),
                            safe_numeric(stats.get('sellVolume')),
                            safe_numeric(stats.get('buyOrganicVolume')),
                            safe_numeric(stats.get('sellOrganicVolume')),
                            stats.get('numBuys'),
                            stats.get('numSells'),
                            stats.get('numTraders')
                        )

                audit = data.get('audit', {})
                if audit:
                    await conn.execute("""
                        UPDATE tokens SET
                            mint_authority_disabled = $2,
                            freeze_authority_disabled = $3,
                            top_holders_percentage = $4,
                            dev_balance_percentage = $5,
                            blockaid_rugpull = $6
                        WHERE id = $1
                    """, 
                        token_id,
                        audit.get('mintAuthorityDisabled'),
                        audit.get('freezeAuthorityDisabled'),
                        safe_numeric(audit.get('topHoldersPercentage')),
                        safe_numeric(audit.get('devBalancePercentage')),
                        audit.get('blockaidRugpull')
                    )

                first_pool = data.get('firstPool', {})
                candidate_pair = first_pool.get('id')
                row = await conn.fetchrow("SELECT token_address, token_pair FROM tokens WHERE id = $1", token_id)
                token_addr = row['token_address'] if row else None
                current_pair = row['token_pair'] if row else None
                updated_pair = None
                if candidate_pair and token_addr and candidate_pair != token_addr:
                    if current_pair != candidate_pair:
                        await conn.execute(
                            "UPDATE tokens SET token_pair = $2, token_updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                            token_id,
                            candidate_pair,
                        )
                        updated_pair = candidate_pair
                if (
                    not updated_pair and self._fallback_left > 0 and
                    (not current_pair or current_pair == token_addr or not candidate_pair or candidate_pair == token_addr)
                ):
                    try:
                        fallback = await resolve_and_update_pair(token_id, token_addr)
                        self._fallback_left -= 1
                    except Exception as _:
                        pass

                usd_price = float(data.get('usdPrice', 0)) if data.get('usdPrice') is not None else None
                mcap = float(data.get('mcap', 0)) if data.get('mcap') is not None else None
                # Analyzer collects data only; trading handled elsewhere
                
                # Логіка підрахунку спроб отримання валідної пари
                if not updated_pair and (
                    not current_pair or 
                    current_pair == token_addr or 
                    not candidate_pair or 
                    candidate_pair == token_addr
                ):
                    # Пара не валідна - збільшуємо лічильник спроб
                    await conn.execute(
                        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
                        token_id
                    )
                else:
                    # Пара валідна - скидаємо лічильник
                    if current_pair and current_pair != token_addr:
                        await conn.execute(
                            "UPDATE tokens SET pair_resolve_attempts = 0 WHERE id = $1", 
                            token_id
                        )

                await conn.execute("UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1", token_id)

                # Завжди записуємо метрики в token_metrics_seconds
                try:
                    ts = int(time.time())
                    usd_p = float(data.get('usdPrice', 0)) if data.get('usdPrice') is not None else None
                    liq = float(data.get('liquidity', 0)) if data.get('liquidity') is not None else None
                    fdv = float(data.get('fdv', 0)) if data.get('fdv') is not None else None
                    mcap = float(data.get('mcap', 0)) if data.get('mcap') is not None else None
                    pblk = data.get('priceBlockId')
                    holders = data.get('holderCount')
                    
                    # Логируем данные для отладки
                    # print(f"  💾 Token {token_id}: Price={usd_p}, MCap={mcap}, Liquidity={liq}, FDV={fdv}, BlockId={pblk}")
                    # print(f"  💾 Token {token_id}")
                    
                    # Записуємо метрики для всіх токенів
                    await conn.execute(
                        """
                        INSERT INTO token_metrics_seconds (
                            token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot, holder_count
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        ON CONFLICT (token_id, ts) DO UPDATE SET
                            usd_price = EXCLUDED.usd_price,
                            liquidity = EXCLUDED.liquidity,
                            fdv = EXCLUDED.fdv,
                            mcap = EXCLUDED.mcap,
                            price_block_id = EXCLUDED.price_block_id,
                            jupiter_slot = EXCLUDED.jupiter_slot,
                            holder_count = EXCLUDED.holder_count
                        """,
                        token_id, ts, usd_p, liq, fdv, mcap, pblk, pblk, holders
                    )

                    # Додаємо buy_count / sell_count з Jupiter (5m зріз) у поточну сек.метрику
                    try:
                        stats5m = data.get('stats5m', {}) or {}
                        b5 = stats5m.get('numBuys')
                        s5 = stats5m.get('numSells')
                        b5_i = int(b5) if b5 is not None else None
                        s5_i = int(s5) if s5 is not None else None
                        await conn.execute(
                            """
                            UPDATE token_metrics_seconds
                            SET buy_count = COALESCE($3, buy_count),
                                sell_count = COALESCE($4, sell_count)
                            WHERE token_id = $1 AND ts = $2
                            """,
                            token_id, ts, b5_i, s5_i
                        )
                    except Exception:
                        pass

                    ai_active = True
                    max_ai_age = int(getattr(config, 'ETA_MAX_TOKEN_AGE_SEC', 0) or 0)
                    if max_ai_age > 0:
                        try:
                            iterations_for_ai = await get_token_iterations_count(conn, token_id)
                            if iterations_for_ai >= max_ai_age:
                                ai_active = False
                                # if self.debug:
                                    # print(f"[Analyzer] ⏸ AI disabled for token {token_id} (iterations={iterations_for_ai} >= {max_ai_age})")
                        except Exception:
                            ai_active = True

                    if ai_active:
                        await self._update_segment_predictions(conn, token_id)

                    # Дублюємо медіани з попередньої секунди (якщо є)
                    await conn.execute("""
                        UPDATE token_metrics_seconds 
                        SET 
                            median_amount_sol = (
                                SELECT median_amount_sol 
                                FROM token_metrics_seconds 
                                WHERE token_id = $1 AND ts < $2 
                                AND median_amount_sol IS NOT NULL
                                ORDER BY ts DESC 
                                LIMIT 1
                            ),
                            median_amount_usd = (
                                SELECT median_amount_usd 
                                FROM token_metrics_seconds 
                                WHERE token_id = $1 AND ts < $2 
                                AND median_amount_usd IS NOT NULL
                                ORDER BY ts DESC 
                                LIMIT 1
                            ),
                            median_token_price = (
                                SELECT median_token_price 
                                FROM token_metrics_seconds 
                                WHERE token_id = $1 AND ts < $2 
                                AND median_token_price IS NOT NULL
                                ORDER BY ts DESC 
                                LIMIT 1
                            )
                        WHERE token_id = $1 AND ts = $2
                    """, token_id, ts)

                    # NOTE: Portfolio tracking moved to wallet_history table (real trading only)
                except Exception as e:
                    # This catch is for the metrics block, keep it silent unless critical
                    pass

                # Analyzer does not handle portfolio tracking; sells are finalized via wallet_history/AI triggers elsewhere

                # Rug/drained-liquidity guard: if last N consecutive seconds are zero/NULL OR flat (same values)
                # (both usd_price and mcap are NULL/0 OR flat) and there is an open position in wallet_history
                # → close dead token at price 0
                zero_tail_triggered = False
                zero_tail = int(getattr(config, 'ZERO_TAIL_CONSEC_SEC', 20))
                try:
                    if zero_tail > 0:
                        row = await conn.fetchrow(
                            """
                            WITH last AS (
                              SELECT usd_price, mcap
                              FROM token_metrics_seconds
                              WHERE token_id=$1
                              ORDER BY ts DESC
                              LIMIT $2
                            )
                            SELECT COUNT(*) FILTER (WHERE COALESCE(usd_price,0)>0 OR COALESCE(mcap,0)>0) AS pos_cnt,
                                   COUNT(*) AS total
                            FROM last
                            """,
                            token_id, zero_tail
                        )
                        pos_cnt = int(row['pos_cnt'] or 0) if row else 0
                        total = int(row['total'] or 0) if row else 0
                        total_points = int(
                            await conn.fetchval(
                                """
                                SELECT COUNT(*)
                                FROM token_metrics_seconds
                                WHERE token_id=$1
                                """,
                                token_id,
                            )
                            or 0
                        )
                        if total >= zero_tail and pos_cnt == 0:
                            open_position = await conn.fetchrow(
                                """
                                SELECT id, wallet_id, entry_token_amount
                                FROM wallet_history
                                WHERE token_id=$1 AND exit_iteration IS NULL
                                LIMIT 1
                                """,
                                token_id
                            )
                            zero_tail_triggered = True
                            try:
                                await conn.execute(
                                    """
                                    UPDATE tokens
                                    SET zero_tail_detected_iter = COALESCE(zero_tail_detected_iter, $2),
                                        cleaner_flagged = TRUE,
                                        cleaner_flag_reason = 'zero_tail',
                                        cleaner_flag_iteration = COALESCE(cleaner_flag_iteration, $2),
                                        cleaner_flagged_at = CURRENT_TIMESTAMP
                                    WHERE id = $1
                                    """,
                                    token_id,
                                    total_points,
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    if zero_tail > 0 and not zero_tail_triggered:
                        try:
                            await conn.execute(
                                """
                                UPDATE tokens
                                SET zero_tail_detected_iter = NULL
                                WHERE id=$1 AND zero_tail_detected_iter IS NOT NULL
                                """,
                                token_id,
                            )
                        except Exception:
                            pass

                # Frozen price detection: цена не менялась N итераций
                frozen_triggered = False
                frozen_window = int(getattr(config, 'FROZEN_PRICE_CONSEC_SEC', 0))
                try:
                    if frozen_window > 0:
                        price_rows = await conn.fetch(
                            """
                            SELECT usd_price
                            FROM token_metrics_seconds
                            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
                            ORDER BY ts DESC
                            LIMIT $2
                            """,
                            token_id,
                            frozen_window,
                        )
                        if len(price_rows) == frozen_window:
                            prices = [float(r['usd_price']) for r in price_rows]
                            eps = float(getattr(config, 'FROZEN_PRICE_EQUAL_EPS', 1e-10) or 0.0)
                            if max(prices) - min(prices) <= max(eps, 0.0):
                                total_points = int(
                                    await conn.fetchval(
                                        "SELECT COUNT(*) FROM token_metrics_seconds WHERE token_id=$1",
                                        token_id,
                                    )
                                    or 0
                                )
                                frozen_triggered = True
                                try:
                                    await conn.execute(
                                        """
                                        UPDATE tokens
                                        SET cleaner_flagged = TRUE,
                                            cleaner_flag_reason = 'frozen_price',
                                            cleaner_flag_iteration = COALESCE(cleaner_flag_iteration, $2),
                                            cleaner_flagged_at = CURRENT_TIMESTAMP
                                        WHERE id = $1
                                        """,
                                        token_id,
                                        total_points,
                                    )
                                except Exception:
                                    pass
                except Exception:
                    pass

                # AUTO-SELL: Check if current portfolio value >= entry_amount * (1 + TARGET_RETURN)
                # This works independently from AI plan (plan_sell_iteration/plan_sell_price_usd)
                # If token pumps early and reaches target profit, we sell immediately without waiting for plan
                # If yes, sell all tokens immediately (with retry logic: reduce by 1% on failure)
                # 
                # IMPORTANT: Auto-sell follows rules (TARGET_RETURN, iterations, etc.)
                # Force sell bypasses all rules and sells immediately
                try:
                    # Get open position with entry data
                    position_row = await conn.fetchrow(
                        """
                        SELECT 
                            wh.entry_token_amount,
                            wh.entry_amount_usd,
                            wh.entry_price_usd
                        FROM wallet_history wh
                        WHERE wh.token_id=$1 AND wh.exit_iteration IS NULL
                        ORDER BY wh.id DESC
                        LIMIT 1
                        """,
                                    token_id
                                    )
                    
                    if position_row:
                        entry_token_amount = float(position_row.get("entry_token_amount") or 0.0)
                        entry_amount_usd = float(position_row.get("entry_amount_usd") or 0.0)
                        
                        if entry_token_amount > 0 and entry_amount_usd > 0:
                            # Get current price
                            price_row = await conn.fetchrow(
                                """
                                SELECT usd_price
                                FROM token_metrics_seconds
                                WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
                                ORDER BY ts DESC
                                LIMIT 1
                                """,
                                        token_id
                                    )
                            current_price = float(price_row["usd_price"] or 0) if price_row and price_row.get("usd_price") else None
                            
                            if current_price and current_price > 0:
                                # Calculate current portfolio value (theoretical, before fees)
                                current_portfolio_value = entry_token_amount * current_price
                                
                                # Get TARGET_RETURN from config
                                target_return = float(getattr(config, 'TARGET_RETURN', 0.13))
                                
                                # Calculate target value: entry + target return (e.g., 20%)
                                # NOTE: Fees (slippage, transaction fees) will be deducted from the sale proceeds
                                # This means the actual profit after fees will be less than target_return
                                # Example: If token grows 20% to $4.80, after 5% slippage + fees we get ~$4.56 (14% real profit)
                                # This is the intended behavior: sell when token grows by target_return%, fees are deducted from proceeds
                                target_value = entry_amount_usd * (1.0 + target_return)
                                
                                # Check if current value >= target value (entry + profit)
                                if current_portfolio_value >= target_value:
                                    # Execute real sell in background task (non-blocking)
                                    # This prevents blocking the analyzer loop during retry logic (up to 30 seconds)
                                    async def _auto_sell_task():
                                        try:
                                            sell_result = await sell_real(token_id)
                                            if sell_result.get("success"):
                                                if self.debug:
                                                    print(
                                                        f"[Analyzer] ✅ Auto-sold token {token_id}: current_value=${current_portfolio_value:.6f} >= "
                                                        f"target=${target_value:.6f} (entry=${entry_amount_usd:.6f} + {target_return*100:.1f}%, fees will be deducted from sale proceeds)"
                                                    )
                                            else:
                                                if self.debug:
                                                    print(f"[Analyzer] ⚠️ Auto-sell failed: token {token_id}, reason={sell_result.get('message', 'Unknown')}")
                                        except Exception as e:
                                            if self.debug:
                                                print(f"[Analyzer] ❌ Auto-sell error: token {token_id}, error={e}")
                                    
                                    # Create background task (non-blocking)
                                    asyncio.create_task(_auto_sell_task())
                except Exception:
                    pass

                # Corridor guard: detect brutal dumps around entry/final checkpoints
                try:
                    if await self._apply_price_corridor_guard(conn, token_id):
                        # Token archived by corridor guard; stop further processing
                        return True
                except Exception:
                    pass

                had_bad_pattern = False
                
                # AUTO-BUY: Check if token should be automatically purchased
                # CRITICAL: Real auto-buy uses ONLY AUTO_BUY_ENTRY_SEC - this is REAL trading!
                # Preview forecast (100s) is SEPARATE - it's just for display, NOT for real trading!
                # 
                # LOGIC: Tokens that survive past AUTO_BUY_ENTRY_SEC (e.g., 120s = 2 minutes) are more likely legitimate.
                # Tokens that get rug-pulled before this threshold (e.g., at 75s) are scams - we avoid them.
                # 
                try:
                    if not getattr(config, "AUTO_BUY_ENABLED", True):
                        # Авто‑покупку временно отключили через конфиг
                        pass
                    else:
                        enabled_wallet_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM wallets WHERE entry_amount_usd IS NOT NULL AND entry_amount_usd > 0"
                        )
                        if enabled_wallet_count:
                            # Determine current age of token in iterations
                            iterations = await get_token_iterations_count(conn, token_id)
                            entry_gate_iter = self.entry_sec
                            final_decision_ready = iterations >= self.holder_momentum_iter
                            
                            momentum_result = None
                            momentum_ok = False
                            if final_decision_ready:
                                momentum_result = await evaluate_holder_momentum(
                                    conn, token_id, self.holder_momentum_iter
                                )
                                momentum_ok = bool(momentum_result.get("ok"))
                            
                            if iterations >= entry_gate_iter:
                                # Check if no open position and get token data
                                auto_buy_check = await conn.fetchrow(
                                    """
                                        WITH no_entry AS (
                                      SELECT NOT EXISTS (
                                        SELECT 1 FROM wallet_history 
                                        WHERE token_id=$1 AND exit_iteration IS NULL
                                      ) AS none
                                    ), tok AS (
                                      SELECT pattern_segment_1,
                                             pattern_segment_2,
                                             pattern_segment_3,
                                             pattern_segment_decision,
                                             num_buys_24h,
                                             num_sells_24h
                                      FROM tokens
                                          WHERE id=$1
                                    )
                                    SELECT 
                                        no_entry.none AS no_entry,
                                        tok.pattern_segment_1,
                                        tok.pattern_segment_2,
                                        tok.pattern_segment_3,
                                        tok.pattern_segment_decision,
                                        tok.num_buys_24h,
                                        tok.num_sells_24h
                                        FROM no_entry, tok
                                    WHERE no_entry.none = TRUE
                                    """,
                                        token_id
                                )
                            else:
                                auto_buy_check = None
                                # if self.debug and iterations < entry_gate_iter:
                                    # print(f"[Analyzer] ⏸ Auto-buy waiting: token {token_id} iterations={iterations} < entry gate {entry_gate_iter}")

                            if auto_buy_check:
                                    segments = [
                                        auto_buy_check.get("pattern_segment_1"),
                                        auto_buy_check.get("pattern_segment_2"),
                                        auto_buy_check.get("pattern_segment_3"),
                                    ]
                                    decision_flag = (auto_buy_check.get("pattern_segment_decision") or "").lower()
                                    total_tx = float(auto_buy_check.get("num_buys_24h") or 0) + float(auto_buy_check.get("num_sells_24h") or 0)
                                    sell_share = (
                                        float(auto_buy_check.get("num_sells_24h") or 0) / total_tx
                                        if total_tx > 0
                                        else 0.0
                                    )
                                    min_tx = float(getattr(config, "MIN_TX_COUNT", 100))
                                    min_sell_share = float(getattr(config, "MIN_SELL_SHARE", 0.2))
                                    latest_price_row = await conn.fetchrow(
                                        """
                                        SELECT usd_price
                                        FROM token_metrics_seconds
                                        WHERE token_id=$1
                                          AND usd_price IS NOT NULL
                                        ORDER BY ts DESC
                                        LIMIT 1
                                        """,
                                        token_id
                                    )
                                    latest_price = float(latest_price_row["usd_price"]) if latest_price_row and latest_price_row.get("usd_price") else 0.0
                                    
                                    segments_ok = self._segments_allow_entry(segments)
                                    basic_conditions = (
                                        decision_flag == "buy"
                                        and segments_ok
                                        and total_tx >= min_tx
                                        and sell_share >= min_sell_share
                                        and latest_price > 0
                                    )
                                    
                                    has_real_trading_final = False
                                    if basic_conditions and final_decision_ready and momentum_ok:
                                        # Final check: Verify real trading (SWAP) before auto-buy
                                        # Use cached result from DB (already checked at segment checkpoints: 35s, 85s, 170s)
                                        has_real_trading_final = await conn.fetchval(
                                            "SELECT has_real_trading FROM tokens WHERE id=$1",
                                            token_id
                                        )
                                        
                                        # If not checked yet (NULL), perform check now
                                        if has_real_trading_final is None:
                                            token_pair_row = await conn.fetchrow(
                                                "SELECT token_pair FROM tokens WHERE id=$1",
                                                token_id
                                            )
                                            token_pair = token_pair_row.get('token_pair') if token_pair_row else None
                                            
                                            if token_pair:
                                                try:
                                                    has_real_trading_final = await check_token_has_real_trading(token_id, token_pair, save_to_db=True)
                                                except Exception as e:
                                                    if self.debug:
                                                        print(f"[Analyzer] ⚠️ Trade type check error for token {token_id}: {e}")
                                                    # On error, be conservative - don't allow buy
                                                    has_real_trading_final = False
                                            else:
                                                has_real_trading_final = False
                                    
                                    final_gate_ok = (
                                        final_decision_ready
                                        and basic_conditions
                                        and momentum_ok
                                        and bool(has_real_trading_final)
                                    )
                                    
                                    if final_decision_ready:
                                        await self._set_final_decision(conn, token_id, final_gate_ok)
                                    
                                    skip_auto_buy = False
                                    gate_reason = None
                                    
                                    if not final_decision_ready:
                                        gate_reason = "final_decision_pending"
                                        skip_auto_buy = True
                                    elif not basic_conditions:
                                        gate_reason = "pattern_or_tx_constraints"
                                        skip_auto_buy = True
                                    elif not momentum_ok:
                                        gate_reason = momentum_result.get("reason", "holder_momentum_failed") if momentum_result else "holder_momentum_failed"
                                        skip_auto_buy = True
                                    elif not final_gate_ok:
                                        gate_reason = "final_gate_failed"
                                        skip_auto_buy = True
                                    
                                    # if skip_auto_buy and self.debug:
                                        # print(f"[Analyzer] ⚠️ Auto-buy blocked for token {token_id}: reason={gate_reason}")
                                    
                                    if not skip_auto_buy and iterations >= self.auto_buy_iter:
                                            # Execute real buy in background task (non-blocking)
                                            # This prevents blocking the analyzer loop during honeypot check and transaction
                                            async def _auto_buy_task():
                                                try:
                                                    buy_result = await buy_real(token_id)
                                                    if buy_result.get("success"):
                                                        if self.debug:
                                                            print(f"[Analyzer] ✅ Auto-buy executed: token {token_id}, iter={iterations}, sell_share={sell_share:.2f}")
                                                    else:
                                                        if self.debug:
                                                            print(f"[Analyzer] ⚠️ Auto-buy failed: token {token_id}, reason={buy_result.get('message', 'Unknown')}")
                                                except Exception as e:
                                                    if self.debug:
                                                        print(f"[Analyzer] ❌ Auto-buy error: token {token_id}, error={e}")
                                            
                                            # Create background task (non-blocking)
                                            asyncio.create_task(_auto_buy_task())
                                    elif not skip_auto_buy and iterations < self.auto_buy_iter and self.debug:
                                        pass
                                        # print(f"[Analyzer] ⏸ Auto-buy waiting buffer for token {token_id}: iter={iterations}, required={self.auto_buy_iter}")
                except Exception:
                    pass

                # Bad pattern guard: archive tokens with bad patterns (no entry) after BAD_PATTERN_HISTORY_READY_ITERS iterations
                # This saves Jupiter API requests on tokens that are clearly not worth tracking
                # Default: 14400 iterations (1 hour) to allow viewing patterns without entry
                try:
                    bad_patterns = ['black_hole', 'flatliner', 'rug_prequel', 'death_spike', 
                                   'smoke_bomb', 'mirage_rise', 'panic_sink']
                    bad_patterns_iter_threshold = int(getattr(config, 'BAD_PATTERN_HISTORY_READY_ITERS', 14400))
                    
                    if bad_patterns_iter_threshold > 0:
                        # Check if token has bad pattern, no entry, and enough iterations
                        # Get iterations count using utility function
                        iterations = await get_token_iterations_count(conn, token_id)
                        
                        if iterations >= bad_patterns_iter_threshold:
                            # Check if token has bad pattern and no entry
                            pattern_check = await conn.fetchrow(
                            """
                                    WITH no_entry AS (
                              SELECT NOT EXISTS (
                                SELECT 1 FROM wallet_history WHERE token_id=$1
                              ) AS none
                            )
                            SELECT 
                                t.pattern_code,
                                no_entry.none AS no_entry
                                    FROM tokens t, no_entry
                            WHERE t.id=$1
                              AND no_entry.none = TRUE
                              AND LOWER(COALESCE(t.pattern_code, '')) = ANY($2)
                            """,
                                token_id, bad_patterns
                        )
                        
                        if pattern_check:
                            # CRITICAL: Check for open position before archiving
                            # Never archive tokens with open positions (user has real money invested)
                            open_pos_check = await conn.fetchrow(
                                """
                                SELECT id FROM wallet_history
                                WHERE token_id=$1 AND exit_iteration IS NULL
                                LIMIT 1
                                """,
                                token_id
                            )
                            
                            if not open_pos_check:
                                if self.debug:
                                    print(f"[Analyzer] ⚠️ Bad pattern detected for token {token_id} (no entry). Keeping token until cleaner threshold.")
                            else:
                                # Open position exists - DO NOT archive
                                if self.debug:
                                    print(f"[Analyzer] ⚠️ Bad pattern detected for token {token_id} but has open position - NOT archiving")
                            if self.debug:
                                pass
                                # print(f"⚠️  Bad pattern detected: token_id={token_id} pattern={pattern_check['pattern_code']} "
                                #       f"iterations={pattern_check['iterations']} → archived")
                except Exception:
                    pass

                # Archive tokens with pattern_segment_decision = "not" (no entry) after BAD_PATTERN_HISTORY_READY_ITERS iterations
                # This includes tokens with liquidity withdrawal (flat mcap/price) and bad segments
                # Default: 14400 iterations (1 hour) to allow viewing patterns without entry
                try:
                    bad_decision_iter_threshold = int(getattr(config, 'BAD_PATTERN_HISTORY_READY_ITERS', 14400))
                    
                    if bad_decision_iter_threshold > 0:
                        # Check if token has decision = "not", no entry, and enough iterations
                        # Get iterations count using utility function
                        iterations = await get_token_iterations_count(conn, token_id)
                        
                        if iterations >= bad_decision_iter_threshold:
                            # Check if token has decision = "not" and no entry
                            decision_check = await conn.fetchrow(
                                """
                                WITH no_entry AS (
                                  SELECT NOT EXISTS (
                                    SELECT 1 FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL
                                  ) AS none
                                )
                                SELECT 
                                    t.pattern_segment_decision,
                                    no_entry.none AS no_entry
                                FROM tokens t, no_entry
                                WHERE t.id=$1
                                  AND no_entry.none = TRUE
                                  AND LOWER(COALESCE(t.pattern_segment_decision, '')) = 'not'
                                """,
                                token_id
                            )
                            
                            if decision_check:
                                # CRITICAL: Check for open position before archiving
                                # Never archive tokens with open positions (user has real money invested)
                                open_pos_check = await conn.fetchrow(
                                    """
                                    SELECT id FROM wallet_history
                                    WHERE token_id=$1 AND exit_iteration IS NULL
                                    LIMIT 1
                                    """,
                                    token_id
                                )
                                
                                if not open_pos_check:
                                    if self.debug:
                                        print(f"⚠️  Bad decision (NOT) detected: token_id={token_id} iterations={iterations} → keeping token alive until cleaner")
                                else:
                                    # Open position exists - DO NOT archive
                                    if self.debug:
                                        print(f"⚠️  Bad decision (NOT) detected: token_id={token_id} iterations={iterations} → NOT archived (has open position)")
                except Exception:
                    pass

                return True

        except Exception as e:
            import traceback
            # print(f"❌ save_token_data error for token_id {token_id}: {e}")
            # print(f"❌ save_token_data traceback: {traceback.format_exc()}")
            return False

    async def _set_final_decision(self, conn, token_id: int, is_buy: bool) -> None:
        """Persist final auto-decision (buy/not) to tokens.pattern_segment_decision."""
        decision = 'buy' if is_buy else 'not'
        try:
            await conn.execute(
                """
                UPDATE tokens
                SET pattern_segment_decision=$2
                WHERE id=$1 AND COALESCE(pattern_segment_decision, '') <> $2
                """,
                token_id, decision
            )
        except Exception:
            pass

    def _get_corridor_windows(self) -> List[Dict[str, Any]]:
        if not getattr(config, 'PRICE_CORRIDOR_GUARD_ENABLED', False):
            return []

        prefix = getattr(config, 'PRICE_CORRIDOR_PATTERN_PREFIX', 'corridor_drop')
        windows: List[Dict[str, Any]] = []

        def add_window(enabled: bool, stage: str, start: int, end: int, drop_threshold: float, recovery_min: float):
            if not enabled:
                return
            if start is None or end is None:
                return
            if start <= 0 or end <= start:
                return
            windows.append({
                "stage": stage,
                "start": int(start),
                "end": int(end),
                "drop_threshold": float(drop_threshold),
                "recovery_min": float(recovery_min),
                "label": f"{prefix}_{stage}"
            })

        add_window(
            getattr(config, 'PRICE_CORRIDOR_PRE_ENABLED', False),
            "pre",
            getattr(config, 'PRICE_CORRIDOR_PRE_START', 75),
            getattr(config, 'PRICE_CORRIDOR_PRE_END', 85),
            getattr(config, 'PRICE_CORRIDOR_PRE_DROP_THRESHOLD', 0.18),
            getattr(config, 'PRICE_CORRIDOR_PRE_RECOVERY_MIN', 0.5),
        )
        add_window(
            getattr(config, 'PRICE_CORRIDOR_FINAL_ENABLED', False),
            "final",
            getattr(config, 'PRICE_CORRIDOR_FINAL_START', 115),
            getattr(config, 'PRICE_CORRIDOR_FINAL_END', 125),
            getattr(config, 'PRICE_CORRIDOR_FINAL_DROP_THRESHOLD', 0.20),
            getattr(config, 'PRICE_CORRIDOR_FINAL_RECOVERY_MIN', 0.4),
        )

        return windows

    @staticmethod
    def _calc_window_drop_recovery(prices: List[float], start_iter: int, end_iter: int) -> Optional[List[float]]:
        if not prices:
            return None
        if start_iter is None or end_iter is None:
            return None
        if end_iter <= start_iter:
            return None

        start_idx = max(0, int(start_iter) - 1)
        end_idx = min(len(prices), int(end_iter))
        if end_idx - start_idx <= 0:
            return None

        window_slice = prices[start_idx:end_idx]
        prefix_slice = prices[:start_idx]

        if not window_slice or not prefix_slice:
            return None

        peak = max(prefix_slice)
        trough = min(window_slice)
        if peak <= 0:
            return None

        drop = (peak - trough) / peak
        delta = peak - trough
        recovery = 1.0 if delta <= 1e-9 else (window_slice[-1] - trough) / delta
        return [drop, recovery]

    async def _flag_corridor_drop(self, conn, token_id: int, label: str, stage: str, drop_pct: float, recovery_pct: float) -> None:
        pattern_label = label or getattr(config, 'PRICE_CORRIDOR_PATTERN_PREFIX', 'corridor_drop')
        
        # CRITICAL: Check for open position before archiving
        # Never archive tokens with open positions (user has real money invested)
        open_pos_check = await conn.fetchrow(
            """
            SELECT id FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            LIMIT 1
            """,
            token_id
        )
        
        await conn.execute(
            """
            UPDATE tokens
            SET pattern_code = $2,
                token_updated_at = CURRENT_TIMESTAMP
            WHERE id=$1
            """,
            token_id,
            pattern_label,
        )
        
        # if open_pos_check:
        #     # Open position exists - DO NOT archive
        #     if self.debug:
        #         print(f"[Analyzer] ⚠️ Corridor drop detected for token {token_id} but has open position - NOT archiving")
        # else:
        #     if self.debug:
        #         print(f"[Analyzer] ⚠️ Corridor drop detected for token {token_id} without open position - keeping token for observation")

        # if self.debug:
        #     print(
        #         f"[Analyzer] ⚠️ Corridor guard ({stage}) blocked token {token_id}: "
        #         f"drop={drop_pct:.3f}, recovery={recovery_pct:.3f}"
        #     )

    async def _apply_price_corridor_guard(self, conn, token_id: int) -> bool:
        windows = self._get_corridor_windows()
        if not windows:
            return False

        # CRITICAL: Never archive tokens with open positions
        # Tokens with open positions should only be archived after sale or timeout
        open_position = await conn.fetchrow(
            """
            SELECT 1
            FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            LIMIT 1
            """,
            token_id
        )
        if open_position:
            # Token has open position - do not archive, let it run until sale or timeout
            return False

        max_end = max(win['end'] for win in windows)
        if max_end <= 0:
            return False

        rows = await conn.fetch(
            """
            SELECT usd_price
            FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL
            ORDER BY ts ASC
            LIMIT $2
            """,
            token_id,
            max_end
        )

        prices: List[float] = []
        for row in rows:
            value = row['usd_price']
            if value is None:
                continue
            try:
                prices.append(float(value))
            except (TypeError, ValueError):
                continue

        if not prices:
            return False

        for window in windows:
            if len(prices) < window['end']:
                continue

            drop_data = self._calc_window_drop_recovery(prices, window['start'], window['end'])
            if not drop_data:
                continue

            drop_pct, recovery_pct = drop_data
            if drop_pct >= window['drop_threshold'] and recovery_pct < window['recovery_min']:
                await self._flag_corridor_drop(conn, token_id, window['label'], window['stage'], drop_pct, recovery_pct)
                return True

        return False
    
    async def _scan_loop(self):
        tick = 0
        while self.is_scanning:
            try:
                tick += 1
                # print(f"🔍 Analyzer tick {tick}: starting batch processing...")
                
                self._fallback_left = self._fallback_rps
                tokens = await self.get_tokens_batch()
                if not tokens:
                    # print(f"🔍 Analyzer tick {tick}: no tokens found, sleeping...")
                    await asyncio.sleep(self.scan_interval)
                    continue

                # print(f"🔍 Analyzer tick {tick}: processing {len(tokens)} tokens")
                jupiter_data = await self.get_jupiter_data(tokens)
                if isinstance(jupiter_data, dict) and "error" in jupiter_data:
                    # print(f"🔍 Analyzer tick {tick}: Jupiter API error: {jupiter_data.get('error')}")
                    await asyncio.sleep(self.scan_interval)
                    continue

                # print(f"🔍 Analyzer tick {tick}: received {len(jupiter_data)} responses from Jupiter")
                token_map = {t["token_address"]: t["token_id"] for t in tokens}
                success_count = 0
                
                for token_data in jupiter_data:
                    token_address = token_data.get('id')
                    if token_address in token_map:
                        if await self.save_token_data(token_map[token_address], token_data):
                            success_count += 1
                
                # print(f"🔍 Analyzer tick {tick}: saved {success_count}/{len(tokens)} tokens successfully")

            except Exception as e:
                import traceback
                # print(f"❌ Analyzer tick {tick}: exception: {e}")
                # print(f"❌ Analyzer tick {tick}: traceback: {traceback.format_exc()}")
                pass

            await asyncio.sleep(self.scan_interval)
    
    async def start(self):
        if not self.is_scanning:
            self.is_scanning = True
            asyncio.create_task(self._scan_loop())
            return {"success": True, "message": "Jupiter analyzer started"}
        return {"success": False, "message": "Already running"}
    
    async def stop(self):
        self.is_scanning = False
        return {"success": True, "message": "Jupiter analyzer stopped"}

_instance: Optional[JupiterAnalyzerV3] = None

async def get_analyzer() -> JupiterAnalyzerV3:
    global _instance
    if _instance is None:
        _instance = JupiterAnalyzerV3()
    return _instance


async def refresh_missing_jupiter_data(
    batch_size: int = 100,
    delay_seconds: float = 3.0,
    force_rescan: bool = False
) -> Dict:
    analyzer = JupiterAnalyzerV3()
    
    try:
        await analyzer.ensure_session()
        
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            if force_rescan:
                rows = await conn.fetch("""
                    SELECT id, token_address, token_pair
                    FROM tokens
                    ORDER BY id ASC
                """)
            else:
                rows = await conn.fetch("""
                    SELECT id, token_address, token_pair
                    FROM tokens
                    WHERE token_pair IS NULL
                    ORDER BY id ASC
                """)
        
        tokens = [
            {
                "token_id": row['id'],
                "token_address": row['token_address'],
                "token_pair": row['token_pair']
            }
            for row in rows
        ]
        
        if not tokens:
            return {
                "success": True,
                "total_tokens": 0,
                "processed_tokens": 0,
                "success_count": 0,
                "failed_count": 0
            }     
        
        success_count = 0
        failed_count = 0
        processed_tokens = 0
        
        total_batches = (len(tokens) + batch_size - 1) // batch_size
        
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(tokens))
            batch = tokens[start_idx:end_idx]
            
            jupiter_data = await analyzer.get_jupiter_data(batch)
            if isinstance(jupiter_data, dict) and "error" in jupiter_data:
                failed_count += len(batch)
                processed_tokens += len(batch)
                continue
            
            token_map = {t["token_address"]: t for t in batch}
            batch_success = 0
            
            for token_data in jupiter_data:
                token_address = token_data.get('id')
                if token_address in token_map:
                    token = token_map[token_address]
                    token_id = token["token_id"]
                    
                    if await analyzer.save_token_data(token_id, token_data):
                        batch_success += 1
                        success_count += 1
                    else:
                        failed_count += 1
                    
                    processed_tokens += 1
            
            if batch_idx < total_batches - 1:
                await asyncio.sleep(delay_seconds)
        
        return {
            "success": True,
            "total_tokens": len(tokens),
            "processed_tokens": processed_tokens,
            "success_count": success_count,
            "failed_count": failed_count
        }
        
    finally:
        await analyzer.close()


async def refresh_until_three(
    batch_size: int = 100,
    delay_seconds: float = 3.0,
    max_rounds: int = 3
) -> Dict:
    total_processed = 0
    total_success = 0
    total_failed = 0
    rounds_done = 0

    for round_idx in range(1, max_rounds + 1):
        rounds_done = round_idx
        res = await refresh_missing_jupiter_data(
            batch_size=batch_size,
            delay_seconds=delay_seconds,
            force_rescan=False,
        )
        total_processed += res.get("processed_tokens", 0)
        total_success += res.get("success_count", 0)
        total_failed += res.get("failed_count", 0)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            remaining = await conn.fetchval("SELECT COUNT(*) FROM tokens WHERE token_pair IS NULL")

        if not remaining:
            break

        await asyncio.sleep(delay_seconds)

    return {
        "success": True,
        "rounds_done": rounds_done,
        "total_processed": total_processed,
        "total_success": total_success,
        "total_failed": total_failed,
    }
