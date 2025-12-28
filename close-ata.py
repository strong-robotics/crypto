#!/usr/bin/env python3
import argparse
import asyncio
import sys
from pathlib import Path

# Add server directory to path
base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir / "server"))

from config import config
from _v3_db_pool import get_db_pool, close_db_pool
from _v2_buy_sell import _load_keypair_by_id
from solana_utils import close_ata_logic, resolve_rpc_endpoint

async def main():
    parser = argparse.ArgumentParser(description="Manual Close ATA CLI (Rent Reclamation)")
    parser.add_argument("--token-address", required=True, help="Token mint address")
    parser.add_argument("--key-id", type=int, required=True, help="Wallet key ID")
    
    args = parser.parse_args()
    
    rpc_endpoint = resolve_rpc_endpoint()
    
    # Load Keypair
    keypair = _load_keypair_by_id(args.key_id)
    if not keypair:
        print(f"[CloseATA] ❌ Wallet key-id {args.key_id} not found in keys.json")
        return

    print(f"[CloseATA] 🔍 Attempting to close ATA for token {args.token_address}...")
    
    success, res = await close_ata_logic(keypair, args.token_address, rpc_endpoint)
    
    if success:
        print(f"[CloseATA] ✅ Result: {res}")
    else:
        print(f"[CloseATA] ❌ Failed: {res}")

if __name__ == "__main__":
    asyncio.run(main())
