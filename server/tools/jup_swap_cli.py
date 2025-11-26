#!/usr/bin/env python3
"""
Простой CLI для тестового buy/sell через Jupiter.

Примеры:
  python jup_swap_cli.py --mode sell --token 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR --wallet-id 2
  python jup_swap_cli.py --mode buy  --token 8Tg6NK4nVe3uCz9FqhGqoY7Ed22th2YLULvCnRNnPBjR --wallet-id 2 --amount-usd 1

По умолчанию использует публичный RPC из config.SOLANA_RPC_URL (не Helius).
"""

import argparse
import base64
import json
import sys
from typing import Optional

import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

sys.path.append("..")
from config import config  # noqa: E402


RPC_ENDPOINT = getattr(config, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
JUP_ENDPOINT = "https://lite-api.jup.ag/swap/v1"
SOL_MINT = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9


def load_keypair(wallet_id: int) -> Keypair:
    with open("../keys.json") as f:
        keys = json.load(f)
    for k in keys:
        if int(k.get("id", -1)) == wallet_id:
            return Keypair.from_bytes(bytes(k["bits"]))
    raise RuntimeError(f"wallet id {wallet_id} not found in keys.json")


def rpc(method: str, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = requests.post(RPC_ENDPOINT, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def get_token_decimals(mint: str) -> int:
    try:
        supply = rpc("getTokenSupply", [mint])
        decimals = supply.get("value", {}).get("decimals")
        if decimals is not None:
            return int(decimals)
    except Exception:
        pass
    try:
        res = rpc("getMint", [mint])
        decimals = res.get("decimals")
        if decimals is not None:
            return int(decimals)
    except Exception:
        pass
    return 6


def get_token_balance(owner: str, mint: str) -> int:
    res = rpc(
        "getTokenAccountsByOwner",
        [
            owner,
            {"mint": mint},
            {"encoding": "jsonParsed"},
        ],
    )
    value = res.get("value", [])
    max_amt = 0
    for acc in value:
        amt = int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
        if amt > max_amt:
            max_amt = amt
    return max_amt


def get_sol_price_usd() -> float:
    amount = int(0.1 * (10**SOL_DECIMALS))
    q = requests.get(
        f"{JUP_ENDPOINT}/quote",
        params={
            "inputMint": SOL_MINT,
            "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "amount": amount,
            "slippageBps": 50,
        },
        timeout=10,
    ).json()
    out_amt = int(q["outAmount"])
    return out_amt / (10**6) / 0.1


def jup_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int):
    r = requests.get(
        f"{JUP_ENDPOINT}/quote",
        params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": slippage_bps,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"quote error: {data['error']}")
    return data


def jup_swap(quote_resp: dict, user_pubkey: str, cu_price: int = 5000):
    r = requests.post(
        f"{JUP_ENDPOINT}/swap",
        json={
            "quoteResponse": quote_resp,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": cu_price,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"swap error: {data['error']}")
    return data


def send_tx(swap_tx_b64: str, keypair: Keypair) -> str:
    tx_bytes = base64.b64decode(swap_tx_b64)
    vtx = VersionedTransaction.from_bytes(tx_bytes)
    vtx = VersionedTransaction(vtx.message, [keypair])
    signed = base64.b64encode(bytes(vtx)).decode()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [signed, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    }
    res = rpc("sendTransaction", payload["params"])
    return res


def do_buy(token_mint: str, wallet_id: int, amount_usd: float, slippage_bps: int):
    kp = load_keypair(wallet_id)
    sol_price = get_sol_price_usd()
    sol_need = amount_usd / sol_price
    sol_lamports = int(sol_need * (10**SOL_DECIMALS))
    quote = jup_quote(SOL_MINT, token_mint, sol_lamports, slippage_bps)
    swap = jup_swap(quote, str(kp.pubkey()))
    sig = send_tx(swap["swapTransaction"], kp)
    print(f"✅ BUY {amount_usd:.2f}$ {token_mint} via wallet {wallet_id}: signature={sig}")


def do_sell(token_mint: str, wallet_id: int, slippage_bps: int):
    kp = load_keypair(wallet_id)
    decimals = get_token_decimals(token_mint)
    balance = get_token_balance(str(kp.pubkey()), token_mint)
    if balance <= 0:
        raise RuntimeError("no token balance to sell")
    quote = jup_quote(token_mint, SOL_MINT, balance, slippage_bps)
    swap = jup_swap(quote, str(kp.pubkey()))
    sig = send_tx(swap["swapTransaction"], kp)
    amt_tokens = balance / (10**decimals)
    print(f"✅ SELL {amt_tokens:.6f} {token_mint} via wallet {wallet_id}: signature={sig}")


def main():
    p = argparse.ArgumentParser(description="Simple Jupiter buy/sell tester")
    p.add_argument("--mode", choices=["buy", "sell"], required=True)
    p.add_argument("--token", required=True, help="token mint")
    p.add_argument("--wallet-id", type=int, default=2, help="wallet id from keys.json (default 2)")
    p.add_argument("--amount-usd", type=float, default=1.0, help="buy amount in USD (for buy mode)")
    p.add_argument("--slippage-bps", type=int, default=50, help="slippage in bps (default 50=0.5%)")
    args = p.parse_args()

    if args.mode == "buy":
        do_buy(args.token, args.wallet_id, args.amount_usd, args.slippage_bps)
    else:
        do_sell(args.token, args.wallet_id, args.slippage_bps)


if __name__ == "__main__":
    main()
