#!/usr/bin/env python3
"""
Standalone manual BUY script via Jupiter.

Usage example:
  python3 server/manual_buy.py \
      --key-id 2 \
      --token-address 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR \
      --amount-usd 1.0 \
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
# Local SOL price fetch (DexScreener) so CLI works even when monitor is offline
async def fetch_sol_price() -> float:
    # Try DexScreener
    url = "https://api.dexscreener.com/tokens/v1/solana/So11111111111111111111111111111111111111112"
    try:
        async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        price = float(data[0].get("priceUsd", 0) or 0)
                        if price > 0:
                            return price
    except Exception:
        pass
    # Fallback to stored config value
    return float(getattr(config, "SOL_PRICE_FALLBACK", 0.0) or 0.0)

JUP_BASE = "https://lite-api.jup.ag/swap/v1"
SOL_MINT = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9
LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)
RPC_TIMEOUT = aiohttp.ClientTimeout(total=30)
BUY_SLIPPAGE_LEVELS = list(getattr(config, "BUY_SLIPPAGE_LEVELS", [250, 350, 450, 550]))
HELIUS_INITIAL_DELAY_SEC = float(getattr(config, "HELIUS_INITIAL_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_DELAY_SEC = float(getattr(config, "HELIUS_RETRY_DELAY_SEC", 2.0) or 0.0)
HELIUS_RETRY_BACKOFF = float(getattr(config, "HELIUS_RETRY_BACKOFF", 1.5) or 1.0)
HELIUS_MAX_ATTEMPTS = int(getattr(config, "HELIUS_MAX_ATTEMPTS", 5) or 1)

_rate_lock = asyncio.Lock()
_last_request = 0.0


def _wallet_keys_path() -> Path:
    path = Path(config.WALLET_KEYS_FILE)
    return path if path.is_absolute() else Path(config.BASE_DIR) / path


def _load_keypair(key_id: int) -> Keypair:
    wallet_file = _wallet_keys_path()
    if not wallet_file.exists():
        raise RuntimeError(f"Wallet keys file not found: {wallet_file}")
    with open(wallet_file) as f:
        keys = json.load(f)
    for entry in keys:
        if int(entry.get("id", -1)) == key_id:
            bits = entry.get("bits")
            if bits:
                return Keypair.from_bytes(bytes(bits))
    raise RuntimeError(f"Wallet key_id={key_id} not found in {wallet_file}")


async def _respect_rate_limit():
    global _last_request
    async with _rate_lock:
        now = time.time()
        delta = now - _last_request
        if delta < 1.0:
            await asyncio.sleep(1.0 - delta)
        _last_request = time.time()


def _rpc_endpoint() -> str:
    helius_url = getattr(config, "HELIUS_RPC_URL", "").strip()
    if helius_url:
        return helius_url
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
        async with session.post(_rpc_endpoint(), json=payload) as resp:
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
            print(f"[ManualBuy] ℹ️ Provided decimals {override}, but mint uses {actual}. Using on-chain value.")
        return actual
    if override is not None:
        return override
    print("[ManualBuy] ⚠️ Failed to fetch token decimals; defaulting to 6")
    return 6


async def _fetch_quote(session: aiohttp.ClientSession, token_address: str, amount_raw: int, slippage_bps: int):
    await _respect_rate_limit()
    params = {
        "inputMint": SOL_MINT,
        "outputMint": token_address,
        "amount": amount_raw,
        "slippageBps": slippage_bps,
    }
    async with session.get(f"{JUP_BASE}/quote", params=params) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Quote HTTP {resp.status}: {text[:200]}")
        data = await resp.json(content_type=None)
        if "error" in data:
            raise RuntimeError(f"Quote error: {data.get('error')}")
        return data


async def _fetch_swap(session: aiohttp.ClientSession, quote: dict, public_key: str):
    await _respect_rate_limit()
    payload = {
        "quoteResponse": quote,
        "userPublicKey": public_key,
        "computeUnitPriceMicroLamports": 10000,
    }
    async with session.post(f"{JUP_BASE}/swap", json=payload) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Swap HTTP {resp.status}: {text[:200]}")
        data = await resp.json(content_type=None)
        if "error" in data:
            raise RuntimeError(f"Swap error: {data.get('error')}")
        return data


async def _send_transaction(session: aiohttp.ClientSession, swap_payload: dict, keypair: Keypair) -> str:
    tx_bytes = base64.b64decode(swap_payload["swapTransaction"])
    vtx = VersionedTransaction.from_bytes(tx_bytes)
    vtx = VersionedTransaction(vtx.message, [keypair])
    signed_tx = base64.b64encode(bytes(vtx)).decode()

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            signed_tx,
            {"encoding": "base64", "preflightCommitment": "confirmed", "skipPreflight": False},
        ],
    }
    async with session.post(_rpc_endpoint(), json=payload, timeout=RPC_TIMEOUT) as resp:
        res = await resp.json(content_type=None)
        if "error" in res:
            raise RuntimeError(f"sendTransaction error: {res['error']}")
        sig = res.get("result")
        if not sig:
            raise RuntimeError("sendTransaction returned no signature")
        return sig


async def _wait_for_signature_confirmation(signature: str, timeout_sec: float = 30.0, poll_interval: float = 0.5) -> bool:
    rpc = _rpc_endpoint()
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
                print(f"[ManualBuy] ⚠️ getSignatureStatuses error: {exc}")
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(poll_interval)


async def _fetch_helius_transaction(
    signature: str,
    retries: int = HELIUS_MAX_ATTEMPTS,
    initial_delay: float = HELIUS_INITIAL_DELAY_SEC,
    delay: float = HELIUS_RETRY_DELAY_SEC,
    backoff: float = HELIUS_RETRY_BACKOFF,
):
    api_key = getattr(config, "HELIUS_API_KEY", "").strip()
    if not api_key:
        print("[ManualBuy] ⚠️ HELIUS_API_KEY not set; cannot fetch real swap data")
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
            print(f"[ManualBuy] ⚠️ Helius fetch failed (attempt {attempt}/{retries}): {exc}")
            if attempt < retries:
                if delay_between > 0:
                    await asyncio.sleep(delay_between)
                delay_between *= backoff
            else:
                return None
    return None


def _extract_token_amount_from_tx(tx_data: dict, wallet_address: str, token_address: str, decimals: int) -> Optional[float]:
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
            raw_decimals = raw.get("decimals", decimals)
            if raw_amount is not None:
                try:
                    return float(raw_amount) / (10 ** raw_decimals)
                except Exception:
                    continue

    for account in tx_data.get("accountData", []):
        for change in account.get("tokenBalanceChanges", []):
            if change.get("userAccount") == wallet_address and change.get("mint") == token_address:
                raw = change.get("rawTokenAmount") or {}
                raw_amount = raw.get("tokenAmount")
                raw_decimals = raw.get("decimals", decimals)
                if raw_amount is not None:
                    try:
                        return float(raw_amount) / (10 ** raw_decimals)
                    except Exception:
                        continue
    return None


async def manual_buy(args):
    keypair = _load_keypair(args.key_id)
    print(f"[ManualBuy] Wallet loaded: {keypair.pubkey()}")

    decimals = await _resolve_token_decimals(args.token_address, args.decimals)

    sol_price = await fetch_sol_price()
    if sol_price <= 0:
        raise RuntimeError("Failed to fetch SOL price (DexScreener + fallback)")
    sol_need = args.amount_usd / sol_price
    if sol_need <= 0:
        raise RuntimeError("Requested USD amount too small")
    lamports = int(round(sol_need * (10 ** SOL_DECIMALS)))
    if lamports <= 0:
        raise RuntimeError("Lamports amount too small after conversion")

    print(f"[ManualBuy] Spending ~{sol_need:.8f} SOL (${args.amount_usd:.2f})")

    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        for slippage_bps in BUY_SLIPPAGE_LEVELS:
            try:
                quote = await _fetch_quote(session, args.token_address, lamports, slippage_bps)
                swap_payload = await _fetch_swap(session, quote, str(keypair.pubkey()))
                sig = await _send_transaction(session, swap_payload, keypair)

                confirmed = await _wait_for_signature_confirmation(sig)
                if not confirmed:
                    print("[ManualBuy] ⚠️ Transaction confirmation timed out; skipping Helius reconciliation")

                out_amount_tokens = int(quote["outAmount"]) / (10 ** decimals)
                real_price = args.amount_usd / out_amount_tokens if out_amount_tokens > 0 else 0.0
                print("[ManualBuy] ✅ Buy completed")
                print(f"  - Slippage used: {slippage_bps / 100:.2f}%")
                print(f"  - Tokens received: {out_amount_tokens:.8f}")
                print(f"  - Effective price: ${real_price:.8f} per token")
                print(f"  - Signature: {sig}")

                if confirmed:
                    tx_data = await _fetch_helius_transaction(sig)
                    if tx_data:
                        actual_tokens = _extract_token_amount_from_tx(tx_data, str(keypair.pubkey()), args.token_address, decimals)
                        if actual_tokens is not None:
                            print(f"  - Actual tokens on-chain: {actual_tokens:.8f}")
                        fee_lamports = int(tx_data.get("fee") or 0)
                        priority_lamports = int(tx_data.get("priorityFee") or tx_data.get("priority_fee") or tx_data.get("prioritizationFeeLamports") or 0)
                        total_fee = fee_lamports + priority_lamports
                        if total_fee:
                            fee_sol = total_fee / LAMPORTS_PER_SOL
                            fee_usd = fee_sol * sol_price
                            print(f"  - Real fee paid: {fee_sol:.9f} SOL (${fee_usd:.6f})")
                    else:
                        print("[ManualBuy] ⚠️ Helius did not return transaction data after retries; keeping quote numbers.")

                return
            except Exception as exc:
                print(f"[ManualBuy] ⚠️ Attempt with slippage {slippage_bps}bps failed: {exc}")
                await asyncio.sleep(1)
        raise RuntimeError("All slippage attempts failed")


def parse_args():
    parser = argparse.ArgumentParser(description="Standalone manual buy CLI")
    parser.add_argument("--token-address", required=True, help="Token mint to buy")
    parser.add_argument("--key-id", type=int, required=True, help="Wallet ID from keys.json")
    parser.add_argument("--amount-usd", type=float, required=True, help="Spend amount in USD")
    parser.add_argument("--decimals", type=int, default=None, help="Token decimals (auto-detect if omitted)")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        asyncio.run(manual_buy(args))
    except Exception as exc:
        print(f"[ManualBuy] ❌ Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
