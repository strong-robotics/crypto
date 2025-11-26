#!/usr/bin/env python3

import aiosqlite
import aiohttp
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

class JupiterScannerV2:
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        
        # Jupiter API налаштування
        self.api_url = "https://lite-api.jup.ag/tokens/v2/recent"
        self.debug = debug
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Rate limiting
        self.rate_limit_delay = 2.0
        self.max_retries = 3
        self.retry_delay = 5.0
        self.last_request_time = 0
        
        # Auto-scan налаштування
        self.is_scanning = False
        self.scan_interval = 5
        self.scan_task: Optional[asyncio.Task] = None
        self.connected_clients: List[WebSocket] = []
    
    async def ensure_connection(self):
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA cache_size=-64000;")
            await self.conn.execute("PRAGMA temp_store=MEMORY;")
            await self.conn.execute("PRAGMA foreign_keys=ON;")
            await self.create_all_tables()
    
    async def close(self):
        if self.session:
            await self.session.close()
        if self.conn:
            await self.conn.close()
            self.conn = None
    
    async def create_all_tables(self):
        """Створює всі таблиці БД (Jupiter + DexScreener + інші)"""
        async with self.db_lock:
            # === ОСНОВНІ ТАБЛИЦІ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT UNIQUE NOT NULL,
                    token_pair TEXT UNIQUE,
                    is_honeypot BOOLEAN,
                    lp_owner TEXT,
                    pattern TEXT DEFAULT '',
                    dev_address TEXT,
                    security_analyzed_at TIMESTAMP,
                    check_dexscreener INTEGER DEFAULT 0,
                    check_jupiter INTEGER DEFAULT 0,
                    check_sol_rpc INTEGER DEFAULT 0,
                    history_ready BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    token_id INTEGER PRIMARY KEY,
                    name TEXT,
                    symbol TEXT,
                    icon TEXT,
                    decimals INTEGER,
                    twitter TEXT,
                    dev TEXT,
                    circ_supply NUMERIC,
                    total_supply NUMERIC,
                    token_program TEXT,
                    launchpad TEXT,
                    holder_count INTEGER,
                    usd_price NUMERIC,
                    liquidity NUMERIC,
                    fdv NUMERIC,
                    mcap NUMERIC,
                    bonding_curve NUMERIC,
                    price_block_id INTEGER,
                    organic_score NUMERIC,
                    organic_score_label TEXT,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === СТАТИСТИКА ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats_5m (
                    token_id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    price_change NUMERIC,
                    liquidity_change NUMERIC,
                    buy_volume NUMERIC,
                    sell_volume NUMERIC,
                    buy_organic_volume NUMERIC,
                    num_buys INTEGER,
                    num_sells INTEGER,
                    num_traders INTEGER,
                    num_net_buyers INTEGER,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats_1h (
                    token_id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    price_change NUMERIC,
                    liquidity_change NUMERIC,
                    buy_volume NUMERIC,
                    sell_volume NUMERIC,
                    buy_organic_volume NUMERIC,
                    num_buys INTEGER,
                    num_sells INTEGER,
                    num_traders INTEGER,
                    num_net_buyers INTEGER,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats_6h (
                    token_id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    price_change NUMERIC,
                    liquidity_change NUMERIC,
                    buy_volume NUMERIC,
                    sell_volume NUMERIC,
                    buy_organic_volume NUMERIC,
                    num_buys INTEGER,
                    num_sells INTEGER,
                    num_traders INTEGER,
                    num_net_buyers INTEGER,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats_24h (
                    token_id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    price_change NUMERIC,
                    liquidity_change NUMERIC,
                    buy_volume NUMERIC,
                    sell_volume NUMERIC,
                    buy_organic_volume NUMERIC,
                    num_buys INTEGER,
                    num_sells INTEGER,
                    num_traders INTEGER,
                    num_net_buyers INTEGER,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === АУДИТ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_audit (
                    token_id INTEGER PRIMARY KEY,
                    mint_authority_disabled BOOLEAN,
                    freeze_authority_disabled BOOLEAN,
                    top_holders_percentage NUMERIC,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === ПЕРШИЙ ПУЛ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_first_pool (
                    token_id INTEGER PRIMARY KEY,
                    pool_id TEXT,
                    created_at TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === ТЕГИ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_tags (
                    token_id INTEGER,
                    tag TEXT,
                    PRIMARY KEY (token_id, tag),
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === DEXSCREENER ТАБЛИЦІ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_pairs (
                    token_id INTEGER PRIMARY KEY,
                    chain_id TEXT,
                    dex_id TEXT,
                    url TEXT,
                    pair_address TEXT,
                    price_native TEXT,
                    price_usd TEXT,
                    fdv NUMERIC,
                    market_cap NUMERIC,
                    pair_created_at TIMESTAMP,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_base_token (
                    token_id INTEGER PRIMARY KEY,
                    address TEXT,
                    name TEXT,
                    symbol TEXT,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_quote_token (
                    token_id INTEGER PRIMARY KEY,
                    address TEXT,
                    name TEXT,
                    symbol TEXT,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_txns (
                    token_id INTEGER PRIMARY KEY,
                    m5_buys INTEGER,
                    m5_sells INTEGER,
                    h1_buys INTEGER,
                    h1_sells INTEGER,
                    h6_buys INTEGER,
                    h6_sells INTEGER,
                    h24_buys INTEGER,
                    h24_sells INTEGER,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_volume (
                    token_id INTEGER PRIMARY KEY,
                    h24 NUMERIC,
                    h6 NUMERIC,
                    h1 NUMERIC,
                    m5 NUMERIC,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_price_change (
                    token_id INTEGER PRIMARY KEY,
                    m5 NUMERIC,
                    h1 NUMERIC,
                    h6 NUMERIC,
                    h24 NUMERIC,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dexscreener_liquidity (
                    token_id INTEGER PRIMARY KEY,
                    usd NUMERIC,
                    base NUMERIC,
                    quote NUMERIC,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # === ІНДЕКСИ ===
            # Основні індекси
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_address ON token_ids(token_address)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_pair ON token_ids(token_pair)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_created ON token_ids(created_at)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_honeypot ON token_ids(is_honeypot)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_pattern ON token_ids(pattern)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_security_analyzed ON token_ids(security_analyzed_at)")
            
            # Індекси токенів
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_price ON tokens(usd_price)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_liquidity ON tokens(liquidity)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_updated ON tokens(updated_at)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_organic_score ON tokens(organic_score)")
            
            # Індекси статистики
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_5m_timestamp ON token_stats_5m(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_1h_timestamp ON token_stats_1h(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_6h_timestamp ON token_stats_6h(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_24h_timestamp ON token_stats_24h(timestamp)")
            
            # DexScreener індекси
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_timestamp ON dexscreener_pairs(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_dex ON dexscreener_pairs(dex_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_chain ON dexscreener_pairs(chain_id)")
            
            await self.conn.commit()
    
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
        """Зберігає тільки Jupiter дані в БД. Повертає (success, is_new)"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                token_address = token_data.get('id', '')
                if not token_address:
                    return (False, False)
                
                # Перевіряємо, чи токен вже існує
                cursor = await self.conn.execute("""
                    SELECT id FROM token_ids WHERE token_address = ?
                """, (token_address,))
                existing_token = await cursor.fetchone()
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
                
                # 1. Створюємо або отримуємо token_id
                cursor = await self.conn.execute("""
                    INSERT OR IGNORE INTO token_ids (token_address, token_pair) 
                    VALUES (?, NULL)
                """, (token_address,))
                
                cursor = await self.conn.execute("""
                    SELECT id FROM token_ids WHERE token_address = ?
                """, (token_address,))
                row = await cursor.fetchone()
                if not row:
                    return False
                token_id = row[0]
                
                # 2. Оновлюємо основну інформацію про токен
                await self.conn.execute("""
                    INSERT OR REPLACE INTO tokens (
                        token_id, name, symbol, icon, decimals, twitter, dev,
                        circ_supply, total_supply, token_program, launchpad,
                        holder_count, usd_price, liquidity, fdv, mcap,
                        bonding_curve, price_block_id, organic_score, organic_score_label, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    safe_get('name', 'Unknown'),
                    safe_get('symbol', 'UNKNOWN'),
                    safe_get('icon', ''),
                    safe_get('decimals', 0, int),
                    safe_get('twitter', ''),
                    safe_get('dev', ''),
                    safe_get('circSupply', 0.0, float),
                    safe_get('totalSupply', 0.0, float),
                    safe_get('tokenProgram', ''),
                    safe_get('launchpad', ''),
                    safe_get('holderCount', 0, int),
                    safe_get('usdPrice', 0.0, float),
                    safe_get('liquidity', 0.0, float),
                    safe_get('fdv', 0.0, float),
                    safe_get('mcap', 0.0, float),
                    safe_get('bondingCurve', 0.0, float),
                    safe_get('priceBlockId', 0, int),
                    safe_get('organicScore', 0.0, float),
                    safe_get('organicScoreLabel', ''),
                    safe_get('updatedAt', '')
                ))
                
                # 3. Зберігаємо статистику 24h
                stats_24h = safe_get('stats24h', {})
                if stats_24h:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO token_stats_24h (
                            token_id, timestamp, price_change, liquidity_change,
                            buy_volume, sell_volume, buy_organic_volume,
                            num_buys, num_sells, num_traders, num_net_buyers
                        ) VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        token_id,
                        stats_24h.get('priceChange', 0.0),
                        stats_24h.get('liquidityChange', 0.0),
                        stats_24h.get('buyVolume', 0.0),
                        stats_24h.get('sellVolume', 0.0),
                        stats_24h.get('buyOrganicVolume', 0.0),
                        stats_24h.get('numBuys', 0),
                        stats_24h.get('numSells', 0),
                        stats_24h.get('numTraders', 0),
                        stats_24h.get('numNetBuyers', 0)
                    ))
                
                # 4. Зберігаємо аудит
                audit = safe_get('audit', {})
                if audit:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO token_audit (
                            token_id, mint_authority_disabled, freeze_authority_disabled, top_holders_percentage
                        ) VALUES (?, ?, ?, ?)
                    """, (
                        token_id,
                        audit.get('mintAuthorityDisabled', False),
                        audit.get('freezeAuthorityDisabled', False),
                        audit.get('topHoldersPercentage', 0.0)
                    ))
                
                # 5. Зберігаємо перший пул
                first_pool = safe_get('firstPool', {})
                if first_pool:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO token_first_pool (token_id, pool_id, created_at)
                        VALUES (?, ?, ?)
                    """, (
                        token_id,
                        first_pool.get('id', ''),
                        first_pool.get('createdAt', '')
                    ))
                
                # 6. Зберігаємо теги
                tags = safe_get('tags', [])
                if tags and isinstance(tags, list):
                    await self.conn.execute("DELETE FROM token_tags WHERE token_id = ?", (token_id,))
                    for tag in tags:
                        await self.conn.execute("INSERT INTO token_tags (token_id, tag) VALUES (?, ?)", (token_id, str(tag)))
                
                await self.conn.commit()
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
            
            # Отримуємо останній timestamp з БД
            await self.ensure_connection()
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT created_at FROM token_ids 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """)
                last_row = await cursor.fetchone()
                last_timestamp = last_row[0] if last_row else None
            
            if self.debug and last_timestamp:
                print(f"🕐 Last token in DB: {last_timestamp}")
            
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
                # Перевіряємо timestamp токена з Jupiter API
                first_pool = token.get('firstPool', {})
                token_created_at = first_pool.get('createdAt', '')
                
                # Якщо є останній timestamp у БД, фільтруємо старі токени
                if last_timestamp and token_created_at:
                    # Конвертуємо обидва формати до порівнюваного вигляду
                    # Jupiter: "2025-10-10T01:15:52Z"
                    # SQLite: "2025-10-10 01:12:53"
                    token_created_clean = token_created_at.replace('T', ' ').replace('Z', '')
                    if token_created_clean <= last_timestamp:
                        if self.debug:
                            print(f"⏭️  Skip OLD token: {token.get('name', '?')} ({token_created_clean} <= {last_timestamp})")
                        continue
                
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
    
    async def get_tokens_from_db(self, limit: int = 100) -> Dict[str, Any]:
        """Отримує токени з БД"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT 
                        ti.token_address,
                        ti.token_pair,
                        ti.is_honeypot,
                        ti.security_analyzed_at,
                        t.name,
                        t.symbol,
                        t.mcap,
                        t.usd_price,
                        t.holder_count,
                        t.updated_at,
                        ti.created_at
                    FROM token_ids ti
                    JOIN tokens t ON t.token_id = ti.id
                    ORDER BY t.updated_at DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = await cursor.fetchall()
                
                formatted_tokens = []
                for row in rows:
                    token_address, token_pair, is_honeypot, security_analyzed_at, name, symbol, mcap, price, holders, updated_at, created_at = row
                    
                    formatted_tokens.append({
                        "id": token_address,
                        "name": name or "Unknown",
                        "symbol": symbol or "UNKNOWN",
                        "mcap": mcap or 0,
                        "price": price or 0,
                        "holders": holders or 0,
                        "dex": "Analyzing...",
                        "pair": None,
                        "is_honeypot": is_honeypot,
                        "security_analyzed_at": security_analyzed_at.isoformat() if security_analyzed_at else None,
                        "updated_at": updated_at.isoformat() if updated_at else None,
                        "created_at": created_at.isoformat() if created_at else None
                    })
                
                return {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "scan_time": datetime.now().isoformat()
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
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
