#!/usr/bin/env python3
"""
Balance V1 - Получение балансов всех токенов на кошельке Solana
Поддерживает devnet и mainnet
"""

import asyncio
import aiohttp
import sys
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TokenBalance:
    """Структура для хранения баланса токена"""
    mint: str
    amount: float
    decimals: int
    uiAmount: float
    symbol: str = "UNKNOWN"
    name: str = "UNKNOWN"
    price_usd: float = 0.0
    value_usd: float = 0.0
    price_sol: float = 0.0
    value_sol: float = 0.0
    liquidity_usd: float = 0.0


class BalanceV1:
    """Получение балансов токенов на кошельке"""
    
    def __init__(self, network: str = "devnet"):
        """
        Инициализация
        network: "devnet" или "mainnet"
        """
        if network == "devnet":
            self.rpc_url = "https://api.devnet.solana.com"
            self.network = "devnet"
        else:
            # Використовуємо публічний RPC для mainnet (більш стабільний)
            self.rpc_url = "https://api.mainnet-beta.solana.com"
            self.network = "mainnet"
        
        self.session = None
        
        # Native SOL mint address
        self.SOL_MINT = "So11111111111111111111111111111111111111112"
        
        # Кеш для ціни SOL (щоб не робити зайві запити)
        self._sol_price_cache = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def _make_rpc_call(self, method: str, params: List[Any]) -> Optional[Dict[str, Any]]:
        """Выполнение RPC вызова к Solana"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            }
            
            headers = {"Content-Type": "application/json"}
            
            async with self.session.post(self.rpc_url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if "error" in data:
                        print(f"❌ RPC Error ({method}): {data['error']}")
                        return {"error": data["error"]}
                    return data.get('result')
                else:
                    return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def get_sol_balance(self, wallet_address: str) -> float:
        """Получение баланса SOL"""
        try:
            balance_result = await self._make_rpc_call("getBalance", [wallet_address])
            if balance_result and not balance_result.get("error"):
                lamports = balance_result.get("value", 0)
                return lamports / 1_000_000_000  # Convert lamports to SOL
            return 0.0
        except Exception as e:
            print(f"❌ Error getting SOL balance: {e}")
            return 0.0
    
    async def get_token_accounts(self, wallet_address: str) -> List[Dict]:
        """Получение всех токен-аккаунтов кошелька"""
        try:
            print(f"   🔍 RPC запит для адреси: {wallet_address}")
            accounts_result = await self._make_rpc_call("getTokenAccountsByOwner", [
                wallet_address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"}
            ])
            
            if accounts_result and 'error' in accounts_result:
                print(f"   ❌ RPC помилка: {accounts_result['error']}")
                return []
            elif accounts_result:
                value = accounts_result.get("value", [])
                print(f"   📊 Знайдено аккаунтів: {len(value)}")
                return value
            else:
                print(f"   ❌ RPC повернув None")
                return []
        except Exception as e:
            print(f"❌ Error getting token accounts: {e}")
            return []
    
    async def get_sol_price_usd(self) -> float:
        """Получение цены SOL в USD через CoinGecko API с резервными источниками"""
        # Використовуємо кеш якщо є
        if self._sol_price_cache is not None:
            return self._sol_price_cache
            
        try:
            # Основной источник - CoinGecko
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    price = float(data.get("solana", {}).get("usd", 0))
                    if price > 0:
                        print(f"💰 SOL ціна (CoinGecko): ${price:.2f}")
                        self._sol_price_cache = price  # Кешуємо ціну
                        return price
                else:
                    print(f"⚠️ CoinGecko API помилка: {response.status}")
            
            # Резервный источник - Jupiter API
            print("🔄 Пробуємо Jupiter API для ціни SOL...")
            sol_mint = "So11111111111111111111111111111111111111112"
            url = f"https://price.jup.ag/v6/price?ids={sol_mint}"
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    token_data = data.get("data", {}).get(sol_mint)
                    if token_data:
                        price = float(token_data.get("price", 0))
                        if price > 0:
                            print(f"💰 SOL ціна (Jupiter): ${price:.2f}")
                            self._sol_price_cache = price  # Кешуємо ціну
                            return price
                else:
                    print(f"⚠️ Jupiter API помилка: {response.status}")
            
            # Если оба источника недоступны
            print("❌ Не вдалося отримати ціну SOL з жодного джерела")
            return 0.0
            
        except Exception as e:
            print(f"❌ Помилка отримання ціни SOL: {e}")
            return 0.0
    
    async def get_token_price_simple(self, token_mint: str, sol_price_usd: float = 0.0) -> Dict[str, Any]:
        """Простий метод отримання ціни токена та метаданих через публічні дані
        Логіка: Беремо публічні дані → Рахуємо математично → Отримуємо ціну + метадані
        """
        if self.network != "mainnet":
            return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "devnet"}
        
        try:
            # Отримуємо всі доступні публічні дані одним запитом
            public_data = await self.get_all_public_token_data(token_mint)
            
            if not public_data:
                return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "no_data"}
            
            # Пробуємо різні методи розрахунку ціни
            price_result = await self.calculate_price_from_public_data(public_data, token_mint, sol_price_usd)
            
            # Додаємо метадані токена з DexScreener
            if public_data.get("has_pairs", False):
                price_result.update({
                    "token_symbol": public_data.get("token_symbol", ""),
                    "token_name": public_data.get("token_name", ""),
                    "dex": public_data.get("dex", ""),
                    "pair_address": public_data.get("pair_address", "")
                })
            
            return price_result
            
        except Exception as e:
            print(f"❌ Помилка простого методу: {e}")
            return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "error"}
    
    async def get_all_public_token_data(self, token_mint: str) -> Dict[str, Any]:
        """Збираємо всі доступні публічні дані про токен з різних джерел"""
        try:
            # DexScreener - найбільш надійне джерело публічних даних
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            async with self.session.get(url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    pairs = data.get("pairs", [])
                    
                    if pairs:
                        # Беремо найбільш ліквідну пару
                        best_pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
                        
                        # Збираємо всі доступні дані включаючи метадані токена
                        base_token = best_pair.get("baseToken", {})
                        quote_token = best_pair.get("quoteToken", {})
                        
                        token_data = {
                            "has_pairs": True,
                            "pair_address": best_pair.get("pairAddress", ""),
                            "dex": best_pair.get("dexId", "unknown"),
                            
                            # Цінові дані
                            "price_usd": float(best_pair.get("priceUsd", 0)),
                            "price_native": float(best_pair.get("priceNative", 0)),
                            
                            # Market data
                            "market_cap": float(best_pair.get("marketCap", 0)),
                            "liquidity_usd": float(best_pair.get("liquidity", {}).get("usd", 0)),
                            
                            # Volume data
                            "volume_24h": float(best_pair.get("volume", {}).get("h24", 0)),
                            "volume_6h": float(best_pair.get("volume", {}).get("h6", 0)),
                            
                            # Token metadata (найважливіше додання!)
                            "token_symbol": base_token.get("symbol", ""),
                            "token_name": base_token.get("name", ""),
                            "token_address": base_token.get("address", ""),
                            
                            # Quote token info
                            "quote_symbol": quote_token.get("symbol", ""),
                            "quote_name": quote_token.get("name", ""),
                            
                            # Pair age
                            "pair_created_at": best_pair.get("pairCreatedAt", 0)
                        }
                        
                        return token_data
                    else:
                        return {"has_pairs": False}
                else:
                    return {"has_pairs": False}
                    
        except Exception as e:
            return {"has_pairs": False}
    
    async def calculate_price_from_public_data(self, public_data: Dict[str, Any], token_mint: str, sol_price_usd: float = 0.0) -> Dict[str, float]:
        """Розраховуємо ціну токена з публічних даних"""
        try:
            if not public_data.get("has_pairs", False):
                return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "no_pairs"}
            
            if sol_price_usd == 0:
                return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "no_sol_price"}
            
            # Метод 1: Використовуємо priceNative (найточніший)
            price_native = public_data.get("price_native", 0)
            if price_native > 0:
                price_usd = price_native * sol_price_usd
                return {
                    "price_sol": price_native,
                    "price_usd": price_usd,
                    "liquidity_usd": public_data.get("liquidity_usd", 0),
                    "method": "price_native"
                }
            
            # Метод 2: Використовуємо priceUsd
            price_usd = public_data.get("price_usd", 0)
            if price_usd > 0:
                price_sol = price_usd / sol_price_usd
                return {
                    "price_sol": price_sol,
                    "price_usd": price_usd,
                    "liquidity_usd": public_data.get("liquidity_usd", 0),
                    "method": "price_usd"
                }
            
            # Метод 3: Розраховуємо через Market Cap (якщо є)
            market_cap = public_data.get("market_cap", 0)
            if market_cap > 0:
                # Отримуємо total supply
                supply_data = await self._make_rpc_call("getTokenSupply", [token_mint])
                if supply_data and not supply_data.get("error"):
                    total_supply = float(supply_data.get("value", {}).get("uiAmount", 0))
                    if total_supply > 0:
                        price_usd = market_cap / total_supply
                        price_sol = price_usd / sol_price_usd
                        return {
                            "price_sol": price_sol,
                            "price_usd": price_usd,
                            "liquidity_usd": public_data.get("liquidity_usd", 0),
                            "method": "market_cap"
                        }
            
            return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "calculation_failed"}
            
        except Exception as e:
            return {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0, "method": "error"}
    
    async def get_all_balances(self, wallet_address: str, show_zero_balances: bool = False) -> List[TokenBalance]:
        """Получение всех балансов токенов"""
        print(f"🔍 Получение балансов для кошелька: {wallet_address}")
        print(f"🌐 Сеть: {self.network}")
        print("-" * 60)
        
        balances = []
        
        # 1. Получаем цену SOL в USD
        print("💰 Получение цены SOL...")
        sol_price_usd = await self.get_sol_price_usd() if self.network == "mainnet" else 0.0
        
        # 2. Получаем баланс SOL
        print("📊 Получение баланса SOL...")
        sol_balance = await self.get_sol_balance(wallet_address)
        
        sol_token = TokenBalance(
            mint=self.SOL_MINT,
            amount=int(sol_balance * 1_000_000_000),  # В lamports
            decimals=9,
            uiAmount=sol_balance,
            symbol="SOL",
            name="Solana",
            price_usd=sol_price_usd,
            value_usd=sol_balance * sol_price_usd
        )
        balances.append(sol_token)
        
        # 3. Получаем все токен-аккаунты
        print("📊 Получение токен-аккаунтов...")
        token_accounts = await self.get_token_accounts(wallet_address)
        
        if not token_accounts:
            print("ℹ️ Токен-аккаунты не найдены")
            return balances
        
        print(f"✅ Найдено {len(token_accounts)} токен-аккаунтов")
        
        # 4. Обрабатываем каждый токен-аккаунт ПАРАЛЕЛЬНО для максимальної швидкості
        async def process_token(account, index):
            """Обробка одного токена"""
            try:
                account_data = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                
                mint = account_data.get("mint", "")
                token_amount = account_data.get("tokenAmount", {})
                amount = int(token_amount.get("amount", 0))
                decimals = int(token_amount.get("decimals", 0))
                ui_amount = float(token_amount.get("uiAmount", 0))
                
                # Пропускаємо токени з нульовим балансом (якщо не вказано інше)
                if ui_amount == 0 and not show_zero_balances:
                    return None
                
                # Получаем цену та метадані в одному запиті (ОПТИМІЗОВАНО!)
                if self.network == "mainnet":
                    price_data = await self.get_token_price_simple(mint, sol_price_usd)
                else:
                    price_data = {"price_sol": 0.0, "price_usd": 0.0, "liquidity_usd": 0.0}
                
                # Рассчитываем стоимость токенов
                price_sol = price_data.get("price_sol", 0.0)
                price_usd = price_data.get("price_usd", 0.0)
                liquidity_usd = price_data.get("liquidity_usd", 0.0)
                
                # Отримуємо реальні метадані з DexScreener або fallback
                token_symbol = price_data.get("token_symbol", "") or f"TOKEN_{mint[:8]}"
                token_name = price_data.get("token_name", "") or f"Token {mint[:8]}"
                
                value_sol = ui_amount * price_sol
                value_usd = ui_amount * price_usd
                
                token_balance = TokenBalance(
                    mint=mint,
                    amount=amount,
                    decimals=decimals,
                    uiAmount=ui_amount,
                    symbol=token_symbol,
                    name=token_name,
                    price_usd=price_usd,
                    value_usd=value_usd,
                    price_sol=price_sol,
                    value_sol=value_sol,
                    liquidity_usd=liquidity_usd
                )
                
                # Показуємо символ токена замість mint адреси
                display_name = token_symbol if token_symbol != f"TOKEN_{mint[:8]}" else mint[:8]
                print(f"{'✅' if price_sol > 0 else '⚠️'} {display_name}: {'%.8f SOL' % price_sol if price_sol > 0 else 'Ціна не знайдена'}")
                
                return token_balance
                
            except Exception as e:
                print(f"❌ Ошибка обработки токена {index}: {e}")
                return None
        
        # Запускаємо обробку всіх токенів паралельно
        tasks = [process_token(account, i+1) for i, account in enumerate(token_accounts)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Додаємо успішно оброблені токени
        for result in results:
            if result and not isinstance(result, Exception):
                balances.append(result)
        
        return balances
    
    def analyze_inactive_tokens(self, balances: List[TokenBalance]) -> Dict[str, Any]:
        """Аналіз токенів без активних торгових пар"""
        inactive_tokens = [b for b in balances if b.price_sol == 0 and b.symbol != "SOL"]
        
        if not inactive_tokens:
            return {"count": 0, "tokens": []}
        
        analysis = {
            "count": len(inactive_tokens),
            "tokens": [],
            "total_tokens": len(inactive_tokens)
        }
        
        for token in inactive_tokens:
            token_info = {
                "symbol": token.symbol,
                "mint": token.mint,
                "balance": token.uiAmount,
                "status": "INACTIVE",
                "reason": "Немає активних торгових пар на DEX"
            }
            
            # Дополнительная классификация
            if token.uiAmount == 0:
                token_info["status"] = "ZERO_BALANCE"
                token_info["reason"] = "Нульовий баланс"
            elif token.symbol.startswith("TOKEN_"):
                token_info["status"] = "UNKNOWN_TOKEN"
                token_info["reason"] = "Невідомий токен без метаданих"
            
            analysis["tokens"].append(token_info)
        
        return analysis
    
    def display_balances(self, balances: List[TokenBalance]):
        """Отображение балансов в консоли"""
        print("\n" + "="*80)
        print("💰 БАЛАНСЫ ТОКЕНОВ С ЦЕНАМИ")
        print("="*80)
        print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🌐 Сеть: {self.network}")
        print(f"📊 Всего токенов: {len(balances)}")
        
        # Статистика по токенам
        tokens_with_price = sum(1 for b in balances if b.price_sol > 0)
        tokens_without_price = len(balances) - tokens_with_price
        
        print(f"✅ С ціною: {tokens_with_price}")
        print(f"⚠️ Без ціни: {tokens_without_price}")
        print("-" * 80)
        
        # Сортируем по стоимости в USD (SOL первым)
        sorted_balances = sorted(balances, key=lambda x: (x.symbol != "SOL", -x.value_usd))
        
        total_value_usd = 0
        total_value_sol = 0
        
        for i, balance in enumerate(sorted_balances, 1):
            # Основная информация
            print(f"{i:2d}. {balance.symbol:12s} | {balance.uiAmount:15,.6f} | {balance.name}")
            
            # Цены и стоимость
            if balance.price_sol > 0:
                print(f"    💰 Ціна: {balance.price_sol:.8f} SOL (~${balance.price_usd:.6f})")
                print(f"    💎 Вартість: {balance.value_sol:.6f} SOL (~${balance.value_usd:.2f})")
                if balance.liquidity_usd > 0:
                    print(f"    💧 Ліквідність: ${balance.liquidity_usd:,.2f}")
                total_value_sol += balance.value_sol
                total_value_usd += balance.value_usd
            elif balance.symbol == "SOL":
                print(f"    💰 Ціна: ${balance.price_usd:.2f}")
                print(f"    💎 Вартість: {balance.uiAmount:.6f} SOL (~${balance.value_usd:.2f})")
                total_value_sol += balance.uiAmount
                total_value_usd += balance.value_usd
            else:
                print(f"    💰 Ціна: Не знайдена")
                print(f"    💎 Вартість: Не розрахована")
            
            print(f"    🏷️  Mint: {balance.mint}")
            if i < len(sorted_balances):
                print()
        
        # Итоговая стоимость портфеля
        print("=" * 80)
        print("📊 ИТОГО ПОРТФЕЛЬ:")
        print(f"💎 Общая стоимость: {total_value_sol:.6f} SOL (~${total_value_usd:.2f})")
        print("=" * 80)
    
    def export_to_file(self, balances: List[TokenBalance], wallet_address: str):
        """Экспорт балансов в файл з повними даними"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wallet_short = wallet_address[:8]
        filename = f"result_v1/balance_v1_{wallet_short}_{timestamp}.txt"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write("💰 BALANCE V1 - TOKEN BALANCES (ПОВНІ ДАНІ)\n")
                f.write("="*80 + "\n")
                f.write(f"Wallet Address: {wallet_address}\n")
                f.write(f"Network: {self.network}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Total Tokens: {len(balances)}\n")
                
                # Статистика
                tokens_with_price = sum(1 for b in balances if b.price_sol > 0)
                tokens_without_price = len(balances) - tokens_with_price
                f.write(f"Tokens with price: {tokens_with_price}\n")
                f.write(f"Tokens without price: {tokens_without_price}\n")
                f.write("-" * 80 + "\n\n")
                
                # Сортируем по стоимости в USD (SOL первым)
                sorted_balances = sorted(balances, key=lambda x: (x.symbol != "SOL", -x.value_usd))
                
                total_value_usd = 0
                total_value_sol = 0
                
                for i, balance in enumerate(sorted_balances, 1):
                    f.write(f"{i:2d}. {balance.symbol:15s} | {balance.uiAmount:20.6f} | {balance.name}\n")
                    f.write(f"    Mint: {balance.mint}\n")
                    f.write(f"    Amount: {balance.amount:,.0f}\n")
                    f.write(f"    Decimals: {balance.decimals}\n")
                    
                    # Ціни та вартість
                    if balance.price_sol > 0:
                        f.write(f"    Price: {balance.price_sol:.8f} SOL (~${balance.price_usd:.6f})\n")
                        f.write(f"    Value: {balance.value_sol:.6f} SOL (~${balance.value_usd:.2f})\n")
                        if balance.liquidity_usd > 0:
                            f.write(f"    Liquidity: ${balance.liquidity_usd:,.2f}\n")
                        total_value_sol += balance.value_sol
                        total_value_usd += balance.value_usd
                    elif balance.symbol == "SOL":
                        f.write(f"    Price: ${balance.price_usd:.2f}\n")
                        f.write(f"    Value: {balance.uiAmount:.6f} SOL (~${balance.value_usd:.2f})\n")
                        total_value_sol += balance.uiAmount
                        total_value_usd += balance.value_usd
                    else:
                        f.write(f"    Price: Not found\n")
                        f.write(f"    Value: Not calculated\n")
                    
                    if i < len(sorted_balances):
                        f.write("\n")
                
                # Итоговая стоимость портфеля
                f.write("=" * 80 + "\n")
                f.write("📊 PORTFOLIO SUMMARY:\n")
                f.write(f"Total Value: {total_value_sol:.6f} SOL (~${total_value_usd:.2f})\n")
                f.write("=" * 80 + "\n")
            
            print(f"📁 Результаты сохранены в: {filename}")
            
        except Exception as e:
            print(f"❌ Ошибка сохранения файла: {e}")


def test_calculations():
    """Тестування розрахункових методів з відомими даними"""
    print("\n" + "="*80)
    print("🧮 ТЕСТ РОЗРАХУНКОВИХ МЕТОДІВ")
    print("="*80)
    
    balance_checker = BalanceV1("mainnet")
    
    # Тестові дані для ERK токена (якщо Phantom показує $3.94)
    test_balance = 475752.106974  # Наш баланс
    test_price_usd = 3.94  # Ціна з Phantom
    sol_price = 201.66  # Поточна ціна SOL
    
    # Розрахунки
    wallet_value_usd = test_balance * test_price_usd
    price_sol = test_price_usd / sol_price
    wallet_value_sol = test_balance * price_sol
    
    print(f"📊 Тестові розрахунки для ERK:")
    print(f"   💰 Баланс: {test_balance:,.6f} ERK")
    print(f"   💵 Ціна токена: ${test_price_usd}")
    print(f"   💵 Ціна токена: {price_sol:.8f} SOL")
    print(f"   💎 Вартість портфеля: ${wallet_value_usd:,.2f}")
    print(f"   💎 Вартість портфеля: {wallet_value_sol:.6f} SOL")
    
    # Зворотний розрахунок Market Cap
    total_supply = 998265707  # Отримали з RPC
    implied_market_cap = test_price_usd * total_supply
    
    print(f"\n📈 Зворотні розрахунки:")
    print(f"   🏭 Total Supply: {total_supply:,.0f}")
    print(f"   📊 Implied Market Cap: ${implied_market_cap:,.0f}")
    print(f"   🧮 Перевірка: MC/Supply = ${implied_market_cap/total_supply:.6f}")
    
    print("="*80)


async def main():
    """Основная функция"""
    # Проверяем аргументы командной строки
    if len(sys.argv) < 2:
        print("❌ Использование: python balance_v1.py <wallet_address> [network] [options]")
        print("   network: devnet (по умолчанию) або mainnet")
        print("   Options:")
        print("     --test: запустити тест розрахунків")
        print("     --show-zero або --all: показати всі токени (включаючи з нульовим балансом)")
        print("   Примеры:")
        print("     python balance_v1.py 78ZxSp4jxZQ2p3ZUXETmsWDbmQHUqu5gaBFSRkFSsDxv mainnet")
        print("     python balance_v1.py 78ZxSp4jxZQ2p3ZUXETmsWDbmQHUqu5gaBFSRkFSsDxv mainnet --show-zero")
        print("     python balance_v1.py test --test")
        return
    
    # Перевіряємо на тест
    if "--test" in sys.argv or sys.argv[1] == "test":
        test_calculations()
        return
    
    wallet_address = sys.argv[1]
    network = sys.argv[2] if len(sys.argv) > 2 else "devnet"
    
    if network not in ["devnet", "mainnet"]:
        print("❌ Неверная сеть. Используйте 'devnet' або 'mainnet'")
        return
    
    async with BalanceV1(network=network) as balance_checker:
        print("🚀 Запуск Balance V1...")
        print(f"📍 Кошелек: {wallet_address}")
        
        # Проверяем нужно ли показывать все токены (включая с нулевым балансом)
        show_zero = "--show-zero" in sys.argv or "--all" in sys.argv
        if show_zero:
            print("ℹ️ Режим показу всіх токенів (включаючи з нульовим балансом)")
        
        # Получаем все балансы
        balances = await balance_checker.get_all_balances(wallet_address, show_zero_balances=show_zero)
        
        # Отображаем результаты
        balance_checker.display_balances(balances)
        
        # Анализируем неактивные токены
        if network == "mainnet":
            print("\n" + "="*80)
            print("🔍 АНАЛІЗ НЕАКТИВНИХ ТОКЕНІВ")
            print("="*80)
            
            inactive_analysis = balance_checker.analyze_inactive_tokens(balances)
            if inactive_analysis["count"] > 0:
                print(f"⚠️ Знайдено {inactive_analysis['count']} токенів без ціни:")
                for i, token in enumerate(inactive_analysis["tokens"], 1):
                    print(f"{i:2d}. {token['symbol']:15s} | {token['balance']:15,.6f}")
                    print(f"    📝 Статус: {token['status']}")
                    print(f"    💬 Причина: {token['reason']}")
                    print(f"    🏷️ Mint: {token['mint']}")
                    if i < len(inactive_analysis["tokens"]):
                        print()
            else:
                print("✅ Всі токени мають ціни на DEX")
            print("="*80)
        
        # Сохраняем в файл
        balance_checker.export_to_file(balances, wallet_address)


if __name__ == "__main__":
    asyncio.run(main())
