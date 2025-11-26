#!/usr/bin/env python3

import asyncio
import aiohttp
# SQLite (BACKUP - commented out)
# import aiosqlite
import json
import random
from typing import Dict, Any, Optional, List
from datetime import datetime
# PostgreSQL (ACTIVE)
from _v2_db_pool import get_db_pool
from config import config

class DexScreenerAnalyzer:
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        # SQLite (BACKUP - commented out)
        # import os
        # os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # self.db_path = db_path
        # self.conn: Optional[aiosqlite.Connection] = None
        # self.db_lock = asyncio.Lock()
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        
        self.is_scanning = False
        self.scan_interval = config.DEXSCREENER_ANALYZER_INTERVAL
        self.scan_task: Optional[asyncio.Task] = None
        self.batch_size = config.DEXSCREENER_ANALYZER_BATCH_SIZE
        
    async def ensure_connection(self):
        """PostgreSQL - pool created globally"""
        pass
        
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    async def _fetch_with_retries(self, url: str, **kwargs) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(1, 4):
            try:
                async with self.session.get(url, **kwargs) as resp:
                    status = resp.status
                    text = await resp.text()
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": parsed}
                    else:
                        return {"ok": False, "status": status, "json": parsed, "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = 0.4 * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                await asyncio.sleep(backoff)
        return {"ok": False, "error": str(last_exc)}
    
    async def get_token_data(self, token_address: str) -> Any:
        try:
            await self.ensure_session()
            url = f"{config.DEXSCREENER_API}?q={token_address}"
            res = await self._fetch_with_retries(url)
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error")}
        except Exception as e:
            return {"error": str(e)}
    
    def extract_pair_address(self, dexscreener_data: Any) -> Optional[str]:
        try:
            if isinstance(dexscreener_data, dict):
                pairs = dexscreener_data.get("pairs") or []
                if isinstance(pairs, list) and pairs:
                    return pairs[0].get("pairAddress")
            return None
        except Exception:
            return None

    async def save_dexscreener_data(self, token_id: int, token_address: str, dexscreener_data: Dict[str, Any]) -> bool:
        try:
            if not isinstance(dexscreener_data, dict):
                if self.debug:
                    print(f"⚠️ DexScreener data is not a dict for {token_address}")
                return False
            
            pairs = dexscreener_data.get("pairs", [])
            if not pairs or not isinstance(pairs, list):
                if self.debug:
                    print(f"⚠️ No pairs found for {token_address}")
                return False
            
            pair = pairs[0]
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                pair_address = pair.get("pairAddress")
                if pair_address:
                    await conn.execute("""
                        UPDATE token_ids SET token_pair = $1 WHERE id = $2
                    """, pair_address, token_id)
                
                # PostgreSQL UPSERT with float conversion for Decimal fields
                await conn.execute("""
                    INSERT INTO dexscreener_pairs (
                        token_id, chain_id, dex_id, url, pair_address,
                        price_native, price_usd, fdv, market_cap, pair_created_at, timestamp
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, CURRENT_TIMESTAMP)
                    ON CONFLICT (token_id) DO UPDATE SET
                        chain_id = EXCLUDED.chain_id,
                        dex_id = EXCLUDED.dex_id,
                        url = EXCLUDED.url,
                        pair_address = EXCLUDED.pair_address,
                        price_native = EXCLUDED.price_native,
                        price_usd = EXCLUDED.price_usd,
                        fdv = EXCLUDED.fdv,
                        market_cap = EXCLUDED.market_cap,
                        pair_created_at = EXCLUDED.pair_created_at,
                        timestamp = CURRENT_TIMESTAMP
                """,
                    token_id,
                    pair.get("chainId"),
                    pair.get("dexId"),
                    pair.get("url"),
                    pair_address,
                    str(pair.get("priceNative", "")) if pair.get("priceNative") else None,
                    str(pair.get("priceUsd", "")) if pair.get("priceUsd") else None,
                    float(pair.get("fdv", 0)) if pair.get("fdv") else None,
                    float(pair.get("marketCap", 0)) if pair.get("marketCap") else None,
                    # pair_created_at в PostgreSQL — це TEXT, тому конвертуємо datetime → str
                    datetime.fromtimestamp(pair.get("pairCreatedAt", 0) / 1000).isoformat() if pair.get("pairCreatedAt") else None
                )
                
                base_token = pair.get("baseToken", {})
                if base_token:
                    await conn.execute("""
                        INSERT INTO dexscreener_base_token (token_id, address, name, symbol)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (token_id) DO UPDATE SET
                            address = EXCLUDED.address,
                            name = EXCLUDED.name,
                            symbol = EXCLUDED.symbol
                    """, token_id, base_token.get("address"), base_token.get("name"), base_token.get("symbol"))
                
                quote_token = pair.get("quoteToken", {})
                if quote_token:
                    await conn.execute("""
                        INSERT INTO dexscreener_quote_token (token_id, address, name, symbol)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (token_id) DO UPDATE SET
                            address = EXCLUDED.address,
                            name = EXCLUDED.name,
                            symbol = EXCLUDED.symbol
                    """, token_id, quote_token.get("address"), quote_token.get("name"), quote_token.get("symbol"))
                
                txns = pair.get("txns", {})
                if txns:
                    m5 = txns.get("m5", {})
                    h1 = txns.get("h1", {})
                    h6 = txns.get("h6", {})
                    h24 = txns.get("h24", {})
                    await conn.execute("""
                        INSERT INTO dexscreener_txns (
                            token_id, m5_buys, m5_sells, h1_buys, h1_sells,
                            h6_buys, h6_sells, h24_buys, h24_sells
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        ON CONFLICT (token_id) DO UPDATE SET
                            m5_buys = EXCLUDED.m5_buys,
                            m5_sells = EXCLUDED.m5_sells,
                            h1_buys = EXCLUDED.h1_buys,
                            h1_sells = EXCLUDED.h1_sells,
                            h6_buys = EXCLUDED.h6_buys,
                            h6_sells = EXCLUDED.h6_sells,
                            h24_buys = EXCLUDED.h24_buys,
                            h24_sells = EXCLUDED.h24_sells
                    """,
                        token_id,
                        m5.get("buys", 0), m5.get("sells", 0),
                        h1.get("buys", 0), h1.get("sells", 0),
                        h6.get("buys", 0), h6.get("sells", 0),
                        h24.get("buys", 0), h24.get("sells", 0)
                    )
                
                volume = pair.get("volume", {})
                if volume:
                    await conn.execute("""
                        INSERT INTO dexscreener_volume (token_id, h24, h6, h1, m5)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (token_id) DO UPDATE SET
                            h24 = EXCLUDED.h24,
                            h6 = EXCLUDED.h6,
                            h1 = EXCLUDED.h1,
                            m5 = EXCLUDED.m5
                    """, token_id,
                        float(volume.get("h24", 0)) if volume.get("h24") else None,
                        float(volume.get("h6", 0)) if volume.get("h6") else None,
                        float(volume.get("h1", 0)) if volume.get("h1") else None,
                        float(volume.get("m5", 0)) if volume.get("m5") else None
                    )
                
                price_change = pair.get("priceChange", {})
                if price_change:
                    await conn.execute("""
                        INSERT INTO dexscreener_price_change (token_id, m5, h1, h6, h24)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (token_id) DO UPDATE SET
                            m5 = EXCLUDED.m5,
                            h1 = EXCLUDED.h1,
                            h6 = EXCLUDED.h6,
                            h24 = EXCLUDED.h24
                    """, token_id,
                        float(price_change.get("m5", 0)) if price_change.get("m5") else None,
                        float(price_change.get("h1", 0)) if price_change.get("h1") else None,
                        float(price_change.get("h6", 0)) if price_change.get("h6") else None,
                        float(price_change.get("h24", 0)) if price_change.get("h24") else None
                    )
                
                liquidity = pair.get("liquidity", {})
                if liquidity:
                    await conn.execute("""
                        INSERT INTO dexscreener_liquidity (token_id, usd, base, quote)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (token_id) DO UPDATE SET
                            usd = EXCLUDED.usd,
                            base = EXCLUDED.base,
                            quote = EXCLUDED.quote
                    """, token_id,
                        float(liquidity.get("usd", 0)) if liquidity.get("usd") else None,
                        float(liquidity.get("base", 0)) if liquidity.get("base") else None,
                        float(liquidity.get("quote", 0)) if liquidity.get("quote") else None
                    )
                
                # Increment check_dexscreener only on successful data update
                await conn.execute("""
                    UPDATE token_ids 
                    SET check_dexscreener = LEAST(check_dexscreener + 1, 3)
                    WHERE id = $1
                """, token_id)

                if self.debug:
                    print(f"✅ Saved DexScreener data for token_id={token_id}, address={token_address}")
                
                return True
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error saving DexScreener data for {token_address}: {e}")
            return False
    
    async def get_tokens_to_scan(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, token_address, check_dexscreener
                    FROM token_ids
                    WHERE check_dexscreener < 3
                    ORDER BY created_at ASC
                    LIMIT $1
                """, limit)
                
                tokens = []
                for row in rows:
                    tokens.append({
                        "token_id": row['id'],
                        "token_address": row['token_address'],
                        "check_dexscreener": row['check_dexscreener']
                    })
                
                return tokens
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting tokens to scan: {e}")
            return []
    
    async def scan_token(self, token: Dict[str, Any]) -> bool:
        try:
            token_id = token["token_id"]
            token_address = token["token_address"]
            
            if self.debug:
                print(f"🔍 Scanning token_id={token_id}, address={token_address}, check={token['check_dexscreener']}")
            
            dexscreener_data = await self.get_token_data(token_address)
            
            # Check for API error
            if "error" in dexscreener_data:
                if self.debug:
                    print(f"⚠️ DexScreener API error for {token_address}: {dexscreener_data['error']}")
                return False
            
            # Try to save data (increments check_dexscreener on success)
            success = await self.save_dexscreener_data(token_id, token_address, dexscreener_data)
            if not success and self.debug:
                print(f"⚠️ No pairs found for {token_address}")
            
            return success
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error scanning token {token.get('token_address')}: {e}")
            return False
    
    async def _auto_scan_loop(self):
        while self.is_scanning:
            try:
                tokens = await self.get_tokens_to_scan(limit=self.batch_size)
                
                if not tokens:
                    if self.debug:
                        print(f"ℹ️ No tokens to scan (all have check_dexscreener >= 3)")
                    await asyncio.sleep(self.scan_interval)
                    continue
                
                if self.debug:
                    print(f"📊 Scanning {len(tokens)} tokens...")
                
                tasks = [self.scan_token(token) for token in tokens]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                success_count = sum(1 for r in results if r is True)
                
                if self.debug:
                    print(f"✅ Scanned {success_count}/{len(tokens)} tokens successfully")
                
            except Exception as e:
                if self.debug:
                    print(f"❌ Auto-scan loop error: {e}")
            
            await asyncio.sleep(self.scan_interval)
    
    async def start_auto_scan(self):
        if self.is_scanning:
            return {"success": False, "message": "DexScreener scanner already running"}
        
        self.is_scanning = True
        self.scan_task = asyncio.create_task(self._auto_scan_loop())
        return {"success": True, "message": "DexScreener scanner started"}
    
    async def stop_auto_scan(self):
        if not self.is_scanning:
            return {"success": False, "message": "DexScreener scanner not running"}
        
        self.is_scanning = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except asyncio.CancelledError:
                pass
            self.scan_task = None
        
        return {"success": True, "message": "DexScreener scanner stopped"}
    
    def get_status(self):
        return {
            "is_scanning": self.is_scanning,
            "scan_interval": self.scan_interval,
            "batch_size": self.batch_size
        }

dexscreener_instance: Optional[DexScreenerAnalyzer] = None

async def get_dexscreener_analyzer() -> DexScreenerAnalyzer:
    global dexscreener_instance
    if dexscreener_instance is None:
        dexscreener_instance = DexScreenerAnalyzer(debug=True)
        await dexscreener_instance.ensure_connection()
        await dexscreener_instance.ensure_session()
    return dexscreener_instance


async def refresh_missing_token_pairs(debug: bool = True, delay_seconds: float = 1.0, batch_size: int = 5, max_tokens: int = None, force_rescan: bool = False) -> Dict:
    """
    Ручне заповнення token_pair для токенів без торгової пари
    
    Проходить по всіх токенах БЕЗ token_pair і запитує DexScreener API.
    Rate limit: 5 запитів/секунду (batch_size=5, delay=1.0s)
    
    Args:
        debug: Виводити детальні логи
        delay_seconds: Затримка між батчами в секундах (за замовчуванням 1.0s)
        batch_size: Кількість токенів на батч (за замовчуванням 5 - rate limit)
        max_tokens: Максимум токенів для обробки (None = всі токени). Для тестування.
        force_rescan: Якщо True, сканує навіть токени де check_dexscreener >= 3
    
    Returns:
        Dict з результатами: total_tokens, processed_tokens, success_count, failed_count
    
    Usage:
        # Тільки токени БЕЗ token_pair (skip готових):
        python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs())"
        
        # Перші 10 токенів (тест):
        python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs(max_tokens=10))"
        
        # Force rescan (навіть якщо check_dexscreener >= 3):
        python3 -c "import asyncio; from _v2_analyzer_dexscreener import refresh_missing_token_pairs; asyncio.run(refresh_missing_token_pairs(force_rescan=True))"
    """
    analyzer = DexScreenerAnalyzer(debug=debug)
    
    try:
        await analyzer.ensure_connection()
        await analyzer.ensure_session()
        
        # Отримуємо токени БЕЗ token_pair
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            if force_rescan:
                # Всі токени БЕЗ token_pair (навіть з check_dexscreener >= 3)
                rows = await conn.fetch("""
                    SELECT id, token_address, check_dexscreener
                    FROM token_ids
                    WHERE token_pair IS NULL OR token_pair = ''
                    ORDER BY created_at ASC
                """)
            else:
                # Тільки токени де check_dexscreener < 3 (ще є спроби)
                rows = await conn.fetch("""
                    SELECT id, token_address, check_dexscreener
                    FROM token_ids
                    WHERE (token_pair IS NULL OR token_pair = '')
                      AND check_dexscreener < 3
                    ORDER BY created_at ASC
                """)
        
        tokens = [
            {
                "token_id": row['id'],
                "token_address": row['token_address'],
                "check_dexscreener": row['check_dexscreener']
            }
            for row in rows
        ]
        
        if not tokens:
            print("⚠️  Токенів БЕЗ token_pair не знайдено в БД")
            return {
                "success": True,
                "total_tokens": 0,
                "processed_tokens": 0,
                "success_count": 0,
                "failed_count": 0
            }
        
        # Обмежуємо кількість токенів для тестування
        if max_tokens:
            tokens = tokens[:max_tokens]
        
        print(f"\n{'='*80}")
        print(f"🚀 ЗАПОВНЕННЯ TOKEN_PAIR ДЛЯ ТОКЕНІВ БЕЗ ТОРГОВОЇ ПАРИ")
        print(f"{'='*80}")
        print(f"📊 Знайдено токенів БЕЗ token_pair: {len(tokens)}")
        if not force_rescan:
            print(f"⏭️  Пропускаємо токени з check_dexscreener >= 3")
        else:
            print(f"🔄 Force rescan: сканування ВСІХ токенів БЕЗ token_pair")
        print(f"⏱️  Затримка між батчами: {delay_seconds}s")
        print(f"📦 Розмір батчу: {batch_size} токенів/сек (rate limit)")
        if max_tokens:
            print(f"🧪 ТЕСТОВИЙ РЕЖИМ: оброблюємо тільки {max_tokens} токени")
        print(f"{'='*80}\n")
        
        success_count = 0
        failed_count = 0
        processed_tokens = 0
        
        # Обробляємо по батчах (5 токенів за 1 секунду)
        total_batches = (len(tokens) + batch_size - 1) // batch_size
        
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(tokens))
            batch = tokens[start_idx:end_idx]
            
            print(f"\n{'─'*80}")
            print(f"📦 Батч {batch_idx + 1}/{total_batches} ({len(batch)} токенів)")
            print(f"{'─'*80}")
            
            # Обробляємо всі токени в батчі паралельно
            tasks = []
            for token in batch:
                tasks.append(analyzer.scan_token(token))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Підраховуємо результати
            for idx, result in enumerate(results):
                token = batch[idx]
                processed_tokens += 1
                
                if isinstance(result, Exception):
                    failed_count += 1
                    if debug:
                        print(f"❌ Token {token['token_address'][:16]}... failed: {result}")
                elif result is True:
                    success_count += 1
                    if debug:
                        print(f"✅ Token {token['token_address'][:16]}... success")
                else:
                    failed_count += 1
                    if debug:
                        print(f"⚠️  Token {token['token_address'][:16]}... no pair found")
            
            print(f"📊 Батч результат: {sum(1 for r in results if r is True)}/{len(batch)} успішно")
            
            # Затримка між батчами (крім останнього)
            if batch_idx < total_batches - 1:
                print(f"⏳ Затримка {delay_seconds}s перед наступним батчем...")
                await asyncio.sleep(delay_seconds)
        
        print(f"\n{'='*80}")
        print(f"🎉 ЗАПОВНЕННЯ ЗАВЕРШЕНО")
        print(f"{'='*80}")
        print(f"✅ Оброблено токенів: {processed_tokens}/{len(tokens)}")
        print(f"✅ Успішно заповнено: {success_count}")
        print(f"❌ Помилок/не знайдено: {failed_count}")
        print(f"{'='*80}\n")
        
        return {
            "success": True,
            "total_tokens": len(tokens),
            "processed_tokens": processed_tokens,
            "success_count": success_count,
            "failed_count": failed_count
        }
        
    finally:
        await analyzer.close()
