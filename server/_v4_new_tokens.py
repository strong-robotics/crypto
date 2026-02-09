#!/usr/bin/env python3

import asyncio
import aiohttp
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from _v3_db_pool import get_db_pool
from config import config

class JupiterScannerV4:
    def __init__(self):
        self.api_url = config.JUPITER_RECENT_API
        self.session: Optional[aiohttp.ClientSession] = None
        
        self.rate_limit_delay = 0.8
        self.max_retries = 3
        self.retry_delay = 1.0
        self.last_request_time = 0
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
            await asyncio.sleep(self.rate_limit_delay - time_since_last_request)
        self.last_request_time = time.time()

    async def make_request_with_retry(self, url: str) -> Optional[List[Dict[str, Any]]]:
        for attempt in range(self.max_retries):
            try:
                await self.respect_rate_limit()
                async with self.session.get(url, timeout=1.5) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:
                        # Fixed 1.5s pause as requested
                        await asyncio.sleep(1.5)
            except Exception:
                await asyncio.sleep(self.retry_delay)
        return None

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

    async def save_batch_data(self, tokens_data: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        Optimized batch save using atomic upserts.
        Saves all tokens in a single transaction with minimized query count.
        """
        if not tokens_data:
            return 0, 0
        
        saved_count = 0
        new_count = 0
        pool = await get_db_pool()
        ts = int(time.time())

        async with pool.acquire() as conn:
            for token_data in tokens_data:
                addr = token_data.get('id')
                if not addr: continue

                try:
                    async with conn.transaction():
                        # 1. Atomic Upsert for core data
                        audit = token_data.get('audit', {})
                        stats5m = token_data.get('stats5m', {}) or {}
                        stats1h = token_data.get('stats1h', {}) or {}
                        stats6h = token_data.get('stats6h', {}) or {}
                        stats24h = token_data.get('stats24h', {}) or {}
                        fp = token_data.get('firstPool') or {}
                        
                        pool_created_at = None
                        if fp.get('createdAt'):
                            try:
                                pool_created_at = datetime.fromisoformat(str(fp['createdAt']).replace('Z', '+00:00')).replace(tzinfo=None)
                            except Exception: pass

                        res = await conn.fetchrow("""
                            INSERT INTO tokens (
                                token_address, name, symbol, icon, decimals, dev,
                                circ_supply, total_supply, token_program, holder_count,
                                usd_price, liquidity, fdv, mcap, price_block_id,
                                organic_score, organic_score_label, pattern_code,
                                token_pair, first_pool_created_at,
                                mint_authority_disabled, freeze_authority_disabled, top_holders_percentage, dev_balance_percentage, blockaid_rugpull,
                                price_change_5m, holder_change_5m, liquidity_change_5m, volume_change_5m, buy_volume_5m, sell_volume_5m, num_buys_5m, num_sells_5m,
                                price_change_1h, num_buys_1h, num_sells_1h,
                                price_change_6h, num_buys_6h, num_sells_6h,
                                price_change_24h, num_buys_24h, num_sells_24h
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'unknown',$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41)
                            ON CONFLICT (token_address) DO UPDATE SET
                                name = EXCLUDED.name, symbol = EXCLUDED.symbol, icon = EXCLUDED.icon, dev = EXCLUDED.dev,
                                holder_count = EXCLUDED.holder_count, usd_price = EXCLUDED.usd_price, liquidity = EXCLUDED.liquidity, 
                                mcap = EXCLUDED.mcap, price_block_id = EXCLUDED.price_block_id,
                                organic_score = EXCLUDED.organic_score, organic_score_label = EXCLUDED.organic_score_label,
                                token_pair = COALESCE(tokens.token_pair, EXCLUDED.token_pair),
                                mint_authority_disabled = EXCLUDED.mint_authority_disabled, freeze_authority_disabled = EXCLUDED.freeze_authority_disabled,
                                top_holders_percentage = EXCLUDED.top_holders_percentage, dev_balance_percentage = EXCLUDED.dev_balance_percentage,
                                num_buys_5m = EXCLUDED.num_buys_5m, num_sells_5m = EXCLUDED.num_sells_5m,
                                num_buys_24h = EXCLUDED.num_buys_24h, num_sells_24h = EXCLUDED.num_sells_24h,
                                token_updated_at = CURRENT_TIMESTAMP
                            RETURNING id, (xmin::text = (select txid_current()::text)) as is_new
                        """, 
                            addr, token_data.get('name', 'Unknown'), token_data.get('symbol', 'UNKNOWN'), token_data.get('icon', ''),
                            int(token_data.get('decimals', 0)), token_data.get('dev', ''),
                            self._sanitize(token_data.get('circSupply')), self._sanitize(token_data.get('totalSupply')), token_data.get('tokenProgram', ''),
                            int(token_data.get('holderCount', 0)), self._sanitize(token_data.get('usdPrice')), self._sanitize(token_data.get('liquidity')),
                            self._sanitize(token_data.get('fdv')), self._sanitize(token_data.get('mcap')), int(token_data.get('priceBlockId', 0)),
                            self._sanitize(token_data.get('organicScore'), 999999.0), token_data.get('organicScoreLabel', ''), 
                            fp.get('id') if fp.get('id') != addr else None, pool_created_at,
                            audit.get('mintAuthorityDisabled'), audit.get('freezeAuthorityDisabled'),
                            self._sanitize(audit.get('topHoldersPercentage'), 999.0),
                            self._sanitize(audit.get('devBalancePercentage'), 999.0),
                            audit.get('blockaidRugpull'),
                            self._sanitize(stats5m.get('priceChange'), 999999.0), self._sanitize(stats5m.get('holderChange'), 999999.0), self._sanitize(stats5m.get('liquidityChange'), 999999.0),
                            self._sanitize(stats5m.get('volumeChange'), 999999.0), self._sanitize(stats5m.get('buyVolume')), self._sanitize(stats5m.get('sellVolume')),
                            stats5m.get('numBuys'), stats5m.get('numSells'),
                            self._sanitize(stats1h.get('priceChange'), 999999.0), stats1h.get('numBuys'), stats1h.get('numSells'),
                            self._sanitize(stats6h.get('priceChange'), 999999.0), stats6h.get('numBuys'), stats6h.get('numSells'),
                            self._sanitize(stats24h.get('priceChange'), 999999.0), stats24h.get('numBuys'), stats24h.get('numSells')
                        )

                        if res:
                            token_id = res['id']
                            if res['is_new']: new_count += 1
                            saved_count += 1
                            
                            # 2. Add initial metric record
                            await conn.execute("""
                                INSERT INTO token_metrics_seconds (
                                    token_id, ts, usd_price, liquidity, fdv, mcap, price_block_id, jupiter_slot
                                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                                ON CONFLICT DO NOTHING
                            """, token_id, ts, self._sanitize(token_data.get('usdPrice')), self._sanitize(token_data.get('liquidity')),
                                self._sanitize(token_data.get('fdv')), self._sanitize(token_data.get('mcap')), int(token_data.get('priceBlockId', 0)),
                                int(token_data.get('priceBlockId', 0)))
                except Exception as e:
                    print(f"[ScannerV4] Failed to save token {addr}: {e}")

        return saved_count, new_count

    async def get_tokens_from_api(self, limit: int = 20) -> Dict[str, Any]:
        try:
            await self.ensure_session()
            data = await self.make_request_with_retry(self.api_url)
            if not data: return {"success": False, "error": "No data"}
            
            tokens = data[:limit]
            # Warm-up skip
            if self._warmup_skip_remaining > 0:
                skip = min(self._warmup_skip_remaining, len(tokens))
                tokens = tokens[skip:]
                self._warmup_skip_remaining = 0

            # Age filter
            max_age = int(getattr(config, 'NEW_TOKENS_MAX_AGE_SEC', 60))
            filtered_tokens = []
            now = datetime.utcnow()
            for t in tokens:
                cp = (t.get('firstPool') or {}).get('createdAt')
                if not cp: continue
                try:
                    dt = datetime.fromisoformat(str(cp).replace('Z', '+00:00')).replace(tzinfo=None)
                    if (now - dt).total_seconds() <= max_age:
                        filtered_tokens.append(t)
                except Exception: continue

            saved, new = await self.save_batch_data(filtered_tokens)
            
            return {"success": True, "saved_count": saved, "new_count": new, "total_fetched": len(filtered_tokens), "scan_time": datetime.now().isoformat()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_status(self) -> Dict[str, Any]:
        return {"api_url": self.api_url, "is_scanning": True} # Scanning is driven by scheduler

_instance: Optional[JupiterScannerV4] = None
async def get_scanner() -> JupiterScannerV4:
    global _instance
    if _instance is None:
        _instance = JupiterScannerV4()
        await _instance.ensure_session()
    return _instance
