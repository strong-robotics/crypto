#!/usr/bin/env python3

import aiosqlite
import aiohttp
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

class AsyncTokenDatabase:
    def __init__(self, db_path: str = "db/tokens.db"):
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
    
    async def ensure_connection(self):
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA cache_size=-64000;")
            await self.conn.execute("PRAGMA temp_store=MEMORY;")
            await self.conn.execute("PRAGMA foreign_keys=ON;")
            await self.init_db()
    
    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None
    
    async def init_db(self):
        async with self.db_lock:
            # === ОСНОВНЫЕ ТАБЛИЦЫ ===
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT UNIQUE NOT NULL,
                    token_pair TEXT UNIQUE,
                    is_honeypot BOOLEAN,
                    lp_owner TEXT,
                    pattern TEXT,
                    dev_address TEXT,
                    security_analyzed_at TIMESTAMP,
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
            
            # === СТАТИСТИКИ ===
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
            
            # === ПЕРВЫЙ ПУЛ ===
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
            
            # === DEXSCREENER ТАБЛИЦЫ ===
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
            
            # === ИНДЕКСЫ ===
            # Основные индексы
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_address ON token_ids(token_address)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_pair ON token_ids(token_pair)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_created ON token_ids(created_at)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_honeypot ON token_ids(is_honeypot)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_pattern ON token_ids(pattern)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ids_security_analyzed ON token_ids(security_analyzed_at)")
            
            # Индексы токенов
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_price ON tokens(usd_price)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_liquidity ON tokens(liquidity)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_updated ON tokens(updated_at)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_organic_score ON tokens(organic_score)")
            
            # Индексы статистики
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_5m_timestamp ON token_stats_5m(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_1h_timestamp ON token_stats_1h(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_6h_timestamp ON token_stats_6h(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_24h_timestamp ON token_stats_24h(timestamp)")
            
            # DexScreener индексы
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_timestamp ON dexscreener_pairs(timestamp)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_dex ON dexscreener_pairs(dex_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_chain ON dexscreener_pairs(chain_id)")
            
            await self.conn.commit()

    async def save_token(self, token_data: Dict[str, Any]) -> bool:
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                token_address = token_data.get('id', '')
                if not token_address:
                    return False
                
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
                
                # 1. Создаем или получаем token_id
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
                
                # 2. Обновляем основную информацию о токене
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
                
                # 3. Сохраняем статистику 24h
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
                
                # 4. Сохраняем аудит
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
                
                # 5. Сохраняем первый пул
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
                
                # 6. Сохраняем теги
                tags = safe_get('tags', [])
                if tags and isinstance(tags, list):
                    await self.conn.execute("DELETE FROM token_tags WHERE token_id = ?", (token_id,))
                    for tag in tags:
                        await self.conn.execute("INSERT INTO token_tags (token_id, tag) VALUES (?, ?)", (token_id, str(tag)))
                
                await self.conn.commit()
                return True
                
        except Exception as e:
            print(f"Error saving token: {e}")
            return False

    async def save_dexscreener_data(self, token_id: int, dexscreener_data: Dict[str, Any]) -> bool:
        """Сохраняет данные DexScreener для токена"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                pairs = dexscreener_data.get('pairs', [])
                if not pairs:
                    return False
                
                pair = pairs[0]  # Берем первую пару
                
                # 1. Обновляем token_pair в основной таблице
                await self.conn.execute("""
                    UPDATE token_ids 
                    SET token_pair = ? 
                    WHERE id = ?
                """, (pair.get('pairAddress', ''), token_id))
                
                # 2. Сохраняем основную информацию о паре
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_pairs (
                        token_id, chain_id, dex_id, url, pair_address,
                        price_native, price_usd, fdv, market_cap, pair_created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    pair.get('chainId', ''),
                    pair.get('dexId', ''),
                    pair.get('url', ''),
                    pair.get('pairAddress', ''),
                    pair.get('priceNative', ''),
                    pair.get('priceUsd', ''),
                    pair.get('fdv', 0.0),
                    pair.get('marketCap', 0.0),
                    datetime.fromtimestamp(pair.get('pairCreatedAt', 0) / 1000) if pair.get('pairCreatedAt') else None
                ))
                
                # 3. Сохраняем base token
                base_token = pair.get('baseToken', {})
                if base_token:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_base_token (token_id, address, name, symbol)
                        VALUES (?, ?, ?, ?)
                    """, (
                        token_id,
                        base_token.get('address', ''),
                        base_token.get('name', ''),
                        base_token.get('symbol', '')
                    ))
                
                # 4. Сохраняем quote token
                quote_token = pair.get('quoteToken', {})
                if quote_token:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_quote_token (token_id, address, name, symbol)
                        VALUES (?, ?, ?, ?)
                    """, (
                        token_id,
                        quote_token.get('address', ''),
                        quote_token.get('name', ''),
                        quote_token.get('symbol', '')
                    ))
                
                # 5. Сохраняем транзакции
                txns = pair.get('txns', {})
                if txns:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_txns (
                            token_id, m5_buys, m5_sells, h1_buys, h1_sells, 
                            h6_buys, h6_sells, h24_buys, h24_sells
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        token_id,
                        txns.get('m5', {}).get('buys', 0),
                        txns.get('m5', {}).get('sells', 0),
                        txns.get('h1', {}).get('buys', 0),
                        txns.get('h1', {}).get('sells', 0),
                        txns.get('h6', {}).get('buys', 0),
                        txns.get('h6', {}).get('sells', 0),
                        txns.get('h24', {}).get('buys', 0),
                        txns.get('h24', {}).get('sells', 0)
                    ))
                
                # 6. Сохраняем объемы
                volume = pair.get('volume', {})
                if volume:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_volume (token_id, h24, h6, h1, m5)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        token_id,
                        volume.get('h24', 0.0),
                        volume.get('h6', 0.0),
                        volume.get('h1', 0.0),
                        volume.get('m5', 0.0)
                    ))
                
                # 7. Сохраняем изменения цены
                price_change = pair.get('priceChange', {})
                if price_change:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_price_change (token_id, m5, h1, h6, h24)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        token_id,
                        price_change.get('m5', 0.0),
                        price_change.get('h1', 0.0),
                        price_change.get('h6', 0.0),
                        price_change.get('h24', 0.0)
                    ))
                
                # 8. Сохраняем ликвидность
                liquidity = pair.get('liquidity', {})
                if liquidity:
                    await self.conn.execute("""
                        INSERT OR REPLACE INTO dexscreener_liquidity (token_id, usd, base, quote)
                        VALUES (?, ?, ?, ?)
                    """, (
                        token_id,
                        liquidity.get('usd', 0.0),
                        liquidity.get('base', 0.0),
                        liquidity.get('quote', 0.0)
                    ))
                
                # 9. Обновляем основную информацию о токене из DexScreener
                await self.conn.execute("""
                    UPDATE tokens SET 
                        usd_price = ?,
                        liquidity = ?,
                        fdv = ?,
                        mcap = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE token_id = ?
                """, (
                    float(pair.get('priceUsd', 0)) if pair.get('priceUsd') else 0.0,
                    liquidity.get('usd', 0.0) if liquidity else 0.0,
                    pair.get('fdv', 0.0),
                    pair.get('marketCap', 0.0),
                    token_id
                ))
                
                await self.conn.commit()
                return True
                
        except Exception as e:
            print(f"Error saving DexScreener data: {e}")
            return False

    async def get_tokens_needing_analysis(self, max_checks: int = 3, limit: int = 200) -> List[str]:
        """Получить токены, которые нуждаются в анализе (без token_pair)"""
        try:
            cursor = await self.conn.execute("""
                SELECT token_address 
                FROM token_ids 
                WHERE token_pair IS NULL 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
            
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
            
        except Exception as e:
            print(f"Error getting tokens needing analysis: {e}")
            return []

    async def get_tokens(self, limit: int = 20) -> Dict[str, Any]:
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT 
                        ti.token_address,
                        ti.token_pair,
                        ti.is_honeypot,
                        ti.lp_owner,
                        ti.dev_address,
                        ti.security_analyzed_at,
                        t.name,
                        t.symbol,
                        t.icon,
                        t.decimals,
                        t.twitter,
                        t.dev,
                        t.circ_supply,
                        t.total_supply,
                        t.token_program,
                        t.launchpad,
                        t.holder_count,
                        t.usd_price,
                        t.liquidity,
                        t.fdv,
                        t.mcap,
                        t.organic_score,
                        t.organic_score_label,
                        t.bonding_curve,
                        t.price_block_id,
                        t.updated_at,
                        a.mint_authority_disabled,
                        a.freeze_authority_disabled,
                        a.top_holders_percentage,
                        s24.price_change as price_change_24h,
                        s24.buy_volume as volume_24h,
                        GROUP_CONCAT(tt.tag) as tags
                    FROM token_ids ti
                    JOIN tokens t ON t.token_id = ti.id
                    LEFT JOIN token_audit a ON a.token_id = ti.id
                    LEFT JOIN token_stats_24h s24 ON s24.token_id = ti.id
                    LEFT JOIN token_tags tt ON tt.token_id = ti.id
                    GROUP BY ti.id
                    ORDER BY t.updated_at DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = await cursor.fetchall()
                
                tokens = []
                for row in rows:
                    token = {
                        'id': row[0],
                        'token_pair': row[1],
                        'is_honeypot': row[2],
                        'lp_owner': row[3],
                        'dev_address': row[4],
                        'security_analyzed_at': row[5],
                        'name': row[6],
                        'symbol': row[7],
                        'icon': row[8],
                        'decimals': row[9],
                        'twitter': row[10],
                        'dev': row[11],
                        'circ_supply': row[12],
                        'total_supply': row[13],
                        'token_program': row[14],
                        'launchpad': row[15],
                        'holder_count': row[16],
                        'usd_price': row[17],
                        'liquidity': row[18],
                        'fdv': row[19],
                        'mcap': row[20],
                        'organic_score': row[21],
                        'organic_score_label': row[22],
                        'bonding_curve': row[23],
                        'price_block_id': row[24],
                        'updated_at': row[25],
                        'mint_authority_disabled': row[26],
                        'freeze_authority_disabled': row[27],
                        'top_holders_percentage': row[28],
                        'price_change_24h': row[29],
                        'volume_24h': row[30],
                        'tags': row[31].split(',') if row[31] else []
                    }
                    tokens.append(token)
                
                return {
                    "success": True,
                    "tokens": tokens,
                    "total_found": len(tokens)
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "tokens": [],
                "total_found": 0
            }


class AsyncJupiterScanner:
    def __init__(self, db: AsyncTokenDatabase, debug: bool = False):
        self.api_url = "https://lite-api.jup.ag/tokens/v2/recent"
        self.debug = debug
        self.db = db
        self.session: Optional[aiohttp.ClientSession] = None
        
        self.rate_limit_delay = 2.0
        self.max_retries = 3
        self.retry_delay = 5.0
        self.last_request_time = 0
        
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
    
    async def close(self):
        if self.session:
            await self.session.close()
        await self.db.close()
    
    async def save_token(self, token: Dict[str, Any]) -> bool:
        try:
            return await self.db.save_token(token)
        except Exception as e:
            return False
    
    async def get_all_tokens_from_db(self, limit: int = 100) -> Dict[str, Any]:
        try:
            await self.db.ensure_connection()
            
            async with self.db.db_lock:
                cursor = await self.db.conn.execute("""
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

    async def get_tokens_from_api(self, limit: int = 20) -> Dict[str, Any]:
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
            for token in tokens:
                if await self.save_token(token):
                    saved_count += 1
            
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
            
            formatted_tokens = []
            for token in tokens:
                formatted_tokens.append({
                    "id": safe_get(token, "id", ""),
                    "name": safe_get(token, "name", "Unknown"),
                    "mcap": safe_get(token, "mcap", 0, float),
                    "symbol": safe_get(token, "symbol", "UNKNOWN"),
                    "price": safe_get(token, "usdPrice", 0, float),
                    "holders": safe_get(token, "holderCount", 0, int),
                    "dex": "Analyzing...",
                    "pair": None
                })
            
            return {
                "success": True,
                "tokens": formatted_tokens,
                "total_found": len(formatted_tokens),
                "saved_count": saved_count,
                "scan_time": datetime.now().isoformat(),
                "replace_old": False
            }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_tokens(self, limit: int = 20) -> Dict[str, Any]:
        return await self.get_tokens_from_api(limit)