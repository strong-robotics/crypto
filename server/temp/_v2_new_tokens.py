#!/usr/bin/env python3

# SQLite (BACKUP - commented out)
# import aiosqlite
import aiohttp
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket
# PostgreSQL (ACTIVE)
from _v2_db_pool import get_db_pool
from config import config

class JupiterScannerV2:
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        # SQLite (BACKUP - commented out)
        # import os
        # os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # self.db_path = db_path
        # self.conn: Optional[aiosqlite.Connection] = None
        # self.db_lock = asyncio.Lock()
        
        # Jupiter API налаштування
        self.api_url = config.JUPITER_RECENT_API
        self.debug = debug
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Rate limiting
        self.rate_limit_delay = 2.0
        self.max_retries = 3
        self.retry_delay = 5.0
        self.last_request_time = 0
        
        # Auto-scan налаштування
        self.is_scanning = False
        self.scan_interval = config.JUPITER_SCANNER_INTERVAL
        self.scan_task: Optional[asyncio.Task] = None
        self.connected_clients: List[WebSocket] = []
    
    async def ensure_connection(self):
        """PostgreSQL - tables created by migration script"""
        # Tables already exist in PostgreSQL from migration
        pass
    
    async def close(self):
        """Close HTTP session only (PostgreSQL pool closed globally)"""
        if self.session:
            await self.session.close()
    
    async def create_all_tables(self):
        """PostgreSQL - tables already created by migration script"""
        # Tables already exist in PostgreSQL, skip creation
        # SQLite version backed up in git history
        pass
    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
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
        """Зберігає тільки Jupiter дані в БД (PostgreSQL). Повертає (success, is_new)"""
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                token_address = token_data.get('id', '')
                if not token_address:
                    return (False, False)
                
                # Перевіряємо, чи токен вже існує (PostgreSQL)
                existing_token = await conn.fetchval("""
                    SELECT id FROM token_ids WHERE token_address = $1
                """, token_address)
                is_new = existing_token is None
                
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
                
                # UPSERT в token_ids (merged table: token_ids + tokens)
                # Якщо token_address новий → INSERT
                # Якщо token_address вже є → UPDATE всіх полів
                token_id = await conn.fetchval("""
                    INSERT INTO token_ids (
                        token_address, name, symbol, icon, decimals, twitter, website, dev,
                        circ_supply, total_supply, token_program, launchpad,
                        holder_count, usd_price, liquidity, fdv, mcap,
                        bonding_curve, price_block_id, organic_score, organic_score_label,
                        updated_at, history_ready
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, FALSE
                    )
                    ON CONFLICT (token_address) DO UPDATE SET
                        name = EXCLUDED.name,
                        symbol = EXCLUDED.symbol,
                        icon = EXCLUDED.icon,
                        decimals = EXCLUDED.decimals,
                        twitter = EXCLUDED.twitter,
                        website = EXCLUDED.website,
                        dev = EXCLUDED.dev,
                        circ_supply = EXCLUDED.circ_supply,
                        total_supply = EXCLUDED.total_supply,
                        token_program = EXCLUDED.token_program,
                        launchpad = EXCLUDED.launchpad,
                        holder_count = EXCLUDED.holder_count,
                        usd_price = EXCLUDED.usd_price,
                        liquidity = EXCLUDED.liquidity,
                        fdv = EXCLUDED.fdv,
                        mcap = EXCLUDED.mcap,
                        bonding_curve = EXCLUDED.bonding_curve,
                        price_block_id = EXCLUDED.price_block_id,
                        organic_score = EXCLUDED.organic_score,
                        organic_score_label = EXCLUDED.organic_score_label,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                """,
                    token_address,
                    safe_get('name', 'Unknown'),
                    safe_get('symbol', 'UNKNOWN'),
                    safe_get('icon', ''),
                    safe_get('decimals', 0, int),
                    safe_get('twitter', ''),
                    safe_get('website', ''),
                    safe_get('dev', ''),
                    float(safe_get('circSupply', 0.0, float)),
                    float(safe_get('totalSupply', 0.0, float)),
                    safe_get('tokenProgram', ''),
                    safe_get('launchpad', ''),
                    safe_get('holderCount', 0, int),
                    float(safe_get('usdPrice', 0.0, float)),
                    float(safe_get('liquidity', 0.0, float)),
                    float(safe_get('fdv', 0.0, float)),
                    float(safe_get('mcap', 0.0, float)),
                    float(safe_get('bondingCurve', 0.0, float)),
                    safe_get('priceBlockId', 0, int),
                    float(safe_get('organicScore', 0.0, float)),
                    safe_get('organicScoreLabel', ''),
                    # Парсимо updatedAt timestamp в datetime (Jupiter format: ISO 8601)
                    datetime.fromisoformat(safe_get('updatedAt', '').replace('Z', '+00:00')).replace(tzinfo=None) if safe_get('updatedAt', '') else None
                )
                
                if not token_id:
                    return (False, False)
                
                # ===== ДОДАТКОВІ ТАБЛИЦІ =====
                
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
                    # Спочатку видаляємо старі теги (щоб уникнути дублікатів)
                    await conn.execute("DELETE FROM token_tags WHERE token_id = $1", token_id)
                    
                    # Додаємо нові теги
                    for tag in tags:
                        if tag and tag.strip():
                            await conn.execute("""
                                INSERT INTO token_tags (token_id, tag)
                                VALUES ($1, $2)
                                ON CONFLICT (token_id, tag) DO NOTHING
                            """, token_id, tag.strip())
                
                if self.debug and is_new:
                    print(f"✅ Saved NEW Jupiter token: {token_address}")
                elif self.debug:
                    print(f"♻️  Updated existing token: {token_address}")
                return (True, is_new)
                
        except Exception as e:
            print(f"Error saving Jupiter data: {e}")
            return (False, False)
    
    async def get_tokens_from_api(self, limit: int = 20) -> Dict[str, Any]:
        """Отримує токени з Jupiter API"""
        try:
            await self.ensure_session()
            
            data = await self.make_request_with_retry(self.api_url, timeout=10)
            
            if data is None:
                return {
                    "success": False,
                    "error": "Failed to fetch data after all retry attempts"
                }
            
            tokens = data[:limit]
            
            saved_count = 0
            new_count = 0
            new_tokens = []
            
            def safe_get(token_data, key: str, default=None, field_type=str):
                value = token_data.get(key, default)
                if value is None or value == '':
                    if field_type == int:
                        return 0
                    elif field_type == float:
                        return 0.0
                    else:
                        return default or 'Unknown'
                return value
            
            for token in tokens:
                # Просто зберігаємо/оновлюємо токен
                # save_jupiter_data сам визначить чи це INSERT (новий) чи UPDATE (існуючий)
                success, is_new = await self.save_jupiter_data(token)
                if success:
                    saved_count += 1
                    if is_new:
                        new_count += 1
                        # Додаємо тільки НОВІ токени до списку
                        new_tokens.append({
                            "id": safe_get(token, "id", ""),
                            "name": safe_get(token, "name", "Unknown"),
                            "mcap": safe_get(token, "mcap", 0, float),
                            "symbol": safe_get(token, "symbol", "UNKNOWN"),
                            "price": safe_get(token, "usdPrice", 0, float),
                            "holders": safe_get(token, "holderCount", 0, int),
                            "dex": "Analyzing...",
                            "pair": None
                        })
            
            if self.debug:
                print(f"📊 Jupiter API: {len(tokens)} fetched, {saved_count} saved, {new_count} NEW tokens")
            
            formatted_tokens = new_tokens
            
            return {
                "success": True,
                "tokens": formatted_tokens,
                "total_found": len(formatted_tokens),
                "total_fetched": len(tokens),
                "saved_count": saved_count,
                "new_count": new_count,
                "scan_time": datetime.now().isoformat(),
                "replace_old": False  # Додаємо тільки нові, не замінюємо всі
            }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    # SQLite version (BACKUP - commented out)
    # async def get_tokens_from_db(self, limit: int = 100) -> Dict[str, Any]:
    #     """Отримує токени з БД"""
    #     # This method used SQLite and is now replaced by TokensReaderV2
    #     # See _v2_tokens_reader.py for PostgreSQL version
    #     pass
    
    async def broadcast_to_clients(self, data):
        """Розсилає дані всім підключеним клієнтам"""
        if not self.connected_clients:
            return
            
        json_data = json.dumps(data, ensure_ascii=False)
        
        data_type = "unknown"
        if isinstance(data, list):
            data_type = f"tokens_update ({len(data)} tokens)"
        elif isinstance(data, dict):
            data_type = data.get('type', 'unknown')
        
        print(f"📡 Broadcasting to {len(self.connected_clients)} clients: {data_type}")
        
        disconnected_clients = []
        for client in self.connected_clients:
            try:
                await client.send_text(json_data)
                await asyncio.sleep(0.001)
            except Exception as e:
                print(f"❌ Error sending to client: {e}")
                disconnected_clients.append(client)
        
        for client in disconnected_clients:
            self.connected_clients.remove(client)
        
        print(f"✅ Broadcast completed to {len(self.connected_clients)} clients")
    
    def add_client(self, websocket: WebSocket):
        """Додає клієнта до списку підключених"""
        self.connected_clients.append(websocket)
    
    def remove_client(self, websocket: WebSocket):
        """Видаляє клієнта зі списку підключених"""
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
    
    async def start_auto_scan(self):
        """Запускає автоматичне сканування"""
        if self.is_scanning:
            return {"success": False, "message": "Auto-scan already running"}
        
        self.is_scanning = True
        self.scan_task = asyncio.create_task(self._auto_scan_loop())
        return {"success": True, "message": "Auto-scan started"}
    
    async def stop_auto_scan(self):
        """Зупиняє автоматичне сканування"""
        if not self.is_scanning:
            return {"success": False, "message": "Auto-scan not running"}
        
        self.is_scanning = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except asyncio.CancelledError:
                pass
            self.scan_task = None
        
        return {"success": True, "message": "Auto-scan stopped"}
    
    async def _auto_scan_loop(self):
        """Внутрішній цикл автоматичного сканування"""
        while self.is_scanning:
            try:
                result = await self.get_tokens_from_api(limit=20)
                
                if result["success"]:
                    new_count = result.get('new_count', 0)
                    if new_count > 0:
                        print(f"✅ Auto-scan: {new_count} NEW tokens found and saved to DB")
                    else:
                        print(f"ℹ️  Auto-scan: No new tokens (checked {result.get('total_fetched', 0)} tokens)")
                else:
                    print(f"❌ Auto-scan error: {result.get('error', 'Unknown error')}")
                    
            except Exception as e:
                print(f"❌ Auto-scan exception: {e}")
            
            await asyncio.sleep(self.scan_interval)
    

    def get_status(self):
        """Повертає статус сканера"""
        return {
            "is_scanning": self.is_scanning,
            "scan_interval": self.scan_interval,
            "connected_clients": len(self.connected_clients),
            "api_url": self.api_url
        }

if __name__ == "__main__":
    pass
