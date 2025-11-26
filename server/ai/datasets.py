"""TCN Dataset for crypto price forecasting.

Loads time series data from database and creates sequences for TCN training.
"""

import asyncio
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from typing import List, Tuple, Dict, Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _v3_db_pool import get_db_pool
from ai.config import ENCODER_SEC, HORIZONS


class TCNDataset(Dataset):
    """Dataset for TCN price forecasting."""
    
    def __init__(self, 
                 sequences: np.ndarray,  # (N, input_size, sequence_length)
                 static_features: np.ndarray,  # (N, static_features)
                 price_targets: np.ndarray,  # (N, output_horizon)
                 pump_targets: np.ndarray,  # (N, 1)
                 return_targets: np.ndarray):  # (N, 1)
        self.sequences = torch.FloatTensor(sequences)
        self.static_features = torch.FloatTensor(static_features)
        self.price_targets = torch.FloatTensor(price_targets)
        self.pump_targets = torch.FloatTensor(pump_targets)
        self.return_targets = torch.FloatTensor(return_targets)
        
    def __len__(self):
        return len(self.sequences)
    
    def __getitem__(self, idx):
        return {
            'time_series': self.sequences[idx],
            'static': self.static_features[idx],
            'price_target': self.price_targets[idx],
            'pump_target': self.pump_targets[idx],
            'return_target': self.return_targets[idx]
        }


async def load_tcn_data(encoder_sec: int = ENCODER_SEC, 
                       horizon_sec: int = 300,
                       target_return: float = 0.20,
                       exclude_tokens: Optional[List[str]] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Load data for TCN training from database.
    
    Args:
        encoder_sec: History window in seconds (default 15)
        horizon_sec: Forecast horizon in seconds (default 300)
        target_return: Target return for pump classification (default 0.20)
        exclude_tokens: List of token addresses to exclude
        
    Returns:
        sequences: (N, 4, encoder_sec) - time series data
        static_features: (N, 3) - static features
        price_targets: (N, horizon_sec) - price trajectory targets
        pump_targets: (N, 1) - pump classification targets
        return_targets: (N, 1) - return regression targets
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get all tokens except excluded ones
        exclude_condition = ""
        if exclude_tokens:
            placeholders = ",".join([f"${i+1}" for i in range(len(exclude_tokens))])
            exclude_condition = f"AND t.token_address NOT IN ({placeholders})"
        
        # Load token data with metrics and extra static fields
        query = f"""
            SELECT 
                t.id as token_id,
                t.token_address,
                t.holder_count,
                t.organic_score,
                EXTRACT(EPOCH FROM (NOW() - t.created_at)) as age_seconds,
                t.circ_supply,
                t.total_supply,
                t.top_holders_percentage,
                t.dev_balance_percentage,
                t.blockaid_rugpull,
                t.mint_authority_disabled,
                t.freeze_authority_disabled,
                tms.ts,
                tms.usd_price,
                tms.liquidity,
                tms.mcap,
                tms.fdv
            FROM tokens_history t
            LEFT JOIN token_metrics_seconds_history tms ON t.id = tms.token_id
            WHERE tms.usd_price IS NOT NULL 
              AND tms.usd_price > 0
              {exclude_condition}
            ORDER BY t.id, tms.ts
        """
        
        params = exclude_tokens or []
        rows = await conn.fetch(query, *params)
        
        if not rows:
            print("❌ No data found for TCN training")
            return np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
        
        # Convert to DataFrame
        df = pd.DataFrame([dict(r) for r in rows])
        print(f"📊 Loaded {len(df)} records from {df['token_id'].nunique()} tokens")
        
        # Process data for each token (augment with per-second trades aggregates)
        sequences = []
        static_features = []
        price_targets = []
        pump_targets = []
        return_targets = []
        
        for token_id, token_data in df.groupby('token_id'):
            token_data = token_data.sort_values('ts').reset_index(drop=True)
            
            if len(token_data) < encoder_sec + horizon_sec:
                continue  # Not enough data

            # Fetch per-second trades aggregates for this token range
            ts_min = int(token_data['ts'].min())
            ts_max = int(token_data['ts'].max())
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
                                ELSE 0 END) AS sell_usd
                FROM trades
                WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                GROUP BY timestamp
                ORDER BY ts ASC
                """,
                int(token_id), ts_min, ts_max
            )

            if trades_rows:
                tdf = pd.DataFrame([dict(r) for r in trades_rows])
                # Add avg and median trade price per second
                # Re-fetch with avg/median to avoid heavy Python-level calc
                tr2 = await conn.fetch(
                    """
                    SELECT timestamp AS ts,
                           AVG(NULLIF(token_price_usd,'')::double precision) AS avg_price_usd,
                           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULLIF(token_price_usd,'')::double precision) AS med_price_usd
                    FROM trades
                    WHERE token_id = $1 AND timestamp BETWEEN $2 AND $3
                    GROUP BY timestamp
                    ORDER BY ts ASC
                    """,
                    int(token_id), ts_min, ts_max
                )
                if tr2:
                    tdf2 = pd.DataFrame([dict(r) for r in tr2])
                    tdf = tdf.merge(tdf2, on='ts', how='left')
            else:
                tdf = pd.DataFrame(columns=['ts','buys','sells','buy_usd','sell_usd','avg_price_usd','med_price_usd'])

            # Merge trades aggregates into token_data by ts
            token_data = token_data.merge(tdf, on='ts', how='left')
            for col in ['buys','sells','buy_usd','sell_usd','avg_price_usd','med_price_usd']:
                token_data[col] = token_data[col].fillna(0.0)
            token_data['net_usd'] = token_data['buy_usd'] - token_data['sell_usd']
            token_data['trades_total'] = token_data['buys'] + token_data['sells']
            # Imbalance in [-1,1]
            token_data['imbalance'] = token_data.apply(lambda r: ((r['buys'] - r['sells']) / r['trades_total']) if r['trades_total'] > 0 else 0.0, axis=1)
            # Log transforms for USD volumes
            token_data['buy_usd_log'] = np.log1p(token_data['buy_usd'].astype(float))
            token_data['sell_usd_log'] = np.log1p(token_data['sell_usd'].astype(float))
            token_data['net_usd_slog'] = np.sign(token_data['net_usd'].astype(float)) * np.log1p(np.abs(token_data['net_usd'].astype(float)))
            
            # Create sequences
            for i in range(encoder_sec, len(token_data) - horizon_sec):
                # Time series input (price, liquidity, volume, trades)
                hist_data = token_data.iloc[i-encoder_sec:i]
                future_data = token_data.iloc[i:i+horizon_sec]
                
                # Extract features
                prices = hist_data['usd_price'].values
                liquidity = hist_data['liquidity'].values
                mcap = hist_data['mcap'].values
                fdv = hist_data['fdv'].values
                
                # Calculate volume (simplified as mcap change)
                volume = np.abs(np.diff(mcap, prepend=mcap[0]))

                # Trade-derived features aligned per second
                buys = hist_data['buys'].values
                sells = hist_data['sells'].values
                trades_total = hist_data['trades_total'].values
                buy_usd_log = hist_data['buy_usd_log'].values
                sell_usd_log = hist_data['sell_usd_log'].values
                net_usd_slog = hist_data['net_usd_slog'].values
                imbalance = hist_data['imbalance'].values
                avg_price_usd = hist_data['avg_price_usd'].values
                med_price_usd = hist_data['med_price_usd'].values
                
                # Create sequence features (metrics + trades)
                # 13 channels: price, liquidity, mcap, |Δmcap|, buys, sells, trades_total, log(buy_usd), log(sell_usd), signed_log(net_usd), imbalance, avg_trade_price_usd, med_trade_price_usd
                sequence = np.stack([
                    prices,
                    liquidity,
                    mcap,
                    volume,
                    buys,
                    sells,
                    trades_total,
                    buy_usd_log,
                    sell_usd_log,
                    net_usd_slog,
                    imbalance,
                    avg_price_usd,
                    med_price_usd
                ], axis=0)  # (13, encoder_sec)
                
                # Static features (extended)
                r0 = token_data.iloc[0]
                circ = float(r0['circ_supply'] or 0)
                total = float(r0['total_supply'] or 0)
                circ_ratio = (circ / total) if total and total > 0 else 0.0
                static = np.array([
                    float(r0['holder_count'] or 0),
                    float(r0['organic_score'] or 0),
                    float(r0['age_seconds'] or 0),
                    circ_ratio,
                    float(r0['top_holders_percentage'] or 0),
                    float(r0['dev_balance_percentage'] or 0),
                    1.0 if bool(r0['blockaid_rugpull']) else 0.0,
                    1.0 if bool(r0['mint_authority_disabled']) else 0.0,
                    1.0 if bool(r0['freeze_authority_disabled']) else 0.0,
                ])
                
                # Targets
                future_prices = future_data['usd_price'].values
                current_price = prices[-1]
                
                # Price trajectory (normalized)
                price_trajectory = future_prices / current_price - 1.0
                
                # Pump classification (did price reach target_return?)
                max_return = np.max(price_trajectory)
                pump_target = 1.0 if max_return >= target_return else 0.0
                
                # Expected return (max return clipped)
                expected_return = np.clip(max_return, -1.0, 5.0)
                
                sequences.append(sequence)
                static_features.append(static)
                price_targets.append(price_trajectory)
                pump_targets.append([pump_target])
                return_targets.append([expected_return])
        
        print(f"✅ Created {len(sequences)} training samples")
        
        return (np.array(sequences), 
                np.array(static_features), 
                np.array(price_targets), 
                np.array(pump_targets), 
                np.array(return_targets))


def create_tcn_dataloader(sequences: np.ndarray,
                         static_features: np.ndarray,
                         price_targets: np.ndarray,
                         pump_targets: np.ndarray,
                         return_targets: np.ndarray,
                         batch_size: int = 32,
                         shuffle: bool = True) -> DataLoader:
    """Create DataLoader for TCN training."""
    dataset = TCNDataset(sequences, static_features, price_targets, pump_targets, return_targets)
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


async def test_tcn_data_loading():
    """Test TCN data loading."""
    print("🧪 Testing TCN data loading...")
    
    # Exclude broken token
    exclude_tokens = ["mqrtbGegiCbUpbiH2RibPwv5EPDoKzUzASrkMKuxHD7"]
    
    sequences, static, prices, pumps, returns = await load_tcn_data(
        encoder_sec=15,
        horizon_sec=300,
        exclude_tokens=exclude_tokens
    )
    
    if len(sequences) == 0:
        print("❌ No data loaded")
        return
    
    print(f"✅ Data loaded successfully:")
    print(f"   Sequences shape: {sequences.shape}")
    print(f"   Static features shape: {static.shape}")
    print(f"   Price targets shape: {prices.shape}")
    print(f"   Pump targets shape: {pumps.shape}")
    print(f"   Return targets shape: {returns.shape}")
    
    # Test DataLoader
    dataloader = create_tcn_dataloader(sequences, static, prices, pumps, returns, batch_size=4)
    
    for batch in dataloader:
        print(f"✅ Batch loaded:")
        print(f"   Time series: {batch['time_series'].shape}")
        print(f"   Static: {batch['static'].shape}")
        print(f"   Price target: {batch['price_target'].shape}")
        print(f"   Pump target: {batch['pump_target'].shape}")
        print(f"   Return target: {batch['return_target'].shape}")
        break
    
    print("🎉 TCN data loading test completed!")


if __name__ == "__main__":
    asyncio.run(test_tcn_data_loading())
