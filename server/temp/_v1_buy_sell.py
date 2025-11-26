#!/usr/bin/env python3
"""
Універсальний бот для торгівлі токенами
Приклади:
    python trade.py --mode buy --amount 1.2 --key-id 2
    python trade.py --mode sell --amount 0.8 --key-id 1

    python3 _v1_buy_sell.py --mode sell --amount 0.9 --key-id 2 --token-address 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR
    python3 _v1_buy_sell.py --mode buy --amount 0.9 --key-id 2 --token-address 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR
"""

import os
import requests, base64, json, argparse, sys
from typing import Optional, List, Tuple, Dict
import asyncio
from _v3_db_pool import get_db_pool
from config import config
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from ai.patterns.catalog import PatternCode, PATTERN_SEED

# === Конфігурація ===
RPC = "https://mainnet.helius-rpc.com/?api-key=276cdc23-e3c7-4847-81f6-d8114f92e4c5"
JUP = "https://lite-api.jup.ag/swap/v1"

# === Токени ===
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_DECIMALS = 9

# Більшість токенів використовує 6 decimals
TOKEN_DECIMALS = 6
TARGET_RETURN = float(getattr(config, 'TARGET_RETURN', 0.20))
TARGET_MULT = 1.0 + TARGET_RETURN
ENTRY_MIN_SECONDS = int(getattr(config, 'SIM_ENTRY_ITERATION', 60))
MAX_TOKEN_AGE_SEC = int(getattr(config, 'ETA_MAX_TOKEN_AGE_SEC', int(os.getenv("SIM_MAX_TOKEN_AGE_SEC", "120"))))
MAX_WAIT_ITERATIONS = int(os.getenv("SIM_MAX_WAIT_ITER", "80"))
CANDIDATE_AMOUNTS = [100.0, 80.0, 60.0, 50.0, 40.0, 30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 7.5, 5.0]
GOOD_PATTERN_CODES = tuple(
    str(item["code"].value if hasattr(item["code"], "value") else item["code"])
    for item in PATTERN_SEED
    if item.get("tier") == "top" and item.get("code") not in (PatternCode.UNKNOWN,)
)

CANDIDATE_AMOUNTS = [100.0, 80.0, 60.0, 50.0, 40.0, 30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 7.5, 5.0]


def load_key_from_file(key_id: int):
    """Завантажуємо приватний ключ з keys.json по ID"""
    with open("keys.json") as f:
        keys = json.load(f)

    for k in keys:
        if k["id"] == key_id:
            return Keypair.from_bytes(bytes(k["bits"]))

    print(f"❌ Ключ з id={key_id} не знайдено")
    sys.exit(1)


def get_sol_price():
    """Отримати курс SOL → USDC"""
    test_amount = int(0.1 * (10**SOL_DECIMALS))
    quote = requests.get(f"{JUP}/quote", params={
        "inputMint": SOL_MINT,
        "outputMint": USDC_MINT,
        "amount": test_amount,
        "slippageBps": 50
    }).json()
    return int(quote["outAmount"]) / (10**6) / 0.1


async def get_free_wallet(conn, exclude_key_id: Optional[int] = None) -> Optional[Dict]:
    """Знайти вільний реальний кошелек з keys.json.
    
    Вільний = не використовується жодним токеном (wallet_id IS NULL для всіх токенів з цим key_id)
    Або не має відкритої позиції в wallet_history
    
    Returns:
        dict with 'key_id', 'keypair', 'address' or None if no free wallet
    """
    try:
        with open(config.WALLET_KEYS_FILE) as f:
            keys = json.load(f)
        
        # Get all currently used key_ids from tokens (wallet_id) and wallet_history (open positions)
        used_key_ids = set()
        if exclude_key_id:
            used_key_ids.add(exclude_key_id)
        # Check tokens.wallet_id
        rows = await conn.fetch("SELECT DISTINCT wallet_id FROM tokens WHERE wallet_id IS NOT NULL")
        for row in rows:
            if row["wallet_id"]:
                used_key_ids.add(int(row["wallet_id"]))
        # Check wallet_history for open positions
        open_rows = await conn.fetch("SELECT DISTINCT wallet_id FROM wallet_history WHERE exit_iteration IS NULL")
        for row in open_rows:
            if row["wallet_id"]:
                used_key_ids.add(int(row["wallet_id"]))
        
        # Find first free wallet
        for k in keys:
            key_id = k.get("id")
            if key_id is None:
                continue
            if int(key_id) not in used_key_ids:
                try:
                    kp = Keypair.from_bytes(bytes(k["bits"]))
                    return {
                        "key_id": key_id,
                        "keypair": kp,
                        "address": str(kp.pubkey())
                    }
                except Exception:
                    continue
        
        return None
    except Exception as e:
        print(f"[get_free_wallet] Error: {e}")
        return None


async def get_wallet_balance_sol(keypair: Keypair) -> float:
    """Отримати баланс SOL для реального кошелька"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [str(keypair.pubkey())]
        }
        res = requests.post(RPC, json=payload, timeout=10).json()
        if "result" in res:
            lamports = res["result"]["value"]
            return lamports / (10**SOL_DECIMALS)
        return 0.0
    except Exception:
        return 0.0


async def execute_buy(token_id: int, keypair: Keypair, amount_usd: float, token_address: str, token_decimals: int) -> Dict:
    """Виконати реальну покупку токена через Jupiter API.
    
    Returns:
        dict with success, signature, amount_tokens, price_usd, etc.
    """
    try:
        sol_price = get_sol_price()
        if sol_price <= 0:
            return {"success": False, "message": "Failed to get SOL price"}
        
        # Check balance
        balance_sol = await get_wallet_balance_sol(keypair)
        if balance_sol <= 0:
            return {"success": False, "message": "Insufficient SOL balance"}
        
        # Calculate amount in SOL
        sol_need = amount_usd / sol_price
        if sol_need > balance_sol * 0.95:  # Leave 5% for fees
            return {"success": False, "message": "Insufficient SOL balance (need fee buffer)"}
        
        raw_amount = int(sol_need * (10**SOL_DECIMALS))
        
        # Get quote
        quote = requests.get(f"{JUP}/quote", params={
            "inputMint": SOL_MINT,
            "outputMint": token_address,
            "amount": raw_amount,
            "slippageBps": 200
        }, timeout=10).json()
        
        if "error" in quote:
            return {"success": False, "message": f"Quote error: {quote.get('error', 'Unknown')}"}
        
        amount_tokens = int(quote["outAmount"]) / (10**token_decimals)
        token_price_usd = amount_usd / amount_tokens if amount_tokens > 0 else 0
        
        # Build swap transaction
        swap = requests.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "computeUnitPriceMicroLamports": 10000  # Priority fee
        }, timeout=10).json()
        
        if "error" in swap:
            return {"success": False, "message": f"Swap error: {swap.get('error', 'Unknown')}"}
        
        # Sign and send transaction
        tx_bytes = base64.b64decode(swap["swapTransaction"])
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        vtx = VersionedTransaction(vtx.message, [keypair])
        
        signed_tx = base64.b64encode(bytes(vtx)).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "sendTransaction",
            "params": [signed_tx, {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False}]
        }
        
        res = requests.post(RPC, json=payload, timeout=30).json()
        
        if "error" in res:
            return {"success": False, "message": f"Transaction error: {res['error']}"}
        
        signature = res.get("result")
        
        return {
            "success": True,
            "signature": signature,
            "amount_tokens": amount_tokens,
            "amount_usd": amount_usd,
            "price_usd": token_price_usd,
            "sol_amount": sol_need
        }
    except Exception as e:
        return {"success": False, "message": f"Exception: {str(e)}"}


async def execute_sell(token_id: int, keypair: Keypair, token_address: str, token_amount: float, token_decimals: int, min_price_usd: float = None) -> Dict:
    """Виконати реальну продажу токена через Jupiter API.
    
    Returns:
        dict with success, signature, amount_sol, amount_usd, etc.
    """
    try:
        sol_price = get_sol_price()
        if sol_price <= 0:
            return {"success": False, "message": "Failed to get SOL price"}
        
        # Get current token price
        test_amount = 1000 * (10**token_decimals)
        q_test = requests.get(f"{JUP}/quote", params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": test_amount,
            "slippageBps": 50
        }, timeout=10).json()
        
        if "error" in q_test:
            return {"success": False, "message": f"Price check error: {q_test.get('error', 'Unknown')}"}
        
        sol_out = int(q_test["outAmount"]) / (10**SOL_DECIMALS)
        token_price_usd = (sol_out / 1000) * sol_price
        
        # Validate minimum price
        if min_price_usd and token_price_usd < min_price_usd:
            return {
                "success": False,
                "message": f"Current price (${token_price_usd:.8f}) below minimum (${min_price_usd:.8f})",
                "current_price": token_price_usd,
                "min_price": min_price_usd
            }
        
        # Calculate amount to sell
        raw_amount = int(token_amount * (10**token_decimals))
        
        # Get quote
        quote = requests.get(f"{JUP}/quote", params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": raw_amount,
            "slippageBps": 200
        }, timeout=10).json()
        
        if "error" in quote:
            return {"success": False, "message": f"Quote error: {quote.get('error', 'Unknown')}"}
        
        expected_sol = int(quote["outAmount"]) / (10**SOL_DECIMALS)
        expected_usd = expected_sol * sol_price
        
        # Build swap transaction
        swap = requests.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "computeUnitPriceMicroLamports": 10000  # Priority fee
        }, timeout=10).json()
        
        if "error" in swap:
            return {"success": False, "message": f"Swap error: {swap.get('error', 'Unknown')}"}
        
        # Sign and send transaction
        tx_bytes = base64.b64decode(swap["swapTransaction"])
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        vtx = VersionedTransaction(vtx.message, [keypair])
        
        signed_tx = base64.b64encode(bytes(vtx)).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "sendTransaction",
            "params": [signed_tx, {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False}]
        }
        
        res = requests.post(RPC, json=payload, timeout=30).json()
        
        if "error" in res:
            return {"success": False, "message": f"Transaction error: {res['error']}"}
        
        signature = res.get("result")
        
        return {
            "success": True,
            "signature": signature,
            "amount_sol": expected_sol,
            "amount_usd": expected_usd,
            "price_usd": token_price_usd
        }
    except Exception as e:
        return {"success": False, "message": f"Exception: {str(e)}"}


def trade_tokens(kp: Keypair, usd_amount: float, mode: str, token_address: str):
    """Купівля або продаж токенів за адресою"""
    print(f"\n🎯 Режим: {mode.upper()} | Сума: ${usd_amount:.2f}")
    print(f"👤 Кошелек: {kp.pubkey()}")

    # 1. Отримуємо курс SOL
    sol_price = get_sol_price()
    print(f"   1 SOL ≈ ${sol_price:.2f}")

    # Показуємо адресу токену
    print(f"🪙 Токен: {token_address}")

    # 2. BUY → SOL -> TOKEN
    if mode == "buy":
        print("\n" + "="*80)
        print("📊 ДЕТАЛЬНА ІНФОРМАЦІЯ ПРО ПОКУПКУ")
        print("="*80)
        
        # Отримуємо decimals токена з БД або використовуємо дефолт
        token_decimals = TOKEN_DECIMALS
        try:
            pool = asyncio.run(get_db_pool())
            async def get_decimals():
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT decimals FROM tokens WHERE token_address=$1",
                        token_address
                    )
                    if row and row["decimals"]:
                        return int(row["decimals"])
                return TOKEN_DECIMALS
            token_decimals = asyncio.run(get_decimals())
        except Exception as e:
            print(f"   ⚠️ Не вдалося отримати decimals з БД, використовуємо дефолт {TOKEN_DECIMALS}: {e}")
        
        print(f"   Token decimals: {token_decimals}")
        
        # Перевірка балансу SOL у кошельку
        print(f"\n1️⃣ Перевірка балансу кошелька:")
        balance_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [str(kp.pubkey())]
        }
        balance_resp = requests.post(RPC, json=balance_payload).json()
        sol_balance = 0.0
        if "result" in balance_resp:
            sol_balance_raw = balance_resp["result"].get("value", 0)
            sol_balance = float(sol_balance_raw) / (10**SOL_DECIMALS)
        print(f"   Баланс SOL у кошельку: {sol_balance:.8f} SOL (${sol_balance * sol_price:.2f} USD)")
        
        # Розрахунок необхідної кількості SOL
        sol_need = usd_amount / sol_price
        print(f"\n2️⃣ Розрахунок для покупки:")
        print(f"   Цільова сума: ${usd_amount:.2f} USD")
        print(f"   Ціна SOL: ${sol_price:.2f} USD")
        print(f"   Необхідно SOL: {sol_need:.8f} SOL")
        
        # Перевірка достатності балансу (з урахуванням комісій)
        estimated_fee = 0.00001  # Приблизна комісія за транзакцію
        if sol_balance < (sol_need + estimated_fee):
            print(f"   ⚠️ УВАГА: Недостатньо SOL! Маємо {sol_balance:.8f} SOL, потрібно {sol_need + estimated_fee:.8f} SOL (включаючи комісії)")
            return
        
        raw_amount = int(sol_need * (10**SOL_DECIMALS))
        print(f"   Raw amount (для Jupiter): {raw_amount} (units)")
        
        # Тестовий запит для отримання ціни токена
        print(f"\n3️⃣ Тестовий запит для отримання ціни токена:")
        test_amount = 1000 * (10**token_decimals)
        q_test = requests.get(f"{JUP}/quote", params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": test_amount,
            "slippageBps": 50
        }).json()
        
        if "error" in q_test:
            print(f"   ❌ Помилка тестового запиту: {q_test['error']}")
            return
        
        sol_for_test = int(q_test["outAmount"]) / (10**SOL_DECIMALS)
        token_price_usd = (sol_for_test / 1000) * sol_price
        print(f"   Test amount: {test_amount} (raw units = 1000 tokens)")
        print(f"   1000 токенів → {sol_for_test:.6f} SOL")
        print(f"   1 токен → ${token_price_usd:.8f} USD")
        
        # Запит котировки на покупку
        print(f"\n4️⃣ Запит котировки на покупку:")
        print(f"   Input mint: {SOL_MINT}")
        print(f"   Output mint: {token_address}")
        print(f"   Amount: {raw_amount} (raw units = {sol_need:.8f} SOL)")
        print(f"   Slippage BPS: 200 (2%)")
        
        quote = requests.get(f"{JUP}/quote", params={
            "inputMint": SOL_MINT,
            "outputMint": token_address,
            "amount": raw_amount,
            "slippageBps": 200
        }).json()
        
        if "error" in quote:
            print(f"   ❌ Помилка котировки: {quote['error']}")
            return
        
        print(f"   ✅ Котировка отримана")
        print(f"   Quote response keys: {list(quote.keys())}")
        
        # Детальна інформація з quote
        out_amount_raw = int(quote.get('outAmount', 0))
        out_amount_tokens = out_amount_raw / (10**token_decimals)
        out_amount_usd = out_amount_tokens * token_price_usd
        
        print(f"\n   📊 Деталі котировки:")
        print(f"      Out amount (raw): {out_amount_raw}")
        print(f"      Out amount (tokens): {out_amount_tokens:,.6f} tokens")
        print(f"      Out amount (USD): ${out_amount_usd:.2f} USD")
        
        # Розрахунок комісій та slippage
        expected_tokens = sol_need / (sol_for_test / 1000)  # Очікувана кількість токенів за тестовою ціною
        actual_tokens = out_amount_tokens
        slippage_tokens = expected_tokens - actual_tokens
        slippage_pct = (slippage_tokens / expected_tokens * 100) if expected_tokens > 0 else 0
        
        print(f"\n   💰 Порівняння очікуваного vs фактичного:")
        print(f"      Очікувано (за тестовою ціною): {expected_tokens:,.6f} tokens = ${expected_tokens * token_price_usd:.2f} USD")
        print(f"      Фактично (за котировкою): {actual_tokens:,.6f} tokens = ${actual_tokens * token_price_usd:.2f} USD")
        print(f"      Різниця (slippage): {slippage_tokens:,.6f} tokens = ${slippage_tokens * token_price_usd:.2f} USD ({slippage_pct:.2f}%)")
        
        # Комісії Jupiter (якщо є в quote)
        if 'priceImpactPct' in quote:
            try:
                price_impact = float(quote['priceImpactPct']) if quote['priceImpactPct'] else 0.0
                print(f"   📉 Price impact: {price_impact:.4f}%")
            except (ValueError, TypeError):
                print(f"   📉 Price impact: {quote['priceImpactPct']}")
        if 'platformFee' in quote and quote['platformFee']:
            fee_info = quote['platformFee']
            print(f"   💸 Platform fee: {fee_info}")
        if 'fee' in quote:
            fee_info = quote['fee']
            print(f"   💸 Fee info: {fee_info}")
        
        print(f"\n   Очікувано отримаємо: {out_amount_tokens:,.6f} токенів (${out_amount_usd:.2f} USD)")

        # 5. Будуємо транзакцію
        print(f"\n5️⃣ Побудова транзакції:")
        print(f"   User public key: {kp.pubkey()}")
        print(f"   Compute unit price: 10000 microLamports (priority fee)")
        
        swap = requests.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(kp.pubkey()),
            "computeUnitPriceMicroLamports": 10000  # Priority fee
        }).json()
        
        if "error" in swap:
            print(f"   ❌ Помилка swap: {swap['error']}")
            return
        
        print(f"   ✅ Swap транзакція створена")
        print(f"   Swap response keys: {list(swap.keys())}")
        
        # 6. Підписуємо і відправляємо
        print(f"\n6️⃣ Підпис та відправка транзакції:")
        tx_bytes = base64.b64decode(swap["swapTransaction"])
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        vtx = VersionedTransaction(vtx.message, [kp])
        
        # Відправка через RPC
        signed_tx = base64.b64encode(bytes(vtx)).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "sendTransaction",
            "params": [signed_tx, {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False}]
        }
        print(f"   Відправляємо транзакцію через RPC...")
        res = requests.post(RPC, json=payload, timeout=30).json()
        
        if "error" in res:
            print(f"   ❌ Помилка відправки: {res['error']}")
            return
        
        sig = res.get("result")
        print(f"\n" + "="*80)
        print(f"✅ ТРАНЗАКЦІЯ ВІДПРАВЛЕНА УСПІШНО!")
        print(f"="*80)
        print(f"   Signature: {sig}")
        print(f"   Solscan: https://solscan.io/tx/{sig}")
        print(f"\n   📋 Підсумок:")
        print(f"      Потрачено: {sol_need:.8f} SOL (${usd_amount:.2f} USD)")
        print(f"      Отримано: {out_amount_tokens:,.6f} токенів (${out_amount_usd:.2f} USD)")
        print(f"      Slippage: {slippage_pct:.2f}%")
        if 'priceImpactPct' in quote:
            try:
                price_impact = float(quote['priceImpactPct']) if quote['priceImpactPct'] else 0.0
                print(f"      Price impact: {price_impact:.4f}%")
            except (ValueError, TypeError):
                print(f"      Price impact: {quote['priceImpactPct']}")
        print(f"      Transaction fee: ~0.00001 SOL (~${estimated_fee * sol_price:.4f} USD) - списано з балансу")
        print(f"="*80 + "\n")

    # 3. SELL → TOKEN -> SOL
    elif mode == "sell":
        print("\n" + "="*80)
        print("📊 ДЕТАЛЬНА ІНФОРМАЦІЯ ПРО ПРОДАЖ")
        print("="*80)
        
        # Отримуємо decimals токена з БД або використовуємо дефолт
        token_decimals = TOKEN_DECIMALS
        try:
            pool = asyncio.run(get_db_pool())
            async def get_decimals():
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT decimals FROM tokens WHERE token_address=$1",
                        token_address
                    )
                    if row and row["decimals"]:
                        return int(row["decimals"])
                return TOKEN_DECIMALS
            token_decimals = asyncio.run(get_decimals())
        except Exception as e:
            print(f"   ⚠️ Не вдалося отримати decimals з БД, використовуємо дефолт {TOKEN_DECIMALS}: {e}")
        
        print(f"   Token decimals: {token_decimals}")
        
        # курс токену через SOL
        test_amount = 1000 * (10**token_decimals)
        print(f"\n1️⃣ Тестовий запит для отримання ціни токена:")
        print(f"   Test amount: {test_amount} (raw units)")
        print(f"   Input mint: {token_address}")
        print(f"   Output mint: {SOL_MINT}")
        print(f"   Slippage BPS: 50 (0.5%)")
        
        q_test = requests.get(f"{JUP}/quote", params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": test_amount,
            "slippageBps": 50
        }).json()
        
        if "error" in q_test:
            print(f"   ❌ Помилка тестового запиту: {q_test['error']}")
            return
        
        print(f"   ✅ Тестовий запит успішний")
        print(f"   Quote response keys: {list(q_test.keys())}")
        
        sol_out = int(q_test["outAmount"]) / (10**SOL_DECIMALS)
        token_price_usd = (sol_out / 1000) * sol_price
        print(f"\n   📈 Ціна токена:")
        print(f"     1000 токенів → {sol_out:.6f} SOL")
        print(f"     1 токен → {sol_out/1000:.8f} SOL")
        print(f"     1 токен → ${token_price_usd:.8f} USD (SOL price: ${sol_price:.2f})")

        tokens_to_sell = usd_amount / token_price_usd
        raw_amount = int(tokens_to_sell * (10**token_decimals))
        print(f"\n2️⃣ Розрахунок кількості для продажу:")
        print(f"   Цільова сума: ${usd_amount:.2f} USD")
        print(f"   Ціна токена: ${token_price_usd:.8f} USD")
        print(f"   Токенів для продажу: {tokens_to_sell:,.6f}")
        print(f"   Raw amount (для Jupiter): {raw_amount} (units)")
        
        # Отримуємо реальний баланс токенів у кошельку
        print(f"\n3️⃣ Перевірка балансу кошелька:")
        balance_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                str(kp.pubkey()),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ]
        }
        balance_resp = requests.post(RPC, json=balance_payload).json()
        token_balance = 0.0
        if "result" in balance_resp and balance_resp["result"]["value"]:
            for account in balance_resp["result"]["value"]:
                if account["account"]["data"]["parsed"]["info"]["mint"] == token_address:
                    token_balance_raw = account["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]
                    token_balance = float(token_balance_raw) / (10**token_decimals)
                    break
        print(f"   Баланс токенів у кошельку: {token_balance:,.6f} токенів")
        if token_balance < tokens_to_sell:
            print(f"   ⚠️ УВАГА: Недостатньо токенів! Маємо {token_balance:.6f}, потрібно {tokens_to_sell:.6f}")
            return

        print(f"\n4️⃣ Запит котировки на продаж:")
        print(f"   Input mint: {token_address}")
        print(f"   Output mint: {SOL_MINT}")
        print(f"   Amount: {raw_amount} (raw units = {tokens_to_sell:.6f} tokens)")
        print(f"   Slippage BPS: 200 (2%)")
        
        quote = requests.get(f"{JUP}/quote", params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": raw_amount,
            "slippageBps": 200
        }).json()
        
        if "error" in quote:
            print(f"   ❌ Помилка котировки: {quote['error']}")
            return
        
        print(f"   ✅ Котировка отримана")
        print(f"   Quote response keys: {list(quote.keys())}")
        
        # Детальна інформація з quote
        out_amount_raw = int(quote.get('outAmount', 0))
        out_amount_sol = out_amount_raw / (10**SOL_DECIMALS)
        out_amount_usd = out_amount_sol * sol_price
        
        print(f"\n   📊 Деталі котировки:")
        print(f"      Out amount (raw): {out_amount_raw}")
        print(f"      Out amount (SOL): {out_amount_sol:.8f} SOL")
        print(f"      Out amount (USD): ${out_amount_usd:.2f} USD")
        
        # Розрахунок комісій та slippage
        expected_sol = tokens_to_sell * (sol_out / 1000)  # Очікувана кількість SOL за ціною з тесту
        actual_sol = out_amount_sol
        slippage_sol = expected_sol - actual_sol
        slippage_pct = (slippage_sol / expected_sol * 100) if expected_sol > 0 else 0
        
        print(f"\n   💰 Порівняння очікуваного vs фактичного:")
        print(f"      Очікувано (за тестовою ціною): {expected_sol:.8f} SOL = ${expected_sol * sol_price:.2f} USD")
        print(f"      Фактично (за котировкою): {actual_sol:.8f} SOL = ${actual_sol * sol_price:.2f} USD")
        print(f"      Різниця (slippage): {slippage_sol:.8f} SOL = ${slippage_sol * sol_price:.2f} USD ({slippage_pct:.2f}%)")
        
        # Комісії Jupiter (якщо є в quote)
        if 'priceImpactPct' in quote:
            try:
                price_impact = float(quote['priceImpactPct']) if quote['priceImpactPct'] else 0.0
                print(f"   📉 Price impact: {price_impact:.4f}%")
            except (ValueError, TypeError):
                print(f"   📉 Price impact: {quote['priceImpactPct']}")
        if 'platformFee' in quote and quote['platformFee']:
            fee_info = quote['platformFee']
            print(f"   💸 Platform fee: {fee_info}")
        if 'fee' in quote:
            fee_info = quote['fee']
            print(f"   💸 Fee info: {fee_info}")
        
        print(f"\n   Очікувано отримаємо: {out_amount_sol:.8f} SOL (${out_amount_usd:.2f} USD)")

        # 5. Будуємо транзакцію
        print(f"\n5️⃣ Побудова транзакції:")
        print(f"   User public key: {kp.pubkey()}")
        print(f"   Compute unit price: 10000 microLamports (priority fee)")
        
        swap = requests.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(kp.pubkey()),
            "computeUnitPriceMicroLamports": 10000  # Priority fee
        }).json()
        
        if "error" in swap:
            print(f"   ❌ Помилка swap: {swap['error']}")
            return
        
        print(f"   ✅ Swap транзакція створена")
        print(f"   Swap response keys: {list(swap.keys())}")
        
        # 6. Підписуємо і відправляємо
        print(f"\n6️⃣ Підпис та відправка транзакції:")
        tx_bytes = base64.b64decode(swap["swapTransaction"])
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        vtx = VersionedTransaction(vtx.message, [kp])
        
        # Відправка через RPC
        signed_tx = base64.b64encode(bytes(vtx)).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "sendTransaction",
            "params": [signed_tx, {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False}]
        }
        print(f"   Відправляємо транзакцію через RPC...")
        res = requests.post(RPC, json=payload, timeout=30).json()
        
        if "error" in res:
            print(f"   ❌ Помилка відправки: {res['error']}")
            return
        
        sig = res.get("result")
        print(f"\n" + "="*80)
        print(f"✅ ТРАНЗАКЦІЯ ВІДПРАВЛЕНА УСПІШНО!")
        print(f"="*80)
        print(f"   Signature: {sig}")
        print(f"   Solscan: https://solscan.io/tx/{sig}")
        print(f"\n   📋 Підсумок:")
        print(f"      Продано: {tokens_to_sell:.6f} токенів (${usd_amount:.2f} USD)")
        print(f"      Отримано: {out_amount_sol:.8f} SOL (${out_amount_usd:.2f} USD)")
        print(f"      Slippage: {slippage_pct:.2f}%")
        if 'priceImpactPct' in quote:
            try:
                price_impact = float(quote['priceImpactPct']) if quote['priceImpactPct'] else 0.0
                print(f"      Price impact: {price_impact:.4f}%")
            except (ValueError, TypeError):
                print(f"      Price impact: {quote['priceImpactPct']}")
        print(f"="*80 + "\n")

    else:
        print("❌ Невідомий режим, використовуйте --mode buy або --mode sell")
        return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, required=True, help="buy або sell")
    parser.add_argument("--amount", type=float, required=True, help="Сума в USD")
    parser.add_argument("--key-id", type=int, required=True, help="ID ключа з keys.json")
    parser.add_argument("--token-address", type=str, required=True, help="Адреса токену для торгівлі")
    args = parser.parse_args()

    kp = load_key_from_file(args.key_id)
    trade_tokens(kp, args.amount, args.mode.lower(), args.token_address)


# =====================
# Simulation utilities
# =====================

async def sim_buy(token_id: int, entry_sec: int = 30, amount_usd: float = None) -> bool:
    """One-off: set sim_buy_* at entry_sec based on token_metrics_seconds.
    Does nothing if sim_buy_* already set.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sim_buy_token_amount, sim_buy_price_usd FROM tokens WHERE id=$1",
            token_id,
        )
        if row and (row["sim_buy_token_amount"] is not None and row["sim_buy_price_usd"] is not None):
            return False
        # Try exact entry_sec first; if no price there, search within ±5 seconds
        pr = await conn.fetchrow(
            """
            SELECT usd_price FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
            ORDER BY ts ASC OFFSET $2 LIMIT 1
            """,
            token_id, max(0, entry_sec - 1)
        )
        if not pr or not pr["usd_price"]:
            # Fallback: find nearest price within entry_sec ± 5 seconds
            pr = await conn.fetchrow(
                """
                WITH numbered AS (
                    SELECT usd_price, ROW_NUMBER() OVER (ORDER BY ts ASC) - 1 AS rn
                    FROM token_metrics_seconds
                    WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
                )
                SELECT usd_price, ABS(rn - $2) AS dist
                FROM numbered
                WHERE rn BETWEEN GREATEST(0, $2 - 5) AND ($2 + 5)
                ORDER BY dist ASC, rn ASC
                LIMIT 1
                """,
                token_id, entry_sec
            )
            if not pr or not pr["usd_price"]:
                return False
        entry_price = float(pr["usd_price"]) or 0.0
        if entry_price <= 0:
            return False
        if amount_usd is None:
            try:
                amount_usd = float(getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
            except Exception:
                amount_usd = 5.0
        amount_tokens = float(amount_usd) / entry_price
        await conn.execute(
            """
            UPDATE tokens
            SET sim_buy_token_amount=$2,
                sim_buy_price_usd=$3,
                sim_buy_iteration=$4,
                token_updated_at=CURRENT_TIMESTAMP
            WHERE id=$1
            """,
            token_id, amount_tokens, entry_price, entry_sec
        )
        return True


# =====================
# Honeypot checker (pre-buy safety)
# =====================
async def check_honeypot(token_id: int, at_iter: Optional[int] = None) -> dict:
    """Heuristic honeypot detector based on on-chain flags and recent buy/sell stats.

    Returns dict: {"honeypot": bool, "reasons": [str], "buys": int, "sells": int}
    Also updates tokens.is_honeypot, honeypot_checked_at, honeypot_reason.
    """
    pool = await get_db_pool()
    reasons = []
    buys = 0
    sells = 0
    is_hp = False
    async with pool.acquire() as conn:
        t = await conn.fetchrow(
            """
            SELECT token_address, holder_count, token_program,
                   mint_authority_disabled, freeze_authority_disabled,
                   top_holders_percentage, dev_balance_percentage
            FROM tokens WHERE id=$1
            """,
            token_id
        )
        if not t:
            return {"honeypot": False, "reasons": ["token_not_found"], "buys": 0, "sells": 0}

        # Aggregate recent buy/sell counts over window
        try:
            win = int(getattr(config, 'HONEYPOT_WINDOW_SEC', 120))
        except Exception:
            win = 120
        # Determine end_ts by target iteration (if provided) or by latest
        if at_iter is not None and at_iter >= 1:
            ts_row = await conn.fetchrow(
                """
                SELECT ts FROM (
                  SELECT ts, ROW_NUMBER() OVER (ORDER BY ts ASC) AS rn
                  FROM token_metrics_seconds
                  WHERE token_id=$1 AND (usd_price IS NOT NULL AND usd_price > 0)
                ) s WHERE rn=$2
                """,
                token_id, int(at_iter)
            )
        else:
            ts_row = await conn.fetchrow(
                "SELECT MAX(ts) AS ts FROM token_metrics_seconds WHERE token_id=$1 AND (usd_price IS NOT NULL AND usd_price > 0)",
                token_id
            )
        end_ts = int(ts_row["ts"]) if ts_row and ts_row["ts"] is not None else None

        rec = None
        if end_ts is not None:
            rec = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(buy_count),0) AS buys,
                       COALESCE(SUM(sell_count),0) AS sells
                FROM token_metrics_seconds
                WHERE token_id=$1
                  AND (usd_price IS NOT NULL AND usd_price > 0)
                  AND ts >= $2::bigint - $3::bigint
                  AND ts <= $2::bigint
                """,
                token_id, end_ts, win
            )
        if rec:
            buys = int(rec["buys"] or 0)
            sells = int(rec["sells"] or 0)

        # Heuristic rules
        try:
            min_buys = int(getattr(config, 'HONEYPOT_MIN_BUYS', 30))
            max_sells = int(getattr(config, 'HONEYPOT_MAX_SELLS', 0))
            min_sell_share = float(getattr(config, 'HONEYPOT_MIN_SELL_SHARE', 0.05))
        except Exception:
            min_buys, max_sells, min_sell_share = 30, 0, 0.05

        # Rule 1: No sells with many buys
        if buys >= min_buys and sells <= max_sells:
            is_hp = True
            reasons.append(f"no_sells_recent(buys={buys},sells={sells})")

        # Rule 2: Too small sell share
        tot = buys + sells
        if not is_hp and tot >= min_buys:
            share = (sells / tot) if tot > 0 else 0.0
            if share < min_sell_share:
                is_hp = True
                reasons.append(f"low_sell_share({share:.3f})")

        # Rule 3: Freeze authority still present
        fa = t.get("freeze_authority_disabled")
        if getattr(config, 'HONEYPOT_FLAG_FREEZE_AUTH', True) and (fa is False):
            is_hp = True
            reasons.append("freeze_authority_present")

        # Rule 4: Mint authority still present (high-risk)
        ma = t.get("mint_authority_disabled")
        if ma is False:
            is_hp = True
            reasons.append("mint_authority_present")

        # Optional note on extreme concentration (do not force flag by itself)
        try:
            thp = float(t.get("top_holders_percentage") or 0.0)
            if thp >= 90.0:
                reasons.append(f"top_holders_{thp:.1f}pct")
        except Exception:
            pass

        # Rule 5: Zero-tail (consecutive null/zero prices)
        try:
            zero_tail_n = int(getattr(config, 'ZERO_TAIL_CONSEC_SEC', 10))
        except Exception:
            zero_tail_n = 10
        if end_ts is not None and zero_tail_n > 0:
            zr = await conn.fetchrow(
                """
                SELECT COUNT(*) AS zc
                FROM token_metrics_seconds
                WHERE token_id=$1 AND ts > ($2::bigint - $3::bigint) AND ts <= $2::bigint
                  AND (usd_price IS NULL OR usd_price <= 0)
                """,
                token_id, end_ts, zero_tail_n
            )
            if zr and int(zr["zc"] or 0) >= zero_tail_n:
                is_hp = True
                reasons.append(f"zero_tail_{zero_tail_n}s")

        await conn.execute(
            """
            UPDATE tokens
            SET is_honeypot=$2,
                honeypot_checked_at=CURRENT_TIMESTAMP,
                honeypot_reason=$3,
                token_updated_at=CURRENT_TIMESTAMP
            WHERE id=$1
            """,
            token_id, is_hp, ",".join(reasons) if reasons else None
        )

    return {"honeypot": bool(is_hp), "reasons": reasons, "buys": buys, "sells": sells}

async def sim_sell(token_id: int, target_mult: float = TARGET_MULT) -> Optional[int]:
    """Check последнюю секунду и, если цель достигнута, зафиксировать выход."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow(
            "SELECT sim_buy_token_amount, sim_buy_price_usd FROM tokens WHERE id=$1",
            token_id
        )
        if not t or t["sim_buy_token_amount"] is None or t["sim_buy_price_usd"] is None:
            return None
        entry_price = float(t["sim_buy_price_usd"])
        target = entry_price * float(target_mult)
        row = await conn.fetchrow(
            """
            SELECT usd_price, rn FROM (
              SELECT usd_price,
                     ROW_NUMBER() OVER (ORDER BY ts ASC) AS rn
              FROM token_metrics_seconds
              WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
            ) s
            ORDER BY rn DESC
            LIMIT 1
            """,
            token_id
        )
        if not row:
            return None
        exit_price = float(row["usd_price"])
        if exit_price < target:
            return None
        exit_iter = int(row["rn"])
        await conn.execute(
            """
            UPDATE tokens
            SET sim_sell_token_amount = COALESCE(sim_sell_token_amount, sim_buy_token_amount),
                sim_sell_price_usd = $2,
                sim_sell_iteration = $3,
                token_updated_at = CURRENT_TIMESTAMP
            WHERE id=$1
              AND sim_buy_token_amount IS NOT NULL
              AND (sim_sell_iteration IS NULL OR sim_sell_iteration < $3)
            """,
            token_id, exit_price, exit_iter
        )
        return exit_iter


async def sim_current(token_id: int) -> bool:
    """Recalculate current portfolio value and profit: sim_cur_income_price_usd, sim_profit_usd."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow(
            "SELECT sim_buy_token_amount, sim_buy_price_usd FROM tokens WHERE id=$1",
            token_id
        )
        if not t or t["sim_buy_token_amount"] is None or t["sim_buy_price_usd"] is None:
            return False
        latest = await conn.fetchrow(
            """
            SELECT usd_price FROM token_metrics_seconds
            WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
            ORDER BY ts DESC LIMIT 1
            """,
            token_id
        )
        if not latest or not latest["usd_price"]:
            return False
        amount = float(t["sim_buy_token_amount"]) or 0.0
        entry_price = float(t["sim_buy_price_usd"]) or 0.0
        cur_price = float(latest["usd_price"]) or 0.0
        cur_value = amount * cur_price
        profit = cur_value - (amount * entry_price) if entry_price > 0 else 0.0
        await conn.execute(
            "UPDATE tokens SET sim_cur_income_price_usd=$2, sim_profit_usd=$3, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
            token_id, cur_value, profit
        )
        return True


# =====================
# Background simulation loop (independent timer)
# =====================
_sim_task: Optional[asyncio.Task] = None


async def _fetch_ids_needing_buy(conn) -> List[Tuple[int, int]]:
    if not GOOD_PATTERN_CODES:
        return []
    rows = await conn.fetch(
        """
        WITH metrics AS (
          SELECT token_id, COUNT(*) AS cnt
          FROM token_metrics_seconds
          WHERE usd_price IS NOT NULL AND usd_price > 0
          GROUP BY token_id
        )
        SELECT t.id, metrics.cnt
        FROM tokens t
        JOIN metrics ON metrics.token_id = t.id
        WHERE t.history_ready = FALSE
          AND (t.sim_buy_token_amount IS NULL OR t.sim_buy_price_usd IS NULL)
          AND t.sim_sell_iteration IS NULL
          AND t.pattern_code IS NOT NULL
          AND t.pattern_code <> ''
          AND t.pattern_code = ANY($2::text[])
          AND (
            (t.first_pool_created_at IS NOT NULL AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - t.first_pool_created_at)) <= $3)
            OR (t.first_pool_created_at IS NULL AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - t.created_at)) <= $3)
          )
          AND metrics.cnt >= $1
          AND (t.sim_buy_iteration IS NOT NULL OR metrics.cnt < $4)
        ORDER BY metrics.cnt ASC
        LIMIT 200
        """,
        ENTRY_MIN_SECONDS,
        list(GOOD_PATTERN_CODES),
        MAX_TOKEN_AGE_SEC,
        MAX_WAIT_ITERATIONS,
    )
    return [(int(r["id"]), int(r["cnt"])) for r in rows]


async def _fetch_ids_needing_current(conn) -> List[int]:
    rows = await conn.fetch(
        """
        SELECT id FROM tokens
        WHERE history_ready = FALSE
          AND sim_buy_token_amount IS NOT NULL
          AND sim_sell_iteration IS NULL
        LIMIT 500
        """
    )
    return [r["id"] for r in rows]


async def _recommend_entry_amount(conn, token_id: int) -> float:
    try:
        base_amount = float(getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
    except Exception:
        base_amount = 5.0
    try:
        tok = await conn.fetchrow(
            "SELECT liquidity, mcap FROM tokens WHERE id=$1",
            token_id
        )
    except Exception:
        tok = None

    if tok:
        liq_val = tok["liquidity"]
        mcap_val = tok["mcap"]
        liquidity = float(liq_val) if liq_val is not None else 0.0
        mcap = float(mcap_val) if mcap_val is not None else 0.0
    else:
        liquidity = 0.0
        mcap = 0.0

    if liquidity <= 0:
        liquidity_cap = 10.0
    elif liquidity < 15000:
        liquidity_cap = 10.0
    elif liquidity < 50000:
        liquidity_cap = 20.0
    elif liquidity < 150000:
        liquidity_cap = 40.0
    elif liquidity < 500000:
        liquidity_cap = 60.0
    else:
        liquidity_cap = 100.0

    mcap_cap = base_amount if mcap <= 0 else max(base_amount, min(100.0, mcap * 0.00005))

    trade_cap = liquidity_cap
    try:
        stats = await conn.fetchrow(
            """
            WITH latest AS (
              SELECT COALESCE(MAX(ts), 0) AS max_ts
              FROM token_metrics_seconds
              WHERE token_id=$1 AND usd_price IS NOT NULL AND usd_price>0
            )
            SELECT
              COALESCE(SUM(buy_usd), 0) AS sum_buy_usd,
              COALESCE(SUM(buy_count), 0) AS sum_buy_count,
              COALESCE(AVG(median_amount_usd), 0) AS avg_median_usd
            FROM token_metrics_seconds
            WHERE token_id=$1
              AND usd_price IS NOT NULL AND usd_price>0
              AND ts >= (SELECT max_ts FROM latest) - 60
              AND ts <= (SELECT max_ts FROM latest)
            """,
            token_id
        )
    except Exception:
        stats = None

    if stats:
        sum_buy_usd = float(stats["sum_buy_usd"] or 0.0)
        sum_buy_count = float(stats["sum_buy_count"] or 0.0)
        avg_median = float(stats["avg_median_usd"] or 0.0)
        avg_buy = sum_buy_usd / sum_buy_count if sum_buy_count > 0 else 0.0
        if sum_buy_count < 3:
            avg_buy *= 0.6
        trade_cap_candidate = max(avg_buy, avg_median, base_amount)
        trade_cap = max(base_amount, min(100.0, trade_cap_candidate))
        if sum_buy_count < 3:
            trade_cap = min(trade_cap, 20.0)
    else:
        trade_cap = liquidity_cap

    limit = max(base_amount, min(liquidity_cap, mcap_cap, trade_cap))
    for amt in CANDIDATE_AMOUNTS:
        if limit >= amt:
            return float(amt)
    return base_amount


async def _simulation_loop(interval_sec: float = 1.0):
    pool = await get_db_pool()
    while True:
        try:
            async with pool.acquire() as conn:
                # 1) Legacy background auto-buys are disabled by default. Enable via config if needed.
                if getattr(config, 'SIM_BACKGROUND_BUY_ENABLED', False):
                    buy_candidates = await _fetch_ids_needing_buy(conn)
                    for tid, iterations in buy_candidates:
                        try:
                            entry_iter = max(iterations, ENTRY_MIN_SECONDS)
                            # Strict base amount (adaptive disabled by request)
                            try:
                                base_amount = float(getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
                            except Exception:
                                base_amount = 5.0
                            await sim_buy(tid, entry_sec=entry_iter, amount_usd=base_amount)
                        except Exception:
                            pass

                # 2) Поточна вартість для всіх токенів з встановленим sim_buy
                cur_ids = await _fetch_ids_needing_current(conn)
                for tid in cur_ids:
                    try:
                        await sim_current(tid)
                        await sim_sell(tid, target_mult=TARGET_MULT)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception:
            # не падаємо, просто наступна ітерація
            pass
        await asyncio.sleep(interval_sec)


async def start_simulation_timer() -> bool:
    """Запустити незалежний таймер симуляції (buy @ 30s + поточна вартість)."""
    global _sim_task
    if _sim_task and not _sim_task.done():
        return False
    _sim_task = asyncio.create_task(_simulation_loop())
    return True


async def stop_simulation_timer() -> bool:
    global _sim_task
    if not _sim_task:
        return False
    _sim_task.cancel()
    try:
        await _sim_task
    except asyncio.CancelledError:
        pass
    _sim_task = None
    return True


# =====================
# Centralized token sale finalization
# =====================

async def finalize_token_sale(token_id: int, conn, reason: str = 'auto') -> bool:
    """
    Centralized function to finalize token sale:
    - Free wallet (active_token_id=NULL, entry_amount_usd=NULL)
    - Update wallet balance (cash_usd += realized, total_profit_usd += profit)
    - Close wallet history (exit_* fields, profit, outcome)
    - Clear tokens.sim_wallet_id = NULL
    - Set tokens.history_ready = TRUE
    
    This function should be called by analyzer when sim_sell_iteration IS NOT NULL.
    ETA and force_sell should only set sim_sell_* fields, then analyzer calls this.
    
    Args:
        token_id: Token ID
        conn: Database connection
        reason: Reason for sale ('auto', 'manual', 'zero-liquidity', 'frozen')
    
    Returns:
        True if finalized successfully, False otherwise
    """
    try:
        # Get token data
        row = await conn.fetchrow(
            """
            SELECT 
                sim_wallet_id, 
                sim_buy_token_amount, 
                sim_buy_price_usd, 
                sim_buy_iteration,
                sim_sell_token_amount,
                sim_sell_price_usd,
                sim_sell_iteration
            FROM tokens 
            WHERE id=$1
            """,
            token_id
        )
        if not row:
            return False
        
        wid = row["sim_wallet_id"]
        if wid is None:
            # No wallet attached - just mark history_ready
            await conn.execute(
                """
                UPDATE tokens 
                SET history_ready = TRUE,
                    sim_wallet_id = NULL,
                    token_updated_at = CURRENT_TIMESTAMP
                WHERE id=$1
                """,
                token_id
            )
            return True
        
        # Get wallet data
        w = await conn.fetchrow(
            "SELECT entry_amount_usd, cash_usd FROM sim_wallets WHERE id=$1",
            int(wid)
        )
        if not w:
            return False
        
        # Calculate realized amount and profit
        sell_amt = float(row["sim_sell_token_amount"] or row["sim_buy_token_amount"] or 0.0)
        sell_price = float(row["sim_sell_price_usd"] or 0.0)
        buy_price = float(row["sim_buy_price_usd"] or 0.0)
        buy_iter = int(row["sim_buy_iteration"]) if row["sim_buy_iteration"] is not None else 0
        sell_iter = int(row["sim_sell_iteration"]) if row["sim_sell_iteration"] is not None else 0
        
        realized_base = sell_amt * sell_price
        entry_amt = float(w["entry_amount_usd"] or getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
        deposit = float(getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
        scale = (entry_amt / deposit) if deposit > 0 else 1.0
        realized = float(realized_base * scale)
        
        # Apply fees
        try:
            sl_bps = int(getattr(config, 'SIM_FEES_SLIPPAGE_BPS', 250))
            jup_bps = int(getattr(config, 'SIM_FEES_JUPITER_BPS', 0))
            bps_rate = float(max(0, sl_bps + jup_bps)) / 10000.0
        except Exception:
            bps_rate = 0.0
        try:
            sol_fee = float(getattr(config, 'SIM_FEES_SOL_PER_TX', 0.000005))
            sol_price = float(getattr(config, 'SOL_PRICE_FALLBACK', 193.0))
            net_fee_usd = float(sol_fee) * float(sol_price)
        except Exception:
            net_fee_usd = 0.0
        
        fees = realized * bps_rate + net_fee_usd
        realized = max(0.0, realized - fees)
        profit_delta = float(realized - entry_amt)
        
        # Update wallet: free it and update balance
        # NOTE: entry_amount_usd is preserved as user preference (not cleared after sale)
        # It will be used for the next buy. Only active_token_id is cleared.
        await conn.execute(
            """
            UPDATE sim_wallets
            SET cash_usd = cash_usd + $2,
                active_token_id = NULL,
                -- entry_amount_usd: DO NOT CLEAR - preserve user-configured entry amount for next buy
                total_profit_usd = COALESCE(total_profit_usd, 0) + $3,
                updated_at = CURRENT_TIMESTAMP
            WHERE id=$1
            """,
            int(wid), realized, profit_delta
        )
        
        # Update wallet history with exit and outcome
        try:
            pct = (profit_delta / entry_amt) if entry_amt > 0 else 0.0
            try:
                target = float(getattr(config, 'TARGET_RETURN', 0.13))
            except Exception:
                target = 0.13
            
            if sell_price <= 0.0:
                outcome = 'frozen'
            elif pct >= target:
                outcome = 'good'
            elif pct > 0:
                outcome = 'medium'
            else:
                outcome = 'bad'
            
            await conn.execute(
                """
                UPDATE wallet_history
                SET exit_amount_usd=$3,
                    exit_token_amount=COALESCE(exit_token_amount, $4),
                    exit_price_usd=$5,
                    exit_iteration=$6,
                    profit_usd=$7,
                    profit_pct=$8,
                    outcome=$9,
                    reason=COALESCE(reason, $10),
                    updated_at=CURRENT_TIMESTAMP
                WHERE token_id=$1 AND wallet_id=$2 AND exit_iteration IS NULL
                """,
                token_id, int(wid),
                realized, sell_amt, sell_price, sell_iter,
                profit_delta, pct, outcome, reason
            )
        except Exception:
            pass
        
        # Finalize token: clear wallet reference and mark history_ready
        await conn.execute(
            """
            UPDATE tokens 
            SET history_ready = TRUE,
                sim_wallet_id = NULL,
                token_updated_at = CURRENT_TIMESTAMP
            WHERE id=$1
            """,
            token_id
        )
        
        return True
    except Exception as e:
        print(f"[finalize_token_sale] Error for token_id={token_id}: {e}")
        return False


# =====================================================================================
# REFACTORED: Separated REAL and SIMULATION trading functions
# =====================================================================================

async def force_buy_real(token_id: int) -> dict:
    """REAL TRADING: Force buy with real wallet and actual blockchain transaction.
    
    Logic:
    1. Get free real wallet from keys.json
    2. Check honeypot via execute_buy (simulates sell)
    3. If honeypot check passes - execute actual buy
    4. Log to wallet_history with transaction details
    5. Bind wallet to token (real_wallet_id)
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        token_row = await conn.fetchrow(
            "SELECT token_address, decimals, wallet_id FROM tokens WHERE id=$1",
            token_id
        )
        if not token_row:
            return {"success": False, "message": "Token not found"}
        
        # Do NOT allow new buy if position is still open (not closed)
        # Check for open position in wallet_history
        open_position = await conn.fetchrow(
            """
            SELECT id FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            LIMIT 1
            """,
            token_id
        )
        if open_position:
            return {"success": False, "message": "Position already open - cannot enter again", "token_id": token_id}
        
        # Get free real wallet
        wallet_info = await get_free_wallet(conn)
        if not wallet_info:
            return {"success": False, "message": "No free real wallet available"}
        
        keypair = wallet_info["keypair"]
        key_id = wallet_info["key_id"]
        token_address = token_row["token_address"]
        token_decimals = int(token_row["decimals"]) if token_row["decimals"] else 6
        
        # Get entry amount from wallet (user-configured per wallet) or config default
        wallet_row = await conn.fetchrow(
            "SELECT entry_amount_usd FROM wallets WHERE id=$1",
            key_id
        )
        if wallet_row and wallet_row.get("entry_amount_usd"):
            entry_amount_usd = float(wallet_row["entry_amount_usd"])
        else:
            # Fallback to config default
            try:
                entry_amount_usd = float(getattr(config, 'VIRTUAL_WALLET_DEPOSIT_USD', 5.0))
            except Exception:
                entry_amount_usd = 5.0
        
        # Execute real buy (includes honeypot check via simulation)
        buy_result = await execute_buy(token_id, keypair, entry_amount_usd, token_address, token_decimals)
        
        if not buy_result.get("success"):
            return buy_result
        
        # Update tokens table: bind wallet to token (wallet_id only, no sim_* fields)
        try:
            await conn.execute(
                """
                UPDATE tokens SET
                  wallet_id=$2,
                  token_updated_at=CURRENT_TIMESTAMP
                WHERE id=$1
                """,
                token_id, key_id
            )
        except Exception:
            pass
        
        # Log to history
        try:
            await conn.execute(
                """
                INSERT INTO wallet_history(
                    wallet_id, token_id,
                    entry_amount_usd, entry_token_amount, entry_price_usd, entry_iteration,
                    entry_slippage_bps, entry_price_impact_pct, entry_transaction_fee_sol, entry_transaction_fee_usd,
                    entry_expected_amount_usd, entry_actual_amount_usd, entry_signature,
                    outcome, reason, created_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'','manual',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """,
                key_id, token_id,
                entry_amount_usd, buy_result.get("amount_tokens"), buy_result.get("price_usd"), 1,
                buy_result.get("slippage_bps"), buy_result.get("price_impact_pct"),
                buy_result.get("transaction_fee_sol"), buy_result.get("transaction_fee_usd"),
                buy_result.get("expected_amount_usd"), buy_result.get("actual_amount_usd"),
                buy_result.get("signature")
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "token_id": token_id,
            "wallet_id": key_id,
            "amount_tokens": buy_result.get("amount_tokens"),
            "price_usd": buy_result.get("price_usd")
        }


## Simulation trading removed


async def force_sell_real(token_id: int) -> dict:
    """REAL TRADING: Force sell - sells exact amount of tokens bought from wallet.
    
    Logic:
    1. Get token with real_wallet_id binding
    2. Execute real sell (token_amount = sim_buy_token_amount)
    3. Update tokens table (sim_sell_*, history_ready)
    4. Finalize position
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Get open position from wallet_history
        history_row = await conn.fetchrow(
            """
            SELECT wallet_id, entry_token_amount
            FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            token_id
        )
        if not history_row:
            return {"success": False, "message": "No open position found in journal"}
        
        wallet_id_bound = int(history_row["wallet_id"])
        token_amount = float(history_row["entry_token_amount"] or 0.0)
        
        if token_amount <= 0:
            return {"success": False, "message": "Invalid token amount in journal"}
        
        # Get token info
        token_row = await conn.fetchrow(
            "SELECT token_address, decimals, wallet_id FROM tokens WHERE id=$1",
            token_id
        )
        if not token_row:
            return {"success": False, "message": "Token not found"}
        
        if not token_row["wallet_id"] or int(token_row["wallet_id"]) != wallet_id_bound:
            return {"success": False, "message": "Wallet binding mismatch"}
        
        key_id = wallet_id_bound
        token_address = token_row["token_address"]
        token_decimals = int(token_row["decimals"]) if token_row["decimals"] else 6
        
        # Load keypair
        try:
            with open(config.WALLET_KEYS_FILE) as f:
                keys = json.load(f)
            wallet_key = None
            for k in keys:
                if k.get("id") == key_id:
                    wallet_key = k
                    break
            if not wallet_key:
                return {"success": False, "message": f"Wallet key_id={key_id} not found"}
            keypair = Keypair.from_bytes(bytes(wallet_key["bits"]))
        except Exception as e:
            return {"success": False, "message": f"Failed to load keypair: {str(e)}"}
        
        # Execute real sell
        sell_result = await execute_sell(token_id, keypair, token_address, token_amount, token_decimals)
        
        if not sell_result.get("success"):
            return sell_result
        
        # Calculate actual USD received
        actual_usd_received = sell_result.get("amount_usd", 0.0)
        price_usd = sell_result.get("price_usd", 0.0)
        signature = sell_result.get("signature")
        
        # Update wallet_history with exit details (REAL trading data)
        try:
            await conn.execute(
                """
                UPDATE wallet_history SET
                  exit_token_amount=$2,
                  exit_price_usd=$3,
                  exit_amount_usd=$4,
                  exit_signature=$5,
                  exit_iteration=1,
                  outcome='closed',
                  reason='manual',
                  updated_at=CURRENT_TIMESTAMP
                WHERE wallet_id=$6 AND token_id=$7 AND exit_iteration IS NULL
                """,
                token_amount, price_usd, actual_usd_received, signature, key_id, token_id
            )
        except Exception:
            pass
        
        # Mark token as history_ready and clear wallet binding
        try:
            await conn.execute(
                """
                UPDATE tokens SET
                  history_ready=TRUE,
                  wallet_id=NULL,
                  token_updated_at=CURRENT_TIMESTAMP
                WHERE id=$1
                """,
                token_id
            )
        except Exception:
            pass
        
        return {
            "success": True,
            "token_id": token_id,
            "amount_tokens": token_amount,
            "price_usd": price_usd,
            "amount_usd": actual_usd_received,
            "signature": signature
        }


# =====================
# Router functions (HTTP endpoints)
# =====================

async def force_buy(token_id: int) -> dict:
    """Router: Choose between REAL or SIMULATION buy based on config."""
    real_trading = getattr(config, 'REAL_TRADING_ENABLED', False)
    
    if real_trading:
        return await force_buy_real(token_id)
    else:
        return {"success": False, "message": "simulation removed"}


async def force_sell(token_id: int) -> dict:
    """Router: Choose between REAL or SIMULATION sell based on config."""
    real_trading = getattr(config, 'REAL_TRADING_ENABLED', False)
    
    if real_trading:
        return await force_sell_real(token_id)
    else:
        return {"success": False, "message": "simulation removed"}
