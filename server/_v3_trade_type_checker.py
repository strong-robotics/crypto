#!/usr/bin/env python3
"""
Trade Type Checker - перевірка типу транзакцій через Helius API
Визначає, чи є реальна торгівля (SWAP) або тільки перекази (TRANSFER)

Використання:
    - Перед покупкою токена (buy_real)
    - Перед вердиктом аналізатора (buy/not)
    - Перевірка останніх N транзакцій через Helius API
"""

import asyncio
import aiohttp
import re
from typing import Dict, Optional, Tuple, Any, List
from _v3_db_pool import get_db_pool
from config import config
from _v2_sol_price import get_current_sol_price


def _get_helius_api_key() -> str:
    """Отримати Helius API ключ з HELIUS_RPC або config"""
    # Спробувати з config
    if config.HELIUS_API_KEY:
        return config.HELIUS_API_KEY
    
    # Спробувати витягти з HELIUS_RPC в _v2_buy_sell.py
    try:
        import os
        buy_sell_path = os.path.join(os.path.dirname(__file__), '_v2_buy_sell.py')
        with open(buy_sell_path, 'r') as f:
            content = f.read()
        match = re.search(r'HELIUS_RPC\s*=\s*["\']https://[^"\']*api-key=([^"\']+)', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    
    return ''


class TradeTypeChecker:
    """Перевіряє тип транзакцій торгової пари через Helius API"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.helius_api_key = _get_helius_api_key()
        self.helius_api_base = config.HELIUS_API_BASE
        self.check_limit = 70  # Кількість транзакцій для перевірки
        self.min_swap_ratio = 0.3  # Мінімальний відсоток SWAP транзакцій (30%)
        self.min_swap_count = 5  # Мінімальна кількість SWAP транзакцій
        self.SOL_MINT = "So11111111111111111111111111111111111111112"
        
    async def ensure_session(self):
        """Створити HTTP сесію якщо потрібно"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """Закрити HTTP сесію"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def check_token_pair(self, token_id: int, token_pair: Optional[str] = None) -> Dict[str, Any]:
        """
        Перевіряє тип транзакцій для токена
        
        Args:
            token_id: ID токена в БД
            token_pair: Адреса торгової пари (якщо None, читає з БД)
        
        Returns:
            Dict з результатами:
            {
                "has_real_trading": bool,  # True якщо є SWAP транзакції
                "swap_count": int,
                "transfer_count": int,
                "withdraw_count": int,
                "total": int,
                "swap_ratio": float,  # Відсоток SWAP транзакцій
                "error": Optional[str]
            }
        """
        try:
            # Отримати token_pair з БД якщо не передано
            if not token_pair:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    pair_row = await conn.fetchrow(
                        "SELECT token_pair FROM tokens WHERE id=$1",
                        token_id
                    )
                    if not pair_row or not pair_row["token_pair"]:
                        return {
                            "has_real_trading": False,
                            "swap_count": 0,
                            "transfer_count": 0,
                            "withdraw_count": 0,
                            "total": 0,
                            "swap_ratio": 0.0,
                            "avg_buy_price_usd": None,
                            "avg_sell_price_usd": None,
                            "median_buy_price_usd": None,
                            "median_sell_price_usd": None,
                            "buy_count": 0,
                            "sell_count": 0,
                            "error": "Token pair not found"
                        }
                    token_pair = pair_row["token_pair"]
            
            # Отримати token_mint з БД для парсингу цін
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                token_row = await conn.fetchrow(
                    "SELECT token_address FROM tokens WHERE id=$1",
                    token_id
                )
                token_mint = token_row["token_address"] if token_row else None
            
            # Перевірити через Helius API
            return await self._check_pair_transactions(token_pair, token_id, token_mint)
            
        except Exception as e:
            return {
                "has_real_trading": False,
                "swap_count": 0,
                "transfer_count": 0,
                "withdraw_count": 0,
                "total": 0,
                "swap_ratio": 0.0,
                "avg_buy_price_usd": None,
                "avg_sell_price_usd": None,
                "median_buy_price_usd": None,
                "median_sell_price_usd": None,
                "buy_count": 0,
                "sell_count": 0,
                "error": str(e)
            }
    
    async def _update_token_medians(
        self,
        token_id: int,
        median_amount_usd: Optional[float],
        median_amount_sol: Optional[float],
        median_amount_tokens: Optional[float],
        median_token_price: Optional[float],
    ) -> None:
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE tokens
                    SET
                        median_amount_usd = $2,
                        median_amount_sol = $3,
                        median_amount_tokens = $4,
                        median_token_price = $5,
                        token_updated_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    token_id,
                    median_amount_usd,
                    median_amount_sol,
                    median_amount_tokens,
                    median_token_price,
                )
        except Exception:
            pass
    
    def _median(self, values: List[float]) -> Optional[float]:
        if not values:
            return None
        sorted_values = sorted(values)
        n = len(sorted_values)
        if n % 2 == 0:
            return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0
        return sorted_values[n // 2]

    def _parse_price_from_swap(self, tx: Dict, token_mint: str, token_pair: str, sol_price: float) -> Optional[Dict[str, Any]]:
        """
        Парсить ціну з SWAP транзакції (аналогічно до _v3_live_trades.py)
        
        Returns:
            Dict з direction, token_price_usd або None якщо не вдалося розпарсити
        """
        if not tx.get('tokenTransfers'):
            return None
        
        token_transfers = tx['tokenTransfers']
        token_transfer = None
        sol_transfer = None
        
        for transfer in token_transfers:
            mint = transfer.get('mint', '')
            if mint == token_mint:
                token_transfer = transfer
            elif mint == self.SOL_MINT:
                sol_transfer = transfer
        
        if not token_transfer:
            return None
        
        if not sol_transfer:
            native_transfers = tx.get('nativeTransfers', [])
            if native_transfers:
                native = native_transfers[0]
                sol_transfer = {
                    'mint': self.SOL_MINT,
                    'tokenAmount': native.get('amount', 0) / 1_000_000_000,
                    'fromUserAccount': native.get('fromUserAccount', ''),
                    'toUserAccount': native.get('toUserAccount', '')
                }
        
        # КРИТИЧНО: Перевірити, чи адреса пари бере участь в транзакції
        # Якщо пара не бере участь - це не SWAP, а просто TRANSFER
        if token_pair:
            pair_in_transaction = False
            token_from = token_transfer.get('fromUserAccount', '')
            token_to = token_transfer.get('toUserAccount', '')
            
            if token_pair in (token_from, token_to):
                pair_in_transaction = True
            
            if sol_transfer:
                sol_from = sol_transfer.get('fromUserAccount', '')
                sol_to = sol_transfer.get('toUserAccount', '')
                if token_pair in (sol_from, sol_to):
                    pair_in_transaction = True
            
            # Якщо пара не бере участь - це не SWAP
            if not pair_in_transaction:
                return None
        
        try:
            token_amount = float(token_transfer.get('tokenAmount', 0))
        except Exception:
            token_amount = float(token_transfer.get('tokenAmount', 0) or 0)
        
        # Визначити напрямок
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
        
        # Розрахувати ціну
        amount_sol = 0.0
        if sol_transfer:
            amount_sol = sol_transfer.get('tokenAmount', 0)
            if amount_sol > 1000:
                amount_sol = amount_sol / 1_000_000_000
        
        amount_usd = amount_sol * sol_price
        token_price_usd = 0.0
        
        if abs(token_amount) > 0:
            try:
                token_price_usd = amount_usd / abs(float(token_amount))
            except Exception:
                token_price_usd = 0.0
        
        if token_price_usd <= 0:
            return None
        
        return {
            "direction": direction,
            "token_price_usd": float(token_price_usd),
            "amount_sol": abs(float(amount_sol)),
            "amount_usd": abs(float(amount_usd)),
            "amount_tokens": abs(float(token_amount))
        }
    
    async def _check_pair_transactions(self, pair_address: str, token_id: int, token_mint: Optional[str] = None) -> Dict[str, Any]:
        """Перевіряє транзакції торгової пари через Helius API"""
        await self.ensure_session()
        
        url = f"{self.helius_api_base}/v0/addresses/{pair_address}/transactions"
        params = {
            "api-key": self.helius_api_key,
            "limit": self.check_limit
        }
        
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return {
                        "has_real_trading": False,
                        "swap_count": 0,
                        "transfer_count": 0,
                        "withdraw_count": 0,
                        "total": 0,
                        "swap_ratio": 0.0,
                        "avg_buy_price_usd": None,
                        "avg_sell_price_usd": None,
                        "median_buy_price_usd": None,
                        "median_sell_price_usd": None,
                        "buy_count": 0,
                        "sell_count": 0,
                        "error": f"Helius API error {resp.status}: {error_text[:100]}"
                    }
                
                data = await resp.json()
                
                if not data:
                    return {
                        "has_real_trading": False,
                        "swap_count": 0,
                        "transfer_count": 0,
                        "withdraw_count": 0,
                        "total": 0,
                        "swap_ratio": 0.0,
                        "avg_buy_price_usd": None,
                        "avg_sell_price_usd": None,
                        "median_buy_price_usd": None,
                        "median_sell_price_usd": None,
                        "buy_count": 0,
                        "sell_count": 0,
                        "error": "No transactions found"
                    }
                
                # Аналіз транзакцій - використовуємо класифікацію Helius напряму
                swap_count = 0
                transfer_count = 0
                withdraw_count = 0
                
                # Збір даних для SWAP транзакцій
                buy_prices: List[float] = []
                sell_prices: List[float] = []
                trade_amounts_usd: List[float] = []
                trade_amounts_sol: List[float] = []
                trade_amounts_tokens: List[float] = []

                sol_price = get_current_sol_price() or 0.0
                if sol_price <= 0:
                    sol_price = float(getattr(config, 'SOL_PRICE_FALLBACK', 0.0) or 0.0)
                
                for tx in data:
                    tx_type = (tx.get('type') or 'UNKNOWN').upper()
                    
                    # Довіряємо класифікації Helius
                    if tx_type == 'SWAP':
                        swap_count += 1
                        # Парсимо ціну тільки для SWAP транзакцій
                        if token_mint:
                            price_data = self._parse_price_from_swap(tx, token_mint, pair_address, sol_price)
                            if price_data:
                                price = price_data.get('token_price_usd', 0.0)
                                direction = price_data.get('direction', '')
                                if price and price > 0:
                                    if direction == 'buy':
                                        buy_prices.append(price)
                                    elif direction == 'sell':
                                        sell_prices.append(price)
                                amount_usd = price_data.get('amount_usd')
                                amount_sol = price_data.get('amount_sol')
                                amount_tokens = price_data.get('amount_tokens')
                                if amount_usd and amount_usd > 0:
                                    trade_amounts_usd.append(amount_usd)
                                if amount_sol and amount_sol > 0:
                                    trade_amounts_sol.append(amount_sol)
                                if amount_tokens and amount_tokens > 0:
                                    trade_amounts_tokens.append(amount_tokens)
                    elif tx_type == 'TRANSFER':
                        transfer_count += 1
                    elif tx_type == 'WITHDRAW':
                        withdraw_count += 1
                    else:
                        # Інші типи (UNKNOWN, BURN, MINT, тощо) - рахуємо як transfer
                        transfer_count += 1
                
                total = len(data)
                swap_ratio = (swap_count / total) if total > 0 else 0.0
                
                # Визначити, чи є реальна торгівля
                has_real_trading = (
                    swap_count >= self.min_swap_count and
                    swap_ratio >= self.min_swap_ratio
                )
                
                # Розрахувати середню та медіанну ціну
                avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else None
                avg_sell_price = sum(sell_prices) / len(sell_prices) if sell_prices else None
                
                # Медіанна ціна
                median_buy_price = self._median(buy_prices)
                median_sell_price = self._median(sell_prices)
                median_amount_usd = self._median(trade_amounts_usd)
                median_amount_sol = self._median(trade_amounts_sol)
                median_amount_tokens = self._median(trade_amounts_tokens)

                result = {
                    "has_real_trading": has_real_trading,
                    "swap_count": swap_count,
                    "transfer_count": transfer_count,
                    "withdraw_count": withdraw_count,
                    "total": total,
                    "swap_ratio": swap_ratio,
                    "avg_buy_price_usd": avg_buy_price,
                    "avg_sell_price_usd": avg_sell_price,
                    "median_buy_price_usd": median_buy_price,
                    "median_sell_price_usd": median_sell_price,
                    "buy_count": len(buy_prices),
                    "sell_count": len(sell_prices),
                    "median_amount_usd": median_amount_usd,
                    "error": None
                }
                
                # avg_buy_str = f"{avg_buy_price:.6f}" if avg_buy_price else "N/A"
                # avg_sell_str = f"{avg_sell_price:.6f}" if avg_sell_price else "N/A"
                # median_buy_str = f"{median_buy_price:.6f}" if median_buy_price else "N/A"
                # median_sell_str = f"{median_sell_price:.6f}" if median_sell_price else "N/A"
                # log_message = (
                #     f"[TradeTypeChecker] Token {token_id} pair {pair_address}: "
                #     f"SWAP={swap_count} TRANSFER={transfer_count} WITHDRAW={withdraw_count} "
                #     f"TOTAL={total} ratio={swap_ratio:.2%} has_trading={has_real_trading} "
                #     f"buy_count={len(buy_prices)} sell_count={len(sell_prices)} "
                #     f"avg_buy=${avg_buy_str} avg_sell=${avg_sell_str} "
                #     f"median_buy=${median_buy_str} median_sell=${median_sell_str}"
                # )
                # print(log_message)
                
                # Persist medians for frontend display
                await self._update_token_medians(
                    token_id,
                    median_amount_usd,
                    median_amount_sol,
                    median_amount_tokens,
                    median_buy_price or median_sell_price
                )

                return result
                
        except asyncio.TimeoutError:
            return {
                "has_real_trading": False,
                "swap_count": 0,
                "transfer_count": 0,
                "withdraw_count": 0,
                "total": 0,
                "swap_ratio": 0.0,
                "avg_buy_price_usd": None,
                "avg_sell_price_usd": None,
                "median_buy_price_usd": None,
                "median_sell_price_usd": None,
                "buy_count": 0,
                "sell_count": 0,
                "error": "Request timeout"
            }
        except Exception as e:
            return {
                "has_real_trading": False,
                "swap_count": 0,
                "transfer_count": 0,
                "withdraw_count": 0,
                "total": 0,
                "swap_ratio": 0.0,
                "avg_buy_price_usd": None,
                "avg_sell_price_usd": None,
                "median_buy_price_usd": None,
                "median_sell_price_usd": None,
                "buy_count": 0,
                "sell_count": 0,
                "error": str(e)
            }


# Глобальний екземпляр
_checker_instance: Optional[TradeTypeChecker] = None


async def get_trade_type_checker() -> TradeTypeChecker:
    """Отримати глобальний екземпляр TradeTypeChecker"""
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = TradeTypeChecker()
    return _checker_instance


async def check_token_has_real_trading(token_id: int, token_pair: Optional[str] = None, save_to_db: bool = True) -> bool:
    """
    Швидка перевірка: чи має токен реальну торгівлю (SWAP транзакції)
    
    Args:
        token_id: ID токена
        token_pair: Адреса торгової пари (якщо None, читає з БД)
        save_to_db: Чи зберігати результат в БД (has_real_trading)
    
    Returns:
        True якщо є реальна торгівля (SWAP >= min_swap_count і swap_ratio >= min_swap_ratio)
        False якщо тільки TRANSFER або помилка
    """
    checker = await get_trade_type_checker()
    result = await checker.check_token_pair(token_id, token_pair)
    has_real_trading = result.get("has_real_trading", False)
    swap_count = int(result.get("swap_count") or 0)
    transfer_count = int(result.get("transfer_count") or 0)
    withdraw_count = int(result.get("withdraw_count") or 0)
    
    # Зберегти результат в БД
    if save_to_db:
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE tokens
                    SET has_real_trading=$1,
                        swap_count=$2,
                        transfer_count=$3,
                        withdraw_count=$4
                    WHERE id=$5
                    """,
                    has_real_trading,
                    swap_count,
                    transfer_count,
                    withdraw_count,
                    token_id
                )
        except Exception as e:
            # Не критична помилка - просто логуємо
            if config.DEBUG:
                print(f"[TradeTypeChecker] Failed to save result to DB for token {token_id}: {e}")
    
    return has_real_trading

