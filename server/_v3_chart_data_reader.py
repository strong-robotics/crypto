import asyncio
import time
import os
from typing import List, Dict, Optional, Set, Tuple
from fastapi import WebSocket
from _v3_db_pool import get_db_pool
from config import config
from statistics import median
import math
import time

class ChartDataReaderV3:
    """
    V3 Reader для chart_data - читає trades з crypto.db та генерує графіки.
    Використовує PostgreSQL з базою crypto_db.
    """
    
    def __init__(self, debug: bool = False):
        self.debug = debug
        
        self.connected_clients: Set[WebSocket] = set()
        self.is_running = False
        self.refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval = config.CHART_REFRESH_INTERVAL
        # Window for SOL-minute bars
        self.chart_seconds = (
            config.CHART_SOL_WINDOW_SECONDS if getattr(config, 'CHART_DATA_MODE', 'usd_second') == 'sol_minute' else 86400
        )
        self.last_trade_counts = {}  # Для відстеження змін по trades
        self.last_metrics_ts = {}    # Для відстеження змін по token_metrics_seconds
        self.last_forecast_ts = {}   # Для відстеження змін по ai_forecasts

        env_show_history = os.getenv("TOKENS_SHOW_HISTORY")
        env_disable_sort = os.getenv("TOKENS_DISABLE_SORT")
        self.show_history = bool(str(env_show_history if env_show_history is not None else getattr(config, 'TOKENS_SHOW_HISTORY', False)).lower() not in ("0", "false", "none", ""))
        self.disable_sort = bool(str(env_disable_sort if env_disable_sort is not None else getattr(config, 'TOKENS_DISABLE_SORT', False)).lower() not in ("0", "false", "none", ""))
    
    async def ensure_connection(self):
        """PostgreSQL - не потрібне (connection pool створюється автоматично)"""
        pass
    
    async def close(self):
        """PostgreSQL - не потрібне (connection pool закривається глобально)"""
        pass

    def _use_history_source(self) -> bool:
        return bool(self.show_history or self.disable_sort)

    def _tokens_table(self) -> str:
        return "tokens_history" if self._use_history_source() else "tokens"

    def _metrics_table(self) -> str:
        return "token_metrics_seconds_history" if self._use_history_source() else "token_metrics_seconds"

    def _trades_table(self) -> str:
        return "trades_history" if self._use_history_source() else "trades"
    
    async def get_all_tokens(self) -> List[Dict]:
        """Отримати всі токени з tokens"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            tokens_table = self._tokens_table()
            order_column = "archived_at" if self._use_history_source() else "created_at"
            rows = await conn.fetch(f"""
                SELECT id, token_address, token_pair 
                FROM {tokens_table}
                WHERE token_pair IS NOT NULL AND token_pair <> '' AND token_pair <> token_address
                ORDER BY COALESCE({order_column}, token_updated_at, created_at) DESC
            """)
            
            return [
                {
                    "token_id": row["id"],
                    "token_address": row["token_address"],
                    "token_pair": row["token_pair"]
                }
                for row in rows
            ]

    async def _get_latest_forecast_p50(self, token_id: int) -> List[float]:
        """Прочитати останній прогноз p50 з ai_forecasts (медіанна жовта лінія).

        Перевага: TCN, якщо доступний; інакше — будь‑який останній.
        """
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            try:
                # First, try the latest TCN forecast
                row = await conn.fetchrow(
                    """
                    SELECT af.y_p50
                    FROM ai_forecasts af
                    JOIN ai_models m ON m.id = af.model_id
                    WHERE af.token_id = $1 AND m.model_type = 'tcn'
                    ORDER BY af.origin_ts DESC, af.horizon_sec DESC
                    LIMIT 1
                    """,
                    token_id,
                )
                if not row:
                    # Fallback to any latest forecast
                    row = await conn.fetchrow(
                        """
                        SELECT y_p50
                        FROM ai_forecasts
                        WHERE token_id = $1
                        ORDER BY origin_ts DESC, horizon_sec DESC
                        LIMIT 1
                        """,
                        token_id,
                    )
                if not row:
                    return []
                arr = row["y_p50"]
                if not arr:
                    return []
                # y_p50 is a Postgres array → python list
                out: List[float] = []
                for v in arr:
                    try:
                        out.append(float(v))
                    except Exception:
                        pass
                
                
                return out
            except Exception:
                return []

    async def _get_latest_forecast_full(self, token_id: int) -> Dict:
        """Return latest forecast fields for token: y_p50, score_up, price_now.

        Prefers TCN model; falls back to any latest forecast.
        """
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Prefer TCN
            row = await conn.fetchrow(
                """
                SELECT af.y_p50, af.score_up, af.price_now
                FROM ai_forecasts af
                JOIN ai_models m ON m.id = af.model_id
                WHERE af.token_id = $1 AND m.model_type = 'tcn'
                ORDER BY af.origin_ts DESC, af.horizon_sec DESC
                LIMIT 1
                """,
                token_id,
            )
            if not row:
                row = await conn.fetchrow(
                    """
                    SELECT y_p50, score_up, price_now
                    FROM ai_forecasts
                    WHERE token_id = $1
                    ORDER BY origin_ts DESC, horizon_sec DESC
                    LIMIT 1
                    """,
                    token_id,
                )
            if not row:
                return {"y_p50": [], "score_up": None, "price_now": None}
            out: Dict = {
                "y_p50": row["y_p50"] or [],
                "score_up": float(row["score_up"]) if row["score_up"] is not None else None,
                "price_now": float(row["price_now"]) if row["price_now"] is not None else None,
            }
            return out

    async def _get_early_window_prices(self, token_id: int, length: int = 30) -> List[float]:
        """Fetch earliest 'length' seconds of usd_price for a token (non-null, >0)."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            metrics_table = self._metrics_table()
            rows = await conn.fetch(
                f"""
                SELECT usd_price
                FROM {metrics_table}
                WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
                ORDER BY ts ASC
                LIMIT $2
                """,
                token_id,
                length,
            )
            if not rows or len(rows) < length:
                return []
            try:
                return [float(r["usd_price"]) for r in rows]
            except Exception:
                return []

    def _z_normalize(self, arr: List[float]) -> List[float]:
        if not arr:
            return []
        x = [float(a) for a in arr]
        m = sum(x) / len(x)
        v = sum((a - m) ** 2 for a in x) / max(1, len(x))
        s = math.sqrt(v) if v > 0 else 1.0
        return [(a - m) / s for a in x]

    def _cosine_sim(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        num = sum(x * y for x, y in zip(a, b))
        da = math.sqrt(sum(x * x for x in a))
        db = math.sqrt(sum(y * y for y in b))
        if da == 0 or db == 0:
            return 0.0
        return max(-1.0, min(1.0, num / (da * db)))

    _shape_cache: Dict[str, any] = {}

    async def _get_good_shape_library(self, refresh_sec: int = 300, max_refs: int = 150) -> List[List[float]]:
        """Build/cached library of z-normalized 30s shapes from top-tier patterns."""
        now = time.time()
        cache = self._shape_cache.get("lib")
        if cache and (now - cache.get("ts", 0)) < refresh_sec:
            return cache.get("refs", [])

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT tp.token_id
                FROM ai_token_patterns tp
                JOIN ai_patterns p ON p.id = tp.pattern_id
                WHERE p.tier = 'top'
                ORDER BY tp.created_at DESC
                LIMIT $1
                """,
                max_refs,
            )
        refs: List[List[float]] = []
        for r in rows:
            tid = int(r["token_id"])
            win = await self._get_early_window_prices(tid, 30)
            if len(win) == 30:
                refs.append(self._z_normalize(win))
        self._shape_cache["lib"] = {"ts": now, "refs": refs}
        return refs

    async def _shape_similarity(self, token_id: int) -> float:
        """Cosine similarity of token's first 30s price shape to top-tier library."""
        win = await self._get_early_window_prices(token_id, 30)
        if len(win) < 30:
            return 0.0
        cur = self._z_normalize(win)
        refs = await self._get_good_shape_library()
        if not refs:
            return 0.0
        return max(self._cosine_sim(cur, ref) for ref in refs)

    async def _pattern_prior(self, token_id: int) -> float:
        """Return normalized pattern prior in [-1,1] based on ai_token_patterns/ai_patterns score."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.score
                FROM ai_token_patterns tp
                JOIN ai_patterns p ON p.id = tp.pattern_id
                WHERE tp.token_id = $1
                ORDER BY tp.created_at DESC
                LIMIT 1
                """,
                token_id,
            )
        if not row or row["score"] is None:
            return 0.0
        try:
            sc = float(row["score"])  # typically 0..100
            # Map [0,100] -> [-1,1]
            return max(-1.0, min(1.0, (sc - 50.0) / 50.0))
        except Exception:
            return 0.0

    async def _get_latest_metrics_row(self, token_id: int) -> Optional[Dict]:
        """Return latest non-null metrics row (usd_price/liquidity/mcap) for token."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            metrics_table = self._metrics_table()
            row = await conn.fetchrow(
                f"""
                SELECT ts, usd_price, liquidity, mcap, fdv
                FROM {metrics_table}
                WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
                ORDER BY ts DESC LIMIT 1
                """,
                token_id,
            )
            return dict(row) if row else None

    async def _get_token_flags(self, token_id: int) -> Dict:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tokens_table = self._tokens_table()
            row = await conn.fetchrow(
                f"""
                SELECT blockaid_rugpull, mint_authority_disabled, freeze_authority_disabled
                FROM {tokens_table} WHERE id=$1
                """,
                token_id,
            )
            if not row:
                return {"rugpull": None, "mint_disabled": None, "freeze_disabled": None}
            return {
                "rugpull": bool(row["blockaid_rugpull"]) if row["blockaid_rugpull"] is not None else None,
                "mint_disabled": bool(row["mint_authority_disabled"]) if row["mint_authority_disabled"] is not None else None,
                "freeze_disabled": bool(row["freeze_authority_disabled"]) if row["freeze_authority_disabled"] is not None else None,
            }

    async def _veto_rules(self, token_id: int) -> Dict:
        """Hard safety checks before model decision."""
        latest = await self._get_latest_metrics_row(token_id)
        if not latest:
            return {"ok": False, "reason": "no-metrics"}
        liq = float(latest.get("liquidity") or 0.0)
        ts_end = int(latest.get("ts") or 0)
        if liq <= 0:
            return {"ok": False, "reason": "no-liquidity"}
        # trades in last 30s
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            cnt = await conn.fetchval(
                f"SELECT COUNT(*) FROM {trades_table} WHERE token_id=$1 AND timestamp BETWEEN $2 AND $3",
                token_id, ts_end - 29, ts_end,
            )
        if not cnt or int(cnt) <= 0:
            return {"ok": False, "reason": "no-trades-30s"}
        flags = await self._get_token_flags(token_id)
        if flags.get("rugpull") is True:
            return {"ok": False, "reason": "rugpull"}
        if flags.get("mint_disabled") is False or flags.get("freeze_disabled") is False:
            return {"ok": False, "reason": "mint/freeze-enabled"}
        return {"ok": True}

    def _compute_entry_exit_plan(self,
                                 chart: List[float],
                                 forecast: List[float],
                                 score_up: Optional[float] = None,
                                 *,
                                 invest_usd: float = None,
                                 aim_usd: float = 6.5,
                                 entry_window: tuple = (30, 30),  # Рішення на 30-й секунді
                                 max_hold_sec: int = 120,
                                 min_confidence: float = 0.5,
                                 max_drawdown: float = 0.15) -> Dict:
        """Pick entry in window and earliest exit that reaches target.

        Returns: {
          decision: 'enter'|'skip', reason?, entry_sec, exit_sec, eta_sec, confidence?, drawdown?
        }
        """
        if invest_usd is None:
            try:
                invest_usd = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
            except Exception:
                invest_usd = 5.0
        ratio = (aim_usd / invest_usd) if invest_usd > 0 else 1.3
        # Must have forecast to enter
        if not forecast or len(forecast) < entry_window[0]:
            return {"decision": "skip", "reason": "no-forecast", "entry_sec": entry_window[0], "exit_sec": None}
        # Confidence gate (if available)
        if score_up is not None and score_up < min_confidence:
            return {"decision": "skip", "reason": "low-confidence", "entry_sec": entry_window[0], "exit_sec": None, "confidence": score_up}

        best = None  # (eta, drawdown, entry_sec, exit_sec)
        e0, e1 = int(entry_window[0]), int(entry_window[1])
        e0 = max(1, e0)
        e1 = max(e0, e1)
        for e in range(e0, min(e1 + 1, len(forecast))):
            entry_idx = e - 1
            try:
                entry_val = float(forecast[entry_idx])
            except Exception:
                continue
            if not math.isfinite(entry_val) or entry_val <= 0:
                continue
            target_val = entry_val * ratio
            exit_idx = None
            local_min = entry_val
            for j in range(entry_idx, min(len(forecast), entry_idx + max_hold_sec + 1)):
                v = forecast[j]
                if v is None:
                    continue
                try:
                    vf = float(v)
                except Exception:
                    continue
                if vf < local_min:
                    local_min = vf
                if vf >= target_val:
                    exit_idx = j
                    break
            if exit_idx is None:
                continue
            eta = exit_idx - entry_idx
            drawdown = (local_min / entry_val) - 1.0
            if drawdown < -max_drawdown:
                continue
            cand = (eta, -drawdown, e, exit_idx + 0)  # prefer smaller eta, then smaller drawdown
            if best is None or cand < best:
                best = cand

        if best is None:
            print(f"🔍 DEBUG _compute_entry_exit_plan: no-crossing, e0={e0}, forecast_len={len(forecast) if forecast else 0}")
            return {"decision": "skip", "reason": "no-crossing", "entry_sec": e0, "exit_sec": None, "confidence": score_up}

        eta, neg_dd, entry_sec, exit_idx = best
        dd = -neg_dd
        print(f"🔍 DEBUG _compute_entry_exit_plan: SUCCESS, entry_sec={entry_sec}, exit_sec={exit_idx}, eta={eta}")
        return {
            "decision": "enter",
            "entry_sec": int(entry_sec),
            "exit_sec": int(exit_idx),
            "eta_sec": int(eta),
            "confidence": score_up,
            "drawdown": float(dd),
        }

    async def _get_supply_for_mcap(self, token_id: int) -> float:
        """Pick supply for mcap projection: circ → token_supply → total_supply."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tokens_table = self._tokens_table()
            row = await conn.fetchrow(
                f"""
                SELECT circ_supply, token_supply, total_supply
                FROM {tokens_table} WHERE id = $1
                """,
                token_id,
            )
            if not row:
                return 0.0
            for key in ("circ_supply", "token_supply", "total_supply"):
                try:
                    v = float(row[key]) if row[key] is not None else 0.0
                except Exception:
                    v = 0.0
                if v and v > 0:
                    return v
            return 0.0

    async def _adjust_forecast_for_mode(self, token_id: int, series: List[float]) -> List[float]:
        """Привести прогноз до одиниць поточного режиму та вирівняти першу точку.

        Правило: перша жовта точка має точно дорівнювати останній синій точці
        у відповідному режимі. Для цього масштабуємо прогноз:
            scale = last_value / max(series[0], eps)
            out = [x * scale for x in series]

        Для режиму mcap_series last_value = останній token_metrics_seconds.mcap.
        Якщо він відсутній — фолбек через співвідношення (mcap/usd_price) або supply.
        """
        if not series:
            return []
        mode = str(getattr(config, 'CHART_DATA_MODE', 'usd_second')).lower()

        # Отримуємо референсне останнє значення в одиницях графіка
        target_last: float = 0.0
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            metrics_table = self._metrics_table()
            if mode == 'mcap_series':
                row = await conn.fetchrow(
                    f"SELECT usd_price, mcap FROM {metrics_table} WHERE token_id=$1 ORDER BY ts DESC LIMIT 1",
                    token_id,
                )
                if row and row['mcap'] and float(row['mcap']) > 0:
                    target_last = float(row['mcap'])
                elif row and row['usd_price'] and float(row['usd_price']) > 0:
                    # fallback: ratio mcap/price за попередньою секундою
                    prev = await conn.fetchrow(
                        f"SELECT usd_price, mcap FROM {metrics_table} WHERE token_id=$1 AND mcap IS NOT NULL ORDER BY ts DESC LIMIT 1 OFFSET 1",
                        token_id,
                    )
                    if prev and prev['mcap'] and prev['usd_price'] and float(prev['usd_price']) > 0:
                        ratio = float(prev['mcap']) / float(prev['usd_price'])
                        target_last = float(row['usd_price']) * ratio
                if target_last <= 0:
                    # фінальний фолбек: supply * остання ціна
                    supply = await self._get_supply_for_mcap(token_id)
                    last_price = await conn.fetchval(
                        f"SELECT usd_price FROM {metrics_table} WHERE token_id=$1 ORDER BY ts DESC LIMIT 1",
                        token_id,
                    ) or 0.0
                    if supply and last_price:
                        target_last = float(supply) * float(last_price)
            else:
                # usd_second/dex_usd – остання ціна
                last_price = await conn.fetchval(
                    f"SELECT usd_price FROM {metrics_table} WHERE token_id=$1 ORDER BY ts DESC LIMIT 1",
                    token_id,
                ) or 0.0
                target_last = float(last_price)

        if target_last <= 0:
            return series

        eps = 1e-12
        scale = target_last / max(float(series[0]), eps)
        adjusted = [float(x) * scale for x in series]
        return adjusted
    
    async def get_trades_from_db(self, token_id: int, start_time: int, end_time: int) -> List[Dict]:
        """Отримати trades з БД для конкретного токена в проміжку часу"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            rows = await conn.fetch(f"""
                SELECT timestamp, amount_usd
                FROM {trades_table}
                WHERE token_id = $1 
                  AND timestamp >= $2 
                  AND timestamp <= $3
                ORDER BY timestamp ASC
            """, token_id, start_time, end_time)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "amount_usd": float(row["amount_usd"]) if row["amount_usd"] else 0.0
                }
                for row in rows
            ]
    
    async def get_all_trades_from_db(self, token_id: int) -> List[Dict]:
        """Отримати ВСІ trades з БД для конкретного токена"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            rows = await conn.fetch(f"""
                SELECT timestamp, token_price_usd
                FROM {trades_table}
                WHERE token_id = $1
                ORDER BY timestamp ASC
            """, token_id)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "token_price_usd": float(row["token_price_usd"]) if row["token_price_usd"] else 0.0
                }
                for row in rows
            ]

    async def _get_metrics_seconds(self, token_id: int, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> List[Dict]:
        """Прочитати секунді метрики (usd_price, fdv, mcap) для токена."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            metrics_table = self._metrics_table()
            if start_ts and end_ts and start_ts < end_ts:
                rows = await conn.fetch(
                    f"""
                    SELECT ts, usd_price, liquidity, fdv, mcap
                    FROM {metrics_table}
                    WHERE token_id = $1 AND ts BETWEEN $2 AND $3
                    ORDER BY ts ASC
                    """,
                    token_id,
                    int(start_ts),
                    int(end_ts),
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT ts, usd_price, liquidity, fdv, mcap
                    FROM {metrics_table}
                    WHERE token_id = $1
                    ORDER BY ts ASC
                    """,
                    token_id,
                )
            out: List[Dict] = []
            for r in rows:
                out.append(
                    {
                        "ts": int(r["ts"]),
                        "usd_price": float(r["usd_price"]) if r["usd_price"] is not None else 0.0,
                        "liquidity": float(r["liquidity"]) if r["liquidity"] is not None else 0.0,
                        "fdv": float(r["fdv"]) if r["fdv"] is not None else 0.0,
                        "mcap": float(r["mcap"]) if r["mcap"] is not None else 0.0,
                    }
                )
            return out

    async def get_last_metrics_ts(self, token_id: int) -> int:
        """Повертає останній ts з token_metrics_seconds або 0, якщо немає метрик."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            metrics_table = self._metrics_table()
            ts = await conn.fetchval(
                f"SELECT COALESCE(MAX(ts), 0) FROM {metrics_table} WHERE token_id = $1",
                token_id,
            )
            return int(ts or 0)

    async def get_last_forecast_ts(self, token_id: int) -> int:
        """Повертає останній origin_ts з ai_forecasts або 0, якщо прогнозів немає."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            ts = await conn.fetchval(
                "SELECT COALESCE(MAX(origin_ts), 0) FROM ai_forecasts WHERE token_id = $1",
                token_id,
            )
            return int(ts or 0)

    async def _get_trades_in_window(self, token_id: int, start_ts: int, end_ts: int) -> List[Dict]:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            rows = await conn.fetch(
                f"""
                SELECT timestamp, token_price_usd
                FROM {trades_table}
                WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                ORDER BY timestamp ASC
                """,
                token_id,
                int(start_ts),
                int(end_ts),
            )
            return [
                {
                    "timestamp": int(r["timestamp"]),
                    "token_price_usd": float(r["token_price_usd"]) if r["token_price_usd"] is not None else 0.0,
                }
                for r in rows
            ]

    async def get_trades_for_sol_bars(self, token_id: int, start_time: int, end_time: int) -> List[Dict]:
        """Отримати trades для SOL-барів у вікні часу (включно з direction)."""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            rows = await conn.fetch(
                f"""
                SELECT timestamp, direction, amount_tokens, amount_sol
                FROM {trades_table}
                WHERE token_id = $1
                  AND timestamp >= $2
                  AND timestamp <= $3
                ORDER BY timestamp ASC
                """,
                token_id,
                start_time,
                end_time,
            )
            out = []
            for r in rows:
                out.append({
                    'timestamp': int(r['timestamp']),
                    'direction': r['direction'],
                    'amount_tokens': float(r['amount_tokens']) if r['amount_tokens'] is not None else 0.0,
                    'amount_sol': float(r['amount_sol']) if r['amount_sol'] is not None else 0.0,
                })
            return out
    
    async def get_trade_count(self, token_id: int) -> int:
        """Отримати кількість trades для токена"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            trades_table = self._trades_table()
            count = await conn.fetchval(f"""
                SELECT COUNT(*) 
                FROM {trades_table} 
                WHERE token_id = $1
            """, token_id)
            
            return count or 0
    
    async def generate_chart_data_usd_second(self, token_id: int) -> Optional[List[float]]:
        """Генерує chart_data (USD/секунда) з trades для конкретного токена"""
        try:
            # Отримуємо ВСІ trades для токена
            trades = await self.get_all_trades_from_db(token_id)
            
            # if self.debug:
                # print(f"🔍 generate_chart_data_usd_second for token_id={token_id}: {len(trades) if trades else 0} trades")
            
            if not trades:
                # Повертаємо порожній масив замість None
                # Це дозволить фронтенду знати, що токен є, але trades немає
                return []
            
            # Групуємо trades по секундах
            trades_by_second = {}
            for trade in trades:
                second = trade['timestamp']
                price = trade['token_price_usd']
                
                if second not in trades_by_second:
                    trades_by_second[second] = []
                
                if price > 0:  # Ігноруємо нульові ціни
                    trades_by_second[second].append(price)
            
            # Формуємо chart_data з усіх секунд
            chart_data = []
            prev_price = None
            
            for second in sorted(trades_by_second.keys()):
                prices = trades_by_second[second]
                # Пропускаємо секунди без цін або використовуємо попередню ціну
                if len(prices) == 0:
                    if prev_price is not None:
                        chart_data.append(prev_price)
                    continue
                
                avg_price = sum(prices) / len(prices)
                chart_data.append(round(avg_price, 10))
                prev_price = round(avg_price, 10)
            
            # if self.debug and chart_data:
                # print(f"🔍 Generated chart_data for token_id={token_id}: {len(chart_data)} points, first={chart_data[0]:.6f}, last={chart_data[-1]:.6f}")
            
            return chart_data
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating chart for token_id {token_id}: {e}")
            return []

    async def generate_chart_data_dex_usd(self, token_id: int) -> List[float]:
        """Dex-подібна USD/сек серія на базі token_metrics_seconds + median trades."""
        try:
            metrics = await self._get_metrics_seconds(token_id)
            if not metrics:
                return []
            start_ts, end_ts = metrics[0]["ts"], metrics[-1]["ts"]
            trades = await self._get_trades_in_window(token_id, start_ts, end_ts)
            by_sec: Dict[int, List[float]] = {}
            for t in trades:
                p = t.get("token_price_usd", 0.0) or 0.0
                if p > 0:
                    ts = int(t["timestamp"])
                    by_sec.setdefault(ts, []).append(p)

            series: List[float] = []
            prev: Optional[float] = None
            for row in metrics:
                ts = row["ts"]
                usd = row.get("usd_price", 0.0) or 0.0
                fdv = row.get("fdv", 0.0) or 0.0
                mcap = row.get("mcap", 0.0) or 0.0
                if fdv > 0 and mcap > 0 and usd > 0:
                    price = (mcap / fdv) * usd
                else:
                    price = usd
                if ts in by_sec and by_sec[ts]:
                    med = median(by_sec[ts])
                    price = 0.7 * price + 0.3 * med
                if (price is None or price <= 0) and prev is not None:
                    price = prev
                prev = price
                series.append(round(float(price or 0.0), 10))
            return series
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating DEX-USD series for token_id {token_id}: {e}")
            return []

    # ============================= SOL minute bars =============================
    def _percentile(self, values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        if pct <= 0:
            return min(values)
        if pct >= 100:
            return max(values)
        vs = sorted(values)
        k = (len(vs) - 1) * (pct / 100.0)
        f = int(k)
        c = min(f + 1, len(vs) - 1)
        if f == c:
            return vs[int(k)]
        d0 = vs[f] * (c - k)
        d1 = vs[c] * (k - f)
        return d0 + d1

    async def generate_chart_data_sol_series(self, token_id: int) -> List[float]:
        """Генерує SOL-деноміновану серію з хвилинних барів (VWAP або close) з робастними фільтрами."""
        try:
            end_time = int(time.time())
            start_time = end_time - int(getattr(config, 'CHART_SOL_WINDOW_SECONDS', 86400))
            raw_trades = await self.get_trades_for_sol_bars(token_id, start_time, end_time)
            if not raw_trades:
                return []

            # Групуємо по хвилинах, відкидаємо withdraw
            by_min: Dict[int, List[Dict]] = {}
            for tr in raw_trades:
                if tr.get('direction') == 'withdraw':
                    continue
                tok = tr.get('amount_tokens') or 0.0
                sol = tr.get('amount_sol') or 0.0
                if tok <= 0 or sol <= 0:
                    continue
                price_sol = sol / tok
                m = (int(tr['timestamp']) // 60) * 60
                by_min.setdefault(m, []).append({'p': price_sol, 'tok': tok, 'sol': sol})

            if not by_min:
                return []

            drop_pct = float(getattr(config, 'CHART_SOL_DROP_PERCENTILE', 0.0) or 0.0)
            weight_by = str(getattr(config, 'CHART_SOL_VWAP_WEIGHT_BY', 'tokens') or 'tokens').lower()
            iqr_k = getattr(config, 'CHART_SOL_IQR_K', None)
            series_value = str(getattr(config, 'CHART_SOL_SERIES_VALUE', 'vwap')).lower()
            ffill = bool(getattr(config, 'CHART_SOL_FORWARD_FILL', False))

            bars: Dict[int, Dict] = {}
            for m, arr in by_min.items():
                items = list(arr)
                # volume percentile
                if drop_pct > 0 and items:
                    vols = [x['tok'] if weight_by == 'tokens' else x['sol'] for x in items]
                    cut = self._percentile(vols, drop_pct)
                    items = [x for x in items if (x['tok'] if weight_by == 'tokens' else x['sol']) >= cut]
                # IQR on price
                if iqr_k is not None and items:
                    prices = [x['p'] for x in items]
                    q1 = self._percentile(prices, 25)
                    q3 = self._percentile(prices, 75)
                    iqr = q3 - q1
                    lo = q1 - float(iqr_k) * iqr
                    hi = q3 + float(iqr_k) * iqr
                    items = [x for x in items if lo <= x['p'] <= hi]
                if not items:
                    continue
                # compute bar
                prices = [x['p'] for x in items]
                o = prices[0]
                c = prices[-1]
                h = max(prices)
                l = min(prices)
                vtok = sum(x['tok'] for x in items)
                vsol = sum(x['sol'] for x in items)
                if weight_by == 'sol':
                    den = vsol
                    num = sum(x['p'] * x['sol'] for x in items)
                else:
                    den = vtok
                    num = sum(x['p'] * x['tok'] for x in items)
                vwap = (num / den) if den > 0 else c
                val = vwap if series_value == 'vwap' else c
                bars[m] = {'val': float(val), 'c': float(c)}

            if not bars:
                return []

            # Build series: chronological values, optionally forward-fill gaps
            out: List[float] = []
            start_m = (start_time // 60) * 60
            end_m = (end_time // 60) * 60
            last_val: Optional[float] = None
            m = start_m
            while m <= end_m:
                if m in bars:
                    v = bars[m]['val']
                    out.append(v)
                    last_val = v
                else:
                    if ffill and last_val is not None:
                        out.append(last_val)
                m += 60
            if not out:
                # fallback to sorted bars
                out = [bars[k]['val'] for k in sorted(bars.keys())]
            return out
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating SOL series for token_id {token_id}: {e}")
            return []

    async def generate_chart_data_mcap_series(self, token_id: int) -> List[float]:
        """Генерує chart_data з market cap (mcap) для конкретного токена з token_metrics_seconds.
        Ось X - час (timestamp), Ось Y - market cap."""
        try:
            # Отримуємо всі метрики для токена
            metrics = await self._get_metrics_seconds(token_id)
            
            if not metrics:
                if self.debug:
                    print(f"⚠️ No metrics found for token_id={token_id}")
                return []
            
            # Формуємо серію market cap по часу
            mcap_series = []
            for metric in metrics:
                mcap = metric.get('mcap', 0.0) or 0.0
                if mcap > 0:  # Ігноруємо нульові значення
                    mcap_series.append(round(float(mcap), 2))
            
            # if self.debug and mcap_series:
                # print(f"📊 Generated mcap series for token_id={token_id}: {len(mcap_series)} points, range: {min(mcap_series):.2f} - {max(mcap_series):.2f}")
            
            return mcap_series
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating mcap series for token_id {token_id}: {e}")
            return []

    async def generate_chart_data(self, token_id: int) -> Optional[List[float]]:
        """Генерує chart_data з trades для конкретного токена з урахуванням режиму з конфігу.
        Має безпечний fallback: якщо SOL/хв серія порожня — повертаємо USD/секундну."""
        mode = getattr(config, 'CHART_DATA_MODE', 'usd_second')
        if mode == 'liquidity_series':
            # Побудова серії ліквідності за секунди з token_metrics_seconds
            metrics = await self._get_metrics_seconds(token_id)
            if not metrics:
                return []
            series: List[float] = []
            for metric in metrics:
                liq = metric.get('liquidity', 0.0) or 0.0
                if liq > 0:
                    series.append(round(float(liq), 2))
            return series
        if mode == 'mcap_series':
            mcap_series = await self.generate_chart_data_mcap_series(token_id)
            if mcap_series:
                return mcap_series
            # fallback на наявні режими
        if mode == 'dex_usd':
            dex = await self.generate_chart_data_dex_usd(token_id)
            if dex:
                return dex
            # fallback на наявні режими
        if mode == 'sol_minute':
            sol_series = await self.generate_chart_data_sol_series(token_id)
            if sol_series:
                return sol_series
            # Fallback для токенів без недавніх торгів у вікні
            if self.debug:
                print(f"⚠️ SOL-minute empty for token_id={token_id}; falling back to USD/second")
            return await self.generate_chart_data_usd_second(token_id)
        return await self.generate_chart_data_usd_second(token_id)
    
    async def broadcast_to_clients(self, data: Dict):
        """Відправити дані всім підключеним клієнтам"""
        if not self.connected_clients:
            return
        
        disconnected = set()
        for client in self.connected_clients:
            try:
                await client.send_json(data)
            except Exception as e:
                if self.debug:
                    print(f"❌ Error sending to client: {e}")
                disconnected.add(client)
        
        for client in disconnected:
            self.connected_clients.discard(client)
    
    async def add_client(self, websocket: WebSocket):
        """Додати WebSocket клієнта"""
        
        self.connected_clients.add(websocket)
        if self.debug:
            print(f"📊 Chart client connected (total: {len(self.connected_clients)})")
        
        # Відправити initial chart data одразу
        await self.send_initial_chart_data(websocket)
    
    async def send_initial_chart_data(self, websocket: WebSocket):
        """Відправити історичні chart_data при підключенні клієнта"""
        try:
            WATCH_PAIRS = {
                'EEL91mEmnVX7BgTKQanAX4Q3emLX6VojiVS9xDqCzMQM',
                '9Tu2hLQT6eHHP4ytMqKvPXTwmiVzeG69nU7mVuK6pump',
                '3N2BJYxS8NTtxSBVbLZoK5bz6MJu6gKCDJLWKfHBpump',
            }
            tokens = await self.get_all_tokens()
            
            if not tokens:
                if self.debug:
                    print("📊 No tokens found for initial chart data")
                return
            
            if self.debug:
                print(f"📊 Sending initial chart data for {len(tokens)} tokens...")
            
            sent_count = 0
            for token in tokens:
                token_id = token['token_id']
                token_address = token['token_address']
                token_pair = token.get('token_pair')
                
                chart_data = await self.generate_chart_data(token_id)
                fc_full = await self._get_latest_forecast_full(token_id)
                raw_fc = fc_full.get("y_p50", [])
                forecast_p50 = await self._adjust_forecast_for_mode(token_id, raw_fc)
                veto = await self._veto_rules(token_id)
                if veto.get("ok"):
                    plan = self._compute_entry_exit_plan(chart_data or [], forecast_p50 or [], fc_full.get("score_up"))
                    # Enrich with prior/similarity and combined score gate
                    prior = await self._pattern_prior(token_id)
                    sim = await self._shape_similarity(token_id)
                    phit = float(fc_full.get("score_up") or 0.5)
                    # Combine: S = 0.6*p_hit + 0.25*sim + 0.15*prior
                    S = 0.6 * phit + 0.25 * sim + 0.15 * prior
                    plan["prior"] = prior
                    plan["similarity"] = sim
                    plan["score"] = S
                    if plan.get("decision") == "enter":
                        # Apply gate
                        if S < 0.65:
                            plan["decision"] = "skip"
                            plan["reason"] = f"score<{0.65}"
                else:
                    plan = {"decision": "skip", "reason": veto.get("reason"), "entry_sec": 30, "exit_sec": None}
                
                # Логування для налагодження
                if token_id == 2504:  # Токен з скріншота
                    print(f"🔍 DEBUG Token 2504:")
                    print(f"  chart_data length: {len(chart_data) if chart_data else 0}")
                    print(f"  forecast_p50 length: {len(forecast_p50) if forecast_p50 else 0}")
                    print(f"  score_up: {fc_full.get('score_up')}")
                    print(f"  eta_to_target_sec: {fc_full.get('eta_to_target_sec')}")
                    # plan details removed in current flow
                    if forecast_p50 and len(forecast_p50) > 0:
                        print(f"  first 5 forecast values: {forecast_p50[:5]}")
                        print(f"  last 5 forecast values: {forecast_p50[-5:]}")
                
                
                if token_pair in WATCH_PAIRS:
                    print(f"🛰️ INIT charts pair={token_pair} id={token_id} addr={token_address[:8]}.. len={len(chart_data) if chart_data else 0}")
                # Skip empty charts to avoid wiping existing graphs on FE
                if not chart_data:
                    continue
                
                # ✅ Відправляємо лише якщо є дані
                try:
                    # Показуємо прогноз для всіх токенів
                    final_forecast = forecast_p50
                    
                    
                    await websocket.send_json({
                        "token_id": token_address,  # mint address для сумісності
                        "id": token_id,  # INTEGER id для ідентифікації
                        "token_pair": token_pair,
                        "chart_data": chart_data,
                        "forecast_p50": final_forecast,
                        "plan_entry_sec": plan.get("entry_sec"),
                        "plan_exit_sec": plan.get("exit_sec"),
                        "plan_decision": plan.get("decision"),
                        "plan_eta_sec": plan.get("eta_sec"),
                        "plan_confidence": plan.get("confidence"),
                        "plan_drawdown": plan.get("drawdown"),
                        "plan_reason": plan.get("reason"),
                        "plan_prior": plan.get("prior"),
                        "plan_similarity": plan.get("similarity"),
                        "plan_score": plan.get("score"),
                    })
                    sent_count += 1
                    # if token_pair in WATCH_PAIRS:
                    #     print(f"📤 SENT INIT charts pair={token_pair} id={token_id} points={len(chart_data)}")
                    # if token_id == 9:
                    #     print(f"✅ SENT chart for TOKEN ID=9 with {len(chart_data)} points")
                    # if self.debug and len(chart_data) > 0:
                    #     print(f"📈 Sent initial chart for {token_address[:8]}... ({len(chart_data)} points)")
                except Exception as e:
                    if self.debug:
                        print(f"❌ Error sending initial chart for {token_address[:8]}...: {e}")
                    break
            
            if self.debug:
                print(f"✅ Sent {sent_count} initial charts to client")
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error sending initial chart data: {e}")
    
    async def remove_client(self, websocket: WebSocket):
        """Видалити WebSocket клієнта (авто-оновлення не зупиняємо)."""
        self.connected_clients.discard(websocket)
        if self.debug:
            print(f"📊 Chart client disconnected (total: {len(self.connected_clients)})")
    
    async def _auto_refresh_loop(self):
        """Головний цикл - читає trades з БД кожну секунду"""
        if self.debug:
            print("📊 Chart Data Reader V3 started")
        
        loop_count = 0
        while self.is_running:
            loop_count += 1
            try:
                # Перевіряємо чи є підключені клієнти
                if not self.connected_clients:
                    if self.debug and loop_count == 1:
                        print("⚠️  No connected clients, waiting...")
                    await asyncio.sleep(self.refresh_interval)
                    continue
                
                tokens = await self.get_all_tokens()
                
                if self.debug and loop_count == 1:
                    print(f"🔍 ChartReader V3 loop #{loop_count}: Found {len(tokens)} tokens")
                
                if not tokens:
                    await asyncio.sleep(self.refresh_interval)
                    continue
                
                updated_tokens = []
                mode = str(getattr(config, 'CHART_DATA_MODE', 'usd_second')).lower()
                WATCH_PAIRS = {
                    'EEL91mEmnVX7BgTKQanAX4Q3emLX6VojiVS9xDqCzMQM',
                    '9Tu2hLQT6eHHP4ytMqKvPXTwmiVzeG69nU7mVuK6pump',
                    '3N2BJYxS8NTtxSBVbLZoK5bz6MJu6gKCDJLWKfHBpump',
                }
                
                for token in tokens:
                    token_id = token['token_id']
                    token_address = token['token_address']
                    token_pair = token.get('token_pair')
                    
                    # Перевіряємо, чи є нові trades/метрики залежно від режиму
                    current_count = await self.get_trade_count(token_id)
                    last_count = self.last_trade_counts.get(token_id, -1)
                    metrics_ts = 0
                    last_ts = self.last_metrics_ts.get(token_id, 0)
                    fc_ts = 0
                    last_fc_ts = self.last_forecast_ts.get(token_id, 0)

                    should_update = False
                    if mode == 'mcap_series':
                        metrics_ts = await self.get_last_metrics_ts(token_id)
                        should_update = metrics_ts > last_ts
                    elif mode == 'dex_usd':
                        metrics_ts = await self.get_last_metrics_ts(token_id)
                        should_update = (current_count > last_count) or (metrics_ts > last_ts)
                    else:
                        should_update = current_count > last_count

                    # Додатковий тригер оновлення: якщо з'явився/оновився прогноз AI
                    try:
                        fc_ts = await self.get_last_forecast_ts(token_id)
                    except Exception:
                        fc_ts = 0
                    if fc_ts > last_fc_ts:
                        should_update = True

                    chart_data = None
                    if should_update:
                        if mode in ('usd_second', 'sol_minute') and current_count == 0:
                            self.last_trade_counts[token_id] = current_count
                            continue
                        chart_data = await self.generate_chart_data(token_id)
                        # if self.debug:
                            # print(f"🔍 Generated chart for token_id={token_id} ({token_address[:8]}...): {len(chart_data) if chart_data else 0} points")
                            # if chart_data and len(chart_data) > 0:
                                # print(f"   First 3 points: {chart_data[:3]}")
                                # print(f"   Last 3 points: {chart_data[-3:]}")
                        # if token_pair in WATCH_PAIRS:
                            # print(f"🛰️ UPDATE charts pair={token_pair} id={token_id} trades={current_count} last={last_count} len={len(chart_data) if chart_data else 0}")
                        if not chart_data:
                            self.last_trade_counts[token_id] = current_count
                            if metrics_ts:
                                self.last_metrics_ts[token_id] = metrics_ts
                            continue
                        
                        # Attach latest forecast (yellow line) if available
                        fc_full = await self._get_latest_forecast_full(token_id)
                        raw_fc = fc_full.get("y_p50", [])
                        forecast_p50 = await self._adjust_forecast_for_mode(token_id, raw_fc)
                        veto = await self._veto_rules(token_id)
                        if veto.get("ok"):
                            plan = self._compute_entry_exit_plan(chart_data or [], forecast_p50 or [], fc_full.get("score_up"))
                            prior = await self._pattern_prior(token_id)
                            sim = await self._shape_similarity(token_id)
                            phit = float(fc_full.get("score_up") or 0.5)
                            S = 0.6 * phit + 0.25 * sim + 0.15 * prior
                            plan["prior"] = prior
                            plan["similarity"] = sim
                            plan["score"] = S
                            if plan.get("decision") == "enter" and S < 0.65:
                                plan["decision"] = "skip"
                                plan["reason"] = f"score<{0.65}"
                        else:
                            plan = {"decision": "skip", "reason": veto.get("reason"), "entry_sec": 30, "exit_sec": None}

                        # Показуємо прогноз для всіх токенів
                        final_forecast = forecast_p50

                        updated_tokens.append({
                            "token_id": token_address,
                            "id": token_id,
                            "token_pair": token_pair,
                            "chart_data": chart_data,
                            "forecast_p50": final_forecast,
                            "plan_entry_sec": plan.get("entry_sec"),
                            "plan_exit_sec": plan.get("exit_sec"),
                            "plan_decision": plan.get("decision"),
                            "plan_eta_sec": plan.get("eta_sec"),
                            "plan_confidence": plan.get("confidence"),
                            "plan_drawdown": plan.get("drawdown"),
                            "plan_reason": plan.get("reason"),
                            "plan_prior": plan.get("prior"),
                            "plan_similarity": plan.get("similarity"),
                            "plan_score": plan.get("score"),
                        })

                    if self.debug and (last_count >= 0 or last_ts > 0):
                        new_count = current_count - last_count if last_count >= 0 else current_count
                        # print(f"📈 Chart updated for token_id={token_id} ({token_address[:8]}...) - trades={current_count} (+{new_count}), metrics_ts={metrics_ts} (prev {last_ts})")

                    # Оновлюємо лічильники
                    self.last_trade_counts[token_id] = current_count
                    if metrics_ts:
                        self.last_metrics_ts[token_id] = metrics_ts
                    if fc_ts:
                        self.last_forecast_ts[token_id] = fc_ts
                
                # Відправляємо тільки оновлені токени
                if updated_tokens:
                    for token_data in updated_tokens:
                        await self.broadcast_to_clients(token_data)
                    
                    # if self.debug:
                        # print(f"📊 Updated {len(updated_tokens)} tokens with chart data")
                elif self.debug and loop_count == 1:
                    print(f"⚠️  ChartReader V3 loop #{loop_count}: No tokens to update (all counts unchanged)")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.debug:
                    print(f"❌ Chart reader V3 error: {e}")
            
            await asyncio.sleep(self.refresh_interval)
        
        if self.debug:
            print("⏸️ Chart auto-refresh V3 stopped")
    
    async def start_auto_refresh(self):
        """Запустити автоматичне оновлення"""
        if self._use_history_source():
            if self.debug:
                print("📊 History mode – chart auto-refresh disabled")
            return {"success": False, "message": "history_mode"}
        if not self.is_running:
            self.is_running = True
            self.refresh_task = asyncio.create_task(self._auto_refresh_loop())
            if self.debug:
                print("🚀 Chart auto-refresh V3 started")
        return {"success": True, "message": "chart auto-refresh started"}
    
    async def stop_auto_refresh(self):
        """Зупинити автоматичне оновлення"""
        if self.is_running:
            self.is_running = False
            if self.refresh_task:
                self.refresh_task.cancel()
                try:
                    await self.refresh_task
                except asyncio.CancelledError:
                    pass
            # Очищаємо last_trade_counts щоб при наступному старті відправити всі дані
            self.last_trade_counts.clear()
            if self.debug:
                print("⏹️ Chart auto-refresh V3 stopped")
