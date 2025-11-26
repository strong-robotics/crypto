#!/usr/bin/env python3
"""TCN inference for crypto price forecasting.

Replaces CatBoost with TCN for better trajectory prediction.
"""

import asyncio
import torch
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai.models.tcn import TCNPriceForecaster
from ai.models.registry import get_latest_model
from ai.datasets import load_tcn_data
from ai.config import ENCODER_SEC, HORIZONS, TARGET_RETURN, MODELS_DIR
from _v3_db_pool import get_db_pool


class TCNForecaster:
    """TCN-based price forecaster."""
    
    def __init__(self, model_path: Optional[str] = None, device: Optional[torch.device] = None):
        self.device = device or torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
        self.model = None
        self.model_path = model_path
        self.model_id: Optional[int] = None
        
    async def load_model(self):
        """Load TCN model.

        Order:
        1) If self.model_path is provided, use it
        2) Try to read latest TCN model from DB registry
        3) Fallback: pick newest local file tcn_best_*.pth under MODELS_DIR
        """
        chosen_path: Optional[str] = self.model_path

        # Step 2: DB registry lookup (best-effort)
        if chosen_path is None:
            try:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM ai_models WHERE model_type = 'tcn' ORDER BY trained_on DESC NULLS LAST, id DESC LIMIT 1"
                    )
                    if row and row.get('path'):
                        chosen_path = row['path']
                        try:
                            self.model_id = int(row['id'])
                        except Exception:
                            self.model_id = None
            except Exception as e:
                print(f"⚠️ DB lookup for model failed, falling back to local files: {e}")

        # Step 3: Local fallback
        if chosen_path is None:
            try:
                os.makedirs(MODELS_DIR, exist_ok=True)
                candidates = [
                    os.path.join(MODELS_DIR, f)
                    for f in os.listdir(MODELS_DIR)
                    if f.startswith("tcn_best_") and f.endswith(".pth")
                ]
                candidates.sort(reverse=True)
                if candidates:
                    chosen_path = candidates[0]
            except Exception:
                pass

        # Prefer newest local checkpoint if DB path older
        try:
            os.makedirs(MODELS_DIR, exist_ok=True)
            locals = [
                os.path.join(MODELS_DIR, f)
                for f in os.listdir(MODELS_DIR)
                if f.startswith("tcn_best_") and f.endswith(".pth")
            ]
            if locals:
                latest_local = max(locals, key=lambda p: os.path.getmtime(p))
                if chosen_path is None or os.path.getmtime(latest_local) > os.path.getmtime(chosen_path):
                    chosen_path = latest_local
        except Exception:
            pass

        if chosen_path is None:
            raise ValueError("No TCN model path resolved (DB registry empty and no local tcn_best_*.pth)")

        if not os.path.isabs(chosen_path):
            # If a relative path slipped in via DB, resolve relative to project root or MODELS_DIR
            rel_path = os.path.join(MODELS_DIR, os.path.basename(chosen_path)) if not os.path.exists(chosen_path) else chosen_path
            chosen_path = rel_path

        if not os.path.exists(chosen_path):
            raise FileNotFoundError(f"Model file not found: {chosen_path}")

        # Load model checkpoint
        checkpoint = torch.load(chosen_path, map_location=self.device)
        
        # Create model
        config = checkpoint.get('model_config', {
            'input_size': 4,
            'sequence_length': 15,
            'output_horizon': 300,
            'static_features': 3
        })
        
        self.model = TCNPriceForecaster(**config).to(self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        self.model_path = chosen_path
        self.expected_input_size = config.get('input_size')
        # If model_id still unknown, try to fetch latest TCN model id
        if self.model_id is None:
            try:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT id FROM ai_models WHERE model_type='tcn' ORDER BY trained_on DESC NULLS LAST, id DESC LIMIT 1"
                    )
                    if row:
                        self.model_id = int(row['id'])
            except Exception:
                self.model_id = None
        print(f"✅ TCN model loaded from {self.model_path}")
        print(f"   Device: {self.device}")
        print(f"   Config: {config}")
        
    async def fetch_token_data(self, token_id: int, origin_ts: int) -> Tuple[np.ndarray, np.ndarray]:
        """Fetch token data + aggregated trades for prediction.

        Returns time series features aligned by second (chronological):
        - price, liquidity, mcap, |Δmcap|
        - buys, sells, log1p(buy_usd), log1p(sell_usd), signed_log1p(net_usd), imbalance
        """
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Get last ENCODER_SEC non-null points up to origin_ts; allow gaps and pad if needed
            time_series_query = """
                SELECT ts, usd_price, liquidity, mcap, fdv
                FROM token_metrics_seconds
                WHERE token_id = $1 AND ts <= $2 AND usd_price IS NOT NULL AND usd_price > 0
                ORDER BY ts DESC
                LIMIT $3
            """
            rows_desc = await conn.fetch(time_series_query, token_id, origin_ts, ENCODER_SEC)
            if not rows_desc:
                raise ValueError(f"Insufficient data for token {token_id}")
            rows = list(reversed(rows_desc))

            ts_list = [int(r['ts']) for r in rows]
            prices = np.array([float(r['usd_price']) for r in rows], dtype=np.float32)
            liquidity = np.array([float(r['liquidity'] or 0) for r in rows], dtype=np.float32)
            mcap = np.array([float(r['mcap'] or 0) for r in rows], dtype=np.float32)
            fdv = np.array([float(r['fdv'] or 0) for r in rows], dtype=np.float32)

            # |Δmcap|
            volume_mcap = np.abs(np.diff(mcap, prepend=mcap[0]))

            # Aggregate trades per second for the same window
            trades_rows = await conn.fetch(
                """
                SELECT timestamp AS ts,
                       SUM(CASE WHEN direction='buy'  THEN 1 ELSE 0 END) AS buys,
                       SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END) AS sells,
                       SUM(CASE WHEN direction='buy'  THEN COALESCE(NULLIF(amount_usd,'')::double precision,
                                                                     COALESCE(NULLIF(token_price_usd,'')::double precision,0) * COALESCE(amount_tokens,0))
                                ELSE 0 END) AS buy_usd,
                       SUM(CASE WHEN direction='sell' THEN COALESCE(NULLIF(amount_usd,'')::double precision,
                                                                     COALESCE(NULLIF(token_price_usd,'')::double precision,0) * COALESCE(amount_tokens,0))
                                ELSE 0 END) AS sell_usd,
                       AVG(NULLIF(token_price_usd,'')::double precision) AS avg_price_usd,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(token_price_usd,'')::double precision) AS med_price_usd
                FROM trades
                WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                GROUP BY timestamp
                ORDER BY ts ASC
                """,
                token_id, int(ts_list[0]), int(ts_list[-1])
            )
            tmap = {int(r['ts']): r for r in trades_rows}
            buys = np.array([float((tmap.get(ts)['buys'] if tmap.get(ts) else 0)) for ts in ts_list], dtype=np.float32)
            sells = np.array([float((tmap.get(ts)['sells'] if tmap.get(ts) else 0)) for ts in ts_list], dtype=np.float32)
            buy_usd = np.array([float((tmap.get(ts)['buy_usd'] if tmap.get(ts) else 0)) for ts in ts_list], dtype=np.float32)
            sell_usd = np.array([float((tmap.get(ts)['sell_usd'] if tmap.get(ts) else 0)) for ts in ts_list], dtype=np.float32)
            avg_price_usd = np.array([float((tmap.get(ts)['avg_price_usd'] if tmap.get(ts) and tmap.get(ts)['avg_price_usd'] is not None else 0)) for ts in ts_list], dtype=np.float32)
            med_price_usd = np.array([float((tmap.get(ts)['med_price_usd'] if tmap.get(ts) and tmap.get(ts)['med_price_usd'] is not None else 0)) for ts in ts_list], dtype=np.float32)
            net_usd = buy_usd - sell_usd
            trades_total = buys + sells
            with np.errstate(divide='ignore', invalid='ignore'):
                imbalance = np.where(trades_total > 0, (buys - sells) / trades_total, 0.0).astype(np.float32)
            buy_usd_log = np.log1p(buy_usd)
            sell_usd_log = np.log1p(sell_usd)
            net_usd_slog = np.sign(net_usd) * np.log1p(np.abs(net_usd))

            # Compose features (metrics + trades)
            feats = [
                prices,
                liquidity,
                mcap,
                volume_mcap,
                buys,
                sells,
                trades_total,
                buy_usd_log,
                sell_usd_log,
                net_usd_slog,
                imbalance,
                avg_price_usd,
                med_price_usd,
            ]
            time_series = np.stack(feats, axis=0)
            # Left-pad to ENCODER_SEC if fewer points
            if time_series.shape[1] < ENCODER_SEC:
                pad_len = ENCODER_SEC - time_series.shape[1]
                pad_block = np.tile(time_series[:, :1], (1, pad_len))
                time_series = np.concatenate([pad_block, time_series], axis=1)
            
            # Get static features
            static_query = """
                SELECT holder_count, organic_score, 
                       EXTRACT(EPOCH FROM (NOW() - created_at)) as age_seconds,
                       circ_supply, total_supply,
                       top_holders_percentage, dev_balance_percentage,
                       blockaid_rugpull, mint_authority_disabled, freeze_authority_disabled
                FROM tokens
                WHERE id = $1
            """
            
            static_row = await conn.fetchrow(static_query, token_id)
            if not static_row:
                raise ValueError(f"Token {token_id} not found")
            
            circ = float(static_row['circ_supply'] or 0)
            total = float(static_row['total_supply'] or 0)
            circ_ratio = (circ / total) if total and total > 0 else 0.0
            static_features = np.array([
                float(static_row['holder_count'] or 0),
                float(static_row['organic_score'] or 0),
                float(static_row['age_seconds'] or 0),
                circ_ratio,
                float(static_row['top_holders_percentage'] or 0),
                float(static_row['dev_balance_percentage'] or 0),
                1.0 if bool(static_row['blockaid_rugpull']) else 0.0,
                1.0 if bool(static_row['mint_authority_disabled']) else 0.0,
                1.0 if bool(static_row['freeze_authority_disabled']) else 0.0,
            ])
            # Align to model input size (slice/pad)
            expected = getattr(self, 'expected_input_size', None)
            if expected is None and self.model is not None:
                expected = getattr(self.model, 'input_size', None)
            if expected is not None:
                f = time_series.shape[0]
                if f > expected:
                    time_series = time_series[:expected, :]
                elif f < expected:
                    pad = np.zeros((expected - f, time_series.shape[1]), dtype=time_series.dtype)
                    time_series = np.concatenate([time_series, pad], axis=0)

            # Align static dims to model
            exp_static = getattr(self.model, 'static_features', None)
            if exp_static is not None:
                s = static_features.shape[0]
                if s > exp_static:
                    static_features = static_features[:exp_static]
                elif s < exp_static:
                    pad = np.zeros((exp_static - s,), dtype=static_features.dtype)
                    static_features = np.concatenate([static_features, pad], axis=0)

            return time_series, static_features
    
    async def predict(self, token_id: int, origin_ts: int) -> Dict[str, float]:
        """Predict price trajectory for a token."""
        if self.model is None:
            await self.load_model()
        
        # Fetch data
        time_series, static_features = await self.fetch_token_data(token_id, origin_ts)
        
        # Convert to tensors
        time_series_tensor = torch.FloatTensor(time_series).unsqueeze(0).to(self.device)  # (1, F, K)
        static_tensor = torch.FloatTensor(static_features).unsqueeze(0).to(self.device)    # (1, 3)
        
        # Predict
        with torch.no_grad():
            price_trajectory, pump_prob, expected_return = self.model(time_series_tensor, static_tensor)
            
            # Convert to numpy
            price_traj = price_trajectory.cpu().numpy()[0]  # (300,)
            pump_prob_val = pump_prob.cpu().numpy()[0][0]   # scalar
            exp_return = expected_return.cpu().numpy()[0][0] # scalar
        
        return {
            'price_trajectory': price_traj,
            'pump_probability': float(pump_prob_val),
            'expected_return': float(exp_return)
        }
    
    async def generate_forecast_data(self, token_id: int, origin_ts: int, 
                                   current_price: float) -> List[Dict[str, float]]:
        """Generate forecast data points for visualization."""
        prediction = await self.predict(token_id, origin_ts)
        
        # Generate forecast points
        forecast_data = []
        price_trajectory = prediction['price_trajectory']
        
        for i, price_change in enumerate(price_trajectory):
            timestamp = origin_ts + i
            forecast_price = current_price * (1 + price_change)
            
            forecast_data.append({
                'timestamp': timestamp,
                'price': forecast_price,
                'price_change': price_change
            })
        
        return forecast_data


async def tcn_forecast_loop():
    """Main TCN forecast loop."""
    print("🧠 Starting TCN forecast loop...")
    
    # Initialize forecaster
    forecaster = TCNForecaster()
    await forecaster.load_model()
    
    # Get candidate tokens
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get tokens with sufficient history
        query = """
            WITH latest AS (
                SELECT token_id, MAX(ts) AS t0 FROM token_metrics_seconds GROUP BY token_id
            )
            SELECT l.token_id, l.t0
            FROM latest l
            WHERE l.t0 > 0
              AND l.t0 >= (SELECT MIN(ts) FROM token_metrics_seconds WHERE token_id = l.token_id) + $1
            ORDER BY l.t0 DESC
        """
        
        rows = await conn.fetch(query, ENCODER_SEC)
        candidates = [{"token_id": r["token_id"], "t0": int(r["t0"])} for r in rows]
        
        print(f"📊 Found {len(candidates)} candidate tokens")
        
        # Process top-N most recent candidates (soft cap to avoid overload)
        cap = min(len(candidates), 40)
        for candidate in candidates[:cap]:
            try:
                token_id = candidate["token_id"]
                origin_ts = candidate["t0"]
                
                print(f"🔮 Predicting for token {token_id} at {origin_ts}")
                
                # Get current price
                price_query = """
                    SELECT usd_price FROM token_metrics_seconds
                    WHERE token_id = $1 AND ts = $2
                """
                price_row = await conn.fetchrow(price_query, token_id, origin_ts)
                if not price_row:
                    continue
                
                current_price = price_row['usd_price']
                
                # Generate prediction
                prediction = await forecaster.predict(token_id, origin_ts)
                # Build absolute price trajectory from relative changes
                price_traj_rel = prediction['price_trajectory']  # np array of returns
                y_abs = (float(current_price) * (1.0 + price_traj_rel)).astype(np.float64).tolist()

                # Compute ETA to target
                eta = None
                target = float(current_price) * (1.0 + TARGET_RETURN)
                for i, p in enumerate(y_abs, 1):
                    if p >= target:
                        eta = i
                        break

                # Upsert into ai_forecasts for frontend consumption
                model_id = forecaster.model_id or 0
                await conn.execute(
                    """
                    INSERT INTO ai_forecasts(
                      token_id, model_id, origin_ts, encoder_len_sec, horizon_sec,
                      score_up, exp_return, y_p50, target_return, eta_to_target_sec, price_now
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (token_id, model_id, origin_ts, horizon_sec) DO UPDATE SET
                      score_up = EXCLUDED.score_up,
                      exp_return = EXCLUDED.exp_return,
                      y_p50 = EXCLUDED.y_p50,
                      eta_to_target_sec = EXCLUDED.eta_to_target_sec,
                      price_now = EXCLUDED.price_now
                    """,
                    int(token_id), int(model_id), int(origin_ts), int(ENCODER_SEC), int(len(y_abs)),
                    float(prediction['pump_probability']), float(prediction['expected_return']), y_abs,
                    float(TARGET_RETURN), eta if eta is not None else None, float(current_price)
                )

                print(f"✅ Token {token_id}: wrote forecast with {len(y_abs)} points, pump={prediction['pump_probability']:.3f}, exp_ret={prediction['expected_return']:.3f}")

            except Exception as e:
                print(f"❌ Error processing token {candidate['token_id']}: {e}")
                continue
    
    print("🎉 TCN forecast loop completed!")


if __name__ == "__main__":
    asyncio.run(tcn_forecast_loop())
