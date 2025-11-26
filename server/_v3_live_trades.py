#!/usr/bin/env python3

import asyncio
import aiohttp
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from _v3_db_pool import get_db_pool
from config import config
from _v2_sol_price import get_current_sol_price
from _v3_slot_synchronizer import sync_multiple_slots_for_token


class LiveTradesReaderV3:
    """
    Live Trades Reader (V3)
    - Читає останні (до 100) транзакцій для токенів з token_pair через Helius
    - Обробляє до 9 токенів за секунду (rate limit)
    - Парсить buy/sell, рахує USD за поточною ціною SOL (кеш монітора)
    - Зберігає в БД (таблиця trades) з дедуплікацією по signature
    - Якщо по токену немає нових записів 15 послідовних тікiв — архівує токен
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_running: bool = False
        self.loop_task: Optional[asyncio.Task] = None

        self.rate_limit_per_sec: int = config.LIVE_TRADES_RATE_LIMIT_PER_SEC
        self.batch_size: int = config.LIVE_TRADES_BATCH_SIZE
        self.api_limit: int = config.LIVE_TRADES_API_LIMIT
        self.loop_interval: float = float(config.LIVE_TRADES_LOOP_INTERVAL)
        self._min_gap: float = 1.0 / float(self.rate_limit_per_sec)

        self.offset: int = 0
        self.empty_streak: Dict[int, int] = {}
        self.empty_streak_threshold: int = config.LIVE_TRADES_EMPTY_STREAK_THRESHOLD

        self.processed_total: int = 0
        self.test_latest_n: Optional[int] = None
        self.prefer_latest: bool = getattr(config, 'LIVE_TRADES_PREFER_LATEST', False)
        self.max_retries: int = config.LIVE_TRADES_MAX_RETRIES
        self.retry_base_delay: float = float(config.LIVE_TRADES_RETRY_BASE_DELAY)
        # Pagination tuning to save API usage
        self.page_limit_small: int = min(config.LIVE_TRADES_PAGE_LIMIT_SMALL, self.api_limit)
        self.max_pages_per_tick: int = config.LIVE_TRADES_MAX_PAGES_PER_TICK

        self._pace_lock: asyncio.Lock = asyncio.Lock()
        self._last_call_at: float = 0.0

    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    # ============================= DB utilities =============================
    async def get_token_batch(self) -> Tuple[List[Dict], int]:
        pool = await get_db_pool()
        
        async with pool.acquire() as conn:
            eff_limit = min(self.batch_size, self.rate_limit_per_sec)

            if self.test_latest_n:
                rows = await conn.fetch(
                    """
                    SELECT id, token_address, token_pair
                    FROM tokens
                    WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    min(int(self.test_latest_n), eff_limit),
                )
                total = len(rows)
            else:
                if self.prefer_latest:
                    rows = await conn.fetch(
                        """
                        SELECT id, token_address, token_pair
                        FROM tokens
                        WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                        ORDER BY created_at DESC
                        LIMIT $1
                        """,
                        eff_limit,
                    )
                    total = len(rows)
                    return [
                        {"id": r["id"], "token_address": r["token_address"], "token_pair": r["token_pair"]}
                        for r in rows
                    ], total
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM tokens
                    WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                    """
                ) or 0

                if total == 0:
                    return [], 0

                off = self.offset % total
                take_first = min(eff_limit, max(0, total - off))
                rows = []
                if take_first > 0:
                    rows1 = await conn.fetch(
                        """
                        SELECT id, token_address, token_pair
                        FROM tokens
                        WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                        ORDER BY created_at ASC
                        OFFSET $1 LIMIT $2
                        """,
                        off,
                        take_first,
                    )
                    rows.extend(rows1)
                remain = eff_limit - len(rows)
                if remain > 0 and total > len(rows):
                    rows2 = await conn.fetch(
                        """
                        SELECT id, token_address, token_pair
                        FROM tokens
                        WHERE token_pair IS NOT NULL AND token_pair != '' AND token_pair <> token_address
                        ORDER BY created_at ASC
                        OFFSET 0 LIMIT $1
                        """,
                        remain,
                    )
                    rows.extend(rows2)
            batch = [
                {
                    "id": r["id"],
                    "token_address": r["token_address"],
                    "token_pair": r["token_pair"],
                }
                for r in rows
            ]

            if not self.test_latest_n and total > 0:
                self.offset = (self.offset + len(batch)) % total

            return batch, total

    async def insert_trade(self, t: Dict) -> bool:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            try:
                result = await conn.execute(
                    """
                    INSERT INTO trades (
                        token_id, signature, timestamp, readable_time,
                        direction, amount_tokens, amount_sol, amount_usd, token_price_usd, slot
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT (signature) DO NOTHING
                    """,
                    t["token_id"],
                    t["signature"],
                    t["timestamp"],
                    t["readable_time"],
                    t["direction"],
                    str(t["amount_tokens"]),
                    str(t["amount_sol"]),
                    str(t["amount_usd"]),
                    str(t["token_price_usd"]),
                    t["slot"],
                )
                # Перевіряємо, чи була вставка (INSERT) або конфлікт (DO NOTHING)
                if "INSERT" in result:
                    # print(f"✅ Inserted new trade: {t['signature'][:8]}...")
                    return True
                else:
                    # print(f"⚠️ Trade already exists: {t['signature'][:8]}...")
                    return False
            except Exception as e:
                # print(f"❌ Insert trade error: {e}")
                return False

    async def mark_history_ready(self, token_id: int):
        """Archive token directly (moves to tokens_history and removes from tokens).
        Only archives if no open position exists.
        """
        from _v3_token_archiver import archive_token
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            try:
                # Check for open position before archiving
                open_pos = await conn.fetchrow(
                    "SELECT id FROM wallet_history WHERE token_id=$1 AND exit_iteration IS NULL LIMIT 1",
                    token_id
                )
                if open_pos:
                    # Open position exists - do not archive
                    return
                
                # No open position - archive token directly
                await archive_token(token_id, conn=conn)
            except Exception:
                pass

    async def get_last_signature(self, token_id: int) -> Optional[str]:
        """Отримати останній signature для токена з таблиці trades"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            try:
                signature = await conn.fetchval(
                    "SELECT signature FROM trades WHERE token_id = $1 ORDER BY timestamp DESC LIMIT 1",
                    token_id
                )
                return signature
            except Exception:
                return None

    # ============================= Helius calls =============================
    async def _pace(self) -> None:
        async with self._pace_lock:
            now = asyncio.get_event_loop().time()
            delta = now - self._last_call_at
            wait = self._min_gap - delta
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_event_loop().time()
            self._last_call_at = now

    async def fetch_recent_transactions(self, token_pair: str, last_signature: str = None, before_signature: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        await self.ensure_session()
        url = f"{config.HELIUS_API_BASE}/v0/addresses/{token_pair}/transactions"
        params = {"api-key": config.HELIUS_API_KEY, "limit": int(limit or self.api_limit)}
        
        # Для инкрементального чтения: забираем последние записи и останавливаемся
        # на первом известном signature. При необходимости пагинируем назад через before.
        if last_signature:
            print(f"🔄 Incremental fetch: will stop at known signature {last_signature[:8]}...")
        else:
            print(f"🆕 First fetch: getting latest {self.api_limit} transactions")
        if before_signature:
            params["before"] = before_signature
        for attempt in range(self.max_retries):
            try:
                await self._pace()
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data or []
                    elif resp.status == 429:
                        retry_after = resp.headers.get('Retry-After')
                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except Exception:
                                delay = self.retry_base_delay * (2 ** attempt)
                        else:
                            delay = self.retry_base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        return []
            except Exception:
                await asyncio.sleep(self.retry_base_delay)
        return []

    # ============================= Parsing =============================
    async def parse_trade_from_transaction(self, tx: Dict, token_mint: str, token_pair: str) -> Optional[Dict]:
        if not tx.get('tokenTransfers'):
            return None
        token_transfers = tx['tokenTransfers']
        SOL_MINT = "So11111111111111111111111111111111111111112"

        token_transfer = None
        sol_transfer = None

        for transfer in token_transfers:
            mint = transfer.get('mint', '')
            if mint == token_mint:
                token_transfer = transfer
            elif mint == SOL_MINT:
                sol_transfer = transfer

        if not token_transfer:
            return None

        if not sol_transfer:
            native_transfers = tx.get('nativeTransfers', [])
            if native_transfers:
                native = native_transfers[0]
                sol_transfer = {
                    'mint': SOL_MINT,
                    'tokenAmount': native.get('amount', 0) / 1_000_000_000,
                    'fromUserAccount': native.get('fromUserAccount', ''),
                    'toUserAccount': native.get('toUserAccount', '')
                }

        tx_type = tx.get('type', '').upper()
        token_amount = token_transfer.get('tokenAmount', 0)
        if tx_type == 'WITHDRAW':
            direction = "withdraw"
        else:
            if sol_transfer:
                sol_from = sol_transfer.get('fromUserAccount', '')
                sol_to = sol_transfer.get('toUserAccount', '')
                if token_pair and sol_to == token_pair:
                    direction = "buy"
                elif token_pair and sol_from == token_pair:
                    direction = "sell"
                else:
                    direction = "buy" if token_amount > 0 else "sell"
            else:
                direction = "buy" if token_amount > 0 else "sell"

        timestamp = tx.get('timestamp', 0)
        signature = tx.get('signature', '')
        slot = tx.get('slot', 0)

        amount_sol = 0.0
        if sol_transfer:
            amount_sol = sol_transfer.get('tokenAmount', 0)
            if amount_sol > 1000:
                amount_sol = amount_sol / 1_000_000_000

        sol_price = get_current_sol_price() or 0.0
        if sol_price <= 0:
            sol_price = float(config.SOL_PRICE_FALLBACK)
        amount_usd = amount_sol * sol_price

        token_price_usd = 0.0
        if abs(token_amount) > 0:
            try:
                token_price_usd = amount_usd / abs(float(token_amount))
            except Exception:
                token_price_usd = 0.0

        return {
            "signature": signature,
            "timestamp": int(timestamp),
            "readable_time": datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S'),
            "direction": direction,
            "amount_tokens": float(token_amount) if token_amount is not None else 0.0,
            "amount_sol": float(amount_sol),
            "amount_usd": float(amount_usd),
            "token_price_usd": float(token_price_usd),
            "slot": int(slot),
        }

    # ============================= Main loop =============================
    async def _loop(self):
        while self.is_running:
            start_ts = asyncio.get_event_loop().time()
            try:
                batch, total = await self.get_token_batch()
                if not batch:
                    await asyncio.sleep(self.loop_interval)
                    continue

                async def process_token(token: Dict) -> Tuple[int, int]:
                    token_id = token['id']
                    token_pair = token['token_pair']
                    token_address = token['token_address']
                    # Отримати останній signature з таблиці trades
                    last_signature = await self.get_last_signature(token_id)

                    # Инкрементальная загрузка: маленькие страницы по умолчанию, пагинация при необходимости
                    page_limit = self.page_limit_small if last_signature else self.api_limit
                    before = None
                    pages = 0
                    stop = False
                    new_inserted = 0
                    saw_withdraw = False
                    touched_slots = set()
                    parsed_count_total = 0
                    while not stop:
                        txs = await self.fetch_recent_transactions(token_pair, last_signature, before_signature=before, limit=page_limit)
                        print(f"🔍 Token {token_id}: fetched {len(txs) if txs else 0} tx (page {pages+1}{' with before' if before else ''})")
                        if not txs:
                            break
                        parsed_count = 0
                        for tx in txs:
                            # Прерываемся, когда достигли уже известной транзакции
                            if last_signature and tx.get('signature') == last_signature:
                                stop = True
                                break
                            parsed = await self.parse_trade_from_transaction(tx, token_address, token_pair)
                            if parsed and parsed.get('signature'):
                                parsed_count += 1
                                record = {**parsed, 'token_id': token_id}
                                ok = await self.insert_trade(record)
                                if ok:
                                    new_inserted += 1
                                    # Копим слоты, синхронизируем после обработки токена
                                    try:
                                        helios_slot = parsed.get('slot')
                                        if helios_slot:
                                            touched_slots.add(int(helios_slot))
                                    except Exception:
                                        pass
                                # Ранний стоп по WITHDRAW: считаем, что ликвидность выведена
                                if (parsed.get('direction') or '').lower() == 'withdraw':
                                    saw_withdraw = True
                                if new_inserted % 50 == 0:
                                    await asyncio.sleep(0)
                        parsed_count_total += parsed_count

                        # Решение о пагинации: если мы не встретили last_signature,
                        # и получили полную страницу — идем дальше по before.
                        if stop:
                            break
                        pages += 1
                        if len(txs) < page_limit or pages >= self.max_pages_per_tick:
                            break
                        before = txs[-1].get('signature')
                        if not before:
                            break
                    print(f"✅ Token {token_id}: parsed {parsed_count_total} valid trades, inserted {new_inserted}")
                    # Синхронизируем медианы один раз после вставки всех транзакций токена
                    if touched_slots:
                        try:
                            latest_slot = max(touched_slots)
                            await sync_multiple_slots_for_token(token_id, latest_slot)
                        except Exception:
                            pass
                    # Если увидели WITHDRAW — сразу считаем историю завершенной
                    if saw_withdraw:
                        await self.mark_history_ready(token_id)
                        self.empty_streak[token_id] = 0
                    else:
                        # Пустые тики: считаем и, если превысили порог, отправляем в history_ready
                        if new_inserted > 0:
                            self.empty_streak[token_id] = 0
                        else:
                            streak = self.empty_streak.get(token_id, 0) + 1
                            self.empty_streak[token_id] = streak
                            if streak >= self.empty_streak_threshold:
                                # Помечаем историю ТОЛЬКО если не было входа (или уже продано) — проверка внутри метода
                                await self.mark_history_ready(token_id)
                                self.empty_streak[token_id] = 0
                    await asyncio.sleep(0)
                    return token_id, new_inserted

                results: List[Tuple[int, int]] = []
                for idx, tok in enumerate(batch):
                    res = await process_token(tok)
                    results.append(res)
                self.processed_total += len(results)

            except Exception:
                pass

            elapsed = asyncio.get_event_loop().time() - start_ts
            tick_interval = max(1.0, float(self.loop_interval))
            if elapsed < tick_interval:
                await asyncio.sleep(tick_interval - elapsed)

    async def start(self):
        if self.is_running:
            return {"success": False, "message": "Live trades already running"}
        self.is_running = True
        self.loop_task = asyncio.create_task(self._loop())
        return {"success": True, "message": "Live trades started"}

    async def stop(self):
        if not self.is_running:
            return {"success": False, "message": "Live trades not running"}
        self.is_running = False
        if self.loop_task:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
            self.loop_task = None
        return {"success": True, "message": "Live trades stopped"}

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "batch_size": self.batch_size,
            "api_limit": self.api_limit,
            "loop_interval": self.loop_interval,
            "offset": self.offset,
            "processed_total": self.processed_total,
            "test_latest_n": self.test_latest_n,
            "rate_limit_per_sec": self.rate_limit_per_sec,
        }


_instance: Optional[LiveTradesReaderV3] = None

async def get_live_trades_reader() -> LiveTradesReaderV3:
    global _instance
    if _instance is None:
        _instance = LiveTradesReaderV3()
    return _instance

async def set_live_trades_test_latest(n: int = 2) -> Dict:
    reader = await get_live_trades_reader()
    reader.test_latest_n = int(n)
    return {"success": True, "test_latest_n": reader.test_latest_n}

async def clear_live_trades_test() -> Dict:
    reader = await get_live_trades_reader()
    reader.test_latest_n = None
    return {"success": True}

async def set_live_trades_rate_limit(rps: int) -> Dict:
    reader = await get_live_trades_reader()
    rps = max(1, int(rps))
    reader.rate_limit_per_sec = rps
    reader._min_gap = 1.0 / float(reader.rate_limit_per_sec)
    return {"success": True, "rate_limit_per_sec": reader.rate_limit_per_sec}
