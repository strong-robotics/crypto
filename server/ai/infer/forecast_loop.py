#!/usr/bin/env python3
"""Baseline inference loop (1 Hz) writing ai_forecasts.

CatBoost не используется. Прогноз строится как продолжение ряда mcap,
первая жёлтая точка = последней синей, запись — в ai_forecasts.
"""

import asyncio
from typing import List, Optional

from _v3_db_pool import get_db_pool
from ai.config import ENCODER_SEC, HORIZONS, TARGET_RETURN, FORECAST_DEBUG
from ai.feature_builder import fetch_window_prices
from ai.infer.tcn_forecast import TCNForecaster

# Глобальна змінна для TCN forecaster
_tcn_forecaster: Optional[TCNForecaster] = None

def _log(msg: str) -> None:
    if FORECAST_DEBUG:
        print(f"[AI-FC] {msg}")


async def _load_models():
    """Завантажуємо TCN модель."""
    global _tcn_forecaster
    if _tcn_forecaster is None:
        _tcn_forecaster = TCNForecaster()
        await _tcn_forecaster.load_model()
        if FORECAST_DEBUG:
            print("✅ TCN модель завантажена для прогнозів")
    return _tcn_forecaster


async def _fetch_candidates(conn) -> List[dict]:
    # AI ПРОГНОЗИ: Всі нові токени з достатньою історією (включаючи історичні)
    q = """
      WITH latest AS (
        SELECT token_id, MAX(ts) AS t0, COUNT(*) as data_points 
        FROM token_metrics_seconds 
        WHERE mcap IS NOT NULL  -- Тільки записи з реальними даними
        GROUP BY token_id
      )
      SELECT l.token_id, l.t0
      FROM latest l
      JOIN tokens t ON t.id = l.token_id
      WHERE l.t0 > 0
        AND l.data_points >= 10  -- Мінімум 10 точок даних
        AND l.t0 >= EXTRACT(EPOCH FROM (NOW() - INTERVAL '7 days'))::bigint  -- За останні 7 днів
    """
    rows = await conn.fetch(q)
    out = [{"token_id": r["token_id"], "t0": int(r["t0"]) } for r in rows]
    _log(f"candidates={len(out)}")
    
    # Тихо обробляємо кандидатів без логів
    
    return out


async def _insert_forecast(conn, model_id: int, token_id: int, t0: int, horizon: int, prob: float, exp_ret: float, price0: float, y_p50: List[float]) -> None:
    # ETA: коли перетнемо +TARGET_RETURN відносно price0
    eta = next((i for i, p in enumerate(y_p50, 1) if p >= price0 * (1 + TARGET_RETURN)), None)
    
    await conn.execute(
        """
        INSERT INTO ai_forecasts(
          token_id, model_id, origin_ts, encoder_len_sec, horizon_sec,
          score_up, exp_return, y_p50, target_return, eta_to_target_sec, price_now
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (token_id, model_id, origin_ts, horizon_sec) DO UPDATE SET
          score_up = EXCLUDED.score_up,
          exp_return = EXCLUDED.exp_return,
          y_p50 = EXCLUDED.y_p50,
          eta_to_target_sec = EXCLUDED.eta_to_target_sec
        """,
        token_id, model_id, t0, ENCODER_SEC, horizon, prob, exp_ret, y_p50, TARGET_RETURN, eta, price0,
    )


async def _ensure_tcn_model(conn) -> int:
    row = await conn.fetchrow(
        "SELECT id FROM ai_models WHERE name='tcn_forecast' ORDER BY trained_on DESC NULLS LAST, id DESC LIMIT 1"
    )
    if row:
        return int(row["id"]) or 0
    row = await conn.fetchrow(
        """
        INSERT INTO ai_models(name, version, model_type, framework, hyperparams,
                              train_window_sec, predict_horizons_sec, trained_on, path, metrics)
        VALUES ('tcn_forecast','v1','tcn','pytorch','{}',$1,$2,now(),NULL,'{}')
        RETURNING id
        """,
        ENCODER_SEC,
        HORIZONS,
    )
    mid = int(row["id"]) if row else 0
    _log(f"registered TCN model id={mid}")
    return mid


async def loop_once() -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        cand = await _fetch_candidates(conn)
        if not cand:
            _log("no candidates; skip tick")
            return
        
        # Завантажуємо TCN модель
        tcn_forecaster = await _load_models()
        model_id = await _ensure_tcn_model(conn)
        
        # ВИПРАВЛЕНО: Додаємо валідацію часу
        import time
        current_ts = int(time.time())
        
        for row in cand:
            # Перевіряємо, що ми не прогнозуємо в майбутньому
            if row["t0"] > current_ts:
                continue
                
            try:
                # Використовуємо TCN для прогнозування
                prediction = await tcn_forecaster.predict(row["token_id"], row["t0"])
                if not prediction:
                    continue
                    
                price_trajectory = prediction.get('price_trajectory', [])
                pump_probability = prediction.get('pump_probability', 0.0)
                expected_return = prediction.get('expected_return', 0.0)
                current_price = prediction.get('current_price', 0.0)
                
                # Для кожного горизонту створюємо прогноз
                for H in HORIZONS:
                    if H <= len(price_trajectory):
                        y_p50 = price_trajectory[:H]
                        await _insert_forecast(conn, model_id, row["token_id"], row["t0"], H, 
                                             pump_probability, expected_return, current_price, y_p50)
                        
                        # Тихо створюємо прогнози без логів
                        eta = next((i for i, p in enumerate(y_p50, 1) if p >= current_price * (1 + TARGET_RETURN)), None)
                                
            except Exception as e:
                _log(f"TCN prediction error token={row['token_id']}: {e}")


async def main_loop() -> None:
    while True:
        try:
            await loop_once()
        except Exception as e:
            _log(f"loop_once error: {e}")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main_loop())
