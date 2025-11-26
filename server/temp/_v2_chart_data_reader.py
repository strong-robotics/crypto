import asyncio
import time
from typing import List, Dict, Optional, Set
from fastapi import WebSocket
from _v2_db_pool import get_db_pool

class ChartDataReader:
    """
    Reader для chart_data - читає trades з PostgreSQL та генерує графіки.
    Використовує connection pool замість окремих з'єднань.
    """
    
    def __init__(self, debug: bool = False):
        self.debug = debug
        
        self.connected_clients: Set[WebSocket] = set()
        self.is_running = False
        self.refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval = 1
        self.chart_seconds = 86400  # 24 години
    
    async def ensure_connection(self):
        """Не потрібне для PostgreSQL - pool створюється автоматично"""
        pass
    
    async def close(self):
        """Не потрібне - pool закривається глобально"""
        pass
    
    async def get_all_tokens(self) -> List[Dict]:
        """Отримати всі токени з token_ids"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, token_address, token_pair 
                FROM token_ids
                ORDER BY created_at DESC
            """)
            
            return [
                {
                    "token_id": row["id"],
                    "token_address": row["token_address"],
                    "token_pair": row["token_pair"]
                }
                for row in rows
            ]
    
    async def get_trades_from_db(self, token_id: int, start_time: int, end_time: int) -> List[Dict]:
        """Отримати trades з БД для конкретного токена в проміжку часу"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT timestamp, amount_usd
                FROM trades
                WHERE token_id = $1 
                  AND timestamp >= $2 
                  AND timestamp <= $3
                ORDER BY timestamp ASC
            """, token_id, start_time, end_time)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "amount_usd": float(row["amount_usd"]) if row["amount_usd"] else 0.0
                }
                for row in rows
            ]
    
    async def get_all_trades_from_db(self, token_id: int) -> List[Dict]:
        """Отримати ВСІ trades з БД для конкретного токена"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT timestamp, token_price_usd
                FROM trades
                WHERE token_id = $1
                ORDER BY timestamp ASC
            """, token_id)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "token_price_usd": float(row["token_price_usd"]) if row["token_price_usd"] else 0.0
                }
                for row in rows
            ]
    
    async def get_trade_count(self, token_id: int) -> int:
        """Отримати кількість trades для токена"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            count = await conn.fetchval("""
                SELECT COUNT(*) 
                FROM trades 
                WHERE token_id = $1
            """, token_id)
            
            return count or 0
    
    async def generate_chart_data(self, token_id: int) -> Optional[List[float]]:
        """Генерує chart_data з trades для конкретного токена"""
        try:
            # Отримуємо ВСІ trades для токена
            trades = await self.get_all_trades_from_db(token_id)
            
            if not trades:
                if self.debug:
                    print(f"📊 Generating chart for token_id {token_id}: 0 trades")
                    print(f"⚠️ No trades found for token_id {token_id}")
                return None
            
            if self.debug:
                print(f"📊 Generating chart for token_id {token_id}: {len(trades)} trades")
            
            # Групуємо trades по секундах
            trades_by_second = {}
            for trade in trades:
                second = trade['timestamp']
                price = trade['token_price_usd']
                
                if second not in trades_by_second:
                    trades_by_second[second] = []
                
                if price > 0:  # Ігноруємо нульові ціни
                    trades_by_second[second].append(price)
            
            # Формуємо chart_data з усіх секунд
            chart_data = []
            prev_price = None
            
            for second in sorted(trades_by_second.keys()):
                prices = trades_by_second[second]
                # Пропускаємо секунди без цін або використовуємо попередню ціну
                if len(prices) == 0:
                    if prev_price is not None:
                        chart_data.append(prev_price)
                    continue
                
                avg_price = sum(prices) / len(prices)
                chart_data.append(round(avg_price, 10))
                prev_price = round(avg_price, 10)
            
            if self.debug:
                print(f"✅ Generated chart_data with {len(chart_data)} points for token_id {token_id}")
            
            return chart_data
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating chart for token_id {token_id}: {e}")
            return None
    
    async def broadcast_to_clients(self, data: Dict):
        """Відправити дані всім підключеним клієнтам"""
        if not self.connected_clients:
            return
        
        disconnected = set()
        for client in self.connected_clients:
            try:
                await client.send_json(data)
            except Exception as e:
                if self.debug:
                    print(f"❌ Error sending to client: {e}")
                disconnected.add(client)
        
        for client in disconnected:
            self.connected_clients.discard(client)
    
    async def add_client(self, websocket: WebSocket):
        """Додати WebSocket клієнта"""
        self.connected_clients.add(websocket)
        if self.debug:
            print(f"📊 Chart client connected (total: {len(self.connected_clients)})")
        
        # Відправити initial chart data
        await self.send_initial_chart_data(websocket)
        
        # Запустити auto-refresh якщо ще не запущений
        if not self.is_running:
            await self.start_auto_refresh()
            if self.debug:
                print("🚀 Chart auto-refresh started after client connection")
    
    async def send_initial_chart_data(self, websocket: WebSocket):
        """Відправити історичні chart_data при підключенні клієнта"""
        try:
            tokens = await self.get_all_tokens()
            
            if not tokens:
                if self.debug:
                    print("📊 No tokens found for initial chart data")
                return
            
            if self.debug:
                print(f"📊 Sending initial chart data for {len(tokens)} tokens...")
            
            sent_count = 0
            for token in tokens:
                token_id = token['token_id']
                token_address = token['token_address']
                token_pair = token.get('token_pair')
                
                chart_data = await self.generate_chart_data(token_id)
                
                # Відправляємо тільки якщо є chart_data
                if chart_data is not None and len(chart_data) > 0:
                    try:
                        await websocket.send_json({
                            "token_id": token_address,
                            "token_pair": token_pair,
                            "chart_data": chart_data
                        })
                        sent_count += 1
                        if self.debug:
                            print(f"📈 Sent initial chart for {token_address[:8]}... ({len(chart_data)} points)")
                    except Exception as e:
                        if self.debug:
                            print(f"❌ Error sending initial chart for {token_address[:8]}...: {e}")
                        break
            
            if self.debug:
                print(f"✅ Sent {sent_count} initial charts to client")
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error sending initial chart data: {e}")
    
    async def remove_client(self, websocket: WebSocket):
        """Видалити WebSocket клієнта"""
        self.connected_clients.discard(websocket)
        if self.debug:
            print(f"📊 Chart client disconnected (total: {len(self.connected_clients)})")
        
        if len(self.connected_clients) == 0 and self.is_running:
            await self.stop_auto_refresh()
    
    async def _auto_refresh_loop(self):
        """Головний цикл - читає trades з БД кожну секунду"""
        if self.debug:
            print("📊 Chart Data Reader started")
        
        last_trade_counts = {}
        
        while self.is_running:
            try:
                tokens = await self.get_all_tokens()
                
                if not tokens:
                    await asyncio.sleep(self.refresh_interval)
                    continue
                
                updated_tokens = []
                
                for token in tokens:
                    token_id = token['token_id']
                    token_address = token['token_address']
                    token_pair = token.get('token_pair')
                    
                    # Перевіряємо, чи є нові trades
                    current_count = await self.get_trade_count(token_id)
                    last_count = last_trade_counts.get(token_id, 0)
                    
                    if current_count > last_count or current_count == 0:
                        chart_data = await self.generate_chart_data(token_id)
                        
                        # Відправляємо тільки якщо є chart_data
                        if chart_data is not None and len(chart_data) > 0:
                            updated_tokens.append({
                                "token_id": token_address,
                                "token_pair": token_pair,
                                "chart_data": chart_data
                            })
                            
                            if self.debug and current_count > last_count:
                                print(f"📈 New trades detected for {token_address[:8]}... ({current_count - last_count} new)")
                    
                    last_trade_counts[token_id] = current_count
                
                # Відправляємо тільки оновлені токени
                if updated_tokens:
                    for token_data in updated_tokens:
                        await self.broadcast_to_clients(token_data)
                    
                    if self.debug:
                        print(f"📊 Updated {len(updated_tokens)} tokens with chart data")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.debug:
                    print(f"❌ Chart reader error: {e}")
            
            await asyncio.sleep(self.refresh_interval)
        
        if self.debug:
            print("⏸️ Chart auto-refresh stopped")
    
    async def start_auto_refresh(self):
        """Запустити автоматичне оновлення"""
        if not self.is_running:
            self.is_running = True
            self.refresh_task = asyncio.create_task(self._auto_refresh_loop())
            if self.debug:
                print("🚀 Chart auto-refresh started")
    
    async def stop_auto_refresh(self):
        """Зупинити автоматичне оновлення"""
        if self.is_running:
            self.is_running = False
            if self.refresh_task:
                self.refresh_task.cancel()
                try:
                    await self.refresh_task
                except asyncio.CancelledError:
                    pass
            if self.debug:
                print("⏹️ Chart auto-refresh stopped")

