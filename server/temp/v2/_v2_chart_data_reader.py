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
        self.last_trade_counts = {}  # Для відстеження змін
    
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
                SELECT timestamp, amount_usd_numeric
                FROM trades
                WHERE token_id = $1 
                  AND timestamp >= $2 
                  AND timestamp <= $3
                ORDER BY timestamp ASC
            """, token_id, start_time, end_time)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "amount_usd": float(row["amount_usd_numeric"]) if row["amount_usd_numeric"] else 0.0
                }
                for row in rows
            ]
    
    async def get_all_trades_from_db(self, token_id: int) -> List[Dict]:
        """Отримати ВСІ trades з БД для конкретного токена"""
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT timestamp, token_price_usd_numeric
                FROM trades
                WHERE token_id = $1
                ORDER BY timestamp ASC
            """, token_id)
            
            return [
                {
                    "timestamp": row["timestamp"],
                    "token_price_usd": float(row["token_price_usd_numeric"]) if row["token_price_usd_numeric"] else 0.0
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
                # Повертаємо порожній масив замість None
                # Це дозволить фронтенду знати, що токен є, але trades немає
                return []
            
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
            
            return chart_data
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error generating chart for token_id {token_id}: {e}")
            return []
    
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
        is_first_client = len(self.connected_clients) == 0
        
        self.connected_clients.add(websocket)
        if self.debug:
            print(f"📊 Chart client connected (total: {len(self.connected_clients)})")
        
        # Відправити initial chart data одразу
        await self.send_initial_chart_data(websocket)
        
        # Запустити auto-refresh якщо ще не запущений
        if not self.is_running:
            # ❗ ВАЖЛИВО: Очищаємо last_trade_counts щоб _auto_refresh_loop відправив дані
            self.last_trade_counts.clear()
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
                
                # DEBUG для токена ID=9
                if token_id == 9:
                    print(f"🔍 DEBUG TOKEN ID=9 (send_initial_chart_data):")
                    print(f"   token_id (INTEGER): {token_id}")
                    print(f"   token_address: {token_address}")
                    print(f"   token_pair: {token_pair}")
                    print(f"   chart_data length: {len(chart_data) if chart_data else 0}")
                    print(f"   chart_data first 5: {chart_data[:5] if chart_data else None}")
                
                # ✅ Відправляємо ЗАВЖДИ, навіть якщо chart_data порожній
                try:
                    await websocket.send_json({
                        "token_id": token_address,  # mint address для сумісності
                        "id": token_id,  # INTEGER id для ідентифікації
                        "token_pair": token_pair,
                        "chart_data": chart_data
                    })
                    sent_count += 1
                    if token_id == 9:
                        print(f"✅ SENT chart for TOKEN ID=9 with {len(chart_data)} points")
                    if self.debug and len(chart_data) > 0:
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
        
        loop_count = 0
        while self.is_running:
            loop_count += 1
            try:
                # Перевіряємо чи є підключені клієнти
                if not self.connected_clients:
                    if self.debug and loop_count == 1:
                        print("⚠️  No connected clients, waiting...")
                    await asyncio.sleep(self.refresh_interval)
                    continue
                
                tokens = await self.get_all_tokens()
                
                if self.debug and loop_count == 1:
                    print(f"🔍 ChartReader loop #{loop_count}: Found {len(tokens)} tokens")
                
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
                    last_count = self.last_trade_counts.get(token_id, -1)  # -1 = ще не перевірявся
                    
                    # Відправляємо якщо:
                    # 1. Токен перевіряється вперше (last_count = -1)
                    # 2. Є нові trades (current_count > last_count)
                    if current_count > last_count:
                        chart_data = await self.generate_chart_data(token_id)
                        
                        # ✅ Відправляємо ЗАВЖДИ, навіть якщо chart_data порожній
                        # Це важливо щоб фронтенд знав, що токен існує
                        updated_tokens.append({
                            "token_id": token_address,  # mint address для сумісності
                            "id": token_id,  # INTEGER id для ідентифікації
                            "token_pair": token_pair,
                            "chart_data": chart_data
                        })
                        
                        if self.debug and last_count >= 0:  # Не перший запуск
                            new_count = current_count - last_count if last_count >= 0 else current_count
                            print(f"📈 Chart updated for token_id={token_id} ({token_address[:8]}...) - {current_count} trades ({new_count} new)")
                    
                    self.last_trade_counts[token_id] = current_count
                
                # Відправляємо тільки оновлені токени
                if updated_tokens:
                    for token_data in updated_tokens:
                        await self.broadcast_to_clients(token_data)
                    
                    if self.debug:
                        print(f"📊 Updated {len(updated_tokens)} tokens with chart data")
                elif self.debug and loop_count == 1:
                    print(f"⚠️  ChartReader loop #{loop_count}: No tokens to update (all counts unchanged)")
                
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
            # Очищаємо last_trade_counts щоб при наступному старті відправити всі дані
            self.last_trade_counts.clear()
            if self.debug:
                print("⏹️ Chart auto-refresh stopped")

