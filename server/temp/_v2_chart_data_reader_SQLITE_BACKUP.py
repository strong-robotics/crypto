import asyncio
import aiosqlite
import time
from typing import List, Dict, Optional, Set
from fastapi import WebSocket

class ChartDataReader:
    """
    Reader для chart_data - читає trades з БД та генерує графіки.
    Працює незалежно від Writer (History Scanner).
    """
    
    def __init__(self, db_path: str = "db/tokens.db", debug: bool = False):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        self.debug = debug
        
        self.connected_clients: Set[WebSocket] = set()
        self.is_running = False
        self.refresh_task: Optional[asyncio.Task] = None
        self.refresh_interval = 1
        self.chart_seconds = 86400  # 24 години замість 450 секунд
    
    async def ensure_connection(self):
        """Підключення до БД"""
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
    
    async def close(self):
        """Закрити підключення"""
        if self.conn:
            await self.conn.close()
            self.conn = None
    
    async def get_all_tokens(self) -> List[Dict]:
        """Отримати всі токени з token_ids"""
        await self.ensure_connection()
        
        async with self.db_lock:
            cursor = await self.conn.execute("""
                SELECT id, token_address, token_pair 
                FROM token_ids
                ORDER BY created_at DESC
            """)
            rows = await cursor.fetchall()
            
            return [
                {
                    "token_id": row[0],
                    "token_address": row[1],
                    "token_pair": row[2]
                }
                for row in rows
            ]
    
    async def get_trades_from_db(self, token_id: int, start_time: int, end_time: int) -> List[Dict]:
        """
        Отримати trades з БД для конкретного токена в проміжку часу.
        """
        await self.ensure_connection()
        
        async with self.db_lock:
            cursor = await self.conn.execute("""
                SELECT timestamp, amount_usd
                FROM trades
                WHERE token_id = ? 
                  AND timestamp >= ? 
                  AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (token_id, start_time, end_time))
            
            rows = await cursor.fetchall()
            
            return [
                {
                    "timestamp": row[0],
                    "amount_usd": float(row[1]) if row[1] else 0.0
                }
                for row in rows
            ]
    
    async def get_all_trades_from_db(self, token_id: int) -> List[Dict]:
        """
        Отримати ВСІ trades з БД для конкретного токена (незалежно від часу).
        """
        await self.ensure_connection()
        
        async with self.db_lock:
            cursor = await self.conn.execute("""
                SELECT timestamp, token_price_usd
                FROM trades
                WHERE token_id = ?
                ORDER BY timestamp ASC
            """, (token_id,))
            
            rows = await cursor.fetchall()
            
            return [
                {
                    "timestamp": row[0],
                    "token_price_usd": float(row[1]) if row[1] else 0.0
                }
                for row in rows
            ]
    
    async def generate_chart_data(self, token_id: int) -> List[Optional[float]]:
        """
        Генерує chart_data для токена з ВСІЇХ trades (незалежно від часу).
        Повертає масив цін по хронологічному порядку.
        Або None якщо немає жодних trades.
        """
        # Отримуємо ВСІ trades для токена
        trades = await self.get_all_trades_from_db(token_id)
        
        if self.debug:
            print(f"📊 Generating chart for token_id {token_id}: {len(trades) if trades else 0} trades")
        
        if not trades or len(trades) == 0:
            if self.debug:
                print(f"⚠️ No trades found for token_id {token_id}")
            return None
        
        # Сортуємо trades по часу
        trades.sort(key=lambda x: x['timestamp'])
        
        # Групуємо по секундах (використовуємо token_price_usd!)
        trades_by_second = {}
        for trade in trades:
            second = trade['timestamp']
            if second not in trades_by_second:
                trades_by_second[second] = []
            # Використовуємо token_price_usd замість amount_usd для графіка ціни!
            price = float(trade.get('token_price_usd', 0))
            if price > 0:  # Пропускаємо нульові ціни
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
            chart_data.append(round(avg_price, 10))  # Більше знаків для точності
            prev_price = round(avg_price, 10)
        
        if self.debug:
            print(f"✅ Generated chart_data with {len(chart_data)} points for token_id {token_id}")
            if token_id == 9:  # Debug для конкретного токена
                print(f"🔍 DEBUG token_id {token_id}:")
                print(f"  - Total trades: {len(trades)}")
                print(f"  - Chart data length: {len(chart_data)}")
                print(f"  - First 5 chart points: {chart_data[:5] if len(chart_data) >= 5 else chart_data}")
                print(f"  - Last 5 chart points: {chart_data[-5:] if len(chart_data) >= 5 else chart_data}")
                print(f"  - Min price: {min(chart_data) if chart_data else 'N/A'}")
                print(f"  - Max price: {max(chart_data) if chart_data else 'N/A'}")
        
        return chart_data
    
    async def broadcast_to_clients(self, data: Dict):
        """Відправити дані всім підключеним клієнтам"""
        if not self.connected_clients:
            return
        
        # Debug логування для конкретного токена
        if data.get('token_id') == 'FPGEiSDwEXcjMpvzhvicHpueNJ225F6DPhZrCRwXpump' or data.get('token_pair') == '8En9ZeLoMwKaHJY68TjMGmqFmoBPSD1xZaQ1VS6dm2R5':
            print(f"🚀 BROADCASTING to Frontend for token {data.get('token_id', 'unknown')[:8]}...:")
            print(f"  - token_id: {data.get('token_id')}")
            print(f"  - token_pair: {data.get('token_pair')}")
            print(f"  - chart_data length: {len(data.get('chart_data', []))}")
            print(f"  - chart_data first 5: {data.get('chart_data', [])[:5]}")
            print(f"  - chart_data last 5: {data.get('chart_data', [])[-5:]}")
            print(f"  - Full data: {data}")
        
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
        
        # Відправляємо історичні дані одразу при підключенні
        await self.send_initial_chart_data(websocket)
        
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
                        # Відправляємо і token_address і token_pair для правильного пошуку на Frontend
                        await websocket.send_json({
                            "token_id": token_address,  # token_mint для пошуку
                            "token_pair": token_pair,   # token_pair для відображення
                            "chart_data": chart_data
                        })
                        sent_count += 1
                        if self.debug:
                            print(f"📈 Sent initial chart for {token_address[:8]}... (pair: {token_pair[:8] if token_pair else 'None'}...) ({len(chart_data)} points)")
                    except Exception as e:
                        if self.debug:
                            print(f"❌ Error sending initial chart for {token_address[:8]}...: {e}")
                        break  # Якщо клієнт відключився, припиняємо
            
            if self.debug:
                print(f"✅ Initial chart data sent: {sent_count}/{len(tokens)} tokens")
                
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
        """
        Головний цикл - читає trades з БД кожну секунду,
        генерує chart_data та відправляє на Frontend тільки при змінах.
        """
        if self.debug:
            print("📊 Chart Data Reader started")
        
        last_trade_counts = {}  # Кеш для відстеження змін
        
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
                                "token_id": token_address,  # token_mint для пошуку
                                "token_pair": token_pair,   # token_pair для відображення
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
            print("📊 Chart Data Reader stopped")
    
    async def get_trade_count(self, token_id: int) -> int:
        """Отримати кількість ВСІХ trades для токена"""
        try:
            await self.ensure_connection()
            
            async with self.db_lock:
                cursor = await self.conn.execute("""
                    SELECT COUNT(*) FROM trades WHERE token_id = ?
                """, (token_id,))
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting trade count for token {token_id}: {e}")
            return 0
    
    async def start_auto_refresh(self):
        """Запустити авто-оновлення"""
        if self.is_running:
            return
        
        self.is_running = True
        self.refresh_task = asyncio.create_task(self._auto_refresh_loop())
        
        if self.debug:
            print(f"✅ Chart auto-refresh started (every {self.refresh_interval}s)")
    
    async def stop_auto_refresh(self):
        """Зупинити авто-оновлення"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.refresh_task:
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass
            self.refresh_task = None
        
        if self.debug:
            print("⏸️ Chart auto-refresh stopped")
    
    def get_status(self) -> Dict:
        """Отримати статус Reader"""
        return {
            "is_running": self.is_running,
            "connected_clients": len(self.connected_clients),
            "refresh_interval": self.refresh_interval,
            "chart_seconds": self.chart_seconds
        }

