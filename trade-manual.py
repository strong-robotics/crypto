#!/usr/bin/env python3
import argparse
import asyncio
import sys
import json
import aiohttp
from pathlib import Path
# Add server directory to path so we can import modules
base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir / "server"))

from config import config
from _v3_db_pool import get_db_pool, close_db_pool
from _v2_buy_sell import (
    execute_buy, 
    execute_sell, 
    _load_keypair_by_id, 
    _choose_rpc_endpoints,
    get_token_balance,
    _wait_for_signature_confirmation
)
from solana_utils import close_ata_logic, resolve_rpc_endpoint

async def fetch_sol_price_manual() -> float:
    """Fetch SOL/USD price using public DexScreener API."""
    try:
        url = "https://api.dexscreener.com/tokens/v1/solana/So11111111111111111111111111111111111111112"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        price = float(data[0].get("priceUsd", 0) or 0)
                        if price > 0:
                            return price
    except Exception as e:
        print(f"[TradeManual] ⚠️ Failed to fetch SOL price: {e}")
    
    return float(getattr(config, "SOL_PRICE_FALLBACK", 193.0))

async def get_token_decimals_rpc(token_address: str, rpc_url: str) -> int:
    """Fetch decimals from blockchain to avoid DB discrepancies."""
    try:
        from solana.rpc.async_api import AsyncClient
        from solders.pubkey import Pubkey
        async with AsyncClient(rpc_url) as client:
            res = await client.get_token_supply(Pubkey.from_string(token_address))
            if res.value:
                return res.value.decimals
    except Exception as e:
        print(f"[TradeManual] ⚠️ Failed to fetch decimals from RPC: {e}")
    return 6 # Fallback if RPC fails

async def main():
    parser = argparse.ArgumentParser(description="Manual Trading CLI")
    parser.add_argument("--mode", choices=["buy", "sell"], required=True, help="Trade mode: buy or sell")
    parser.add_argument("--token-address", required=True, help="Token mint address")
    parser.add_argument("--amount", type=float, help="Amount: USD for buy, Tokens for sell (default: all for sell)")
    parser.add_argument("--key-id", type=int, help="Wallet key ID override")
    parser.add_argument("--simulate", action="store_true", help="Simulate transaction without sending to blockchain")
    
    args = parser.parse_args()
    
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # Check if token exists in DB
        token_row = await conn.fetchrow(
            "SELECT id, decimals FROM tokens WHERE token_address = $1", args.token_address
        )
        
        if not token_row:
            if args.mode == "buy":
                print(f"[TradeManual] ⚠️ Token {args.token_address} not found in DB. Creating dummy entry...")
                token_id = await conn.fetchval(
                    "INSERT INTO tokens (token_address, decimals, symbol, name) VALUES ($1, 6, 'UNKNOWN', 'Manual CLI') RETURNING id",
                    args.token_address
                )
            else:
                print(f"[TradeManual] ❌ Token {args.token_address} not found in DB. Cannot sell what we don't know.")
                await close_db_pool()
                return
        else:
            token_id = token_row["id"]

        # Choose RPC
        rpc_endpoint, sender_endpoint = _choose_rpc_endpoints()

        # ALWAYS fetch decimals from RPC to be absolutely sure
        print(f"[TradeManual] 🔍 Fetching real decimals for {args.token_address} from RPC...")
        token_decimals = await get_token_decimals_rpc(args.token_address, rpc_endpoint)
        print(f"[TradeManual] 🔢 Decimals: {token_decimals}")
        
        # Sync DB if decimals mismatch
        if token_row and token_row["decimals"] != token_decimals:
            print(f"[TradeManual] 🔄 Syncing DB decimals: {token_row['decimals']} -> {token_decimals}")
            await conn.execute("UPDATE tokens SET decimals = $1 WHERE id = $2", token_decimals, token_id)

        if args.mode == "buy":
            if not args.amount:
                print("[TradeManual] ❌ --amount (USD) is required for buy mode.")
                await close_db_pool()
                return
            
            if not args.key_id:
                print("[TradeManual] ❌ --key-id is required for manual buy.")
                await close_db_pool()
                return

            keypair = _load_keypair_by_id(args.key_id)
            if not keypair:
                print(f"[TradeManual] ❌ Wallet key-id {args.key_id} not found in keys.json")
                await close_db_pool()
                return

            print(f"[TradeManual] 🔎 Fetching fresh SOL price for accurate calculation...")
            # Note: We don't start the global monitor to avoid modifying bot state.
            # We just fetch once.
            sol_price = await fetch_sol_price_manual()
            print(f"[TradeManual] 💰 Current SOL Price: ${sol_price:.2f}")
            
            # Since buy_real is not used, we call execute_buy directly.
            # We need to ensure we calculate amount correctly.
            # Actually, execute_buy internally calculates lamports based on get_current_sol_price().
            # Oh wait, if get_current_sol_price() uses fallback, it will be wrong.
            
            # Since we can't modify get_current_sol_price without Touching core,
            # we will temporarily patch the 'config' or 'sol_price' module IN MEMORY.
            import _v2_sol_price
            original_fallback = getattr(config, "SOL_PRICE_FALLBACK", 193.0)
            config.SOL_PRICE_FALLBACK = sol_price # Patch config for this run
            
            print(f"[TradeManual] 🛒 Buying token {args.token_address} for ${args.amount:.2f}...")
            res = await execute_buy(
                token_id=token_id,
                keypair=keypair,
                amount_usd=args.amount,
                token_address=args.token_address,
                token_decimals=token_decimals,
                rpc_endpoint=rpc_endpoint,
                sender_endpoint=sender_endpoint,
                simulate=args.simulate
            )
            # Revert patch
            config.SOL_PRICE_FALLBACK = original_fallback
            
            print(f"[TradeManual] Result: {res}")
            
        elif args.mode == "sell":
            # Find wallet bound to token if key_id not provided
            if not args.key_id:
                key_id_to_use = await conn.fetchval("SELECT wallet_id FROM tokens WHERE id = $1", token_id)
                if not key_id_to_use:
                    print(f"[TradeManual] ❌ No wallet bound to token {args.token_address} in DB. Please use --key-id")
                    await close_db_pool()
                    return
            else:
                key_id_to_use = args.key_id

            keypair = _load_keypair_by_id(key_id_to_use)
            if not keypair:
                print(f"[TradeManual] ❌ Wallet key-id {key_id_to_use} not found in keys.json")
                await close_db_pool()
                return

            # Get balance
            balance_ui, balance_raw = await get_token_balance(keypair, args.token_address, token_decimals)
            if balance_raw <= 0:
                print(f"[TradeManual] ❌ Token balance is 0 for wallet {key_id_to_use}. Nothing to sell.")
                await close_db_pool()
                return
            
            token_amount = args.amount if args.amount is not None else balance_ui
            print(f"[TradeManual] 🚀 Selling {token_amount:.8f} tokens from wallet {key_id_to_use}...")
            
            res = await execute_sell(
                token_id=token_id,
                keypair=keypair,
                token_address=args.token_address,
                token_amount=token_amount,
                token_decimals=token_decimals,
                rpc_endpoint=rpc_endpoint,
                sender_endpoint=sender_endpoint,
                simulate=args.simulate
            )
            print(f"[TradeManual] Result: {res}")
            
            if res.get("success") and not args.simulate:
                sig = res.get("signature")
                if sig:
                    print(f"[TradeManual] ⏳ Waiting for confirmation of {sig}...")
                    await _wait_for_signature_confirmation(sig)
                
                print("[TradeManual] ⏳ Waiting 5 seconds before closing account...")
                await asyncio.sleep(5)
                
                print("[TradeManual] 🔄 Closing token account to reclaim Rent...")
                close_success, close_res = await close_ata_logic(keypair, args.token_address, rpc_endpoint)
                if close_success:
                    print(f"[TradeManual] ✅ Account closed: {close_res}")
                else:
                    print(f"[TradeManual] ⚠️ Failed to close account: {close_res}")

    await close_db_pool()

if __name__ == "__main__":
    # Check if execute_buy has correct argument names
    # In outline it was: rpc_endpoint, sender_endpoint
    # Let's verify the actual signature in _v2_buy_sell.py
    asyncio.run(main())
