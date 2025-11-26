#!/usr/bin/env python3

import asyncio
import aiohttp
import json
import random
from typing import Dict, Any, Optional, List
from datetime import datetime
from _v2_db_pool import get_db_pool
from config import config

class JupiterAnalyzer:
    """
    Jupiter API analyzer для заповнення додаткових таблиць (stats, audit, firstPool, tags)
    
    Використовує Jupiter Search API:
    https://lite-api.jup.ag/tokens/v2/search?query={token_address}
    
    Аналогічно до DexScreenerAnalyzer, але для Jupiter даних.
    """
    
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        
        # Auto-scan налаштування
        self.is_scanning = False
        self.scan_interval = config.JUPITER_ANALYZER_INTERVAL
        self.scan_task: Optional[asyncio.Task] = None
        self.batch_size = config.JUPITER_ANALYZER_BATCH_SIZE
        
        # Cursor для пагінації (запам'ятовуємо де зупинились)
        self.last_processed_id = 0  # ID останнього обробленого токена
        
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
        """Запит з retry логікою"""
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
    
    async def get_tokens_data_batch(self, token_addresses: List[str]) -> Any:
        """
        Отримує дані для БАГАТЬОХ токенів одночасно з Jupiter Search API
        
        https://lite-api.jup.ag/tokens/v2/search?query={address1,address2,address3,...}
        
        Limit: 100 токенів за запит
        """
        try:
            await self.ensure_session()
            
            # Comma-separated список mint addresses
            query = ",".join(token_addresses[:100])  # Jupiter limit: 100 addresses
            url = f"{config.JUPITER_SEARCH_API}?query={query}"
            
            res = await self._fetch_with_retries(url)
            if res["ok"]:
                return res["json"]
            return {"error": res.get("error")}
        except Exception as e:
            return {"error": str(e)}
    
    async def save_jupiter_extended_data(self, token_id: int, token_address: str, jupiter_data: Any) -> bool:
        """
        Зберігає ДОДАТКОВІ дані Jupiter (stats, audit, firstPool, tags)
        
        Не чіпає основну таблицю token_ids (це робить JupiterScannerV2).
        Тільки заповнює token_stats, token_audit, token_first_pool, token_tags.
        """
        try:
            if not isinstance(jupiter_data, list) or not jupiter_data:
                if self.debug:
                    print(f"⚠️  Jupiter Search повернув порожній результат для {token_address}")
                return False
            
            # Jupiter Search повертає масив, беремо перший токен
            token_data = jupiter_data[0]
            
            if token_data.get('id') != token_address:
                if self.debug:
                    print(f"⚠️  Jupiter повернув інший токен: {token_data.get('id')} != {token_address}")
                return False
            
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                # 1. Token Stats (5m, 1h, 6h, 24h)
                stats_5m = token_data.get('stats5m', {})
                stats_1h = token_data.get('stats1h', {})
                stats_6h = token_data.get('stats6h', {})
                stats_24h = token_data.get('stats24h', {})
                
                if stats_5m or stats_1h or stats_6h or stats_24h:
                    await conn.execute("""
                        INSERT INTO token_stats (
                            token_id,
                            stats_5m_price_change, stats_5m_buy_volume, stats_5m_sell_volume,
                            stats_5m_num_buys, stats_5m_num_sells, stats_5m_num_traders, stats_5m_num_net_buyers,
                            stats_1h_price_change, stats_1h_buy_volume, stats_1h_sell_volume,
                            stats_1h_num_buys, stats_1h_num_sells, stats_1h_num_traders, stats_1h_num_net_buyers,
                            stats_6h_price_change, stats_6h_buy_volume, stats_6h_sell_volume,
                            stats_6h_num_buys, stats_6h_num_sells, stats_6h_num_traders, stats_6h_num_net_buyers,
                            stats_24h_price_change, stats_24h_buy_volume, stats_24h_sell_volume,
                            stats_24h_num_buys, stats_24h_num_sells, stats_24h_num_traders, stats_24h_num_net_buyers
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                            $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29
                        )
                        ON CONFLICT (token_id) DO UPDATE SET
                            stats_5m_price_change = EXCLUDED.stats_5m_price_change,
                            stats_5m_buy_volume = EXCLUDED.stats_5m_buy_volume,
                            stats_5m_sell_volume = EXCLUDED.stats_5m_sell_volume,
                            stats_5m_num_buys = EXCLUDED.stats_5m_num_buys,
                            stats_5m_num_sells = EXCLUDED.stats_5m_num_sells,
                            stats_5m_num_traders = EXCLUDED.stats_5m_num_traders,
                            stats_5m_num_net_buyers = EXCLUDED.stats_5m_num_net_buyers,
                            stats_1h_price_change = EXCLUDED.stats_1h_price_change,
                            stats_1h_buy_volume = EXCLUDED.stats_1h_buy_volume,
                            stats_1h_sell_volume = EXCLUDED.stats_1h_sell_volume,
                            stats_1h_num_buys = EXCLUDED.stats_1h_num_buys,
                            stats_1h_num_sells = EXCLUDED.stats_1h_num_sells,
                            stats_1h_num_traders = EXCLUDED.stats_1h_num_traders,
                            stats_1h_num_net_buyers = EXCLUDED.stats_1h_num_net_buyers,
                            stats_6h_price_change = EXCLUDED.stats_6h_price_change,
                            stats_6h_buy_volume = EXCLUDED.stats_6h_buy_volume,
                            stats_6h_sell_volume = EXCLUDED.stats_6h_sell_volume,
                            stats_6h_num_buys = EXCLUDED.stats_6h_num_buys,
                            stats_6h_num_sells = EXCLUDED.stats_6h_num_sells,
                            stats_6h_num_traders = EXCLUDED.stats_6h_num_traders,
                            stats_6h_num_net_buyers = EXCLUDED.stats_6h_num_net_buyers,
                            stats_24h_price_change = EXCLUDED.stats_24h_price_change,
                            stats_24h_buy_volume = EXCLUDED.stats_24h_buy_volume,
                            stats_24h_sell_volume = EXCLUDED.stats_24h_sell_volume,
                            stats_24h_num_buys = EXCLUDED.stats_24h_num_buys,
                            stats_24h_num_sells = EXCLUDED.stats_24h_num_sells,
                            stats_24h_num_traders = EXCLUDED.stats_24h_num_traders,
                            stats_24h_num_net_buyers = EXCLUDED.stats_24h_num_net_buyers,
                            updated_at = CURRENT_TIMESTAMP
                    """,
                        token_id,
                        float(stats_5m.get('priceChange', 0)) if stats_5m.get('priceChange') else None,
                        float(stats_5m.get('buyVolume', 0)) if stats_5m.get('buyVolume') else None,
                        float(stats_5m.get('sellVolume', 0)) if stats_5m.get('sellVolume') else None,
                        stats_5m.get('numBuys', 0) if stats_5m.get('numBuys') else None,
                        stats_5m.get('numSells', 0) if stats_5m.get('numSells') else None,
                        stats_5m.get('numTraders', 0) if stats_5m.get('numTraders') else None,
                        stats_5m.get('numNetBuyers', 0) if stats_5m.get('numNetBuyers') else None,
                        float(stats_1h.get('priceChange', 0)) if stats_1h.get('priceChange') else None,
                        float(stats_1h.get('buyVolume', 0)) if stats_1h.get('buyVolume') else None,
                        float(stats_1h.get('sellVolume', 0)) if stats_1h.get('sellVolume') else None,
                        stats_1h.get('numBuys', 0) if stats_1h.get('numBuys') else None,
                        stats_1h.get('numSells', 0) if stats_1h.get('numSells') else None,
                        stats_1h.get('numTraders', 0) if stats_1h.get('numTraders') else None,
                        stats_1h.get('numNetBuyers', 0) if stats_1h.get('numNetBuyers') else None,
                        float(stats_6h.get('priceChange', 0)) if stats_6h.get('priceChange') else None,
                        float(stats_6h.get('buyVolume', 0)) if stats_6h.get('buyVolume') else None,
                        float(stats_6h.get('sellVolume', 0)) if stats_6h.get('sellVolume') else None,
                        stats_6h.get('numBuys', 0) if stats_6h.get('numBuys') else None,
                        stats_6h.get('numSells', 0) if stats_6h.get('numSells') else None,
                        stats_6h.get('numTraders', 0) if stats_6h.get('numTraders') else None,
                        stats_6h.get('numNetBuyers', 0) if stats_6h.get('numNetBuyers') else None,
                        float(stats_24h.get('priceChange', 0)) if stats_24h.get('priceChange') else None,
                        float(stats_24h.get('buyVolume', 0)) if stats_24h.get('buyVolume') else None,
                        float(stats_24h.get('sellVolume', 0)) if stats_24h.get('sellVolume') else None,
                        stats_24h.get('numBuys', 0) if stats_24h.get('numBuys') else None,
                        stats_24h.get('numSells', 0) if stats_24h.get('numSells') else None,
                        stats_24h.get('numTraders', 0) if stats_24h.get('numTraders') else None,
                        stats_24h.get('numNetBuyers', 0) if stats_24h.get('numNetBuyers') else None
                    )
                
                # 2. Token Audit
                audit = token_data.get('audit', {})
                if audit:
                    await conn.execute("""
                        INSERT INTO token_audit (
                            token_id, mint_authority_disabled, freeze_authority_disabled,
                            top_holders_percentage, dev_balance_percentage, dev_migrations
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (token_id) DO UPDATE SET
                            mint_authority_disabled = EXCLUDED.mint_authority_disabled,
                            freeze_authority_disabled = EXCLUDED.freeze_authority_disabled,
                            top_holders_percentage = EXCLUDED.top_holders_percentage,
                            dev_balance_percentage = EXCLUDED.dev_balance_percentage,
                            dev_migrations = EXCLUDED.dev_migrations,
                            updated_at = CURRENT_TIMESTAMP
                    """,
                        token_id,
                        audit.get('mintAuthorityDisabled', False),
                        audit.get('freezeAuthorityDisabled', False),
                        float(audit.get('topHoldersPercentage', 0)) if audit.get('topHoldersPercentage') else None,
                        float(audit.get('devBalancePercentage', 0)) if audit.get('devBalancePercentage') else None,
                        audit.get('devMigrations', 0) if audit.get('devMigrations') else None
                    )
                
                # 3. Token First Pool
                first_pool = token_data.get('firstPool', {})
                if first_pool:
                    pool_created_at = first_pool.get('createdAt', '')
                    if pool_created_at:
                        pool_created_dt = datetime.fromisoformat(pool_created_at.replace('Z', '+00:00')).replace(tzinfo=None)
                    else:
                        pool_created_dt = None
                    
                    await conn.execute("""
                        INSERT INTO token_first_pool (token_id, pool_id, created_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (token_id) DO UPDATE SET
                            pool_id = EXCLUDED.pool_id,
                            created_at = EXCLUDED.created_at,
                            updated_at = CURRENT_TIMESTAMP
                    """,
                        token_id,
                        first_pool.get('id', ''),
                        pool_created_dt
                    )
                
                # 4. Token Tags
                tags = token_data.get('tags', [])
                if tags:
                    # Спочатку видаляємо старі теги
                    await conn.execute("DELETE FROM token_tags WHERE token_id = $1", token_id)
                    
                    # Додаємо нові теги
                    for tag in tags:
                        if tag and tag.strip():
                            await conn.execute("""
                                INSERT INTO token_tags (token_id, tag)
                                VALUES ($1, $2)
                                ON CONFLICT (token_id, tag) DO NOTHING
                            """, token_id, tag.strip())
                
                # Оновлюємо check_jupiter counter
                await conn.execute("""
                    UPDATE token_ids 
                    SET check_jupiter = LEAST(check_jupiter + 1, 3)
                    WHERE id = $1
                """, token_id)
                
                if self.debug:
                    print(f"✅ Saved Jupiter extended data for token_id={token_id}, address={token_address}")
                
                return True
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error saving Jupiter extended data for {token_address}: {e}")
            return False
    
    async def get_tokens_to_scan(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Отримує РІВНО 100 токенів для BATCH оновлення Jupiter даних
        
        Логіка (CURSOR-BASED):
        1. Починаємо з self.last_processed_id (де зупинились минулого разу)
        2. Ітеруємося по ВСІХ токенах (ORDER BY id ASC)
        3. Пропускаємо токени де check_jupiter >= 3
        4. Збираємо РІВНО 100 токенів (не більше, не менше)
        5. Зберігаємо ID останнього токена в self.last_processed_id
        6. Якщо дійшли до кінця БД → скидаємо cursor на 0 (починаємо спочатку)
        
        Приклад (254 токени):
        Цикл 1: Token 1-150 → знайшли 100 з check<3 → last_id=150
        Цикл 2: Token 151-200 → знайшли 50 з check<3 → продовжуємо
                Token 201-254 → знайшли ще 50 → всього 100 → last_id=254
        Цикл 3: Дійшли до кінця (254) → RESET cursor=0 → починаємо з Token 1
        
        Returns:
            List[Dict]: РІВНО 100 токенів (або менше, якщо в БД < 100 з check<3)
        """
        try:
            pool = await get_db_pool()
            tokens = []
            
            async with pool.acquire() as conn:
                # Отримуємо максимальний ID в БД
                max_id = await conn.fetchval("SELECT MAX(id) FROM token_ids")
                if not max_id:
                    return []
                
                # Якщо курсор вийшов за межі - скидаємо на 0
                if self.last_processed_id >= max_id:
                    self.last_processed_id = 0
                    if self.debug:
                        print(f"🔄 Cursor RESET: починаємо з початку БД")
                
                current_id = self.last_processed_id
                
                # Збираємо РІВНО 100 токенів (або менше якщо закінчились)
                while len(tokens) < limit:
                    # Fetch наступну порцію токенів (по 200 за раз для швидкості)
                    rows = await conn.fetch("""
                        SELECT id, token_address, check_jupiter
                        FROM token_ids
                        WHERE id > $1 AND check_jupiter < 3
                        ORDER BY id ASC
                        LIMIT 200
                    """, current_id)
                    
                    if not rows:
                        # Дійшли до кінця БД - перевіряємо чи є ще токени з початку
                        if current_id > 0:
                            if self.debug:
                                print(f"📍 Досягли кінця БД (id={current_id}), перевіряємо з початку...")
                            # Спробуємо з початку БД
                            rows = await conn.fetch("""
                                SELECT id, token_address, check_jupiter
                                FROM token_ids
                                WHERE id <= $1 AND check_jupiter < 3
                                ORDER BY id ASC
                                LIMIT 200
                            """, self.last_processed_id)
                            
                            if not rows:
                                # Немає більше токенів для обробки
                                break
                            else:
                                # Reset cursor для наступного циклу
                                current_id = 0
                        else:
                            # Вже пробували з початку - немає більше токенів
                            break
                    
                    # Додаємо токени до списку
                    for row in rows:
                        if len(tokens) >= limit:
                            break
                        tokens.append({
                            "token_id": row['id'],
                            "token_address": row['token_address'],
                            "check_jupiter": row['check_jupiter']
                        })
                        current_id = row['id']  # Оновлюємо позицію курсора
                    
                    # Якщо не знайшли достатньо токенів - виходимо
                    if len(rows) < 200 and len(tokens) < limit:
                        break
                
                # Зберігаємо позицію для наступного циклу
                if tokens:
                    self.last_processed_id = tokens[-1]["token_id"]
                    if self.debug:
                        print(f"📍 Cursor position: last_id={self.last_processed_id}, collected={len(tokens)} tokens")
                
                return tokens
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting tokens to scan: {e}")
            return []
    
    async def scan_token(self, token: Dict[str, Any]) -> bool:
        """Сканує один токен"""
        try:
            token_id = token["token_id"]
            token_address = token["token_address"]
            
            if self.debug:
                print(f"🔍 Scanning token_id={token_id}, address={token_address}, check={token['check_jupiter']}")
            
            jupiter_data = await self.get_token_data(token_address)
            
            # API error → increment check_jupiter
            if "error" in jupiter_data:
                if self.debug:
                    print(f"⚠️  Jupiter API error for {token_address}: {jupiter_data['error']}")
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE token_ids 
                        SET check_jupiter = LEAST(check_jupiter + 1, 3)
                        WHERE id = $1
                    """, token_id)
                return False
            
            # Try to save data (increments check_jupiter inside)
            success = await self.save_jupiter_extended_data(token_id, token_address, jupiter_data)
            
            # If save failed (no data) → ALSO increment check_jupiter
            if not success:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE token_ids 
                        SET check_jupiter = LEAST(check_jupiter + 1, 3)
                        WHERE id = $1
                    """, token_id)
            
            return success
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error scanning token {token.get('token_address')}: {e}")
            # On exception → also increment to avoid infinite loop
            try:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE token_ids 
                        SET check_jupiter = LEAST(check_jupiter + 1, 3)
                        WHERE id = $1
                    """, token["token_id"])
            except:
                pass
            return False
    
    async def _auto_scan_loop(self):
        """
        Auto-scan loop для BATCH оновлення Jupiter даних
        
        Логіка:
        1. Кожні 3 секунди беремо 100 токенів де check_jupiter < 3
        2. Робимо ОДИН batch запит до Jupiter API (comma-separated addresses)
        3. Оновлюємо дані + інкрементуємо check_jupiter для ВСІХ токенів
        4. Повертаємось на початок - знову перші 100 токенів (ORDER BY created_at ASC)
        5. Цикл безкінечний
        """
        while self.is_scanning:
            try:
                # Крок 1: Беремо 100 токенів (найстаріші з check_jupiter < 3)
                tokens = await self.get_tokens_to_scan(limit=self.batch_size)
                
                if not tokens:
                    if self.debug:
                        print(f"ℹ️  Немає токенів для сканування (всі check_jupiter >= 3)")
                    await asyncio.sleep(self.scan_interval)
                    continue
                
                if self.debug:
                    print(f"\n{'='*80}")
                    print(f"📊 BATCH SCAN: {len(tokens)} токенів")
                    print(f"{'='*80}")
                
                # Крок 2: Один batch запит для всіх токенів
                token_addresses = [t["token_address"] for t in tokens]
                token_map = {t["token_address"]: t for t in tokens}  # address -> token_id mapping
                
                jupiter_data = await self.get_tokens_data_batch(token_addresses)
                
                # Крок 3: Обробка результатів
                if "error" in jupiter_data:
                    if self.debug:
                        print(f"⚠️  Jupiter API error: {jupiter_data['error']}")
                    # Інкрементуємо check_jupiter для ВСІХ токенів при помилці
                    await self._increment_check_jupiter_bulk(tokens)
                    await asyncio.sleep(self.scan_interval)
                    continue
                
                # Jupiter повертає масив токенів
                if not isinstance(jupiter_data, list):
                    if self.debug:
                        print(f"⚠️  Jupiter повернув невірний формат даних")
                    await self._increment_check_jupiter_bulk(tokens)
                    await asyncio.sleep(self.scan_interval)
                    continue
                
                # Обробляємо кожен токен з відповіді
                updated_count = 0
                for token_data in jupiter_data:
                    token_address = token_data.get('id')
                    if token_address and token_address in token_map:
                        token_info = token_map[token_address]
                        success = await self.save_jupiter_extended_data(
                            token_info["token_id"],
                            token_address,
                            [token_data]  # save_jupiter_extended_data очікує масив
                        )
                        if success:
                            updated_count += 1
                
                # Інкрементуємо check_jupiter для ВСІХ токенів (навіть якщо дані не знайдено)
                await self._increment_check_jupiter_bulk(tokens)
                
                if self.debug:
                    print(f"✅ Оновлено {updated_count}/{len(tokens)} токенів")
                    print(f"⏳ Затримка {self.scan_interval}s перед наступним батчем...")
                    print(f"{'='*80}\n")
                
            except Exception as e:
                if self.debug:
                    print(f"❌ Auto-scan loop error: {e}")
            
            await asyncio.sleep(self.scan_interval)
    
    async def _increment_check_jupiter_bulk(self, tokens: List[Dict[str, Any]]):
        """Інкрементує check_jupiter для всіх токенів в батчі"""
        try:
            pool = await get_db_pool()
            token_ids = [t["token_id"] for t in tokens]
            
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE token_ids 
                    SET check_jupiter = LEAST(check_jupiter + 1, 3)
                    WHERE id = ANY($1::int[])
                """, token_ids)
                
                if self.debug:
                    print(f"📊 Інкрементовано check_jupiter для {len(token_ids)} токенів")
                    
        except Exception as e:
            if self.debug:
                print(f"❌ Error incrementing check_jupiter: {e}")
    
    async def start_auto_scan(self):
        """Запускає auto-scan"""
        if self.is_scanning:
            return {"success": False, "message": "Jupiter analyzer already running"}
        
        self.is_scanning = True
        self.scan_task = asyncio.create_task(self._auto_scan_loop())
        return {"success": True, "message": "Jupiter analyzer started"}
    
    async def stop_auto_scan(self):
        """Зупиняє auto-scan"""
        if not self.is_scanning:
            return {"success": False, "message": "Jupiter analyzer not running"}
        
        self.is_scanning = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except asyncio.CancelledError:
                pass
            self.scan_task = None
        
        return {"success": True, "message": "Jupiter analyzer stopped"}
    
    def get_status(self):
        """Статус analyzer"""
        return {
            "is_scanning": self.is_scanning,
            "scan_interval": self.scan_interval,
            "batch_size": self.batch_size
        }


# Singleton instance
jupiter_analyzer_instance: Optional[JupiterAnalyzer] = None

async def get_jupiter_analyzer() -> JupiterAnalyzer:
    global jupiter_analyzer_instance
    if jupiter_analyzer_instance is None:
        jupiter_analyzer_instance = JupiterAnalyzer(debug=True)
        await jupiter_analyzer_instance.ensure_connection()
        await jupiter_analyzer_instance.ensure_session()
    return jupiter_analyzer_instance


async def refresh_missing_jupiter_data(debug: bool = True, delay_seconds: float = 1.0, batch_size: int = 5, max_tokens: int = None, force_rescan: bool = False) -> Dict:
    """
    Ручне заповнення Jupiter додаткових даних (stats, audit, firstPool, tags)
    
    Проходить по всіх токенах БЕЗ token_stats і запитує Jupiter Search API.
    Rate limit: 5 запитів/секунду (batch_size=5, delay=1.0s)
    
    Args:
        debug: Виводити детальні логи
        delay_seconds: Затримка між батчами в секундах (за замовчуванням 1.0s)
        batch_size: Кількість токенів на батч (за замовчуванням 5)
        max_tokens: Максимум токенів для обробки (None = всі токени)
        force_rescan: Якщо True, сканує навіть токени де check_jupiter >= 3
    
    Returns:
        Dict з результатами: total_tokens, processed_tokens, success_count, failed_count
    
    Usage:
        # Заповнення для всіх токенів БЕЗ stats:
        python3 -c "import asyncio; from _v2_analyzer_jupiter import refresh_missing_jupiter_data; asyncio.run(refresh_missing_jupiter_data())"
        
        # Перші 10 токенів (тест):
        python3 -c "import asyncio; from _v2_analyzer_jupiter import refresh_missing_jupiter_data; asyncio.run(refresh_missing_jupiter_data(max_tokens=10))"
    """
    analyzer = JupiterAnalyzer(debug=debug)
    
    try:
        await analyzer.ensure_connection()
        await analyzer.ensure_session()
        
        # Отримуємо токени БЕЗ token_stats
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            if force_rescan:
                rows = await conn.fetch("""
                    SELECT t.id, t.token_address, t.check_jupiter
                    FROM token_ids t
                    LEFT JOIN token_stats s ON t.id = s.token_id
                    WHERE s.token_id IS NULL
                    ORDER BY t.created_at ASC
                """)
            else:
                rows = await conn.fetch("""
                    SELECT t.id, t.token_address, t.check_jupiter
                    FROM token_ids t
                    LEFT JOIN token_stats s ON t.id = s.token_id
                    WHERE s.token_id IS NULL
                      AND t.check_jupiter < 3
                    ORDER BY t.created_at ASC
                """)
        
        tokens = [
            {
                "token_id": row['id'],
                "token_address": row['token_address'],
                "check_jupiter": row['check_jupiter']
            }
            for row in rows
        ]
        
        if not tokens:
            print("⚠️  Токенів БЕЗ Jupiter додаткових даних не знайдено в БД")
            return {
                "success": True,
                "total_tokens": 0,
                "processed_tokens": 0,
                "success_count": 0,
                "failed_count": 0
            }
        
        if max_tokens:
            tokens = tokens[:max_tokens]
        
        print(f"\n{'='*80}")
        print(f"🚀 ЗАПОВНЕННЯ JUPITER ДОДАТКОВИХ ДАНИХ (stats, audit, firstPool, tags)")
        print(f"{'='*80}")
        print(f"📊 Знайдено токенів БЕЗ stats: {len(tokens)}")
        if not force_rescan:
            print(f"⏭️  Пропускаємо токени з check_jupiter >= 3")
        else:
            print(f"🔄 Force rescan: сканування ВСІХ токенів БЕЗ stats")
        print(f"⏱️  Затримка між батчами: {delay_seconds}s")
        print(f"📦 Розмір батчу: {batch_size} токенів/сек")
        if max_tokens:
            print(f"🧪 ТЕСТОВИЙ РЕЖИМ: оброблюємо тільки {max_tokens} токени")
        print(f"{'='*80}\n")
        
        success_count = 0
        failed_count = 0
        processed_tokens = 0
        
        total_batches = (len(tokens) + batch_size - 1) // batch_size
        
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(tokens))
            batch = tokens[start_idx:end_idx]
            
            print(f"\n{'─'*80}")
            print(f"📦 Батч {batch_idx + 1}/{total_batches} ({len(batch)} токенів)")
            print(f"{'─'*80}")
            
            tasks = []
            for token in batch:
                tasks.append(analyzer.scan_token(token))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
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
                        print(f"⚠️  Token {token['token_address'][:16]}... no data found")
            
            print(f"📊 Батч результат: {sum(1 for r in results if r is True)}/{len(batch)} успішно")
            
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

