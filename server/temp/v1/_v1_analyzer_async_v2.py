#!/usr/bin/env python3
"""
Async Token Analyzer V2 - тільки логіка аналізу + БД
"""

import asyncio
import aiosqlite
import time
from datetime import datetime
from typing import Dict, Any, Optional

# Імпорти аналізаторів
from _v1_analyzer_jupiter_async import get_jupiter_analyzer
from _v2_analyzer_dexscreener import get_dexscreener_analyzer
from _v1_analyzer_solana_rpc_async import get_solana_rpc_analyzer

class AsyncTokenAnalyzer:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.db_path = "db/tokens.db"
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        
    def _debug_print(self, *args):
        if self.debug:
            print("[ANALYZER DEBUG]", *args)
    
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
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def init_db(self):
        """Ініціалізація таблиць аналізу токенів"""
        async with self.db_lock:
            # Головна таблиця token_ids (якщо не існує)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS token_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT UNIQUE NOT NULL,
                    token_pair TEXT,
                    is_honeypot BOOLEAN,
                    lp_owner TEXT,
                    dev_address TEXT,
                    pattern TEXT,
                    security_analyzed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
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
            
            await self.conn.commit()

    async def analyze_risk_quick(self, token_address: str) -> Dict[str, Any]:
        """ШВИДКА ПЕРЕВІРКА HONEYPOT (тільки Jupiter)"""
        start_time = time.time()
        
        try:
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"🚨 QUICK HONEYPOT CHECK: {token_address}")
            self._debug_print(f"{'='*60}")
            
            # Отримуємо Jupiter аналізатор
            jupiter = await get_jupiter_analyzer()
            await jupiter.ensure_session()
            
            # Jupiter honeypot check
            self._debug_print("\n🔍 Honeypot check (Jupiter Quote API)...")
            honeypot_result = await jupiter.check_honeypot(token_address)
            
            # Якщо honeypot=TRUE → одразу повертаємо CRITICAL
            if honeypot_result.get('honeypot') is True:
                analysis_time = time.time() - start_time
                result = {
                    "success": True,
                    "token_address": token_address,
                    "timestamp": datetime.now().isoformat(),
                    "analysis_time": f"{analysis_time:.2f}s",
                    "risk_analysis": {
                        "honeypot_check": honeypot_result
                    },
                    "risk_level": "CRITICAL"
                }
                
                self._debug_print(f"\n⛔ HONEYPOT DETECTED - CRITICAL")
                return result
            
            # Honeypot=FALSE → токен безпечний
            analysis_time = time.time() - start_time
            
            result = {
                "success": True,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": f"{analysis_time:.2f}s",
                "risk_analysis": {
                    "honeypot_check": honeypot_result
                },
                "risk_level": "LOW"
            }
            
            self._debug_print(f"\n✅ NOT HONEYPOT - LOW RISK")
            return result
            
        except Exception as e:
            self._debug_print(f"❌ Error: {e}")
            import traceback
            return {
                "success": False,
                "token_address": token_address,
                "error": str(e),
                "traceback": traceback.format_exc()
            }

    async def analyze_token_full(self, token_address: str, save_to_db: bool = False) -> Dict[str, Any]:
        """ПОВНИЙ АНАЛІЗ ТОКЕНА"""
        start_time = time.time()
        
        try:
            self._debug_print(f"\n{'='*60}")
            self._debug_print(f"📊 FULL TOKEN ANALYSIS: {token_address}")
            self._debug_print(f"{'='*60}")
            
            # Отримуємо аналізатори
            jupiter = await get_jupiter_analyzer()
            dexscreener = await get_dexscreener_analyzer()
            solana_rpc = await get_solana_rpc_analyzer()
            
            await jupiter.ensure_session()
            await dexscreener.ensure_session()
            await solana_rpc.ensure_session()
            
            # 1️⃣ Honeypot check
            self._debug_print("\n1️⃣ Step 1: Honeypot check...")
            honeypot_result = await jupiter.check_honeypot(token_address)
            
            # ⚠️ Якщо honeypot → СТОП
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
                    "stopped_at": "honeypot_check"
                }
            
            # ✅ NOT honeypot → продовжуємо
            self._debug_print(f"✅ NOT honeypot, continuing...")
            
            # 2️⃣ Jupiter token info
            self._debug_print("\n2️⃣ Step 2: Jupiter token info...")
            jupiter_data = await jupiter.get_token_info(token_address)
            
            # 3️⃣ DexScreener
            self._debug_print("\n3️⃣ Step 3: DexScreener data...")
            dexscreener_data = await dexscreener.get_token_data(token_address)
            
            # 4️⃣ Solana RPC (тільки базова інфа)
            self._debug_print("\n4️⃣ Step 4: Solana RPC (basic)...")
            solana_rpc_data = await solana_rpc.get_basic_data(token_address)
            
            # Витягуємо важливі дані
            dev_address = jupiter.extract_dev_address(jupiter_data)
            pair_address = dexscreener.extract_pair_address(dexscreener_data)
            
            analysis_time = time.time() - start_time
            
            result = {
                "success": True,
                "token_address": token_address,
                "timestamp": datetime.now().isoformat(),
                "analysis_time": f"{analysis_time:.2f}s",
                "risk_level": "LOW",
                "security": {
                    "honeypot_check": honeypot_result,
                    "dev_address": dev_address,
                    "pair_address": pair_address
                },
                "jupiter_data": jupiter_data,
                "dexscreener_data": dexscreener_data,
                "solana_rpc_data": solana_rpc_data
            }
            
            # Зберігаємо в БД якщо потрібно
            if save_to_db:
                self._debug_print(f"\n💾 Saving to database...")
                save_result = await self._save_analysis_to_db(token_address, result)
                self._debug_print(f"�� Save result: {save_result}")
            
            self._debug_print(f"\n✅ ANALYSIS COMPLETE ({analysis_time:.2f}s)")
            return result
            
        except Exception as e:
            self._debug_print(f"❌ Error: {e}")
            import traceback
            return {
                "success": False,
                "token_address": token_address,
                "error": str(e),
                "traceback": traceback.format_exc()
            }

    async def _get_token_id_by_address(self, token_address: str) -> Optional[int]:
        """Отримати token_id за адресою"""
        try:
            cursor = await self.conn.execute(
                "SELECT id FROM token_ids WHERE token_address = ?",
                (token_address,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            self._debug_print(f"Error getting token_id: {e}")
            return None

    async def _save_analysis_to_db(self, token_address: str, analysis: Dict[str, Any]) -> bool:
        """Зберегти результати аналізу в БД"""
        try:
            await self.ensure_connection()
            
            self._debug_print(f"\n💾 Saving to database...")
            
            # Отримуємо token_id
            token_id = await self._get_token_id_by_address(token_address)
            if not token_id:
                # Створюємо токен якщо не існує
                async with self.db_lock:
                    await self.conn.execute(
                        "INSERT OR IGNORE INTO token_ids (token_address) VALUES (?)",
                        (token_address,)
                    )
                    await self.conn.commit()
                token_id = await self._get_token_id_by_address(token_address)
            
            if not token_id:
                self._debug_print(f"❌ Could not create token_id")
                return False
            
            async with self.db_lock:
                # Зберігаємо DexScreener дані
                if analysis.get('dexscreener_data'):
                    self._debug_print(f"💾 Saving DexScreener data...")
                    await self._save_dexscreener_data(token_id, analysis['dexscreener_data'])
                
                # Зберігаємо Solana RPC дані
                self._debug_print(f"💾 Checking solana_rpc_data: {bool(analysis.get('solana_rpc_data'))}")
                if analysis.get('solana_rpc_data'):
                    self._debug_print(f"💾 Saving Solana RPC data...")
                    await self._save_solana_rpc_data(token_id, analysis['solana_rpc_data'])
                else:
                    self._debug_print(f"⚠️ No solana_rpc_data in analysis!")
                
                # Оновлюємо token_ids (pair_address, honeypot, dev_address)
                pair_address = analysis.get('security', {}).get('pair_address')
                honeypot_check = analysis.get('security', {}).get('honeypot_check', {})
                dev_address = analysis.get('security', {}).get('dev_address')
                
                if pair_address:
                    await self.conn.execute("""
                        UPDATE token_ids 
                        SET token_pair = ?,
                            is_honeypot = ?,
                            dev_address = ?,
                            security_analyzed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (pair_address, honeypot_check.get('honeypot'), dev_address, token_id))
                    self._debug_print(f"✅ Updated token_ids: pair={pair_address}, honeypot={honeypot_check.get('honeypot')}")
                
                await self.conn.commit()
            
            self._debug_print(f"✅ Saved to database (token_id={token_id})")
            return True
            
        except Exception as e:
            self._debug_print(f"❌ Save error: {e}")
            return False

    async def _save_dexscreener_data(self, token_id: int, dexscreener_data: Any):
        """Зберегти DexScreener дані"""
        try:
            if not isinstance(dexscreener_data, dict) or 'pairs' not in dexscreener_data:
                return
            
            pairs = dexscreener_data.get('pairs', [])
            if not pairs:
                return
            
            pair = pairs[0]
            
            # Основна пара
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
            
            # Base token
            base_token = pair.get('baseToken', {})
            if base_token:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_base_token (
                        token_id, address, name, symbol
                    ) VALUES (?, ?, ?, ?)
                """, (token_id, base_token.get('address'), base_token.get('name'), base_token.get('symbol')))
            
            # Quote token
            quote_token = pair.get('quoteToken', {})
            if quote_token:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_quote_token (
                        token_id, address, name, symbol
                    ) VALUES (?, ?, ?, ?)
                """, (token_id, quote_token.get('address'), quote_token.get('name'), quote_token.get('symbol')))
            
            # Транзакції
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
            
            # Об'єм
            volume = pair.get('volume', {})
            if volume:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_volume (
                        token_id, h24, h6, h1, m5
                    ) VALUES (?, ?, ?, ?)
                """, (token_id, volume.get('h24'), volume.get('h6'), volume.get('h1'), volume.get('m5')))
            
            # Ліквідність
            liquidity = pair.get('liquidity', {})
            if liquidity:
                await self.conn.execute("""
                    INSERT OR REPLACE INTO dexscreener_liquidity (
                        token_id, usd, base, quote
                    ) VALUES (?, ?, ?, ?)
                """, (token_id, liquidity.get('usd'), liquidity.get('base'), liquidity.get('quote')))
            
        except Exception as e:
            self._debug_print(f"Error saving dexscreener: {e}")

    async def _save_solana_rpc_data(self, token_id: int, solana_rpc_data: Dict[str, Any]):
        """Зберегти Solana RPC дані"""
        try:
            # Token supply
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
            
            # Token metadata
            token_metadata = solana_rpc_data.get('token_metadata', {})
            self._debug_print(f"   → token_metadata present: {bool(token_metadata)}")
            if token_metadata and 'value' in token_metadata:
                metadata_value = token_metadata['value']
                context = token_metadata.get('context', {})
                parsed_info = metadata_value.get('data', {}).get('parsed', {}).get('info', {})
                
                self._debug_print(f"   → Saving metadata: decimals={parsed_info.get('decimals')}, mintAuth={parsed_info.get('mintAuthority')}")
                
                # Конвертуємо великі числа в strings для SQLite
                rent_epoch = metadata_value.get('rentEpoch')
                if rent_epoch is not None:
                    rent_epoch = str(rent_epoch)
                
                supply = parsed_info.get('supply')
                if supply is not None:
                    supply = str(supply)
                
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
                    supply,  # Використовуємо string
                    metadata_value.get('data', {}).get('program'),
                    metadata_value.get('space'),
                    metadata_value.get('executable'),
                    metadata_value.get('lamports'),
                    metadata_value.get('owner'),
                    rent_epoch,  # Використовуємо string
                    context.get('slot'),
                    context.get('apiVersion')
                ))
            else:
                self._debug_print(f"   ⚠️ No token_metadata or missing 'value' key")
            
        except Exception as e:
            self._debug_print(f"Error saving solana rpc: {e}")


# ========================================================================
# 🌐 ГЛОБАЛЬНІ ФУНКЦІЇ
# ========================================================================

analyzer_instance: Optional[AsyncTokenAnalyzer] = None

async def get_analyzer() -> AsyncTokenAnalyzer:
    """Отримати глобальний екземпляр аналізатора"""
    global analyzer_instance
    if analyzer_instance is None:
        analyzer_instance = AsyncTokenAnalyzer(debug=True)
    return analyzer_instance
