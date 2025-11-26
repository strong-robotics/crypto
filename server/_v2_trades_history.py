"""
Trades History Module
Отримання історичних trades з Helius API та збереження в БД (PostgreSQL)
"""
import asyncio
# SQLite (BACKUP - commented out)
# import aiosqlite
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional
from config import config
# PostgreSQL (ACTIVE)
from _v3_db_pool import get_db_pool
from _v3_token_archiver import archive_token


class TradesHistory:
    def __init__(self, helius_api_key: str, debug: bool = True):
        """
        PostgreSQL version - no db_path needed
        Uses global connection pool from _v3_db_pool
        """
        self.helius_api_key = helius_api_key
        self.debug = debug
        self.base_url = config.HELIUS_API_BASE
        self.session = None
    
    async def ensure_connection(self):
        """PostgreSQL - connection pool already initialized globally"""
        # Connection pool managed globally, nothing to do here
        pass
    
    async def ensure_session(self):
        """Забезпечити HTTP сесію"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def get_sol_price(self) -> float:
        """Отримати поточну ціну SOL"""
        from _v2_sol_price import get_current_sol_price
        return get_current_sol_price()
    
    async def close(self):
        """Закрити HTTP сесію (PostgreSQL pool закривається глобально)"""
        if self.session:
            await self.session.close()
    
    async def get_token_info_by_pair(self, token_pair: str) -> Optional[Dict]:
        """Отримати інформацію про токен по trading pair (PostgreSQL, таблиця tokens)"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, token_address, token_pair
                FROM tokens
                WHERE token_pair = $1
                """,
                token_pair,
            )
            if row:
                return {
                    "id": row['id'],
                    "token_address": row['token_address'],
                    "token_pair": row['token_pair'],
                }
            return None
    
    async def get_all_tokens_with_pairs(self, skip_ready: bool = False) -> List[Dict]:
        """Отримати всі токени, які мають trading pair (PostgreSQL, таблиця tokens)
        
        Note: skip_ready parameter is deprecated (kept for backward compatibility).
        Archived tokens are in tokens_history table, so this function only returns live tokens.
        """
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Archived tokens are in tokens_history table, so we only query tokens table (live tokens)
            rows = await conn.fetch(
                """
                SELECT id, token_address, token_pair
                FROM tokens
                WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                ORDER BY created_at ASC
                """
            )
            return [
                {
                    "id": row['id'],
                    "token_address": row['token_address'],
                    "token_pair": row['token_pair'],
                }
                for row in rows
            ]
    
    async def get_all_historical_trades_with_pagination(self, token_pair: str, max_requests: int = 100) -> List[Dict]:
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
                    "limit": config.HISTORY_HELIUS_LIMIT  # Максимальний ліміт за запит
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
                    await asyncio.sleep(config.HISTORY_PAGINATION_DELAY)
            
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
        slot = tx.get('slot', 0)
        
        # Розраховуємо SOL amount
        amount_sol = 0
        if sol_transfer:
            amount_sol = sol_transfer.get('tokenAmount', 0)
            if amount_sol > 1000:  # Конвертуємо з lamports
                amount_sol = amount_sol / 1_000_000_000
        
        # Розраховуємо USD amount (використовуємо поточну ціну SOL)
        sol_price = await self.get_sol_price()
        if sol_price == 0:
            if self.debug:
                print(f"⚠️ Warning: SOL price is 0, using fallback price {config.SOL_PRICE_FALLBACK}")
            sol_price = float(config.SOL_PRICE_FALLBACK)  # Fallback price (із конфiгу)
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
            "signature": signature,
            "slot": int(slot)
        }
    
    async def save_trades_to_db(self, token_id: int, trades: List[Dict]) -> int:
        """Зберегти trades в БД (PostgreSQL UPSERT)"""
        if not trades:
            return 0
        
        try:
            pool = await get_db_pool()
            
            saved_count = 0
            async with pool.acquire() as conn:
                for trade in trades:
                    try:
                        # PostgreSQL UPSERT → оновлює існуючі trades за signature
                        # Зберігаємо тільки NUMERIC поля (TEXT поля тимчасово закоментовані)
                        await conn.execute(
                            """
                            INSERT INTO trades (
                                token_id, signature, timestamp, readable_time,
                                direction, amount_tokens, amount_sol, amount_usd, token_price_usd, slot
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                            ON CONFLICT (signature) DO NOTHING
                            """,
                            token_id,
                            trade.get('signature'),
                            trade.get('timestamp'),
                            trade.get('readable_time'),
                            trade.get('direction'),
                            trade.get('amount_tokens'),
                            # Совместим формат с LiveTrades: сохраняем как строки без фиксированного форматирования
                            str(trade.get('amount_sol', 0)),
                            str(trade.get('amount_usd', 0)),
                            str(trade.get('token_price_usd', 0)),
                            trade.get('slot'),
                        )
                        saved_count += 1
                    except Exception as e:
                        if self.debug:
                            print(f"❌ Error saving trade {trade.get('signature')}: {e}")
                
                if self.debug and saved_count > 0:
                    print(f"✅ Saved/Updated {saved_count} trades for token_id {token_id}")
            
            return saved_count
            
        except Exception as e:
            if self.debug:
                print(f"❌ Error saving trades to DB: {e}")
            return 0
    
    async def mark_history_ready(self, token_id: int):
        """Archive token directly (moves to tokens_history and removes from tokens)"""
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                # Check for open position before archiving
                open_pos = await conn.fetchrow(
                    "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
                    token_id
                )
                if open_pos:
                    if self.debug:
                        print(f"⚠️ Token {token_id} has open position - NOT archiving")
                    return
                
                try:
                    await archive_token(token_id, conn=conn)
                    if self.debug:
                        print(f"✅ Archived token_id {token_id}")
                except Exception as e:
                    if self.debug:
                        print(f"❌ Error archiving token {token_id}: {e}")
        except Exception as e:
            if self.debug:
                print(f"❌ Error in mark_history_ready for token {token_id}: {e}")
    
    async def fetch_all_trades_for_token_with_pagination(self, token_pair: str, token_mint: str, token_id: int, max_requests: int = 100) -> int:
        """Отримати історичні trades для конкретного токена з pagination.

        Додатково: якщо існують метрики (token_metrics_seconds), покриваємо весь їх діапазон
        [ts_min; ts_max] (мінімум по часу), навіть якщо LiveTrades був зупинений раніше.
        Історію вважаємо «достатньою», якщо:
          - зустріли withdraw, або
          - дійшли по часу до ts_min метрик (coverage).
        """
        if self.debug:
            print(f"🔄 Processing token with pagination: {token_mint[:8]}... (pair: {token_pair[:8]}...)")

        # 0) Обчислюємо цільовий часовий діапазон з метрик (якщо є)
        target_from_ts = None
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT MIN(ts) AS ts_min, MAX(ts) AS ts_max FROM token_metrics_seconds WHERE token_id = $1",
                    token_id,
                )
                if row and row['ts_min'] and row['ts_max']:
                    target_from_ts = int(row['ts_min'])
        except Exception:
            # Без метрик працюємо як раніше
            target_from_ts = None

        # 1) Тягнемо raw транзакції з пагінацією (лімітуємо кількість запитів)
        raw_transactions = await self.get_all_historical_trades_with_pagination(token_pair, max_requests)
        if not raw_transactions:
            return 0

        # 2) Парсимо й одночасно перевіряємо умови завершення
        trades = []
        found_withdraw = False
        covered_metrics = False

        # Сортуємо за часом зростання, щоб зручно перевіряти покриття (дані з Helius latest→older)
        raw_sorted = sorted(raw_transactions, key=lambda x: x.get('timestamp', 0))

        for tx in raw_sorted:
            ts = int(tx.get('timestamp', 0) or 0)
            if target_from_ts is not None and ts <= target_from_ts:
                covered_metrics = True
            trade = await self.parse_trade_from_transaction(tx, token_mint, token_pair)
            if trade:
                trades.append(trade)
                if (trade.get('direction') or '').lower() == 'withdraw':
                    found_withdraw = True

        if self.debug:
            print(f"📊 Parsed {len(trades)} trades from {len(raw_transactions)} transactions (covered_metrics={covered_metrics}, withdraw={found_withdraw})")

        # 3) Зберігаємо в БД
        saved_count = await self.save_trades_to_db(token_id, trades)

        # 4) Позначаємо history_ready тільки коли дійсно достатньо
        if saved_count > 0 and (found_withdraw or covered_metrics):
            await self.mark_history_ready(token_id)

        return saved_count


# ============================================================================
# ГЛОБАЛЬНІ ФУНКЦІЇ ДЛЯ ВИКОРИСТАННЯ
# ============================================================================

async def fetch_trades_for_single_token(token_pair: str, debug: bool = True, max_requests: int = 100) -> Dict:
    """
    Отримати trades для ОДНОГО токена (з pagination)
    
    Args:
        token_pair: Адреса торгової пари
        debug: Виводити детальні логи
        max_requests: Максимум запитів до Helius (pagination) - за замовчуванням 100
    
    Returns:
        Dict з результатами: success, message, trades_count
    """
    history = TradesHistory(config.HELIUS_API_KEY, debug=debug)
    
    try:
        # Знаходимо інформацію про токен
        token_info = await history.get_token_info_by_pair(token_pair)
        if not token_info:
            return {
                "success": False,
                "message": f"Token pair {token_pair[:8]}... not found in database"
            }
        
        # Отримуємо ВСІ trades з pagination
        trades_count = await history.fetch_all_trades_for_token_with_pagination(
            token_info['token_pair'],
            token_info['token_address'], 
            token_info['id'],
            max_requests=max_requests
        )
        
        return {
            "success": True,
            "message": f"Saved {trades_count} trades for token {token_info['token_address'][:8]}...",
            "trades_count": trades_count
        }
        
    finally:
        await history.close()


async def refresh_all_trades_history(debug: bool = True, delay_seconds: float = 1.0, max_requests_per_token: int = 100, max_tokens: int = None, skip_ready: bool = True) -> Dict:
    """
    Оновити історичні trades для ВСІХ токенів з БД
    
    Проходить по всіх токенах, які мають token_pair, та збирає trades з Helius API.
    Затримка між токенами - 1 секунда (за замовчуванням).
    
    Args:
        debug: Виводити детальні логи
        delay_seconds: Затримка між токенами в секундах
        max_requests_per_token: Максимум запитів до Helius для кожного токена (pagination) - за замовчуванням 100
        max_tokens: Максимум токенів для обробки (None = всі токени). Для тестування.
        skip_ready: Deprecated parameter (kept for backward compatibility). Archived tokens are in tokens_history table.
    
    Returns:
        Dict з результатами: total_tokens, total_trades, processed_tokens
    
    Usage:
        # Тільки токени БЕЗ history (skip_ready=True, max_requests=100):
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history())"
        
        # ВСІ токени (включно з history_ready=1):
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history(skip_ready=False))"
        
        # Тільки 2 токени (тест):
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history(max_tokens=2))"
        
        # З кастомними параметрами (100 запитів на токен):
        python3 -c "import asyncio; from _v2_trades_history import refresh_all_trades_history; asyncio.run(refresh_all_trades_history(max_requests_per_token=100))"
    """
    # Запускаємо SOL Price Monitor (якщо ще не запущений)
    from _v2_sol_price import get_sol_price_monitor, get_current_sol_price
    await get_sol_price_monitor(update_interval=1, debug=debug)
    
    # Перевіряємо що ціна отримана
    sol_price = get_current_sol_price()
    if debug and sol_price > 0:
        print(f"💰 Current SOL price: ${sol_price:.2f}")
    
    history = TradesHistory(config.HELIUS_API_KEY, debug=debug)
    
    try:
        # Отримуємо токени з token_pair (пропускаємо готові якщо skip_ready=True)
        tokens = await history.get_all_tokens_with_pairs(skip_ready=skip_ready)
        
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
