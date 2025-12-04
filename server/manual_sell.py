#!/usr/bin/env python3
"""
Manual standalone token sell script.

Usage:
  python3 server/manual_sell.py \
      --key-id 2 \
      --token-address 5stKCLTe3S7Fn8bZwzU3TCpYCbu4bcUQEMfp3YFWkWoh \
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
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)
RPC_TIMEOUT = aiohttp.ClientTimeout(total=30)
SELL_SLIPPAGE_LEVELS = list(getattr(config, "SELL_SLIPPAGE_LEVELS", [250, 270, 290, 310, 330, 350]))

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


async def manual_sell(args):
    keypair = _load_keypair(args.key_id)
    print(f"[ManualSell] Wallet loaded: {keypair.pubkey()}")

    if args.amount.lower() == "all":
        balance = await _get_token_balance(keypair, args.token_address, args.decimals)
        if balance <= 0:
            raise RuntimeError("Token balance is zero")
        amount_tokens = balance
    else:
        amount_tokens = float(args.amount)
    print(f"[ManualSell] Selling amount: {amount_tokens} tokens")

    amount_raw = int(round(amount_tokens * (10 ** args.decimals)))
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
                return
            except Exception as exc:
                print(f"[ManualSell] ⚠️ Attempt with slippage {slippage_bps}bps failed: {exc}")
                await asyncio.sleep(1)
        raise RuntimeError("All slippage attempts failed")


def parse_args():
    parser = argparse.ArgumentParser(description="Standalone manual sell CLI")
    parser.add_argument("--token-address", required=True, help="Token mint address")
    parser.add_argument("--key-id", type=int, required=True, help="Wallet ID from keys.json")
    parser.add_argument("--amount", required=True, help="Amount to sell ('all' to sell entire wallet balance)")
    parser.add_argument("--decimals", type=int, default=6, help="Token decimals (default 6)")
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
