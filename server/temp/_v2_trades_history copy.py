"""
Trades History Module
Отримання історичних trades з Helius API та збереження в БД
"""
import asyncio
import aiosqlite
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional
from config import config


class TradesHistory:
    def __init__(self, helius_api_key: str, db_path: str, debug: bool = True):
        self.helius_api_key = helius_api_key
        self.db_path = db_path
        self.debug = debug
        self.base_url = "https://api.helius.xyz"
        self.session = None
        self.conn = None
        self.db_lock = asyncio.Lock()
    
    async def ensure_connection(self):
        """Забезпечити з'єднання з БД"""
        if not self.conn:
            self.conn = await aiosqlite.connect(self.db_path)
    
    async def ensure_session(self):
        """Забезпечити HTTP сесію"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def get_sol_price(self) -> float:
        """Отримати поточну ціну SOL"""
        from _v2_sol_price import get_current_sol_price
        return get_current_sol_price()
    
    async def close(self):
        """Закрити з'єднання"""
        if self.session:
            await self.session.close()
        if self.conn:
            await self.conn.close()
    
    async def get_token_info_by_pair(self, token_pair: str) -> Optional[Dict]:
        """Отримати інформацію про токен по trading pair"""
        await self.ensure_connection()
        
        async with self.db_lock:
            cursor = await self.conn.execute("""
                SELECT id, token_address, token_pair 
                FROM token_ids 
                WHERE token_pair = ?
            """, (token_pair,))
            row = await cursor.fetchone()
            
            if row:
                return {
                    "id": row[0],
                    "token_address": row[1], 
                    "token_pair": row[2]
                }
            return None
    
    async def get_all_tokens_with_pairs(self) -> List[Dict]:
        """Отримати всі токени, які мають trading pair"""
        await self.ensure_connection()
        
        async with self.db_lock:
            cursor = await self.conn.execute("""
                SELECT id, token_address, token_pair 
                FROM token_ids 
                WHERE token_pair IS NOT NULL AND token_pair != ''
                ORDER BY created_at ASC
            """)
            rows = await cursor.fetchall()
            
            return [
                {
                    "id": row[0],
                    "token_address": row[1],
                    "token_pair": row[2]
                }
                for row in rows
            ]
    
    async def get_historical_trades(self, token_pair: str, limit: int = 50) -> List[Dict]:
        """Отримати історичні trades для trading pair через Helius API"""
        try:
            await self.ensure_session()
            
            url = f"{self.base_url}/v0/addresses/{token_pair}/transactions"
            params = {
                "api-key": self.helius_api_key,
                "limit": limit
            }
            
            if self.debug:
                print(f"🔍 Fetching trades for pair {token_pair[:8]}... (limit: {limit})")
            
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    if self.debug:
                        print(f"❌ Helius API error: {resp.status}")
                    return []
                
                data = await resp.json()
                if not data:
                    if self.debug:
                        print(f"⚠️ No data returned for {token_pair[:8]}...")
                    return []
                
                if self.debug:
                    print(f"✅ Got {len(data)} raw transactions for {token_pair[:8]}...")
                
                return data
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting historical trades: {e}")
            return []
    
    async def get_all_historical_trades_with_pagination(self, token_pair: str, max_requests: int = 10) -> List[Dict]:
        """Отримати ВСІ історичні trades з pagination"""
        try:
            await self.ensure_session()
            
            all_transactions = []
            before = None
            request_count = 0
            
            if self.debug:
                print(f"🔄 Starting pagination for {token_pair[:8]}... (max requests: {max_requests})")
            
            while request_count < max_requests:
                url = f"{self.base_url}/v0/addresses/{token_pair}/transactions"
                params = {
                    "api-key": self.helius_api_key,
                    "limit": 100  # Максимальний ліміт за запит
                }
                
                if before:
                    params["before"] = before
                
                if self.debug:
                    print(f"📡 Request {request_count + 1}: fetching with before={before[:8] if before else 'None'}...")
                
                async with self.session.get(url, params=params) as resp:
                    if resp.status != 200:
                        if self.debug:
                            print(f"❌ Helius API error: {resp.status}")
                        break
                    
                    data = await resp.json()
                    if not data:
                        if self.debug:
                            print(f"⚠️ No more data returned")
                        break
                    
                    all_transactions.extend(data)
                    
                    if self.debug:
                        print(f"✅ Got {len(data)} transactions (total: {len(all_transactions)})")
                    
                    # Для pagination - беремо signature останньої транзакції
                    last_sig = data[-1].get("signature")
                    if not last_sig:
                        if self.debug:
                            print(f"⚠️ No signature in last transaction")
                        break
                    
                    before = last_sig
                    request_count += 1
                    
                    # Якщо 0 транзакцій - значить, більше немає
                    if len(data) == 0:
                        if self.debug:
                            print(f"✅ Reached end of data (got 0 transactions)")
                        break
                    
                    # Затримка між запитами
                    await asyncio.sleep(0.25)
            
            if self.debug:
                print(f"🎉 Pagination complete: {len(all_transactions)} total transactions in {request_count} requests")
            
            return all_transactions
                
        except Exception as e:
            if self.debug:
                print(f"❌ Error getting historical trades with pagination: {e}")
            return []
    
    async def parse_trade_from_transaction(self, tx: Dict, token_mint: str, token_pair: str = None) -> Optional[Dict]:
        """Парсити trade з транзакції"""
        if not tx.get('tokenTransfers'):
            return None
        
        token_transfers = tx['tokenTransfers']
        SOL_MINT = "So11111111111111111111111111111111111111112"
        
        # Шукаємо transfer з нашим токеном
        token_transfer = None
        sol_transfer = None
        
        for transfer in token_transfers:
            mint = transfer.get('mint', '')
            
            if mint == token_mint:
                token_transfer = transfer
            elif mint == SOL_MINT:
                # Wrapped SOL в tokenTransfers
                sol_transfer = transfer
        
        if not token_transfer:
            return None
        
        # Якщо не знайшли wrapped SOL, шукаємо native SOL transfers
        if not sol_transfer:
            native_transfers = tx.get('nativeTransfers', [])
            if native_transfers and len(native_transfers) > 0:
                # Беремо перший native transfer як SOL transfer
                # Конвертуємо формат nativeTransfers в формат tokenTransfers
                native = native_transfers[0]
                sol_transfer = {
                    'mint': SOL_MINT,
                    'tokenAmount': native.get('amount', 0) / 1_000_000_000,  # lamports -> SOL
                    'fromUserAccount': native.get('fromUserAccount', ''),
                    'toUserAccount': native.get('toUserAccount', '')
                }
        
        # Визначаємо напрямок (buy/sell/withdraw)
        tx_type = tx.get('type', '').upper()
        token_amount = token_transfer.get('tokenAmount', 0)
        
        if tx_type == 'WITHDRAW':
            direction = "withdraw"
        else:
            # Дивимося на SOL transfer, а не на TOKEN transfer!
            # BUY: SOL йде В пул (USER платить SOL за токени)
            # SELL: SOL йде З пулу (USER отримує SOL за токени)
            if sol_transfer:
                sol_from = sol_transfer.get('fromUserAccount', '')
                sol_to = sol_transfer.get('toUserAccount', '')
                
                if token_pair and sol_to == token_pair:
                    direction = "buy"  # SOL йде В пул
                elif token_pair and sol_from == token_pair:
                    direction = "sell"  # SOL йде З пулу
                else:
                    # Fallback
                    direction = "buy" if token_amount > 0 else "sell"
            else:
                # Немає SOL transfer - використовуємо token_amount
                direction = "buy" if token_amount > 0 else "sell"
        
        # Отримуємо timestamp (Helius повертає в секундах)
        timestamp = tx.get('timestamp', 0)  # Вже в секундах!
        signature = tx.get('signature', '')
        
        # Розраховуємо SOL amount
        amount_sol = 0
        if sol_transfer:
            amount_sol = sol_transfer.get('tokenAmount', 0)
            if amount_sol > 1000:  # Конвертуємо з lamports
                amount_sol = amount_sol / 1_000_000_000
        
        # Розраховуємо USD amount (використовуємо поточну ціну SOL)
        sol_price = await self.get_sol_price()
        if sol_price == 0:
            sol_price = 210.0  # Fallback price
        amount_usd = amount_sol * sol_price
        
        # Обчислюємо ціну токена (USD per token)
        token_price_usd = 0.0
        if abs(token_amount) > 0:
            token_price_usd = amount_usd / abs(token_amount)
        
        if self.debug:
            print(f"  💰 SOL price: {sol_price}, amount_sol: {amount_sol}, amount_usd: {amount_usd}")
            print(f"  💵 Token price: ${token_price_usd:.10f} per token")
        
        readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "timestamp": timestamp,
            "readable_time": readable_time,
            "direction": direction,
            "amount_tokens": abs(token_amount),
            "amount_sol": amount_sol,
            "amount_usd": amount_usd,
            "token_price_usd": token_price_usd,  # Додаємо ціну токена!
            "signature": signature
        }
    
    async def save_trades_to_db(self, token_id: int, trades: List[Dict]) -> int:
        """Зберегти trades в БД (INSERT OR REPLACE для оновлення існуючих)"""
        if not trades:
            return 0
        
        try:
            await self.ensure_connection()
            
            saved_count = 0
            async with self.db_lock:
                for trade in trades:
                    try:
                        # INSERT OR REPLACE → оновлює існуючі trades за signature
                        await self.conn.execute("""
                            INSERT OR REPLACE INTO trades (
                                token_id, signature, timestamp, readable_time,
                                direction, amount_tokens, amount_sol, amount_usd, token_price_usd
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            token_id,
                            trade.get('signature'),
                            trade.get('timestamp'),
                            trade.get('readable_time'),
                            trade.get('direction'),
                            trade.get('amount_tokens'),
                            f"{trade.get('amount_sol', 0):.8f}",
                            f"{trade.get('amount_usd', 0):.2f}",
                            f"{trade.get('token_price_usd', 0):.10f}"
                        ))
                        saved_count += 1
                    except Exception as e:
                        if self.debug:
                            print(f"❌ Error saving trade {trade.get('signature')}: {e}")
                
                await self.conn.commit()
                
                if self.debug and saved_count > 0:
                    print(f"✅ Saved/Updated {saved_count} trades for token_id {token_id}")
            
            return saved_count
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error saving trades to DB: {e}")
            return 0
    
    async def fetch_trades_for_token(self, token_pair: str, token_mint: str, token_id: int, limit: int = 50) -> int:
        """Отримати та зберегти trades для конкретного токена"""
        if self.debug:
            print(f"🔄 Processing token: {token_mint[:8]}... (pair: {token_pair[:8]}...)")
        
        # Отримуємо raw транзакції
        raw_transactions = await self.get_historical_trades(token_pair, limit)
        if not raw_transactions:
            return 0
        
        # Парсимо trades
        trades = []
        for tx in raw_transactions:
            trade = await self.parse_trade_from_transaction(tx, token_mint, token_pair)
            if trade:
                trades.append(trade)
        
        if self.debug:
            print(f"📊 Parsed {len(trades)} trades from {len(raw_transactions)} transactions")
        
        # Зберігаємо в БД
        saved_count = await self.save_trades_to_db(token_id, trades)
        return saved_count
    
    async def fetch_all_trades_for_token_with_pagination(self, token_pair: str, token_mint: str, token_id: int, max_requests: int = 10) -> int:
        """Отримати ВСІ trades для конкретного токена з pagination"""
        if self.debug:
            print(f"🔄 Processing token with pagination: {token_mint[:8]}... (pair: {token_pair[:8]}...)")
        
        # Отримуємо ВСІ raw транзакції з pagination
        raw_transactions = await self.get_all_historical_trades_with_pagination(token_pair, max_requests)
        if not raw_transactions:
            return 0
        
        # Парсимо trades
        trades = []
        for tx in raw_transactions:
            trade = await self.parse_trade_from_transaction(tx, token_mint, token_pair)
            if trade:
                trades.append(trade)
        
        if self.debug:
            print(f"📊 Parsed {len(trades)} trades from {len(raw_transactions)} transactions")
        
        # Зберігаємо в БД
        saved_count = await self.save_trades_to_db(token_id, trades)
        return saved_count
    
    async def fetch_all_historical_trades(self, batch_size: int = 10, delay_seconds: float = 1.0) -> Dict:
        """Отримати історичні trades для всіх токенів"""
        if self.debug:
            print("🚀 Starting historical trades fetch...")
        
        # Отримуємо всі токени з trading pairs
        tokens = await self.get_all_tokens_with_pairs()
        if not tokens:
            return {
                "success": False,
                "message": "No tokens with trading pairs found",
                "total_tokens": 0,
                "total_trades": 0
            }
        
        if self.debug:
            print(f"📋 Found {len(tokens)} tokens with trading pairs")
        
        total_trades = 0
        processed_tokens = 0
        
        # Обробляємо токени батчами
        for i in range(0, len(tokens), batch_size):
            batch = tokens[i:i + batch_size]
            
            if self.debug:
                print(f"🔄 Processing batch {i//batch_size + 1}/{(len(tokens) + batch_size - 1)//batch_size}")
            
            # Обробляємо токени в батчі паралельно
            tasks = []
            for token in batch:
                task = self.fetch_trades_for_token(
                    token['token_pair'], 
                    token['token_address'], 
                    token['id']
                )
                tasks.append(task)
            
            # Виконуємо всі завдання в батчі
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Підраховуємо результати
            for result in batch_results:
                if isinstance(result, int):
                    total_trades += result
                    processed_tokens += 1
                elif isinstance(result, Exception):
                    if self.debug:
                        print(f"❌ Error in batch: {result}")
            
            # Затримка між батчами
            if i + batch_size < len(tokens):
                if self.debug:
                    print(f"⏳ Waiting {delay_seconds}s before next batch...")
                await asyncio.sleep(delay_seconds)
        
        result = {
            "success": True,
            "message": f"Processed {processed_tokens} tokens, saved {total_trades} trades",
            "total_tokens": processed_tokens,
            "total_trades": total_trades
        }
        
        if self.debug:
            print(f"✅ Historical trades fetch completed: {result}")
        
        return result


# Глобальна функція для використання в main.py
async def fetch_all_historical_trades(debug: bool = True) -> Dict:
    """Отримати всі історичні trades"""
    history = TradesHistory(config.HELIUS_API_KEY, "db/tokens.db", debug=debug)
    
    try:
        result = await history.fetch_all_historical_trades()
        return result
    finally:
        await history.close()


async def fetch_trades_for_single_token(token_pair: str, debug: bool = True) -> Dict:
    """Отримати trades для одного токена"""
    history = TradesHistory(config.HELIUS_API_KEY, "db/tokens.db", debug=debug)
    
    try:
        # Знаходимо інформацію про токен
        token_info = await history.get_token_info_by_pair(token_pair)
        if not token_info:
            return {
                "success": False,
                "message": f"Token pair {token_pair[:8]}... not found in database"
            }
        
        # Отримуємо trades
        trades_count = await history.fetch_trades_for_token(
            token_info['token_pair'],
            token_info['token_address'], 
            token_info['id']
        )
        
        return {
            "success": True,
            "message": f"Saved {trades_count} trades for token {token_info['token_address'][:8]}...",
            "trades_count": trades_count
        }
        
    finally:
        await history.close()


async def refresh_all_trades_history(debug: bool = True, delay_seconds: float = 1.0, max_requests_per_token: int = 50, max_tokens: int = None) -> Dict:
    """
    Оновити історичні trades для ВСІХ токенів з БД
    
    Проходить по всіх токенах, які мають token_pair, та збирає trades з Helius API.
    Затримка між токенами - 1 секунда (за замовчуванням).
    
    Args:
        debug: Виводити детальні логи
        delay_seconds: Затримка між токенами в секундах
        max_requests_per_token: Максимум запитів до Helius для кожного токена (pagination)
        max_tokens: Максимум токенів для обробки (None = всі токени). Для тестування.
    
    Returns:
        Dict з результатами: total_tokens, total_trades, processed_tokens
    
    Usage:
        # Всі токени:
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history())"
        
        # Тільки 2 токени (тест):
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history(max_tokens=2))"
    """
    history = TradesHistory(config.HELIUS_API_KEY, "db/tokens.db", debug=debug)
    
    try:
        # Отримуємо всі токени з token_pair
        tokens = await history.get_all_tokens_with_pairs()
        
        if not tokens:
            print("⚠️  Токенів з торговими парами не знайдено в БД")
            return {
                "success": True,
                "total_tokens": 0,
                "processed_tokens": 0,
                "total_trades": 0
            }
        
        # Обмежуємо кількість токенів для тестування
        if max_tokens:
            tokens = tokens[:max_tokens]
        
        print(f"\n{'='*80}")
        print(f"🚀 ОНОВЛЕННЯ ІСТОРИЧНИХ TRADES ДЛЯ ВСІХ ТОКЕНІВ")
        print(f"{'='*80}")
        print(f"📊 Знайдено токенів з торговими парами: {len(tokens)}")
        print(f"⏱️  Затримка між токенами: {delay_seconds}s")
        print(f"📡 Макс запитів на токен (pagination): {max_requests_per_token}")
        if max_tokens:
            print(f"🧪 ТЕСТОВИЙ РЕЖИМ: оброблюємо тільки {max_tokens} токени")
        print(f"{'='*80}\n")
        
        total_trades = 0
        processed_tokens = 0
        failed_tokens = 0
        
        # Обробляємо кожен токен
        for idx, token in enumerate(tokens):
            token_id = token['id']
            token_address = token['token_address']
            token_pair = token['token_pair']
            
            print(f"\n{'─'*80}")
            print(f"🔄 Токен {idx + 1}/{len(tokens)}")
            print(f"   Token Address: {token_address[:30]}...")
            print(f"   Token Pair: {token_pair[:30]}...")
            print(f"{'─'*80}")
            
            try:
                # Збираємо ВСІ trades з pagination
                saved_count = await history.fetch_all_trades_for_token_with_pagination(
                    token_pair,
                    token_address,
                    token_id,
                    max_requests=max_requests_per_token
                )
                
                total_trades += saved_count
                processed_tokens += 1
                
                print(f"✅ Токен {idx + 1}/{len(tokens)}: Збережено {saved_count} trades")
                
            except Exception as e:
                failed_tokens += 1
                print(f"❌ Токен {idx + 1}/{len(tokens)}: Помилка - {str(e)}")
            
            # Затримка між токенами (крім останнього)
            if idx < len(tokens) - 1:
                if debug:
                    print(f"⏳ Затримка {delay_seconds}s перед наступним токеном...")
                await asyncio.sleep(delay_seconds)
        
        # Підсумок
        print(f"\n{'='*80}")
        print(f"🎉 ОНОВЛЕННЯ ЗАВЕРШЕНО")
        print(f"{'='*80}")
        print(f"✅ Оброблено токенів: {processed_tokens}/{len(tokens)}")
        print(f"❌ Помилок: {failed_tokens}")
        print(f"📊 Всього збережено trades: {total_trades}")
        print(f"{'='*80}\n")
        
        return {
            "success": True,
            "total_tokens": len(tokens),
            "processed_tokens": processed_tokens,
            "failed_tokens": failed_tokens,
            "total_trades": total_trades
        }
        
    finally:
        await history.close()
