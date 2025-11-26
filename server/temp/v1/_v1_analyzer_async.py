#!/usr/bin/env python3
"""
Async Token Analyzer - Асинхронний аналізатор токенів з SQLite
Аналізує токени кожні 3 секунди, 3 ітерації для кожного токена
"""

import asyncio
import aiohttp
import aiosqlite
import json
import time
import random
from datetime import datetime
from typing import Dict, Any, Optional, List, Set
import os

# Конфіг ретраїв
RETRY_COUNT = 3
RETRY_BACKOFF_BASE = 0.4

class AsyncTokenAnalyzer:
    def __init__(self, debug: bool = False):
        self.solana_rpc_url = "https://api.mainnet-beta.solana.com"
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        self.db_path = "db/tokens.db"
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        
        # Черга аналізу токенів
        self.analysis_queue: Dict[str, Dict[str, Any]] = {}  # token_id -> {iterations_left, last_analysis}
        self.analysis_lock = asyncio.Lock()
        
        # Rate limiting для аналізатора
        self.rate_limit_delay = 1.0  # 1 секунда між аналізами
        self.last_analysis_time = 0
        
    def _debug_print(self, *args):
        if self.debug:
            print("[ANALYZER DEBUG]", *args)
    
    async def respect_rate_limit(self):
        """Ensure we don't exceed rate limits for analysis"""
        current_time = time.time()
        time_since_last_analysis = current_time - self.last_analysis_time
        
        if time_since_last_analysis < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last_analysis
            if self.debug:
                print(f"⏳ Analysis rate limiting: waiting {sleep_time:.1f}s")
            await asyncio.sleep(sleep_time)
        
        self.last_analysis_time = time.time()
    
    async def batch_analyze_tokens(self, token_addresses: List[str]) -> Dict[str, Any]:
        """Batch analyze multiple tokens using Jupiter API (up to 100 tokens per request)"""
        try:
            await self.ensure_session()
            
            if not token_addresses:
                return {}
            
            # Jupiter API supports up to 100 mint addresses in one query
            batch_size = 100
            results = {}
            
            for i in range(0, len(token_addresses), batch_size):
                batch = token_addresses[i:i + batch_size]
                
                # Create comma-separated query string
                query_string = ",".join(batch)
                url = f"https://lite-api.jup.ag/tokens/v2/search?query={query_string}"
                
                if self.debug:
                    print(f"🔍 Batch analyzing {len(batch)} tokens...")
                
                # Rate limiting before request
                await self.respect_rate_limit()
                
                async with self.session.get(url, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Process each token in the response
                        for token_data in data:
                            token_id = token_data.get('id', '')
                            if token_id:
                                results[token_id] = {
                                    'jupiter_data': token_data,
                                    'timestamp': datetime.now().isoformat()
                                }
                        
                        if self.debug:
                            print(f"✅ Batch analysis complete: {len(data)} tokens processed")
                    else:
                        error_text = await response.text()
                        if self.debug:
                            print(f"❌ Batch analysis failed: {response.status} - {error_text}")
                        return {}
            
            return results
            
        except Exception as e:
            if self.debug:
                print(f"❌ Batch analysis error: {e}")
            return {}

    async def ensure_connection(self):
        """Ensure database connection is established"""
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            
            # SQLite PRAGMA оптимізації
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA cache_size=-64000;")
            await self.conn.execute("PRAGMA temp_store=MEMORY;")
            await self.conn.execute("PRAGMA foreign_keys=ON;")
            
            await self.init_db()

    async def close(self):
        """Close all resources"""
        if self.session:
            await self.session.close()
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def init_db(self):
        """Ініціалізація таблиць аналізу токенів згідно з документацією"""
        async with self.db_lock:
            # DexScreener таблиці
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
            
            # Solana RPC таблиці
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS solana_token_supply (
                    token_id INTEGER PRIMARY KEY,
                    amount TEXT,
                    decimals INTEGER,
                    ui_amount NUMERIC,
                    ui_amount_string TEXT,
                    slot INTEGER,
                    api_version TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS solana_token_metadata (
                    token_id INTEGER PRIMARY KEY,
                    decimals INTEGER,
                    freeze_authority TEXT,
                    is_initialized BOOLEAN,
                    mint_authority TEXT,
                    supply TEXT,
                    program TEXT,
                    space INTEGER,
                    executable BOOLEAN,
                    lamports INTEGER,
                    owner TEXT,
                    rent_epoch TEXT,
                    slot INTEGER,
                    api_version TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS solana_recent_signatures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER,
                    block_time INTEGER,
                    confirmation_status TEXT,
                    err TEXT,
                    memo TEXT,
                    signature TEXT,
                    slot INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS solana_dev_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER,
                    block_time INTEGER,
                    confirmation_status TEXT,
                    err TEXT,
                    memo TEXT,
                    signature TEXT,
                    slot INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS solana_largest_accounts (
                    token_id INTEGER PRIMARY KEY,
                    error_message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES token_ids(id)
                )
            """)
            
            # Індекси для швидкості
            await self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dexscreener_pairs_timestamp 
                ON dexscreener_pairs(timestamp)
            """)
            
            await self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_solana_supply_timestamp 
                ON solana_token_supply(timestamp)
            """)
            
            await self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_solana_signatures_timestamp 
                ON solana_recent_signatures(timestamp)
            """)
            
            await self.conn.commit()

    async def ensure_session(self):
        """Ensure HTTP session is initialized"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def add_tokens_to_analysis(self, token_ids: List[str]):
        """Додати токени до черги аналізу"""
        async with self.analysis_lock:
            added_count = 0
            for token_id in token_ids:
                if token_id not in self.analysis_queue:
                    self.analysis_queue[token_id] = {
                        'iterations_left': 3,  # 3 ітерації аналізу
                        'last_analysis': None
                    }
                    added_count += 1
                    self._debug_print(f"Added {token_id} to analysis queue")
            
            self._debug_print(f"📊 Added {added_count} new tokens to analysis queue. Total queue size: {len(self.analysis_queue)}")

    async def analyze_token(self, token_address: str, iteration: int = 1) -> Dict[str, Any]:
        """Аналіз одного токена"""
        start_time = time.time()

        try:
            # Rate limiting перед аналізом
            await self.respect_rate_limit()
            
            await self.ensure_session()
            
            # Отримуємо дані з різних джерел
            jupiter_data = await self._get_jupiter_data(token_address)
            dexscreener_data = await self._get_dexscreener_data(token_address)
            solana_rpc_data = await self._get_solana_rpc_data(token_address)
            holders_data = await self._get_token_holders(token_address)

            # Honeypot check
            honeypot_check = await self._honeypot_with_fallback(token_address, dexscreener_data, solana_rpc_data)

            # Dev address detection
            dev_address = self._extract_dev_from_jupiter(jupiter_data)
            dev_activity = await self._get_dev_activity(dev_address) if dev_address else None

            # LP owner
            pair_address = self._extract_pair_from_dexscreener(dexscreener_data)
            lp_owner = await self._get_lp_owner(pair_address) if pair_address else None

            analysis_time = time.time() - start_time

            result = {
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": f"{analysis_time:.2f}s",
                "iteration": iteration,
                "raw_data": {
                    "jupiter": jupiter_data,
                    "dexscreener": dexscreener_data,
                    "solana_rpc": {
                        **solana_rpc_data,
                        "largest_accounts": holders_data,
                        "dev_activity": dev_activity
                    }
                },
                "security": {
                    "honeypot_check": honeypot_check,
                    "lp_owner": lp_owner,
                    "dev_address": dev_address
                }
            }

            # Зберігаємо аналіз в SQLite
            await self.save_analysis(result)

            return result

        except Exception as e:
            self._debug_print(f"Error analyzing token {token_address}: {str(e)}")
            return {
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": "0.00s",
                "iteration": iteration,
                "error": str(e)
            }

    async def save_analysis(self, analysis: Dict[str, Any]) -> bool:
        """Збереження аналізу в нові таблиці згідно з документацією"""
        try:
            await self.ensure_connection()
            
            token_address = analysis['token_address']
            self._debug_print(f"💾 SAVING ANALYSIS for {token_address}")
            
            # Отримуємо token_id з token_ids таблиці
            token_id = await self._get_token_id_by_address(token_address)
            if not token_id:
                self._debug_print(f"❌ Token {token_address} not found in token_ids table")
                return False
            
            async with self.db_lock:
                # Зберігаємо DexScreener дані
                await self._save_dexscreener_data(token_id, analysis['raw_data']['dexscreener'])
                
                # Зберігаємо Solana RPC дані
                await self._save_solana_rpc_data(token_id, analysis['raw_data']['solana_rpc'])
                
                # Оновлюємо token_pair в token_ids якщо знайшли пару
                pair_address = self._extract_pair_from_dexscreener(analysis['raw_data']['dexscreener'])
                if pair_address:
                    await self.conn.execute("""
                        UPDATE token_ids 
                        SET token_pair = ? 
                        WHERE id = ?
                    """, (pair_address, token_id))
                    self._debug_print(f"✅ Updated token_pair for {token_address}: {pair_address}")
                
                # Оновлюємо основні дані токена з DexScreener
                await self._update_token_data_from_dexscreener(token_id, analysis['raw_data']['dexscreener'])
                
                await self.conn.commit()
                self._debug_print(f"✅ Analysis saved successfully for {token_address}")
                return True
                
        except Exception as e:
            self._debug_print(f"Error saving analysis: {str(e)}")
            return False

    async def _get_token_id_by_address(self, token_address: str) -> Optional[int]:
        """Отримати token_id за token_address"""
        try:
            cursor = await self.conn.execute("""
                SELECT id FROM token_ids WHERE token_address = ?
            """, (token_address,))
            row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            self._debug_print(f"Error getting token_id for {token_address}: {e}")
            return None

    async def _save_dexscreener_data(self, token_id: int, dexscreener_data: Any):
        """Збереження DexScreener даних в нові таблиці"""
        try:
            if not isinstance(dexscreener_data, dict) or 'pairs' not in dexscreener_data:
                return
            
            pairs = dexscreener_data.get('pairs', [])
            if not isinstance(pairs, list) or not pairs:
                return
            
            pair = pairs[0]  # Беремо першу пару
            if not isinstance(pair, dict):
                return
            
            # Зберігаємо основну інформацію про пару
            await self.conn.execute("""
                INSERT OR REPLACE INTO dexscreener_pairs (
                    token_id, chain_id, dex_id, url, pair_address,
                    price_native, price_usd, fdv, market_cap, pair_created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token_id,
                pair.get('chainId'),
                pair.get('dexId'),
                pair.get('url'),
                pair.get('pairAddress'),
                pair.get('priceNative'),
                pair.get('priceUsd'),
                pair.get('fdv'),
                pair.get('marketCap'),
                datetime.fromtimestamp(pair.get('pairCreatedAt', 0) / 1000).isoformat() if pair.get('pairCreatedAt') else None
            ))
            
            # Зберігаємо base token
            base_token = pair.get('baseToken', {})
            if base_token:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_base_token (
                        token_id, address, name, symbol
                    ) VALUES (?, ?, ?, ?)
                """, (
                    token_id,
                    base_token.get('address'),
                    base_token.get('name'),
                    base_token.get('symbol')
                ))
            
            # Зберігаємо quote token
            quote_token = pair.get('quoteToken', {})
            if quote_token:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_quote_token (
                        token_id, address, name, symbol
                    ) VALUES (?, ?, ?, ?)
                """, (
                    token_id,
                    quote_token.get('address'),
                    quote_token.get('name'),
                    quote_token.get('symbol')
                ))
            
            # Зберігаємо транзакції
            txns = pair.get('txns', {})
            if txns:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_txns (
                        token_id, m5_buys, m5_sells, h1_buys, h1_sells,
                        h6_buys, h6_sells, h24_buys, h24_sells
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    txns.get('m5', {}).get('buys'),
                    txns.get('m5', {}).get('sells'),
                    txns.get('h1', {}).get('buys'),
                    txns.get('h1', {}).get('sells'),
                    txns.get('h6', {}).get('buys'),
                    txns.get('h6', {}).get('sells'),
                    txns.get('h24', {}).get('buys'),
                    txns.get('h24', {}).get('sells')
                ))
            
            # Зберігаємо об'єм
            volume = pair.get('volume', {})
            if volume:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_volume (
                        token_id, h24, h6, h1, m5
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    token_id,
                    volume.get('h24'),
                    volume.get('h6'),
                    volume.get('h1'),
                    volume.get('m5')
                ))
            
            # Зберігаємо зміни ціни
            price_change = pair.get('priceChange', {})
            if price_change:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_price_change (
                        token_id, m5, h1, h6, h24
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    token_id,
                    price_change.get('m5'),
                    price_change.get('h1'),
                    price_change.get('h6'),
                    price_change.get('h24')
                ))
            
            # Зберігаємо ліквідність
            liquidity = pair.get('liquidity', {})
            if liquidity:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_liquidity (
                        token_id, usd, base, quote
                    ) VALUES (?, ?, ?, ?)
                """, (
                    token_id,
                    liquidity.get('usd'),
                    liquidity.get('base'),
                    liquidity.get('quote')
                ))
            
            self._debug_print(f"✅ DexScreener data saved for token_id {token_id}")
            
        except Exception as e:
            self._debug_print(f"Error saving DexScreener data: {e}")

    async def _save_solana_rpc_data(self, token_id: int, solana_rpc_data: Dict[str, Any]):
        """Збереження Solana RPC даних в нові таблиці"""
        try:
            # Зберігаємо token supply
            token_supply = solana_rpc_data.get('token_supply', {})
            if token_supply and 'value' in token_supply:
                supply_value = token_supply['value']
                context = token_supply.get('context', {})
                await self.conn.execute("""
                    INSERT OR REPLACE INTO solana_token_supply (
                        token_id, amount, decimals, ui_amount, ui_amount_string,
                        slot, api_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    supply_value.get('amount'),
                    supply_value.get('decimals'),
                    supply_value.get('uiAmount'),
                    supply_value.get('uiAmountString'),
                    context.get('slot'),
                    context.get('apiVersion')
                ))
            
            # Зберігаємо token metadata
            token_metadata = solana_rpc_data.get('token_metadata', {})
            if token_metadata and 'value' in token_metadata:
                metadata_value = token_metadata['value']
                context = token_metadata.get('context', {})
                parsed_info = metadata_value.get('data', {}).get('parsed', {}).get('info', {})
                
                await self.conn.execute("""
                    INSERT OR REPLACE INTO solana_token_metadata (
                        token_id, decimals, freeze_authority, is_initialized,
                        mint_authority, supply, program, space, executable,
                        lamports, owner, rent_epoch, slot, api_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    parsed_info.get('decimals'),
                    parsed_info.get('freezeAuthority'),
                    parsed_info.get('isInitialized'),
                    parsed_info.get('mintAuthority'),
                    parsed_info.get('supply'),
                    metadata_value.get('data', {}).get('program'),
                    metadata_value.get('space'),
                    metadata_value.get('executable'),
                    metadata_value.get('lamports'),
                    metadata_value.get('owner'),
                    metadata_value.get('rentEpoch'),
                    context.get('slot'),
                    context.get('apiVersion')
                ))
            
            # Зберігаємо recent signatures
            recent_signatures = solana_rpc_data.get('recent_signatures', [])
            if isinstance(recent_signatures, list):
                # Спочатку видаляємо старі записи
                await self.conn.execute("""
                    DELETE FROM solana_recent_signatures WHERE token_id = ?
                """, (token_id,))
                
                # Додаємо нові
                for sig in recent_signatures:
                    if isinstance(sig, dict):
                        await self.conn.execute("""
                            INSERT INTO solana_recent_signatures (
                                token_id, block_time, confirmation_status, err, memo, signature, slot
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            token_id,
                            sig.get('blockTime'),
                            sig.get('confirmationStatus'),
                            sig.get('err'),
                            sig.get('memo'),
                            sig.get('signature'),
                            sig.get('slot')
                        ))
            
            # Зберігаємо dev activity
            dev_activity = solana_rpc_data.get('dev_activity', [])
            if isinstance(dev_activity, list):
                # Спочатку видаляємо старі записи
                await self.conn.execute("""
                    DELETE FROM solana_dev_activity WHERE token_id = ?
                """, (token_id,))
                
                # Додаємо нові
                for activity in dev_activity:
                    if isinstance(activity, dict):
                        await self.conn.execute("""
                            INSERT INTO solana_dev_activity (
                                token_id, block_time, confirmation_status, err, memo, signature, slot
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            token_id,
                            activity.get('blockTime'),
                            activity.get('confirmationStatus'),
                            activity.get('err'),
                            activity.get('memo'),
                            activity.get('signature'),
                            activity.get('slot')
                        ))
            
            # Зберігаємо largest accounts
            largest_accounts = solana_rpc_data.get('largest_accounts', {})
            if isinstance(largest_accounts, dict):
                await self.conn.execute("""
                    INSERT OR REPLACE INTO solana_largest_accounts (
                        token_id, error_message
                    ) VALUES (?, ?)
                """, (
                    token_id,
                    largest_accounts.get('error')
                ))
            
            self._debug_print(f"✅ Solana RPC data saved for token_id {token_id}")
            
        except Exception as e:
            self._debug_print(f"Error saving Solana RPC data: {e}")

    async def _update_token_data_from_dexscreener(self, token_id: int, dexscreener_data: Any):
        """Оновлення основних даних токена з DexScreener"""
        try:
            if not isinstance(dexscreener_data, dict) or 'pairs' not in dexscreener_data:
                return
            
            pairs = dexscreener_data.get('pairs', [])
            if not isinstance(pairs, list) or not pairs:
                return
            
            pair = pairs[0]
            if not isinstance(pair, dict):
                return
            
            # Оновлюємо основні дані в таблиці tokens
            await self.conn.execute("""
                UPDATE tokens SET
                    usd_price = ?,
                    liquidity = ?,
                    fdv = ?,
                    mcap = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE token_id = ?
            """, (
                pair.get('priceUsd'),
                pair.get('liquidity', {}).get('usd'),
                pair.get('fdv'),
                pair.get('marketCap'),
                token_id
            ))
            
            self._debug_print(f"✅ Token data updated from DexScreener for token_id {token_id}")
            
        except Exception as e:
            self._debug_print(f"Error updating token data from DexScreener: {e}")

    # ✅ ВИПРАВЛЕНО: Змінено тип параметра з int на str
    async def _broadcast_token_update(self, token_address: str):
        """Відправка оновлення токена на frontend через WebSocket"""
        try:
            self._debug_print(f"📡 Starting broadcast for token_address {token_address}")
            
            # Отримуємо token_id з бази даних
            token_id = await self._get_token_id_by_address(token_address)
            if not token_id:
                self._debug_print(f"❌ Token {token_address} not found in token_ids table")
                return
            
            # Отримуємо оновлені дані токена
            updated_token = await self._get_updated_token_data(token_id)
            if not updated_token:
                self._debug_print(f"❌ No updated token data found for token_id {token_id}")
                return
            
            self._debug_print(f"📊 Token data: {updated_token.get('id')} - DEX: {updated_token.get('dex')} - Pair: {updated_token.get('token_pair')}")
            
            # Імпортуємо broadcast функцію з main
            import main
            broadcast_data = {
                "success": True,
                "type": "token_update",
                "token": updated_token,
                "timestamp": datetime.now().isoformat()
            }
            
            await main.broadcast_to_clients(broadcast_data)
            
            self._debug_print(f"📡 Broadcasted token update for token_address {token_address}: {updated_token.get('dex', 'N/A')} - {updated_token.get('token_pair', 'N/A')}")
            
        except Exception as e:
            self._debug_print(f"❌ Error broadcasting token update: {e}")
            import traceback
            self._debug_print(f"Traceback: {traceback.format_exc()}")

    async def _get_updated_token_data(self, token_id: int) -> Optional[Dict[str, Any]]:
        """Отримати оновлені дані токена для broadcast"""
        try:
            cursor = await self.conn.execute("""
                SELECT 
                    ti.token_address,
                    ti.token_pair,
                    ti.is_honeypot,
                    ti.lp_owner,
                    ti.dev_address,
                    t.name,
                    t.symbol,
                    t.usd_price,
                    t.liquidity,
                    t.fdv,
                    t.mcap,
                    dp.dex_id,
                    dbt.symbol as base_symbol,
                    dqt.symbol as quote_symbol
                FROM token_ids ti
                LEFT JOIN tokens t ON t.token_id = ti.id
                LEFT JOIN dexscreener_pairs dp ON dp.token_id = ti.id
                LEFT JOIN dexscreener_base_token dbt ON dbt.token_id = ti.id
                LEFT JOIN dexscreener_quote_token dqt ON dqt.token_id = ti.id
                WHERE ti.id = ?
            """, (token_id,))
            
            row = await cursor.fetchone()
            if not row:
                return None
            
            return {
                "id": row[0],  # token_address
                "name": row[5],
                "symbol": row[6],
                "mcap": row[10],
                "holders": None,  # Можна додати з іншої таблиці
                "dex": row[11] or "Analyzing...",
                "token_pair": row[1] or "Analyzing...",
                "usd_price": row[7],
                "liquidity": row[8],
                "fdv": row[9],
                "is_honeypot": row[2],
                "lp_owner": row[3],
                "dev_address": row[4],
                "base_symbol": row[12],
                "quote_symbol": row[13]
            }
            
        except Exception as e:
            self._debug_print(f"Error getting updated token data: {e}")
            return None

    async def run_analysis_cycle(self):
        """Запуск одного циклу аналізу з batch обробкою (50 токенів за раз з ротацією)"""
        try:
            self._debug_print("📥 Loading tokens needing analysis...")
            # Спочатку додаємо токени з бази даних, які потребують аналізу
            await self.load_tokens_needing_analysis()
            
            async with self.analysis_lock:
                self._debug_print(f"📊 Analysis queue size: {len(self.analysis_queue)}")
                if not self.analysis_queue:
                    self._debug_print("⚠️ Analysis queue is empty, skipping cycle")
                    return
                
                # Показуємо перші 10 токенів в черзі для діагностики
                queue_sample = list(self.analysis_queue.items())[:10]
                self._debug_print(f"🔍 First 10 tokens in queue: {[(k, v['iterations_left']) for k, v in queue_sample]}")
                
                # Фільтруємо токени, які ще не завершили аналіз (iterations_left > 0)
                active_tokens = {
                    token_id: data for token_id, data in self.analysis_queue.items() 
                    if data['iterations_left'] > 0
                }
                
                self._debug_print(f"📊 Active tokens (iterations_left > 0): {len(active_tokens)}")
                if not active_tokens:
                    self._debug_print("⚠️ No active tokens, skipping cycle")
                    return
                
                # Беремо наступні 50 токенів для аналізу (ротація)
                tokens_to_analyze = list(active_tokens.keys())[:50]
                self._debug_print(f"🎯 Selected {len(tokens_to_analyze)} tokens for analysis")
            
            if not tokens_to_analyze:
                return
            
            # Batch аналіз через Jupiter API
            self._debug_print(f"Starting batch analysis for {len(tokens_to_analyze)} tokens (rotation)")
            batch_results = await self.batch_analyze_tokens(tokens_to_analyze)
            
            # ✅ ВИПРАВЛЕНО: Додано start_time для правильного розрахунку analysis_time
            cycle_start_time = time.time()
            
            # Обробляємо результати batch аналізу
            for token_id in tokens_to_analyze:
                try:
                    if token_id in batch_results:
                        # Отримуємо дані з batch результату
                        jupiter_data = batch_results[token_id]['jupiter_data']
                        
                        # Отримуємо додаткові дані (DexScreener, Solana RPC)
                        dexscreener_data = await self._get_dexscreener_data(token_id)
                        solana_rpc_data = await self._get_solana_rpc_data(token_id)
                        
                        # Створюємо повний аналіз
                        iteration = 4 - self.analysis_queue[token_id]['iterations_left']  # 1, 2, 3
                        
                        # ✅ ВИПРАВЛЕНО: Правильний розрахунок analysis_time
                        analysis_time = time.time() - cycle_start_time
                        
                        analysis = {
                            'token_address': token_id,
                            'timestamp': datetime.now().isoformat(),
                            'analysis_time': f"{analysis_time:.2f}s",
                            'iteration': iteration,
                            'raw_data': {
                                'jupiter': jupiter_data,
                                'dexscreener': dexscreener_data,
                                'solana_rpc': solana_rpc_data
                            },
                            'security': {
                                # ✅ ВИПРАВЛЕНО: Використовуємо детальний honeypot check
                                'honeypot_check': await self._honeypot_with_fallback(token_id, dexscreener_data, solana_rpc_data),
                                # ✅ ВИПРАВЛЕНО: Правильні параметри для LP owner
                                'lp_owner': await self._get_lp_owner(self._extract_pair_from_dexscreener(dexscreener_data)) if self._extract_pair_from_dexscreener(dexscreener_data) else None,
                                'dev_address': self._get_dev_address(jupiter_data)
                            }
                        }
                        
                        # Детальне логування аналізу
                        self._debug_print(f"🔍 ANALYSIS DATA for {token_id} (iteration {iteration}):")
                        self._debug_print(f"  📊 Jupiter data keys: {list(jupiter_data.keys()) if isinstance(jupiter_data, dict) else 'Not a dict'}")
                        self._debug_print(f"  📊 DexScreener data keys: {list(dexscreener_data.keys()) if isinstance(dexscreener_data, dict) else 'Not a dict'}")
                        self._debug_print(f"  📊 Solana RPC data keys: {list(solana_rpc_data.keys()) if isinstance(solana_rpc_data, dict) else 'Not a dict'}")
                        
                        # Логування DexScreener структури
                        if isinstance(dexscreener_data, dict) and 'pairs' in dexscreener_data:
                            pairs = dexscreener_data.get('pairs', [])
                            self._debug_print(f"  🔗 DexScreener pairs count: {len(pairs)}")
                            if pairs and isinstance(pairs, list):
                                first_pair = pairs[0]
                                if isinstance(first_pair, dict):
                                    self._debug_print(f"  🔗 First pair keys: {list(first_pair.keys())}")
                                    self._debug_print(f"  🔗 DexId: {first_pair.get('dexId', 'NOT_FOUND')}")
                        
                        # Зберігаємо аналіз
                        await self.save_analysis(analysis)
                        
                        # Оновлюємо час останнього аналізу
                        await self.conn.execute("""
                            UPDATE token_ids 
                            SET security_analyzed_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (token_id,))
                        
                        # ✅ ВИПРАВЛЕНО: Передаємо token_address замість token_id
                        await self._broadcast_token_update(token_id)
                        
                        self._debug_print(f"Batch analysis completed for {token_id}, iteration {iteration}")
                    else:
                        self._debug_print(f"No batch data for {token_id}")
                        
                except Exception as e:
                    self._debug_print(f"Error processing {token_id}: {e}")
            
            # Оновлюємо чергу
            async with self.analysis_lock:
                for token_id in tokens_to_analyze:
                    if token_id in self.analysis_queue:
                        self.analysis_queue[token_id]['iterations_left'] -= 1
                        self.analysis_queue[token_id]['last_analysis'] = datetime.now()
                        
                        # Видаляємо токени, які завершили всі ітерації
                        if self.analysis_queue[token_id]['iterations_left'] <= 0:
                            del self.analysis_queue[token_id]
                            self._debug_print(f"Removed {token_id} from analysis queue (completed)")
            
            self._debug_print(f"Batch analysis cycle complete. Queue size: {len(self.analysis_queue)} (active: {len(active_tokens)})")
            
        except Exception as e:
            self._debug_print(f"Analysis cycle error: {e}")

    async def load_tokens_needing_analysis(self):
        """Завантаження токенів з бази даних, які потребують аналізу"""
        try:
            # Використовуємо глобальний екземпляр бази даних
            import main
            db = main.db_instance
            await db.ensure_connection()
            
            # Отримуємо токени, які ще не мають token_pair (не аналізовані)
            tokens_needing_analysis = await db.get_tokens_needing_analysis(max_checks=3, limit=200)
            
            self._debug_print(f"📊 Found {len(tokens_needing_analysis)} tokens needing analysis")
            
            if tokens_needing_analysis:
                self._debug_print(f"📋 Tokens needing analysis: {tokens_needing_analysis[:3]}...")
            
            # Додаємо їх до черги аналізу
            added_count = 0
            for token_address in tokens_needing_analysis:
                async with self.analysis_lock:
                    if token_address not in self.analysis_queue:
                        self.analysis_queue[token_address] = {
                            'iterations_left': 3,
                            'last_analysis': None
                        }
                        added_count += 1
                        self._debug_print(f"Added {token_address} to analysis queue")
            
            self._debug_print(f"📊 Added {added_count} tokens from DB to analysis queue. Total queue size: {len(self.analysis_queue)}")
            
        except Exception as e:
            self._debug_print(f"Error loading tokens needing analysis: {str(e)}")
            import traceback
            self._debug_print(f"Traceback: {traceback.format_exc()}")

    async def start_analysis_loop(self):
        """Запуск основного циклу аналізу з ротацією"""
        try:
            self._debug_print("🚀 Starting analysis loop with rotation (50 tokens every 3 seconds)...")
            
            cycle_count = 0
            while True:
                try:
                    cycle_count += 1
                    self._debug_print(f"🔄 Analysis cycle #{cycle_count} starting...")
                    await self.run_analysis_cycle()
                    self._debug_print(f"✅ Analysis cycle #{cycle_count} completed, sleeping for 3 seconds...")
                    await asyncio.sleep(3)  # 3 секунди між циклами (ротація)
                except Exception as e:
                    self._debug_print(f"❌ Error in analysis loop cycle #{cycle_count}: {str(e)}")
                    import traceback
                    self._debug_print(f"Traceback: {traceback.format_exc()}")
                    await asyncio.sleep(3)
        except Exception as e:
            print(f"❌ Critical error in analysis loop: {e}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")

    # Методи для отримання даних (адаптовані з _v1_analyzer_SQLite.py)
    
    async def _fetch_with_retries(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """Універсальна GET з ретраями"""
        last_exc = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                self._debug_print(f"fetch try {attempt} -> {url}")
                async with self.session.get(url, **kwargs) as resp:
                    status = resp.status
                    text = await resp.text()
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": parsed, "text": text}
                    else:
                        return {"ok": False, "status": status, "json": parsed, "text": text,
                                "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                self._debug_print(f"fetch error {e}, backoff {backoff:.2f}s")
                await asyncio.sleep(backoff)
        return {"ok": False, "status": None, "json": None, "text": None, "error": str(last_exc)}

    async def _post_rpc_with_retries(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """RPC POST з ретраями"""
        last_exc = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                self._debug_print("rpc try", attempt, payload.get("method"))
                async with self.session.post(self.solana_rpc_url, json=payload, timeout=10) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
                    if 200 <= status < 300:
                        return {"ok": True, "status": status, "json": data}
                    else:
                        return {"ok": False, "status": status, "json": data, "error": f"HTTP {status}"}
            except Exception as e:
                last_exc = e
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                self._debug_print(f"rpc error {e}, backoff {backoff:.2f}s")
                await asyncio.sleep(backoff)
        return {"ok": False, "status": None, "json": None, "error": str(last_exc)}

    async def _get_jupiter_data(self, token_address: str) -> Any:
        try:
            url = f"https://lite-api.jup.ag/tokens/v2/search?query={token_address}"
            self._debug_print(f"🪐 Fetching Jupiter data for {token_address}")
            res = await self._fetch_with_retries("GET", url, headers={"User-Agent": "Mozilla/5.0"})
            if res["ok"]:
                data = res["json"]
                if isinstance(data, list) and data:
                    self._debug_print(f"  🪐 Jupiter returned {len(data)} tokens")
                    first_token = data[0]
                    if isinstance(first_token, dict):
                        self._debug_print(f"  🪐 First token name: {first_token.get('name', 'UNKNOWN')}")
                        self._debug_print(f"  🪐 First token symbol: {first_token.get('symbol', 'UNKNOWN')}")
                return data
            else:
                self._debug_print(f"  ❌ Jupiter error: {res.get('error')}")
                return {"error": res.get("error") or f"HTTP {res.get('status')}"}
        except Exception as e:
            self._debug_print(f"  ❌ Jupiter exception: {str(e)}")
            return {"error": str(e)}

    async def _get_dexscreener_data(self, token_address: str) -> Any:
        try:
            url = f"https://api.dexscreener.com/latest/dex/search/?q={token_address}"
            self._debug_print(f"🔗 Fetching DexScreener data for {token_address}")
            res = await self._fetch_with_retries("GET", url)
            if res["ok"]:
                data = res["json"]
                if isinstance(data, dict) and 'pairs' in data:
                    pairs = data.get('pairs', [])
                    self._debug_print(f"  🔗 DexScreener returned {len(pairs)} pairs")
                    
                    # Якщо pairs порожній, спробуємо альтернативний API
                    if not pairs or len(pairs) == 0:
                        self._debug_print(f"  ⚠️ Empty pairs, trying alternative DexScreener API...")
                        alt_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                        alt_res = await self._fetch_with_retries("GET", alt_url)
                        if alt_res["ok"] and isinstance(alt_res["json"], dict):
                            alt_data = alt_res["json"]
                            if 'pairs' in alt_data and alt_data['pairs']:
                                self._debug_print(f"  ✅ Alternative API returned {len(alt_data['pairs'])} pairs")
                                return alt_data
                    
                    if pairs and isinstance(pairs, list):
                        first_pair = pairs[0]
                        if isinstance(first_pair, dict):
                            self._debug_print(f"  🔗 First pair dexId: {first_pair.get('dexId', 'MISSING')}")
                return data
            else:
                self._debug_print(f"  ❌ DexScreener error: {res.get('error')}")
                return {"error": res.get("error") or f"HTTP {res.get('status')}"}
        except Exception as e:
            self._debug_print(f"  ❌ DexScreener exception: {str(e)}")
            return {"error": str(e)}

    async def _get_solana_rpc_data(self, token_address: str) -> Dict[str, Any]:
        rpc_data: Dict[str, Any] = {}
        
        # getAccountInfo
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "json"}]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_account_info"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getTokenSupply
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token_address]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_supply"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getAccountInfo jsonParsed (metadata)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "jsonParsed"}]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_metadata"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # getSignaturesForAddress (recent)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [token_address, {"limit": 12}]}
        res = await self._post_rpc_with_retries(payload)
        signatures = res["json"].get("result") if res["ok"] and res.get("json") else []
        rpc_data["recent_signatures"] = signatures

        # Fetch transactions for analysis
        txs = []
        if isinstance(signatures, list):
            for sig_item in signatures[:6]:
                sig = sig_item.get("signature") if isinstance(sig_item, dict) else sig_item
                if not sig:
                    continue
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, {"encoding": "jsonParsed"}]}
                r = await self._post_rpc_with_retries(payload)
                if r["ok"] and r.get("json"):
                    txs.append(r["json"].get("result"))
        rpc_data["recent_transactions_parsed"] = txs
        
        return rpc_data

    async def _get_token_holders(self, token_address: str) -> Dict[str, Any]:
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [token_address]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                result = res["json"].get("result")
                if isinstance(result, dict):
                    val = result.get("value")
                    if isinstance(val, list):
                        return {"value": val}
                if isinstance(result, list):
                    return {"value": result}
            return {"error": res.get("error") or "no_result"}
        except Exception as e:
            return {"error": str(e)}

    async def _get_dev_activity(self, dev_address: str) -> Optional[List[Dict[str, Any]]]:
        if not dev_address:
            return None
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [dev_address, {"limit": 10}]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                return res["json"].get("result")
            return None
        except Exception:
            return None

    async def _get_lp_owner(self, pair_address: str) -> Optional[str]:
        if not pair_address:
            return None
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [pair_address, {"encoding": "jsonParsed"}]}
            res = await self._post_rpc_with_retries(payload)
            if res["ok"] and res.get("json"):
                account = res["json"].get("result", {}).get("value")
                if isinstance(account, dict):
                    return account.get("owner")
            return None
        except Exception:
            return None

    async def _honeypot_with_fallback(self, token_address: str, dexscreener_data: Any, solana_rpc_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Honeypot check з оптимізованим fallback
        
        Стратегія:
        1. Jupiter Quote API (основний метод - найточніший)
        2. Solana RPC transactions (fallback для молодих токенів)
        
        Пропускаємо DexScreener для молодих токенів (< 5 хвилин),
        бо в них ще немає історії транзакцій
        """
        result = {
            "checked_by": [],
            "buy_possible": None,
            "sell_possible": None,
            "honeypot": None,
            "reasons": [],
            "token_age_seconds": None
        }

        # 1️⃣ МЕТОД 1: Jupiter Quote API (основний)
        self._debug_print("🔍 Honeypot check: trying Jupiter Quote API...")
        try:
            # Спробуємо купити токен (SOL → Token)
            # Використовуємо lite-api.jup.ag/swap/v1/quote (новий endpoint)
            quote_buy_url = f"https://lite-api.jup.ag/swap/v1/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=100000000&slippageBps=50&restrictIntermediateTokens=true"
            # Спробуємо продати токен (Token → SOL)
            quote_sell_url = f"https://lite-api.jup.ag/swap/v1/quote?inputMint={token_address}&outputMint=So11111111111111111111111111111111111111112&amount=100000000&slippageBps=50&restrictIntermediateTokens=true"
            
            buy_res = await self._fetch_with_retries("GET", quote_buy_url)
            sell_res = await self._fetch_with_retries("GET", quote_sell_url)
            
            # Перевіряємо чи є outAmount (сума яку отримаємо)
            can_buy = False
            can_sell = False
            
            if buy_res["ok"] and buy_res.get("json"):
                buy_data = buy_res["json"]
                can_buy = bool(buy_data.get('outAmount')) and not buy_data.get('error')
                
            if sell_res["ok"] and sell_res.get("json"):
                sell_data = sell_res["json"]
                can_sell = bool(sell_data.get('outAmount')) and not sell_data.get('error')
            
            # Якщо хоча б один запит успішний - використовуємо результат
            if buy_res["ok"] or sell_res["ok"]:
                result["checked_by"] = ["jupiter_quote_api"]
                result["buy_possible"] = can_buy
                result["sell_possible"] = can_sell
                result["honeypot"] = not can_sell  # Якщо не можна продати - honeypot
                
                if can_buy and can_sell:
                    result["reasons"].append("✅ Jupiter: can BUY and SELL - NOT honeypot")
                elif can_buy and not can_sell:
                    result["reasons"].append("⚠️ Jupiter: can BUY but CANNOT SELL - HONEYPOT!")
                elif not can_buy and can_sell:
                    result["reasons"].append("⚠️ Jupiter: CANNOT BUY but can SELL - unusual")
                else:
                    result["reasons"].append("❌ Jupiter: CANNOT BUY and CANNOT SELL - check liquidity")
                
                self._debug_print(f"✅ Jupiter check: buy={can_buy}, sell={can_sell}, honeypot={result['honeypot']}")
                return result
                
        except Exception as e:
            self._debug_print(f"❌ Jupiter Quote API error: {e}")
            result["reasons"].append(f"Jupiter API error: {str(e)}")

        # 2️⃣ FALLBACK: Solana RPC transactions (для молодих токенів)
        # Пропускаємо якщо solana_rpc_data порожній (швидка перевірка)
        if not solana_rpc_data or not solana_rpc_data.get("recent_transactions_parsed"):
            self._debug_print("⚠️ No RPC data provided, skipping fallback")
            if not result["checked_by"]:
                result["checked_by"] = ["jupiter_quote_api_failed"]
                result["reasons"].append("⚠️ Jupiter failed and no RPC data for fallback")
                result["honeypot"] = None
            return result
        
        self._debug_print("🔍 Honeypot check: fallback to RPC transactions...")
        try:
            parsed_txs = solana_rpc_data.get("recent_transactions_parsed", [])
            sells_found = 0
            buys_found = 0
            
            for tx in parsed_txs:
                if not isinstance(tx, dict):
                    continue
                    
                meta = tx.get("meta") or {}
                post_token_balances = meta.get("postTokenBalances") or []
                pre_token_balances = meta.get("preTokenBalances") or []
                
                try:
                    for i_pre in pre_token_balances:
                        for i_post in post_token_balances:
                            if i_pre.get("mint") == i_post.get("mint") == token_address:
                                pre_amount = float(i_pre.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                post_amount = float(i_post.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                
                                if post_amount < pre_amount:
                                    sells_found += 1  # Баланс зменшився = продаж
                                elif post_amount > pre_amount:
                                    buys_found += 1   # Баланс збільшився = купівля
                                break
                except Exception:
                    pass
            
            result["checked_by"].append("rpc_recent_txs")
            result["buy_possible"] = buys_found > 0
            result["sell_possible"] = sells_found > 0
            result["honeypot"] = not (sells_found > 0)
            
            if sells_found > 0:
                result["reasons"].append(f"✅ RPC: found {sells_found} sells, {buys_found} buys - NOT honeypot")
            else:
                result["reasons"].append(f"⚠️ RPC: found 0 sells, {buys_found} buys - possibly honeypot or very new token")
            
            self._debug_print(f"✅ RPC check: sells={sells_found}, buys={buys_found}, honeypot={result['honeypot']}")
            
        except Exception as e:
            self._debug_print(f"❌ RPC fallback error: {e}")
            result["reasons"].append(f"RPC error: {str(e)}")

        # Якщо жоден метод не спрацював
        if not result["checked_by"]:
            result["checked_by"] = ["none"]
            result["reasons"].append("⚠️ All methods failed - network issues or APIs down")
            result["honeypot"] = None

        return result

    def _extract_dev_from_jupiter(self, jupiter_data: Any) -> Optional[str]:
        try:
            if isinstance(jupiter_data, list) and jupiter_data:
                first = jupiter_data[0]
                return first.get("dev") or first.get("dev_address") or first.get("devAddress")
            if isinstance(jupiter_data, dict):
                if "dev_address" in jupiter_data:
                    return jupiter_data.get("dev_address")
                for k in ("dev", "dev_address", "devAddress"):
                    if k in jupiter_data:
                        return jupiter_data.get(k)
            return None
        except Exception:
            return None

    def _extract_pair_from_dexscreener(self, dexscreener_data: Any) -> Optional[str]:
        try:
            if isinstance(dexscreener_data, dict):
                pairs = dexscreener_data.get("pairs") or []
                if isinstance(pairs, list) and pairs:
                    p0 = pairs[0]
                    return p0.get("pairAddress") or p0.get("pairAddress".lower())
            return None
        except Exception:
            return None

    def _check_honeypot(self, jupiter_data: Any) -> Dict[str, Any]:
        """Простий honeypot check на основі Jupiter даних"""
        try:
            if isinstance(jupiter_data, list) and jupiter_data:
                token = jupiter_data[0]
                # Перевіряємо чи є продажі
                stats = token.get('stats24h', {})
                sells = stats.get('numSells', 0)
                return {
                    "checked_by": ["jupiter_stats"],
                    "buy_possible": True,
                    "sell_possible": sells > 0,
                    "honeypot": sells == 0,
                    "reasons": [f"Jupiter stats: {sells} sells in 24h"]
                }
            return {
                "checked_by": ["none"],
                "buy_possible": None,
                "sell_possible": None,
                "honeypot": None,
                "reasons": ["No Jupiter data available"]
            }
        except Exception as e:
            return {
                "checked_by": ["error"],
                "buy_possible": None,
                "sell_possible": None,
                "honeypot": None,
                "reasons": [f"Error: {str(e)}"]
            }

    def _get_lp_owner(self, solana_rpc_data: Dict[str, Any]) -> Optional[str]:
        """Отримати LP owner з Solana RPC даних"""
        try:
            # Шукаємо в largest_accounts
            largest_accounts = solana_rpc_data.get('largest_accounts', {})
            if isinstance(largest_accounts, dict) and 'value' in largest_accounts:
                accounts = largest_accounts['value']
                if isinstance(accounts, list) and accounts:
                    # Перший аккаунт зазвичай LP
                    return accounts[0].get('address')
            return None
        except Exception:
            return None

    def _get_dev_address(self, jupiter_data: Any) -> Optional[str]:
        """Отримати dev address з Jupiter даних"""
        try:
            if isinstance(jupiter_data, list) and jupiter_data:
                token = jupiter_data[0]
                return token.get('dev')
            return None
        except Exception:
            return None
    
    async def analyze_risk_quick(self, token_address: str) -> Dict[str, Any]:
        """
        🚨 ШВИДКИЙ АНАЛІЗ РИЗИКІВ ТОКЕНА
        
        Перевіряє ТІЛЬКИ honeypot без зайвих запитів:
        ✅ Jupiter Quote API (2 запити) - BUY/SELL check
        
        БЕЗ зайвих запитів до Solana RPC!
        
        Використовується для швидкої перевірки перед купівлею
        """
        start_time = time.time()
        
        try:
            await self.ensure_session()
            
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"🚨 QUICK HONEYPOT CHECK: {token_address}")
            self._debug_print(f"{'='*60}")
            
            # 🎯 ТІЛЬКИ Jupiter honeypot check (2 запити)
            self._debug_print("\n🔍 Honeypot check (Jupiter Quote API)...")
            honeypot_result = await self._honeypot_with_fallback(
                token_address,
                {},  # Не використовуємо DexScreener
                {}   # Не використовуємо Solana RPC (fallback відключений)
            )
            
            # ⚡ Якщо honeypot=TRUE → одразу повертаємо CRITICAL
            if honeypot_result.get('honeypot') is True:
                analysis_time = time.time() - start_time
                result = {
                    "success": True,
                    "token_address": token_address,
                    "timestamp": datetime.now().isoformat(),
                    "analysis_time": f"{analysis_time:.2f}s",
                    "risk_analysis": {
                        "honeypot_check": honeypot_result,
                        "token_age_seconds": None,
                        "token_created_at": None,
                        "is_very_new": None
                    },
                    "risk_level": "CRITICAL"
                }
                
                self._debug_print(f"\n{'='*60}")
                self._debug_print(f"⛔ HONEYPOT DETECTED - STOPPING")
                self._debug_print(f"   Risk level: CRITICAL")
                self._debug_print(f"{'='*60}\n")
                
                return result
            
            # ✅ Honeypot=FALSE → токен безпечний
            analysis_time = time.time() - start_time
            
            result = {
                "success": True,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": f"{analysis_time:.2f}s",
                "risk_analysis": {
                    "honeypot_check": honeypot_result,
                    "token_age_seconds": None,  # Не запитуємо для швидкості
                    "token_created_at": None,
                    "is_very_new": None
                },
                "risk_level": self._calculate_risk_level(honeypot_result, None)
            }
            
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"✅ HONEYPOT CHECK COMPLETE")
            self._debug_print(f"   Honeypot: {honeypot_result.get('honeypot')}")
            self._debug_print(f"   Risk level: {result['risk_level']}")
            self._debug_print(f"{'='*60}\n")
            
            return result
            
        except Exception as e:
            self._debug_print(f"❌ Error in quick risk analysis: {e}")
            import traceback
            return {
                "success": False,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "traceback": traceback.format_exc()
            }
    
    async def analyze_token_full(self, token_address: str) -> Dict[str, Any]:
        """
        📊 ПОВНИЙ АНАЛІЗ ТОКЕНА (оптимізована версія)
        
        Послідовність (з early exit):
        1️⃣ Jupiter Honeypot Check → якщо TRUE → СТОП ⛔
        2️⃣ Jupiter Token Info → name, symbol, dev
        3️⃣ DexScreener → торгова пара, ліквідність
        4️⃣ Solana RPC → supply, metadata (опціонально)
        """
        start_time = time.time()
        
        try:
            await self.ensure_session()
            
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"📊 FULL TOKEN ANALYSIS: {token_address}")
            self._debug_print(f"{'='*60}")
            
            # 1️⃣ КРОК 1: Honeypot check (КРИТИЧНО!)
            self._debug_print("\n1️⃣ Step 1: Honeypot check...")
            honeypot_result = await self._honeypot_with_fallback(token_address, {}, {})
            
            # ⚠️ Якщо honeypot=TRUE → одразу повертаємо і зупиняємо аналіз
            if honeypot_result.get('honeypot') is True:
                analysis_time = time.time() - start_time
                self._debug_print(f"\n⛔ HONEYPOT DETECTED! Stopping analysis.")
                
                return {
                    "success": True,
                    "token_address": token_address,
                    "timestamp": datetime.now().isoformat(),
                    "analysis_time": f"{analysis_time:.2f}s",
                    "risk_level": "CRITICAL",
                    "honeypot_check": honeypot_result,
                    "jupiter_data": None,
                    "dexscreener_data": None,
                    "solana_rpc_data": None,
                    "stopped_at": "honeypot_check",
                    "reason": "Token is honeypot - stopped analysis"
                }
            
            # ✅ Honeypot=FALSE → продовжуємо
            self._debug_print(f"✅ NOT honeypot, continuing analysis...")
            
            # 2️⃣ КРОК 2: Jupiter token info
            self._debug_print("\n2️⃣ Step 2: Jupiter token info...")
            jupiter_data = await self._get_jupiter_data(token_address)
            
            # 3️⃣ КРОК 3: DexScreener
            self._debug_print("\n3️⃣ Step 3: DexScreener data...")
            dexscreener_data = await self._get_dexscreener_data(token_address)
            
            # 4️⃣ КРОК 4: Solana RPC (базова інформація - 2 запити)
            self._debug_print("\n4️⃣ Step 4: Solana RPC (basic info)...")
            solana_rpc_data = await self._get_solana_rpc_basic(token_address)
            
            # Формуємо результат
            analysis_time = time.time() - start_time
            
            # Витягуємо dev address
            dev_address = self._extract_dev_from_jupiter(jupiter_data)
            
            # Витягуємо pair address
            pair_address = self._extract_pair_from_dexscreener(dexscreener_data)
            
            result = {
                "success": True,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": f"{analysis_time:.2f}s",
                "risk_level": self._calculate_risk_level(honeypot_result, None),
                "security": {
                    "honeypot_check": honeypot_result,
                    "dev_address": dev_address,
                    "pair_address": pair_address
                },
                "jupiter_data": jupiter_data,
                "dexscreener_data": dexscreener_data,
                "solana_rpc_data": solana_rpc_data
            }
            
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"✅ FULL ANALYSIS COMPLETE")
            self._debug_print(f"   Honeypot: {honeypot_result.get('honeypot')}")
            self._debug_print(f"   Dev address: {dev_address or 'N/A'}")
            self._debug_print(f"   Pair address: {pair_address or 'N/A'}")
            self._debug_print(f"   Risk level: {result['risk_level']}")
            self._debug_print(f"   Time: {analysis_time:.2f}s")
            self._debug_print(f"{'='*60}\n")
            
            return result
            
        except Exception as e:
            self._debug_print(f"❌ Error in full analysis: {e}")
            import traceback
            return {
                "success": False,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "traceback": traceback.format_exc()
            }
    
    async def _get_solana_rpc_basic(self, token_address: str) -> Dict[str, Any]:
        """
        Базова інформація з Solana RPC (тільки 2 запити)
        
        Отримує:
        - Token supply (кількість токенів)
        - Token metadata (decimals, mint authority)
        """
        rpc_data: Dict[str, Any] = {}
        
        # 1. getTokenSupply
        self._debug_print("   → getTokenSupply")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token_address]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_supply"] = res["json"].get("result") if res["ok"] and res.get("json") else None

        # 2. getAccountInfo jsonParsed (metadata)
        self._debug_print("   → getAccountInfo (metadata)")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token_address, {"encoding": "jsonParsed"}]}
        res = await self._post_rpc_with_retries(payload)
        rpc_data["token_metadata"] = res["json"].get("result") if res["ok"] and res.get("json") else None
        
        return rpc_data
    
    def _calculate_risk_level(self, honeypot_result: Dict[str, Any], token_age_seconds: Optional[int]) -> str:
        """
        Розрахунок рівня ризику
        
        Returns:
            "CRITICAL" - дуже небезпечно
            "HIGH" - високий ризик
            "MEDIUM" - середній ризик
            "LOW" - низький ризик
            "UNKNOWN" - недостатньо даних
        """
        # Перевірка honeypot
        is_honeypot = honeypot_result.get('honeypot')
        
        if is_honeypot is True:
            return "CRITICAL"  # Точно honeypot
        
        # Перевірка віку токена
        if token_age_seconds:
            if token_age_seconds < 60:  # < 1 хвилини
                return "HIGH"  # Дуже молодий, немає історії
            elif token_age_seconds < 300:  # < 5 хвилин
                return "MEDIUM"  # Молодий, мало даних
        
        # Перевірка методів
        checked_by = honeypot_result.get('checked_by', [])
        if 'jupiter_quote_api' in checked_by and is_honeypot is False:
            return "LOW"  # Jupiter підтвердив безпечність
        
        if is_honeypot is False:
            return "LOW"  # NOT honeypot
        
        return "UNKNOWN"  # Недостатньо даних

# Глобальний екземпляр аналізатора
analyzer_instance: Optional[AsyncTokenAnalyzer] = None

async def get_analyzer() -> AsyncTokenAnalyzer:
    """Отримати глобальний екземпляр аналізатора"""
    global analyzer_instance
    if analyzer_instance is None:
        analyzer_instance = AsyncTokenAnalyzer(debug=True)
    return analyzer_instance

async def start_analyzer():
    """Запустити аналізатор"""
    try:
        print("🔍 Initializing analyzer...")
        analyzer = await get_analyzer()
        print("🚀 Starting analyzer background task...")
        # Запускаємо аналізатор в окремій задачі, щоб не блокувати сервер
        asyncio.create_task(analyzer.start_analysis_loop())
        print("✅ Analyzer started successfully")
    except Exception as e:
        print(f"❌ Error starting analyzer: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")

async def add_tokens_for_analysis(token_addresses: List[str]):
    """Додати токени для аналізу"""
    print(f"🔍 Adding {len(token_addresses)} tokens for analysis: {token_addresses[:3]}...")
    analyzer = await get_analyzer()
    await analyzer.add_tokens_to_analysis(token_addresses)
    print(f"✅ Added tokens to analysis queue. Queue size: {len(analyzer.analysis_queue)}")

async def stop_analyzer():
    """Зупинити аналізатор"""
    global analyzer_instance
    if analyzer_instance:
        await analyzer_instance.close()
        analyzer_instance = None
