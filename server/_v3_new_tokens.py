#!/usr/bin/env python3

import aiohttp
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from _v3_db_pool import get_db_pool
from config import config

class JupiterScannerV3:
    def __init__(self):
        self.api_url = config.JUPITER_RECENT_API
        self.session: Optional[aiohttp.ClientSession] = None
        
        self.rate_limit_delay = 2.0
        self.max_retries = 3
        self.retry_delay = 5.0
        self.last_request_time = 0
        # In-memory warm-up skip: drop first N tokens from the very first response
        self._warmup_skip_remaining: int = int(getattr(config, 'NEW_TOKENS_WARMUP_SKIP', 0) or 0) if getattr(config, 'NEW_TOKENS_WARMUP_SKIP_ENABLED', False) else 0
    
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def respect_rate_limit(self):
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if time_since_last_request < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last_request
            await asyncio.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    async def make_request_with_retry(self, url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        for attempt in range(self.max_retries):
            try:
                await self.respect_rate_limit()
                
                async with self.session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:
                        wait_time = self.retry_delay * (2 ** attempt)
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        return None
            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                continue
            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                continue
        
        return None
    
    async def save_jupiter_data(self, token_data: Dict[str, Any]) -> tuple[bool, bool]:
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                token_address = token_data.get('id', '')
                if not token_address:
                    return (False, False)
                
                existing_token = await conn.fetchval(
                    "SELECT id FROM tokens WHERE token_address = $1",
                    token_address,
                )
                is_new = existing_token is None

                if is_new and getattr(config, 'NEW_TOKENS_INSERT_CAP_ENABLED', False):
                    total_tokens = await conn.fetchval("SELECT COUNT(*) FROM tokens") or 0
                    cap = int(getattr(config, 'NEW_TOKENS_INSERT_CAP', 300))
                    if total_tokens >= cap:
                        return (True, False)
                
                def safe_get(key: str, default=None, field_type=str):
                    value = token_data.get(key, default)
                    if value is None or value == '':
                        if field_type == int:
                            return 0
                        elif field_type == float:
                            return 0.0
                        elif field_type == bool:
                            return False
                        else:
                            return default or 'Unknown'
                    return value
                
                # Використовуємо окремі INSERT або UPDATE замість ON CONFLICT
                if is_new:
                    # Новий токен - INSERT
                    await conn.execute("""
                        INSERT INTO tokens (
                            token_address, name, symbol, icon, decimals, dev,
                            circ_supply, total_supply, token_program,
                            holder_count, usd_price, liquidity, fdv, mcap,
                            price_block_id, organic_score, organic_score_label,
                            pattern_code
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, 'unknown'
                        )
                    """, 
                        token_address,
                        safe_get('name', 'Unknown'),
                        safe_get('symbol', 'UNKNOWN'),
                        safe_get('icon', ''),
                        safe_get('decimals', 0, int),
                        safe_get('dev', ''),
                        float(safe_get('circSupply', 0.0, float)),
                        float(safe_get('totalSupply', 0.0, float)),
                        safe_get('tokenProgram', ''),
                        safe_get('holderCount', 0, int),
                        float(safe_get('usdPrice', 0.0, float)),
                        float(safe_get('liquidity', 0.0, float)),
                        float(safe_get('fdv', 0.0, float)),
                        float(safe_get('mcap', 0.0, float)),
                        safe_get('priceBlockId', 0, int),
                        float(safe_get('organicScore', 0.0, float)),
                        safe_get('organicScoreLabel', '')
                    )
                else:
                    # Існуючий токен - UPDATE
                    await conn.execute("""
                        UPDATE tokens SET
                            name = $2,
                            symbol = $3,
                            icon = $4,
                            decimals = $5,
                            dev = $6,
                            circ_supply = $7,
                            total_supply = $8,
                            token_program = $9,
                            holder_count = $10,
                            usd_price = $11,
                            liquidity = $12,
                            fdv = $13,
                            mcap = $14,
                            price_block_id = $15,
                            organic_score = $16,
                            organic_score_label = $17
                        WHERE token_address = $1
                    """, 
                        token_address,
                        safe_get('name', 'Unknown'),
                        safe_get('symbol', 'UNKNOWN'),
                        safe_get('icon', ''),
                        safe_get('decimals', 0, int),
                        safe_get('dev', ''),
                        float(safe_get('circSupply', 0.0, float)),
                        float(safe_get('totalSupply', 0.0, float)),
                        safe_get('tokenProgram', ''),
                        safe_get('holderCount', 0, int),
                        float(safe_get('usdPrice', 0.0, float)),
                        float(safe_get('liquidity', 0.0, float)),
                        float(safe_get('fdv', 0.0, float)),
                        float(safe_get('mcap', 0.0, float)),
                        safe_get('priceBlockId', 0, int),
                        float(safe_get('organicScore', 0.0, float)),
                        safe_get('organicScoreLabel', '')
                    )
                
                token_id = await conn.fetchval("""
                    SELECT id FROM tokens WHERE token_address = $1
                """, token_address)
                
                if not token_id:
                    return (False, False)

                first_pool = token_data.get('firstPool', {})
                if first_pool:
                    candidate_pair = first_pool.get('id')
                    pool_created_at = first_pool.get('createdAt', '')
                    
                    if candidate_pair and candidate_pair != token_address:
                        existing_pair = await conn.fetchval("SELECT token_pair FROM tokens WHERE id = $1", token_id)
                        if existing_pair != candidate_pair:
                            pool_created_dt = None
                            if pool_created_at:
                                try:
                                    pool_created_dt = datetime.fromisoformat(pool_created_at.replace('Z', '+00:00')).replace(tzinfo=None)
                                except Exception:
                                    pool_created_dt = None
                            
                            await conn.execute(
                                "UPDATE tokens SET token_pair = $2, first_pool_created_at = $3, pair_resolve_attempts = 0 WHERE id = $1",
                                token_id,
                                candidate_pair,
                                pool_created_dt,
                            )
                    else:
                        # Пара не валідна - збільшуємо лічильник спроб
                        await conn.execute(
                            "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
                            token_id
                        )
                else:
                    # Немає first_pool - збільшуємо лічильник спроб
                    await conn.execute(
                        "UPDATE tokens SET pair_resolve_attempts = COALESCE(pair_resolve_attempts, 0) + 1 WHERE id = $1", 
                        token_id
                    )
                
                audit = token_data.get('audit', {})
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
                        float(audit.get('topHoldersPercentage', 0)) if audit.get('topHoldersPercentage') is not None else None,
                        float(audit.get('devBalancePercentage', 0)) if audit.get('devBalancePercentage') is not None else None,
                        audit.get('blockaidRugpull')
                    )
                
                for period in ['5m', '1h', '6h', '24h']:
                    stats = token_data.get(f'stats{period}', {})
                    if stats:
                        suffix = f'_{period}'
                        await conn.execute(f"""
                            UPDATE tokens SET
                                price_change{suffix} = $2,
                                holder_change{suffix} = $3,
                                liquidity_change{suffix} = $4,
                                volume_change{suffix} = $5,
                                buy_volume{suffix} = $6,
                                sell_volume{suffix} = $7,
                                buy_organic_volume{suffix} = $8,
                                sell_organic_volume{suffix} = $9,
                                num_buys{suffix} = $10,
                                num_sells{suffix} = $11,
                                num_traders{suffix} = $12
                            WHERE id = $1
                        """,
                            token_id,
                            float(stats.get('priceChange', 0)) if stats.get('priceChange') is not None else None,
                            float(stats.get('holderChange', 0)) if stats.get('holderChange') is not None else None,
                            float(stats.get('liquidityChange', 0)) if stats.get('liquidityChange') is not None else None,
                            float(stats.get('volumeChange', 0)) if stats.get('volumeChange') is not None else None,
                            float(stats.get('buyVolume', 0)) if stats.get('buyVolume') is not None else None,
                            float(stats.get('sellVolume', 0)) if stats.get('sellVolume') is not None else None,
                            float(stats.get('buyOrganicVolume', 0)) if stats.get('buyOrganicVolume') is not None else None,
                            float(stats.get('sellOrganicVolume', 0)) if stats.get('sellOrganicVolume') is not None else None,
                            stats.get('numBuys'),
                            stats.get('numSells'),
                            stats.get('numTraders')
                        )

                # Завжди записуємо метрики в token_metrics_seconds (і для нових, і для оновлених токенів)
                try:
                    ts = int(time.time())
                    usd_p = float(token_data.get('usdPrice', 0)) if token_data.get('usdPrice') is not None else None
                    liq = float(token_data.get('liquidity', 0)) if token_data.get('liquidity') is not None else None
                    fdv = float(token_data.get('fdv', 0)) if token_data.get('fdv') is not None else None
                    mcap = float(token_data.get('mcap', 0)) if token_data.get('mcap') is not None else None
                    pblk = token_data.get('priceBlockId')
                    
                    # Записуємо метрики для всіх токенів (нових і оновлених)
                    await conn.execute(
                        """
                        INSERT INTO token_metrics_seconds (
                            token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                        ON CONFLICT (token_id, ts) DO UPDATE SET
                            usd_price = EXCLUDED.usd_price,
                            liquidity = EXCLUDED.liquidity,
                            fdv = EXCLUDED.fdv,
                            mcap = EXCLUDED.mcap,
                            price_block_id = EXCLUDED.price_block_id,
                            jupiter_slot = EXCLUDED.jupiter_slot
                        """,
                        token_id, ts, usd_p, liq, fdv, mcap, pblk, pblk
                    )
                except Exception:
                    pass

                await conn.execute("UPDATE tokens SET token_updated_at = CURRENT_TIMESTAMP WHERE id = $1", token_id)

                return (True, is_new)
                
        except Exception:
            return (False, False)
    
    async def get_tokens_from_api(self, limit: int = 20, *, skip_persist: bool = False) -> Dict[str, Any]:
        try:
            await self.ensure_session()
            
            data = await self.make_request_with_retry(self.api_url, timeout=10)
            
            if data is None:
                return {
                    "success": False,
                    "error": "Failed to fetch data after all retry attempts"
                }
            
            tokens = data[:limit]
            # Apply warm-up skip only once, purely in-memory (do not persist skipped tokens)
            if self._warmup_skip_remaining > 0 and tokens:
                skip_n = min(self._warmup_skip_remaining, len(tokens))
                tokens = tokens[skip_n:]
                self._warmup_skip_remaining = 0
            
            saved_count = 0
            new_count = 0
            new_tokens = []
            
            for token in tokens:
                # Age filter: skip tokens older than configured threshold (based on firstPool.createdAt)
                fp = token.get('firstPool') or {}
                created_at = fp.get('createdAt')
                if not created_at:
                    continue  # Жёстко игнорируем токены без таймстампа
                try:
                    max_age = int(getattr(config, 'NEW_TOKENS_MAX_AGE_SEC', 60) or 0)
                except Exception:
                    max_age = 60
                try:
                    created_dt = datetime.fromisoformat(str(created_at).replace('Z', '+00:00')).replace(tzinfo=None)
                    age_sec = (datetime.utcnow().replace(tzinfo=None) - created_dt).total_seconds()
                    if max_age > 0 and age_sec > max_age:
                        continue
                except Exception:
                    continue
                if skip_persist:
                    # Warm-up mode: do not write anything to DB, only form response
                    new_tokens.append({
                        "id": token.get("id", ""),
                        "name": token.get("name", "Unknown"),
                        "mcap": float(token.get("mcap", 0)),
                        "symbol": token.get("symbol", "UNKNOWN"),
                        "price": float(token.get("usdPrice", 0)),
                        "holders": int(token.get("holderCount", 0)),
                        "pair": token.get("firstPool", {}).get("id")
                    })
                    continue
                success, is_new = await self.save_jupiter_data(token)
                if success:
                    saved_count += 1
                    if is_new:
                        new_count += 1
                        new_tokens.append({
                            "id": token.get("id", ""),
                            "name": token.get("name", "Unknown"),
                            "mcap": float(token.get("mcap", 0)),
                            "symbol": token.get("symbol", "UNKNOWN"),
                            "price": float(token.get("usdPrice", 0)),
                            "holders": int(token.get("holderCount", 0)),
                            "pair": token.get("firstPool", {}).get("id")
                        })
            
            return {
                "success": True,
                "tokens": new_tokens,
                "total_found": len(new_tokens),
                "total_fetched": len(tokens),
                "saved_count": saved_count,
                "new_count": new_count,
                "scan_time": datetime.now().isoformat(),
                "replace_old": False
            }
                
        except Exception:
            return {
                "success": False,
                "error": "Failed to fetch data after all retry attempts"
            }
    
    def get_status(self):
        return {
            "api_url": self.api_url
        }

_instance: Optional[JupiterScannerV3] = None

async def get_scanner() -> JupiterScannerV3:
    global _instance

    if _instance is None:
        _instance = JupiterScannerV3()
        await _instance.ensure_session()

    return _instance
