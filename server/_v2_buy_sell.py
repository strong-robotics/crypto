#!/usr/bin/env python3
"""
Версія 2: Функція продажі на основі журналу покупок/продаж
"""

import asyncio
import base64
import json
import os
import random
import sys
from math import floor
from pathlib import Path
from typing import Dict, Optional, Any

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
def _maybe_rerun_with_venv(exc: ModuleNotFoundError):
    """If asyncpg is missing, rerun script using venv python automatically."""
    if os.environ.get("BUYSELL_SKIP_VENV_RELAUNCH") == "1":
        raise exc
    base_dir = Path(__file__).resolve().parents[1]
    candidates = [
        base_dir / "venv" / "bin" / "python3",
        base_dir / "venv" / "bin" / "python",
        base_dir / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            os.environ["BUYSELL_SKIP_VENV_RELAUNCH"] = "1"
            os.execv(str(candidate), [str(candidate)] + sys.argv)
    raise exc


try:
    from _v3_db_pool import get_db_pool, close_db_pool
except ModuleNotFoundError as exc:
    if exc.name == "asyncpg":
        _maybe_rerun_with_venv(exc)
    raise
from config import config
from _v2_sol_price import get_current_sol_price
from _v3_token_archiver import archive_token, purge_token
from _v3_db_utils import get_token_iterations_count
from _v3_trade_type_checker import check_token_has_real_trading

# === Конфігурація ===
# Helius RPC endpoint (used for all RPC operations: getBalance, simulateTransaction, sendTransaction)
HELIUS_RPC = getattr(config, "HELIUS_RPC_URL", "").strip()
if not HELIUS_RPC:
    helius_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if helius_key:
        HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    else:
        HELIUS_RPC = getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Jupiter API - used ONLY for quotes and analysis (not for transaction execution)
JUP = "https://lite-api.jup.ag/swap/v1"
SOL_MINT = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)
RPC_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Alternate RPC (Jupiter/public) when Helius quota is exhausted
JUPITER_RPC_ENDPOINT = getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
USE_JUPITER_RPC = bool(getattr(config, "USE_JUPITER_RPC", False))

# Slippage ladders (basis points) from config
BUY_SLIPPAGE_LEVELS = list(getattr(config, "BUY_SLIPPAGE_LEVELS", [250, 350, 450, 550]))
SELL_SLIPPAGE_LEVELS = list(getattr(config, "SELL_SLIPPAGE_LEVELS", [250, 270, 290, 310, 330, 350]))
# Base network fee per transaction (approximate Solana fee)
BASE_TX_FEE_LAMPORTS = 5000

# Rate limiting для Jupiter API: максимум 1 запит в секунду
_jupiter_rate_limiter = asyncio.Semaphore(1)
_last_jupiter_request_time = 0.0
LAMPORTS_PER_SOL = 1_000_000_000
HELIUS_INITIAL_DELAY_SEC = float(getattr(config, "HELIUS_INITIAL_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_DELAY_SEC = float(getattr(config, "HELIUS_RETRY_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_BACKOFF = float(getattr(config, "HELIUS_RETRY_BACKOFF", 1.5) or 1.0)
HELIUS_MAX_ATTEMPTS = int(getattr(config, "HELIUS_MAX_ATTEMPTS", 5) or 1)


async def _archive_or_purge_token(conn, token_id: int, iteration_count: Optional[int] = None) -> Dict[str, Any]:
    """Archive tokens with sufficient life; otherwise purge them completely."""
    threshold = int(getattr(config, "ARCHIVE_MIN_ITERATIONS", 700))
    if iteration_count is None or iteration_count < 0:
        iteration_count = await get_token_iterations_count(conn, token_id)
    if iteration_count >= threshold:
        result = await archive_token(token_id, conn=conn)
        return {"action": "archive", "result": result}
    result = await purge_token(token_id, conn=conn)
    return {"action": "purge", "result": result}

async def _wait_for_jupiter_rate_limit():
    """Чекати між запитами до Jupiter API (максимум 1 запит в секунду)"""
    global _last_jupiter_request_time
    async with _jupiter_rate_limiter:
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - _last_jupiter_request_time
        if time_since_last < 1.0:
            await asyncio.sleep(1.0 - time_since_last)
        _last_jupiter_request_time = asyncio.get_event_loop().time()


def _is_slippage_error(res: dict) -> bool:
    """Check if error is slippage-related (0x1771 = 6001 = slippage tolerance exceeded)"""
    error_code = res.get("error", {}).get("data", {}).get("err", {})
    return (
        "0x1771" in str(res.get("error", {})) or
        "6001" in str(res.get("error", {})) or
        "slippage" in str(res.get("error", {})).lower() or
        "InstructionError" in str(error_code)
    )


def _safe_float(value, default: float = 0.0) -> float:
    """Convert arbitrary numeric-like value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _set_if_missing(target: dict, key: str, value):
    """Assign value to dict only when key is absent or None."""
    if value is None:
        return
    if target.get(key) is None:
        target[key] = value


async def _sign_and_send_transaction(
    session: aiohttp.ClientSession,
    swap: dict,
    keypair: Keypair,
    sender_endpoint: str,
    slippage_bps: int,
    slippage_levels: list
) -> Dict:
    """Sign and send transaction with error handling and slippage retry logic.
    
    Returns:
        dict with 'success', 'signature', 'error' keys
        - If success: {'success': True, 'signature': '...'}
        - If should retry: {'success': False, 'retry': True, 'message': '...'}
        - If final error: {'success': False, 'retry': False, 'message': '...'}
    """
    try:
        # Sign transaction
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
        
        # Send transaction
        async with session.post(sender_endpoint, json=payload, timeout=RPC_TIMEOUT) as resp:
            if resp.status != 200:
                text = await resp.text()
                if slippage_bps == slippage_levels[-1]:  # Last attempt
                    return {"success": False, "retry": False, "message": f"Transaction HTTP error {resp.status}: {text[:200]}"}
                return {"success": False, "retry": True, "message": f"Transaction HTTP error {resp.status}: {text[:200]}"}
            
            try:
                res = await resp.json(content_type=None)
            except Exception as e:
                text = await resp.text()
                if slippage_bps == slippage_levels[-1]:  # Last attempt
                    return {"success": False, "retry": False, "message": f"Transaction JSON parse error: {str(e)}, response: {text[:200]}"}
                return {"success": False, "retry": True, "message": f"Transaction JSON parse error: {str(e)}, response: {text[:200]}"}
        
        # Check for errors
        if "error" in res:
            is_slippage_error = _is_slippage_error(res)
            
            if is_slippage_error and slippage_bps < slippage_levels[-1]:
                # Slippage error and not last attempt - retry with higher slippage
                return {"success": False, "retry": True, "message": f"Transaction error: {res['error']}"}
            elif slippage_bps == slippage_levels[-1]:  # Last attempt
                return {"success": False, "retry": False, "message": f"Transaction error: {res['error']}"}
            else:
                # Non-slippage error - don't retry
                return {"success": False, "retry": False, "message": f"Transaction error: {res['error']}"}
        
        signature = res.get("result")
        if signature:
            return {"success": True, "signature": signature}
        else:
            if slippage_bps == slippage_levels[-1]:  # Last attempt
                return {"success": False, "retry": False, "message": "Transaction sent but no signature returned (transaction may have failed)"}
            return {"success": False, "retry": True, "message": "Transaction sent but no signature returned (transaction may have failed)"}
            
    except Exception as e:
        if slippage_bps == slippage_levels[-1]:  # Last attempt
            return {"success": False, "retry": False, "message": f"Exception during transaction: {str(e)}"}
        return {"success": False, "retry": True, "message": f"Exception during transaction: {str(e)}"}


async def simulate_buy_transaction(
    session: aiohttp.ClientSession,
    swap: dict,
    keypair: Keypair,
    quote: dict,
    amount_usd: float,
    token_decimals: int,
    slippage_bps: int
) -> Dict:
    """Simulate buy transaction without actually sending it to blockchain.
    
    Returns the same format as _sign_and_send_transaction but with simulated data.
    This allows testing the full buy flow without spending real SOL.
    """
    import random
    from datetime import datetime
    
    # Simulate transaction signature (fake signature for testing)
    fake_signature = f"SIM_{keypair.pubkey()}_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}"
    
    # Get expected token amount from quote
    amount_tokens = int(quote["outAmount"]) / (10**token_decimals)
    token_price_usd = amount_usd / amount_tokens if amount_tokens > 0 else 0
    
    # Simulate slippage (fixed 5%)
    slippage_pct = slippage_bps / 10000.0
    actual_slippage_pct = 0.05  # Fixed 5% slippage for simulation
    actual_amount_tokens = amount_tokens * (1 - actual_slippage_pct)
    
    # Simulate transaction fee (typical Solana fee ~0.000005 SOL)
    sol_price = get_current_sol_price()
    transaction_fee_sol = 0.000005
    transaction_fee_usd = transaction_fee_sol * sol_price
    
    print(f"[simulate_buy] ✅ Simulated buy transaction:")
    print(f"  - Signature: {fake_signature}")
    print(f"  - Amount USD: ${amount_usd:.2f}")
    print(f"  - Token amount: {actual_amount_tokens:.8f}")
    print(f"  - Token price USD: ${token_price_usd:.8f}")
    print(f"  - Slippage: {actual_slippage_pct:.4%}")
    print(f"  - Transaction fee: {transaction_fee_sol} SOL (${transaction_fee_usd:.6f})")
    
    # Simulate price impact (small random variation, typically 0.1-0.5%)
    price_impact_pct = random.uniform(0.001, 0.005)  # 0.1% to 0.5%
    
    return {
        "success": True,
        "signature": fake_signature,
        "amount_tokens": actual_amount_tokens,
        "amount_usd": amount_usd,
        "price_usd": token_price_usd,
        "slippage_bps": int(actual_slippage_pct * 10000),
        "slippage_pct": actual_slippage_pct,
        "price_impact_pct": price_impact_pct,
        "transaction_fee_sol": transaction_fee_sol,
        "transaction_fee_usd": transaction_fee_usd,
        "expected_amount_usd": amount_usd,
        "actual_amount_usd": amount_usd - transaction_fee_usd,
        "simulated": True
    }


async def simulate_sell_transaction(
    session: aiohttp.ClientSession,
    swap: dict,
    keypair: Keypair,
    quote: dict,
    token_amount: float,
    token_decimals: int,
    slippage_bps: int
) -> Dict:
    """Simulate sell transaction without actually sending it to blockchain.
    
    Returns the same format as _sign_and_send_transaction but with simulated data.
    This allows testing the full sell flow without actually selling tokens.
    """
    import random
    from datetime import datetime
    
    # Simulate transaction signature (fake signature for testing)
    fake_signature = f"SIM_{keypair.pubkey()}_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}"
    
    # Get expected SOL amount from quote
    sol_price = get_current_sol_price()
    expected_sol = int(quote["outAmount"]) / (10**SOL_DECIMALS)
    expected_usd = expected_sol * sol_price
    token_price_usd = expected_usd / token_amount if token_amount > 0 else 0
    
    # Simulate slippage (fixed 5%)
    slippage_pct = slippage_bps / 10000.0
    actual_slippage_pct = 0.05  # Fixed 5% slippage for simulation
    actual_sol = expected_sol * (1 - actual_slippage_pct)
    actual_usd = actual_sol * sol_price
    
    # Simulate transaction fee (typical Solana fee ~0.000005 SOL)
    transaction_fee_sol = 0.000005
    transaction_fee_usd = transaction_fee_sol * sol_price
    final_sol = actual_sol - transaction_fee_sol
    final_usd = final_sol * sol_price
    
    # Simulate price impact (small random variation, typically 0.1-0.5%)
    price_impact_pct = random.uniform(0.001, 0.005)  # 0.1% to 0.5%
    
    print(f"[simulate_sell] ✅ Simulated sell transaction:")
    print(f"  - Signature: {fake_signature}")
    print(f"  - Token amount: {token_amount:.8f}")
    print(f"  - Expected SOL: {expected_sol:.8f} (${expected_usd:.2f})")
    print(f"  - Actual SOL: {actual_sol:.8f} (${actual_usd:.2f})")
    print(f"  - Final SOL (after fee): {final_sol:.8f} (${final_usd:.2f})")
    print(f"  - Token price USD: ${token_price_usd:.8f}")
    print(f"  - Slippage: {actual_slippage_pct:.4%}")
    print(f"  - Price impact: {price_impact_pct:.4%}")
    print(f"  - Transaction fee: {transaction_fee_sol} SOL (${transaction_fee_usd:.6f})")
    
    return {
        "success": True,
        "signature": fake_signature,
        "amount_sol": final_sol,
        "amount_usd": final_usd,
        "price_usd": token_price_usd,
        "expected_sol": expected_sol,
        "expected_usd": expected_usd,
        "actual_sol": actual_sol,
        "actual_usd": actual_usd,
        "slippage_bps": int(actual_slippage_pct * 10000),
        "slippage_pct": actual_slippage_pct,
        "price_impact_pct": price_impact_pct,
        "transaction_fee_sol": transaction_fee_sol,
        "transaction_fee_usd": transaction_fee_usd,
        "simulated": True
    }


def _choose_rpc_endpoints():
    """Select RPC endpoints depending on config.USE_JUPITER_RPC flag."""
    if USE_JUPITER_RPC:
        return JUPITER_RPC_ENDPOINT, JUPITER_RPC_ENDPOINT
    return HELIUS_RPC, HELIUS_RPC


def _get_balance_rpc():
    rpc, _ = _choose_rpc_endpoints()
    return rpc


async def log_trade_attempt(
    conn,
    token_id: int,
    wallet_id: Optional[int],
    action: str,
    status: str,
    message: Optional[str],
    details: Optional[dict] = None,
):
    """Persist trade attempt diagnostics (success/fail/skipped)."""
    try:
        details_json = json.dumps(details) if details is not None else None
        await conn.execute(
            """
            INSERT INTO trade_attempts(token_id, wallet_id, action, status, message, details, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,CURRENT_TIMESTAMP)
            """,
            token_id,
            wallet_id,
            action,
            status,
            message,
            details_json,
        )
    except Exception:
        # Logging must never break trading flow
        pass


async def get_wallet_balance_sol(keypair: Keypair, session: Optional[aiohttp.ClientSession] = None) -> float:
    """Отримати баланс SOL для реального кошелька"""
    owns_session = False
    if session is None:
        session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        owns_session = True
    try:
        rpc_endpoint = _get_balance_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [str(keypair.pubkey())]
        }
        async with session.post(rpc_endpoint, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            data = await resp.json(content_type=None)
            if "result" in data:
                lamports = data["result"].get("value", 0)
                return lamports / (10**SOL_DECIMALS)
        return 0.0
    except Exception:
        return 0.0
    finally:
        if owns_session:
            await session.close()


def _load_keypair_by_id(key_id: int) -> Optional[Keypair]:
    """Load keypair from keys.json by wallet ID"""
    try:
        with open(config.WALLET_KEYS_FILE) as f:
            keys = json.load(f)
        for k in keys:
            if k.get("id") == key_id:
                return Keypair.from_bytes(bytes(k["bits"]))
        return None
    except Exception:
        return None


async def get_token_balance(keypair: Keypair, token_address: str, token_decimals: int, session: Optional[aiohttp.ClientSession] = None) -> float:
    """Отримати реальний баланс токенів на кошельку"""
    owns_session = False
    if session is None:
        session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        owns_session = True
    try:
        rpc_endpoint = _get_balance_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                str(keypair.pubkey()),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ]
        }
        async with session.post(rpc_endpoint, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            data = await resp.json(content_type=None)
            if "result" in data and "value" in data["result"]:
                accounts = data["result"]["value"]
                total_amount = 0.0
                for acc in accounts:
                    parsed = acc.get("account", {}).get("data", {}).get("parsed", {})
                    info = parsed.get("info", {})
                    token_amount = info.get("tokenAmount", {})
                    ui_amount = token_amount.get("uiAmount")
                    if ui_amount is not None:
                        total_amount += float(ui_amount)
                return total_amount
        return 0.0
    except Exception as e:
        print(f"[get_token_balance] ⚠️ Error getting token balance: {e}")
        return 0.0
    finally:
        if owns_session:
            await session.close()


async def get_free_wallet(conn, exclude_key_id: Optional[int] = None) -> Optional[Dict]:
    """Знайти вільний реальний кошелек з keys.json з round-robin логікою (черга по колу).
    
    Вільний = не використовується жодним токеном (wallet_id IS NULL для всіх токенів з цим key_id)
    Або не має відкритої позиції в wallet_history
    І має entry_amount_usd > 0 (якщо entry_amount_usd = 0, гаманець пропускається)
    
    Round-robin: Бере наступний вільний гаманець після останнього використаного.
    Якщо всі гаманці вільні - бере найменший ID.
    
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
        
        # Get wallets with entry_amount_usd > 0 (skip wallets with 0 or NULL)
        enabled_wallets = await conn.fetch(
            "SELECT id, entry_amount_usd FROM wallets WHERE entry_amount_usd IS NOT NULL AND entry_amount_usd > 0"
        )
        enabled_wallet_ids = {int(row["id"]) for row in enabled_wallets}
        
        # Debug: log enabled wallets
        if getattr(config, 'DEBUG', False):
            enabled_list = [f"id={row['id']}, amount=${float(row['entry_amount_usd']):.2f}" for row in enabled_wallets]
            print(f"[get_free_wallet] Enabled wallets: {', '.join(enabled_list)}")
        
        # If no wallets have entry_amount_usd > 0, return None (all wallets disabled)
        if not enabled_wallet_ids:
            if getattr(config, 'DEBUG', False):
                print("[get_free_wallet] ⚠️ No enabled wallets found (all have entry_amount_usd = 0 or NULL)")
            return None
        
        # Sort keys by ID for consistent ordering, but only include enabled wallets
        sorted_keys = sorted(
            [k for k in keys if k.get("id") is not None and int(k.get("id")) in enabled_wallet_ids],
            key=lambda x: int(x.get("id", 0))
        )
        
        if not sorted_keys:
            return None
        
        # Get last used wallet_id for round-robin (from most recent entry, open or closed)
        # This ensures we continue the queue from where we left off
        last_used_row = await conn.fetchrow(
            """
            SELECT wallet_id FROM wallet_history
            WHERE wallet_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        )
        last_used_id = int(last_used_row["wallet_id"]) if last_used_row and last_used_row.get("wallet_id") else None
        
        # Find free wallets (not used AND enabled)
        free_wallets = []
        for k in sorted_keys:
            key_id = int(k.get("id"))
            if key_id not in used_key_ids:
                free_wallets.append(k)
        
        if not free_wallets:
            return None
        
        # Round-robin: if we have last_used_id, find next free wallet after it
        # IMPORTANT: last_used_id might not be in sorted_keys if it has entry_amount_usd = 0
        # In that case, we skip round-robin and use fallback (first free wallet)
        if last_used_id is not None:
            # Check if last_used_id is still enabled (has entry_amount_usd > 0)
            # If not, skip round-robin and use fallback
            if last_used_id in enabled_wallet_ids:
                # Find index of last used wallet in sorted list
                last_used_idx = None
                for idx, k in enumerate(sorted_keys):
                    if int(k.get("id")) == last_used_id:
                        last_used_idx = idx
                        break
                
                # Start from next wallet after last_used
                if last_used_idx is not None:
                    # Try wallets after last_used (wrap around)
                    for offset in range(1, len(sorted_keys) + 1):
                        check_idx = (last_used_idx + offset) % len(sorted_keys)
                        check_key = sorted_keys[check_idx]
                        check_id = int(check_key.get("id"))
                        if check_id not in used_key_ids:
                            kp = _load_keypair_by_id(check_id)
                            if kp:
                                wallet_info = {
                                    "key_id": check_id,
                                    "keypair": kp,
                                    "address": str(kp.pubkey())
                                }
                                if getattr(config, 'DEBUG', False):
                                    print(f"[get_free_wallet] ✅ Selected wallet (round-robin): id={check_id}, address={wallet_info['address']}")
                                return wallet_info
            else:
                if getattr(config, 'DEBUG', False):
                    print(f"[get_free_wallet] ⚠️ Last used wallet id={last_used_id} no longer enabled; using fallback")
        
        # Fallback: return first free wallet (lowest ID)
        for k in free_wallets:
            key_id = int(k.get("id"))
            kp = _load_keypair_by_id(key_id)
            if kp:
                wallet_info = {
                    "key_id": key_id,
                    "keypair": kp,
                    "address": str(kp.pubkey())
                }
                if getattr(config, 'DEBUG', False):
                    print(f"[get_free_wallet] ✅ Selected wallet (fallback): id={key_id}, address={wallet_info['address']}")
                return wallet_info
        
        return None
    except Exception as e:
        return None


async def execute_buy(
    token_id: int, 
    keypair: Keypair, 
    amount_usd: float, 
    token_address: str, 
    token_decimals: int,
    rpc_endpoint: str = HELIUS_RPC,
    sender_endpoint: str = HELIUS_RPC,
    simulate: bool = False
) -> Dict:
    """Універсальна функція для виконання реальної покупки токена через RPC endpoint.
    
    Використовує Jupiter API для quote/swap (аналіз, котирування), а RPC endpoint для симуляції та відправки транзакцій.
    
    Args:
        token_id: ID токена в БД
        keypair: Solana keypair для підпису транзакцій
        amount_usd: Сума покупки в USD
        token_address: Адреса токена
        token_decimals: Кількість десяткових знаків токена
        rpc_endpoint: RPC endpoint для симуляції транзакцій (за замовчуванням: HELIUS_RPC)
        sender_endpoint: RPC endpoint для відправки транзакцій (за замовчуванням: HELIUS_RPC)
    
    Algorithm:
    1. Simulate a sell transaction to detect honeypot (if sell fails → honeypot)
    2. If simulation passes → execute real buy
    3. Collect all commission/slippage data
    
    Returns:
        dict with success, signature, amount_tokens, price_usd, etc.
    """
    MAX_RETRIES = 3
    RETRY_DELAY = (1, 3)
    
    try:
        async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
            sol_price = get_current_sol_price()
            if sol_price <= 0:
                sol_price = 0.0
            
            # Check balance ONLY for real transactions (skip for simulation)
            # In simulation mode, we don't need real balance - transaction is not sent to blockchain
            use_simulation = simulate or getattr(config, 'SIMULATE_TRANSACTIONS', False)
            if not use_simulation:
                balance_sol = await get_wallet_balance_sol(keypair, session=session)
                if balance_sol <= 0:
                    return {"success": False, "message": "Insufficient SOL balance"}
                
                # Calculate amount in SOL
                sol_need = amount_usd / sol_price
                if sol_need > balance_sol * 0.95:  # Leave 5% for fees
                    return {"success": False, "message": "Insufficient SOL balance (need fee buffer)"}
            else:
                # Simulation mode: calculate sol_need for quote, but don't check balance
                sol_need = amount_usd / sol_price
            
            # STEP 1: HONEYPOT CHECK - Simulate a small sell to detect honeypot
            # If we can't simulate selling, it's likely a honeypot
            test_sell_amount = 1000 * (10**token_decimals)  # Small test amount (1000 tokens)
            
            honeypot_check_passed = False
            for attempt in range(MAX_RETRIES):
                try:
                    # Rate limiting: чекати між запитами до Jupiter
                    await _wait_for_jupiter_rate_limit()
                    
                    # Get test sell quote (Jupiter для аналізу)
                    async with session.get(
                        f"{JUP}/quote",
                        params={
                            "inputMint": token_address,
                            "outputMint": SOL_MINT,
                            "amount": test_sell_amount,
                            "slippageBps": 50
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        test_quote = await resp.json(content_type=None)
                    
                    if "error" in test_quote:
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(random.uniform(*RETRY_DELAY))
                            continue
                        return {"success": False, "message": f"Honeypot detected: cannot get sell quote: {test_quote.get('error', 'Unknown')}"}
                    
                    # Rate limiting: чекати між запитами до Jupiter
                    await _wait_for_jupiter_rate_limit()
                    
                    # Build swap transaction for simulation (Jupiter для побудови транзакції)
                    async with session.post(
                        f"{JUP}/swap",
                        json={
                            "quoteResponse": test_quote,
                            "userPublicKey": "11111111111111111111111111111111",  # Dummy pubkey for simulation
                            "computeUnitPriceMicroLamports": 10000
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        swap_res = await resp.json(content_type=None)
                    
                    if "error" in swap_res:
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(random.uniform(*RETRY_DELAY))
                            continue
                        return {"success": False, "message": f"Honeypot detected: cannot build sell swap: {swap_res.get('error', 'Unknown')}"}
                    
                    swap_tx = swap_res.get("swapTransaction")
                    if not swap_tx:
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(random.uniform(*RETRY_DELAY))
                            continue
                        return {"success": False, "message": "Honeypot detected: no swap transaction returned"}
                    
                    # Simulate transaction через RPC endpoint
                    tx_bytes = base64.b64decode(swap_tx)
                    tx_base64 = base64.b64encode(tx_bytes).decode()
                    
                    simulate_payload = {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "simulateTransaction",
                        "params": [
                            tx_base64,
                            {
                                "encoding": "base64",
                                "commitment": "confirmed",
                                "sigVerify": False,
                                "replaceRecentBlockhash": True
                            }
                        ]
                    }
                    
                    async with session.post(rpc_endpoint, json=simulate_payload, timeout=DEFAULT_TIMEOUT) as resp:
                        sim_res = await resp.json(content_type=None)
                    if "error" in sim_res:
                        error_msg = sim_res["error"].get("message", "Unknown")
                        # If simulation fails → likely honeypot
                        return {
                            "success": False,
                            "message": f"Honeypot detected: sell simulation failed: {error_msg}",
                            "simulation_error": error_msg
                        }
                    
                    # Simulation passed → not a honeypot, proceed with buy
                    honeypot_check_passed = True
                    break
                    
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(random.uniform(*RETRY_DELAY))
                        continue
                    return {"success": False, "message": f"Honeypot check failed: {str(e)}"}
            
            if not honeypot_check_passed:
                return {"success": False, "message": "Honeypot check failed after retries"}
            
            # STEP 2: Execute real buy (honeypot check passed) with retry logic for slippage
            # Start with conservative 2.5% slippage and step up gradually
            raw_amount = int(sol_need * (10**SOL_DECIMALS))
            slippage_levels = BUY_SLIPPAGE_LEVELS or [250, 350, 450, 550]
            buy_success = False
            signature = None
            amount_tokens = 0
            token_price_usd = 0
            final_tx_result = None  # Store final transaction result for simulation data
            last_quote_data = None
            last_swap_payload = None
            last_slippage_used = None
            final_tx_result = None
            
            for slippage_bps in slippage_levels:
                try:
                    # Rate limiting: чекати між запитами до Jupiter (максимум 1 запит в секунду)
                    await _wait_for_jupiter_rate_limit()
            
                    # Get fresh quote with current slippage tolerance (Jupiter для аналізу)
                    async with session.get(
                        f"{JUP}/quote",
                        params={
                            "inputMint": SOL_MINT,
                            "outputMint": token_address,
                            "amount": raw_amount,
                            "slippageBps": slippage_bps
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Quote HTTP error {resp.status}: {text[:200]}"}
                            continue  # Try next slippage level
                        try:
                            quote = await resp.json(content_type=None)
                        except Exception as e:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Quote JSON parse error: {str(e)}, response: {text[:200]}"}
                            continue  # Try next slippage level
            
                    if "error" in quote:
                        if slippage_bps == slippage_levels[-1]:  # Last attempt
                            return {"success": False, "message": f"Quote error: {quote.get('error', 'Unknown')}"}
                        continue  # Try next slippage level
            
                    amount_tokens = int(quote["outAmount"]) / (10**token_decimals)
                    token_price_usd = amount_usd / amount_tokens if amount_tokens > 0 else 0
                    quote_usd_value = _safe_float(quote.get("swapUsdValue"), amount_usd)
                    if quote_usd_value <= 0:
                        quote_usd_value = amount_usd
                    last_quote_data = {
                        "slippage_bps": slippage_bps,
                        "price_impact_pct": _safe_float(quote.get("priceImpactPct")),
                        "expected_amount_usd": quote_usd_value,
                    }
                    last_slippage_used = slippage_bps
            
                    # Rate limiting: чекати між запитами до Jupiter
                    await _wait_for_jupiter_rate_limit()
                    
                    # Build swap transaction (Jupiter для побудови транзакції)
                    async with session.post(
                        f"{JUP}/swap",
                        json={
                            "quoteResponse": quote,
                            "userPublicKey": str(keypair.pubkey()),
                            "computeUnitPriceMicroLamports": 10000  # Priority fee
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Swap HTTP error {resp.status}: {text[:200]}"}
                            continue  # Try next slippage level
                        try:
                            swap = await resp.json(content_type=None)
                        except Exception as e:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Swap JSON parse error: {str(e)}, response: {text[:200]}"}
                            continue  # Try next slippage level
            
                    if "error" in swap:
                        if slippage_bps == slippage_levels[-1]:  # Last attempt
                            return {"success": False, "message": f"Swap error: {swap.get('error', 'Unknown')}"}
                        continue  # Try next slippage level
            
                    # Sign and send transaction через sender endpoint (or simulate)
                    # TEMPORARY: Use config.SIMULATE_TRANSACTIONS for testing (set to False for real trading)
                    use_simulation = simulate or getattr(config, 'SIMULATE_TRANSACTIONS', False)
                    if use_simulation:
                        tx_result = await simulate_buy_transaction(
                            session, swap, keypair, quote, amount_usd, token_decimals, slippage_bps
                        )
                    else:
                        tx_result = await _sign_and_send_transaction(
                            session, swap, keypair, sender_endpoint, slippage_bps, slippage_levels
                        )
                    
                    if tx_result.get("success"):
                        signature = tx_result.get("signature")
                        buy_success = True
                        final_tx_result = tx_result  # Store for later use
                        last_swap_payload = swap
                        # Store additional simulation data if available
                        if simulate and "amount_tokens" in tx_result:
                            amount_tokens = tx_result.get("amount_tokens", amount_tokens)
                        break  # Success - exit retry loop
                    elif tx_result.get("retry"):
                        # Should retry with next slippage level
                        continue
                    else:
                        # Final error - don't retry
                        return {"success": False, "message": tx_result.get("message", "Transaction failed")}
                        
                except Exception as e:
                    if slippage_bps == slippage_levels[-1]:  # Last attempt
                        return {"success": False, "message": f"Exception during buy: {str(e)}"}
                    continue  # Try next slippage level
            
            if not buy_success or not signature:
                return {"success": False, "message": "Buy failed after all slippage retry attempts"}
        
        # Prepare result with all transaction details
        result = {
            "success": True,
            "signature": signature,
            "amount_tokens": amount_tokens,
            "amount_usd": amount_usd,
            "price_usd": token_price_usd,
            "sol_amount": sol_need
        }
        
        # Add simulation data if available (from simulate_buy_transaction)
        if simulate and final_tx_result:
            result.update({
                "slippage_bps": final_tx_result.get("slippage_bps"),
                "slippage_pct": final_tx_result.get("slippage_pct"),
                "price_impact_pct": final_tx_result.get("price_impact_pct"),
                "transaction_fee_sol": final_tx_result.get("transaction_fee_sol"),
                "transaction_fee_usd": final_tx_result.get("transaction_fee_usd"),
                "expected_amount_usd": final_tx_result.get("expected_amount_usd"),
                "actual_amount_usd": final_tx_result.get("actual_amount_usd"),
            })
        
        # Ensure real trades also capture metrics for history/analytics
        if last_slippage_used is not None:
            _set_if_missing(result, "slippage_bps", last_slippage_used)
            _set_if_missing(result, "slippage_pct", last_slippage_used / 10000.0)
        if last_quote_data:
            _set_if_missing(result, "price_impact_pct", last_quote_data.get("price_impact_pct"))
            _set_if_missing(result, "expected_amount_usd", last_quote_data.get("expected_amount_usd"))
        tx_fee_sol = None
        tx_fee_usd = None
        if last_swap_payload:
            priority_fee_lamports = int(last_swap_payload.get("prioritizationFeeLamports") or 0)
            tx_fee_sol = (priority_fee_lamports + BASE_TX_FEE_LAMPORTS) / (10**9)
            tx_fee_usd = tx_fee_sol * sol_price
            _set_if_missing(result, "transaction_fee_sol", tx_fee_sol)
            _set_if_missing(result, "transaction_fee_usd", tx_fee_usd)
        expected_usd = result.get("expected_amount_usd", amount_usd)
        _set_if_missing(result, "expected_amount_usd", expected_usd)
        if tx_fee_usd is None:
            tx_fee_usd = result.get("transaction_fee_usd", 0.0) or 0.0
        _set_if_missing(result, "actual_amount_usd", expected_usd + tx_fee_usd)
        
        return result
    except Exception as e:
        return {"success": False, "message": f"Exception: {str(e)}"}


async def execute_sell(
    token_id: int, 
    keypair: Keypair, 
    token_address: str, 
    token_amount: float, 
    token_decimals: int,
    rpc_endpoint: str = HELIUS_RPC,
    sender_endpoint: str = HELIUS_RPC,
    simulate: bool = False,
) -> Dict:
    """Універсальна функція для виконання реальної продажі токена через RPC endpoint.
    
    Використовує Jupiter API для quote/swap (аналіз, котирування), а RPC endpoint для відправки транзакцій.
    
    Args:
        token_id: ID токена в БД
        keypair: Solana keypair для підпису транзакцій
        token_address: Адреса токена
        token_amount: Кількість токенів для продажу
        token_decimals: Кількість десяткових знаків токена
        rpc_endpoint: RPC endpoint для симуляції транзакцій (за замовчуванням: HELIUS_RPC)
        sender_endpoint: RPC endpoint для відправки транзакцій (за замовчуванням: HELIUS_RPC)
    
    Returns:
        dict with success, signature, amount_sol, amount_usd, price_usd, etc.
    """
    try:
        async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
            sol_price = get_current_sol_price()
            if sol_price <= 0:
                return {"success": False, "message": "Failed to get SOL price"}
           
            # Calculate amount to sell
            raw_amount = int(round(token_amount * (10**token_decimals)))
            
            # Execute sell with retry logic for slippage (fine-grained steps)
            slippage_levels = SELL_SLIPPAGE_LEVELS or [250, 270, 290, 310, 330, 350]
            sell_success = False
            signature = None
            expected_sol = 0
            expected_usd = 0
            last_quote_data = None
            last_swap_payload = None
            last_slippage_used = None
            
            for slippage_bps in slippage_levels:
                try:
                    # Rate limiting: чекати між запитами до Jupiter (максимум 1 запит в секунду)
                    await _wait_for_jupiter_rate_limit()
            
                    # Get fresh quote with current slippage tolerance (Jupiter для аналізу)
                    async with session.get(
                        f"{JUP}/quote",
                        params={
                            "inputMint": token_address,
                            "outputMint": SOL_MINT,
                            "amount": raw_amount,
                            "slippageBps": slippage_bps
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Quote HTTP error {resp.status}: {text[:200]}"}
                            continue  # Try next slippage level
                        try:
                            quote = await resp.json(content_type=None)
                        except Exception as e:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Quote JSON parse error: {str(e)}, response: {text[:200]}"}
                            continue  # Try next slippage level
            
                    if "error" in quote:
                        if slippage_bps == slippage_levels[-1]:  # Last attempt
                            return {"success": False, "message": f"Quote error: {quote.get('error', 'Unknown')}"}
                        continue  # Try next slippage level
            
                    expected_sol = int(quote["outAmount"]) / (10**SOL_DECIMALS)
                    expected_usd = expected_sol * sol_price
                    quote_usd_value = _safe_float(quote.get("swapUsdValue"), expected_usd)
                    if quote_usd_value <= 0:
                        quote_usd_value = expected_usd
                    last_quote_data = {
                        "slippage_bps": slippage_bps,
                        "price_impact_pct": _safe_float(quote.get("priceImpactPct")),
                        "expected_amount_usd": quote_usd_value,
                    }
                    last_slippage_used = slippage_bps
                    
                    # IMPORTANT: Update expected_usd from quote BEFORE simulation/real transaction
                    # This ensures we have a valid fallback value even if tx_result doesn't contain amount_usd
                    # (This is especially important for simulation mode where we need to calculate exit_amount_usd)
            
                    # Rate limiting: чекати між запитами до Jupiter
                    await _wait_for_jupiter_rate_limit()
                    
                    # Build swap transaction (Jupiter для побудови транзакції)
                    async with session.post(
                        f"{JUP}/swap",
                        json={
                            "quoteResponse": quote,
                            "userPublicKey": str(keypair.pubkey()),
                            "computeUnitPriceMicroLamports": 10000  # Priority fee
                        },
                        timeout=DEFAULT_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Swap HTTP error {resp.status}: {text[:200]}"}
                            continue  # Try next slippage level
                        try:
                            swap = await resp.json(content_type=None)
                        except Exception as e:
                            text = await resp.text()
                            if slippage_bps == slippage_levels[-1]:  # Last attempt
                                return {"success": False, "message": f"Swap JSON parse error: {str(e)}, response: {text[:200]}"}
                            continue  # Try next slippage level
            
                    if "error" in swap:
                        if slippage_bps == slippage_levels[-1]:  # Last attempt
                            return {"success": False, "message": f"Swap error: {swap.get('error', 'Unknown')}"}
                        continue  # Try next slippage level
            
                    # Sign and send transaction через sender endpoint (or simulate)
                    # TEMPORARY: Use config.SIMULATE_TRANSACTIONS for testing (set to False for real trading)
                    use_simulation = simulate or getattr(config, 'SIMULATE_TRANSACTIONS', False)
                    if use_simulation:
                        tx_result = await simulate_sell_transaction(
                            session, swap, keypair, quote, token_amount, token_decimals, slippage_bps
                        )
                    else:
                        tx_result = await _sign_and_send_transaction(
                            session, swap, keypair, sender_endpoint, slippage_bps, slippage_levels
                        )
                    
                    if tx_result.get("success"):
                        signature = tx_result.get("signature")
                        sell_success = True
                        last_swap_payload = swap
                        final_tx_result = tx_result
                        # IMPORTANT: Always use actual values from tx_result (simulation or real)
                        # This ensures exit_amount_usd is correctly set even in simulation mode
                        if "amount_sol" in tx_result:
                            expected_sol = tx_result.get("amount_sol", expected_sol)
                        if "amount_usd" in tx_result:
                            expected_usd = tx_result.get("amount_usd", expected_usd)
                        # Also check for actual_usd (from simulation) if amount_usd is not available
                        if expected_usd == 0 and "actual_usd" in tx_result:
                            expected_usd = tx_result.get("actual_usd", 0)
                        break  # Success - exit retry loop
                    elif tx_result.get("retry"):
                        # Should retry with next slippage level
                        continue
                    else:
                        # Final error - don't retry
                        return {"success": False, "message": tx_result.get("message", "Transaction failed")}
                        
                except Exception as e:
                    if slippage_bps == slippage_levels[-1]:  # Last attempt
                        return {"success": False, "message": f"Exception during sell: {str(e)}"}
                    continue  # Try next slippage level
            
            if not sell_success or not signature:
                return {"success": False, "message": "Sell failed after all slippage retry attempts"}
            
            result = {
                "success": True,
                "signature": signature,
                "amount_sol": expected_sol,
                "amount_usd": expected_usd,
                "price_usd": token_price_usd
            }
            if simulate and final_tx_result:
                result.update({
                    "slippage_bps": final_tx_result.get("slippage_bps"),
                    "slippage_pct": final_tx_result.get("slippage_pct"),
                    "price_impact_pct": final_tx_result.get("price_impact_pct"),
                    "transaction_fee_sol": final_tx_result.get("transaction_fee_sol"),
                    "transaction_fee_usd": final_tx_result.get("transaction_fee_usd"),
                    "expected_amount_usd": final_tx_result.get("expected_amount_usd"),
                    "actual_amount_usd": final_tx_result.get("actual_amount_usd"),
                })
            if last_slippage_used is not None:
                _set_if_missing(result, "slippage_bps", last_slippage_used)
                _set_if_missing(result, "slippage_pct", last_slippage_used / 10000.0)
            if last_quote_data:
                _set_if_missing(result, "price_impact_pct", last_quote_data.get("price_impact_pct"))
                _set_if_missing(result, "expected_amount_usd", last_quote_data.get("expected_amount_usd"))
            tx_fee_sol = None
            tx_fee_usd = None
            if last_swap_payload:
                priority_fee_lamports = int(last_swap_payload.get("prioritizationFeeLamports") or 0)
                tx_fee_sol = (priority_fee_lamports + BASE_TX_FEE_LAMPORTS) / (10**9)
                tx_fee_usd = tx_fee_sol * sol_price
                _set_if_missing(result, "transaction_fee_sol", tx_fee_sol)
                _set_if_missing(result, "transaction_fee_usd", tx_fee_usd)
            expected_usd_value = result.get("expected_amount_usd", expected_usd)
            _set_if_missing(result, "expected_amount_usd", expected_usd_value)
            if tx_fee_usd is None:
                tx_fee_usd = result.get("transaction_fee_usd", 0.0) or 0.0
            net_usd = max(expected_usd_value - tx_fee_usd, 0.0)
            result["amount_usd"] = net_usd
            _set_if_missing(result, "actual_amount_usd", net_usd)
            return result
    except Exception as e:
        return {"success": False, "message": f"Exception: {str(e)}"}


async def sell_real(token_id: int, *, source: str = 'auto_sell', simulate: bool = False) -> dict:
    """REAL TRADING: Продати токени на основі запису в журналі покупок/продаж.
    
    Логіка:
    1. Знайти відкриту позицію в wallet_history для token_id (exit_iteration IS NULL)
    2. Взяти entry_token_amount з журналу - це кількість токенів для продажу (ВСЯ кількість)
    3. Виконати реальну продажу через Jupiter API (з retry logic: зменшує на 1% при помилці)
    4. Оновити журнал (exit_* поля)
    
    NOTE: This function is used by both auto-sell (via analyzer) and force-sell (manual).
    - Force sell: Sells immediately, all tokens, no conditions
    - Auto-sell: Analyzer checks conditions (TARGET_RETURN, iterations) before calling this
    
    Returns:
        dict with success, token_id, amount_tokens, price_usd, amount_usd, signature
    """
    print(f"[sell_real] 🎯 sell_real called for token {token_id}, source={source}")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async def _log(status: str, message: str, wallet_id_value: Optional[int] = None, details: Optional[dict] = None):
            await log_trade_attempt(conn, token_id, wallet_id_value, source, status, message, details)

        # 1. Знайти відкриту позицію в журналі (з FOR UPDATE lock для запобігання race conditions)
        history_row = await conn.fetchrow(
            """
            SELECT 
                wallet_id, 
                entry_token_amount,
                token_id
            FROM wallet_history
            WHERE token_id=$1 
              AND exit_iteration IS NULL
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            token_id
        )
        
        if not history_row:
            # No open position - archive token directly (quiet path)
            await _log("skipped", "No open position to sell")
            
            # Check if token exists in tokens table
            token_exists = await conn.fetchval("SELECT id FROM tokens WHERE id=$1", token_id)
            if not token_exists:
                return {"success": True, "message": "Token already deleted (no open position to sell)", "token_id": token_id}
            
            iterations = await get_token_iterations_count(conn, token_id)
            archive_info = await _archive_or_purge_token(conn, token_id, iterations)
            result_payload = archive_info.get("result", {})
            success_flag = bool(result_payload.get("success"))
            message = "Token archived (no open position to sell)" if archive_info["action"] == "archive" else "Token purged (short lifespan)"
            if not success_flag:
                return {"success": False, "message": result_payload.get("message", message), "token_id": token_id, archive_info["action"]: result_payload}
            return {"success": True, "message": message, "token_id": token_id, archive_info["action"]: result_payload}
        
        wallet_id_value = history_row.get("wallet_id")
        if not wallet_id_value:
            print(f"[sell_real] ❌ wallet_id missing in wallet_history entry for token {token_id}")
            await _log("failed", "wallet_id missing in wallet_history entry")
            return {"success": False, "message": "wallet_id missing in wallet_history"}

        wallet_id = int(wallet_id_value)
        token_amount_db = float(history_row["entry_token_amount"] or 0.0)
        print(f"[sell_real] 📊 Found position: wallet_id={wallet_id}, token_amount (from DB)={token_amount_db}")
        
        if token_amount_db <= 0:
            print(f"[sell_real] ❌ Invalid token amount: {token_amount_db}")
            await _log("failed", "Invalid token amount in journal", wallet_id)
            return {"success": False, "message": "Invalid token amount in journal"}
        
        # 2. Отримати інформацію про токен
        token_row = await conn.fetchrow(
            "SELECT token_address, decimals, wallet_id FROM tokens WHERE id=$1",
            token_id
        )
        
        if not token_row:
            print(f"[sell_real] ❌ Token {token_id} not found in tokens table")
            await _log("failed", "Token not found", wallet_id)
            return {"success": False, "message": "Token not found"}
        
        token_address = token_row["token_address"]
        token_decimals = int(token_row["decimals"]) if token_row["decimals"] else 6
        print(f"[sell_real] 📝 Token info: address={token_address}, decimals={token_decimals}")
        
        # 3. Перевірити wallet_id
        wallet_id_bound = token_row.get("wallet_id")
        if not wallet_id_bound:
            print(f"[sell_real] ❌ No wallet binding found for token {token_id} (tokens.wallet_id is NULL)")
            await _log("failed", "No wallet binding found for this token", wallet_id)
            return {"success": False, "message": "No wallet binding found for this token"}
        
        key_id = int(wallet_id_bound)
        print(f"[sell_real] 🔑 Loading keypair for wallet_id={key_id}")
        
        # 4. Load keypair
        keypair = _load_keypair_by_id(key_id)
        if not keypair:
            print(f"[sell_real] ❌ Wallet key_id={key_id} not found in keys.json")
            await _log("failed", f"Wallet key_id={key_id} not found", wallet_id)
            return {"success": False, "message": f"Wallet key_id={key_id} not found"}
        
        print(f"[sell_real] ✅ Keypair loaded: {keypair.pubkey()}")
        
        # 4.5. Use DB token amount directly (avoid extra RPC requests that hit rate limits)
        # Ensure we sell only whole tokens to avoid fractional residuals causing failures
        token_amount = floor(token_amount_db)
        if token_amount <= 0:
            print(f"[sell_real] ❌ Token amount too small after flooring: {token_amount_db}")
            await _log("failed", "Token amount too small after flooring", wallet_id)
            return {"success": False, "message": "Token amount too small after flooring"}
        if token_amount < token_amount_db:
            print(f"[sell_real] ℹ️ Truncated fractional tokens ({token_amount_db - token_amount:.8f}). Selling {token_amount} whole tokens.")
        print(f"[sell_real] 🧾 Using DB token amount: {token_amount:.8f} tokens for sell execution")

        # 5. Execute real sell with retry logic (reduce amount by 1% on failure)
        current_amount = token_amount
        max_retries = 10  # Maximum 10 retries (reduce by 1% each time)
        RETRY_DELAY = (1, 3)  # Delay between retries (1-3 seconds) to avoid Jupiter rate limiting
        sell_result = None
        
        print(f"[sell_real] 🚀 Starting sell execution: amount={current_amount}, max_retries={max_retries}")
        
        for attempt in range(max_retries):
            print(f"[sell_real] 🔄 Attempt {attempt + 1}/{max_retries}: selling {current_amount} tokens")
            # Execute real sell через Helius (Jupiter для quote/swap, Helius для відправки)
            rpc_endpoint, sender_endpoint = _choose_rpc_endpoints()
            sell_result = await execute_sell(
                token_id=token_id,
                keypair=keypair,
                token_address=token_address,
                token_amount=current_amount,
                token_decimals=token_decimals,
                rpc_endpoint=rpc_endpoint,
                sender_endpoint=sender_endpoint,
                simulate=simulate
            )
            print(f"[sell_real] 📥 execute_sell result: success={sell_result.get('success') if sell_result else None}, message={sell_result.get('message', 'N/A') if sell_result else 'No result'}")
            
            if sell_result.get("success"):
                # Success - use the actual amount sold
                token_amount = current_amount
                break
            
            # Failed - reduce amount by 1% for next attempt
            if attempt < max_retries - 1:
                next_amount = floor(current_amount * 0.99)
                if next_amount == current_amount and current_amount > 1:
                    next_amount -= 1
                current_amount = max(next_amount, 1)
                # Wait before next retry to avoid Jupiter rate limiting
                await asyncio.sleep(random.uniform(*RETRY_DELAY))
                # Continue to next retry
            else:
                # All retries exhausted
                await _log("failed", sell_result.get("message", "Sell simulation failed") if sell_result else "Sell simulation failed", wallet_id, sell_result)
                return sell_result
        
        if not sell_result or not sell_result.get("success"):
            print(f"[sell_real] ❌ Force sell failed for token {token_id}: {sell_result.get('message', 'Unknown error') if sell_result else 'No result'}")
            await _log("failed", sell_result.get("message", "Unknown error") if sell_result else "Unknown error", wallet_id, sell_result)
            return sell_result
        
        actual_usd_received = sell_result.get("actual_amount_usd", sell_result.get("amount_usd", 0.0))
        expected_usd_received = sell_result.get("expected_amount_usd")
        fee_sol_val = sell_result.get("transaction_fee_sol")
        fee_usd_val = sell_result.get("transaction_fee_usd")
        price_usd = sell_result.get("price_usd", 0.0)
        signature = sell_result.get("signature")
        
        sim_status = " (SIMULATED)" if simulate else ""
        print(f"[sell_real] ✅ Force sell successful{sim_status} for token {token_id}: wallet_id={key_id}, amount=${actual_usd_received:.2f}, signature={signature}")
        if expected_usd_received is not None:
            print(f"  - Expected gross amount: ${expected_usd_received:.2f}")
        if fee_sol_val is not None:
            print(f"  - Transaction fee: {fee_sol_val} SOL (${fee_usd_val or 0:.6f})")
        slippage_pct_val = sell_result.get("slippage_pct")
        if slippage_pct_val is not None:
            print(f"  - Slippage: {slippage_pct_val:.4%}")
        if sell_result.get("price_impact_pct") is not None:
            print(f"  - Price impact: {sell_result.get('price_impact_pct'):.4%}")
        await _log(
            "success",
            "Sell executed",
            wallet_id,
            {
                "wallet_id": wallet_id,
                "amount_usd": actual_usd_received,
                "price_usd": price_usd,
                "signature": signature,
                "source": source,
            },
        )
        
        # 6. Clear wallet binding (free wallet for next use)
        sim_status = " (SIMULATED)" if simulate else ""
        try:
            await conn.execute(
                """
                UPDATE tokens 
                SET wallet_id=NULL,
                    token_updated_at=CURRENT_TIMESTAMP 
                WHERE id=$1
                """,
                token_id
            )
            print(f"[sell_real] ✅ Cleared wallet_id for token {token_id}{sim_status}")
            print(f"  - Wallet {wallet_id} is now FREE and available for next buy")
        except Exception as e:
            print(f"[sell_real] ⚠️ Failed to clear wallet_id for token {token_id}: {e}")
        
        # 7. Update journal with exit details (REAL trading data)
        # Get current iteration = count of records in token_metrics_seconds with usd_price > 0 (non-zero)
        # Iteration = real seconds of token life with valid price (each token has its own count)
        # Example: 100th iteration = 100th second with valid price
        # This is the REAL exit iteration, not hardcoded 1!
        exit_iteration = await get_token_iterations_count(conn, token_id)
        
        # Use actual amount sold (may be less than entry_token_amount if retry was needed)
        actual_amount_sold = token_amount  # This is the amount that was successfully sold
        exit_slippage_bps = sell_result.get("slippage_bps")
        exit_slippage_pct = sell_result.get("slippage_pct")
        exit_price_impact_pct = sell_result.get("price_impact_pct")
        exit_expected_amount_usd = expected_usd_received
        if exit_expected_amount_usd is None:
            exit_expected_amount_usd = actual_usd_received + (fee_usd_val or 0)
        exit_actual_amount_usd = sell_result.get("actual_amount_usd", actual_usd_received)
        try:
            await conn.execute(
                """
                UPDATE wallet_history SET
                  exit_token_amount=$1,
                  exit_price_usd=$2,
                  exit_amount_usd=$3,
                  exit_signature=$4,
                  exit_iteration=$5,
                  exit_slippage_bps=$6,
                  exit_slippage_pct=$7,
                  exit_price_impact_pct=$8,
                  exit_transaction_fee_sol=$9,
                  exit_transaction_fee_usd=$10,
                  exit_expected_amount_usd=$11,
                  exit_actual_amount_usd=$12,
                  outcome='closed',
                  reason='manual',
                  updated_at=CURRENT_TIMESTAMP
                WHERE wallet_id=$13 AND token_id=$14 AND exit_iteration IS NULL
                """,
                actual_amount_sold, price_usd, actual_usd_received, signature, exit_iteration,
                exit_slippage_bps, exit_slippage_pct, exit_price_impact_pct,
                fee_sol_val, fee_usd_val, exit_expected_amount_usd, exit_actual_amount_usd,
                wallet_id, token_id
            )
            print(f"[sell_real] 📝 Updated wallet_history{sim_status}:")
            print(f"  - wallet_id={wallet_id}, token_id={token_id}")
            print(f"  - exit_token_amount={actual_amount_sold:.8f}")
            print(f"  - exit_price_usd=${price_usd:.8f}")
            print(f"  - exit_amount_usd=${actual_usd_received:.2f}")
            print(f"  - exit_iteration={exit_iteration}")
            if exit_slippage_pct is not None:
                print(f"  - exit_slippage: {exit_slippage_pct:.4%}")
            if exit_price_impact_pct is not None:
                print(f"  - exit_price_impact: {exit_price_impact_pct:.4%}")
            if fee_sol_val is not None:
                print(f"  - exit_transaction_fee: {fee_sol_val} SOL (${fee_usd_val or 0:.6f})")
        except Exception as e:
            print(f"[sell_real] ⚠️ Failed to update wallet_history for token {token_id}: {e}")
            import traceback
            traceback.print_exc()

        # CRITICAL: Archive token after successful sell (MANDATORY - token is no longer needed)
        # After selling, we never return to this token, so it MUST be archived
        archive_success = False
        archive_retries = 3
        archive_delay = 0.5  # seconds between retries
        
        for archive_attempt in range(archive_retries):
            try:
                archive_result = await archive_token(token_id, conn=conn)
                if archive_result.get("success"):
                    archive_success = True
                    print(f"[sell_real] 📦 Token {token_id} archived successfully (attempt {archive_attempt + 1}/{archive_retries}): moved_metrics={archive_result.get('moved_metrics', 0)}, moved_trades={archive_result.get('moved_trades', 0)}, deleted_tokens={archive_result.get('deleted_tokens', 'N/A')}")
                    # Verify token was deleted
                    token_still_exists = await conn.fetchval("SELECT id FROM tokens WHERE id=$1", token_id)
                    if token_still_exists:
                        print(f"[sell_real] ❌ ERROR: Token {token_id} still exists in tokens table after archiving!")
                    else:
                        print(f"[sell_real] ✅ Verified: Token {token_id} successfully removed from tokens table")
                    break  # Success - exit retry loop
                else:
                    archive_message = archive_result.get('message', 'Unknown error')
                    if archive_attempt < archive_retries - 1:
                        print(f"[sell_real] ⚠️ Token {token_id} archive failed (attempt {archive_attempt + 1}/{archive_retries}): {archive_message}, retrying...")
                        await asyncio.sleep(archive_delay)
                    else:
                        print(f"[sell_real] ❌ CRITICAL: Token {token_id} archive failed after {archive_retries} attempts: {archive_message}")
            except Exception as e:
                if archive_attempt < archive_retries - 1:
                    print(f"[sell_real] ⚠️ Error archiving token {token_id} (attempt {archive_attempt + 1}/{archive_retries}): {e}, retrying...")
                    await asyncio.sleep(archive_delay)
                else:
                    print(f"[sell_real] ❌ CRITICAL: Error archiving token {token_id} after {archive_retries} attempts: {e}")
                    import traceback
                    traceback.print_exc()
        
        if not archive_success:
            # CRITICAL ERROR: Archive failed but sell was successful
            # Token will remain in tokens table, but position is closed
            # This is a critical error that needs attention
            print(f"[sell_real] 🚨 CRITICAL ERROR: Token {token_id} was sold successfully but archiving failed!")
            print(f"[sell_real] 🚨 Token {token_id} will remain in tokens table - manual cleanup may be required")
            await _log("warning", f"Token sold but archive failed after {archive_retries} attempts", wallet_id, {
                "token_id": token_id,
                "archive_retries": archive_retries,
                "sell_successful": True
            })
        
        return {
            "success": True,
            "token_id": token_id,
            "amount_tokens": actual_amount_sold,  # Actual amount sold (may be less than entry if retry was needed)
            "price_usd": price_usd,
            "amount_usd": actual_usd_received,
            "signature": signature,
            "archived": archive_success  # Indicate if archiving was successful
        }


async def finalize_token_sale(token_id: int, conn, reason: str = 'auto') -> bool:
    """
    Finalize token sale for REAL trading only:
    - If an open wallet_history exists: close it with zeroed exit (for freeze/zero_liquidity), clear wallet binding, and archive token.
    - If no open history: archive token directly.
    """
    try:
        # Try close open journal record
        row = await conn.fetchrow(
            """
            SELECT id, wallet_id, entry_token_amount
            FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            token_id
        )
        if row:
            # Get current iteration (real seconds with valid price)
            current_iteration = await get_token_iterations_count(conn, token_id)
            exit_iteration = int(current_iteration or 0)
            
            try:
                await conn.execute(
                    """
                    UPDATE wallet_history
                    SET exit_iteration=$2,
                        exit_token_amount=COALESCE(exit_token_amount, entry_token_amount),
                        exit_price_usd=0.0,
                        exit_amount_usd=0.0,
                        outcome='frozen',
                        reason=$3,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=$1
                    """,
                    row["id"], exit_iteration, reason
                )
            except Exception:
                pass
            # Clear wallet binding before archiving
            await conn.execute(
                """
                UPDATE tokens
                SET wallet_id = NULL,
                    token_updated_at = CURRENT_TIMESTAMP
                WHERE id=$1
                """,
                token_id
            )
            # Archive token directly (moves to tokens_history and removes from tokens)
            try:
                await archive_token(token_id, conn=conn)
            except Exception:
                pass
            return True
        else:
            archive_info = await _archive_or_purge_token(conn, token_id)
            return bool(archive_info.get("result", {}).get("success", False))
    except Exception:
        return False


# Router functions for HTTP endpoints
async def force_sell(token_id: int, simulate: bool = False) -> dict:
    """Router: Force sell (REAL trading only, or SIMULATED if simulate=True).
    
    IMPORTANT: Force sell sells ALL tokens immediately (entry_token_amount from wallet_history).
    It does NOT check:
    - Target return (TARGET_RETURN)
    - Current portfolio value vs target
    - Plan sell iteration/price (plan_sell_*)
    - Any auto-sell conditions
    
    Force sell executes immediately in the current thread (not in parallel).
    Auto-sell (via analyzer) works according to rules: TARGET_RETURN, iterations, etc.
    
    If simulate=True, transaction is simulated without actually sending to blockchain.
    """
    sim_status = " (SIMULATED)" if simulate else ""
    print(f"[force_sell] 🚀 Force sell{sim_status} called for token {token_id}")
    try:
        result = await sell_real(token_id, source='force_sell', simulate=simulate)
        print(f"[force_sell] ✅ Force sell completed for token {token_id}: success={result.get('success')}")
        return result
    except Exception as e:
        print(f"[force_sell] ❌ Force sell error for token {token_id}: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"Force sell error: {str(e)}"}


async def buy_real(token_id: int, *, source: str = 'auto_buy', simulate: bool = False) -> dict:
    """REAL TRADING: Купити токени з реальним кошельком та блокчейн транзакцією.
    
    Логіка:
    1. Отримати вільний реальний кошелек з keys.json
    2. Викликати execute_buy, який:
       a. Перевіряє honeypot через симуляцію продажу (test sell simulation)
       b. Якщо honeypot check пройшов - виконує реальну покупку
    3. Записати в wallet_history з деталями транзакції
    4. Прив'язати кошелек до токена (wallet_id)
    
    NOTE: This function is used by both auto-buy (via analyzer) and force-buy (manual).
    It does NOT check patterns - pattern checks are done in analyzer before calling auto-buy.
    Force buy bypasses all pattern checks and calls this function directly.
    
    IMPORTANT: Honeypot check is ALWAYS performed (even for force buy) to protect against scams.
    Honeypot check simulates a small sell transaction - if it fails, token is blocked.
    
    Returns:
        dict with success, token_id, wallet_id, amount_tokens, price_usd
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async def _log(status: str, message: str, wallet_id_value: Optional[int] = None, details: Optional[dict] = None):
            await log_trade_attempt(conn, token_id, wallet_id_value, source, status, message, details)

        # ATOMIC CHECK: Lock token row and check for open position to prevent race conditions
        # This prevents double-buy when multiple buy_real calls happen in parallel
        token_row = await conn.fetchrow(
            """
            SELECT token_address, decimals, wallet_id 
            FROM tokens 
            WHERE id=$1
            FOR UPDATE
            """,
            token_id
        )
        if not token_row:
            await _log("failed", "Token not found")
            return {"success": False, "message": "Token not found"}
        
        # Check if token already has wallet_id (atomic check - prevents race condition)
        if token_row.get("wallet_id") is not None:
            await _log("failed", "Token already bound to wallet - cannot enter again")
            return {"success": False, "message": "Token already bound to wallet - cannot enter again", "token_id": token_id}
        
        # Do NOT allow new buy if position is still open (not closed)
        # Check for open position in wallet_history (double-check)
        open_position = await conn.fetchrow(
            """
            SELECT id FROM wallet_history
            WHERE token_id=$1 AND exit_iteration IS NULL
            LIMIT 1
            """,
            token_id
        )
        if open_position:
            await _log("failed", "Position already open - cannot enter again")
            return {"success": False, "message": "Position already open - cannot enter again", "token_id": token_id}
        
        # CRITICAL: Check if token has real trading (SWAP) before allowing buy
        # This prevents buying tokens that only have TRANSFER transactions (no real market)
        token_pair = token_row.get("token_pair")
        if token_pair and not USE_JUPITER_RPC:
            try:
                has_real_trading = await check_token_has_real_trading(token_id, token_pair)
                if not has_real_trading:
                    await _log("failed", "Token has no real trading (only TRANSFER, no SWAP transactions)")
                    return {
                        "success": False,
                        "message": "Token has no real trading (only TRANSFER, no SWAP transactions) - unsafe to buy",
                        "token_id": token_id
                    }
            except Exception as e:
                # On error, be conservative - don't allow buy
                await _log("failed", f"Trade type check error: {str(e)}")
                return {
                    "success": False,
                    "message": f"Trade type check failed: {str(e)}",
                    "token_id": token_id
                }
        
        # Get free real wallet
        wallet_info = await get_free_wallet(conn)
        if not wallet_info:
            await _log("failed", "No free real wallet available")
            return {"success": False, "message": "No free real wallet available"}
        
        keypair = wallet_info["keypair"]
        key_id = wallet_info["key_id"]
        token_address = token_row["token_address"]
        token_decimals = int(token_row["decimals"]) if token_row["decimals"] else 6
        
        sim_status = " (SIMULATED)" if simulate else ""
        print(f"[buy_real] 🔑 Selected wallet{sim_status}: wallet_id={key_id}, pubkey={keypair.pubkey()}")
        
        # Get entry amount from wallet (user-configured per wallet) or config default
        wallet_row = await conn.fetchrow(
            "SELECT entry_amount_usd FROM wallets WHERE id=$1",
            key_id
        )
        if wallet_row and wallet_row.get("entry_amount_usd"):
            entry_amount_usd = float(wallet_row["entry_amount_usd"])
            print(f"[buy_real] 💰 Using wallet-specific entry amount: ${entry_amount_usd:.2f} (from wallets table)")
        else:
            # Fallback to config default
            try:
                entry_amount_usd = float(getattr(config, 'DEFAULT_ENTRY_AMOUNT_USD', 5.0))
                print(f"[buy_real] 💰 Using default entry amount: ${entry_amount_usd:.2f} (from config)")
            except Exception:
                entry_amount_usd = 5.0
                print(f"[buy_real] 💰 Using fallback entry amount: ${entry_amount_usd:.2f}")
        
        # ATOMIC RESERVATION: Set wallet_id immediately to prevent race condition
        # This ensures only one buy_real call can proceed for this token
        # If buy fails, we'll clear wallet_id in the error handler
        try:
            # Use UPDATE ... RETURNING to check if update succeeded
            updated_row = await conn.fetchrow(
                """
                UPDATE tokens 
                SET wallet_id=$2, token_updated_at=CURRENT_TIMESTAMP 
                WHERE id=$1 AND wallet_id IS NULL
                RETURNING id
                """,
                token_id, key_id
            )
            # If no rows returned, another buy_real already claimed this token
            if not updated_row:
                await _log("failed", "Token already reserved by another buy operation")
                return {"success": False, "message": "Token already reserved by another buy operation", "token_id": token_id}
        except Exception as e:
            await _log("failed", f"Failed to reserve token: {str(e)}")
            return {"success": False, "message": f"Failed to reserve token: {str(e)}"}
        
        # Execute real buy (Jupiter для quote/swap, RPC endpoint для симуляції/відправки)
        rpc_endpoint, sender_endpoint = _choose_rpc_endpoints()
        buy_result = await execute_buy(
            token_id=token_id,
            keypair=keypair,
            amount_usd=entry_amount_usd,
            token_address=token_address,
            token_decimals=token_decimals,
            rpc_endpoint=rpc_endpoint,
            sender_endpoint=sender_endpoint,
            simulate=simulate
        )
        
        if not buy_result.get("success"):
            error_message = buy_result.get("message", "Unknown error")
            print(f"[buy_real] ❌ Force buy failed for token {token_id}: {error_message}")
            await _log("failed", error_message, key_id, buy_result)
            
            # IMPORTANT: If Jupiter cannot find route or slippage error after all retries,
            # this is a safety signal - mark token as "not buy" and archive it
            # This prevents entering dangerous tokens with low liquidity or route problems
            is_jupiter_route_error = (
                "Could not find any route" in error_message or
                "Quote error" in error_message or
                "0x1771" in error_message or
                "6001" in error_message or
                "slippage" in error_message.lower()
            )
            
            if is_jupiter_route_error:
                # Покупка не состоялась из-за маршрута/slippage. Просто логируем.
                print(f"[buy_real] ⚠️ Jupiter route/slippage error on token {token_id}: {error_message}")
            
            # Clear wallet_id reservation if buy failed
            try:
                await conn.execute(
                    "UPDATE tokens SET wallet_id=NULL, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
                    token_id
                )
            except Exception:
                pass
            
            return buy_result
        
        # CRITICAL: Verify that signature exists before writing to DB
        # If signature is missing, transaction didn't actually execute
        signature = buy_result.get("signature")
        if not signature:
            error_msg = "Buy transaction returned success but no signature (transaction may have failed)"
            print(f"[buy_real] ❌ {error_msg} for token {token_id}")
            await _log("failed", error_msg, key_id, buy_result)
            # Clear wallet_id reservation if signature is missing
            try:
                await conn.execute(
                    "UPDATE tokens SET wallet_id=NULL, token_updated_at=CURRENT_TIMESTAMP WHERE id=$1",
                    token_id
                )
            except Exception:
                pass
            return {"success": False, "message": error_msg}
        
        sim_status = " (SIMULATED)" if simulate else ""
        amount_tokens = buy_result.get('amount_tokens', 0)
        price_usd = buy_result.get('price_usd', 0)
        signature = buy_result.get('signature', 'N/A')
        print(f"[buy_real] ✅ Force buy successful{sim_status} for token {token_id}:")
        print(f"  - Wallet ID: {key_id}")
        print(f"  - Entry amount USD: ${entry_amount_usd:.2f}")
        print(f"  - Token amount: {amount_tokens:.8f} tokens")
        print(f"  - Token price USD: ${price_usd:.8f}")
        print(f"  - Signature: {signature}")
        slippage_pct_val = buy_result.get("slippage_pct")
        if slippage_pct_val is not None:
            print(f"  - Slippage: {slippage_pct_val:.4%}")
        price_impact_val = buy_result.get("price_impact_pct")
        if price_impact_val is not None:
            print(f"  - Price impact: {price_impact_val:.4%}")
        fee_sol_val = buy_result.get("transaction_fee_sol")
        if fee_sol_val is not None:
            print(f"  - Transaction fee: {fee_sol_val} SOL (${buy_result.get('transaction_fee_usd', 0):.6f})")
        await _log(
            "success",
            "Buy executed",
            key_id,
            {
                "wallet_id": key_id,
                "amount_usd": entry_amount_usd,
                "amount_tokens": buy_result.get("amount_tokens"),
                "price_usd": buy_result.get("price_usd"),
                "signature": buy_result.get("signature"),
                "source": source,
            },
        )
        
        # Note: wallet_id is already set atomically above (lines 1224-1232) to prevent race conditions
        # No need to update again here
        
        # Get current iteration = count of records in token_metrics_seconds with usd_price > 0 (non-zero)
        # Iteration = real seconds of token life with valid price (each token has its own count)
        # Example: 100th iteration = 100th second with valid price
        # This is the REAL entry iteration, not hardcoded 1!
        entry_iteration = await get_token_iterations_count(conn, token_id)
        
        # Log to history
        history_id = None
        try:
            history_id = await conn.fetchval(
                """
                INSERT INTO wallet_history(
                    wallet_id, token_id,
                    entry_amount_usd, entry_token_amount, entry_price_usd, entry_iteration,
                    entry_slippage_bps, entry_slippage_pct, entry_price_impact_pct, entry_transaction_fee_sol, entry_transaction_fee_usd,
                    entry_expected_amount_usd, entry_actual_amount_usd, entry_signature,
                    outcome, reason, created_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'','manual',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                key_id, token_id,
                entry_amount_usd, buy_result.get("amount_tokens"), buy_result.get("price_usd"), entry_iteration,
                buy_result.get("slippage_bps"), buy_result.get("slippage_pct"), buy_result.get("price_impact_pct"),
                buy_result.get("transaction_fee_sol"), buy_result.get("transaction_fee_usd"),
                buy_result.get("expected_amount_usd"), buy_result.get("actual_amount_usd"),
                buy_result.get("signature")
            )
            print(f"[buy_real] 📝 Recorded in wallet_history{sim_status}:")
            print(f"  - wallet_id={key_id}, token_id={token_id}")
            print(f"  - entry_amount_usd=${entry_amount_usd:.2f}")
            print(f"  - entry_token_amount={buy_result.get('amount_tokens'):.8f}")
            print(f"  - entry_price_usd=${buy_result.get('price_usd'):.8f}")
            print(f"  - entry_iteration={entry_iteration}")
            print(f"  - Wallet is now bound to token (tokens.wallet_id={key_id})")
            print(f"  - Waiting for sell to free wallet...")
        except Exception as e:
            print(f"[buy_real] ⚠️ Failed to write to wallet_history: {e}")
            import traceback
            traceback.print_exc()

        # After transaction is confirmed, reconcile quantities/fees with on-chain data (Helius)
        if history_id and not simulate:
            await _update_real_buy_metrics(
                conn=conn,
                history_id=history_id,
                token_id=token_id,
                wallet_id=key_id,
                wallet_address=str(keypair.pubkey()),
                signature=signature,
                token_address=token_address,
                token_decimals=token_decimals
            )
        
        return {
            "success": True,
            "token_id": token_id,
            "wallet_id": key_id,
            "amount_tokens": buy_result.get("amount_tokens"),
            "price_usd": buy_result.get("price_usd")
        }


async def force_buy(token_id: int, simulate: bool = False) -> dict:
    """Router: Force buy (REAL trading only, or SIMULATED if simulate=True).
    
    IMPORTANT: Force buy bypasses ALL pattern checks and blocking logic.
    It only checks:
    - Token exists
    - No open position exists
    - Free wallet available
    - Sufficient balance
    
    Force buy does NOT check:
    - Pattern code (good/bad patterns)
    - Pattern at AI_PREVIEW_ENTRY_SEC
    - Bad pattern history
    - AUTO_BUY_ENTRY_SEC threshold
    - Pattern score
    
    This allows manual entry even for tokens that would be blocked by auto-buy logic.
    
    If simulate=True, transaction is simulated without actually sending to blockchain.
    """
    return await buy_real(token_id, source='force_buy', simulate=simulate)


async def _fetch_helius_transaction(
    signature: str,
    retries: int = HELIUS_MAX_ATTEMPTS,
    initial_delay: float = HELIUS_INITIAL_DELAY_SEC,
    delay: float = HELIUS_RETRY_DELAY_SEC,
    backoff: float = HELIUS_RETRY_BACKOFF,
) -> Optional[Dict[str, Any]]:
    """
    Fetch parsed transaction payload from Helius Transaction API.
    """
    api_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if not api_key:
        print("[buy_real] ⚠️ HELIUS_API_KEY not configured; skipping reconciliation")
        return None

    base_url = getattr(config, "HELIUS_TRANSACTIONS_URL", "https://api.helius.xyz/v0/transactions")
    url = f"{base_url}?api-key={api_key}"
    payload = {"transactions": [signature]}

    wait = max(initial_delay, 0.0)
    if wait > 0:
        await asyncio.sleep(wait)
    delay_between = max(delay, 0.0)
    backoff = max(backoff, 1.0)
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Helius HTTP {resp.status}: {text[:200]}")
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        return data[0]
                    raise RuntimeError("Helius returned empty response")
        except Exception as e:
            print(f"[buy_real] ⚠️ Helius fetch failed (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                if delay_between > 0:
                    await asyncio.sleep(delay_between)
                delay_between *= backoff
            else:
                return None
    return None


def _extract_token_amount_from_tx(tx_data: Dict[str, Any], wallet_address: str, token_address: str, token_decimals: int) -> Optional[float]:
    """
    Extract token amount transferred to wallet from Helius payload.
    """
    if not tx_data:
        return None

    for transfer in tx_data.get("tokenTransfers", []):
        if transfer.get("mint") == token_address and transfer.get("toUserAccount") == wallet_address:
            amount = transfer.get("tokenAmount")
            if amount is not None:
                try:
                    return float(amount)
                except ValueError:
                    pass
            raw = transfer.get("rawTokenAmount") or {}
            raw_amount = raw.get("tokenAmount")
            decimals = raw.get("decimals", token_decimals)
            if raw_amount is not None:
                try:
                    return float(raw_amount) / (10 ** decimals)
                except Exception:
                    continue

    for acc in tx_data.get("accountData", []):
        for change in acc.get("tokenBalanceChanges", []):
            if change.get("userAccount") == wallet_address and change.get("mint") == token_address:
                raw = change.get("rawTokenAmount") or {}
                raw_amount = raw.get("tokenAmount")
                decimals = raw.get("decimals", token_decimals)
                if raw_amount is not None:
                    try:
                        return float(raw_amount) / (10 ** decimals)
                    except Exception:
                        continue
    return None


async def _update_real_buy_metrics(
    conn,
    history_id: int,
    token_id: int,
    wallet_id: int,
    wallet_address: str,
    signature: str,
    token_address: str,
    token_decimals: int,
) -> None:
    """
    After a buy succeeds, fetch actual on-chain data (via Helius) and update wallet_history.
    """
    confirmed = await _wait_for_signature_confirmation(signature)
    if not confirmed:
        print(f"[buy_real] ⚠️ Signature {signature} not confirmed within timeout; skipping reconciliation")
        return
    tx_data = await _fetch_helius_transaction(signature)
    if not tx_data:
        print(f"[buy_real] ⚠️ Helius did not return data for signature {signature}; keeping quoted metrics.")
        return

    actual_token_amount = _extract_token_amount_from_tx(tx_data, wallet_address, token_address, token_decimals)
    fee_lamports = int(tx_data.get("fee") or 0)
    priority_lamports = int(tx_data.get("priorityFee") or tx_data.get("priority_fee") or tx_data.get("prioritizationFeeLamports") or 0)
    total_fee_lamports = fee_lamports + priority_lamports
    fee_sol = total_fee_lamports / LAMPORTS_PER_SOL if total_fee_lamports else None
    fee_usd = fee_sol * get_current_sol_price() if fee_sol else None

    try:
        await conn.execute(
            """
            UPDATE wallet_history
            SET
                entry_token_amount = COALESCE($2, entry_token_amount),
                entry_transaction_fee_sol = COALESCE($3, entry_transaction_fee_sol),
                entry_transaction_fee_usd = COALESCE($4, entry_transaction_fee_usd),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            history_id,
            actual_token_amount,
            fee_sol,
            fee_usd,
        )
        if actual_token_amount is not None:
            print(f"[buy_real] ♻️ wallet_history#{history_id}: token amount reconciled to {actual_token_amount:.8f}")
        if fee_sol is not None:
            print(f"[buy_real] ♻️ wallet_history#{history_id}: fee updated to {fee_sol:.9f} SOL")
    except Exception as e:
        print(f"[buy_real] ⚠️ Failed to update wallet_history with real metrics: {e}")


# =============================================================================
# CLI helper
# =============================================================================
async def _cli_force_sell(token_address: str, key_id: int, simulate: bool) -> int:
    """
    Sell entire position for token_address from wallet key_id via CLI.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, wallet_id
            FROM tokens
            WHERE token_address = $1 OR token_pair = $1
            ORDER BY token_updated_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            token_address,
        )

    if not row:
        print(f"[CLI] ❌ Token '{token_address}' not found in tokens table.")
        await close_db_pool()
        return 1

    token_id = int(row["id"])
    bound_wallet = row["wallet_id"]
    if not bound_wallet:
        print(f"[CLI] ❌ Token {token_id} has no wallet binding (wallet_id=NULL). Cannot sell.")
        await close_db_pool()
        return 1

    bound_wallet = int(bound_wallet)
    if bound_wallet != key_id:
        print(
            f"[CLI] ❌ Wallet mismatch: token {token_id} bound to wallet_id={bound_wallet}, "
            f"but --key-id={key_id} supplied."
        )
        await close_db_pool()
        return 1

    print(f"[CLI] 🚀 Selling token_id={token_id}, wallet_id={bound_wallet}, simulate={simulate}")
    result = await sell_real(token_id, source="cli_force_sell", simulate=simulate)
    await close_db_pool()

    if result.get("success"):
        print(f"[CLI] ✅ Sell completed: {result.get('message', 'success')}")
        amt = result.get("amount_usd") or result.get("actual_amount_usd")
        if amt is not None:
            print(f"[CLI] 💵 Amount USD: {amt}")
        price = result.get("price_usd")
        if price is not None:
            print(f"[CLI] 🏷 Price USD: {price}")
        sig = result.get("signature")
        if sig:
            print(f"[CLI] 🔗 Signature: {sig}")
        return 0

    print(f"[CLI] ❌ Sell failed: {result.get('message', 'Unknown error')}")
    return 1
