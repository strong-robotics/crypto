#!/usr/bin/env python3
"""
Manual standalone token sell script.

Usage:
  python3 server/manual_sell.py \
      --key-id 2 \
      --token-address 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR \
      --amount all \
      --decimals 9
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from config import config
from _v2_sol_price import get_current_sol_price

JUP_BASE = "https://lite-api.jup.ag/swap/v1"
SOL_MINT = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9
LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)
RPC_TIMEOUT = aiohttp.ClientTimeout(total=30)
SELL_SLIPPAGE_LEVELS = list(getattr(config, "SELL_SLIPPAGE_LEVELS", [250, 270, 290, 310, 330, 350]))
HELIUS_INITIAL_DELAY_SEC = float(getattr(config, "HELIUS_INITIAL_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_DELAY_SEC = float(getattr(config, "HELIUS_RETRY_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_BACKOFF = float(getattr(config, "HELIUS_RETRY_BACKOFF", 1.5) or 1.0)
HELIUS_MAX_ATTEMPTS = int(getattr(config, "HELIUS_MAX_ATTEMPTS", 5) or 1)
BALANCE_RETRY_ATTEMPTS = int(getattr(config, "MANUAL_SELL_BALANCE_ATTEMPTS", 5) or 1)
BALANCE_RETRY_DELAY = float(getattr(config, "MANUAL_SELL_BALANCE_DELAY_SEC", 0.75) or 0.0)

_rate_limit_lock = asyncio.Lock()
_last_jup_request = 0.0


def _wallet_file() -> Path:
    path = Path(config.WALLET_KEYS_FILE)
    if not path.is_absolute():
        path = Path(config.BASE_DIR) / path
    return path


def _load_keypair(key_id: int) -> Keypair:
    wallet_file = _wallet_file()
    if not wallet_file.exists():
        raise RuntimeError(f"Wallet keys file not found: {wallet_file}")
    with open(wallet_file) as f:
        keys = json.load(f)
    for entry in keys:
        if int(entry.get("id", -1)) == key_id:
            bits = entry.get("bits")
            if not bits:
                break
            return Keypair.from_bytes(bytes(bits))
    raise RuntimeError(f"Wallet key_id={key_id} not found in {wallet_file}")


async def _wait_jupiter_limit():
    global _last_jup_request
    async with _rate_limit_lock:
        now = time.time()
        delta = now - _last_jup_request
        if delta < 1.0:
            await asyncio.sleep(1.0 - delta)
        _last_jup_request = time.time()


def _resolve_rpc_endpoint() -> str:
    helius = getattr(config, "HELIUS_RPC_URL", "").strip()
    if helius:
        return helius
    helius_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    return getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


async def _fetch_token_decimals(token_address: str) -> Optional[int]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [token_address, {"encoding": "base64"}],
    }
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.post(_resolve_rpc_endpoint(), json=payload) as resp:
            data = await resp.json(content_type=None)
            value = data.get("result", {}).get("value")
            if not value:
                return None
            b64_data = value.get("data", [None])[0]
            if not b64_data:
                return None
            raw = base64.b64decode(b64_data)
            if len(raw) > 44:
                return raw[44]
    return None


async def _resolve_token_decimals(token_address: str, override: Optional[int]) -> int:
    actual = await _fetch_token_decimals(token_address)
    if actual is not None:
        if override is not None and override != actual:
            print(f"[ManualSell] ℹ️ Provided decimals {override}, but mint uses {actual}. Using on-chain value.")
        return actual
    if override is not None:
        return override
    print("[ManualSell] ⚠️ Failed to fetch token decimals; defaulting to 6")
    return 6


async def _get_token_balance(keypair: Keypair, token_address: str, token_decimals: int) -> float:
    rpc = _resolve_rpc_endpoint()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            str(keypair.pubkey()),
            {"mint": token_address},
            {"encoding": "jsonParsed"},
        ],
    }
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.post(rpc, json=payload) as resp:
            data = await resp.json(content_type=None)
            if "result" not in data:
                return 0.0
            accounts = data["result"].get("value", [])
            total = 0.0
            for acc in accounts:
                ui = (
                    acc.get("account", {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                    .get("tokenAmount", {})
                    .get("uiAmount")
                )
                if ui is not None:
                    total += float(ui)
            return total


async def _get_token_balance_with_retry(keypair: Keypair, token_address: str, token_decimals: int) -> float:
    """
    Wallet balance may still be zero immediately after a buy. Poll for a short period.
    """
    attempts = max(BALANCE_RETRY_ATTEMPTS, 1)
    delay = max(BALANCE_RETRY_DELAY, 0.0)
    for attempt in range(1, attempts + 1):
        balance = await _get_token_balance(keypair, token_address, token_decimals)
        if balance > 0:
            return balance
        if attempt < attempts and delay > 0:
            print(f"[ManualSell] ⏳ Awaiting token balance (attempt {attempt}/{attempts}); retrying in {delay:.2f}s")
            await asyncio.sleep(delay)
    return balance


async def _fetch_quote(session: aiohttp.ClientSession, token_address: str, amount_raw: int, slippage_bps: int):
    await _wait_jupiter_limit()
    params = {
        "inputMint": token_address,
        "outputMint": SOL_MINT,
        "amount": amount_raw,
        "slippageBps": slippage_bps,
    }
    async with session.get(f"{JUP_BASE}/quote", params=params) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Quote HTTP {resp.status}: {text[:200]}")
        out = await resp.json(content_type=None)
        if "error" in out:
            raise RuntimeError(f"Quote error: {out.get('error')}")
        return out


async def _fetch_swap(session: aiohttp.ClientSession, quote: dict, public_key: str):
    await _wait_jupiter_limit()
    payload = {
        "quoteResponse": quote,
        "userPublicKey": public_key,
        "computeUnitPriceMicroLamports": 10000,
    }
    async with session.post(f"{JUP_BASE}/swap", json=payload) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Swap HTTP {resp.status}: {text[:200]}")
        out = await resp.json(content_type=None)
        if "error" in out:
            raise RuntimeError(f"Swap error: {out.get('error')}")
        return out


async def _send_transaction(session: aiohttp.ClientSession, swap_payload: dict, keypair: Keypair) -> str:
    tx_bytes = base64.b64decode(swap_payload["swapTransaction"])
    vtx = VersionedTransaction.from_bytes(tx_bytes)
    vtx = VersionedTransaction(vtx.message, [keypair])
    signed_tx = base64.b64encode(bytes(vtx)).decode()

    rpc_endpoint = _resolve_rpc_endpoint()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            signed_tx,
            {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False},
        ],
    }
    async with session.post(rpc_endpoint, json=payload, timeout=RPC_TIMEOUT) as resp:
        res = await resp.json(content_type=None)
        if "error" in res:
            raise RuntimeError(f"sendTransaction error: {res['error']}")
        signature = res.get("result")
        if not signature:
            raise RuntimeError("sendTransaction returned no signature")
        return signature


async def _wait_for_signature_confirmation(signature: str, timeout_sec: float = 30.0, poll_interval: float = 0.5) -> bool:
    rpc = _resolve_rpc_endpoint()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [[signature], {"searchTransactionHistory": True}],
    }
    deadline = asyncio.get_event_loop().time() + timeout_sec
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        while True:
            try:
                async with session.post(rpc, json=payload) as resp:
                    data = await resp.json(content_type=None)
                    value = data.get("result", {}).get("value", [None])[0]
                    if value is not None:
                        return True
            except Exception as exc:
                print(f"[ManualSell] ⚠️ getSignatureStatuses error: {exc}")
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(poll_interval)


async def manual_sell(args):
    keypair = _load_keypair(args.key_id)
    print(f"[ManualSell] Wallet loaded: {keypair.pubkey()}")

    decimals = await _resolve_token_decimals(args.token_address, args.decimals)

    if args.amount.lower() == "all":
        balance = await _get_token_balance_with_retry(keypair, args.token_address, decimals)
        if balance <= 0:
            raise RuntimeError("Token balance is zero")
        amount_tokens = balance
    else:
        amount_tokens = float(args.amount)
    # Sell whole tokens only (drop fractional part)
    whole_tokens = int(amount_tokens)
    if whole_tokens <= 0:
        raise RuntimeError("Amount too small after truncation to whole tokens")
    if amount_tokens - whole_tokens > 0:
        print(f"[ManualSell] ℹ️ Truncated fractional part ({amount_tokens - whole_tokens:.6f} tokens). Selling {whole_tokens} tokens.")
    else:
        print(f"[ManualSell] Selling amount: {whole_tokens} tokens")
    if whole_tokens <= 0:
        print("[ManualSell] ⚠️ Balance below 1 token — nothing to sell.")
        return

    amount_tokens = float(whole_tokens)
    amount_raw = amount_tokens * (10 ** decimals)
    amount_raw = int(amount_raw)
    if amount_raw <= 0:
        raise RuntimeError("Amount too small after decimals conversion")

    sol_price = get_current_sol_price()
    if sol_price <= 0:
        print("[ManualSell] ⚠️ SOL price not available, USD output will be zero.")
        sol_price = 0.0

    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        for slippage_bps in SELL_SLIPPAGE_LEVELS:
            try:
                quote = await _fetch_quote(session, args.token_address, amount_raw, slippage_bps)
                swap_payload = await _fetch_swap(session, quote, str(keypair.pubkey()))
                signature = await _send_transaction(session, swap_payload, keypair)

                out_amount_sol = int(quote["outAmount"]) / (10 ** SOL_DECIMALS)
                usd_amount = out_amount_sol * sol_price
                print("[ManualSell] ✅ Sale completed")
                print(f"  - Slippage used: {slippage_bps / 100:.2f}%")
                print(f"  - Out amount: {out_amount_sol:.8f} SOL (~${usd_amount:.2f})")
                print(f"  - Signature: {signature}")
                confirmed = await _wait_for_signature_confirmation(signature)
                if not confirmed:
                    print("[ManualSell] ⚠️ Transaction confirmation timed out; skipping Helius reconciliation")
                    return
                await _report_real_sale(signature, str(keypair.pubkey()))
                return
            except Exception as exc:
                print(f"[ManualSell] ⚠️ Attempt with slippage {slippage_bps}bps failed: {exc}")
                await asyncio.sleep(1)
        raise RuntimeError("All slippage attempts failed")




async def _fetch_helius_transaction(
    signature: str,
    retries: int = HELIUS_MAX_ATTEMPTS,
    initial_delay: float = HELIUS_INITIAL_DELAY_SEC,
    delay: float = HELIUS_RETRY_DELAY_SEC,
    backoff: float = HELIUS_RETRY_BACKOFF,
) -> Optional[dict]:
    api_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if not api_key:
        print("[ManualSell] ⚠️ HELIUS_API_KEY not configured; cannot fetch real metrics")
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
            async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as helius_session:
                async with helius_session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Helius HTTP {resp.status}: {text[:200]}")
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        return data[0]
                    raise RuntimeError("Helius returned empty response")
        except Exception as exc:
            print(f"[ManualSell] ⚠️ Helius fetch failed (attempt {attempt}/{retries}): {exc}")
            if attempt < retries:
                if delay_between > 0:
                    await asyncio.sleep(delay_between)
                delay_between *= backoff
            else:
                return None
    return None


def _extract_native_change(tx_data: dict, wallet_address: str) -> Optional[float]:
    if not tx_data:
        return None
    for account in tx_data.get("accountData", []):
        if account.get("account") == wallet_address:
            lamports = int(account.get("nativeBalanceChange") or 0)
            return lamports / LAMPORTS_PER_SOL
    return None


async def _report_real_sale(signature: str, wallet_address: str):
    tx_data = await _fetch_helius_transaction(signature)
    if not tx_data:
        print("[ManualSell] ⚠️ Helius did not return transaction data after retries.")
        return
    sol_change = _extract_native_change(tx_data, wallet_address)
    if sol_change is not None:
        print(f"  - Actual SOL change (after fees): {sol_change:.8f}")
    fee_lamports = int(tx_data.get("fee") or 0)
    priority_lamports = int(tx_data.get("priorityFee") or tx_data.get("priority_fee") or tx_data.get("prioritizationFeeLamports") or 0)
    total_fee = fee_lamports + priority_lamports
    if total_fee:
        fee_sol = total_fee / LAMPORTS_PER_SOL
        fee_usd = fee_sol * get_current_sol_price()
        print(f"  - Real fee paid: {fee_sol:.9f} SOL (${fee_usd:.6f})")

def parse_args():
    parser = argparse.ArgumentParser(description="Standalone manual sell CLI")
    parser.add_argument("--token-address", required=True, help="Token mint address")
    parser.add_argument("--key-id", type=int, required=True, help="Wallet ID from keys.json")
    parser.add_argument("--amount", required=True, help="Amount to sell ('all' to sell entire wallet balance)")
    parser.add_argument("--decimals", type=int, default=None, help="Token decimals (auto-detect if omitted)")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        asyncio.run(manual_sell(args))
    except Exception as exc:
        print(f"[ManualSell] ❌ Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
