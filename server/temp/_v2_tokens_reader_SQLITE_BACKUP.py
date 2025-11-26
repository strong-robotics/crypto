#!/usr/bin/env python3

import aiosqlite
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

class TokensReaderV2:
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        self.debug = debug
        
        # WebSocket клієнти для real-time оновлень
        self.connected_clients: List[WebSocket] = []
        
        # Авто-оновлення
        self.auto_refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval: int = 5  # Кожні 5 секунд (синхронно зі сканером)
        self.last_token_count: int = 0  # Зберігаємо кількість токенів для перевірки змін
    
    async def ensure_connection(self):
        """Встановлює з'єднання з БД"""
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA cache_size=-64000;")
            await self.conn.execute("PRAGMA temp_store=MEMORY;")
            await self.conn.execute("PRAGMA foreign_keys=ON;")
    
    async def close(self):
        """Закриває з'єднання з БД"""
        if self.conn:
            await self.conn.close()
            self.conn = None
    
    async def get_tokens_from_db(self, limit: int = 1000, offset: int = 0) -> Dict[str, Any]:
        """Отримує токени з БД з пагінацією"""
        try:
            await self.ensure_connection()
            
            if self.debug:
                print(f"🔍 Getting tokens from DB: limit={limit}, offset={offset}")
            
            async with self.db_lock:
                # Отримуємо загальну кількість токенів
                cursor = await self.conn.execute("""
                    SELECT COUNT(*) FROM token_ids ti
                    LEFT JOIN tokens t ON t.token_id = ti.id
                """)
                total_count = (await cursor.fetchone())[0]
                
                # Отримуємо токени з пагінацією
                cursor = await self.conn.execute("""
                    SELECT 
                        ti.token_address,
                        ti.token_pair,
                        ti.is_honeypot,
                        ti.security_analyzed_at,
                        ti.created_at,
                        ti.pattern,
                        ti.check_dexscreener,
                        ti.check_jupiter,
                        ti.check_sol_rpc,
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
                        t.bonding_curve,
                        t.organic_score,
                        t.organic_score_label,
                        t.updated_at
                    FROM token_ids ti
                    LEFT JOIN tokens t ON t.token_id = ti.id
                    ORDER BY ti.created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
                
                rows = await cursor.fetchall()
                
                formatted_tokens = []
                for row in rows:
                    token_address, token_pair, is_honeypot, security_analyzed_at, created_at, pattern, check_dexscreener, check_jupiter, check_sol_rpc, name, symbol, icon, decimals, twitter, dev, circ_supply, total_supply, token_program, launchpad, holder_count, usd_price, liquidity, fdv, mcap, bonding_curve, organic_score, organic_score_label, updated_at = row
                    
                    formatted_tokens.append({
                        "id": token_address,
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
        """Отримує конкретний токен за адресою"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT 
                        ti.token_address,
                        ti.token_pair,
                        ti.is_honeypot,
                        ti.security_analyzed_at,
                        ti.created_at,
                        ti.pattern,
                        ti.check_dexscreener,
                        ti.check_jupiter,
                        ti.check_sol_rpc,
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
                        t.bonding_curve,
                        t.organic_score,
                        t.organic_score_label,
                        t.updated_at
                    FROM token_ids ti
                    LEFT JOIN tokens t ON t.token_id = ti.id
                    WHERE ti.token_address = ?
                """, (token_address,))
                
                row = await cursor.fetchone()
                
                if not row:
                    return {
                        "success": False,
                        "error": "Token not found",
                        "token": None
                    }
                
                token_address, token_pair, is_honeypot, security_analyzed_at, created_at, pattern, check_dexscreener, check_jupiter, check_sol_rpc, name, symbol, icon, decimals, twitter, dev, circ_supply, total_supply, token_program, launchpad, holder_count, usd_price, liquidity, fdv, mcap, bonding_curve, organic_score, organic_score_label, updated_at = row
                
                token = {
                    "id": token_address,
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
        """Пошук токенів за назвою або символом"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT 
                        ti.token_address,
                        t.name,
                        t.symbol,
                        t.mcap,
                        t.usd_price,
                        t.holder_count
                    FROM token_ids ti
                    LEFT JOIN tokens t ON t.token_id = ti.id
                    WHERE LOWER(t.name) LIKE LOWER(?) OR LOWER(t.symbol) LIKE LOWER(?)
                    ORDER BY t.mcap DESC
                    LIMIT ?
                """, (f"%{query}%", f"%{query}%", limit))
                
                rows = await cursor.fetchall()
                
                formatted_tokens = []
                for row in rows:
                    token_address, name, symbol, mcap, price, holders = row
                    
                    formatted_tokens.append({
                        "id": token_address,
                        "name": name or "Unknown",
                        "symbol": symbol or "UNKNOWN",
                        "mcap": mcap or 0,
                        "price": price or 0,
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
            if self.debug:
                print(f"✅ Auto-refresh started (every {self.refresh_interval}s)")
    
    async def stop_auto_refresh(self):
        """Зупиняє авто-оновлення"""
        if self.auto_refresh_task:
            self.auto_refresh_task.cancel()
            try:
                await self.auto_refresh_task
            except asyncio.CancelledError:
                pass
            self.auto_refresh_task = None
            if self.debug:
                print("🛑 Auto-refresh stopped")
    
    async def _auto_refresh_loop(self):
        """Періодично перевіряє БД і відправляє оновлення ТІЛЬКИ якщо є зміни"""
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                
                if not self.connected_clients:
                    continue
                
                # Спочатку перевіряємо, чи змінилась кількість токенів
                async with self.db_lock:
                    cursor = await self.conn.execute("""
                        SELECT COUNT(*) FROM token_ids ti
                        LEFT JOIN tokens t ON t.token_id = ti.id
                    """)
                    current_count = (await cursor.fetchone())[0]
                
                # Якщо кількість змінилась, читаємо всі токени
                if current_count != self.last_token_count:
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
                        
                        if self.debug:
                            print(f"📡 DB changed! Sent {len(result['tokens'])} tokens to {len(self.connected_clients)} clients")
                else:
                    if self.debug:
                        print(f"ℹ️  No changes in DB ({current_count} tokens)")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.debug:
                    print(f"❌ Auto-refresh error: {e}")
    
    def get_status(self):
        """Повертає статус читача"""
        return {
            "connected_clients": len(self.connected_clients),
            "db_path": self.db_path,
            "debug": self.debug,
            "auto_refresh_running": self.auto_refresh_task is not None
        }

if __name__ == "__main__":
    pass
