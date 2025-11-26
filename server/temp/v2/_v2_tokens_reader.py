#!/usr/bin/env python3

# SQLite (BACKUP - commented out)
# import aiosqlite
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket
# PostgreSQL (ACTIVE)
from _v2_db_pool import get_db_pool
from config import config

class TokensReaderV2:
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        # SQLite (BACKUP - commented out)
        # import os
        # os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # self.db_path = db_path
        # self.conn: Optional[aiosqlite.Connection] = None
        # self.db_lock = asyncio.Lock()
        self.debug = debug
        
        # WebSocket клієнти для real-time оновлень
        self.connected_clients: List[WebSocket] = []
        
        # Авто-оновлення
        self.auto_refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval: int = config.TOKENS_REFRESH_INTERVAL
        self.last_token_count: int = 0  # Зберігаємо кількість токенів для перевірки змін
        self.last_updated_at: Optional[datetime] = None  # Зберігаємо час останнього оновлення
    
    async def ensure_connection(self):
        """PostgreSQL - не потрібне (pool створюється автоматично)"""
        pass
    
    async def close(self):
        """PostgreSQL - не потрібне (pool закривається глобально)"""
        pass
    
    async def get_tokens_from_db(self, limit: int = 1000, offset: int = 0) -> Dict[str, Any]:
        """Отримує токени з БД з пагінацією (PostgreSQL)"""
        try:
            pool = await get_db_pool()
            
            # if self.debug:
                # print(f"🔍 Getting tokens from DB: limit={limit}, offset={offset}")
            
            async with pool.acquire() as conn:
                # Отримуємо загальну кількість токенів
                total_count = await conn.fetchval("""
                    SELECT COUNT(*) FROM token_ids
                """)
                
                # Отримуємо токени з пагінацією (всі поля в одній таблиці!)
                rows = await conn.fetch("""
                    SELECT 
                        id, token_address, token_pair, is_honeypot, security_analyzed_at, created_at,
                        pattern, check_dexscreener, check_jupiter, check_sol_rpc,
                        name, symbol, icon, decimals, twitter, dev,
                        circ_supply, total_supply, token_program, launchpad, holder_count,
                        usd_price, liquidity, fdv, mcap, bonding_curve,
                        organic_score, organic_score_label, updated_at
                    FROM token_ids
                    ORDER BY created_at DESC 
                    LIMIT $1 OFFSET $2
                """, limit, offset)
                
                formatted_tokens = []
                for row in rows:
                    # Всі дані тепер в одній таблиці!
                    token_id = row['id']
                    token_address = row['token_address']
                    token_pair = row['token_pair']
                    is_honeypot = row['is_honeypot']
                    security_analyzed_at = row['security_analyzed_at']
                    created_at = row['created_at']
                    pattern = row['pattern']
                    check_dexscreener = row['check_dexscreener']
                    check_jupiter = row['check_jupiter']
                    check_sol_rpc = row['check_sol_rpc']
                    name = row['name']
                    symbol = row['symbol']
                    icon = row['icon']
                    decimals = row['decimals']
                    twitter = row['twitter']
                    dev = row['dev']
                    token_program = row['token_program']
                    launchpad = row['launchpad']
                    holder_count = row['holder_count']
                    # PostgreSQL Decimal → float для JSON serialization
                    circ_supply = float(row['circ_supply']) if row['circ_supply'] is not None else 0
                    total_supply = float(row['total_supply']) if row['total_supply'] is not None else 0
                    usd_price = float(row['usd_price']) if row['usd_price'] is not None else 0
                    liquidity = float(row['liquidity']) if row['liquidity'] is not None else 0
                    fdv = float(row['fdv']) if row['fdv'] is not None else 0
                    mcap = float(row['mcap']) if row['mcap'] is not None else 0
                    bonding_curve = float(row['bonding_curve']) if row['bonding_curve'] is not None else 0
                    organic_score = float(row['organic_score']) if row['organic_score'] is not None else 0
                    organic_score_label = row['organic_score_label']
                    updated_at = row['updated_at']
                    
                    # 24h transactions (placeholders; real values can be joined later from stats table)
                    stats_24h_num_buys = 0
                    stats_24h_num_sells = 0

                    formatted_tokens.append({
                        "id": token_id,  # INTEGER id для ідентифікації
                        "token_address": token_address,  # mint address
                        "name": name or "Unknown",
                        "symbol": symbol or "UNKNOWN",
                        "icon": icon or "",
                        "decimals": decimals or 0,
                        "twitter": twitter or "",
                        "dev": dev or "",
                        "circ_supply": circ_supply or 0,
                        "total_supply": total_supply or 0,
                        "token_program": token_program or "",
                        "launchpad": launchpad or "",
                        "holders": holder_count or 0,
                        "price": usd_price or 0,
                        "liquidity": liquidity or 0,
                        "fdv": fdv or 0,
                        "mcap": mcap or 0,
                        "bonding_curve": bonding_curve or 0,
                        "organic_score": organic_score or 0,
                        "organic_score_label": organic_score_label or "",
                        "dex": "Analyzing...",
                        "pair": token_pair,
                        "is_honeypot": is_honeypot,
                        "pattern": pattern or "",
                        "check_dexscreener": check_dexscreener or 0,
                        "check_jupiter": check_jupiter or 0,
                        "check_sol_rpc": check_sol_rpc or 0,
                        # 24h transactions (Jupiter stats)
                        "stats_24h_num_buys": stats_24h_num_buys or 0,
                        "stats_24h_num_sells": stats_24h_num_sells or 0,
                        "security_analyzed_at": security_analyzed_at.isoformat() if security_analyzed_at and hasattr(security_analyzed_at, 'isoformat') else str(security_analyzed_at) if security_analyzed_at else None,
                        "updated_at": updated_at.isoformat() if updated_at and hasattr(updated_at, 'isoformat') else str(updated_at) if updated_at else None,
                        "created_at": created_at.isoformat() if created_at and hasattr(created_at, 'isoformat') else str(created_at) if created_at else None
                    })
                
                result = {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "total_count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < total_count,
                    "scan_time": datetime.now().isoformat()
                }
                
                # Оновлюємо збережену кількість
                self.last_token_count = total_count
                
                if self.debug:
                    print(f"✅ Retrieved {len(formatted_tokens)} tokens from DB (total: {total_count})")
                
                return result
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting tokens from DB: {e}")
            return {
                "success": False,
                "error": str(e),
                "tokens": [],
                "total_count": 0
            }
    
    async def get_token_by_address(self, token_address: str) -> Dict[str, Any]:
        """Отримує конкретний токен за адресою (PostgreSQL)"""
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                # Query merged token_ids (all data in one table)
                row = await conn.fetchrow("""
                    SELECT 
                        id, token_address, token_pair, is_honeypot, security_analyzed_at, created_at,
                        pattern, check_dexscreener, check_jupiter, check_sol_rpc,
                        name, symbol, icon, decimals, twitter, dev,
                        circ_supply, total_supply, token_program, launchpad, holder_count,
                        usd_price, liquidity, fdv, mcap, bonding_curve,
                        organic_score, organic_score_label, updated_at
                    FROM token_ids
                    WHERE token_address = $1
                """, token_address)
                
                if not row:
                    return {
                        "success": False,
                        "error": "Token not found",
                        "token": None
                    }
                
                # Extract all data from merged table
                token_id = row['id']
                token_address = row['token_address']
                token_pair = row['token_pair']
                is_honeypot = row['is_honeypot']
                security_analyzed_at = row['security_analyzed_at']
                created_at = row['created_at']
                pattern = row['pattern']
                check_dexscreener = row['check_dexscreener']
                check_jupiter = row['check_jupiter']
                check_sol_rpc = row['check_sol_rpc']
                name = row['name']
                symbol = row['symbol']
                icon = row['icon']
                decimals = row['decimals']
                twitter = row['twitter']
                dev = row['dev']
                token_program = row['token_program']
                launchpad = row['launchpad']
                holder_count = row['holder_count']
                # PostgreSQL Decimal → float для JSON serialization
                circ_supply = float(row['circ_supply']) if row['circ_supply'] is not None else 0
                total_supply = float(row['total_supply']) if row['total_supply'] is not None else 0
                usd_price = float(row['usd_price']) if row['usd_price'] is not None else 0
                liquidity = float(row['liquidity']) if row['liquidity'] is not None else 0
                fdv = float(row['fdv']) if row['fdv'] is not None else 0
                mcap = float(row['mcap']) if row['mcap'] is not None else 0
                bonding_curve = float(row['bonding_curve']) if row['bonding_curve'] is not None else 0
                organic_score = float(row['organic_score']) if row['organic_score'] is not None else 0
                organic_score_label = row['organic_score_label']
                updated_at = row['updated_at']
                
                token = {
                    "id": token_id,  # INTEGER id для ідентифікації
                    "token_address": token_address,  # mint address
                    "name": name or "Unknown",
                    "symbol": symbol or "UNKNOWN",
                    "icon": icon or "",
                    "decimals": decimals or 0,
                    "twitter": twitter or "",
                    "dev": dev or "",
                    "circ_supply": circ_supply or 0,
                    "total_supply": total_supply or 0,
                    "token_program": token_program or "",
                    "launchpad": launchpad or "",
                    "holders": holder_count or 0,
                    "price": usd_price or 0,
                    "liquidity": liquidity or 0,
                    "fdv": fdv or 0,
                    "mcap": mcap or 0,
                    "bonding_curve": bonding_curve or 0,
                    "organic_score": organic_score or 0,
                    "organic_score_label": organic_score_label or "",
                    "dex": "Analyzing...",
                    "pair": token_pair,
                    "is_honeypot": is_honeypot,
                    "pattern": pattern or "",
                    "check_dexscreener": check_dexscreener or 0,
                    "check_jupiter": check_jupiter or 0,
                    "check_sol_rpc": check_sol_rpc or 0,
                    "security_analyzed_at": security_analyzed_at.isoformat() if security_analyzed_at and hasattr(security_analyzed_at, 'isoformat') else str(security_analyzed_at) if security_analyzed_at else None,
                    "updated_at": updated_at.isoformat() if updated_at and hasattr(updated_at, 'isoformat') else str(updated_at) if updated_at else None,
                    "created_at": created_at.isoformat() if created_at and hasattr(created_at, 'isoformat') else str(created_at) if created_at else None
                }
                
                return {
                    "success": True,
                    "token": token
                }
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting token by address: {e}")
            return {
                "success": False,
                "error": str(e),
                "token": None
            }
    
    async def search_tokens(self, query: str, limit: int = 50) -> Dict[str, Any]:
        """Пошук токенів за назвою або символом (PostgreSQL)"""
        try:
            pool = await get_db_pool()
            
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        id,
                        token_address,
                        name,
                        symbol,
                        mcap,
                        usd_price,
                        holder_count
                    FROM token_ids
                    WHERE LOWER(name) LIKE LOWER($1) OR LOWER(symbol) LIKE LOWER($2)
                    ORDER BY mcap DESC NULLS LAST
                    LIMIT $3
                """, f"%{query}%", f"%{query}%", limit)
                
                formatted_tokens = []
                for row in rows:
                    token_id = row['id']
                    token_address = row['token_address']
                    name = row['name']
                    symbol = row['symbol']
                    # PostgreSQL Decimal → float для JSON serialization
                    mcap = float(row['mcap']) if row['mcap'] is not None else 0
                    price = float(row['usd_price']) if row['usd_price'] is not None else 0
                    holders = row['holder_count']
                    
                    formatted_tokens.append({
                        "id": token_id,  # INTEGER id для ідентифікації
                        "token_address": token_address,  # mint address
                        "name": name or "Unknown",
                        "symbol": symbol or "UNKNOWN",
                        "mcap": mcap,
                        "price": price,
                        "holders": holders or 0,
                        "dex": "Analyzing...",
                        "pair": None
                    })
                
                return {
                    "success": True,
                    "tokens": formatted_tokens,
                    "total_found": len(formatted_tokens),
                    "query": query,
                    "scan_time": datetime.now().isoformat()
                }
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error searching tokens: {e}")
            return {
                "success": False,
                "error": str(e),
                "tokens": []
            }
    
    async def add_client(self, websocket: WebSocket):
        """Додає клієнта до списку підключених"""
        self.connected_clients.append(websocket)
        
        # Якщо це перший клієнт, запускаємо авто-оновлення
        if len(self.connected_clients) == 1:
            await self.start_auto_refresh()
    
    def remove_client(self, websocket: WebSocket):
        """Видаляє клієнта зі списку підключених"""
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
    
    async def start_auto_refresh(self):
        """Запускає авто-оновлення для всіх клієнтів"""
        if self.auto_refresh_task is None:
            self.auto_refresh_task = asyncio.create_task(self._auto_refresh_loop())
            # if self.debug:
                # print(f"✅ Auto-refresh started (every {self.refresh_interval}s)")
    
    async def stop_auto_refresh(self):
        """Зупиняє авто-оновлення"""
        if self.auto_refresh_task:
            self.auto_refresh_task.cancel()
            try:
                await self.auto_refresh_task
            except asyncio.CancelledError:
                pass
            self.auto_refresh_task = None
            # if self.debug:
                # print("🛑 Auto-refresh stopped")
    
    async def _auto_refresh_loop(self):
        """Періодично перевіряє БД і відправляє оновлення ТІЛЬКИ якщо є зміни (PostgreSQL)"""
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                
                if not self.connected_clients:
                    continue
                
                # Перевіряємо COUNT та MAX(updated_at) для виявлення будь-яких змін
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT COUNT(*) as count, MAX(updated_at) as last_updated
                        FROM token_ids
                    """)
                    current_count = row['count']
                    current_updated_at = row['last_updated']
                
                # Якщо кількість змінилась АБО updated_at новіший → оновлюємо frontend
                has_changes = (
                    current_count != self.last_token_count or 
                    (current_updated_at and current_updated_at != self.last_updated_at)
                )
                
                if has_changes:
                    result = await self.get_tokens_from_db(limit=1000)
                    
                    if result["success"] and result["tokens"]:
                        json_data = json.dumps(result, ensure_ascii=False)
                        
                        disconnected_clients = []
                        for client in self.connected_clients:
                            try:
                                await client.send_text(json_data)
                            except Exception as e:
                                disconnected_clients.append(client)
                        
                        for client in disconnected_clients:
                            self.connected_clients.remove(client)
                        
                        self.last_token_count = current_count
                        self.last_updated_at = current_updated_at
                        
                        # if self.debug:
                            # print(f"📡 DB changed! Sent {len(result['tokens'])} tokens to {len(self.connected_clients)} clients")
                # else:
                    # if self.debug:
                        # print(f"ℹ️  No changes in DB ({current_count} tokens)")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.debug:
                    print(f"❌ Auto-refresh error: {e}")
    
    def get_status(self):
        """Повертає статус читача (PostgreSQL)"""
        return {
            "connected_clients": len(self.connected_clients),
            "database": "PostgreSQL",
            "debug": self.debug,
            "auto_refresh_running": self.auto_refresh_task is not None
        }

if __name__ == "__main__":
    pass
