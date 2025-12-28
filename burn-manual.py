#!/usr/bin/env python3
import argparse
import asyncio
import sys
import json
from pathlib import Path
# Add server directory to path
base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir / "server"))

from config import config
from _v3_db_pool import get_db_pool, close_db_pool
from _v2_buy_sell import _load_keypair_by_id
from solana_utils import (
    burn_tokens_logic, 
    close_ata_logic, 
    resolve_rpc_endpoint, 
    get_ata_balance,
    wait_for_signature_confirmation
)

async def get_token_decimals_rpc(token_address: str, rpc_url: str) -> int:
    """Fetch decimals from blockchain to avoid DB discrepancies."""
    try:
        from solana.rpc.async_api import AsyncClient
        from solders.pubkey import Pubkey
        from solders.signature import Signature
        async with AsyncClient(rpc_url) as client:
            res = await client.get_token_supply(Pubkey.from_string(token_address))
            if res.value:
                return res.value.decimals
    except Exception as e:
        print(f"[BurnManual] ⚠️ Failed to fetch decimals from RPC: {e}")
    return 6

async def main():
    parser = argparse.ArgumentParser(description="Manual Token Burning CLI")
    parser.add_argument("--token-address", required=True, help="Token mint address")
    parser.add_argument("--key-id", type=int, required=True, help="Wallet key ID")
    parser.add_argument("--amount", type=float, help="Amount to burn (default: all)")
    parser.add_argument("--all", action="store_true", help="Burn all tokens")
    
    args = parser.parse_args()
    
    rpc_endpoint = resolve_rpc_endpoint()
    
    # Load Keypair
    keypair = _load_keypair_by_id(args.key_id)
    if not keypair:
        print(f"[BurnManual] ❌ Wallet key-id {args.key_id} not found in keys.json")
        return

    # Connect to DB to check/sync decimals
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        token_row = await conn.fetchrow(
            "SELECT id, decimals FROM tokens WHERE token_address = $1", args.token_address
        )
        
        # Always fetch real decimals
        print(f"[BurnManual] 🔍 Fetching real decimals for {args.token_address} from RPC...")
        token_decimals = await get_token_decimals_rpc(args.token_address, rpc_endpoint)
        print(f"[BurnManual] 🔢 Decimals: {token_decimals}")
        
        if token_row:
            if token_row["decimals"] != token_decimals:
                print(f"[BurnManual] 🔄 Syncing DB decimals: {token_row['decimals']} -> {token_decimals}")
                await conn.execute("UPDATE tokens SET decimals = $1 WHERE id = $2", token_decimals, token_row["id"])
        else:
            print(f"[BurnManual] ℹ️ Token not in DB. No sync needed.")

    # Check balance
    print(f"[BurnManual] 🔍 Checking balance for wallet {args.key_id}...")
    print(f"[BurnManual] 👛 Wallet Check: {keypair.pubkey()}")
    
    # Debug: Print ATA address
    from solana_utils import derive_ata
    from solders.pubkey import Pubkey
    ata = derive_ata(keypair.pubkey(), Pubkey.from_string(args.token_address))
    print(f"[BurnManual] 🧾 Derived ATA: {ata}")

    ui_balance, raw_balance = await get_ata_balance(keypair, args.token_address, rpc_endpoint)
    print(f"[BurnManual] 💰 Balance: {ui_balance:.8f} tokens")
    
    if raw_balance <= 0:
        print(f"[BurnManual] ℹ️ Balance is 0. Skipping burn, proceeding to close account.")
    else:

        burn_amount = args.amount if (args.amount and not args.all) else ui_balance
        if burn_amount > ui_balance:
            print(f"[BurnManual] ⚠️ Requested amount {burn_amount} > balance {ui_balance}. Burning max.")
            burn_amount = ui_balance

        print(f"[BurnManual] 🔥 Burning {burn_amount:.8f} tokens...")
        success, res = await burn_tokens_logic(
            keypair, 
            args.token_address, 
            burn_amount, 
            token_decimals, 
            rpc_endpoint
        )
        
        if success:
            print(f"[BurnManual] ✅ Burn successful: {res}")
            print("[BurnManual] ⏳ Waiting for transaction confirmation (timeout 30s)...")
            
            # Use shared wait logic
            confirmed = await wait_for_signature_confirmation(res, rpc_endpoint, timeout_sec=30.0)
            
            if confirmed:
                print("[BurnManual] ✅ Transaction confirmed!")
                print("[BurnManual] ⏳ Waiting 2 seconds for indexing...")
                await asyncio.sleep(2)
            else:
                print(f"[BurnManual] ⚠️ Warning: Transaction confirmation timed out or failed.")
                print("[BurnManual] ⏳ Proceeding to attempt close anyway after 5s wait...")
                await asyncio.sleep(5)
        else:
            print(f"[BurnManual] ❌ Burn failed: {res}")
            print("[BurnManual] 🛑 Stopping execution.")
            await close_db_pool()
            return
            
    print(f"[BurnManual] 🔄 Closing token account to reclaim Rent...")
    close_success, close_res = await close_ata_logic(keypair, args.token_address, rpc_endpoint)
    if close_success:
        print(f"[BurnManual] ✅ Account closed: {close_res}")
    else:
        print(f"[BurnManual] ⚠️ Failed to close account: {close_res}")

    await close_db_pool()

if __name__ == "__main__":
    asyncio.run(main())
