#!/usr/bin/env python3

import sqlite3
import aiohttp
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

class TokenDatabase:
    def __init__(self, db_path: str = "db/tokens.db"):
        # Створюємо директорію якщо її немає
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Ініціалізація структури бази даних"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    symbol TEXT,
                    icon TEXT,
                    decimals INTEGER,
                    dev TEXT,
                    circ_supply REAL,
                    total_supply REAL,
                    token_program TEXT,
                    launchpad TEXT,
                    holder_count INTEGER,
                    
                    -- Аудит
                    mint_authority_disabled BOOLEAN,
                    freeze_authority_disabled BOOLEAN,
                    top_holders_percentage REAL,
                    
                    -- Скори та теги
                    organic_score REAL,
                    organic_score_label TEXT,
                    tags TEXT,
                    
                    -- Ціни та ліквідність
                    fdv REAL,
                    mcap REAL,
                    usd_price REAL,
                    price_block_id INTEGER,
                    liquidity REAL,
                    bonding_curve REAL,
                    
                    -- Метадані
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT,
                    period TEXT CHECK(period IN ('5m', '1h', '6h', '24h')),
                    timestamp TIMESTAMP,
                    
                    price_change REAL,
                    holder_change REAL,
                    liquidity_change REAL,
                    volume_change REAL,
                    buy_volume REAL,
                    sell_volume REAL,
                    buy_organic_volume REAL,
                    num_buys INTEGER,
                    num_sells INTEGER,
                    num_traders INTEGER,
                    num_net_buyers INTEGER,
                    
                    FOREIGN KEY(token_id) REFERENCES tokens(id)
                )
            """)

class JupiterScannerSQL:
    """Сканер для Jupiter DEX з підтримкою SQLite та WebSocket"""
    
    def __init__(self, debug: bool = False):
        self.api_url = "https://lite-api.jup.ag/tokens/v2/recent"
        self.session: Optional[aiohttp.ClientSession] = None
        self.debug = debug
        self.last_scan_time: Optional[datetime] = None
        self.db = TokenDatabase()
        self.active_connections: List[WebSocket] = []
    
    async def ensure_session(self) -> None:
        """Ensure aiohttp session is created and active"""
        if not self.session or self.session.closed:
            if self.debug:
                print("📡 Creating new aiohttp session...")
            self.session = aiohttp.ClientSession()
    
    async def connect_client(self, websocket: WebSocket):
        """Підключення нового WebSocket клієнта"""
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.debug:
            print(f"👥 New client connected. Total clients: {len(self.active_connections)}")
    
    async def disconnect_client(self, websocket: WebSocket):
        """Відключення WebSocket клієнта"""
        self.active_connections.remove(websocket)
        if self.debug:
            print(f"👋 Client disconnected. Remaining clients: {len(self.active_connections)}")
    
    async def broadcast_tokens(self, data: Dict[str, Any]):
        """Розсилка даних всім підключеним клієнтам"""
        disconnected = []
        for connection in self.active_connections:
            try:
                if self.debug:
                    print(f"📡 Broadcasting to client...")
                await connection.send_json(data)
            except Exception as e:
                print(f"Error broadcasting to client: {e}")
                disconnected.append(connection)
        
        # Видаляємо відключені з'єднання
        for conn in disconnected:
            await self.disconnect_client(conn)
    
    async def save_token(self, token: Dict[str, Any]) -> bool:
        """Збереження токену в SQLite"""
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                # Підготовка даних
                stats_data = {
                    '5m': token.get('stats5m', {}),
                    '1h': token.get('stats1h', {}),
                    '6h': token.get('stats6h', {}),
                    '24h': token.get('stats24h', {})
                }
                
                # Вставка основних даних токену
                cursor.execute("""
                    INSERT OR REPLACE INTO tokens (
                        id, name, symbol, icon, decimals, dev, circ_supply,
                        total_supply, token_program, holder_count, 
                        mint_authority_disabled, freeze_authority_disabled,
                        top_holders_percentage, organic_score, organic_score_label,
                        tags, fdv, mcap, usd_price, price_block_id, liquidity,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token['id'], token['name'], token['symbol'], token.get('icon'),
                    token['decimals'], token.get('dev'), token.get('circSupply'),
                    token.get('totalSupply'), token.get('tokenProgram'),
                    token.get('holderCount'), 
                    token.get('audit', {}).get('mintAuthorityDisabled'),
                    token.get('audit', {}).get('freezeAuthorityDisabled'),
                    token.get('audit', {}).get('topHoldersPercentage'),
                    token.get('organicScore'), token.get('organicScoreLabel'),
                    json.dumps(token.get('tags', [])),
                    token.get('fdv'), token.get('mcap'), token.get('usdPrice'),
                    token.get('priceBlockId'), token.get('liquidity'),
                    datetime.now().isoformat()
                ))
                
                # Вставка статистики
                for period, stats in stats_data.items():
                    if stats:
                        cursor.execute("""
                            INSERT INTO token_stats (
                                token_id, period, timestamp,
                                price_change, holder_change, liquidity_change,
                                volume_change, buy_volume, sell_volume,
                                num_buys, num_sells, num_traders, num_net_buyers
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            token['id'], period, datetime.now().isoformat(),
                            stats.get('priceChange'), stats.get('holderChange'),
                            stats.get('liquidityChange'), stats.get('volumeChange'),
                            stats.get('buyVolume'), stats.get('sellVolume'),
                            stats.get('numBuys'), stats.get('numSells'),
                            stats.get('numTraders'), stats.get('numNetBuyers')
                        ))
                
                conn.commit()
                return True
                
        except Exception as e:
            if self.debug:
                print(f"Error saving token: {e}")
            return False

    async def get_tokens(self, limit: int = 20) -> Dict[str, Any]:
        """Get recent tokens from Jupiter API"""
        try:
            await self.ensure_session()
            
            if self.debug:
                print(f"🔍 Fetching {limit} recent tokens from Jupiter...")
            
            async with self.session.get(self.api_url, timeout=10) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return {
                        "success": False,
                        "error": f"API returned status {response.status}: {error_text}"
                    }
                
                data = await response.json()
                tokens = data[:limit]
                
                self.last_scan_time = datetime.now()
                
                # Зберігаємо токени в SQLite
                saved_count = 0
                for token in tokens:
                    if await self.save_token(token):
                        saved_count += 1
                
                if self.debug:
                    print(f"💾 Saved {saved_count} tokens to database")
                
                # Форматуємо дані для відображення
                formatted_tokens = []
                for token in tokens:
                    formatted_tokens.append({
                        "id": token.get("id", ""),
                        "name": token.get("name", "Unknown"),
                        "mcap": token.get("mcap", 0),
                        "symbol": token.get("symbol", ""),
                        "price": token.get("usdPrice", 0)
                    })
                
                return {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "saved_count": saved_count,
                    "scan_time": self.last_scan_time.isoformat()
                }
                
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Request timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def close(self) -> None:
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            if self.debug:
                print("🔌 Closing aiohttp session...")
            await self.session.close()
            
    async def __aenter__(self):
        """Async context manager entry"""
        await self.ensure_session()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

async def main():
    """Test function"""
    async with JupiterScannerSQL(debug=True) as scanner:
        result = await scanner.get_tokens(20)
        print(f"Scan result: {json.dumps(result, indent=2)}")

if __name__ == "__main__":
    asyncio.run(main())
