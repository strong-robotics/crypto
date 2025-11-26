#!/usr/bin/env python3
"""
Тестовий скрипт для покупки токена через Helius (використовує execute_buy_helius)
"""

import sys
import os
import json
import asyncio

# Додати server до шляху для імпортів
server_path = os.path.join(os.path.dirname(__file__), 'server')
sys.path.insert(0, server_path)

from solders.keypair import Keypair
from _v2_buy_sell import execute_buy_helius
from _v2_sol_price import get_sol_price_monitor, get_current_sol_price
from config import config

# Параметри тесту
TOKEN_ADDRESS = "8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR"
TOKEN_DECIMALS = 6  # Більшість токенів використовує 6 decimals
AMOUNT_USD = 0.9
KEY_ID = 2


def load_key_from_file(key_id: int) -> Keypair:
    """Завантажуємо приватний ключ з keys.json по ID"""
    keys_file = os.path.join(server_path, 'keys.json')
    with open(keys_file) as f:
        keys = json.load(f)

    for k in keys:
        if k["id"] == key_id:
            return Keypair.from_bytes(bytes(k["bits"]))

    raise ValueError(f"❌ Ключ з id={key_id} не знайдено")


async def test_helius_buy():
    """Тестова функція для покупки токена через Helius"""
    print("=" * 80)
    print("🧪 ТЕСТ ПОКУПКИ ТОКЕНА ЧЕРЕЗ HELIUS")
    print("=" * 80)
    print(f"🪙 Токен: {TOKEN_ADDRESS}")
    print(f"💰 Сума: ${AMOUNT_USD:.2f} USD")
    print(f"🔑 Кошелек: key-id {KEY_ID}")
    print()
    
    try:
        # Запустити монітор ціни SOL (якщо не запущений)
        print("0️⃣ Запуск монітора ціни SOL...")
        monitor = await get_sol_price_monitor(update_interval=1, debug=True)
        await asyncio.sleep(2)  # Дати час на отримання першої ціни
        sol_price = get_current_sol_price()
        if sol_price <= 0:
            print("   ⚠️  Не вдалося отримати ціну SOL, спробуємо продовжити...")
        else:
            print(f"   ✅ Ціна SOL: ${sol_price:.2f}")
        print()
        
        # Завантажити ключ
        print("1️⃣ Завантаження ключа...")
        keypair = load_key_from_file(KEY_ID)
        print(f"   ✅ Ключ завантажено: {keypair.pubkey()}")
        print()
        
        # Викликати execute_buy_helius
        print("2️⃣ Виконання покупки через Helius...")
        print("   (Jupiter для quote/swap, Helius для відправки транзакції)")
        print()
        
        result = await execute_buy_helius(
            token_id=0,  # Не використовується в execute_buy_helius, можна передати 0
            keypair=keypair,
            amount_usd=AMOUNT_USD,
            token_address=TOKEN_ADDRESS,
            token_decimals=TOKEN_DECIMALS
        )
        
        print()
        print("=" * 80)
        if result.get("success"):
            print("✅ ПОКУПКА УСПІШНА!")
            print("=" * 80)
            print(f"📝 Signature: {result.get('signature')}")
            print(f"🪙 Отримано токенів: {result.get('amount_tokens', 0):,.6f}")
            print(f"💰 Сума: ${result.get('amount_usd', 0):.2f} USD")
            print(f"💵 Ціна токена: ${result.get('price_usd', 0):.8f} USD")
            print(f"💎 SOL витрачено: {result.get('sol_amount', 0):.8f} SOL")
            if result.get('signature'):
                print(f"🔗 Solscan: https://solscan.io/tx/{result.get('signature')}")
        else:
            print("❌ ПОКУПКА НЕ ВДАЛАСЯ")
            print("=" * 80)
            print(f"⚠️  Помилка: {result.get('message', 'Unknown error')}")
        print("=" * 80)
        
        return result
        
    except Exception as e:
        print()
        print("=" * 80)
        print("❌ ПОМИЛКА ПІД ЧАС ВИКОНАННЯ")
        print("=" * 80)
        print(f"⚠️  {str(e)}")
        import traceback
        traceback.print_exc()
        print("=" * 80)
        return {"success": False, "message": str(e)}


if __name__ == "__main__":
    result = asyncio.run(test_helius_buy())
    sys.exit(0 if result.get("success") else 1)

